"""FastAPI application: wiring, middleware and structured error handling."""

import logging
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError, OperationalError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.v1 import account, auth, misc, work_items
from app.core.config import get_settings
from app.core.errors import AppError
from app.db.session import get_engine

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("opsflow.api")


def _error(
    status: int,
    code: str,
    message: str,
    request: Request,
    details: dict | None = None,
    headers: dict[str, str] | None = None,
):
    return JSONResponse(
        status_code=status,
        content={
            "error": {"code": code, "message": message, "details": details or {}},
            "request_id": getattr(request.state, "request_id", None),
        },
        headers=headers,
    )


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="OpsFlow API",
        version="0.1.0",
        description="Operational work management — Newtonite engineering challenge. "
        "Errors use the envelope {error: {code, message, details}, request_id}.",
        docs_url="/api/docs",
        redoc_url="/api/redoc",
        openapi_url="/api/openapi.json",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"],
        allow_headers=["Content-Type", "Authorization", "Idempotency-Key", "X-Requested-With"],
        expose_headers=["Idempotent-Replayed", "X-Request-ID"],
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        request.state.request_id = request_id
        start = time.perf_counter()
        response = await call_next(request)
        elapsed_ms = (time.perf_counter() - start) * 1000
        response.headers["X-Request-ID"] = request_id
        # Defence-in-depth headers for every API response (nginx adds CSP/HSTS for the SPA).
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        if request.url.path.startswith("/api/v1/"):
            # Authenticated JSON must never be stored by browsers or shared caches.
            response.headers.setdefault("Cache-Control", "no-store")
        log.info(
            "request_id=%s method=%s path=%s status=%s duration_ms=%.1f",
            request_id,
            request.method,
            request.url.path,
            response.status_code,
            elapsed_ms,
        )
        return response

    @app.exception_handler(AppError)
    async def app_error(request: Request, exc: AppError):
        return _error(exc.status_code, exc.code, exc.message, request, exc.details, exc.headers or None)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        fields = [
            {
                "loc": [str(p) for p in e.get("loc", [])],
                "message": e.get("msg", "invalid"),
                "type": e.get("type", ""),
            }
            for e in exc.errors()
        ]
        return _error(422, "validation_failed", "Request validation failed", request, {"fields": fields})

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        code = {404: "not_found", 405: "method_not_allowed"}.get(exc.status_code, "http_error")
        return _error(exc.status_code, code, str(exc.detail), request)

    @app.exception_handler(IntegrityError)
    async def integrity_error(request: Request, exc: IntegrityError):
        constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
        log.warning("integrity error request_id=%s constraint=%s", request.state.request_id, constraint)
        return _error(
            409,
            "constraint_violation",
            "The request conflicts with the current state of the data",
            request,
            {"constraint": constraint} if constraint else None,
        )

    @app.exception_handler(OperationalError)
    async def db_unavailable(request: Request, exc: OperationalError):
        log.error("database operational error request_id=%s: %s", request.state.request_id, exc)
        return _error(503, "database_unavailable", "The database is temporarily unavailable", request)

    @app.exception_handler(DBAPIError)
    async def db_error(request: Request, exc: DBAPIError):
        log.exception("database error request_id=%s", request.state.request_id)
        return _error(500, "internal_error", "An unexpected error occurred", request)

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):
        log.exception("unhandled error request_id=%s", getattr(request.state, "request_id", None))
        return _error(500, "internal_error", "An unexpected error occurred", request)

    @app.get("/api/health", tags=["ops"])
    def health():
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"status": "ok", "database": "ok"}

    app.include_router(auth.router, prefix="/api/v1")
    app.include_router(work_items.router, prefix="/api/v1")
    app.include_router(work_items.comments_router, prefix="/api/v1")
    app.include_router(misc.router, prefix="/api/v1")
    app.include_router(account.router, prefix="/api/v1")
    return app


app = create_app()
