"""Tests for the VPN user service."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.core.validators import ValidationError
from app.models.user import Role
from app.models.vpn_user import VpnUserStatus
from app.services import users as users_service
from app.services import vpn_users as vpn_users_service


def test_create_vpn_user(db) -> None:  # noqa: ANN001
    user = vpn_users_service.create_vpn_user(db, username="client1", password="client-pass")
    assert user.common_name == "client1"
    assert user.status == VpnUserStatus.ACTIVE
    assert vpn_users_service.is_connectable(user)


def test_duplicate_vpn_username_rejected(db) -> None:  # noqa: ANN001
    vpn_users_service.create_vpn_user(db, username="client1", password="client-pass")
    with pytest.raises(vpn_users_service.VpnUserError):
        vpn_users_service.create_vpn_user(db, username="client1", password="client-pass")


def test_invalid_vpn_username_rejected(db) -> None:  # noqa: ANN001
    with pytest.raises(ValidationError):
        vpn_users_service.create_vpn_user(db, username="bad name", password="client-pass")


def test_expired_user_not_connectable(db) -> None:  # noqa: ANN001
    past = datetime.now(timezone.utc) - timedelta(days=1)
    user = vpn_users_service.create_vpn_user(
        db, username="client1", password="client-pass", expires_at=past
    )
    assert user.is_expired
    assert not vpn_users_service.is_connectable(user)


def test_disabled_user_not_connectable(db) -> None:  # noqa: ANN001
    user = vpn_users_service.create_vpn_user(db, username="client1", password="client-pass")
    vpn_users_service.set_status(db, user, VpnUserStatus.DISABLED)
    assert not vpn_users_service.is_connectable(user)


def test_quota_exceeded_not_connectable(db) -> None:  # noqa: ANN001
    user = vpn_users_service.create_vpn_user(
        db, username="client1", password="client-pass", data_limit_bytes=100
    )
    user.bytes_in = 60
    user.bytes_out = 60
    assert user.quota_exceeded
    assert not vpn_users_service.is_connectable(user)


def test_reseller_quota_enforced(db) -> None:  # noqa: ANN001
    reseller = users_service.create_user(
        db, username="reseller", password="reseller-pass", role=Role.RESELLER, quota_users=1
    )
    vpn_users_service.create_vpn_user(
        db, username="client1", password="client-pass", owner=reseller
    )
    with pytest.raises(vpn_users_service.VpnUserError):
        vpn_users_service.create_vpn_user(
            db, username="client2", password="client-pass", owner=reseller
        )


def test_reset_usage(db) -> None:  # noqa: ANN001
    user = vpn_users_service.create_vpn_user(db, username="client1", password="client-pass")
    user.bytes_in = 10
    user.bytes_out = 20
    vpn_users_service.reset_usage(db, user)
    assert user.bytes_in == 0 and user.bytes_out == 0


def test_delete_vpn_user(db) -> None:  # noqa: ANN001
    user = vpn_users_service.create_vpn_user(db, username="client1", password="client-pass")
    vpn_users_service.delete_vpn_user(db, user)
    assert vpn_users_service.get(db, user.id) is None
