"""Teams and memberships.

Membership changes lock the team row (FOR UPDATE), which serialises them per team. That makes
the "a team always keeps at least one manager" invariant race-free (two managers cannot demote
each other simultaneously) without touching other teams.
"""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.policy import MANAGER, team_access
from app.core.errors import Conflict, Forbidden, NotFound
from app.domain.workflow import TERMINAL_STATUSES
from app.models import Team, TeamMembership, User, WorkItem
from app.schemas import MemberOut, TeamCreate, TeamOut
from app.services.work_items import user_brief

TERMINAL = [s.value for s in TERMINAL_STATUSES]


def _team_out_rows(db: Session, user: User, team_ids: list[int] | None) -> list[TeamOut]:
    member_count = (
        select(func.count()).where(TeamMembership.team_id == Team.id).correlate(Team).scalar_subquery()
    )
    active_count = (
        select(func.count())
        .where(WorkItem.team_id == Team.id, WorkItem.status.notin_(TERMINAL))
        .correlate(Team)
        .scalar_subquery()
    )
    my_role = (
        select(TeamMembership.role)
        .where(TeamMembership.team_id == Team.id, TeamMembership.user_id == user.id)
        .correlate(Team)
        .scalar_subquery()
    )
    stmt = select(Team, member_count, active_count, my_role).order_by(Team.name)
    if team_ids is not None:
        stmt = stmt.where(Team.id.in_(team_ids))
    elif not user.is_admin:
        stmt = stmt.where(
            Team.id.in_(select(TeamMembership.team_id).where(TeamMembership.user_id == user.id))
        )
    return [
        TeamOut(
            id=t.id,
            key=t.key,
            name=t.name,
            description=t.description,
            my_role=role,
            member_count=mc,
            active_item_count=ac,
            can_manage=user.is_admin or role == MANAGER,
        )
        for t, mc, ac, role in db.execute(stmt).all()
    ]


def list_teams(db: Session, user: User) -> list[TeamOut]:
    return _team_out_rows(db, user, None)


def get_team(db: Session, user: User, team_id: int) -> TeamOut:
    access = team_access(db, user, team_id)
    rows = _team_out_rows(db, user, [team_id]) if access.can_view else []
    if not rows:
        raise NotFound("Team not found")
    return rows[0]


def create_team(db: Session, user: User, data: TeamCreate) -> Team:
    if not user.is_admin:
        raise Forbidden("Administrator role required")
    exists = db.execute(
        select(Team.id).where((Team.key == data.key) | (func.lower(Team.name) == data.name.lower()))
    ).first()
    if exists:
        raise Conflict("A team with this key or name already exists", code="duplicate_team")
    team = Team(key=data.key, name=data.name, description=data.description, item_seq=0)
    db.add(team)
    db.flush()
    return team


def list_members(db: Session, user: User, team_id: int) -> list[MemberOut]:
    if not team_access(db, user, team_id).can_view:
        raise NotFound("Team not found")
    rows = db.execute(
        select(TeamMembership, User)
        .join(User, User.id == TeamMembership.user_id)
        .where(TeamMembership.team_id == team_id)
        .order_by(TeamMembership.role, User.full_name)
    ).all()
    return [MemberOut(user=user_brief(u), role=m.role, created_at=m.created_at) for m, u in rows]


def _lock_team_for_membership_change(db: Session, user: User, team_id: int) -> None:
    access = team_access(db, user, team_id)
    if not access.can_view:
        raise NotFound("Team not found")
    if not access.is_manager:
        raise Forbidden("Only a team manager or administrator can manage membership")
    db.execute(select(Team.id).where(Team.id == team_id).with_for_update())


def _manager_count(db: Session, team_id: int) -> int:
    return db.execute(
        select(func.count()).where(TeamMembership.team_id == team_id, TeamMembership.role == MANAGER)
    ).scalar_one()


def upsert_member(db: Session, user: User, team_id: int, target_user_id: int, role: str) -> MemberOut:
    _lock_team_for_membership_change(db, user, team_id)
    target = db.get(User, target_user_id)
    if target is None or not target.is_active:
        raise NotFound("User not found")
    membership = db.get(TeamMembership, (team_id, target_user_id))
    if membership is None:
        membership = TeamMembership(team_id=team_id, user_id=target_user_id, role=role)
        db.add(membership)
    elif membership.role != role:
        if membership.role == MANAGER and _manager_count(db, team_id) <= 1:
            raise Conflict("A team must keep at least one manager", code="last_manager")
        membership.role = role
    db.flush()
    db.refresh(membership)
    return MemberOut(user=user_brief(target), role=membership.role, created_at=membership.created_at)


def remove_member(db: Session, user: User, team_id: int, target_user_id: int) -> None:
    _lock_team_for_membership_change(db, user, team_id)
    membership = db.get(TeamMembership, (team_id, target_user_id))
    if membership is None:
        raise NotFound("Membership not found")
    if membership.role == MANAGER and _manager_count(db, team_id) <= 1:
        raise Conflict("A team must keep at least one manager", code="last_manager")
    # Delete first: this takes the row lock that conflicts with FOR SHARE in assign/claim, so
    # any in-flight assignment to this user either commits before us (and is counted below)
    # or waits and then finds the membership gone.
    db.delete(membership)
    db.flush()
    active_assigned = db.execute(
        select(func.count()).where(
            WorkItem.team_id == team_id,
            WorkItem.assignee_id == target_user_id,
            WorkItem.status.notin_(TERMINAL),
        )
    ).scalar_one()
    if active_assigned:
        raise Conflict(
            f"User still owns {active_assigned} active work item(s) in this team; reassign them first",
            code="member_has_active_items",
            details={"active_items": active_assigned},
        )
