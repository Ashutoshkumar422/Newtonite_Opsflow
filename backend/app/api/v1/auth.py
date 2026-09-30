from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.auth.deps import AuthContext, get_auth, get_current_user
from app.core.config import get_settings
from app.core.errors import NotFound
from app.core.net import client_ip, user_agent
from app.core.security import UNUSABLE_PASSWORD
from app.db.session import get_session
from app.models import Team, TeamMembership, User
from app.schemas import AuthConfigOut, LoginIn, MembershipOut, MeOut, UserBrief
from app.services import auth as auth_svc
from app.services import oidc, security_log
from app.services import sessions as sessions_svc

router = APIRouter()
OIDC_STATE_COOKIE = "opsflow_oidc"


def me_out(db: Session, user: User, ctx: AuthContext | None = None, session_id=None, method=None) -> MeOut:
    rows = db.execute(
        select(TeamMembership.role, Team.id, Team.key, Team.name)
        .join(Team, Team.id == TeamMembership.team_id)
        .where(TeamMembership.user_id == user.id)
        .order_by(Team.name)
    ).all()
    return MeOut(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        is_admin=user.is_admin,
        memberships=[
            MembershipOut(team_id=tid, team_key=key, team_name=name, role=role)
            for role, tid, key, name in rows
        ],
        must_change_password=user.must_change_password,
        has_password=user.password_hash != UNUSABLE_PASSWORD,
        sso_linked=user.oidc_subject is not None,
        session_id=str(ctx.session.id if ctx else session_id) if (ctx or session_id) else None,
        auth_method=ctx.session.auth_method if ctx else method,
    )


def _set_session_cookie(response: Response, token: str) -> None:
    s = get_settings()
    response.set_cookie(
        s.cookie_name,
        token,
        max_age=s.access_token_ttl_minutes * 60,
        httponly=True,
        secure=s.cookie_secure,
        samesite="lax",
        path="/",
    )


@router.get("/auth/config", response_model=AuthConfigOut, tags=["auth"])
def auth_config() -> AuthConfigOut:
    s = get_settings()
    return AuthConfigOut(
        password_login_enabled=s.password_login_enabled,
        sso_enabled=s.oidc_enabled,
        sso_provider_name=s.oidc_provider_name if s.oidc_enabled else None,
        password_min_length=s.password_min_length,
    )


@router.post(
    "/auth/login",
    response_model=MeOut,
    tags=["auth"],
    responses={401: {"description": "invalid_credentials"}, 429: {"description": "too_many_attempts"}},
)
def login(data: LoginIn, request: Request, response: Response, db: Session = Depends(get_session)) -> MeOut:
    """Password sign-in for browsers. Creates a server-side session and sets the HttpOnly cookie.
    Throttled per account and per client address (429 with Retry-After when locked)."""
    user, session, token = auth_svc.password_login(
        db,
        data.email,
        data.password,
        method="password",
        ip=client_ip(request),
        user_agent=user_agent(request),
    )
    _set_session_cookie(response, token)
    return me_out(db, user, session_id=session.id, method=session.auth_method)


@router.post("/auth/token", tags=["auth"], summary="Obtain a bearer token (API clients)")
def token(data: LoginIn, request: Request, db: Session = Depends(get_session)) -> dict:
    """Same checks and throttling as /auth/login; returns a bearer token bound to a revocable session."""
    _, session, access_token = auth_svc.password_login(
        db,
        data.email,
        data.password,
        method="api_token",
        ip=client_ip(request),
        user_agent=user_agent(request),
    )
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "session_id": str(session.id),
        "expires_at": session.expires_at.isoformat(),
    }


@router.post("/auth/logout", status_code=204, tags=["auth"])
def logout(request: Request, response: Response, db: Session = Depends(get_session)) -> Response:
    """Revokes the current session server-side (the token stops working everywhere) and clears
    the cookie. Idempotent: calling it without a valid session still succeeds."""
    s = get_settings()
    raw = request.cookies.get(s.cookie_name)
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        raw = header[7:].strip()
    if raw:
        resolved = sessions_svc.resolve(db, raw)
        if resolved:
            user, session = resolved
            sessions_svc.revoke(db, session.id, "logout")
            security_log.record(db, "logout", actor_id=user.id, target_user_id=user.id, ip=client_ip(request))
            db.commit()
    response.delete_cookie(s.cookie_name, path="/")
    response.status_code = 204
    return response


@router.get("/auth/me", response_model=MeOut, tags=["auth"])
def me(ctx: AuthContext = Depends(get_auth), db: Session = Depends(get_session)) -> MeOut:
    return me_out(db, ctx.user, ctx)


# ---------------------------------------------------------------- SSO (OIDC)
@router.get("/auth/oidc/login", tags=["auth"], summary="Start single sign-on (browser redirect)")
def oidc_login(next: str | None = Query(None, max_length=500)) -> RedirectResponse:
    s = get_settings()
    if not s.oidc_enabled:
        raise NotFound("Single sign-on is not configured")
    try:
        url, state_cookie = oidc.begin(next)
    except oidc.OIDCError as exc:
        return RedirectResponse(f"/login?sso_error={exc.code}", status_code=302)
    response = RedirectResponse(url, status_code=302)
    response.set_cookie(
        OIDC_STATE_COOKIE,
        state_cookie,
        max_age=600,
        httponly=True,
        secure=s.cookie_secure,
        samesite="lax",  # sent on the top-level redirect back from the provider
        path="/api/v1/auth/oidc",
    )
    return response


@router.get("/auth/oidc/callback", tags=["auth"], summary="SSO redirect target")
def oidc_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    db: Session = Depends(get_session),
) -> RedirectResponse:
    s = get_settings()
    if not s.oidc_enabled:
        raise NotFound("Single sign-on is not configured")
    ip = client_ip(request)
    try:
        if error or not code or not state:
            raise oidc.OIDCError("provider_error", error or "missing code/state")
        _, _, access_token, next_path = oidc.complete(
            db,
            code=code,
            state=state,
            cookie=request.cookies.get(OIDC_STATE_COOKIE),
            ip=ip,
            user_agent=user_agent(request),
        )
    except oidc.OIDCError as exc:
        db.rollback()
        security_log.record(db, "sso_rejected", ip=ip, reason=exc.code, message=exc.message[:300])
        db.commit()
        response = RedirectResponse(f"/login?sso_error={exc.code}", status_code=302)
        response.delete_cookie(OIDC_STATE_COOKIE, path="/api/v1/auth/oidc")
        return response
    response = RedirectResponse(next_path, status_code=302)
    _set_session_cookie(response, access_token)
    response.delete_cookie(OIDC_STATE_COOKIE, path="/api/v1/auth/oidc")
    return response


# ---------------------------------------------------------------- directory
@router.get("/users", response_model=list[UserBrief], tags=["users"])
def search_users(
    q: str = Query("", max_length=100),
    limit: int = Query(20, ge=1, le=50),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> list[UserBrief]:
    """Internal directory lookup (used when adding team members)."""
    stmt = select(User).where(User.is_active.is_(True))
    if q.strip():
        term = f"%{q.strip().lower()}%"
        stmt = stmt.where(or_(func.lower(User.full_name).like(term), User.email.like(term)))
    rows = db.execute(stmt.order_by(User.full_name).limit(limit)).scalars().all()
    return [UserBrief(id=u.id, full_name=u.full_name, email=u.email) for u in rows]
