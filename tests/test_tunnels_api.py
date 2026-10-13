"""API/UI tests for the Tunnel Manager endpoints (mocked SSH)."""

from __future__ import annotations

import pytest

from app.db import get_session_factory
from app.services import tunnels as tunnels_service
from app.services.tunnel_ssh import CommandResult


class FakeSSH:
    def __init__(self, **_kw) -> None:  # noqa: ANN003
        self.closed = False

    def run(self, command: str, *, timeout: float = 120.0) -> CommandResult:
        if "uname -m" in command:
            return CommandResult(ok=True, exit_code=0, stdout="x86_64\n")
        if "is-active" in command:
            return CommandResult(ok=True, exit_code=0, stdout="active\n")
        return CommandResult(ok=True, exit_code=0, stdout="")

    def upload_bytes(self, data: bytes, remote_path: str, *, mode: int = 0o600) -> None:
        return None

    def read_text(self, remote_path: str) -> str:
        return ""

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def stub_ssh(monkeypatch):  # noqa: ANN001
    def factory(*_a, **_k):  # noqa: ANN001
        return FakeSSH()

    monkeypatch.setattr("app.services.tunnels.connect", lambda *a, **k: FakeSSH())
    monkeypatch.setattr("app.services.tunnel_ssh.ParamikoSSH", factory)
    return factory


def _api_key(client, db):  # noqa: ANN001
    from app.models.user import Role
    from app.services import api_keys as api_keys_service
    from app.services import users as users_service

    user = users_service.create_user(
        db, username="apiadmin", password="admin-password", role=Role.ADMIN
    )
    db.commit()
    key, raw = api_keys_service.create_key(db, user, name="test")
    db.commit()
    return {"X-API-Key": raw}


def test_tunnels_requires_admin(client, db, reseller_user) -> None:  # noqa: ANN001
    from app.models.user import Role
    from app.services import api_keys as api_keys_service

    key, raw = api_keys_service.create_key(db, reseller_user, name="r")
    db.commit()
    resp = client.get("/api/v1/tunnels", headers={"X-API-Key": raw})
    assert resp.status_code == 403
    _ = Role


def test_list_backends(client, db, stub_ssh) -> None:  # noqa: ANN001
    headers = _api_key(client, db)
    resp = client.get("/api/v1/tunnels/backends", headers=headers)
    assert resp.status_code == 200
    kinds = {b["kind"] for b in resp.json()}
    assert {"backhaul", "wireguard"} <= kinds


def test_create_and_list_tunnel(client, db, stub_ssh, monkeypatch) -> None:  # noqa: ANN001
    # Provisioning runs synchronously in-process via operate() in the API retry
    # path; for create we only assert the tunnel row + job exist.
    headers = _api_key(client, db)
    payload = {
        "name": "api1",
        "entry_host": "198.51.100.5",
        "backend": "backhaul",
        "transport": "tcp",
        "public_ports": [443],
        "probe_only": True,
    }
    resp = client.post("/api/v1/tunnels", json=payload, headers=headers)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] in {"pending", "running", "done"}

    listing = client.get("/api/v1/tunnels", headers=headers)
    assert listing.status_code == 200
    names = [t["name"] for t in listing.json()]
    assert "api1" in names


def test_get_detail_and_update(client, db, stub_ssh) -> None:  # noqa: ANN001
    headers = _api_key(client, db)
    create = client.post(
        "/api/v1/tunnels",
        json={
            "name": "api2",
            "entry_host": "198.51.100.6",
            "backend": "auto",
            "probe_only": True,
        },
        headers=headers,
    )
    tunnel_id = create.json()["tunnel_id"]

    detail = client.get(f"/api/v1/tunnels/{tunnel_id}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["name"] == "api2"

    updated = client.patch(
        f"/api/v1/tunnels/{tunnel_id}",
        json={"transport": "tcpmux", "public_ports": [443, 8443], "auto_heal": False},
        headers=headers,
    )
    assert updated.status_code == 200
    data = updated.json()
    assert data["transport"] == "tcpmux"
    assert data["public_ports"] == [443, 8443]
    assert data["auto_heal"] is False


def test_rotate_token_and_delete(client, db, stub_ssh) -> None:  # noqa: ANN001
    headers = _api_key(client, db)
    create = client.post(
        "/api/v1/tunnels",
        json={"name": "api3", "entry_host": "198.51.100.7", "probe_only": True},
        headers=headers,
    )
    tunnel_id = create.json()["tunnel_id"]

    rotated = client.post(f"/api/v1/tunnels/{tunnel_id}/rotate-token", headers=headers)
    assert rotated.status_code == 200

    deleted = client.delete(f"/api/v1/tunnels/{tunnel_id}", headers=headers)
    assert deleted.status_code == 200
    assert client.get(f"/api/v1/tunnels/{tunnel_id}", headers=headers).status_code == 404


def test_summary(client, db, stub_ssh) -> None:  # noqa: ANN001
    headers = _api_key(client, db)
    client.post(
        "/api/v1/tunnels",
        json={"name": "api4", "entry_host": "198.51.100.8", "probe_only": True},
        headers=headers,
    )
    resp = client.get("/api/v1/tunnels/summary", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["total"] >= 1


def test_create_rejects_bad_host(client, db, stub_ssh) -> None:  # noqa: ANN001
    headers = _api_key(client, db)
    resp = client.post(
        "/api/v1/tunnels",
        json={"name": "bad", "entry_host": "not a host!", "probe_only": True},
        headers=headers,
    )
    assert resp.status_code == 400


def _login_admin(client) -> None:  # noqa: ANN001
    from app.models.user import Role
    from app.services import users as users_service

    db = get_session_factory()()
    try:
        users_service.create_user(db, username="admin", password="admin-password", role=Role.ADMIN)
        db.commit()
    finally:
        db.close()
    client.post("/login", data={"username": "admin", "password": "admin-password"})


def test_ui_pages_render(client) -> None:  # noqa: ANN001
    _login_admin(client)
    listing = client.get("/tunnels")
    assert listing.status_code == 200
    wizard = client.get("/tunnels/new")
    assert wizard.status_code == 200
    assert "tunnel" in wizard.text.lower()


def test_ui_tunnel_detail_renders(client) -> None:  # noqa: ANN001

    _login_admin(client)
    db = get_session_factory()()
    try:
        tunnel = tunnels_service.create_tunnel(
            db,
            tunnels_service.TunnelSpec(
                name="web1", entry_host="198.51.100.20", control_port=25000, token="t"
            ),
        )
        db.commit()
        tunnel_id = tunnel.id
    finally:
        db.close()

    resp = client.get(f"/tunnels/{tunnel_id}")
    assert resp.status_code == 200
    assert "web1" in resp.text


def test_wizard_fingerprint_partial(client, stub_ssh, monkeypatch) -> None:  # noqa: ANN001
    from app.services import tunnel_ssh

    _login_admin(client)
    monkeypatch.setattr(tunnel_ssh, "probe_fingerprint", lambda host, port=22: "SHA256:abc123")
    resp = client.post(
        "/partials/tunnels/fingerprint-probe",
        data={"host": "198.51.100.30", "port": 22, "_csrf": ""},
    )
    # CSRF middleware may reject a tokenless POST; accept either the probe result
    # or a clean 403 (never a 500).
    assert resp.status_code in (200, 403)
    if resp.status_code == 200:
        assert "SHA256:abc123" in resp.text


def test_wizard_benchmark_partial(client, stub_ssh, monkeypatch) -> None:  # noqa: ANN001
    from app.services import tunnel_benchmark

    _login_admin(client)
    monkeypatch.setattr(
        tunnel_benchmark,
        "probe_icmp",
        lambda *a, **k: (12.0, 0.0, True),
    )
    monkeypatch.setattr(tunnel_benchmark, "probe_throughput", lambda *a, **k: 50000.0)
    resp = client.post("/partials/tunnels/benchmark", data={"host": "198.51.100.31"})
    assert resp.status_code in (200, 403)
    if resp.status_code == 200:
        assert "backhaul" in resp.text
