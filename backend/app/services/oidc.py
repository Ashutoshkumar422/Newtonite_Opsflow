"""OpenID Connect single sign-on (authorization code flow + PKCE).

Flow
  1. GET /auth/oidc/login   → we create `state`, `nonce` and a PKCE `code_verifier`, keep them in a
     short-lived signed HttpOnly cookie, and redirect the browser to the provider.
  2. Provider authenticates the user and redirects to /auth/oidc/callback?code&state.
  3. We check `state` against the cookie (CSRF on the login flow), exchange the code at the token
     endpoint server-to-server (with client secret + code_verifier), and verify the ID token:
     signature against the provider's JWKS, `iss`, `aud`, `exp`/`iat`, `nonce` (replay), and
     `email_verified` when present.
  4. The identity is mapped to an OpsFlow account: by (issuer, subject) if linked before; otherwise
     by verified email (and the link is recorded); otherwise auto-provisioned when enabled for the
     email's domain; otherwise rejected. Teams/roles are always managed in OpsFlow.
  5. A normal server-side session is created — SSO sessions are revocable like any other.
"""

import base64
import hashlib
import secrets
import time
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import UNUSABLE_PASSWORD, read_state, sign_state
from app.models import User, UserSession
from app.services import security_log, sessions

CACHE_SECONDS = 3600
_cache: dict[str, tuple[float, Any]] = {}


class OIDCError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def http_client() -> httpx.Client:
    """Factory so tests can substitute an in-process identity provider."""
    return httpx.Client(timeout=10)


def _cached(key: str, loader) -> Any:
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < CACHE_SECONDS:
        return hit[1]
    value = loader()
    _cache[key] = (time.monotonic(), value)
    return value


def clear_cache() -> None:
    _cache.clear()


def _issuer() -> str:
    s = get_settings()
    assert s.oidc_issuer
    return s.oidc_issuer.rstrip("/")


def discovery() -> dict[str, Any]:
    s = get_settings()
    url = s.oidc_discovery_url or f"{_issuer()}/.well-known/openid-configuration"

    def load() -> dict[str, Any]:
        with http_client() as client:
            r = client.get(url)
            r.raise_for_status()
            doc = r.json()
        if doc.get("issuer", "").rstrip("/") != _issuer():
            raise OIDCError("provider_misconfigured", "Discovery issuer does not match configuration")
        return doc

    try:
        return _cached(f"discovery:{url}", load)
    except httpx.HTTPError as exc:
        raise OIDCError("provider_unreachable", f"Identity provider unreachable: {exc}") from exc


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def safe_next(path: str | None) -> str:
    """Only same-site relative paths; blocks open redirects like //evil.com or https://…"""
    if not path or not path.startswith("/") or path.startswith("//") or "\\" in path:
        return "/"
    return path


def begin(next_path: str | None) -> tuple[str, str]:
    """Returns (authorization URL, signed state cookie value)."""
    s = get_settings()
    doc = discovery()
    state = secrets.token_urlsafe(24)
    nonce = secrets.token_urlsafe(24)
    verifier = secrets.token_urlsafe(64)
    challenge = _b64url(hashlib.sha256(verifier.encode()).digest())
    params = {
        "response_type": "code",
        "client_id": s.oidc_client_id,
        "redirect_uri": s.oidc_redirect_uri,
        "scope": s.oidc_scopes,
        "state": state,
        "nonce": nonce,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    cookie = sign_state({"state": state, "nonce": nonce, "verifier": verifier, "next": safe_next(next_path)})
    return f"{doc['authorization_endpoint']}?{urlencode(params)}", cookie


def _signing_key(kid: str | None, alg: str) -> Any:
    doc = discovery()

    def load() -> dict[str, Any]:
        with http_client() as client:
            r = client.get(doc["jwks_uri"])
            r.raise_for_status()
            return r.json()

    for attempt in range(2):
        jwks = _cached(f"jwks:{doc['jwks_uri']}", load)
        for key in jwks.get("keys", []):
            if kid is None or key.get("kid") == kid:
                return jwt.PyJWK(key, algorithm=alg).key
        # Unknown kid: the provider may have rotated keys — refetch once.
        _cache.pop(f"jwks:{doc['jwks_uri']}", None)
        if attempt:
            break
    raise OIDCError("invalid_id_token", "No matching signing key")


def verify_id_token(id_token: str, nonce: str) -> dict[str, Any]:
    s = get_settings()
    try:
        header = jwt.get_unverified_header(id_token)
        alg = header.get("alg", "")
        if alg not in ("RS256", "ES256", "PS256"):
            raise OIDCError("invalid_id_token", f"Unsupported signing algorithm {alg!r}")
        key = _signing_key(header.get("kid"), alg)
        claims = jwt.decode(
            id_token,
            key,
            algorithms=[alg],
            audience=s.oidc_client_id,
            issuer=_issuer(),
            leeway=60,
            options={"require": ["exp", "iat", "sub", "iss", "aud"]},
        )
    except jwt.PyJWTError as exc:
        raise OIDCError("invalid_id_token", f"ID token rejected: {exc}") from exc
    if not secrets.compare_digest(str(claims.get("nonce", "")), nonce):
        raise OIDCError("invalid_id_token", "Nonce mismatch")
    if claims.get("email_verified") is False:
        raise OIDCError("email_not_verified", "The identity provider has not verified this email")
    if not claims.get("email"):
        raise OIDCError("email_missing", "The identity provider did not supply an email address")
    return claims


def _exchange_code(code: str, verifier: str) -> dict[str, Any]:
    s = get_settings()
    doc = discovery()
    try:
        with http_client() as client:
            r = client.post(
                doc["token_endpoint"],
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": s.oidc_redirect_uri,
                    "client_id": s.oidc_client_id,
                    "client_secret": s.oidc_client_secret or "",
                    "code_verifier": verifier,
                },
                headers={"Accept": "application/json"},
            )
    except httpx.HTTPError as exc:
        raise OIDCError("provider_unreachable", f"Token endpoint unreachable: {exc}") from exc
    if r.status_code != 200:
        raise OIDCError("code_exchange_failed", f"Token endpoint returned {r.status_code}")
    body = r.json()
    if "id_token" not in body:
        raise OIDCError("code_exchange_failed", "No id_token in token response")
    return body


def _map_user(db: Session, claims: dict[str, Any], ip: str) -> User:
    s = get_settings()
    issuer, subject = _issuer(), str(claims["sub"])
    email = str(claims["email"]).strip().lower()

    user = db.execute(
        select(User).where(User.oidc_issuer == issuer, User.oidc_subject == subject).with_for_update()
    ).scalar_one_or_none()
    if user is None:
        user = db.execute(select(User).where(User.email == email).with_for_update()).scalar_one_or_none()
        if user is not None:
            if user.oidc_subject is not None:
                raise OIDCError("identity_conflict", "This account is linked to a different SSO identity")
            user.oidc_issuer, user.oidc_subject = issuer, subject
            security_log.record(
                db, "sso_linked", actor_id=user.id, target_user_id=user.id, ip=ip, email=email
            )
        else:
            domain = email.rsplit("@", 1)[-1]
            allowed = [d.lower() for d in s.oidc_allowed_domains]
            if not s.oidc_auto_provision or (allowed and domain not in allowed):
                raise OIDCError("no_account", "No OpsFlow account exists for this email")
            name = str(claims.get("name") or email.split("@")[0]).strip()[:120] or email
            user = User(
                email=email,
                full_name=name,
                password_hash=UNUSABLE_PASSWORD,
                is_admin=False,
                is_active=True,
                must_change_password=False,
                oidc_issuer=issuer,
                oidc_subject=subject,
            )
            db.add(user)
            db.flush()
            security_log.record(db, "sso_provisioned", target_user_id=user.id, ip=ip, email=email)
    if not user.is_active:
        raise OIDCError("account_disabled", "This OpsFlow account has been deactivated")
    return user


def complete(
    db: Session, *, code: str, state: str, cookie: str | None, ip: str, user_agent: str
) -> tuple[User, UserSession, str, str]:
    """Returns (user, session, access token, next path)."""
    saved = read_state(cookie) if cookie else None
    if not saved or not secrets.compare_digest(saved.get("state", ""), state or ""):
        raise OIDCError("state_mismatch", "Sign-in session expired or was tampered with; please retry")
    tokens = _exchange_code(code, saved["verifier"])
    claims = verify_id_token(tokens["id_token"], saved["nonce"])
    user = _map_user(db, claims, ip)
    session, token = sessions.create_session(db, user, "sso", ip=ip, user_agent=user_agent)
    user.last_login_at = datetime.now(UTC)
    security_log.record(
        db, "sso_login", actor_id=user.id, target_user_id=user.id, ip=ip, subject=claims["sub"]
    )
    db.commit()
    return user, session, token, safe_next(saved.get("next"))
