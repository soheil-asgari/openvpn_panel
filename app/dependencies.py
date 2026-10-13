"""FastAPI dependencies: current user, roles, CSRF, API key auth."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.core.ratelimit import RateLimiter
from app.db import get_db
from app.models.user import Role, User
from app.services import api_keys as api_keys_service
from app.services import sessions as sessions_service

DbSession = Annotated[Session, Depends(get_db)]
SettingsDep = Annotated[Settings, Depends(get_settings)]

_login_limiter = RateLimiter(
    max_attempts=get_settings().login_max_attempts,
    window_seconds=get_settings().login_window_seconds,
)


def get_login_limiter() -> RateLimiter:
    return _login_limiter


def client_ip(request: Request) -> str:
    if request.client:
        return request.client.host
    return "unknown"


def get_current_user(request: Request, db: DbSession) -> User:
    """Resolve the panel user from the signed session cookie."""

    from app.core.sessions import build_signer

    settings = get_settings()
    raw = request.cookies.get(settings.session_cookie)
    if not raw:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="not authenticated")
    signer = build_signer(settings)
    session_id = signer.verify(raw)
    if not session_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid session")
    session = sessions_service.get_session(db, session_id)
    if session is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="session expired")
    user = db.get(User, session.user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="inactive user")
    sessions_service.touch(db, session, settings.session_max_age)
    db.commit()
    request.state.session = session
    request.state.user = user
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_admin(user: CurrentUser) -> User:
    if user.role != Role.ADMIN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="admin required")
    return user


AdminUser = Annotated[User, Depends(require_admin)]


def get_api_user(
    request: Request,
    db: DbSession,
    authorization: Annotated[str | None, Header()] = None,
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> User:
    """Authenticate API requests via ``Authorization: Bearer`` or ``X-API-Key``."""

    raw = x_api_key
    if not raw and authorization and authorization.lower().startswith("bearer "):
        raw = authorization[7:].strip()
    if not raw:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing API key")
    resolved = api_keys_service.resolve_key(db, raw)
    if resolved is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid API key")
    key, user = resolved
    if not user.is_active or api_keys_service.key_expired(key):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="expired API key")
    # Expose the key so endpoints can enforce scopes.
    request.state.api_key = key
    request.state.api_scopes = key.scope_set()
    db.commit()
    return user


ApiUser = Annotated[User, Depends(get_api_user)]


def scopes_allow(request: Request, scope: str) -> bool:
    """Whether the authenticated key may perform ``scope`` (empty = inherit all)."""

    scopes: set[str] = getattr(request.state, "api_scopes", set())
    if not scopes:
        return True
    return scope in scopes or "admin" in scopes


def require_scope(request: Request, scope: str) -> None:
    if not scopes_allow(request, scope):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="insufficient scope")


def scope_guard(scope: str):  # noqa: ANN201
    """Dependency factory enforcing a required API scope."""

    def _dep(request: Request, user: ApiUser) -> User:
        require_scope(request, scope)
        return user

    return _dep


ScopedUser = Annotated[User, Depends(scope_guard("users:read"))]
