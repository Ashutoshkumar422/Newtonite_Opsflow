"""Append-only security audit log (logins, lockouts, session revocations, account changes).

Distinct from work-item Activity: these events are about identities and access, and are what a
security review or incident investigation needs ("who reset this password, from where?").
"""

from typing import Any

from sqlalchemy.orm import Session

from app.models import SecurityEvent

EVENT_TYPES = frozenset(
    {
        "login_succeeded",
        "login_failed",
        "login_locked",
        "logout",
        "session_revoked",
        "sessions_revoked",
        "password_changed",
        "password_reset",
        "user_created",
        "user_updated",
        "user_deactivated",
        "user_reactivated",
        "admin_granted",
        "admin_revoked",
        "team_created",
        "sso_login",
        "sso_linked",
        "sso_provisioned",
        "sso_rejected",
    }
)


def record(
    db: Session,
    event_type: str,
    *,
    actor_id: int | None = None,
    target_user_id: int | None = None,
    ip: str | None = None,
    **details: Any,
) -> None:
    assert event_type in EVENT_TYPES, event_type
    db.add(
        SecurityEvent(
            event_type=event_type,
            actor_id=actor_id,
            target_user_id=target_user_id,
            ip_address=ip,
            details=details,
        )
    )
