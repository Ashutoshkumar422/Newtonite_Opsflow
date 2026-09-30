"""Work item lifecycle — the business logic layer.

Transaction discipline: functions here flush but never commit. The API layer commits once per
request, so the item change, its Activity row, its outbox event and (when used) its
idempotency record either all persist or none do.

Concurrency discipline:
  * claim        — single-statement compare-and-set (UPDATE ... WHERE assignee_id IS NULL).
                   The precondition is the state itself, so no client version is needed.
  * edit/assign/transition — row lock (SELECT ... FOR UPDATE) + client-supplied `version`
                   check. The lock serialises writers on one item and gives us accurate
                   before-values for the audit diff; the version check rejects clients that
                   acted on stale data (HTTP 409 with the current state).
  * assignee membership — the target's membership row is read FOR SHARE, which conflicts with a
                   concurrent membership removal (see services/teams.remove_member).
"""

import re
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import Select, and_, func, or_, select, update
from sqlalchemy.orm import Session

from app.auth.policy import (
    TeamAccess,
    actors_for,
    can_assign_others,
    can_comment,
    can_edit,
    can_release,
    is_terminal,
    item_permissions,
    team_access,
    visible_team_ids_subquery,
)
from app.core.errors import (
    Conflict,
    Forbidden,
    InvalidTransition,
    NotFound,
    ValidationFailed,
)
from app.domain.workflow import (
    ACTIVE_STATUSES,
    PRIORITY_RANK,
    RANK_PRIORITY,
    STATUSES_REQUIRING_ASSIGNEE,
    TERMINAL_STATUSES,
    Priority,
    Status,
    TransitionError,
    check_transition,
)
from app.models import Activity, Comment, Team, TeamMembership, User, WorkItem
from app.schemas import (
    ActivityOut,
    CommentOut,
    ItemPermissions,
    Page,
    TeamBrief,
    UserBrief,
    WorkItemCreate,
    WorkItemOut,
    WorkItemSummary,
    WorkItemUpdate,
)
from app.services import outbox
from app.services.pagination import SORTS, apply_keyset, cursor_values, encode_cursor

STATUS_LABEL = {
    Status.OPEN: "Open",
    Status.IN_PROGRESS: "In progress",
    Status.BLOCKED: "Blocked",
    Status.RESOLVED: "Awaiting approval",
    Status.CLOSED: "Closed",
    Status.CANCELLED: "Cancelled",
}
KEY_RE = re.compile(r"^\s*([A-Za-z][A-Za-z0-9]{1,9})-(\d+)\s*$")
TERMINAL = [s.value for s in TERMINAL_STATUSES]


# ---------------------------------------------------------------- serialisation
def today() -> date:
    return datetime.now(UTC).date()


def user_brief(u: User) -> UserBrief:
    return UserBrief(id=u.id, full_name=u.full_name, email=u.email)


def _brief_or_none(u: User | None) -> dict | None:
    return {"id": u.id, "name": u.full_name} if u else None


def item_key(item: WorkItem) -> str:
    return f"{item.team.key}-{item.number}"


def is_overdue(item: WorkItem) -> bool:
    return (
        item.due_date is not None and item.due_date < today() and Status(item.status) not in TERMINAL_STATUSES
    )


def to_summary(item: WorkItem) -> WorkItemSummary:
    return WorkItemSummary(
        id=item.id,
        key=item_key(item),
        number=item.number,
        title=item.title,
        status=Status(item.status),
        priority=RANK_PRIORITY[item.priority],
        team=TeamBrief(id=item.team.id, key=item.team.key, name=item.team.name),
        reporter=user_brief(item.reporter),
        assignee=user_brief(item.assignee) if item.assignee else None,
        due_date=item.due_date,
        is_overdue=is_overdue(item),
        version=item.version,
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


def to_out(item: WorkItem, access: TeamAccess) -> WorkItemOut:
    return WorkItemOut(
        **to_summary(item).model_dump(),
        description=item.description,
        permissions=ItemPermissions(**item_permissions(access, item)),
    )


# ---------------------------------------------------------------- loading & guards
def _fetch(db: Session, item_id: int, *, lock: bool) -> WorkItem | None:
    stmt = select(WorkItem).where(WorkItem.id == item_id).execution_options(populate_existing=True)
    if lock:
        stmt = stmt.with_for_update(of=WorkItem)
    return db.execute(stmt).unique().scalar_one_or_none()


def load_for_user(
    db: Session, user: User, item_id: int, *, lock: bool = False
) -> tuple[WorkItem, TeamAccess]:
    item = _fetch(db, item_id, lock=lock)
    if item is None:
        raise NotFound("Work item not found")
    access = team_access(db, user, item.team_id)
    if not access.can_view:
        # Deliberately indistinguishable from a missing item: no cross-team existence leak.
        raise NotFound("Work item not found")
    return item, access


def _check_version(item: WorkItem, expected: int, access: TeamAccess) -> None:
    if item.version != expected:
        raise Conflict(
            "This work item was changed by someone else since you loaded it",
            code="version_conflict",
            details={
                "expected_version": expected,
                "current_version": item.version,
                "current": to_out(item, access).model_dump(mode="json"),
            },
        )


def _reject_terminal(item: WorkItem) -> None:
    if is_terminal(item):
        raise Conflict(f"Work item is {item.status} and can no longer be changed", code="item_closed")


def _lock_membership(db: Session, team_id: int, user_id: int) -> str:
    """Read the membership FOR SHARE so it cannot be removed until this transaction ends."""
    role = db.execute(
        select(TeamMembership.role)
        .join(User, User.id == TeamMembership.user_id)
        .where(
            TeamMembership.team_id == team_id,
            TeamMembership.user_id == user_id,
            User.is_active.is_(True),
        )
        .with_for_update(of=TeamMembership, read=True)
    ).scalar_one_or_none()
    if role is None:
        raise ValidationFailed(
            "The assignee must be an active member of the work item's team",
            code="assignee_not_member",
            details={"assignee_id": user_id},
        )
    return role


def _bump(item: WorkItem) -> None:
    item.version += 1
    item.updated_at = datetime.now(UTC)


def _record(db: Session, item: WorkItem, actor: User, action: str, changes: dict[str, Any]) -> None:
    db.add(Activity(work_item_id=item.id, actor_id=actor.id, action=action, changes=changes))


def _participants(item: WorkItem) -> set[int]:
    return {item.reporter_id, item.assignee_id or 0}


def _finish(db: Session, item: WorkItem) -> WorkItem:
    db.flush()
    db.refresh(item)
    return item


# ---------------------------------------------------------------- create
def create_item(db: Session, user: User, data: WorkItemCreate) -> tuple[WorkItem, TeamAccess]:
    access = team_access(db, user, data.team_id)
    if not access.can_view:
        raise NotFound("Team not found")
    if not (access.is_member or user.is_admin):
        raise Forbidden("Only team members can create work items")
    if data.due_date and data.due_date < today():
        raise ValidationFailed("Due date cannot be in the past", details={"field": "due_date"})
    if data.assignee_id is not None:
        if data.assignee_id != user.id and not access.is_manager:
            raise Forbidden("Only a team manager can assign work to someone else")
        _lock_membership(db, data.team_id, data.assignee_id)

    # Atomic per-team counter: the row lock serialises concurrent creations in one team only.
    number = db.execute(
        update(Team)
        .where(Team.id == data.team_id)
        .values(item_seq=Team.item_seq + 1)
        .returning(Team.item_seq)
    ).scalar_one()

    item = WorkItem(
        team_id=data.team_id,
        number=number,
        title=data.title,
        description=data.description,
        status=Status.OPEN.value,
        priority=PRIORITY_RANK[data.priority],
        reporter_id=user.id,
        assignee_id=data.assignee_id,
        due_date=data.due_date,
        version=1,
    )
    db.add(item)
    db.flush()
    assignee = db.get(User, data.assignee_id) if data.assignee_id else None
    _record(
        db,
        item,
        user,
        "created",
        {
            "title": {"from": None, "to": item.title},
            "priority": {"from": None, "to": data.priority.value},
            "status": {"from": None, "to": Status.OPEN.value},
            **({"assignee": {"from": None, "to": _brief_or_none(assignee)}} if assignee else {}),
            **({"due_date": {"from": None, "to": data.due_date.isoformat()}} if data.due_date else {}),
        },
    )
    if assignee:
        outbox.emit_notification(
            db,
            "assigned",
            recipients={assignee.id},
            actor_id=user.id,
            work_item_id=item.id,
            message=f"{user.full_name} assigned you a new work item: {data.title}",
        )
    return _finish(db, item), access


# ---------------------------------------------------------------- edit
EDITABLE = ("title", "description", "priority", "due_date")


def _wire(field: str, value: Any) -> Any:
    if value is None:
        return None
    if field == "priority":
        return RANK_PRIORITY[value].value if isinstance(value, int) else Priority(value).value
    if isinstance(value, date):
        return value.isoformat()
    return value


def update_item(db: Session, user: User, item_id: int, data: WorkItemUpdate) -> tuple[WorkItem, TeamAccess]:
    item, access = load_for_user(db, user, item_id, lock=True)
    _reject_terminal(item)
    if not can_edit(access, item):
        raise Forbidden("Only the reporter, the assignee or a team manager can edit this item")
    _check_version(item, data.version, access)

    changes: dict[str, Any] = {}
    for field in EDITABLE:
        if field not in data.model_fields_set:
            continue
        new = getattr(data, field)
        if field == "title" and not new:
            raise ValidationFailed("title must not be blank", details={"field": "title"})
        if field == "description" and new is None:
            new = ""
        if field == "priority":
            if new is None:
                raise ValidationFailed("priority cannot be null", details={"field": "priority"})
            new = PRIORITY_RANK[Priority(new)]
        if field == "due_date" and new is not None and new != item.due_date and new < today():
            raise ValidationFailed("Due date cannot be in the past", details={"field": "due_date"})
        old = getattr(item, field)
        if old != new:
            changes[field] = {"from": _wire(field, old), "to": _wire(field, new)}
            setattr(item, field, new)

    if not changes:
        return item, access  # nothing changed: no version bump, no audit noise
    _bump(item)
    _record(db, item, user, "updated", changes)
    if "priority" in changes:
        outbox.emit_notification(
            db,
            "priority_changed",
            recipients=_participants(item),
            actor_id=user.id,
            work_item_id=item.id,
            message=(
                f"{user.full_name} changed priority of {item_key(item)} "
                f"from {changes['priority']['from']} to {changes['priority']['to']}"
            ),
        )
    return _finish(db, item), access


# ---------------------------------------------------------------- claim (compare-and-set)
def claim_item(db: Session, user: User, item_id: int) -> tuple[WorkItem, TeamAccess, bool]:
    """Returns (item, access, changed). Claiming an item you already hold is a no-op success,
    which makes claim naturally idempotent for the same user."""
    item, access = load_for_user(db, user, item_id)
    if not access.is_member:
        raise Forbidden("Only members of the team can claim its work items")
    _lock_membership(db, item.team_id, user.id)

    claimed_version = db.execute(
        update(WorkItem)
        .where(
            WorkItem.id == item_id,
            WorkItem.assignee_id.is_(None),
            WorkItem.status.notin_(TERMINAL),
        )
        .values(
            assignee_id=user.id,
            version=WorkItem.version + 1,
            updated_at=func.now(),
        )
        .returning(WorkItem.version)
        .execution_options(synchronize_session=False)
    ).scalar_one_or_none()

    fetched = _fetch(db, item_id, lock=False)
    assert fetched is not None
    item = fetched
    if claimed_version is None:
        if item.assignee_id == user.id:
            return item, access, False
        _reject_terminal(item)
        raise Conflict(
            f"Already claimed by {item.assignee.full_name if item.assignee else 'someone else'}",
            code="already_claimed",
            details={"current": to_out(item, access).model_dump(mode="json")},
        )

    _record(db, item, user, "claimed", {"assignee": {"from": None, "to": _brief_or_none(user)}})
    outbox.emit_notification(
        db,
        "claimed",
        recipients={item.reporter_id},
        actor_id=user.id,
        work_item_id=item.id,
        message=f"{user.full_name} picked up {item_key(item)}: {item.title}",
    )
    return _finish(db, item), access, True


# ---------------------------------------------------------------- assign / reassign / release
def assign_item(
    db: Session, user: User, item_id: int, assignee_id: int | None, version: int
) -> tuple[WorkItem, TeamAccess]:
    item, access = load_for_user(db, user, item_id, lock=True)
    _reject_terminal(item)
    if assignee_id is None:
        if not (can_assign_others(access, item) or can_release(access, item)):
            raise Forbidden("Only a team manager or the current assignee can unassign this item")
    elif not can_assign_others(access, item):
        raise Forbidden("Only a team manager can assign work items to others; use claim instead")
    _check_version(item, version, access)

    if assignee_id == item.assignee_id:
        return item, access  # already in the requested state
    if assignee_id is None and Status(item.status) in STATUSES_REQUIRING_ASSIGNEE:
        raise Conflict(
            f"Cannot unassign an item that is '{item.status}'; stop work first",
            code="assignee_required",
        )
    new_user: User | None = None
    if assignee_id is not None:
        _lock_membership(db, item.team_id, assignee_id)
        new_user = db.get(User, assignee_id)

    previous = item.assignee
    item.assignee_id = assignee_id
    _bump(item)
    _record(
        db,
        item,
        user,
        "assigned" if new_user else "unassigned",
        {"assignee": {"from": _brief_or_none(previous), "to": _brief_or_none(new_user)}},
    )
    if new_user:
        outbox.emit_notification(
            db,
            "assigned",
            recipients={new_user.id},
            actor_id=user.id,
            work_item_id=item.id,
            message=f"{user.full_name} assigned {item_key(item)} to you: {item.title}",
        )
    if previous and previous.id != assignee_id:
        outbox.emit_notification(
            db,
            "unassigned",
            recipients={previous.id},
            actor_id=user.id,
            work_item_id=item.id,
            message=f"{user.full_name} reassigned {item_key(item)} away from you",
        )
    return _finish(db, item), access


# ---------------------------------------------------------------- workflow transition
_TRANSITION_ERRORS = {
    "forbidden": Forbidden,
    "invalid_transition": InvalidTransition,
    "assignee_required": Conflict,
    "reason_required": ValidationFailed,
}


def transition_item(
    db: Session, user: User, item_id: int, target: Status, reason: str | None, version: int
) -> tuple[WorkItem, TeamAccess]:
    item, access = load_for_user(db, user, item_id, lock=True)
    _check_version(item, version, access)
    current = Status(item.status)
    try:
        check_transition(
            current,
            target,
            actors_for(access, item),
            has_assignee=item.assignee_id is not None,
            reason=reason,
        )
    except TransitionError as e:
        raise _TRANSITION_ERRORS[e.code](e.message, code=e.code) from e

    item.status = target.value
    _bump(item)
    changes: dict[str, Any] = {"status": {"from": current.value, "to": target.value}}
    if reason and reason.strip():
        changes["reason"] = reason.strip()
    _record(db, item, user, "status_changed", changes)
    outbox.emit_notification(
        db,
        "status_changed",
        recipients=_participants(item),
        actor_id=user.id,
        work_item_id=item.id,
        message=(
            f"{user.full_name} moved {item_key(item)} from {STATUS_LABEL[current]} to {STATUS_LABEL[target]}"
            + (f": {reason.strip()[:80]}" if reason and reason.strip() else "")
        ),
    )
    return _finish(db, item), access


# ---------------------------------------------------------------- listing & search
def _filtered(
    user: User,
    *,
    team_id: int | None = None,
    statuses: list[Status] | None = None,
    priorities: list[Priority] | None = None,
    assignee: str | None = None,
    reporter: str | None = None,
    q: str | None = None,
    overdue: bool = False,
) -> Select:
    stmt = select(WorkItem)
    if not user.is_admin:
        stmt = stmt.where(WorkItem.team_id.in_(visible_team_ids_subquery(user)))
    if team_id is not None:
        stmt = stmt.where(WorkItem.team_id == team_id)
    if statuses:
        stmt = stmt.where(WorkItem.status.in_([s.value for s in statuses]))
    if priorities:
        stmt = stmt.where(WorkItem.priority.in_([PRIORITY_RANK[p] for p in priorities]))
    if assignee:
        if assignee == "me":
            stmt = stmt.where(WorkItem.assignee_id == user.id)
        elif assignee == "none":
            stmt = stmt.where(WorkItem.assignee_id.is_(None))
        else:
            stmt = stmt.where(WorkItem.assignee_id == _int_param(assignee, "assignee"))
    if reporter:
        rid = user.id if reporter == "me" else _int_param(reporter, "reporter")
        stmt = stmt.where(WorkItem.reporter_id == rid)
    if overdue:
        stmt = stmt.where(WorkItem.due_date < today(), WorkItem.status.notin_(TERMINAL))
    if q and q.strip():
        q = q.strip()
        key = KEY_RE.match(q)
        if key:
            stmt = stmt.join(Team, Team.id == WorkItem.team_id).where(
                and_(Team.key == key.group(1).upper(), WorkItem.number == int(key.group(2)))
            )
        else:
            ts = func.websearch_to_tsquery("english", q)
            stmt = stmt.where(
                or_(WorkItem.search_vector.op("@@")(ts), WorkItem.title.ilike(f"%{_escape_like(q)}%"))
            )
    return stmt


def _int_param(value: str, name: str) -> int:
    try:
        return int(value)
    except ValueError as exc:
        raise ValidationFailed(f"'{name}' must be 'me', 'none' or a user id") from exc


def _escape_like(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def list_items(
    db: Session,
    user: User,
    *,
    sort: str = "-updated_at",
    cursor: str | None = None,
    limit: int = 25,
    with_total: bool = True,
    **filters: Any,
) -> Page[WorkItemSummary]:
    if sort not in SORTS:
        raise ValidationFailed(f"sort must be one of {sorted(SORTS)}", details={"param": "sort"})
    spec = SORTS[sort]
    base = _filtered(user, **filters)
    rows: list[WorkItem] = list(db.execute(apply_keyset(base, spec, cursor, limit)).unique().scalars().all())
    has_more = len(rows) > limit
    rows = rows[:limit]
    total = None
    if with_total:
        total = db.execute(select(func.count()).select_from(base.order_by(None).subquery())).scalar_one()
    return Page[WorkItemSummary](
        items=[to_summary(r) for r in rows],
        next_cursor=encode_cursor(cursor_values(rows[-1], sort)) if has_more and rows else None,
        total=total,
    )


# ---------------------------------------------------------------- activity & comments
def list_activity(
    db: Session, user: User, item_id: int, *, before_id: int | None, limit: int
) -> Page[ActivityOut]:
    load_for_user(db, user, item_id)
    stmt = select(Activity).where(Activity.work_item_id == item_id)
    if before_id:
        stmt = stmt.where(Activity.id < before_id)
    rows = db.execute(stmt.order_by(Activity.id.desc()).limit(limit + 1)).unique().scalars().all()
    has_more = len(rows) > limit
    rows = rows[:limit]
    return Page[ActivityOut](
        items=[
            ActivityOut(
                id=a.id,
                action=a.action,
                actor=user_brief(a.actor),
                changes=a.changes,
                created_at=a.created_at,
            )
            for a in rows
        ],
        next_cursor=str(rows[-1].id) if has_more and rows else None,
    )


def comment_out(c: Comment, user: User) -> CommentOut:
    return CommentOut(
        id=c.id,
        author=user_brief(c.author),
        body=c.body,
        created_at=c.created_at,
        edited_at=c.edited_at,
        can_edit=c.author_id == user.id,
    )


def list_comments(
    db: Session, user: User, item_id: int, *, after_id: int | None, limit: int
) -> Page[CommentOut]:
    load_for_user(db, user, item_id)
    stmt = select(Comment).where(Comment.work_item_id == item_id)
    if after_id:
        stmt = stmt.where(Comment.id > after_id)
    rows = db.execute(stmt.order_by(Comment.id).limit(limit + 1)).unique().scalars().all()
    has_more = len(rows) > limit
    rows = rows[:limit]
    return Page[CommentOut](
        items=[comment_out(c, user) for c in rows],
        next_cursor=str(rows[-1].id) if has_more and rows else None,
    )


def add_comment(db: Session, user: User, item_id: int, body: str) -> Comment:
    item, access = load_for_user(db, user, item_id)
    if not can_comment(access):
        raise Forbidden("Only team members can comment")
    comment = Comment(work_item_id=item.id, author_id=user.id, body=body)
    db.add(comment)
    db.flush()
    _record(db, item, user, "commented", {"comment_id": comment.id, "excerpt": body[:140]})
    outbox.emit_notification(
        db,
        "commented",
        recipients=_participants(item),
        actor_id=user.id,
        work_item_id=item.id,
        message=f"{user.full_name} commented on {item_key(item)}: {body[:80]}",
    )
    db.flush()
    db.refresh(comment)
    return comment


def edit_comment(db: Session, user: User, comment_id: int, body: str) -> Comment:
    comment = (
        db.execute(select(Comment).where(Comment.id == comment_id).with_for_update(of=Comment))
        .unique()
        .scalar_one_or_none()
    )
    if comment is None:
        raise NotFound("Comment not found")
    load_for_user(db, user, comment.work_item_id)  # visibility (404 across teams)
    if comment.author_id != user.id:
        raise Forbidden("Only the author can edit a comment")
    if comment.body != body:
        comment.body = body
        comment.edited_at = datetime.now(UTC)
    db.flush()
    db.refresh(comment)
    return comment


def active_status_values() -> list[str]:
    return [s.value for s in ACTIVE_STATUSES]
