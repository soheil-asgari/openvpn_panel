"""CSRF protection using the double-submit cookie pattern.

The CSRF token is stored in the server-side session and mirrored into a
non-HttpOnly cookie so HTMX requests can send it back in the ``X-CSRF-Token``
header. State changing requests are rejected when the header and the session
token do not match.
"""

from __future__ import annotations

import secrets

from fastapi import HTTPException, Request, status

SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}
HEADER_NAME = "X-CSRF-Token"
FORM_FIELD = "csrf_token"


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def _provided_token(request: Request, form_token: str | None = None) -> str | None:
    header = request.headers.get(HEADER_NAME)
    if header:
        return header
    if form_token:
        return form_token
    return None


def verify_csrf(
    request: Request,
    session_token: str | None,
    form_token: str | None = None,
) -> None:
    """Raise :class:`HTTPException` when CSRF validation fails."""

    if request.method in SAFE_METHODS:
        return
    provided = _provided_token(request, form_token)
    if not session_token or not provided:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="CSRF token missing")
    if not secrets.compare_digest(session_token, provided):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="CSRF token invalid")
