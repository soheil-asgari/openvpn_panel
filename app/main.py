"""FastAPI application entry point."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import __version__
from app.api import (
    api_protocols,
    api_tunnels,
    api_v1,
    auth,
    hooks,
    partials,
    subscription,
    web,
)
from app.config import get_settings
from app.logging import configure_logging
from app.middleware import CSRFMiddleware, SecurityHeadersMiddleware, TrustedHostsMiddleware

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ANN001
    from app.services.scheduler import start_scheduler, stop_scheduler

    settings = get_settings()
    scheduler = None
    if not getattr(settings, "disable_scheduler", False):
        try:
            scheduler = start_scheduler()
        except Exception as exc:  # noqa: BLE001
            logger.warning("scheduler not started: %s", exc)

    # Local unix socket for OpenVPN's auth-user-pass-verify hook.
    from app.services import auth_socket

    try:
        auth_socket.start_auth_socket()
    except Exception as exc:  # noqa: BLE001
        logger.warning("auth socket not started: %s", exc)

    yield
    auth_socket.stop_auth_socket()
    if scheduler is not None:
        stop_scheduler()


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings)

    app = FastAPI(
        title="OpenVPN Web Panel",
        version=__version__,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )

    app.add_middleware(TrustedHostsMiddleware)
    app.add_middleware(CSRFMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)

    from pathlib import Path

    static_dir = Path(__file__).resolve().parent / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    prefix = settings.admin_prefix
    app.include_router(auth.router, prefix=prefix)
    app.include_router(web.router, prefix=prefix)
    app.include_router(partials.router, prefix=prefix)
    app.include_router(api_v1.router, prefix="/api/v1")
    app.include_router(api_tunnels.router, prefix="/api/v1")
    app.include_router(api_protocols.router, prefix="/api/v1")
    app.include_router(subscription.router)
    app.include_router(hooks.router)

    # Expose the admin prefix to templates/middleware and allow redirects from
    # the root to the configured admin path.
    app.state.admin_prefix = prefix

    register_exception_handlers(app)
    return app


def _wants_html(request: Request) -> bool:
    accept = request.headers.get("accept", "")
    return "text/html" in accept and not request.url.path.startswith("/api")


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(StarletteHTTPException)
    async def _http_handler(request: Request, exc: StarletteHTTPException):  # noqa: ANN001
        if (
            exc.status_code == 401
            and _wants_html(request)
            and not request.url.path.endswith("/login")
            and not request.url.path.startswith("/hooks")
        ):
            return RedirectResponse(url=f"{_login_url()}", status_code=303)
        if _wants_html(request):
            from pathlib import Path

            from fastapi.templating import Jinja2Templates

            from app.core.i18n import translator

            t = translator(get_settings().default_language)
            templates = Jinja2Templates(
                directory=str(Path(__file__).resolve().parent / "templates")
            )
            context = {
                "request": request,
                "t": t,
                "lang": t.language,
                "is_rtl": t.is_rtl,
                "code": exc.status_code,
                "detail": exc.detail,
            }
            return templates.TemplateResponse(
                request, "error.html", context, status_code=exc.status_code
            )
        return await http_exception_handler(request, exc)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):  # noqa: ANN001
        logger.exception("unhandled error: %s", exc)
        if _wants_html(request):
            return HTMLResponse("<h1>500 — Internal Server Error</h1>", status_code=500)
        return JSONResponse({"detail": "internal server error"}, status_code=500)


def _login_url() -> str:
    return f"{get_settings().admin_prefix}/login"


app = create_app()
