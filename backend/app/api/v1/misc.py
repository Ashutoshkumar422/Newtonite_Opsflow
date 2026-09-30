from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.auth.deps import get_current_user, require_admin
from app.core.errors import NotFound
from app.core.net import client_ip
from app.db.session import get_session
from app.models import Notification, OutboxEvent, User
from app.schemas import (
    DashboardOut,
    MemberOut,
    MemberUpsert,
    NotificationOut,
    NotificationPage,
    OutboxStats,
    TeamCreate,
    TeamOut,
)
from app.services import dashboard as dashboard_svc
from app.services import security_log
from app.services import teams as teams_svc

router = APIRouter()


# ---------------------------------------------------------------- teams
@router.get("/teams", response_model=list[TeamOut], tags=["teams"])
def list_teams(user: User = Depends(get_current_user), db: Session = Depends(get_session)):
    return teams_svc.list_teams(db, user)


@router.post("/teams", response_model=TeamOut, status_code=201, tags=["teams"])
def create_team(
    data: TeamCreate,
    request: Request,
    user: User = Depends(require_admin),
    db: Session = Depends(get_session),
):
    team = teams_svc.create_team(db, user, data)
    security_log.record(
        db, "team_created", actor_id=user.id, ip=client_ip(request), team_id=team.id, key=team.key
    )
    db.commit()
    return teams_svc.get_team(db, user, team.id)


@router.get("/teams/{team_id}", response_model=TeamOut, tags=["teams"])
def get_team(team_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_session)):
    return teams_svc.get_team(db, user, team_id)


@router.get("/teams/{team_id}/members", response_model=list[MemberOut], tags=["teams"])
def list_members(team_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_session)):
    return teams_svc.list_members(db, user, team_id)


@router.put("/teams/{team_id}/members/{user_id}", response_model=MemberOut, tags=["teams"])
def upsert_member(
    team_id: int,
    user_id: int,
    data: MemberUpsert,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
):
    """Add a member or change their role (idempotent by nature: PUT of the same role is a no-op)."""
    out = teams_svc.upsert_member(db, user, team_id, user_id, data.role)
    db.commit()
    return out


@router.delete("/teams/{team_id}/members/{user_id}", status_code=204, tags=["teams"])
def remove_member(
    team_id: int,
    user_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
):
    teams_svc.remove_member(db, user, team_id, user_id)
    db.commit()
    return Response(status_code=204)


# ---------------------------------------------------------------- dashboard
@router.get("/dashboard/summary", response_model=DashboardOut, tags=["dashboard"])
def dashboard_summary(
    team_id: int | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
):
    return dashboard_svc.summary(db, user, team_id)


# ---------------------------------------------------------------- notifications
@router.get("/notifications", response_model=NotificationPage, tags=["notifications"])
def list_notifications(
    cursor: int | None = None,
    limit: int = Query(20, ge=1, le=100),
    unread_only: bool = False,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
):
    stmt = select(Notification).where(Notification.user_id == user.id)
    if unread_only:
        stmt = stmt.where(Notification.read_at.is_(None))
    if cursor:
        stmt = stmt.where(Notification.id < cursor)
    rows = db.execute(stmt.order_by(Notification.id.desc()).limit(limit + 1)).scalars().all()
    unread = db.execute(
        select(func.count()).where(Notification.user_id == user.id, Notification.read_at.is_(None))
    ).scalar_one()
    has_more = len(rows) > limit
    rows = rows[:limit]
    return NotificationPage(
        items=[NotificationOut.model_validate(n) for n in rows],
        next_cursor=str(rows[-1].id) if has_more and rows else None,
        unread_count=unread,
    )


@router.post("/notifications/{notification_id}/read", status_code=204, tags=["notifications"])
def mark_read(
    notification_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_session)
):
    result = db.execute(
        update(Notification)
        .where(Notification.id == notification_id, Notification.user_id == user.id)
        .values(read_at=func.coalesce(Notification.read_at, func.now()))
    )
    if not result.rowcount:  # type: ignore[attr-defined]
        raise NotFound("Notification not found")
    db.commit()
    return Response(status_code=204)


@router.post("/notifications/read-all", status_code=204, tags=["notifications"])
def mark_all_read(user: User = Depends(get_current_user), db: Session = Depends(get_session)):
    db.execute(
        update(Notification)
        .where(Notification.user_id == user.id, Notification.read_at.is_(None))
        .values(read_at=func.now())
    )
    db.commit()
    return Response(status_code=204)


# ---------------------------------------------------------------- admin / observability
@router.get("/admin/outbox", response_model=OutboxStats, tags=["admin"])
def outbox_stats(_: User = Depends(require_admin), db: Session = Depends(get_session)):
    counts = dict(db.execute(select(OutboxEvent.status, func.count()).group_by(OutboxEvent.status)).all())
    oldest = db.execute(
        select(func.extract("epoch", func.now() - func.min(OutboxEvent.created_at))).where(
            OutboxEvent.status == "pending"
        )
    ).scalar_one_or_none()
    failures = db.execute(
        select(OutboxEvent.id, OutboxEvent.event_type, OutboxEvent.attempts, OutboxEvent.last_error)
        .where(OutboxEvent.status == "failed")
        .order_by(OutboxEvent.id.desc())
        .limit(10)
    ).all()
    return OutboxStats(
        pending=counts.get("pending", 0),
        processed=counts.get("processed", 0),
        failed=counts.get("failed", 0),
        oldest_pending_age_seconds=float(oldest) if oldest is not None else None,
        recent_failures=[
            {"id": f.id, "event_type": f.event_type, "attempts": f.attempts, "last_error": f.last_error}
            for f in failures
        ],
    )
