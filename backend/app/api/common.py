"""Helpers shared by API routers."""

from collections.abc import Callable
from typing import Any

from fastapi import Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.models import User
from app.services.idempotency import IdempotencyGuard


def commit_idempotent(
    db: Session,
    user: User,
    request: Request,
    idempotency_key: str | None,
    payload: Any,
    status_code: int,
    operation: Callable[[], BaseModel],
) -> JSONResponse:
    """Run `operation` as one unit of work, optionally protected by an Idempotency-Key.

    The key reservation, the business change and the stored response share one transaction.
    """
    guard: IdempotencyGuard | None = None
    if idempotency_key:
        guard = IdempotencyGuard(db, user.id, idempotency_key, request.method, request.url.path, payload)
        replay = guard.begin()
        if replay is not None:
            db.rollback()
            replay_status, replay_body = replay
            return JSONResponse(
                content=replay_body,
                status_code=replay_status,
                headers={"Idempotent-Replayed": "true"},
            )

    body = jsonable_encoder(operation())
    if guard is not None:
        guard.complete(status_code, body)
    db.commit()
    return JSONResponse(content=body, status_code=status_code)
