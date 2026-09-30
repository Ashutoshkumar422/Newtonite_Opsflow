"""Pure unit tests of the workflow state machine (no database)."""

import pytest

from app.domain.workflow import (
    TERMINAL_STATUSES,
    TRANSITIONS,
    Actor,
    Status,
    TransitionError,
    allowed_transitions,
    check_transition,
)

A, R, M = Actor.ASSIGNEE, Actor.REPORTER, Actor.MANAGER


def err(current, target, actors, has_assignee=True, reason=None) -> str:
    with pytest.raises(TransitionError) as e:
        check_transition(current, target, set(actors), has_assignee=has_assignee, reason=reason)
    return e.value.code


@pytest.mark.parametrize(
    "current,target,actors,reason",
    [
        (Status.OPEN, Status.IN_PROGRESS, {A}, None),
        (Status.IN_PROGRESS, Status.BLOCKED, {A}, "waiting on vendor"),
        (Status.BLOCKED, Status.IN_PROGRESS, {A}, None),
        (Status.IN_PROGRESS, Status.RESOLVED, {A}, None),
        (Status.RESOLVED, Status.CLOSED, {M}, None),
        (Status.RESOLVED, Status.IN_PROGRESS, {R}, "fix does not work"),
        (Status.OPEN, Status.CANCELLED, {R}, "duplicate"),
        (Status.IN_PROGRESS, Status.OPEN, {M}, None),
    ],
)
def test_valid_transitions(current, target, actors, reason):
    check_transition(current, target, actors, has_assignee=True, reason=reason)


@pytest.mark.parametrize(
    "current,target",
    [
        (Status.OPEN, Status.RESOLVED),  # cannot skip work
        (Status.OPEN, Status.CLOSED),  # cannot skip approval
        (Status.IN_PROGRESS, Status.CLOSED),  # approval only from resolved
        (Status.BLOCKED, Status.RESOLVED),
        (Status.CLOSED, Status.OPEN),  # terminal
        (Status.CANCELLED, Status.IN_PROGRESS),  # terminal
        (Status.OPEN, Status.OPEN),  # no self-loops
    ],
)
def test_structurally_invalid_transitions(current, target):
    assert err(current, target, {A, R, M}, reason="x") == "invalid_transition"


def test_terminal_states_have_no_exits():
    for s in TERMINAL_STATUSES:
        assert TRANSITIONS[s] == {}


def test_only_manager_can_approve_closure():
    assert err(Status.RESOLVED, Status.CLOSED, {A, R}) == "forbidden"


def test_non_assignee_member_cannot_start_work():
    assert err(Status.OPEN, Status.IN_PROGRESS, set()) == "forbidden"
    assert err(Status.OPEN, Status.IN_PROGRESS, {R}) == "forbidden"


def test_assignee_cannot_reject_own_resolution():
    assert err(Status.RESOLVED, Status.IN_PROGRESS, {A}, reason="x") == "forbidden"


def test_owned_states_require_assignee():
    assert err(Status.OPEN, Status.IN_PROGRESS, {M}, has_assignee=False) == "assignee_required"


@pytest.mark.parametrize("reason", [None, "", "   "])
def test_reason_required_for_blocking_cancelling_and_rejecting(reason):
    assert err(Status.IN_PROGRESS, Status.BLOCKED, {A}, reason=reason) == "reason_required"
    assert err(Status.OPEN, Status.CANCELLED, {M}, reason=reason) == "reason_required"
    assert err(Status.RESOLVED, Status.IN_PROGRESS, {M}, reason=reason) == "reason_required"


def test_allowed_transitions_reflect_actor_and_assignment():
    assert allowed_transitions(Status.OPEN, set(), has_assignee=False) == []
    member_view = allowed_transitions(Status.OPEN, {R}, has_assignee=False)
    assert [t["to"] for t in member_view] == ["cancelled"]
    manager_unassigned = allowed_transitions(Status.OPEN, {M}, has_assignee=False)
    assert "in_progress" not in [t["to"] for t in manager_unassigned]
    assignee_view = allowed_transitions(Status.RESOLVED, {A}, has_assignee=True)
    assert assignee_view == []
