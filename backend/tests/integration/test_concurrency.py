"""Critical scenarios A & B: simultaneous claims and stale edits.

Two styles of test:
  * Racing tests fire many truly concurrent HTTP requests through the full stack (threads with a
    barrier) and assert the invariant over many trials.
  * Interleaving tests pin down the exact ordering with two database sessions: session 1 holds the
    row, session 2 is started in a thread and must block, then session 1 commits. This proves the
    database-level behaviour deterministically, rather than hoping the race window is hit.
"""

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.core.errors import Conflict
from app.db.session import session_factory
from app.models import User
from app.schemas import WorkItemUpdate
from app.services import work_items as svc
from tests.conftest import Api


# ---------------------------------------------------------------- A. concurrent claims
@pytest.mark.parametrize("trial", range(5))
def test_concurrent_claims_exactly_one_winner(world, trial):
    manager = world.user("Manager")
    racers = [world.user(f"Racer {i}") for i in range(8)]
    team = world.team(manager=manager, members=racers)
    item = world.item(team, manager)
    barrier = threading.Barrier(len(racers))

    def claim(user):
        client = Api()  # one client per thread
        barrier.wait()
        return user.id, client.post(user, f"/work-items/{item.id}/claim")

    with ThreadPoolExecutor(len(racers)) as pool:
        results = list(pool.map(claim, racers))

    statuses = sorted(r.status_code for _, r in results)
    assert statuses == [200] + [409] * (len(racers) - 1)
    loser_codes = {r.json()["error"]["code"] for _, r in results if r.status_code == 409}
    assert loser_codes == {"already_claimed"}

    winner_id = next(uid for uid, r in results if r.status_code == 200)
    fresh = world.fetch(item.id)
    assert fresh.assignee_id == winner_id
    assert fresh.version == 2  # exactly one successful write
    assert (
        world.scalar(
            "SELECT count(*) FROM activities WHERE work_item_id = :i AND action = 'claimed'", i=item.id
        )
        == 1
    )
    claimed_actor = world.scalar(
        "SELECT actor_id FROM activities WHERE work_item_id = :i AND action = 'claimed'", i=item.id
    )
    assert claimed_actor == winner_id


def test_claim_blocked_by_uncommitted_claim_then_loses(world, ops):
    """Deterministic interleaving of the compare-and-set under READ COMMITTED."""
    item = world.item(ops.team, ops.manager)
    s1 = session_factory()()
    s1_alice = s1.get(User, ops.alice.id)
    svc.claim_item(s1, s1_alice, item.id)  # UPDATE executed, row locked, NOT committed

    outcome: dict = {}

    def bob_claims():
        with session_factory()() as s2:
            try:
                svc.claim_item(s2, s2.get(User, ops.bob.id), item.id)
                s2.commit()
                outcome["result"] = "won"
            except Conflict as e:
                outcome["result"] = e.code

    t = threading.Thread(target=bob_claims)
    t.start()
    time.sleep(0.5)
    assert t.is_alive(), "Bob's claim should be blocked on Alice's row lock"
    s1.commit()
    s1.close()
    t.join(5)
    assert outcome["result"] == "already_claimed"
    assert world.fetch(item.id).assignee_id == ops.alice.id


def test_reclaim_by_owner_is_a_noop(api, world, ops):
    item = world.item(ops.team, ops.manager)
    first = api.post(ops.alice, f"/work-items/{item.id}/claim")
    second = api.post(ops.alice, f"/work-items/{item.id}/claim")
    assert first.status_code == second.status_code == 200
    assert second.json()["version"] == first.json()["version"] == 2
    assert world.scalar("SELECT count(*) FROM activities WHERE action='claimed'") == 1


# ---------------------------------------------------------------- B. optimistic concurrency
def test_update_with_current_version_succeeds_and_increments(api, world, ops):
    item = world.item(ops.team, ops.alice)
    r = api.patch(ops.alice, f"/work-items/{item.id}", json={"version": 1, "title": "Refined title"})
    assert r.status_code == 200, r.text
    assert r.json()["version"] == 2
    assert r.json()["title"] == "Refined title"


def test_stale_update_is_rejected_with_current_state(api, world, ops):
    item = world.item(ops.team, ops.alice)
    # Alice and the manager both loaded version 1.
    ok = api.patch(ops.manager, f"/work-items/{item.id}", json={"version": 1, "priority": "critical"})
    assert ok.status_code == 200
    stale = api.patch(ops.alice, f"/work-items/{item.id}", json={"version": 1, "title": "Alice's edit"})
    assert stale.status_code == 409
    body = stale.json()["error"]
    assert body["code"] == "version_conflict"
    assert body["details"]["current_version"] == 2
    assert body["details"]["current"]["priority"] == "critical"
    # Alice's write did not happen; the manager's did.
    fresh = world.fetch(item.id)
    assert fresh.title == "Something needs attention"
    assert fresh.version == 2
    # Alice can retry on top of the latest version (what the UI's "re-apply my changes" does).
    retry = api.patch(ops.alice, f"/work-items/{item.id}", json={"version": 2, "title": "Alice's edit"})
    assert retry.status_code == 200
    assert retry.json()["priority"] == "critical" and retry.json()["title"] == "Alice's edit"


def test_stale_transition_and_assignment_are_rejected(api, world, ops):
    item = world.item(ops.team, ops.manager, assignee=ops.alice)
    assert (
        api.post(
            ops.alice, f"/work-items/{item.id}/transitions", json={"version": 1, "to_status": "in_progress"}
        ).status_code
        == 200
    )
    r = api.post(ops.manager, f"/work-items/{item.id}/assign", json={"version": 1, "assignee_id": ops.bob.id})
    assert r.status_code == 409 and r.json()["error"]["code"] == "version_conflict"
    r = api.post(
        ops.alice, f"/work-items/{item.id}/transitions", json={"version": 1, "to_status": "resolved"}
    )
    assert r.status_code == 409


def test_concurrent_updates_same_version_exactly_one_wins(world, ops):
    item = world.item(ops.team, ops.alice)
    editors = [ops.alice, ops.manager, ops.bob, ops.alice, ops.manager, ops.admin]
    barrier = threading.Barrier(len(editors))

    def edit(pair):
        idx, user = pair
        client = Api()
        barrier.wait()
        return client.patch(user, f"/work-items/{item.id}", json={"version": 1, "title": f"edit {idx}"})

    with ThreadPoolExecutor(len(editors)) as pool:
        results = list(pool.map(edit, enumerate(editors)))
    codes = [r.status_code for r in results]
    # Bob is neither reporter nor assignee -> 403 regardless of timing; the rest race.
    assert codes.count(403) == 1
    assert codes.count(200) == 1
    assert codes.count(409) == len(editors) - 2
    assert world.fetch(item.id).version == 2
    assert world.scalar("SELECT count(*) FROM activities WHERE action = 'updated'") == 1


def test_update_waits_for_row_lock_then_detects_staleness(world, ops):
    item = world.item(ops.team, ops.alice)
    s1 = session_factory()()
    svc.update_item(s1, s1.get(User, ops.manager.id), item.id, WorkItemUpdate(version=1, priority="high"))
    outcome: dict = {}

    def alice_edits():
        with session_factory()() as s2:
            try:
                svc.update_item(
                    s2, s2.get(User, ops.alice.id), item.id, WorkItemUpdate(version=1, title="late")
                )
                s2.commit()
                outcome["r"] = "ok"
            except Conflict as e:
                outcome["r"] = e.code

    t = threading.Thread(target=alice_edits)
    t.start()
    time.sleep(0.5)
    assert t.is_alive(), "second writer must wait on SELECT ... FOR UPDATE"
    s1.commit()
    s1.close()
    t.join(5)
    assert outcome["r"] == "version_conflict"


def test_noop_update_does_not_bump_version_or_audit(api, world, ops):
    item = world.item(ops.team, ops.alice, title="Same")
    r = api.patch(ops.alice, f"/work-items/{item.id}", json={"version": 1, "title": "Same"})
    assert r.status_code == 200 and r.json()["version"] == 1
    assert world.scalar("SELECT count(*) FROM activities WHERE action='updated'") == 0


def test_concurrent_creation_gets_unique_sequential_keys(world, ops):
    barrier = threading.Barrier(10)

    def create(i):
        client = Api()
        barrier.wait()
        return client.post(ops.alice, "/work-items", json={"team_id": ops.team.id, "title": f"t{i}"})

    with ThreadPoolExecutor(10) as pool:
        results = list(pool.map(create, range(10)))
    assert all(r.status_code == 201 for r in results)
    numbers = sorted(r.json()["number"] for r in results)
    assert numbers == list(range(1, 11))
