"""Login, logout and CSRF-aware session handling."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Form, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse

from app.config import get_settings
from app.core.sessions import build_signer
from app.db import get_session_factory
from app.dependencies import client_ip, get_login_limiter
from app.services import sessions as sessions_service
from app.services import users as users_service

router = APIRouter()


def _set_session_cookie(response: RedirectResponse, token: str, settings) -> None:  # noqa: ANN001
    response.set_cookie(
        settings.session_cookie,
        token,
        max_age=settings.session_max_age,
        httponly=True,
        secure=settings.session_secure,
        samesite="lax",
        path="/",
    )


def _clear_session_cookie(response: RedirectResponse, settings) -> None:  # noqa: ANN001
    response.delete_cookie(settings.session_cookie, path="/")


def _login_page(request: Request, error: str | None = None, locked: bool = False) -> HTMLResponse:
    from pathlib import Path

    from fastapi.templating import Jinja2Templates

    from app.core.i18n import translator

    settings = get_settings()
    t = translator(settings.default_language)
    templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))
    context = {
        "request": request,
        "t": t,
        "lang": t.language,
        "is_rtl": t.is_rtl,
        "base_path": settings.admin_prefix,
        "error": error,
        "locked": locked,
    }
    return templates.TemplateResponse(request, "login.html", context)


@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request) -> HTMLResponse:
    settings = get_settings()
    raw = request.cookies.get(settings.session_cookie)
    if raw and build_signer(settings).verify(raw):
        return RedirectResponse(url=f"{settings.admin_prefix}/", status_code=303)
    return _login_page(request)


@router.post("/login")
def login(
    request: Request,
    username: Annotated[str, Form()],
    password: Annotated[str, Form()],
) -> Response:
    settings = get_settings()
    limiter = get_login_limiter()
    key = f"{client_ip(request)}:{username}"
    if limiter.is_locked(key):
        return _login_page(request, locked=True)

    db = get_session_factory()()
    try:
        user = users_service.authenticate(db, username, password)
        if user is None:
            limiter.register_failure(key)
            db.rollback()
            return _login_page(request, error="invalid")
        limiter.reset(key)
        from app.services import notifications

        notifications.notify_login(db, user, client_ip(request))
        session = sessions_service.create_session(
            db,
            user_id=user.id,
            max_age=settings.session_max_age,
            ip_address=client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
        db.commit()
        token = build_signer(settings).sign(session.session_id)
    finally:
        db.close()

    response = RedirectResponse(url=f"{settings.admin_prefix}/", status_code=303)
    _set_session_cookie(response, token, settings)
    return response


@router.post("/logout")
def logout(request: Request) -> RedirectResponse:
    settings = get_settings()
    raw = request.cookies.get(settings.session_cookie)
    if raw:
        session_id = build_signer(settings).verify(raw)
        if session_id:
            db = get_session_factory()()
            try:
                sessions_service.delete_session(db, session_id)
                db.commit()
            finally:
                db.close()
    response = RedirectResponse(url=f"{settings.admin_prefix}/login", status_code=303)
    _clear_session_cookie(response, settings)
    return response
