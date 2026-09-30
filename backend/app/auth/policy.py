"""Authorization policy — the single place that decides who may do what.

Model
-----
* Global role: `is_admin` — may administer teams/memberships and acts as a manager everywhere.
* Team role (per TeamMembership): `manager` or `member`.
* Resource relationships (per WorkItem): reporter, assignee.

Visibility rule: a work item is visible only to members of its team (and admins). Invisible
resources yield 404 rather than 403 so their existence is not leaked across teams.

The API computes `permissions` for the UI from these same functions, but the services
re-check them on every mutation; hiding a button is never the security boundary.
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.workflow import TERMINAL_STATUSES, Actor, Status, allowed_transitions
from app.models import TeamMembership, User, WorkItem

MANAGER = "manager"
MEMBER = "member"


@dataclass(frozen=True)
class TeamAccess:
    user: User
    team_id: int
    role: str | None  # None => not a member

    @property
    def is_member(self) -> bool:
        return self.role is not None

    @property
    def can_view(self) -> bool:
        return self.is_member or self.user.is_admin

    @property
    def is_manager(self) -> bool:
        return self.role == MANAGER or self.user.is_admin


def team_access(db: Session, user: User, team_id: int) -> TeamAccess:
    role = db.execute(
        select(TeamMembership.role).where(
            TeamMembership.team_id == team_id, TeamMembership.user_id == user.id
        )
    ).scalar_one_or_none()
    return TeamAccess(user=user, team_id=team_id, role=role)


def visible_team_ids_subquery(user: User):
    return select(TeamMembership.team_id).where(TeamMembership.user_id == user.id)


def actors_for(access: TeamAccess, item: WorkItem) -> set[Actor]:
    actors: set[Actor] = set()
    if access.is_manager:
        actors.add(Actor.MANAGER)
    if item.assignee_id == access.user.id:
        actors.add(Actor.ASSIGNEE)
    if item.reporter_id == access.user.id:
        actors.add(Actor.REPORTER)
    return actors


def is_terminal(item: WorkItem) -> bool:
    return Status(item.status) in TERMINAL_STATUSES


def can_edit(access: TeamAccess, item: WorkItem) -> bool:
    """Title/description/priority/due date: reporter, assignee or manager, on non-terminal items.
    Reporters/assignees must still be team members to edit."""
    if is_terminal(item):
        return False
    if access.is_manager:
        return True
    return access.is_member and bool({Actor.ASSIGNEE, Actor.REPORTER} & actors_for(access, item))


def can_claim(access: TeamAccess, item: WorkItem) -> bool:
    return access.is_member and item.assignee_id is None and not is_terminal(item)


def can_assign_others(access: TeamAccess, item: WorkItem) -> bool:
    return access.is_manager and not is_terminal(item)


def can_release(access: TeamAccess, item: WorkItem) -> bool:
    """The current assignee may hand an item back while it is still 'open'."""
    return item.assignee_id == access.user.id and access.is_member and item.status == Status.OPEN


def can_comment(access: TeamAccess) -> bool:
    return access.is_member or access.user.is_admin


def item_permissions(access: TeamAccess, item: WorkItem) -> dict:
    transitions = (
        allowed_transitions(
            Status(item.status), actors_for(access, item), has_assignee=item.assignee_id is not None
        )
        if (access.is_member or access.user.is_admin)
        else []
    )
    return {
        "can_edit": can_edit(access, item),
        "can_claim": can_claim(access, item),
        "can_assign": can_assign_others(access, item),
        "can_release": can_release(access, item),
        "can_comment": can_comment(access),
        "transitions": transitions,
    }
