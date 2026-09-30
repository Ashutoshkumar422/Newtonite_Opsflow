"""Transactional outbox + notification worker.

Producers call `emit()` inside the same transaction as the business change, so an event exists
if and only if the change committed. A separate worker process (`python -m app.worker`) claims
pending events with `FOR UPDATE SKIP LOCKED` (so several workers never process the same event
concurrently), runs the handler, and marks the event processed in the same transaction.

Failure semantics
  * Handler raises   -> attempts+1, exponential backoff via available_at, last_error recorded.
  * attempts >= max  -> status 'failed' (dead letter), visible at GET /api/v1/admin/outbox.
  * Worker crashes   -> transaction rolls back, row locks release, event is retried later.
  * Runs twice       -> handlers are idempotent: notifications are unique per (event, user).
Core writes (create/assign/transition) never depend on the worker being up; notifications are
simply delayed.
"""

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.models import Notification, OutboxEvent

log = logging.getLogger("opsflow.outbox")


def emit(db: Session, event_type: str, payload: dict[str, Any]) -> None:
    db.add(OutboxEvent(event_type=event_type, payload=payload))


def emit_notification(
    db: Session,
    kind: str,
    *,
    recipients: set[int],
    actor_id: int,
    work_item_id: int,
    message: str,
) -> None:
    targets = sorted(r for r in recipients if r and r != actor_id)
    if not targets:
        return
    emit(
        db,
        "notify",
        {"kind": kind, "recipients": targets, "work_item_id": work_item_id, "message": message},
    )


# ---------------------------------------------------------------- handlers
def _handle_notify(db: Session, event: OutboxEvent) -> None:
    p = event.payload
    for user_id in p["recipients"]:
        db.execute(
            insert(Notification)
            .values(
                user_id=user_id,
                event_id=event.id,
                work_item_id=p.get("work_item_id"),
                kind=p["kind"],
                message=p["message"],
            )
            .on_conflict_do_nothing(constraint="uq_notification_event_user")
        )


HANDLERS: dict[str, Callable[[Session, OutboxEvent], None]] = {"notify": _handle_notify}


def backoff(attempts: int) -> timedelta:
    return timedelta(seconds=min(2**attempts, 300))


def process_batch(factory: sessionmaker[Session], batch_size: int | None = None) -> int:
    """Process up to batch_size due events. Returns the number of events examined."""
    s = get_settings()
    limit = batch_size or s.outbox_batch_size
    with factory() as db:
        events = (
            db.execute(
                select(OutboxEvent)
                .where(OutboxEvent.status == "pending", OutboxEvent.available_at <= func.now())
                .order_by(OutboxEvent.id)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
            .scalars()
            .all()
        )
        now = datetime.now(UTC)
        for event in events:
            handler = HANDLERS.get(event.event_type)
            try:
                if handler is None:
                    raise RuntimeError(f"no handler for event type {event.event_type!r}")
                with db.begin_nested():  # savepoint: a failing event can't poison the batch
                    handler(db, event)
                event.status = "processed"
                event.processed_at = now
            except Exception as exc:  # noqa: BLE001 — record any handler failure
                event.attempts += 1
                event.last_error = f"{type(exc).__name__}: {exc}"[:2000]
                if event.attempts >= s.outbox_max_attempts:
                    event.status = "failed"
                    log.error("outbox event %s dead-lettered: %s", event.id, event.last_error)
                else:
                    event.available_at = now + backoff(event.attempts)
                    log.warning("outbox event %s failed (attempt %s)", event.id, event.attempts)
        db.commit()
        return len(events)
