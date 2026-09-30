"""Idempotency keys for state-changing requests.

Contract (documented in docs/API_DOCUMENTATION.md):
  * Scope:       (user, Idempotency-Key). Keys from different users never collide.
  * Lifetime:    OPSFLOW_IDEMPOTENCY_TTL_HOURS (default 24h); expired keys are treated as new.
  * Fingerprint: SHA-256 over method + path + canonical JSON body.
  * Same key + same fingerprint  -> the stored response is replayed (header Idempotent-Replayed).
  * Same key + different request -> 422 idempotency_key_reused; nothing is executed.
  * Concurrent duplicates:        the key row is inserted inside the business transaction, so a
    second request with the same key blocks on the unique index until the first commits, then
    replays its response. If the first fails, its transaction (key row included) rolls back and
    the retry executes normally. Only successful responses are ever stored.
"""

import hashlib
import json
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import IdempotencyKeyReused, ValidationFailed
from app.models import IdempotencyKey

_KEY_RE = re.compile(r"^[A-Za-z0-9_\-:.]{8,255}$")


def fingerprint(method: str, path: str, body: Any) -> str:
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(f"{method.upper()} {path}\n{canonical}".encode()).hexdigest()


class IdempotencyGuard:
    def __init__(self, db: Session, user_id: int, key: str, method: str, path: str, body: Any):
        if not _KEY_RE.match(key):
            raise ValidationFailed(
                "Idempotency-Key must be 8-255 characters of [A-Za-z0-9_-:.]",
                details={"header": "Idempotency-Key"},
            )
        self.db = db
        self.user_id = user_id
        self.key = key
        self.method = method.upper()
        self.path = path
        self.request_hash = fingerprint(method, path, body)
        self._record_id: int | None = None

    def begin(self) -> tuple[int, Any] | None:
        """Reserve the key. Returns a stored (status, body) to replay, or None to proceed."""
        now = datetime.now(UTC)
        # Lazy expiry: an expired key for this user is discarded and the request runs fresh.
        self.db.execute(
            delete(IdempotencyKey).where(
                IdempotencyKey.user_id == self.user_id,
                IdempotencyKey.key == self.key,
                IdempotencyKey.expires_at <= now,
            )
        )
        stmt = (
            insert(IdempotencyKey)
            .values(
                user_id=self.user_id,
                key=self.key,
                request_hash=self.request_hash,
                method=self.method,
                path=self.path,
                expires_at=now + timedelta(hours=get_settings().idempotency_ttl_hours),
            )
            .on_conflict_do_nothing(constraint="uq_idempotency_user_key")
            .returning(IdempotencyKey.id)
        )
        inserted = self.db.execute(stmt).scalar_one_or_none()
        if inserted is not None:
            self._record_id = inserted
            return None

        existing = self.db.execute(
            select(IdempotencyKey).where(
                IdempotencyKey.user_id == self.user_id, IdempotencyKey.key == self.key
            )
        ).scalar_one()
        if existing.request_hash != self.request_hash:
            raise IdempotencyKeyReused(
                "This Idempotency-Key was already used for a different request",
                details={"original_method": existing.method, "original_path": existing.path},
            )
        assert existing.response_status is not None  # rows are only visible once completed
        return existing.response_status, existing.response_body

    def complete(self, status_code: int, body: Any) -> None:
        assert self._record_id is not None
        self.db.execute(
            update(IdempotencyKey)
            .where(IdempotencyKey.id == self._record_id)
            .values(response_status=status_code, response_body=body)
        )


def purge_expired(db: Session) -> int:
    result = db.execute(delete(IdempotencyKey).where(IdempotencyKey.expires_at <= datetime.now(UTC)))
    return result.rowcount or 0  # type: ignore[attr-defined]
