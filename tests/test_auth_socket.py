"""Tests for the unix-socket authentication service."""

from __future__ import annotations

from app.models.user import Role
from app.models.vpn_user import VpnUserStatus
from app.services import auth_socket
from app.services import users as users_service
from app.services import vpn_users as vpn_users_service


def _make_vpn_user(db, username: str = "vpnuser", password: str = "vpn-pass-123"):  # noqa: ANN001
    admin = users_service.create_user(
        db, username="authadmin", password="admin-pass-123", role=Role.ADMIN
    )
    db.commit()
    vpn_user = vpn_users_service.create_vpn_user(
        db, username=username, password=password, owner=admin
    )
    db.commit()
    return vpn_user


def test_check_accepts_valid_credentials(db) -> None:  # noqa: ANN001
    _make_vpn_user(db)
    ok, reason = auth_socket._check("vpnuser", "vpn-pass-123")
    assert ok is True
    assert reason is None


def test_check_rejects_bad_password(db) -> None:  # noqa: ANN001
    _make_vpn_user(db)
    ok, reason = auth_socket._check("vpnuser", "wrong-password")
    assert ok is False
    assert reason == "bad password"


def test_check_rejects_unknown_user(db) -> None:  # noqa: ANN001
    ok, reason = auth_socket._check("ghostuser", "whatever-123")
    assert ok is False
    assert reason == "unknown user"


def test_check_rejects_disabled_user(db) -> None:  # noqa: ANN001
    vpn_user = _make_vpn_user(db, username="disabled1")
    vpn_users_service.set_status(db, vpn_user, VpnUserStatus.DISABLED)
    db.commit()
    ok, reason = auth_socket._check("disabled1", "vpn-pass-123")
    assert ok is False
    assert reason == "not connectable"


def test_check_rejects_invalid_username(db) -> None:  # noqa: ANN001
    ok, reason = auth_socket._check("x", "whatever-123")
    assert ok is False
    assert reason == "invalid username"
