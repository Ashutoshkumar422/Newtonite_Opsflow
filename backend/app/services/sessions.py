"""Server-side sessions: issue, resolve, revoke.

Why sessions in the database instead of pure stateless JWTs: a stateless token stays valid until
it expires, so "log out", "sign out my other devices", "an admin disabled this account" and "the
password was reset" would not take effect for up to 8 hours. Here every request resolves the
token's `sid` to an active session row (one primary-key lookup joined to the user), so revocation
is immediate for every API replica.
"""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text, update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import create_access_token, decode_access_token
from app.db.session import get_engine
from app.models import User, UserSession

LAST_SEEN_RESOLUTION = timedelta(minutes=5)


def create_session(
    db: Session,
    user: User,
    method: str,
    *,
    ip: str | None = None,
    user_agent: str | None = None,
) -> tuple[UserSession, str]:
    now = datetime.now(UTC)
    session = UserSession(
        id=uuid.uuid4(),
        user_id=user.id,
        auth_method=method,
        created_at=now,
        last_seen_at=now,
        expires_at=now + timedelta(minutes=get_settings().access_token_ttl_minutes),
        ip_address=ip,
        user_agent=user_agent,
    )
    db.add(session)
    db.flush()
    return session, create_access_token(user.id, session.id, session.expires_at)


def resolve(db: Session, token: str) -> tuple[User, UserSession] | None:
    claims = decode_access_token(token)
    if claims is None:
        return None
    row = db.execute(
        select(User, UserSession)
        .join(UserSession, UserSession.user_id == User.id)
        .where(
            UserSession.id == claims.session_id,
            UserSession.user_id == claims.user_id,
            UserSession.revoked_at.is_(None),
            UserSession.expires_at > datetime.now(UTC),
        )
    ).first()
    if row is None:
        return None
    user, session = row
    if datetime.now(UTC) - session.last_seen_at > LAST_SEEN_RESOLUTION:
        _touch(session.id)
    return user, session


def _touch(session_id: uuid.UUID) -> None:
    """Best-effort 'last active' timestamp, written at most every 5 minutes per session on its
    own short connection so read-only requests don't need to commit."""
    try:
        with get_engine().begin() as conn:
            conn.execute(
                text("UPDATE sessions SET last_seen_at = now() WHERE id = :id AND revoked_at IS NULL"),
                {"id": session_id},
            )
    except Exception:  # noqa: BLE001 — never fail a request over a cosmetic timestamp
        pass


def revoke(db: Session, session_id: uuid.UUID, reason: str) -> bool:
    result = db.execute(
        update(UserSession)
        .where(UserSession.id == session_id, UserSession.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC), revoked_reason=reason)
    )
    return bool(result.rowcount)  # type: ignore[attr-defined]


def revoke_all(db: Session, user_id: int, reason: str, *, except_session: uuid.UUID | None = None) -> int:
    stmt = (
        update(UserSession)
        .where(UserSession.user_id == user_id, UserSession.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC), revoked_reason=reason)
    )
    if except_session is not None:
        stmt = stmt.where(UserSession.id != except_session)
    return db.execute(stmt).rowcount or 0  # type: ignore[attr-defined]


def active_sessions(db: Session, user_id: int) -> list[UserSession]:
    return list(
        db.execute(
            select(UserSession)
            .where(
                UserSession.user_id == user_id,
                UserSession.revoked_at.is_(None),
                UserSession.expires_at > datetime.now(UTC),
            )
            .order_by(UserSession.last_seen_at.desc())
        )
        .scalars()
        .all()
    )
