"""Tests for Phase 3 user-management features."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.core.units import format_size, parse_size
from app.models.user import Role
from app.models.vpn_user import VpnUserStatus
from app.services import users as users_service
from app.services import vpn_users as vpn_users_service


def test_parse_size_units() -> None:
    assert parse_size("500MB") == 500 * 1024 * 1024
    assert parse_size("2GB") == 2 * 1024 * 1024 * 1024
    assert parse_size("1024") == 1024
    assert parse_size("0") is None
    assert parse_size(None) is None


def test_format_size() -> None:
    assert format_size(None) == "unlimited"
    assert format_size(1024) == "1.00 KB"
    assert format_size(1024 * 1024 * 1024).endswith("GB")


def test_bulk_create_generates_credentials(db) -> None:  # noqa: ANN001
    created = vpn_users_service.bulk_create(db, prefix="cli", count=5)
    assert len(created) == 5
    usernames = [c.user.username for c in created]
    assert usernames == [f"cli{i:02d}" for i in range(1, 6)]
    assert all(c.password for c in created)
    assert all(c.user.status == VpnUserStatus.ACTIVE for c in created)


def test_bulk_create_enforces_reseller_quota(db) -> None:  # noqa: ANN001
    reseller = users_service.create_user(
        db, username="reseller", password="reseller-pass", role=Role.RESELLER, quota_users=3
    )
    with pytest.raises(vpn_users_service.VpnUserError):
        vpn_users_service.bulk_create(db, prefix="cli", count=5, owner=reseller)


def test_bulk_set_status_and_delete(db) -> None:  # noqa: ANN001
    created = vpn_users_service.bulk_create(db, prefix="cli", count=3)
    ids = [c.user.id for c in created]
    rows = vpn_users_service.bulk_set_status(db, ids, VpnUserStatus.DISABLED)
    assert all(r.status == VpnUserStatus.DISABLED for r in rows)
    assert vpn_users_service.bulk_delete(db, ids) == 3
    assert vpn_users_service.count_vpn_users(db) == 0


def test_bulk_reset_traffic(db) -> None:  # noqa: ANN001
    created = vpn_users_service.bulk_create(db, prefix="cli", count=2)
    for c in created:
        c.user.bytes_in = 100
        c.user.bytes_out = 50
    vpn_users_service.bulk_reset_traffic(db, [c.user.id for c in created])
    assert all(c.user.bytes_in == 0 and c.user.bytes_out == 0 for c in created)


def test_bulk_extend_days(db) -> None:  # noqa: ANN001
    user = vpn_users_service.create_vpn_user(db, username="client1", password="client-pass")
    now = datetime.now(timezone.utc)
    rows = vpn_users_service.bulk_extend(db, [user.id], days=10)
    assert rows[0].expires_at is not None
    assert rows[0].expires_at > now + timedelta(days=9)


def test_relative_expiry_after_first_connect(db) -> None:  # noqa: ANN001
    user = vpn_users_service.create_vpn_user(
        db, username="client1", password="client-pass", expire_days=30
    )
    # No first connection yet -> not expired, no absolute expiry.
    assert user.effective_expiry is None
    assert not user.is_expired
    vpn_users_service.mark_first_connection(db, user)
    assert user.effective_expiry is not None
    assert not user.is_expired


def test_relative_expiry_in_past_is_expired(db) -> None:  # noqa: ANN001
    user = vpn_users_service.create_vpn_user(
        db, username="client1", password="client-pass", expire_days=1
    )
    user.first_connected_at = datetime.now(timezone.utc) - timedelta(days=5)
    db.flush()
    assert user.is_expired
    assert not vpn_users_service.is_connectable(user)


def test_effective_connection_limit_override(db) -> None:  # noqa: ANN001
    user = vpn_users_service.create_vpn_user(
        db, username="client1", password="client-pass", max_connections=2
    )
    assert vpn_users_service.effective_connection_limit(db, user, 5) == 2


def test_effective_connection_limit_default(db) -> None:  # noqa: ANN001
    user = vpn_users_service.create_vpn_user(db, username="client1", password="client-pass")
    assert vpn_users_service.effective_connection_limit(db, user, 5) == 5


def test_reset_password(db) -> None:  # noqa: ANN001
    from app.core.security import verify_password

    user = vpn_users_service.create_vpn_user(db, username="client1", password="client-pass")
    new_password = vpn_users_service.reset_password(db, user)
    assert new_password != "client-pass"
    assert verify_password(new_password, user.password_hash)


def test_enforce_limits_marks_expired(db) -> None:  # noqa: ANN001
    user = vpn_users_service.create_vpn_user(
        db, username="client1", password="client-pass", data_limit_bytes=10
    )
    user.bytes_in = 20
    changed = vpn_users_service.enforce_limits(db, user)
    assert changed
    assert user.status == VpnUserStatus.EXPIRED
    assert user.expired_at is not None


def test_export_csv(db) -> None:  # noqa: ANN001
    vpn_users_service.create_vpn_user(db, username="client1", password="client-pass")
    csv_text = vpn_users_service.export_csv(db)
    lines = csv_text.strip().splitlines()
    assert lines[0].startswith("id,username,status")
    assert "client1" in lines[1]


def test_list_filters_and_sort(db) -> None:  # noqa: ANN001
    vpn_users_service.create_vpn_user(db, username="alpha", password="client-pass")
    beta = vpn_users_service.create_vpn_user(db, username="beta", password="client-pass")
    beta.is_online = True
    db.flush()

    online = vpn_users_service.list_vpn_users(db, status="online")
    assert [u.username for u in online] == ["beta"]

    found = vpn_users_service.list_vpn_users(db, search="alph")
    assert [u.username for u in found] == ["alpha"]

    desc = vpn_users_service.list_vpn_users(db, sort_by="username", sort_dir="desc")
    assert [u.username for u in desc] == ["beta", "alpha"]


# --------------------------------------------------------------------------- #
# REST API coverage
# --------------------------------------------------------------------------- #
def _admin_key() -> str:
    from app.db import get_session_factory
    from app.services import api_keys as api_keys_service

    db = get_session_factory()()
    try:
        admin = users_service.create_user(
            db, username="admin", password="admin-password", role=Role.ADMIN
        )
        _, raw = api_keys_service.create_key(db, admin, name="test")
        db.commit()
        return raw
    finally:
        db.close()


def _headers(raw: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {raw}"}


def test_api_bulk_create_and_export(client) -> None:  # noqa: ANN001
    raw = _admin_key()
    response = client.post(
        "/api/v1/users/bulk",
        json={"prefix": "cli", "count": 3},
        headers=_headers(raw),
    )
    assert response.status_code == 201
    body = response.json()
    assert len(body) == 3
    assert body[0]["username"] == "cli01"
    assert body[0]["password"]

    export = client.get("/api/v1/users/export", headers=_headers(raw))
    assert export.status_code == 200
    assert "cli01" in export.text


def test_api_bulk_actions(client) -> None:  # noqa: ANN001
    raw = _admin_key()
    created = client.post(
        "/api/v1/users/bulk",
        json={"prefix": "cli", "count": 2},
        headers=_headers(raw),
    ).json()
    ids = [item["id"] for item in created]

    disabled = client.post("/api/v1/users/bulk/disable", json={"ids": ids}, headers=_headers(raw))
    assert disabled.status_code == 200
    assert disabled.json()["affected"] == 2

    extended = client.post(
        "/api/v1/users/bulk/extend", json={"ids": ids, "days": 5}, headers=_headers(raw)
    )
    assert extended.status_code == 200
    assert extended.json()["affected"] == 2

    reset = client.post(
        "/api/v1/users/bulk/reset-traffic", json={"ids": ids}, headers=_headers(raw)
    )
    assert reset.status_code == 200

    deleted = client.post("/api/v1/users/bulk/delete", json={"ids": ids}, headers=_headers(raw))
    assert deleted.json()["affected"] == 2


def test_api_filters_sort_pagination(client) -> None:  # noqa: ANN001
    raw = _admin_key()
    client.post("/api/v1/users/bulk", json={"prefix": "cli", "count": 5}, headers=_headers(raw))
    listing = client.get(
        "/api/v1/users?limit=2&offset=1&sort_by=username&sort_dir=desc",
        headers=_headers(raw),
    )
    assert listing.status_code == 200
    assert [u["username"] for u in listing.json()] == ["cli04", "cli03"]


def test_api_reset_password_and_kick(client) -> None:  # noqa: ANN001
    raw = _admin_key()
    created = client.post(
        "/api/v1/users",
        json={"username": "client1", "password": "client-pass"},
        headers=_headers(raw),
    ).json()
    user_id = created["id"]

    reset = client.post(f"/api/v1/users/{user_id}/reset-password", headers=_headers(raw))
    assert reset.status_code == 200
    assert reset.json()["password"]

    kick = client.post(f"/api/v1/users/{user_id}/kick", headers=_headers(raw))
    assert kick.status_code == 200
