"""Identity & access: revocable sessions, login throttling, password lifecycle, user administration."""

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy.exc import DBAPIError

from app.core.config import get_settings
from tests.conftest import PASSWORD, Api


@pytest.fixture
def settings_override():
    """Temporarily change cached settings for one test."""
    s = get_settings()
    saved: dict = {}

    def set_(**kw):
        for k, v in kw.items():
            saved.setdefault(k, getattr(s, k))
            setattr(s, k, v)

    yield set_
    for k, v in saved.items():
        setattr(s, k, v)


def login(api: Api, email: str, password: str = PASSWORD, ip: str | None = None):
    headers = {"X-Forwarded-For": ip} if ip else {}
    return api.client.post("/api/v1/auth/login", json={"email": email, "password": password}, headers=headers)


def bearer(api: Api, email: str, password: str = PASSWORD) -> str:
    r = api.client.post("/api/v1/auth/token", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


CSRF = {"X-Requested-With": "opsflow"}


# ---------------------------------------------------------------- sessions
def test_logout_revokes_the_session_server_side(api, world):
    u = world.user("Sam Session")
    assert login(api, u.email).status_code == 200
    stolen_cookie = dict(api.client.cookies)
    assert api.client.get("/api/v1/auth/me").status_code == 200
    assert api.client.post("/api/v1/auth/logout", headers=CSRF).status_code == 204
    # Replaying the (still unexpired) token after logout must fail.
    replay = Api()
    for k, v in stolen_cookie.items():
        replay.client.cookies.set(k, v)
    r = replay.client.get("/api/v1/auth/me")
    assert r.status_code == 401 and r.json()["error"]["code"] == "session_expired"
    assert world.scalar("SELECT revoked_reason FROM sessions") == "logout"


def test_bearer_tokens_are_sessions_too(api, world):
    u = world.user("Tina Token")
    t = bearer(api, u.email)
    assert api.client.get("/api/v1/auth/me", headers=auth(t)).json()["auth_method"] == "api_token"
    api.client.post("/api/v1/auth/logout", headers=auth(t))
    assert api.client.get("/api/v1/auth/me", headers=auth(t)).status_code == 401


def test_sign_out_other_devices(api, world):
    u = world.user("Multi Device")
    laptop, phone, tablet = bearer(api, u.email), bearer(api, u.email), bearer(api, u.email)
    sessions = api.client.get("/api/v1/account/sessions", headers=auth(laptop)).json()
    assert len(sessions) == 3 and sum(s["current"] for s in sessions) == 1
    r = api.client.post("/api/v1/account/sessions/revoke-others", headers=auth(laptop))
    assert r.json() == {"revoked": 2}
    assert api.client.get("/api/v1/auth/me", headers=auth(laptop)).status_code == 200
    assert api.client.get("/api/v1/auth/me", headers=auth(phone)).status_code == 401
    assert api.client.get("/api/v1/auth/me", headers=auth(tablet)).status_code == 401


def test_revoke_single_session_and_cannot_touch_others(api, world):
    a, b = world.user("Alpha"), world.user("Beta")
    a1, a2, b1 = bearer(api, a.email), bearer(api, a.email), bearer(api, b.email)
    a_sessions = api.client.get("/api/v1/account/sessions", headers=auth(a1)).json()
    other = next(s["id"] for s in a_sessions if not s["current"])
    # Beta cannot revoke Alpha's session (and cannot learn it exists)
    assert api.client.delete(f"/api/v1/account/sessions/{other}", headers=auth(b1)).status_code == 404
    assert api.client.delete(f"/api/v1/account/sessions/{other}", headers=auth(a1)).status_code == 204
    assert api.client.get("/api/v1/auth/me", headers=auth(a2)).status_code == 401
    assert api.client.get("/api/v1/auth/me", headers=auth(a1)).status_code == 200


def test_forged_or_foreign_session_ids_are_rejected(api, world):
    import uuid

    from app.core.security import create_access_token

    a, b = world.user("Owner"), world.user("Intruder")
    t = bearer(api, a.email)
    sid = api.client.get("/api/v1/auth/me", headers=auth(t)).json()["session_id"]
    # A validly signed token that claims someone else's session is useless.
    forged = create_access_token(b.id, uuid.UUID(sid))
    assert api.client.get("/api/v1/auth/me", headers=auth(forged)).status_code == 401
    unknown = create_access_token(a.id, uuid.uuid4())
    assert api.client.get("/api/v1/auth/me", headers=auth(unknown)).status_code == 401


def test_expired_session_is_rejected(api, world):
    u = world.user("Expiring")
    t = bearer(api, u.email)
    world.execute(
        "UPDATE sessions SET created_at = now() - interval '2 days', expires_at = now() - interval '1 second'"
    )
    assert api.client.get("/api/v1/auth/me", headers=auth(t)).status_code == 401


# ---------------------------------------------------------------- password lifecycle
def test_change_password_revokes_other_sessions(api, world):
    u = world.user("Pat Password")
    here, elsewhere = bearer(api, u.email), bearer(api, u.email)
    bad = api.client.post(
        "/api/v1/account/password",
        headers=auth(here),
        json={"current_password": "wrong", "new_password": "x"},
    )
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "invalid_current_password"
    weak = api.client.post(
        "/api/v1/account/password",
        headers=auth(here),
        json={"current_password": PASSWORD, "new_password": "short"},
    )
    assert weak.status_code == 422 and weak.json()["error"]["code"] == "weak_password"
    personal = api.client.post(
        "/api/v1/account/password",
        headers=auth(here),
        json={"current_password": PASSWORD, "new_password": "PasswordPat-2026"},
    )
    assert personal.status_code == 422  # contains the user's name
    ok = api.client.post(
        "/api/v1/account/password",
        headers=auth(here),
        json={"current_password": PASSWORD, "new_password": "violet-harbour-lantern-91"},
    )
    assert ok.status_code == 200 and ok.json() == {"other_sessions_revoked": 1}
    assert api.client.get("/api/v1/auth/me", headers=auth(here)).status_code == 200
    assert api.client.get("/api/v1/auth/me", headers=auth(elsewhere)).status_code == 401
    assert login(Api(), u.email).status_code == 401
    assert login(Api(), u.email, "violet-harbour-lantern-91").status_code == 200


# ---------------------------------------------------------------- throttling
def test_account_lockout_after_repeated_failures(api, world):
    u = world.user("Locky")
    for _ in range(5):
        r = login(api, u.email, "nope")
        assert r.status_code == 401
    # Even the correct password is refused while locked.
    r = login(api, u.email)
    assert r.status_code == 429
    assert r.json()["error"]["code"] == "too_many_attempts"
    assert int(r.headers["Retry-After"]) > 0
    assert r.json()["error"]["details"]["retry_after_seconds"] > 0
    assert world.scalar("SELECT count(*) FROM security_events WHERE event_type='login_failed'") == 5
    assert world.scalar("SELECT count(*) FROM security_events WHERE event_type='login_locked'") == 1
    # When the lock expires, the correct password works again and clears the counter.
    world.execute("UPDATE auth_throttle SET locked_until = now() - interval '1 second'")
    assert login(api, u.email).status_code == 200


def test_lockout_does_not_reveal_whether_an_account_exists(api):
    for _ in range(5):
        assert login(api, "nobody@test.dev", "nope").status_code == 401
    assert login(api, "nobody@test.dev", "nope").status_code == 429


def test_successful_login_resets_the_account_counter(api, world):
    u = world.user("Forgetful")
    for _ in range(4):
        login(api, u.email, "nope")
    assert login(api, u.email).status_code == 200
    for _ in range(4):
        assert login(api, u.email, "nope").status_code == 401
    assert login(api, u.email).status_code == 200


def test_per_ip_limit_stops_password_spraying(api, world, settings_override):
    settings_override(login_max_failures_per_ip=3, trust_proxy_headers=True)
    victims = [world.user(f"Victim {i}") for i in range(4)]
    for v in victims[:3]:
        assert login(api, v.email, "Winter2026!", ip="203.0.113.9").status_code == 401
    r = login(api, victims[3].email, "Winter2026!", ip="203.0.113.9")
    assert r.status_code == 429
    # A different client address is unaffected.
    assert login(api, victims[3].email, ip="198.51.100.7").status_code == 200


def test_forwarded_for_is_ignored_unless_trusted(api, world, settings_override):
    settings_override(login_max_failures_per_ip=2, trust_proxy_headers=False)
    a, b = world.user("One"), world.user("Two")
    login(api, a.email, "x", ip="1.1.1.1")
    login(api, b.email, "x", ip="2.2.2.2")
    # Spoofed headers don't create separate buckets: the real peer address was counted twice.
    assert login(api, b.email, ip="3.3.3.3").status_code == 429


def test_concurrent_failures_are_all_counted(world):
    u = world.user("Hammered")
    barrier = threading.Barrier(8)

    def attempt(_):
        client = Api()
        barrier.wait()
        return login(client, u.email, "wrong").status_code

    with ThreadPoolExecutor(8) as pool:
        codes = list(pool.map(attempt, range(8)))
    assert set(codes) <= {401, 429}
    assert login(Api(), u.email).status_code == 429  # locked: no failure was lost to a race


def test_password_login_can_be_disabled_except_for_admins(api, world, settings_override):
    settings_override(password_login_enabled=False)
    member, admin = world.user("Member"), world.user("Root", admin=True)
    r = login(api, member.email)
    assert r.status_code == 403 and r.json()["error"]["code"] == "password_login_disabled"
    assert login(Api(), admin.email).status_code == 200  # break-glass


# ---------------------------------------------------------------- user administration
def test_admin_endpoints_require_admin(api, ops):
    for method, path in [
        ("GET", "/admin/users"),
        ("POST", "/admin/users"),
        ("PATCH", f"/admin/users/{ops.alice.id}"),
        ("POST", f"/admin/users/{ops.alice.id}/reset-password"),
        ("POST", f"/admin/users/{ops.alice.id}/revoke-sessions"),
        ("GET", "/admin/security-events"),
    ]:
        assert api.request(method, ops.manager, path, json={}).status_code == 403, path


def test_create_user_with_temporary_password_forces_change(api, world, ops):
    r = api.post(ops.admin, "/admin/users", json={"email": " New.Hire@Test.dev ", "full_name": "New Hire"})
    assert r.status_code == 201, r.text
    body = r.json()
    temp = body["temporary_password"]
    assert temp and len(temp) >= 16
    assert body["user"]["email"] == "new.hire@test.dev" and body["user"]["must_change_password"] is True
    assert world.scalar("SELECT password_hash <> :t FROM users WHERE email='new.hire@test.dev'", t=temp)

    newbie = Api()
    assert login(newbie, "new.hire@test.dev", temp).json()["must_change_password"] is True
    # Server-side enforcement: nothing but auth/account endpoints until the password is changed.
    r = newbie.client.get("/api/v1/work-items")
    assert r.status_code == 403 and r.json()["error"]["code"] == "password_change_required"
    r = newbie.client.post(
        "/api/v1/account/password",
        headers=CSRF,
        json={"current_password": temp, "new_password": "copper-meadow-signal-42"},
    )
    assert r.status_code == 200
    assert newbie.client.get("/api/v1/work-items").status_code == 200
    assert newbie.client.get("/api/v1/auth/me").json()["must_change_password"] is False


def test_create_user_validation(api, ops):
    assert (
        api.post(ops.admin, "/admin/users", json={"email": ops.alice.email, "full_name": "Dup"}).status_code
        == 409
    )
    r = api.post(ops.admin, "/admin/users", json={"email": "not-an-email", "full_name": "X"})
    assert r.status_code == 422
    r = api.post(
        ops.admin, "/admin/users", json={"email": "ok@test.dev", "full_name": "Ok", "password": "123"}
    )
    assert r.status_code == 422 and r.json()["error"]["code"] == "weak_password"


def test_deactivation_is_immediate_and_complete(api, world, ops):
    alice_token = bearer(Api(), ops.alice.email)
    item = world.item(ops.team, ops.manager, assignee=ops.alice)
    r = api.patch(ops.admin, f"/admin/users/{ops.alice.id}", json={"is_active": False})
    assert r.status_code == 200
    assert r.json()["is_active"] is False and r.json()["active_assigned_items"] == 1
    assert r.json()["active_session_count"] == 0
    # Existing sessions die at once; logging in again fails like a bad password.
    assert api.client.get("/api/v1/auth/me", headers=auth(alice_token)).status_code == 401
    assert login(Api(), ops.alice.email).status_code == 401
    # Inactive users cannot be given new work.
    other = world.item(ops.team, ops.manager)
    r = api.post(
        ops.manager, f"/work-items/{other.id}/assign", json={"version": 1, "assignee_id": ops.alice.id}
    )
    assert r.status_code == 422
    # Reassigning her open work to someone else still works.
    r = api.post(ops.manager, f"/work-items/{item.id}/assign", json={"version": 1, "assignee_id": ops.bob.id})
    assert r.status_code == 200
    # Reactivation restores access.
    assert api.patch(ops.admin, f"/admin/users/{ops.alice.id}", json={"is_active": True}).status_code == 200
    assert login(Api(), ops.alice.email).status_code == 200


def test_admin_cannot_lock_themselves_out(api, world, ops):
    r = api.patch(ops.admin, f"/admin/users/{ops.admin.id}", json={"is_active": False})
    assert r.status_code == 409 and r.json()["error"]["code"] == "cannot_modify_self"
    r = api.patch(ops.admin, f"/admin/users/{ops.admin.id}", json={"is_admin": False})
    assert r.status_code == 409


def test_last_active_admin_is_protected(api, world, ops):
    second = world.user("Second Admin", admin=True)
    # Two admins: demoting the other one is fine...
    assert api.patch(ops.admin, f"/admin/users/{second.id}", json={"is_admin": False}).status_code == 200
    # ...then promote back, deactivate the original admin from the second account.
    assert api.patch(ops.admin, f"/admin/users/{second.id}", json={"is_admin": True}).status_code == 200
    assert api.patch(second, f"/admin/users/{ops.admin.id}", json={"is_active": False}).status_code == 200
    # Now `second` is the only active admin. Every remaining path to removing it goes through
    # itself, which is refused, so the system can never end up without an administrator.
    assert api.patch(second, f"/admin/users/{second.id}", json={"is_admin": False}).status_code == 409


def test_role_changes_take_effect_on_the_next_request(api, world, ops):
    assert api.get(ops.alice, "/admin/users").status_code == 403
    api.patch(ops.admin, f"/admin/users/{ops.alice.id}", json={"is_admin": True})
    assert api.get(ops.alice, "/admin/users").status_code == 200  # same token, new privileges


def test_admin_password_reset(api, world, ops):
    old_token = bearer(Api(), ops.bob.email)
    r = api.post(ops.admin, f"/admin/users/{ops.bob.id}/reset-password")
    assert r.status_code == 200
    temp = r.json()["temporary_password"]
    assert api.client.get("/api/v1/auth/me", headers=auth(old_token)).status_code == 401
    assert login(Api(), ops.bob.email).status_code == 401
    assert login(Api(), ops.bob.email, temp).json()["must_change_password"] is True


def test_admin_revoke_sessions(api, world, ops):
    t1, t2 = bearer(Api(), ops.bob.email), bearer(Api(), ops.bob.email)
    r = api.post(ops.admin, f"/admin/users/{ops.bob.id}/revoke-sessions")
    assert r.json() == {"revoked": 2}
    assert api.client.get("/api/v1/auth/me", headers=auth(t1)).status_code == 401
    assert api.client.get("/api/v1/auth/me", headers=auth(t2)).status_code == 401


def test_user_list_search_and_paging(api, world, ops):
    for i in range(5):
        world.user(f"Paged Person {i}")
    page1 = api.get(ops.admin, "/admin/users", params={"q": "paged", "limit": 3}).json()
    assert page1["total"] == 5 and len(page1["items"]) == 3 and page1["next_cursor"]
    page2 = api.get(
        ops.admin, "/admin/users", params={"q": "paged", "limit": 3, "cursor": page1["next_cursor"]}
    ).json()
    assert len(page2["items"]) == 2 and page2["next_cursor"] is None
    ids = [u["id"] for u in page1["items"] + page2["items"]]
    assert len(set(ids)) == 5


def test_security_events_are_recorded_and_append_only(api, world, ops):
    login(Api(), ops.alice.email, "wrong")
    api.post(ops.admin, "/admin/users", json={"email": "audit@test.dev", "full_name": "Audit Me"})
    api.post(ops.admin, "/teams", json={"key": "AUD", "name": "Audit"})
    events = api.get(ops.admin, "/admin/security-events").json()["items"]
    types = [e["event_type"] for e in events]
    assert {"login_failed", "user_created", "team_created"} <= set(types)
    created = next(e for e in events if e["event_type"] == "user_created")
    assert created["actor"]["id"] == ops.admin.id and created["target"]["email"] == "audit@test.dev"
    only_failed = api.get(ops.admin, "/admin/security-events", params={"event_type": "login_failed"}).json()[
        "items"
    ]
    assert only_failed and all(e["event_type"] == "login_failed" for e in only_failed)
    with pytest.raises(DBAPIError, match="append-only"):
        world.execute("DELETE FROM security_events")


def test_api_responses_carry_security_headers(api, ops):
    r = api.get(ops.alice, "/work-items")
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["X-Frame-Options"] == "DENY"
    assert r.headers["Cache-Control"] == "no-store"
