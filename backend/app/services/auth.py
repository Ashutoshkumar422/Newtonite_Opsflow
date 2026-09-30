"""Authentication use cases: password login and the password lifecycle."""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import Forbidden, NotAuthenticated, ValidationFailed
from app.core.security import UNUSABLE_PASSWORD, hash_password, validate_password, verify_password
from app.models import User, UserSession
from app.services import security_log, sessions, throttle

# Always run one bcrypt comparison, even for unknown emails, so response time does not reveal
# whether an account exists.
_DUMMY_HASH = hash_password("timing-equaliser-not-a-real-password")


def password_login(
    db: Session, email: str, password: str, *, method: str, ip: str, user_agent: str
) -> tuple[User, UserSession, str]:
    s = get_settings()
    normalised = email.strip().lower()
    acct_key, net_key = throttle.account_key(normalised), throttle.ip_key(ip)
    throttle.ensure_not_locked(db, [acct_key, net_key])

    user = db.execute(select(User).where(User.email == normalised)).scalar_one_or_none()
    ok = verify_password(password, user.password_hash if user else _DUMMY_HASH)
    if not user or not ok or not user.is_active:
        # The request fails, but the failure must persist: record it in its own transaction.
        db.rollback()
        locked = throttle.register_failure(db, acct_key, s.login_max_failures_per_account)
        throttle.register_failure(db, net_key, s.login_max_failures_per_ip)
        target = user.id if user else None
        reason = "unknown_email" if not user else ("inactive" if not user.is_active else "bad_password")
        security_log.record(db, "login_failed", target_user_id=target, ip=ip, email=normalised, reason=reason)
        if locked:
            security_log.record(db, "login_locked", target_user_id=target, ip=ip, email=normalised)
        db.commit()
        raise NotAuthenticated("Invalid email or password", code="invalid_credentials")

    if not s.password_login_enabled and not user.is_admin:
        raise Forbidden("Password sign-in is disabled; use single sign-on", code="password_login_disabled")

    throttle.register_success(db, acct_key)
    session, token = sessions.create_session(db, user, method, ip=ip, user_agent=user_agent)
    user.last_login_at = datetime.now(UTC)
    security_log.record(db, "login_succeeded", actor_id=user.id, target_user_id=user.id, ip=ip, method=method)
    db.commit()
    return user, session, token


def change_own_password(
    db: Session,
    user: User,
    current_session: UserSession,
    current_password: str,
    new_password: str,
    *,
    ip: str,
) -> int:
    """Returns the number of other sessions that were signed out."""
    s = get_settings()
    if user.password_hash == UNUSABLE_PASSWORD:
        raise Forbidden("This account signs in with single sign-on and has no password", code="sso_account")
    acct_key = throttle.account_key(user.email)
    throttle.ensure_not_locked(db, [acct_key])
    if not verify_password(current_password, user.password_hash):
        db.rollback()
        throttle.register_failure(db, acct_key, s.login_max_failures_per_account)
        db.commit()
        raise ValidationFailed(
            "Current password is incorrect",
            code="invalid_current_password",
            details={"field": "current_password"},
        )
    if current_password == new_password:
        raise ValidationFailed(
            "The new password must be different", code="weak_password", details={"field": "new_password"}
        )
    validate_password(new_password, email=user.email, full_name=user.full_name)

    user.password_hash = hash_password(new_password)
    user.must_change_password = False
    user.password_changed_at = datetime.now(UTC)
    revoked = sessions.revoke_all(db, user.id, "password_changed", except_session=current_session.id)
    security_log.record(
        db,
        "password_changed",
        actor_id=user.id,
        target_user_id=user.id,
        ip=ip,
        other_sessions_revoked=revoked,
    )
    db.commit()
    return revoked
