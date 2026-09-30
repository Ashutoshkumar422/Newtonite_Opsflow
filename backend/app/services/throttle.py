"""Login throttling backed by PostgreSQL.

Two independent fixed-window counters per attempt:
  * account key  "email:<normalised address>" — 5 failures / 15 min ⇒ that account is locked for
    15 min (defeats password guessing against one user). Applied to any submitted address, existing
    or not, so a lockout never reveals whether an account exists.
  * network key  "ip:<client address>"       — 30 failures / 15 min ⇒ that address is locked
    (defeats password spraying across many accounts).

Counters are updated with a single atomic upsert, so concurrent failures from many API replicas
are all counted. Failures are committed in their own transaction because the request itself fails.
A successful login clears the account counter (not the IP counter).
"""

import math
from datetime import UTC, datetime

from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import TooManyRequests
from app.models import AuthThrottle


def account_key(email: str) -> str:
    return f"email:{email.strip().lower()}"[:320]


def ip_key(ip: str) -> str:
    return f"ip:{ip}"[:320]


def ensure_not_locked(db: Session, keys: list[str]) -> None:
    now = datetime.now(UTC)
    locked = db.execute(
        select(AuthThrottle.key, AuthThrottle.locked_until).where(
            AuthThrottle.key.in_(keys), AuthThrottle.locked_until > now
        )
    ).all()
    if locked:
        until = max(r.locked_until for r in locked)
        retry_after = max(1, math.ceil((until - now).total_seconds()))
        raise TooManyRequests(
            "Too many failed sign-in attempts. Try again later.",
            details={"retry_after_seconds": retry_after},
            headers={"Retry-After": str(retry_after)},
        )


_UPSERT = text(
    """
    INSERT INTO auth_throttle (key, failures, window_started_at)
    VALUES (:key, 1, now())
    ON CONFLICT (key) DO UPDATE SET
        failures = CASE WHEN auth_throttle.window_started_at < now() - make_interval(mins => :window)
                        THEN 1 ELSE auth_throttle.failures + 1 END,
        window_started_at = CASE WHEN auth_throttle.window_started_at < now() - make_interval(mins => :window)
                        THEN now() ELSE auth_throttle.window_started_at END,
        locked_until = CASE WHEN auth_throttle.locked_until <= now() THEN NULL
                        ELSE auth_throttle.locked_until END
    RETURNING failures
    """
)


def register_failure(db: Session, key: str, limit: int) -> bool:
    """Count one failure; lock the key when the limit is reached. Returns True if now locked."""
    s = get_settings()
    failures: int = db.execute(_UPSERT, {"key": key, "window": s.login_failure_window_minutes}).scalar_one()
    if failures >= limit:
        db.execute(
            text(
                "UPDATE auth_throttle SET locked_until = now() + make_interval(mins => :m), failures = 0, "
                "window_started_at = now() WHERE key = :key"
            ),
            {"key": key, "m": s.login_lockout_minutes},
        )
        return True
    return False


def register_success(db: Session, key: str) -> None:
    db.execute(delete(AuthThrottle).where(AuthThrottle.key == key))
