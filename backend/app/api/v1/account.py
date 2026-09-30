"""Self-service account endpoints and administrator user management."""

import uuid

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session, aliased

from app.auth.deps import AuthContext, get_auth, require_admin
from app.core.errors import NotFound
from app.core.net import client_ip
from app.db.session import get_session
from app.models import SecurityEvent, User, UserSession
from app.schemas import (
    ChangePasswordIn,
    ChangePasswordOut,
    RevokedOut,
    SecurityEventOut,
    SecurityEventPage,
    SessionOut,
    UserAdminOut,
    UserAdminPage,
    UserBrief,
    UserCreateIn,
    UserUpdateIn,
    UserWithPasswordOut,
)
from app.services import auth as auth_svc
from app.services import security_log
from app.services import sessions as sessions_svc
from app.services import users as users_svc

router = APIRouter()


def _session_out(s: UserSession, current_id: uuid.UUID) -> SessionOut:
    return SessionOut(
        id=str(s.id),
        auth_method=s.auth_method,
        created_at=s.created_at,
        last_seen_at=s.last_seen_at,
        expires_at=s.expires_at,
        ip_address=s.ip_address,
        user_agent=s.user_agent,
        current=s.id == current_id,
    )


# ---------------------------------------------------------------- self-service
@router.get("/account/sessions", response_model=list[SessionOut], tags=["account"])
def my_sessions(ctx: AuthContext = Depends(get_auth), db: Session = Depends(get_session)):
    return [_session_out(s, ctx.session.id) for s in sessions_svc.active_sessions(db, ctx.user.id)]


@router.delete("/account/sessions/{session_id}", status_code=204, tags=["account"])
def revoke_my_session(
    session_id: uuid.UUID,
    request: Request,
    ctx: AuthContext = Depends(get_auth),
    db: Session = Depends(get_session),
):
    owned = db.execute(
        select(UserSession.id).where(UserSession.id == session_id, UserSession.user_id == ctx.user.id)
    ).scalar_one_or_none()
    if owned is None:
        raise NotFound("Session not found")
    if sessions_svc.revoke(db, session_id, "user_revoked"):
        security_log.record(
            db,
            "session_revoked",
            actor_id=ctx.user.id,
            target_user_id=ctx.user.id,
            ip=client_ip(request),
            session_id=str(session_id),
        )
    db.commit()
    return Response(status_code=204)


@router.post("/account/sessions/revoke-others", response_model=RevokedOut, tags=["account"])
def revoke_other_sessions(
    request: Request, ctx: AuthContext = Depends(get_auth), db: Session = Depends(get_session)
):
    """Sign out every other device; this session stays signed in."""
    n = sessions_svc.revoke_all(db, ctx.user.id, "user_revoked_others", except_session=ctx.session.id)
    security_log.record(
        db,
        "sessions_revoked",
        actor_id=ctx.user.id,
        target_user_id=ctx.user.id,
        ip=client_ip(request),
        count=n,
    )
    db.commit()
    return RevokedOut(revoked=n)


@router.post("/account/password", response_model=ChangePasswordOut, tags=["account"])
def change_password(
    data: ChangePasswordIn,
    request: Request,
    ctx: AuthContext = Depends(get_auth),
    db: Session = Depends(get_session),
):
    """Change your own password. Signs out all your other sessions and clears a temporary-password
    requirement. Wrong current passwords count towards the account lockout."""
    n = auth_svc.change_own_password(
        db, ctx.user, ctx.session, data.current_password, data.new_password, ip=client_ip(request)
    )
    return ChangePasswordOut(other_sessions_revoked=n)


# ---------------------------------------------------------------- administration
@router.get("/admin/users", response_model=UserAdminPage, tags=["admin"])
def admin_list_users(
    q: str = Query("", max_length=100),
    include_inactive: bool = True,
    cursor: int | None = None,
    limit: int = Query(50, ge=1, le=200),
    _: User = Depends(require_admin),
    db: Session = Depends(get_session),
):
    rows, next_cursor, total = users_svc.list_users(
        db, q=q, include_inactive=include_inactive, after_id=cursor, limit=limit
    )
    return UserAdminPage(
        items=[users_svc.admin_view(db, u) for u in rows], next_cursor=next_cursor, total=total
    )


@router.post("/admin/users", response_model=UserWithPasswordOut, status_code=201, tags=["admin"])
def admin_create_user(
    data: UserCreateIn,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_session),
):
    """Create an account. Without `password`, a one-time temporary password is generated and returned
    exactly once; either way the user must choose their own password at first sign-in."""
    user, temporary = users_svc.create_user(
        db,
        admin,
        email=data.email,
        full_name=data.full_name,
        is_admin=data.is_admin,
        password=data.password,
        ip=client_ip(request),
    )
    out = UserWithPasswordOut(user=users_svc.admin_view(db, user), temporary_password=temporary)
    db.commit()
    return out


@router.get("/admin/users/{user_id}", response_model=UserAdminOut, tags=["admin"])
def admin_get_user(user_id: int, _: User = Depends(require_admin), db: Session = Depends(get_session)):
    user = db.get(User, user_id)
    if user is None:
        raise NotFound("User not found")
    return users_svc.admin_view(db, user)


@router.patch("/admin/users/{user_id}", response_model=UserAdminOut, tags=["admin"])
def admin_update_user(
    user_id: int,
    data: UserUpdateIn,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_session),
):
    """Rename, grant/revoke admin, deactivate/reactivate. Deactivation revokes all sessions at once.
    The response's `active_assigned_items` tells you what still needs reassigning."""
    fields = {k: getattr(data, k) for k in data.model_fields_set}
    user = users_svc.update_user(db, admin, user_id, fields=fields, ip=client_ip(request))
    out = users_svc.admin_view(db, user)
    db.commit()
    return out


@router.post("/admin/users/{user_id}/reset-password", response_model=UserWithPasswordOut, tags=["admin"])
def admin_reset_password(
    user_id: int, request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_session)
):
    user, temporary = users_svc.reset_password(db, admin, user_id, ip=client_ip(request))
    out = UserWithPasswordOut(user=users_svc.admin_view(db, user), temporary_password=temporary)
    db.commit()
    return out


@router.post("/admin/users/{user_id}/revoke-sessions", response_model=RevokedOut, tags=["admin"])
def admin_revoke_sessions(
    user_id: int, request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_session)
):
    n = users_svc.revoke_user_sessions(db, admin, user_id, ip=client_ip(request))
    db.commit()
    return RevokedOut(revoked=n)


@router.get("/admin/security-events", response_model=SecurityEventPage, tags=["admin"])
def admin_security_events(
    cursor: int | None = None,
    event_type: str | None = Query(None, max_length=40),
    user_id: int | None = None,
    limit: int = Query(50, ge=1, le=200),
    _: User = Depends(require_admin),
    db: Session = Depends(get_session),
):
    actor, target = aliased(User), aliased(User)
    stmt = (
        select(SecurityEvent, actor, target)
        .outerjoin(actor, actor.id == SecurityEvent.actor_id)
        .outerjoin(target, target.id == SecurityEvent.target_user_id)
    )
    if cursor:
        stmt = stmt.where(SecurityEvent.id < cursor)
    if event_type:
        stmt = stmt.where(SecurityEvent.event_type == event_type)
    if user_id:
        stmt = stmt.where((SecurityEvent.target_user_id == user_id) | (SecurityEvent.actor_id == user_id))
    rows = db.execute(stmt.order_by(SecurityEvent.id.desc()).limit(limit + 1)).all()

    def brief(u: User | None) -> UserBrief | None:
        return UserBrief(id=u.id, full_name=u.full_name, email=u.email) if u else None

    items = [
        SecurityEventOut(
            id=e.id,
            occurred_at=e.occurred_at,
            event_type=e.event_type,
            actor=brief(a),
            target=brief(t),
            ip_address=e.ip_address,
            details=e.details,
        )
        for e, a, t in rows[:limit]
    ]
    return SecurityEventPage(items=items, next_cursor=items[-1].id if len(rows) > limit else None)
