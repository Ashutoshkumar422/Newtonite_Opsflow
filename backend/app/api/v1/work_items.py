from typing import Any

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.api.common import commit_idempotent
from app.auth.deps import get_current_user
from app.db.session import get_session
from app.domain.workflow import Priority, Status
from app.models import User
from app.schemas import (
    ActivityOut,
    AssignIn,
    CommentCreate,
    CommentOut,
    Page,
    TransitionIn,
    WorkItemCreate,
    WorkItemOut,
    WorkItemSummary,
    WorkItemUpdate,
)
from app.services import work_items as svc

router = APIRouter(prefix="/work-items", tags=["work items"])

IdemKey = Header(
    None,
    alias="Idempotency-Key",
    description="Client-generated unique key (8-255 chars). Retries with the same key and body "
    "replay the original response instead of repeating the side effect.",
)

CONFLICT_DOC: dict[int | str, dict[str, Any]] = {
    409: {"description": "version_conflict | already_claimed | invalid_transition | item_closed"},
    403: {"description": "Authenticated but not permitted"},
    404: {"description": "Not found or not visible to you"},
}


@router.get("", response_model=Page[WorkItemSummary])
def list_work_items(
    team_id: int | None = None,
    status: list[Status] | None = Query(None, description="Repeatable: ?status=open&status=blocked"),
    priority: list[Priority] | None = Query(None),
    assignee: str | None = Query(None, description="'me', 'none' or a user id"),
    reporter: str | None = Query(None, description="'me' or a user id"),
    q: str | None = Query(None, max_length=200, description="Full-text search, or an item key like PAY-12"),
    overdue: bool = False,
    sort: str = Query("-updated_at", description="-updated_at | -created_at | -priority | due_date"),
    cursor: str | None = None,
    limit: int = Query(25, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> Page[WorkItemSummary]:
    return svc.list_items(
        db,
        user,
        sort=sort,
        cursor=cursor,
        limit=limit,
        team_id=team_id,
        statuses=status,
        priorities=priority,
        assignee=assignee,
        reporter=reporter,
        q=q,
        overdue=overdue,
    )


@router.post("", status_code=201, response_model=WorkItemOut, responses=CONFLICT_DOC)
def create_work_item(
    data: WorkItemCreate,
    request: Request,
    idempotency_key: str | None = IdemKey,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> JSONResponse:
    def op() -> WorkItemOut:
        item, access = svc.create_item(db, user, data)
        return svc.to_out(item, access)

    return commit_idempotent(db, user, request, idempotency_key, data.model_dump(mode="json"), 201, op)


@router.get("/{item_id}", response_model=WorkItemOut, responses=CONFLICT_DOC)
def get_work_item(
    item_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_session)
) -> WorkItemOut:
    item, access = svc.load_for_user(db, user, item_id)
    return svc.to_out(item, access)


@router.patch("/{item_id}", response_model=WorkItemOut, responses=CONFLICT_DOC)
def update_work_item(
    item_id: int,
    data: WorkItemUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> WorkItemOut:
    """Partial update with optimistic concurrency: send the `version` you last saw.
    A mismatch returns 409 `version_conflict` with the current item in `error.details.current`."""
    item, access = svc.update_item(db, user, item_id, data)
    out = svc.to_out(item, access)
    db.commit()
    return out


@router.post("/{item_id}/claim", response_model=WorkItemOut, responses=CONFLICT_DOC)
def claim_work_item(
    item_id: int,
    request: Request,
    idempotency_key: str | None = IdemKey,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> JSONResponse:
    """Take ownership of an unassigned item. Exactly one of several concurrent claimers wins;
    the others get 409 `already_claimed`. Re-claiming an item you already own is a no-op 200."""

    def op() -> WorkItemOut:
        item, access, _changed = svc.claim_item(db, user, item_id)
        return svc.to_out(item, access)

    return commit_idempotent(db, user, request, idempotency_key, {}, 200, op)


@router.post("/{item_id}/assign", response_model=WorkItemOut, responses=CONFLICT_DOC)
def assign_work_item(
    item_id: int,
    data: AssignIn,
    request: Request,
    idempotency_key: str | None = IdemKey,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> JSONResponse:
    def op() -> WorkItemOut:
        item, access = svc.assign_item(db, user, item_id, data.assignee_id, data.version)
        return svc.to_out(item, access)

    return commit_idempotent(db, user, request, idempotency_key, data.model_dump(mode="json"), 200, op)


@router.post("/{item_id}/transitions", response_model=WorkItemOut, responses=CONFLICT_DOC)
def transition_work_item(
    item_id: int,
    data: TransitionIn,
    request: Request,
    idempotency_key: str | None = IdemKey,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> JSONResponse:
    def op() -> WorkItemOut:
        item, access = svc.transition_item(db, user, item_id, data.to_status, data.reason, data.version)
        return svc.to_out(item, access)

    return commit_idempotent(db, user, request, idempotency_key, data.model_dump(mode="json"), 200, op)


@router.get("/{item_id}/activity", response_model=Page[ActivityOut])
def item_activity(
    item_id: int,
    cursor: int | None = Query(None, description="Return entries older than this activity id"),
    limit: int = Query(50, ge=1, le=200),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> Page[ActivityOut]:
    return svc.list_activity(db, user, item_id, before_id=cursor, limit=limit)


@router.get("/{item_id}/comments", response_model=Page[CommentOut])
def item_comments(
    item_id: int,
    cursor: int | None = Query(None, description="Return comments newer than this comment id"),
    limit: int = Query(50, ge=1, le=200),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> Page[CommentOut]:
    return svc.list_comments(db, user, item_id, after_id=cursor, limit=limit)


@router.post("/{item_id}/comments", status_code=201, response_model=CommentOut)
def add_item_comment(
    item_id: int,
    data: CommentCreate,
    request: Request,
    idempotency_key: str | None = IdemKey,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> JSONResponse:
    def op() -> CommentOut:
        return svc.comment_out(svc.add_comment(db, user, item_id, data.body), user)

    return commit_idempotent(db, user, request, idempotency_key, data.model_dump(mode="json"), 201, op)


comments_router = APIRouter(prefix="/comments", tags=["work items"])


@comments_router.patch("/{comment_id}", response_model=CommentOut)
def edit_comment(
    comment_id: int,
    data: CommentCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> CommentOut:
    out = svc.comment_out(svc.edit_comment(db, user, comment_id, data.body), user)
    db.commit()
    return out
