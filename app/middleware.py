"""Middleware: host check, security headers and CSRF enforcement for HTML forms."""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.config import get_settings
from app.core.csrf import FORM_FIELD, verify_csrf

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
    "X-XSS-Protection": "1; mode=block",
}

CSRF_EXEMPT_PREFIXES = ("/api/v1", "/hooks", "/static", "/login", "/sub")


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):  # noqa: ANN001
        response: Response = await call_next(request)
        for key, value in SECURITY_HEADERS.items():
            response.headers.setdefault(key, value)
        return response


class CSRFMiddleware(BaseHTTPMiddleware):
    """Verify the CSRF token for same-site HTML form submissions.

    API and hook routes are exempt because they use bearer/API-key auth or a
    dedicated hook token instead of cookies.
    """

    async def dispatch(self, request: Request, call_next):  # noqa: ANN001
        if request.method in {"GET", "HEAD", "OPTIONS", "TRACE"}:
            return await call_next(request)
        path = request.url.path
        if path.startswith(CSRF_EXEMPT_PREFIXES) or path.endswith("/login"):
            return await call_next(request)
        session_token = _resolve_session_token(request)
        form_token: str | None = request.headers.get("X-CSRF-Token")
        if form_token is None and request.headers.get("content-type", "").startswith(
            "application/x-www-form-urlencoded"
        ):
            body = await request.body()
            from urllib.parse import parse_qs

            parsed = parse_qs(body.decode("utf-8", errors="replace"))
            values = parsed.get(FORM_FIELD)
            if values:
                form_token = values[0]

            async def receive() -> dict:
                return {"type": "http.request", "body": body, "more_body": False}

            request._receive = receive  # type: ignore[attr-defined]
        try:
            verify_csrf(request, session_token, form_token)
        except Exception as exc:  # noqa: BLE001
            from fastapi import HTTPException

            if isinstance(exc, HTTPException):
                return Response(
                    content=exc.detail, status_code=exc.status_code, media_type="text/plain"
                )
            raise
        return await call_next(request)


def _resolve_session_token(request: Request) -> str | None:
    """Load the CSRF token from the server-side session referenced by the cookie."""

    from app.core.sessions import build_signer
    from app.db import get_session_factory
    from app.services import sessions as sessions_service

    settings = get_settings()
    raw = request.cookies.get(settings.session_cookie)
    if not raw:
        return None
    session_id = build_signer(settings).verify(raw)
    if not session_id:
        return None
    db = get_session_factory()()
    try:
        session = sessions_service.get_session(db, session_id)
        return session.csrf_token if session else None
    finally:
        db.close()


class TrustedHostsMiddleware(BaseHTTPMiddleware):
    """Reject requests whose ``Host`` header is not allow-listed."""

    async def dispatch(self, request: Request, call_next):  # noqa: ANN001
        allowed = get_settings().trusted_hosts
        if allowed and "*" not in allowed:
            host = request.headers.get("host", "").split(":")[0]
            if host and host not in allowed:
                return Response(content="Invalid host", status_code=400)
        return await call_next(request)
