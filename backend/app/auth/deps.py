"""Authentication dependencies.

Accepts either:
  * the HttpOnly session cookie set by POST /auth/login or the SSO callback (browser), or
  * an `Authorization: Bearer <token>` header (API clients, tests).

Either way the token must name an *active server-side session* (see services/sessions.py), so
logout, admin revocation, deactivation and password resets take effect on the next request.

CSRF: cookie-authenticated unsafe requests must carry `X-Requested-With: opsflow`. Browsers
cannot attach a custom header cross-origin without a CORS preflight, which our CORS allowlist
rejects — combined with SameSite=Lax cookies this blocks cross-site request forgery.

Accounts with a temporary password (`must_change_password`) may only reach /auth/* and
/account/* until they choose their own password — enforced here, not just in the UI.
"""

from dataclasses import dataclass

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import Forbidden, NotAuthenticated
from app.db.session import get_session
from app.models import User, UserSession
from app.services import sessions

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
CSRF_HEADER = "x-requested-with"
CSRF_VALUE = "opsflow"
PASSWORD_CHANGE_ALLOWED_PREFIXES = ("/api/v1/auth/", "/api/v1/account/")


@dataclass
class AuthContext:
    user: User
    session: UserSession
    via_cookie: bool


def get_auth(request: Request, db: Session = Depends(get_session)) -> AuthContext:
    token: str | None = None
    via_cookie = False
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        token = auth[7:].strip()
    else:
        token = request.cookies.get(get_settings().cookie_name)
        via_cookie = token is not None

    if not token:
        raise NotAuthenticated("Authentication required")
    resolved = sessions.resolve(db, token)
    if resolved is None:
        raise NotAuthenticated("Session is invalid, expired or was signed out", code="session_expired")
    user, session = resolved
    if not user.is_active:
        raise NotAuthenticated("Account is not active", code="account_disabled")

    if via_cookie and request.method not in SAFE_METHODS and request.headers.get(CSRF_HEADER) != CSRF_VALUE:
        raise Forbidden("Missing CSRF header", code="csrf_failed")

    if user.must_change_password and not request.url.path.startswith(PASSWORD_CHANGE_ALLOWED_PREFIXES):
        raise Forbidden(
            "You must change your temporary password before continuing", code="password_change_required"
        )
    return AuthContext(user=user, session=session, via_cookie=via_cookie)


def get_current_user(ctx: AuthContext = Depends(get_auth)) -> User:
    return ctx.user


def require_admin(user: User = Depends(get_current_user)) -> User:
    if not user.is_admin:
        raise Forbidden("Administrator role required")
    return user
