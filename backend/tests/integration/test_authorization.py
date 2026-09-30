"""Critical scenario D: authorization is enforced by the server at the resource level."""

import pytest

from app.core.config import get_settings
from tests.conftest import issue_token


def test_unauthenticated_requests_are_rejected(api):
    for method, path in [
        ("GET", "/work-items"),
        ("GET", "/work-items/1"),
        ("POST", "/work-items"),
        ("GET", "/dashboard/summary"),
        ("GET", "/teams"),
    ]:
        r = api.request(method, None, path, json={})
        assert r.status_code == 401, (method, path)
        assert r.json()["error"]["code"] == "not_authenticated"


def test_invalid_and_expired_tokens_are_rejected(api):
    r = api.client.get("/api/v1/work-items", headers={"Authorization": "Bearer not-a-jwt"})
    assert r.status_code == 401


def test_cross_team_item_is_invisible(api, world, ops):
    item = world.item(ops.team, ops.alice)
    for path in [
        f"/work-items/{item.id}",
        f"/work-items/{item.id}/activity",
        f"/work-items/{item.id}/comments",
    ]:
        r = api.get(ops.outsider, path)
        assert r.status_code == 404, path  # indistinguishable from "does not exist"
    assert (
        api.patch(ops.outsider, f"/work-items/{item.id}", json={"version": 1, "title": "x"}).status_code
        == 404
    )
    assert api.post(ops.outsider, f"/work-items/{item.id}/claim").status_code == 404
    assert api.post(ops.outsider, f"/work-items/{item.id}/comments", json={"body": "hi"}).status_code == 404
    assert (
        api.post(
            ops.outsider, f"/work-items/{item.id}/transitions", json={"version": 1, "to_status": "cancelled"}
        ).status_code
        == 404
    )
    assert world.fetch(item.id).version == 1


def test_listing_search_and_dashboard_respect_team_boundaries(api, world, ops):
    world.item(ops.team, ops.alice, title="Secret payroll discrepancy")
    world.item(ops.other, ops.outsider, title="Visible to outsider")
    r = api.get(ops.outsider, "/work-items", params={"q": "payroll"})
    assert r.json()["items"] == [] and r.json()["total"] == 0
    r = api.get(ops.outsider, "/work-items", params={"team_id": ops.team.id})
    assert r.json()["items"] == []
    r = api.get(ops.outsider, "/work-items", params={"q": "OPS-1"})
    assert r.json()["items"] == []
    titles = [i["title"] for i in api.get(ops.outsider, "/work-items").json()["items"]]
    assert titles == ["Visible to outsider"]
    dash = api.get(ops.outsider, "/dashboard/summary").json()
    assert dash["totals"]["active"] == 1
    assert [t["team"]["key"] for t in dash["by_team"]] == ["FIN"]


def test_creating_in_a_foreign_team_is_denied(api, ops):
    r = api.post(ops.outsider, "/work-items", json={"team_id": ops.team.id, "title": "x"})
    assert r.status_code == 404


def test_member_cannot_assign_others_but_manager_can(api, world, ops):
    item = world.item(ops.team, ops.alice)
    r = api.post(ops.alice, f"/work-items/{item.id}/assign", json={"version": 1, "assignee_id": ops.bob.id})
    assert r.status_code == 403
    r = api.post(ops.manager, f"/work-items/{item.id}/assign", json={"version": 1, "assignee_id": ops.bob.id})
    assert r.status_code == 200 and r.json()["assignee"]["id"] == ops.bob.id


def test_cannot_assign_to_non_member(api, world, ops):
    item = world.item(ops.team, ops.alice)
    r = api.post(
        ops.manager, f"/work-items/{item.id}/assign", json={"version": 1, "assignee_id": ops.outsider.id}
    )
    assert r.status_code == 422 and r.json()["error"]["code"] == "assignee_not_member"


def test_member_cannot_create_item_assigned_to_someone_else(api, ops):
    r = api.post(
        ops.alice, "/work-items", json={"team_id": ops.team.id, "title": "x", "assignee_id": ops.bob.id}
    )
    assert r.status_code == 403
    r = api.post(
        ops.alice, "/work-items", json={"team_id": ops.team.id, "title": "x", "assignee_id": ops.alice.id}
    )
    assert r.status_code == 201


def test_only_participants_or_manager_can_edit(api, world, ops):
    item = world.item(ops.team, ops.alice)
    assert api.patch(ops.bob, f"/work-items/{item.id}", json={"version": 1, "title": "x"}).status_code == 403
    assert (
        api.patch(ops.manager, f"/work-items/{item.id}", json={"version": 1, "title": "y"}).status_code == 200
    )


def test_unauthorized_status_changes(api, world, ops):
    item = world.item(ops.team, ops.manager, assignee=ops.alice)
    # Bob (member, not assignee) cannot start Alice's work
    r = api.post(
        ops.bob, f"/work-items/{item.id}/transitions", json={"version": 1, "to_status": "in_progress"}
    )
    assert r.status_code == 403
    api.post(ops.alice, f"/work-items/{item.id}/transitions", json={"version": 1, "to_status": "in_progress"})
    api.post(ops.alice, f"/work-items/{item.id}/transitions", json={"version": 2, "to_status": "resolved"})
    # Assignee cannot approve their own work; only a manager closes.
    r = api.post(ops.alice, f"/work-items/{item.id}/transitions", json={"version": 3, "to_status": "closed"})
    assert r.status_code == 403
    r = api.post(
        ops.manager, f"/work-items/{item.id}/transitions", json={"version": 3, "to_status": "closed"}
    )
    assert r.status_code == 200 and r.json()["status"] == "closed"


def test_admin_can_act_across_teams(api, world, ops):
    item = world.item(ops.team, ops.alice)
    assert api.get(ops.admin, f"/work-items/{item.id}").status_code == 200
    r = api.post(ops.admin, f"/work-items/{item.id}/assign", json={"version": 1, "assignee_id": ops.bob.id})
    assert r.status_code == 200


def test_team_administration_permissions(api, world, ops):
    assert api.post(ops.manager, "/teams", json={"key": "NEW", "name": "New"}).status_code == 403
    r = api.post(ops.admin, "/teams", json={"key": "NEW", "name": "New"})
    assert r.status_code == 201
    carol = world.user("Carol")
    # A plain member cannot manage membership
    assert (
        api.put(ops.alice, f"/teams/{ops.team.id}/members/{carol.id}", json={"role": "member"}).status_code
        == 403
    )
    # A manager of another team cannot either
    assert (
        api.put(ops.outsider, f"/teams/{ops.team.id}/members/{carol.id}", json={"role": "member"}).status_code
        == 404
    )
    # The team manager can
    assert (
        api.put(ops.manager, f"/teams/{ops.team.id}/members/{carol.id}", json={"role": "member"}).status_code
        == 200
    )
    assert api.delete(ops.alice, f"/teams/{ops.team.id}/members/{carol.id}").status_code == 403


def test_admin_only_observability_endpoint(api, ops):
    assert api.get(ops.manager, "/admin/outbox").status_code == 403
    assert api.get(ops.admin, "/admin/outbox").status_code == 200


def test_permissions_payload_matches_enforcement(api, world, ops):
    item = world.item(ops.team, ops.alice)
    bob_view = api.get(ops.bob, f"/work-items/{item.id}").json()["permissions"]
    assert bob_view["can_edit"] is False and bob_view["can_assign"] is False
    assert bob_view["can_claim"] is True and bob_view["transitions"] == []
    mgr_view = api.get(ops.manager, f"/work-items/{item.id}").json()["permissions"]
    assert mgr_view["can_assign"] is True and mgr_view["can_edit"] is True


# ---------------------------------------------------------------- browser session + CSRF
@pytest.fixture
def cookie_client(api, world):
    user = world.user("Cookie User")
    team = world.team(manager=user)
    api.client.cookies.set(get_settings().cookie_name, issue_token(user, "password"))
    return api, team


def test_login_sets_httponly_cookie(api, world):
    from tests.conftest import PASSWORD

    u = world.user("Login User")
    r = api.client.post("/api/v1/auth/login", json={"email": u.email, "password": PASSWORD})
    assert r.status_code == 200 and r.json()["email"] == u.email
    set_cookie = r.headers["set-cookie"].lower()
    assert "httponly" in set_cookie and "samesite=lax" in set_cookie
    bad = api.client.post("/api/v1/auth/login", json={"email": u.email, "password": "wrong"})
    assert bad.status_code == 401 and bad.json()["error"]["code"] == "invalid_credentials"


def test_cookie_authenticated_writes_require_csrf_header(cookie_client):
    api, team = cookie_client
    body = {"team_id": team.id, "title": "via cookie"}
    r = api.client.post("/api/v1/work-items", json=body)
    assert r.status_code == 403 and r.json()["error"]["code"] == "csrf_failed"
    r = api.client.post("/api/v1/work-items", json=body, headers={"X-Requested-With": "opsflow"})
    assert r.status_code == 201
    assert api.client.get("/api/v1/work-items").status_code == 200  # safe methods need no header
