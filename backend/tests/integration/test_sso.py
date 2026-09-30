"""OIDC single sign-on against an in-process identity provider (app.devtools.dev_oidc_provider).

The backend's outbound HTTP client is pointed at the provider app, and the "browser" steps are
performed with test clients, so the full authorization-code + PKCE flow runs end to end.
"""

import time
from urllib.parse import parse_qs, urlparse

import jwt
import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.devtools.dev_oidc_provider import create_app
from app.services import oidc
from tests.conftest import Api

ISSUER = "http://idp.test"
CLIENT_ID = "opsflow-test"
SECRET = "test-secret"


@pytest.fixture
def idp(monkeypatch):
    provider = create_app(ISSUER, CLIENT_ID, SECRET, unverified_emails=("unverified@test.dev",))
    s = get_settings()
    for k, v in {
        "oidc_issuer": ISSUER,
        "oidc_client_id": CLIENT_ID,
        "oidc_client_secret": SECRET,
        "oidc_redirect_uri": "http://testserver/api/v1/auth/oidc/callback",
        "oidc_auto_provision": False,
        "oidc_allowed_domains": [],
    }.items():
        monkeypatch.setattr(s, k, v)
    monkeypatch.setattr(oidc, "http_client", lambda: TestClient(provider, base_url=ISSUER))
    oidc.clear_cache()
    yield provider
    oidc.clear_cache()


def sso_login(api: Api, idp, email: str, next_path: str = "/work-items"):
    """Drive the browser side of the flow. Returns the final callback response."""
    start = api.client.get("/api/v1/auth/oidc/login", params={"next": next_path}, follow_redirects=False)
    assert start.status_code == 302, start.text
    authorize = urlparse(start.headers["location"])
    assert f"{authorize.scheme}://{authorize.netloc}" == ISSUER
    params = {k: v[0] for k, v in parse_qs(authorize.query).items()}
    assert params["code_challenge_method"] == "S256" and params["nonce"] and params["state"]
    # The user signs in at the provider, which redirects back with a code.
    with TestClient(idp, base_url=ISSUER) as browser_at_idp:
        back = browser_at_idp.post("/authorize", data={**params, "email": email}, follow_redirects=False)
    callback = urlparse(back.headers["location"])
    return api.client.get(f"{callback.path}?{callback.query}", follow_redirects=False)


def test_sso_login_links_existing_account_and_creates_session(api, world, idp):
    u = world.user("Sso Person")
    r = sso_login(api, idp, u.email, next_path="/work-items?status=open")
    assert r.status_code == 302 and r.headers["location"] == "/work-items?status=open"
    me = api.client.get("/api/v1/auth/me").json()
    assert me["id"] == u.id and me["auth_method"] == "sso" and me["sso_linked"] is True
    assert world.scalar("SELECT count(*) FROM security_events WHERE event_type='sso_linked'") == 1
    # Second login matches by (issuer, subject); no re-linking.
    fresh = Api()
    assert sso_login(fresh, idp, u.email).status_code == 302
    assert fresh.client.get("/api/v1/auth/me").json()["id"] == u.id
    assert world.scalar("SELECT count(*) FROM security_events WHERE event_type='sso_linked'") == 1
    assert world.scalar("SELECT count(*) FROM security_events WHERE event_type='sso_login'") == 2


def test_sso_sessions_are_revocable(api, world, idp):
    u = world.user("Revocable Sso")
    sso_login(api, idp, u.email)
    cookie = dict(api.client.cookies)
    api.client.post("/api/v1/auth/logout", headers={"X-Requested-With": "opsflow"})
    replay = Api()
    for k, v in cookie.items():
        replay.client.cookies.set(k, v)
    assert replay.client.get("/api/v1/auth/me").status_code == 401


def test_unknown_email_is_rejected_without_auto_provisioning(api, world, idp):
    r = sso_login(api, idp, "stranger@test.dev")
    assert r.status_code == 302 and r.headers["location"] == "/login?sso_error=no_account"
    assert api.client.get("/api/v1/auth/me").status_code == 401
    assert (
        world.scalar("SELECT details->>'reason' FROM security_events WHERE event_type='sso_rejected'")
        == "no_account"
    )


def test_auto_provisioning_respects_allowed_domains(api, world, idp, monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "oidc_auto_provision", True)
    monkeypatch.setattr(s, "oidc_allowed_domains", ["corp.test"])
    assert sso_login(api, idp, "outsider@gmail.test").headers["location"] == "/login?sso_error=no_account"
    r = sso_login(api, idp, "jane.doe@corp.test")
    assert r.headers["location"] == "/work-items"
    me = api.client.get("/api/v1/auth/me").json()
    assert me["email"] == "jane.doe@corp.test" and me["memberships"] == [] and me["has_password"] is False
    assert me["is_admin"] is False
    # Provisioned SSO accounts have no usable password.
    r = api.client.post("/api/v1/auth/login", json={"email": "jane.doe@corp.test", "password": "!"})
    assert r.status_code == 401


def test_unverified_email_is_rejected(api, world, idp):
    world.user("Unverified")  # irrelevant account
    r = sso_login(api, idp, "unverified@test.dev")
    assert r.headers["location"] == "/login?sso_error=email_not_verified"


def test_deactivated_account_cannot_sign_in_with_sso(api, world, idp):
    u = world.user("Gone Sso")
    world.execute("UPDATE users SET is_active = false WHERE id = :i", i=u.id)
    assert sso_login(api, idp, u.email).headers["location"] == "/login?sso_error=account_disabled"


def test_callback_without_matching_state_is_rejected(api, world, idp):
    u = world.user("State Check")
    # Attacker-initiated callback: the victim's browser has no state cookie for this login.
    attacker = Api()
    start = attacker.client.get("/api/v1/auth/oidc/login", follow_redirects=False)
    params = {k: v[0] for k, v in parse_qs(urlparse(start.headers["location"]).query).items()}
    with TestClient(idp, base_url=ISSUER) as browser:
        back = browser.post("/authorize", data={**params, "email": u.email}, follow_redirects=False)
    cb = urlparse(back.headers["location"])
    victim = Api()
    r = victim.client.get(f"{cb.path}?{cb.query}", follow_redirects=False)
    assert r.headers["location"] == "/login?sso_error=state_mismatch"
    assert victim.client.get("/api/v1/auth/me").status_code == 401


def test_authorization_code_is_single_use(api, world, idp):
    u = world.user("Replay Code")
    start = api.client.get("/api/v1/auth/oidc/login", follow_redirects=False)
    params = {k: v[0] for k, v in parse_qs(urlparse(start.headers["location"]).query).items()}
    with TestClient(idp, base_url=ISSUER) as browser:
        back = browser.post("/authorize", data={**params, "email": u.email}, follow_redirects=False)
    cb = urlparse(back.headers["location"])
    cookies = dict(api.client.cookies)
    assert api.client.get(f"{cb.path}?{cb.query}", follow_redirects=False).headers["location"] == "/"
    replay = Api()
    for k, v in cookies.items():
        replay.client.cookies.set(k, v, path="/api/v1/auth/oidc")
    r = replay.client.get(f"{cb.path}?{cb.query}", follow_redirects=False)
    assert r.headers["location"] == "/login?sso_error=code_exchange_failed"


def _token(idp, **overrides) -> str:
    now = int(time.time())
    claims = {
        "iss": ISSUER,
        "aud": CLIENT_ID,
        "sub": "s1",
        "email": "a@test.dev",
        "nonce": "n1",
        "iat": now,
        "exp": now + 300,
    }
    claims.update(overrides)
    return jwt.encode(claims, idp.state.signing_key, algorithm="RS256", headers={"kid": idp.state.kid})


@pytest.mark.parametrize(
    "overrides,expected",
    [
        ({"nonce": "other"}, "invalid_id_token"),
        ({"aud": "someone-else"}, "invalid_id_token"),
        ({"iss": "http://evil.test"}, "invalid_id_token"),
        ({"exp": int(time.time()) - 3600, "iat": int(time.time()) - 7200}, "invalid_id_token"),
        ({"email_verified": False}, "email_not_verified"),
        ({"email": None}, "email_missing"),
    ],
)
def test_id_token_validation(idp, overrides, expected):
    token = _token(idp, **overrides)
    with pytest.raises(oidc.OIDCError) as e:
        oidc.verify_id_token(token, "n1")
    assert e.value.code == expected


def test_id_token_signed_by_another_key_is_rejected(idp):
    from cryptography.hazmat.primitives.asymmetric import rsa

    rogue = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = int(time.time())
    forged = jwt.encode(
        {
            "iss": ISSUER,
            "aud": CLIENT_ID,
            "sub": "s",
            "email": "a@test.dev",
            "nonce": "n1",
            "iat": now,
            "exp": now + 60,
        },
        rogue,
        algorithm="RS256",
        headers={"kid": idp.state.kid},
    )
    with pytest.raises(oidc.OIDCError) as e:
        oidc.verify_id_token(forged, "n1")
    assert e.value.code == "invalid_id_token"
    assert oidc.verify_id_token(_token(idp), "n1")["sub"] == "s1"  # the genuine one passes


def test_unsigned_tokens_are_rejected(idp):
    forged = jwt.encode({"iss": ISSUER, "aud": CLIENT_ID, "sub": "s", "nonce": "n1"}, None, algorithm="none")
    with pytest.raises(oidc.OIDCError):
        oidc.verify_id_token(forged, "n1")


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("/work-items/5", "/work-items/5"),
        ("//evil.test/x", "/"),
        ("https://evil.test", "/"),
        ("\\\\evil", "/"),
        (None, "/"),
    ],
)
def test_post_login_redirect_cannot_leave_the_site(raw, expected):
    assert oidc.safe_next(raw) == expected


def test_sso_endpoints_404_when_not_configured(api):
    assert get_settings().oidc_issuer is None
    assert api.client.get("/api/v1/auth/oidc/login", follow_redirects=False).status_code == 404
    cfg = api.client.get("/api/v1/auth/config").json()
    assert cfg["sso_enabled"] is False and cfg["password_login_enabled"] is True
