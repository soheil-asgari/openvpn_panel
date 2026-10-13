"""Tests for the REST API (API-key authenticated)."""

from __future__ import annotations

from app.db import get_session_factory
from app.models.user import Role


def _make_admin_with_key() -> str:
    from app.services import api_keys as api_keys_service
    from app.services import users as users_service

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


def _make_reseller_with_key() -> str:
    from app.services import api_keys as api_keys_service
    from app.services import users as users_service

    db = get_session_factory()()
    try:
        reseller = users_service.create_user(
            db, username="reseller", password="reseller-password", role=Role.RESELLER
        )
        _, raw = api_keys_service.create_key(db, reseller, name="test")
        db.commit()
        return raw
    finally:
        db.close()


def _headers(raw: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {raw}"}


def test_api_requires_key(client) -> None:  # noqa: ANN001
    assert client.get("/api/v1/users").status_code == 401


def test_api_rejects_bad_key(client) -> None:  # noqa: ANN001
    assert client.get("/api/v1/users", headers=_headers("ovpn_bad_nope")).status_code == 401


def test_api_x_api_key_header(client) -> None:  # noqa: ANN001
    raw = _make_admin_with_key()
    response = client.get("/api/v1/users", headers={"X-API-Key": raw})
    assert response.status_code == 200


def test_api_create_and_list_users(client) -> None:  # noqa: ANN001
    raw = _make_admin_with_key()
    create = client.post(
        "/api/v1/users",
        json={"username": "client1", "password": "client-pass"},
        headers=_headers(raw),
    )
    assert create.status_code == 201
    assert create.json()["username"] == "client1"

    listing = client.get("/api/v1/users", headers=_headers(raw))
    assert [u["username"] for u in listing.json()] == ["client1"]


def test_api_create_user_rejects_bad_username(client) -> None:  # noqa: ANN001
    raw = _make_admin_with_key()
    response = client.post(
        "/api/v1/users",
        json={"username": "bad name", "password": "client-pass"},
        headers=_headers(raw),
    )
    assert response.status_code == 422


def test_api_reseller_scope_isolation(client) -> None:  # noqa: ANN001
    admin_key = _make_admin_with_key()
    client.post(
        "/api/v1/users",
        json={"username": "adminclient", "password": "client-pass"},
        headers=_headers(admin_key),
    )
    reseller_key = _make_reseller_with_key()
    listing = client.get("/api/v1/users", headers=_headers(reseller_key))
    assert listing.status_code == 200
    assert listing.json() == []


def test_api_reseller_cannot_create_nodes(client) -> None:  # noqa: ANN001
    raw = _make_reseller_with_key()
    response = client.post(
        "/api/v1/nodes",
        json={"name": "de1", "host": "10.0.0.1"},
        headers=_headers(raw),
    )
    assert response.status_code == 403


def test_api_create_node_returns_agent_token(client) -> None:  # noqa: ANN001
    raw = _make_admin_with_key()
    response = client.post(
        "/api/v1/nodes",
        json={"name": "de1", "host": "vpn.example.com", "agent_url": "http://127.0.0.1:8090"},
        headers=_headers(raw),
    )
    assert response.status_code == 201
    assert response.json()["agent_token"]


def test_api_update_and_delete_user(client) -> None:  # noqa: ANN001
    raw = _make_admin_with_key()
    created = client.post(
        "/api/v1/users",
        json={"username": "client1", "password": "client-pass"},
        headers=_headers(raw),
    ).json()
    user_id = created["id"]

    patch = client.patch(
        f"/api/v1/users/{user_id}", json={"status": "disabled"}, headers=_headers(raw)
    )
    assert patch.status_code == 200
    assert patch.json()["status"] == "disabled"

    delete = client.delete(f"/api/v1/users/{user_id}", headers=_headers(raw))
    assert delete.status_code == 200


def test_api_unknown_user_returns_404(client) -> None:  # noqa: ANN001
    raw = _make_admin_with_key()
    assert client.get("/api/v1/users/999", headers=_headers(raw)).status_code == 404


def test_api_create_reseller(client) -> None:  # noqa: ANN001
    raw = _make_admin_with_key()
    response = client.post(
        "/api/v1/resellers",
        json={"username": "res1", "password": "reseller-pass", "quota_users": 5},
        headers=_headers(raw),
    )
    assert response.status_code == 201
    assert response.json()["role"].lower() == "reseller"


def test_api_keys_self_service(client) -> None:  # noqa: ANN001
    raw = _make_admin_with_key()
    created = client.post("/api/v1/keys", params={"name": "ci"}, headers=_headers(raw))
    assert created.status_code == 201
    assert created.json()["key"].startswith("ovpn_")

    listing = client.get("/api/v1/keys", headers=_headers(raw))
    assert len(listing.json()) == 2
