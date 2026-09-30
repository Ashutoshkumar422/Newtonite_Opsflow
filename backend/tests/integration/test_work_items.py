"""Creation, retrieval, validation, search, filtering and keyset pagination."""

from datetime import date, timedelta

from sqlalchemy import text

from app.db.session import get_engine


def test_create_and_retrieve(api, ops):
    due = (date.today() + timedelta(days=3)).isoformat()
    r = api.post(
        ops.alice,
        "/work-items",
        json={
            "team_id": ops.team.id,
            "title": "  Payment gateway timeouts  ",
            "description": "Seeing 504s from the acquirer",
            "priority": "critical",
            "due_date": due,
        },
    )
    assert r.status_code == 201, r.text
    created = r.json()
    assert created["key"] == "OPS-1"
    assert created["title"] == "Payment gateway timeouts"  # trimmed
    assert created["status"] == "open" and created["version"] == 1
    assert created["reporter"]["id"] == ops.alice.id and created["assignee"] is None
    got = api.get(ops.bob, f"/work-items/{created['id']}").json()
    assert {k: got[k] for k in ("id", "title", "priority", "due_date")} == {
        "id": created["id"],
        "title": "Payment gateway timeouts",
        "priority": "critical",
        "due_date": due,
    }


def test_validation_errors_are_structured(api, ops):
    r = api.post(ops.alice, "/work-items", json={"team_id": ops.team.id, "title": "   "})
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "validation_failed"
    assert any("title" in f["loc"] for f in err["details"]["fields"])
    assert r.json()["request_id"]

    r = api.post(ops.alice, "/work-items", json={"team_id": ops.team.id, "title": "x", "priority": "urgent"})
    assert r.status_code == 422
    past = (date.today() - timedelta(days=1)).isoformat()
    r = api.post(ops.alice, "/work-items", json={"team_id": ops.team.id, "title": "x", "due_date": past})
    assert r.status_code == 422
    r = api.post(ops.alice, "/work-items", json={"team_id": ops.team.id, "title": "x" * 201})
    assert r.status_code == 422
    r = api.patch(ops.alice, "/work-items/1", json={"title": "missing version"})
    assert r.status_code == 422


def test_missing_resource_is_404(api, ops):
    r = api.get(ops.alice, "/work-items/999999")
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"


def _make(world, ops, n, **kw):
    return [world.item(ops.team, ops.alice, title=f"Item {i:03d}", **kw) for i in range(n)]


def test_keyset_pagination_walks_every_item_exactly_once(api, world, ops):
    _make(world, ops, 23)
    seen, cursor, pages = [], None, 0
    while True:
        params = {"limit": 5}
        if cursor:
            params["cursor"] = cursor
        page = api.get(ops.alice, "/work-items", params=params).json()
        assert page["total"] == 23
        seen += [i["id"] for i in page["items"]]
        pages += 1
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert pages == 5
    assert len(seen) == len(set(seen)) == 23


def test_pagination_is_stable_when_items_change_between_pages(api, world, ops):
    """Offset pagination would skip/duplicate rows here; keyset pagination does not."""
    items = _make(world, ops, 10)
    first = api.get(ops.alice, "/work-items", params={"limit": 4}).json()
    # An item already shown on page 1 is updated and jumps to the top of the sort order.
    moved = first["items"][0]
    api.patch(ops.alice, f"/work-items/{moved['id']}", json={"version": 1, "title": "bumped"})
    rest, cursor = [], first["next_cursor"]
    while cursor:
        page = api.get(ops.alice, "/work-items", params={"limit": 4, "cursor": cursor}).json()
        rest += [i["id"] for i in page["items"]]
        cursor = page["next_cursor"]
    shown = [i["id"] for i in first["items"]] + rest
    assert sorted(shown) == sorted(i.id for i in items)  # nothing skipped, nothing repeated


def test_all_sort_orders_paginate_consistently(api, world, ops):
    for i in range(9):
        world.item(ops.team, ops.alice, title=f"S{i}", priority=["low", "medium", "high", "critical"][i % 4])
    with get_engine().begin() as conn:
        conn.execute(text("UPDATE work_items SET due_date = current_date + (id % 3)::int WHERE id % 2 = 0"))
    for sort in ["-updated_at", "-created_at", "-priority", "due_date"]:
        ids, cursor = [], None
        while True:
            params = {"limit": 2, "sort": sort}
            if cursor:
                params["cursor"] = cursor
            page = api.get(ops.alice, "/work-items", params=params).json()
            ids += [i["id"] for i in page["items"]]
            cursor = page["next_cursor"]
            if not cursor:
                break
        assert len(ids) == len(set(ids)) == 9, sort
    prios = [
        i["priority"] for i in api.get(ops.alice, "/work-items", params={"sort": "-priority"}).json()["items"]
    ]
    order = {"critical": 3, "high": 2, "medium": 1, "low": 0}
    assert [order[p] for p in prios] == sorted((order[p] for p in prios), reverse=True)


def test_bad_cursor_and_sort_are_validation_errors(api, ops):
    assert api.get(ops.alice, "/work-items", params={"cursor": "garbage!!"}).status_code == 422
    assert api.get(ops.alice, "/work-items", params={"sort": "title"}).status_code == 422


def test_filters(api, world, ops):
    a = world.item(ops.team, ops.manager, title="Mine", priority="high", assignee=ops.alice)
    world.item(ops.team, ops.manager, title="Unassigned low", priority="low")
    b = world.item(ops.team, ops.manager, title="Bob's", assignee=ops.bob)
    api.post(ops.bob, f"/work-items/{b.id}/transitions", json={"version": 1, "to_status": "in_progress"})

    def ids(**params):
        return {i["id"] for i in api.get(ops.alice, "/work-items", params=params).json()["items"]}

    assert ids(assignee="me") == {a.id}
    assert len(ids(assignee="none")) == 1
    assert ids(status="in_progress") == {b.id}
    assert ids(status=["in_progress", "open"]) == ids()
    assert ids(priority="high") == {a.id}
    assert ids(assignee=str(ops.bob.id)) == {b.id}
    assert ids(team_id=ops.team.id) == ids()
    assert ids(reporter="me") == set()


def test_overdue_filter(api, world, ops):
    late = world.item(ops.team, ops.alice, title="late")
    done_late = world.item(ops.team, ops.alice, title="late but cancelled")
    with get_engine().begin() as conn:
        conn.execute(text("UPDATE work_items SET due_date = current_date - 2"))
        conn.execute(text("UPDATE work_items SET status='cancelled' WHERE id=:i"), {"i": done_late.id})
    r = api.get(ops.alice, "/work-items", params={"overdue": "true"}).json()
    assert [i["id"] for i in r["items"]] == [late.id]
    assert r["items"][0]["is_overdue"] is True


def test_full_text_search_and_key_lookup(api, world, ops):
    world.item(
        ops.team, ops.alice, title="Refund stuck in processing", description="acquirer settlement delay"
    )
    world.item(ops.team, ops.alice, title="Password reset emails not delivered")
    world.item(ops.team, ops.alice, title="Unrelated")

    def titles(q):
        return [i["title"] for i in api.get(ops.alice, "/work-items", params={"q": q}).json()["items"]]

    assert titles("refunds") == ["Refund stuck in processing"]  # stemming
    assert titles("settlement") == ["Refund stuck in processing"]  # description match
    assert titles("passw") == ["Password reset emails not delivered"]  # substring on title
    assert titles("OPS-2") == ["Password reset emails not delivered"]  # key lookup
    assert titles("ops-2") == ["Password reset emails not delivered"]
    assert titles("100%_") == []  # LIKE wildcards are escaped


def test_activity_pagination(api, world, ops):
    item = world.item(ops.team, ops.alice)
    for i in range(6):
        api.post(ops.alice, f"/work-items/{item.id}/comments", json={"body": f"c{i}"})
    page1 = api.get(ops.alice, f"/work-items/{item.id}/activity", params={"limit": 4}).json()
    page2 = api.get(
        ops.alice, f"/work-items/{item.id}/activity", params={"limit": 4, "cursor": page1["next_cursor"]}
    ).json()
    assert len(page1["items"]) == 4 and len(page2["items"]) == 3 and page2["next_cursor"] is None


def test_dashboard_counts(api, world, ops):
    world.item(ops.team, ops.manager, priority="critical", assignee=ops.alice)
    world.item(ops.team, ops.manager, priority="low")
    x = world.item(ops.team, ops.manager, assignee=ops.alice)
    api.post(ops.alice, f"/work-items/{x.id}/transitions", json={"version": 1, "to_status": "in_progress"})
    api.post(
        ops.alice,
        f"/work-items/{x.id}/transitions",
        json={"version": 2, "to_status": "blocked", "reason": "r"},
    )
    with get_engine().begin() as conn:
        conn.execute(text("UPDATE work_items SET due_date = current_date - 1 WHERE id = :i"), {"i": x.id})
    t = api.get(ops.alice, "/dashboard/summary").json()["totals"]
    assert t == {
        "active": 3,
        "open": 2,
        "in_progress": 0,
        "blocked": 1,
        "resolved": 0,
        "overdue": 1,
        "high_priority": 1,
        "unassigned": 1,
        "assigned_to_me": 2,
    }
