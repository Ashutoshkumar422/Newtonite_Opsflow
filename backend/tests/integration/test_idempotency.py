"""Critical scenario C: a user repeats an operation because the first request timed out."""

import threading
from concurrent.futures import ThreadPoolExecutor

from tests.conftest import Api

KEY = "create-7d3c1a2b-0001"


def _create(api, user, team, key=KEY, title="Card terminal offline at store 42"):
    return api.post(
        user,
        "/work-items",
        json={"team_id": team.id, "title": title, "priority": "high"},
        headers={"Idempotency-Key": key},
    )


def test_retried_create_returns_same_item_without_duplicating(api, world, ops):
    first = _create(api, ops.alice, ops.team)
    retry = _create(api, ops.alice, ops.team)
    assert first.status_code == retry.status_code == 201
    assert retry.json() == first.json()
    assert retry.headers.get("Idempotent-Replayed") == "true"
    assert "Idempotent-Replayed" not in first.headers
    assert world.scalar("SELECT count(*) FROM work_items") == 1
    assert world.scalar("SELECT count(*) FROM activities WHERE action='created'") == 1


def test_same_key_with_different_payload_is_rejected(api, world, ops):
    assert _create(api, ops.alice, ops.team).status_code == 201
    r = _create(api, ops.alice, ops.team, title="A different request")
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "idempotency_key_reused"
    assert world.scalar("SELECT count(*) FROM work_items") == 1


def test_same_key_on_different_endpoint_is_rejected(api, world, ops):
    item = world.item(ops.team, ops.manager)
    assert (
        api.post(ops.alice, f"/work-items/{item.id}/claim", headers={"Idempotency-Key": KEY}).status_code
        == 200
    )
    r = _create(api, ops.alice, ops.team)
    assert r.status_code == 422


def test_keys_are_scoped_per_user(api, world, ops):
    assert _create(api, ops.alice, ops.team).status_code == 201
    r = _create(api, ops.bob, ops.team)
    assert r.status_code == 201 and "Idempotent-Replayed" not in r.headers
    assert world.scalar("SELECT count(*) FROM work_items") == 2


def test_concurrent_duplicate_requests_execute_once(world, ops):
    """Double-click / aggressive client retry: identical requests in flight at the same time."""
    n = 6
    barrier = threading.Barrier(n)

    def send(_):
        client = Api()
        barrier.wait()
        return _create(client, ops.alice, ops.team)

    with ThreadPoolExecutor(n) as pool:
        results = list(pool.map(send, range(n)))
    assert all(r.status_code == 201 for r in results), [r.text for r in results]
    assert len({r.json()["id"] for r in results}) == 1
    assert sum(1 for r in results if r.headers.get("Idempotent-Replayed") == "true") == n - 1
    assert world.scalar("SELECT count(*) FROM work_items") == 1


def test_failed_request_does_not_consume_the_key(api, world, ops):
    """Only successful responses are stored: a failure rolls back the key with the change."""
    item = world.item(ops.team, ops.manager)
    body = {"version": 1, "assignee_id": ops.bob.id}
    key = {"Idempotency-Key": "assign-retry-000001"}
    # Alice is not a manager: 403, nothing stored.
    denied = api.post(ops.alice, f"/work-items/{item.id}/assign", json=body, headers=key)
    assert denied.status_code == 403
    assert world.scalar("SELECT count(*) FROM idempotency_keys") == 0
    # Alice is promoted; the retry with the same key now executes for real.
    api.put(ops.manager, f"/teams/{ops.team.id}/members/{ops.alice.id}", json={"role": "manager"})
    ok = api.post(ops.alice, f"/work-items/{item.id}/assign", json=body, headers=key)
    assert ok.status_code == 200 and "Idempotent-Replayed" not in ok.headers
    assert ok.json()["assignee"]["id"] == ops.bob.id


def test_retried_transition_replays_instead_of_conflicting(api, world, ops):
    """Without a key, retrying a transition whose response was lost would hit a version conflict
    (or a second, different transition). With a key the client gets the original success."""
    item = world.item(ops.team, ops.manager, assignee=ops.alice)
    key = {"Idempotency-Key": "start-work-00000001"}
    body = {"version": 1, "to_status": "in_progress"}
    first = api.post(ops.alice, f"/work-items/{item.id}/transitions", json=body, headers=key)
    retry = api.post(ops.alice, f"/work-items/{item.id}/transitions", json=body, headers=key)
    assert first.status_code == retry.status_code == 200
    assert retry.headers.get("Idempotent-Replayed") == "true"
    no_key = api.post(ops.alice, f"/work-items/{item.id}/transitions", json=body)
    assert no_key.status_code == 409
    assert world.scalar("SELECT count(*) FROM activities WHERE action='status_changed'") == 1


def test_retried_comment_is_posted_once(api, world, ops):
    item = world.item(ops.team, ops.manager)
    key = {"Idempotency-Key": "comment-0000000001"}
    for _ in range(3):
        r = api.post(ops.alice, f"/work-items/{item.id}/comments", json={"body": "On it"}, headers=key)
        assert r.status_code == 201
    assert world.scalar("SELECT count(*) FROM comments") == 1


def test_expired_key_is_treated_as_new(api, world, ops):
    assert _create(api, ops.alice, ops.team).status_code == 201
    world.execute("UPDATE idempotency_keys SET expires_at = now() - interval '1 second'")
    r = _create(api, ops.alice, ops.team)
    assert r.status_code == 201 and "Idempotent-Replayed" not in r.headers
    assert world.scalar("SELECT count(*) FROM work_items") == 2


def test_malformed_key_is_rejected(api, ops):
    r = _create(api, ops.alice, ops.team, key="short")
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "validation_failed"
