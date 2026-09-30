"""Dashboard metrics computed in a single aggregate query — never by shipping items to the browser."""

from sqlalchemy import Integer, and_, cast, func, select
from sqlalchemy.orm import Session

from app.auth.policy import visible_team_ids_subquery
from app.domain.workflow import TERMINAL_STATUSES
from app.models import Team, User, WorkItem
from app.schemas import DashboardOut, StatusCounts, TeamBrief, TeamCounts
from app.services.work_items import today

TERMINAL = [s.value for s in TERMINAL_STATUSES]


def summary(db: Session, user: User, team_id: int | None = None) -> DashboardOut:
    active = WorkItem.status.notin_(TERMINAL)

    def c(cond):
        return func.count().filter(cond)

    cols = [
        c(active).label("active"),
        c(WorkItem.status == "open").label("open"),
        c(WorkItem.status == "in_progress").label("in_progress"),
        c(WorkItem.status == "blocked").label("blocked"),
        c(WorkItem.status == "resolved").label("resolved"),
        c(and_(active, WorkItem.due_date < today())).label("overdue"),
        c(and_(active, WorkItem.priority >= cast(2, Integer))).label("high_priority"),
        c(and_(active, WorkItem.assignee_id.is_(None))).label("unassigned"),
        c(and_(active, WorkItem.assignee_id == user.id)).label("assigned_to_me"),
    ]
    stmt = (
        select(Team.id, Team.key, Team.name, *cols)
        .select_from(Team)
        .outerjoin(WorkItem, WorkItem.team_id == Team.id)
        .group_by(Team.id)
        .order_by(Team.name)
    )
    if not user.is_admin:
        stmt = stmt.where(Team.id.in_(visible_team_ids_subquery(user)))
    if team_id is not None:
        stmt = stmt.where(Team.id == team_id)

    fields = list(StatusCounts.model_fields)
    by_team: list[TeamCounts] = []
    totals = dict.fromkeys(fields, 0)
    for row in db.execute(stmt).all():
        counts = {f: getattr(row, f) for f in fields}
        by_team.append(TeamCounts(team=TeamBrief(id=row.id, key=row.key, name=row.name), **counts))
        for f in fields:
            totals[f] += counts[f]
    return DashboardOut(totals=StatusCounts(**totals), by_team=by_team)
