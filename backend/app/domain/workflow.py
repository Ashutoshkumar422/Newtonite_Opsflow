"""Pure domain rules for the work item lifecycle — no I/O, fully unit-testable.

State machine
-------------
    open ──► in_progress ──► resolved ──► closed        (closed = manager approval)
     │  ▲        │  ▲             │
     │  └────────┘  │             └──► in_progress      (rejected by reporter/manager)
     │        ▼     │
     │      blocked ┘
     ▼        │
  cancelled ◄─┘

closed and cancelled are terminal.
"""

from dataclasses import dataclass
from enum import StrEnum


class Status(StrEnum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    RESOLVED = "resolved"
    CLOSED = "closed"
    CANCELLED = "cancelled"


TERMINAL_STATUSES = frozenset({Status.CLOSED, Status.CANCELLED})
ACTIVE_STATUSES = frozenset(set(Status) - TERMINAL_STATUSES)
# States that represent someone actively owning the work. DB CHECK mirrors this.
STATUSES_REQUIRING_ASSIGNEE = frozenset({Status.IN_PROGRESS, Status.BLOCKED, Status.RESOLVED})


class Priority(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


# Stored as smallint so ORDER BY priority is meaningful and indexable.
PRIORITY_RANK: dict[Priority, int] = {
    Priority.LOW: 0,
    Priority.MEDIUM: 1,
    Priority.HIGH: 2,
    Priority.CRITICAL: 3,
}
RANK_PRIORITY: dict[int, Priority] = {v: k for k, v in PRIORITY_RANK.items()}


class Actor(StrEnum):
    """Relationship of the acting user to a work item (computed per request)."""

    ASSIGNEE = "assignee"
    REPORTER = "reporter"
    MANAGER = "manager"  # team manager or global admin


@dataclass(frozen=True)
class TransitionRule:
    allowed_actors: frozenset[Actor]
    requires_reason: bool = False
    label: str = ""


_A = Actor
TRANSITIONS: dict[Status, dict[Status, TransitionRule]] = {
    Status.OPEN: {
        Status.IN_PROGRESS: TransitionRule(frozenset({_A.ASSIGNEE, _A.MANAGER}), label="Start work"),
        Status.CANCELLED: TransitionRule(
            frozenset({_A.REPORTER, _A.MANAGER}), requires_reason=True, label="Cancel"
        ),
    },
    Status.IN_PROGRESS: {
        Status.BLOCKED: TransitionRule(
            frozenset({_A.ASSIGNEE, _A.MANAGER}), requires_reason=True, label="Mark blocked"
        ),
        Status.RESOLVED: TransitionRule(frozenset({_A.ASSIGNEE, _A.MANAGER}), label="Resolve"),
        Status.OPEN: TransitionRule(frozenset({_A.ASSIGNEE, _A.MANAGER}), label="Stop work"),
    },
    Status.BLOCKED: {
        Status.IN_PROGRESS: TransitionRule(frozenset({_A.ASSIGNEE, _A.MANAGER}), label="Unblock"),
        Status.CANCELLED: TransitionRule(frozenset({_A.MANAGER}), requires_reason=True, label="Cancel"),
    },
    Status.RESOLVED: {
        Status.CLOSED: TransitionRule(frozenset({_A.MANAGER}), label="Approve & close"),
        Status.IN_PROGRESS: TransitionRule(
            frozenset({_A.REPORTER, _A.MANAGER}), requires_reason=True, label="Reject resolution"
        ),
    },
    Status.CLOSED: {},
    Status.CANCELLED: {},
}


class TransitionError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def check_transition(
    current: Status,
    target: Status,
    actors: set[Actor],
    *,
    has_assignee: bool,
    reason: str | None,
) -> TransitionRule:
    """Validate a transition. Raises TransitionError; returns the rule when permitted.

    Order matters for good error messages: structural validity first (is this edge in the
    graph at all), then authorization for the edge, then edge preconditions.
    """
    rule = TRANSITIONS.get(current, {}).get(target)
    if rule is None:
        raise TransitionError("invalid_transition", f"Cannot move a work item from '{current}' to '{target}'")
    if not (rule.allowed_actors & actors):
        allowed = ", ".join(sorted(a.value for a in rule.allowed_actors))
        raise TransitionError(
            "forbidden", f"Only the {allowed} may move this item from '{current}' to '{target}'"
        )
    if target in STATUSES_REQUIRING_ASSIGNEE and not has_assignee:
        raise TransitionError(
            "assignee_required", f"A work item must be assigned before it can be '{target}'"
        )
    if rule.requires_reason and not (reason and reason.strip()):
        raise TransitionError("reason_required", f"A reason is required to move to '{target}'")
    return rule


def allowed_transitions(current: Status, actors: set[Actor], *, has_assignee: bool) -> list[dict]:
    """Transitions this actor may attempt right now — used to drive the UI (never for security)."""
    out = []
    for target, rule in TRANSITIONS.get(current, {}).items():
        if not (rule.allowed_actors & actors):
            continue
        if target in STATUSES_REQUIRING_ASSIGNEE and not has_assignee:
            continue
        out.append({"to": target.value, "label": rule.label, "requires_reason": rule.requires_reason})
    return out
