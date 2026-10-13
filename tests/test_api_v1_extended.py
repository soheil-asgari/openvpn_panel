"""Tests for Phase 8 REST API: scoped keys, stats and the renew endpoint."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.db import get_session_factory
from app.models.user import Role


def _admin_key(scopes: list[str] | None = None, expires_at: datetime | None = None) -> str:
    from app.services import api_keys as api_keys_service
    from app.services import users as users_service

    db = get_session_factory()()
    try:
        admin = users_service.get_by_username(db, "admin")
        if admin is None:
            admin = users_service.create_user(
                db, username="admin", password="admin-password", role=Role.ADMIN
            )
        _, raw = api_keys_service.create_key(
            db, admin, name="test", scopes=scopes, expires_at=expires_at
        )
        db.commit()
        return raw
    finally:
        db.close()


def _headers(raw: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {raw}"}


def test_keys_list_reports_scopes(client) -> None:  # noqa: ANN001
    raw = _admin_key(scopes=["users:read", "reports:read"])
    listing = client.get("/api/v1/keys", headers=_headers(raw))
    assert listing.status_code == 200
    key = listing.json()[0]
    assert key["scopes"] == ["reports:read", "users:read"]


def test_key_create_with_scopes(client) -> None:  # noqa: ANN001
    raw = _admin_key()
    created = client.post(
        "/api/v1/keys",
        json={"name": "ci", "scopes": ["users:read"]},
        headers=_headers(raw),
    )
    assert created.status_code == 201
    body = created.json()
    assert body["scopes"] == ["users:read"]
    assert body["key"].startswith("ovpn_")


def test_key_create_rejects_unknown_scope(client) -> None:  # noqa: ANN001
    raw = _admin_key()
    response = client.post(
        "/api/v1/keys",
        json={"name": "bad", "scopes": ["nonsense"]},
        headers=_headers(raw),
    )
    assert response.status_code == 400


def test_key_query_name_still_supported(client) -> None:  # noqa: ANN001
    raw = _admin_key()
    response = client.post("/api/v1/keys", params={"name": "ci"}, headers=_headers(raw))
    assert response.status_code == 201


def test_key_rotate_and_revoke(client) -> None:  # noqa: ANN001
    raw = _admin_key()
    created = client.post("/api/v1/keys", json={"name": "ci"}, headers=_headers(raw)).json()
    key_id = created["id"]

    rotated = client.post(f"/api/v1/keys/{key_id}/rotate", headers=_headers(raw))
    assert rotated.status_code == 200
    new_raw = rotated.json()["key"]
    assert new_raw != created["key"]
    # The new secret works.
    assert client.get("/api/v1/users", headers=_headers(new_raw)).status_code == 200

    assert client.post(f"/api/v1/keys/{key_id}/revoke", headers=_headers(raw)).status_code == 200
    assert client.get("/api/v1/users", headers=_headers(new_raw)).status_code == 401


def test_expired_key_is_rejected(client) -> None:  # noqa: ANN001
    raw = _admin_key(expires_at=datetime.now(timezone.utc) - timedelta(days=1))
    assert client.get("/api/v1/users", headers=_headers(raw)).status_code == 401


def test_scope_enforcement_on_stats(client) -> None:  # noqa: ANN001
    raw = _admin_key(scopes=["users:read"])
    # reports:read missing -> 403
    assert client.get("/api/v1/stats", headers=_headers(raw)).status_code == 403

    allowed = _admin_key(scopes=["reports:read"])
    assert client.get("/api/v1/stats", headers=_headers(allowed)).status_code == 200


def test_empty_scopes_inherit_all(client) -> None:  # noqa: ANN001
    raw = _admin_key()
    assert client.get("/api/v1/stats", headers=_headers(raw)).status_code == 200


def test_stats_shape(client) -> None:  # noqa: ANN001
    raw = _admin_key()
    response = client.get("/api/v1/stats", headers=_headers(raw))
    assert response.status_code == 200
    body = response.json()
    for field in ("total_users", "online_users", "total_nodes", "total_bytes", "top_users"):
        assert field in body


def test_renew_endpoint_by_days(client) -> None:  # noqa: ANN001
    raw = _admin_key()
    created = client.post(
        "/api/v1/users",
        json={"username": "client1", "password": "client-pass"},
        headers=_headers(raw),
    ).json()
    renewed = client.post(
        f"/api/v1/users/{created['id']}/renew",
        json={"days": 30},
        headers=_headers(raw),
    )
    assert renewed.status_code == 200
    assert renewed.json()["expires_at"] is not None


def test_renew_endpoint_absolute_and_reset(client) -> None:  # noqa: ANN001
    raw = _admin_key()
    created = client.post(
        "/api/v1/users",
        json={"username": "client1", "password": "client-pass"},
        headers=_headers(raw),
    ).json()
    target = (datetime.now(timezone.utc) + timedelta(days=10)).isoformat()
    renewed = client.post(
        f"/api/v1/users/{created['id']}/renew",
        json={"expires_at": target, "reset_traffic": True},
        headers=_headers(raw),
    )
    assert renewed.status_code == 200


def test_usage_endpoint(client) -> None:  # noqa: ANN001
    raw = _admin_key()
    created = client.post(
        "/api/v1/users",
        json={"username": "client1", "password": "client-pass"},
        headers=_headers(raw),
    ).json()
    usage = client.get(f"/api/v1/users/{created['id']}/usage", headers=_headers(raw))
    assert usage.status_code == 200
    assert usage.json()["bytes_in"] == 0


def test_reseller_scoped_stats(client) -> None:  # noqa: ANN001
    from app.services import api_keys as api_keys_service
    from app.services import users as users_service

    db = get_session_factory()()
    try:
        reseller = users_service.create_user(
            db, username="reseller", password="reseller-pass", role=Role.RESELLER
        )
        _, raw = api_keys_service.create_key(db, reseller, name="test")
        db.commit()
    finally:
        db.close()
    stats = client.get("/api/v1/stats", headers=_headers(raw))
    assert stats.status_code == 200
