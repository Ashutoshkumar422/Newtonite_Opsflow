"""Critical scenario E (workflow rules) and audit consistency."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.db.session import get_engine
from app.services import work_items as svc


def transition(api, user, item_id, version, to, reason=None):
    body = {"version": version, "to_status": to}
    if reason is not None:
        body["reason"] = reason
    return api.post(user, f"/work-items/{item_id}/transitions", json=body)


def test_full_lifecycle_with_rejection_and_approval(api, world, ops):
    item = world.item(ops.team, ops.bob)  # Bob reports
    v = api.post(ops.alice, f"/work-items/{item.id}/claim").json()["version"]
    v = transition(api, ops.alice, item.id, v, "in_progress").json()["version"]
    v = transition(api, ops.alice, item.id, v, "blocked", "Waiting for bank statement").json()["version"]
    v = transition(api, ops.alice, item.id, v, "in_progress").json()["version"]
    v = transition(api, ops.alice, item.id, v, "resolved").json()["version"]
    r = transition(api, ops.bob, item.id, v, "in_progress", "Still charged twice")  # reporter rejects
    assert r.status_code == 200
    v = r.json()["version"]
    v = transition(api, ops.alice, item.id, v, "resolved").json()["version"]
    r = transition(api, ops.manager, item.id, v, "closed")
    assert r.status_code == 200 and r.json()["status"] == "closed"
    assert r.json()["permissions"]["transitions"] == []

    history = api.get(ops.manager, f"/work-items/{item.id}/activity").json()["items"]
    status_moves = [
        (h["changes"]["status"]["from"], h["changes"]["status"]["to"])
        for h in reversed(history)
        if h["action"] == "status_changed"
    ]
    assert status_moves == [
        ("open", "in_progress"),
        ("in_progress", "blocked"),
        ("blocked", "in_progress"),
        ("in_progress", "resolved"),
        ("resolved", "in_progress"),
        ("in_progress", "resolved"),
        ("resolved", "closed"),
    ]
    reasons = [h["changes"].get("reason") for h in history if h["changes"].get("reason")]
    assert "Still charged twice" in reasons


def test_invalid_transitions_rejected_via_direct_api_calls(api, world, ops):
    item = world.item(ops.team, ops.manager, assignee=ops.alice)
    r = transition(api, ops.manager, item.id, 1, "closed")
    assert r.status_code == 409 and r.json()["error"]["code"] == "invalid_transition"
    r = transition(api, ops.manager, item.id, 1, "resolved")
    assert r.status_code == 409
    r = transition(api, ops.manager, item.id, 1, "bogus")
    assert r.status_code == 422  # not a status at all
    assert world.fetch(item.id).status == "open"


def test_reason_required(api, world, ops):
    item = world.item(ops.team, ops.alice)
    r = transition(api, ops.alice, item.id, 1, "cancelled", "  ")
    assert r.status_code == 422 and r.json()["error"]["code"] == "reason_required"


def test_cannot_start_unassigned_item(api, world, ops):
    item = world.item(ops.team, ops.alice)
    r = transition(api, ops.manager, item.id, 1, "in_progress")
    assert r.status_code == 409 and r.json()["error"]["code"] == "assignee_required"


def test_cannot_unassign_active_work(api, world, ops):
    item = world.item(ops.team, ops.manager, assignee=ops.alice)
    transition(api, ops.alice, item.id, 1, "in_progress")
    r = api.post(ops.manager, f"/work-items/{item.id}/assign", json={"version": 2, "assignee_id": None})
    assert r.status_code == 409 and r.json()["error"]["code"] == "assignee_required"


def test_assignee_can_release_open_item(api, world, ops):
    item = world.item(ops.team, ops.manager)
    v = api.post(ops.alice, f"/work-items/{item.id}/claim").json()["version"]
    r = api.post(ops.alice, f"/work-items/{item.id}/assign", json={"version": v, "assignee_id": None})
    assert r.status_code == 200 and r.json()["assignee"] is None


def test_terminal_items_are_read_only(api, world, ops):
    item = world.item(ops.team, ops.alice)
    transition(api, ops.alice, item.id, 1, "cancelled", "duplicate of OPS-7")
    r = api.patch(ops.alice, f"/work-items/{item.id}", json={"version": 2, "title": "reopen?"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "item_closed"
    r = api.post(ops.bob, f"/work-items/{item.id}/claim")
    assert r.status_code == 409 and r.json()["error"]["code"] == "item_closed"
    # Discussion remains possible after closure
    assert api.post(ops.bob, f"/work-items/{item.id}/comments", json={"body": "ok"}).status_code == 201


def test_database_rejects_owned_state_without_assignee(world, ops):
    """Defence in depth: even a buggy code path cannot persist in_progress without an owner."""
    item = world.item(ops.team, ops.alice)
    with pytest.raises(IntegrityError), get_engine().begin() as conn:
        conn.execute(text("UPDATE work_items SET status = 'in_progress' WHERE id = :i"), {"i": item.id})


# ---------------------------------------------------------------- audit consistency
def test_every_state_change_writes_exactly_one_activity(api, world, ops):
    item = world.item(ops.team, ops.manager)
    n = lambda: world.scalar("SELECT count(*) FROM activities WHERE work_item_id=:i", i=item.id)  # noqa: E731
    assert n() == 1  # created
    api.post(ops.alice, f"/work-items/{item.id}/claim")
    assert n() == 2
    api.patch(ops.manager, f"/work-items/{item.id}", json={"version": 2, "priority": "critical"})
    assert n() == 3
    transition(api, ops.alice, item.id, 3, "in_progress")
    assert n() == 4
    api.post(ops.manager, f"/work-items/{item.id}/assign", json={"version": 4, "assignee_id": ops.bob.id})
    assert n() == 5
    api.post(ops.bob, f"/work-items/{item.id}/comments", json={"body": "taking over"})
    assert n() == 6
    history = api.get(ops.manager, f"/work-items/{item.id}/activity").json()["items"]
    assert [h["action"] for h in history] == [
        "commented",
        "assigned",
        "status_changed",
        "updated",
        "claimed",
        "created",
    ]
    reassign = history[1]
    assert reassign["actor"]["id"] == ops.manager.id
    assert reassign["changes"]["assignee"]["from"]["id"] == ops.alice.id
    assert reassign["changes"]["assignee"]["to"]["id"] == ops.bob.id
    prio = history[3]["changes"]["priority"]
    assert prio == {"from": "medium", "to": "critical"}


def test_audit_failure_rolls_back_the_change(api, world, ops, monkeypatch):
    """If the audit write fails, the business change must not persist either."""
    item = world.item(ops.team, ops.alice)

    def broken_record(*a, **kw):
        raise RuntimeError("simulated audit failure")

    monkeypatch.setattr(svc, "_record", broken_record)
    r = api.patch(ops.alice, f"/work-items/{item.id}", json={"version": 1, "title": "Should not persist"})
    assert r.status_code == 500
    body = r.json()
    assert body["error"]["code"] == "internal_error"
    assert "simulated" not in r.text and "Traceback" not in r.text  # no internals leaked
    fresh = world.fetch(item.id)
    assert fresh.title == "Something needs attention" and fresh.version == 1


def test_audit_log_is_append_only_in_the_database(world, ops):
    world.item(ops.team, ops.alice)
    with pytest.raises(DBAPIError, match="append-only"), get_engine().begin() as conn:
        conn.execute(text("UPDATE activities SET action = 'tampered'"))
    with pytest.raises(DBAPIError, match="append-only"), get_engine().begin() as conn:
        conn.execute(text("DELETE FROM activities"))
    assert world.scalar("SELECT count(*) FROM activities") == 1


def test_comments_are_editable_by_author_only_and_not_audited(api, world, ops):
    item = world.item(ops.team, ops.alice)
    c = api.post(ops.alice, f"/work-items/{item.id}/comments", json={"body": "first draft"}).json()
    assert api.patch(ops.bob, f"/comments/{c['id']}", json={"body": "hijack"}).status_code == 403
    r = api.patch(ops.alice, f"/comments/{c['id']}", json={"body": "final wording"})
    assert r.status_code == 200 and r.json()["edited_at"] is not None
    assert world.scalar("SELECT count(*) FROM activities WHERE action='commented'") == 1
