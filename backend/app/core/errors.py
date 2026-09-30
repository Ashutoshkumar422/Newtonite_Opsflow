"""Domain errors and their mapping to structured HTTP responses.

Every error response has the same envelope:
    {"error": {"code": "...", "message": "...", "details": {...}}, "request_id": "..."}
Stack traces and internal messages are never returned to clients.
"""

from typing import Any


class AppError(Exception):
    status_code = 400
    code = "bad_request"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        details: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ):
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        self.details = details or {}
        self.headers = headers or {}


class NotAuthenticated(AppError):
    status_code = 401
    code = "not_authenticated"


class Forbidden(AppError):
    status_code = 403
    code = "forbidden"


class NotFound(AppError):
    status_code = 404
    code = "not_found"


class Conflict(AppError):
    """State-dependent conflict: stale version, item already claimed, invariant violation."""

    status_code = 409
    code = "conflict"


class InvalidTransition(AppError):
    status_code = 409
    code = "invalid_transition"


class ValidationFailed(AppError):
    status_code = 422
    code = "validation_failed"


class IdempotencyKeyReused(AppError):
    status_code = 422
    code = "idempotency_key_reused"


class TooManyRequests(AppError):
    status_code = 429
    code = "too_many_attempts"
