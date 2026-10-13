"""Tests for the OpenVPN hook endpoints."""

from __future__ import annotations

from app.db import get_session_factory
from app.services import vpn_users as vpn_users_service

TOKEN = "test-hook-token"


def _mk_user(username: str = "client1", password: str = "client-pass") -> int:
    db = get_session_factory()()
    try:
        user = vpn_users_service.create_vpn_user(db, username=username, password=password)
        db.commit()
        return user.id
    finally:
        db.close()


def test_hook_requires_token(client) -> None:  # noqa: ANN001
    response = client.post("/hooks/auth", json={"username": "client1", "password": "x"})
    assert response.status_code == 401


def test_hook_rejects_wrong_token(client) -> None:  # noqa: ANN001
    response = client.post(
        "/hooks/auth",
        json={"username": "client1", "password": "x"},
        headers={"X-Hook-Token": "nope"},
    )
    assert response.status_code == 401


def test_hook_auth_accepts_valid_credentials(client) -> None:  # noqa: ANN001
    _mk_user()
    response = client.post(
        "/hooks/auth",
        json={"username": "client1", "password": "client-pass"},
        headers={"X-Hook-Token": TOKEN},
    )
    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_hook_auth_rejects_bad_credentials(client) -> None:  # noqa: ANN001
    _mk_user()
    response = client.post(
        "/hooks/auth",
        json={"username": "client1", "password": "wrong"},
        headers={"X-Hook-Token": TOKEN},
    )
    assert response.status_code == 403


def test_hook_auth_rejects_unknown_user(client) -> None:  # noqa: ANN001
    response = client.post(
        "/hooks/auth",
        json={"username": "ghost", "password": "x"},
        headers={"X-Hook-Token": TOKEN},
    )
    assert response.status_code == 403


def test_hook_connect_and_disconnect(client) -> None:  # noqa: ANN001
    user_id = _mk_user()
    connect = client.post(
        "/hooks/connect",
        json={"common_name": "client1", "trusted_ip": "1.2.3.4"},
        headers={"X-Hook-Token": TOKEN},
    )
    assert connect.status_code == 200

    db = get_session_factory()()
    try:
        assert vpn_users_service.get(db, user_id).is_online is True
    finally:
        db.close()

    disconnect = client.post(
        "/hooks/disconnect",
        json={"common_name": "client1", "bytes_received": 100, "bytes_sent": 200},
        headers={"X-Hook-Token": TOKEN},
    )
    assert disconnect.status_code == 200

    db = get_session_factory()()
    try:
        user = vpn_users_service.get(db, user_id)
        assert user.is_online is False
        assert user.bytes_in == 100
        assert user.bytes_out == 200
    finally:
        db.close()


def test_hook_rejects_invalid_username(client) -> None:  # noqa: ANN001
    response = client.post(
        "/hooks/auth",
        json={"username": "bad name;rm", "password": "x"},
        headers={"X-Hook-Token": TOKEN},
    )
    assert response.status_code == 422  # rejected by validation


def test_connect_opens_session_and_disconnect_closes_it(client) -> None:  # noqa: ANN001
    from sqlalchemy import select

    from app.models.traffic_log import TrafficLog
    from app.models.vpn_session import VpnSession

    _mk_user()
    client.post(
        "/hooks/connect",
        json={"common_name": "client1", "trusted_ip": "1.2.3.4"},
        headers={"X-Hook-Token": TOKEN},
    )
    db = get_session_factory()()
    try:
        sessions = list(db.scalars(select(VpnSession).where(VpnSession.common_name == "client1")))
        assert len(sessions) == 1
        assert sessions[0].is_active is True
        assert sessions[0].trusted_ip == "1.2.3.4"
    finally:
        db.close()

    client.post(
        "/hooks/disconnect",
        json={"common_name": "client1", "bytes_received": 10, "bytes_sent": 20},
        headers={"X-Hook-Token": TOKEN},
    )
    db = get_session_factory()()
    try:
        session = db.scalar(select(VpnSession).where(VpnSession.common_name == "client1"))
        assert session.is_active is False
        assert session.bytes_in == 10 and session.bytes_out == 20
        logs = list(db.scalars(select(TrafficLog)))
        assert len(logs) == 1
        assert logs[0].bytes_in == 10 and logs[0].bytes_out == 20
    finally:
        db.close()


def test_hook_auth_enforces_concurrent_limit(client, monkeypatch) -> None:  # noqa: ANN001
    from app.config import get_settings
    from app.models.base import utcnow
    from app.models.vpn_session import VpnSession

    user_id = _mk_user()
    settings = get_settings()
    monkeypatch.setattr(settings, "max_concurrent_connections", 1)

    db = get_session_factory()()
    try:
        db.add(
            VpnSession(
                vpn_user_id=user_id,
                common_name="client1",
                is_active=True,
                connected_at=utcnow(),
            )
        )
        db.commit()
    finally:
        db.close()

    response = client.post(
        "/hooks/auth",
        json={"username": "client1", "password": "client-pass"},
        headers={"X-Hook-Token": TOKEN},
    )
    assert response.status_code == 403
    assert "concurrent" in response.json()["detail"]
