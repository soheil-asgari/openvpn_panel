"""OpenVPN hook endpoints.

These are called by the OpenVPN daemon (through small wrapper scripts invoked
via ``auth-user-pass-verify``, ``client-connect`` and ``client-disconnect``).
They are localhost-only and authenticated with a shared hook token stored in
``/etc/ovpn-panel/`` (mode 600).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, status
from pydantic import BaseModel, field_validator
from sqlalchemy import select

from app.config import get_settings
from app.core.security import constant_time_compare, verify_password
from app.core.validators import validate_username
from app.dependencies import DbSession
from app.models.vpn_user import VpnUser
from app.services import vpn_sessions as vpn_sessions_service
from app.services import vpn_users as vpn_users_service

router = APIRouter(prefix="/hooks", tags=["hooks"])


class AuthRequest(BaseModel):
    username: str
    password: str

    @field_validator("username")
    @classmethod
    def _check_username(cls, value: str) -> str:
        return validate_username(value)


class ConnectRequest(BaseModel):
    common_name: str
    trusted_ip: str | None = None
    bytes_received: int = 0
    bytes_sent: int = 0

    @field_validator("common_name")
    @classmethod
    def _check_common_name(cls, value: str) -> str:
        return validate_username(value)


class DisconnectRequest(BaseModel):
    common_name: str
    bytes_received: int = 0
    bytes_sent: int = 0

    @field_validator("common_name")
    @classmethod
    def _check_common_name(cls, value: str) -> str:
        return validate_username(value)


def _check_token(token: str | None) -> None:
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="hook token required")
    expected = getattr(get_settings(), "hook_token", "") or ""
    if not expected or not constant_time_compare(token, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid hook token")


def _get_vpn_user(db: DbSession, common_name: str) -> VpnUser | None:
    validate_username(common_name)
    return db.scalar(select(VpnUser).where(VpnUser.username == common_name))


@router.post("/auth")
def hook_auth(
    payload: AuthRequest,
    db: DbSession,
    x_hook_token: Annotated[str | None, Header(alias="X-Hook-Token")] = None,
) -> dict:
    _check_token(x_hook_token)
    vpn_user = _get_vpn_user(db, payload.username)
    if vpn_user is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="denied")
    if not verify_password(payload.password, vpn_user.password_hash):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="denied")
    if not vpn_users_service.is_connectable(
        vpn_user, owner_active=vpn_users_service.owner_active(db, vpn_user)
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="not connectable")
    # Reseller aggregate data quota is enforced on every authenticate.
    from app.models.user import Role, User
    from app.services import resellers as resellers_service

    if vpn_user.owner_id is not None:
        owner = db.get(User, vpn_user.owner_id)
        if owner is not None and owner.role == Role.RESELLER:
            try:
                resellers_service.enforce_data_quota(db, owner)
            except resellers_service.ResellerError as exc:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    limit = vpn_users_service.effective_connection_limit(
        db, vpn_user, get_settings().max_concurrent_connections
    )
    if vpn_users_service.exceeds_concurrent_limit(db, vpn_user, limit):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="concurrent connection limit reached"
        )
    return {"ok": True}


@router.post("/connect")
def hook_connect(
    payload: ConnectRequest,
    db: DbSession,
    x_hook_token: Annotated[str | None, Header(alias="X-Hook-Token")] = None,
) -> dict:
    _check_token(x_hook_token)
    vpn_user = _get_vpn_user(db, payload.common_name)
    if vpn_user is None or not vpn_users_service.is_connectable(
        vpn_user, owner_active=vpn_users_service.owner_active(db, vpn_user)
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="denied")
    vpn_sessions_service.open_session(
        db,
        vpn_user=vpn_user,
        common_name=payload.common_name,
        trusted_ip=payload.trusted_ip,
        node_id=vpn_user.node_id,
    )
    db.commit()
    return {"ok": True}


@router.post("/disconnect")
def hook_disconnect(
    payload: DisconnectRequest,
    db: DbSession,
    x_hook_token: Annotated[str | None, Header(alias="X-Hook-Token")] = None,
) -> dict:
    _check_token(x_hook_token)
    vpn_user = _get_vpn_user(db, payload.common_name)
    if vpn_user is None:
        return {"ok": True}
    vpn_sessions_service.close_session(
        db,
        vpn_user=vpn_user,
        common_name=payload.common_name,
        bytes_received=payload.bytes_received,
        bytes_sent=payload.bytes_sent,
    )
    db.commit()
    return {"ok": True}
