"""Request metadata helpers."""

from fastapi import Request

from app.core.config import get_settings


def client_ip(request: Request) -> str:
    """Client address. With OPSFLOW_TRUST_PROXY_HEADERS the right-most X-Forwarded-For entry is used:
    it is the address our own proxy (nginx) observed and appended, so clients cannot spoof it by
    sending their own header."""
    if get_settings().trust_proxy_headers:
        forwarded = request.headers.get("x-forwarded-for", "")
        parts = [p.strip() for p in forwarded.split(",") if p.strip()]
        if parts:
            return parts[-1][:64]
    return (request.client.host if request.client else "unknown")[:64]


def user_agent(request: Request) -> str:
    return (request.headers.get("user-agent") or "")[:300]
