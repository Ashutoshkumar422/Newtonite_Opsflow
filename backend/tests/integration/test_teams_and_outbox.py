"""Team membership invariants and the transactional outbox / notification worker."""

import threading
import time

from app.db.session import session_factory
from app.models import User
from app.services import outbox
from app.services import teams as teams_svc
from app.services import work_items as svc


# ---------------------------------------------------------------- membership invariants
def test_cannot_remove_member_who_owns_active_work(api, world, ops):
    world.item(ops.team, ops.manager, assignee=ops.alice)
    r = api.delete(ops.manager, f"/teams/{ops.team.id}/members/{ops.alice.id}")
    assert r.status_code == 409 and r.json()["error"]["code"] == "member_has_active_items"
    assert world.scalar("SELECT count(*) FROM team_memberships WHERE user_id=:u", u=ops.alice.id) == 1
    assert api.delete(ops.manager, f"/teams/{ops.team.id}/members/{ops.bob.id}").status_code == 204


def test_team_keeps_at_least_one_manager(api, ops):
    r = api.put(ops.manager, f"/teams/{ops.team.id}/members/{ops.manager.id}", json={"role": "member"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "last_manager"
    r = api.delete(ops.admin, f"/teams/{ops.team.id}/members/{ops.manager.id}")
    assert r.status_code == 409


def test_membership_removal_and_assignment_cannot_interleave(world, ops):
    """Assignment holds the target's membership FOR SHARE; removal must wait, then sees the
    committed assignment and refuses. No item can end up assigned to a non-member."""
    item = world.item(ops.team, ops.manager)
    s1 = session_factory()()
    svc.assign_item(s1, s1.get(User, ops.manager.id), item.id, ops.bob.id, 1)  # uncommitted
    outcome: dict = {}

    def remove_bob():
        with session_factory()() as s2:
            try:
                teams_svc.remove_member(s2, s2.get(User, ops.admin.id), ops.team.id, ops.bob.id)
                s2.commit()
                outcome["r"] = "removed"
            except Exception as e:  # noqa: BLE001
                outcome["r"] = getattr(e, "code", repr(e))

    t = threading.Thread(target=remove_bob)
    t.start()
    time.sleep(0.5)
    assert t.is_alive(), "removal must wait for the in-flight assignment"
    s1.commit()
    s1.close()
    t.join(5)
    assert outcome["r"] == "member_has_active_items"
    assert world.fetch(item.id).assignee_id == ops.bob.id


# ---------------------------------------------------------------- outbox
def process():
    return outbox.process_batch(session_factory())


def notifications_for(world, user):
    return world.scalar("SELECT count(*) FROM notifications WHERE user_id=:u", u=user.id)


def test_events_are_written_atomically_and_delivered_by_worker(api, world, ops):
    item = world.item(ops.team, ops.alice)
    api.post(ops.manager, f"/work-items/{item.id}/assign", json={"version": 1, "assignee_id": ops.bob.id})
    assert world.scalar("SELECT count(*) FROM outbox_events WHERE status='pending'") == 1
    assert notifications_for(world, ops.bob) == 0  # asynchronous: nothing yet
    assert process() == 1
    assert notifications_for(world, ops.bob) == 1
    feed = api.get(ops.bob, "/notifications").json()
    assert feed["unread_count"] == 1 and "assigned" in feed["items"][0]["message"]
    nid = feed["items"][0]["id"]
    assert api.post(ops.alice, f"/notifications/{nid}/read").status_code == 404  # not hers
    assert api.post(ops.bob, f"/notifications/{nid}/read").status_code == 204
    assert api.get(ops.bob, "/notifications").json()["unread_count"] == 0


def test_rolled_back_change_emits_no_event(api, world, ops):
    item = world.item(ops.team, ops.alice)
    r = api.post(
        ops.manager, f"/work-items/{item.id}/assign", json={"version": 99, "assignee_id": ops.bob.id}
    )
    assert r.status_code == 409
    assert world.scalar("SELECT count(*) FROM outbox_events") == 0


def test_actor_is_not_notified_about_their_own_action(api, world, ops):
    item = world.item(ops.team, ops.alice)
    api.post(ops.alice, f"/work-items/{item.id}/comments", json={"body": "note to self"})
    assert world.scalar("SELECT count(*) FROM outbox_events") == 0


def test_reprocessing_an_event_does_not_duplicate_notifications(api, world, ops):
    item = world.item(ops.team, ops.alice)
    api.post(ops.manager, f"/work-items/{item.id}/assign", json={"version": 1, "assignee_id": ops.bob.id})
    process()
    # Simulate at-least-once delivery (e.g. worker crashed after the side effect)
    world.execute("UPDATE outbox_events SET status='pending', processed_at=NULL")
    process()
    assert notifications_for(world, ops.bob) == 1


def test_failing_handler_retries_with_backoff_then_dead_letters(api, world, ops, monkeypatch):
    calls = {"n": 0}

    def flaky(db, event):
        calls["n"] += 1
        raise ConnectionError("smtp down")

    monkeypatch.setitem(outbox.HANDLERS, "notify", flaky)
    item = world.item(ops.team, ops.alice)
    api.post(ops.manager, f"/work-items/{item.id}/assign", json={"version": 1, "assignee_id": ops.bob.id})
    # The primary action succeeded regardless of the downstream failure.
    assert world.fetch(item.id).assignee_id == ops.bob.id

    process()
    assert world.scalar("SELECT attempts FROM outbox_events") == 1
    assert world.scalar("SELECT status FROM outbox_events") == "pending"
    assert world.scalar("SELECT available_at > now() FROM outbox_events") is True  # backed off
    process()
    assert calls["n"] == 1  # not due yet: backoff respected

    for _ in range(10):
        world.execute("UPDATE outbox_events SET available_at = now()")
        process()
    assert world.scalar("SELECT status FROM outbox_events") == "failed"
    assert world.scalar("SELECT attempts FROM outbox_events") == 5
    assert "smtp down" in world.scalar("SELECT last_error FROM outbox_events")
    stats = api.get(ops.admin, "/admin/outbox").json()
    assert stats["failed"] == 1 and stats["recent_failures"][0]["attempts"] == 5


def test_one_bad_event_does_not_block_others(api, world, ops, monkeypatch):
    original = outbox.HANDLERS["notify"]

    def selective(db, event):
        if "OPS-1" in event.payload["message"]:
            raise ValueError("poison")
        original(db, event)

    monkeypatch.setitem(outbox.HANDLERS, "notify", selective)
    a = world.item(ops.team, ops.alice)
    b = world.item(ops.team, ops.alice)
    api.post(ops.manager, f"/work-items/{a.id}/assign", json={"version": 1, "assignee_id": ops.bob.id})
    api.post(ops.manager, f"/work-items/{b.id}/assign", json={"version": 1, "assignee_id": ops.bob.id})
    process()
    assert world.scalar("SELECT count(*) FROM outbox_events WHERE status='processed'") == 1
    assert notifications_for(world, ops.bob) == 1


def test_concurrent_workers_process_each_event_once(api, world, ops, monkeypatch):
    seen: list[int] = []
    lock = threading.Lock()
    original = outbox.HANDLERS["notify"]

    def tracking(db, event):
        with lock:
            seen.append(event.id)
        time.sleep(0.01)
        original(db, event)

    monkeypatch.setitem(outbox.HANDLERS, "notify", tracking)
    for _ in range(12):
        item = world.item(ops.team, ops.alice)
        api.post(ops.manager, f"/work-items/{item.id}/assign", json={"version": 1, "assignee_id": ops.bob.id})

    def worker():
        factory = session_factory()
        while outbox.process_batch(factory, batch_size=3):
            pass

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert sorted(seen) == sorted(set(seen)) and len(seen) == 12
    assert notifications_for(world, ops.bob) == 12
