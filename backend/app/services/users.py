"""User administration (global admins only).

Invariants:
  * at least one active administrator always exists (admin rows are locked while checking);
  * admins cannot deactivate or demote themselves (prevents accidental lock-out);
  * deactivation and password resets revoke every session of the target immediately;
  * passwords set by an administrator are temporary: the user must change them at next sign-in.
"""

import re
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import Conflict, NotFound, ValidationFailed
from app.core.security import UNUSABLE_PASSWORD, generate_temporary_password, hash_password, validate_password
from app.domain.workflow import TERMINAL_STATUSES
from app.models import TeamMembership, User, UserSession, WorkItem
from app.schemas import UserAdminOut
from app.services import security_log, sessions

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
TERMINAL = [s.value for s in TERMINAL_STATUSES]


def admin_view(db: Session, user: User) -> UserAdminOut:
    team_count = db.execute(select(func.count()).where(TeamMembership.user_id == user.id)).scalar_one()
    active_sessions = db.execute(
        select(func.count()).where(
            UserSession.user_id == user.id,
            UserSession.revoked_at.is_(None),
            UserSession.expires_at > datetime.now(UTC),
        )
    ).scalar_one()
    active_items = db.execute(
        select(func.count()).where(WorkItem.assignee_id == user.id, WorkItem.status.notin_(TERMINAL))
    ).scalar_one()
    return UserAdminOut(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        is_admin=user.is_admin,
        is_active=user.is_active,
        must_change_password=user.must_change_password,
        sso_linked=user.oidc_subject is not None,
        has_password=user.password_hash != UNUSABLE_PASSWORD,
        created_at=user.created_at,
        last_login_at=user.last_login_at,
        deactivated_at=user.deactivated_at,
        team_count=team_count,
        active_session_count=active_sessions,
        active_assigned_items=active_items,
    )


def list_users(
    db: Session, *, q: str, include_inactive: bool, after_id: int | None, limit: int
) -> tuple[list[User], int | None, int]:
    stmt = select(User)
    if not include_inactive:
        stmt = stmt.where(User.is_active.is_(True))
    if q.strip():
        term = f"%{q.strip().lower()}%"
        stmt = stmt.where((func.lower(User.full_name).like(term)) | (User.email.like(term)))
    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    if after_id:
        stmt = stmt.where(User.id > after_id)
    rows = list(db.execute(stmt.order_by(User.id).limit(limit + 1)).scalars().all())
    next_cursor = rows[limit - 1].id if len(rows) > limit else None
    return rows[:limit], next_cursor, total


def _get(db: Session, user_id: int, *, lock: bool = False) -> User:
    stmt = select(User).where(User.id == user_id)
    if lock:
        stmt = stmt.with_for_update()
    user = db.execute(stmt).scalar_one_or_none()
    if user is None:
        raise NotFound("User not found")
    return user


def create_user(
    db: Session,
    admin: User,
    *,
    email: str,
    full_name: str,
    is_admin: bool,
    password: str | None,
    ip: str,
) -> tuple[User, str | None]:
    email = email.strip().lower()
    full_name = full_name.strip()
    if not EMAIL_RE.match(email):
        raise ValidationFailed("Enter a valid email address", details={"field": "email"})
    if not full_name:
        raise ValidationFailed("Name is required", details={"field": "full_name"})
    if db.execute(select(User.id).where(User.email == email)).first():
        raise Conflict(
            "A user with this email already exists", code="duplicate_email", details={"field": "email"}
        )
    temporary = None
    if password is None:
        temporary = password = generate_temporary_password()
    else:
        validate_password(password, email=email, full_name=full_name)
    user = User(
        email=email,
        full_name=full_name,
        password_hash=hash_password(password),
        is_admin=is_admin,
        is_active=True,
        must_change_password=True,
    )
    db.add(user)
    db.flush()
    security_log.record(
        db, "user_created", actor_id=admin.id, target_user_id=user.id, ip=ip, is_admin=is_admin
    )
    if is_admin:
        security_log.record(db, "admin_granted", actor_id=admin.id, target_user_id=user.id, ip=ip)
    return user, temporary


def _lock_active_admins(db: Session) -> list[int]:
    return list(
        db.execute(select(User.id).where(User.is_admin.is_(True), User.is_active.is_(True)).with_for_update())
        .scalars()
        .all()
    )


def update_user(
    db: Session,
    admin: User,
    user_id: int,
    *,
    fields: dict,
    ip: str,
) -> User:
    removing_admin_power = fields.get("is_active") is False or fields.get("is_admin") is False
    admins = _lock_active_admins(db) if removing_admin_power else []
    user = _get(db, user_id, lock=True)

    if user.id == admin.id and removing_admin_power:
        raise Conflict("You cannot deactivate or demote your own account", code="cannot_modify_self")
    if removing_admin_power and user.is_admin and user.is_active and admins == [user.id]:
        raise Conflict("At least one active administrator must remain", code="last_admin")

    if "full_name" in fields and fields["full_name"] is not None:
        name = fields["full_name"].strip()
        if not name:
            raise ValidationFailed("Name is required", details={"field": "full_name"})
        if name != user.full_name:
            security_log.record(
                db,
                "user_updated",
                actor_id=admin.id,
                target_user_id=user.id,
                ip=ip,
                full_name={"from": user.full_name, "to": name},
            )
            user.full_name = name

    if "is_admin" in fields and fields["is_admin"] is not None and fields["is_admin"] != user.is_admin:
        user.is_admin = fields["is_admin"]
        security_log.record(
            db,
            "admin_granted" if user.is_admin else "admin_revoked",
            actor_id=admin.id,
            target_user_id=user.id,
            ip=ip,
        )

    if "is_active" in fields and fields["is_active"] is not None and fields["is_active"] != user.is_active:
        if fields["is_active"]:
            user.is_active = True
            user.deactivated_at = None
            security_log.record(db, "user_reactivated", actor_id=admin.id, target_user_id=user.id, ip=ip)
        else:
            user.is_active = False
            user.deactivated_at = datetime.now(UTC)
            revoked = sessions.revoke_all(db, user.id, "deactivated")
            security_log.record(
                db,
                "user_deactivated",
                actor_id=admin.id,
                target_user_id=user.id,
                ip=ip,
                sessions_revoked=revoked,
            )
    db.flush()
    return user


def reset_password(db: Session, admin: User, user_id: int, *, ip: str) -> tuple[User, str]:
    user = _get(db, user_id, lock=True)
    temporary = generate_temporary_password()
    user.password_hash = hash_password(temporary)
    user.must_change_password = True
    user.password_changed_at = datetime.now(UTC)
    revoked = sessions.revoke_all(db, user.id, "password_reset")
    security_log.record(
        db, "password_reset", actor_id=admin.id, target_user_id=user.id, ip=ip, sessions_revoked=revoked
    )
    db.flush()
    return user, temporary


def revoke_user_sessions(db: Session, admin: User, user_id: int, *, ip: str) -> int:
    user = _get(db, user_id)
    revoked = sessions.revoke_all(db, user.id, "admin_revoked")
    security_log.record(
        db, "sessions_revoked", actor_id=admin.id, target_user_id=user.id, ip=ip, count=revoked
    )
    return revoked
