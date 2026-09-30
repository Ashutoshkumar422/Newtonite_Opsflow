"""Password hashing, password policy, and access tokens.

An access token is a short signed JWT that names a *server-side session* (`sid`). The token alone
is not sufficient: `app.services.sessions.resolve` also requires the session row to be active,
which is what makes logout and revocation immediate.
"""

import secrets
import string
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import bcrypt
import jwt

from app.core.config import get_settings
from app.core.errors import ValidationFailed

UNUSABLE_PASSWORD = "!"  # stored for SSO-only accounts; can never match a bcrypt check


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=12)).decode()


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), password_hash.encode())
    except ValueError:  # unusable or malformed hash
        return False


def validate_password(password: str, *, email: str = "", full_name: str = "") -> None:
    """Length-based policy (NIST SP 800-63B style): long enough, not trivially personal."""
    s = get_settings()
    problems = []
    if len(password) < s.password_min_length:
        problems.append(f"at least {s.password_min_length} characters")
    if len(password) > 128:
        problems.append("at most 128 characters")
    lowered = password.lower()
    local_part = email.split("@")[0].lower()
    if local_part and len(local_part) >= 3 and local_part in lowered:
        problems.append("must not contain your email name")
    if full_name and any(len(p) >= 3 and p.lower() in lowered for p in full_name.split()):
        problems.append("must not contain your name")
    if len(set(password)) < 4:
        problems.append("too repetitive")
    if problems:
        raise ValidationFailed(
            "Password does not meet the policy: " + "; ".join(problems),
            code="weak_password",
            details={"field": "new_password", "problems": problems},
        )


def generate_temporary_password() -> str:
    """16 characters from an unambiguous alphabet, ~90 bits of entropy."""
    alphabet = "".join(c for c in string.ascii_letters + string.digits if c not in "Il1O0o")
    return "-".join("".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(4))


@dataclass(frozen=True)
class TokenClaims:
    user_id: int
    session_id: uuid.UUID


def create_access_token(user_id: int, session_id: uuid.UUID, expires_at: datetime | None = None) -> str:
    s = get_settings()
    now = datetime.now(UTC)
    exp = expires_at or now + timedelta(minutes=s.access_token_ttl_minutes)
    payload = {
        "sub": str(user_id),
        "sid": str(session_id),
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
        "typ": "access",
    }
    return jwt.encode(payload, s.jwt_secret, algorithm=s.jwt_algorithm)


def decode_access_token(token: str) -> TokenClaims | None:
    s = get_settings()
    try:
        payload = jwt.decode(token, s.jwt_secret, algorithms=[s.jwt_algorithm])
    except jwt.PyJWTError:
        return None
    if payload.get("typ") != "access":
        return None
    try:
        return TokenClaims(user_id=int(payload["sub"]), session_id=uuid.UUID(payload["sid"]))
    except (KeyError, ValueError, TypeError):
        return None


def sign_state(data: dict, minutes: int = 10) -> str:
    """Short-lived signed blob (used for the OIDC state/nonce/PKCE cookie)."""
    s = get_settings()
    payload = {
        **data,
        "typ": "oidc_state",
        "exp": int((datetime.now(UTC) + timedelta(minutes=minutes)).timestamp()),
    }
    return jwt.encode(payload, s.jwt_secret, algorithm=s.jwt_algorithm)


def read_state(token: str) -> dict | None:
    s = get_settings()
    try:
        payload = jwt.decode(token, s.jwt_secret, algorithms=[s.jwt_algorithm])
    except jwt.PyJWTError:
        return None
    return payload if payload.get("typ") == "oidc_state" else None
