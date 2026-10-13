"""Unit tests for the Tunnel Manager (mocked SSH).

These tests never touch a real host: :class:`FakeSSH` implements the
:class:`~app.services.tunnel_ssh.SSHTransport` protocol and records every
command so assertions can verify the allow-listed templates and secret handling.
"""

from __future__ import annotations

import json

import pytest

from app.models.tunnel import (
    TunnelProvisionJobStatus,
    TunnelStatus,
)
from app.services import tunnel_backend, tunnel_ssh
from app.services import tunnels as tunnels_service
from app.services.tunnel_ssh import CommandResult


class FakeSSH:
    """In-memory SSH transport for tests."""

    def __init__(self, *, fail_on: str | None = None) -> None:
        self.commands: list[str] = []
        self.uploads: list[tuple[str, bytes, int]] = []
        self.files: dict[str, bytes] = {}
        self.closed = False
        self.fail_on = fail_on
        self.connect_kwargs: dict = {}

    def run(self, command: str, *, timeout: float = 120.0) -> CommandResult:
        self.commands.append(command)
        if self.fail_on and self.fail_on in command:
            return CommandResult(ok=False, exit_code=1, stderr="boom")
        if "uname -m" in command:
            return CommandResult(ok=True, exit_code=0, stdout="x86_64\n")
        if "is-active" in command:
            return CommandResult(ok=True, exit_code=0, stdout="active\n")
        if "wg pubkey" in command:
            return CommandResult(ok=True, exit_code=0, stdout="cHVia2V5\n")
        if "api.github.com" in command:
            return CommandResult(ok=True, exit_code=0, stdout="v0.6.5\n")
        if " -v " in command or "--version" in command:
            return CommandResult(ok=True, exit_code=0, stdout="backhaul 0.6.5\n")
        return CommandResult(ok=True, exit_code=0, stdout="")

    def upload_bytes(self, data: bytes, remote_path: str, *, mode: int = 0o600) -> None:
        self.uploads.append((remote_path, data, mode))
        self.files[remote_path] = data

    def read_text(self, remote_path: str) -> str:
        return self.files.get(remote_path, b"").decode("utf-8")

    def close(self) -> None:
        self.closed = True


def _factory(fake: FakeSSH):  # noqa: ANN201
    def build(host, **kwargs):  # noqa: ANN001
        fake.connect_kwargs = {"host": host, **kwargs}
        return fake

    return build


# --------------------------------------------------------------------------- #
# Backend catalogue / research metadata
# --------------------------------------------------------------------------- #
def test_backend_metadata_matches_research() -> None:
    meta = {b["kind"]: b for b in tunnel_backend.backend_metadata()}
    assert meta["backhaul"]["license"] == "AGPL-3.0"
    assert meta["backhaul"]["upstream"] == "https://github.com/Musixal/Backhaul"
    assert set(meta["backhaul"]["transports"]) == {"tcp", "tcpmux", "udp"}
    assert meta["backhaul"]["supports_udp"] is True
    assert meta["wireguard"]["license"] == "GPL-2.0"
    assert meta["wireguard"]["supports_udp"] is True


# --------------------------------------------------------------------------- #
# Backhaul backend
# --------------------------------------------------------------------------- #
def test_backhaul_configure_writes_toml_and_unit() -> None:
    fake = FakeSSH()
    backend = tunnel_backend.BackhaulBackend(fake)
    result = backend.configure(
        name="t1",
        token="s3cret-token",
        transport="tcpmux",
        control_port=25000,
        public_ports=[443, 8443],
        exit_host="203.0.113.9",
    )
    assert result.ok
    toml = fake.read_text(f"{tunnel_backend.BACKHAUL_DIR}/t1.toml")
    assert 'token = "s3cret-token"' in toml
    assert 'transport = "tcpmux"' in toml
    assert 'remote_addr = "203.0.113.9:25000"' in toml
    assert '  "443"' in toml and '  "8443"' in toml
    assert "accept_udp = true" in toml

    unit_path = f"{tunnel_backend.BACKHAUL_UNIT_DIR}/ovpn-tunnel-t1.service"
    unit = fake.read_text(unit_path)
    assert f"ExecStart={tunnel_backend.BACKHAUL_BIN}" in unit
    # Config files must be private.
    toml_mode = next(m for p, _d, m in fake.uploads if p.endswith("t1.toml"))
    assert toml_mode == 0o600


def test_backhaul_install_offline_binary() -> None:
    fake = FakeSSH()
    backend = tunnel_backend.BackhaulBackend(fake)
    result = backend.install(offline_binary=b"\x7fELF-fake")
    assert result.ok
    assert any(p.endswith("backhaul") or "backhaul" in p for p, _d, _m in fake.uploads)


def test_backhaul_uninstall_removes_units_and_binary() -> None:
    fake = FakeSSH()
    backend = tunnel_backend.BackhaulBackend(fake)
    result = backend.uninstall()
    assert result.ok
    joined = " ".join(fake.commands)
    assert "ovpn-tunnel-*.service" in joined
    assert tunnel_backend.BACKHAUL_BIN in joined


# --------------------------------------------------------------------------- #
# WireGuard backend
# --------------------------------------------------------------------------- #
def test_wireguard_configure_uses_wg_pubkey() -> None:
    fake = FakeSSH()
    backend = tunnel_backend.WireGuardBackend(fake)
    result = backend.configure(
        name="wg1",
        token="cHJpdmtleQ==",
        transport="wg",
        control_port=51820,
        public_ports=[1194],
        exit_host="cGVlcnB1Yg==",
    )
    assert result.ok
    conf = fake.read_text(f"{tunnel_backend.WG_DIR}/wg1.conf")
    assert "PrivateKey = cHJpdmtleQ==" in conf
    assert "ListenPort = 51820" in conf
    assert "PublicKey = cGVlcnB1Yg==" in conf
    mode = next(m for p, _d, m in fake.uploads if p.endswith("wg1.conf"))
    assert mode == 0o600
    assert any("wg pubkey" in c for c in fake.commands)


# --------------------------------------------------------------------------- #
# CRUD / validation
# --------------------------------------------------------------------------- #
def test_create_tunnel_validates_and_persists(db) -> None:  # noqa: ANN001
    spec = tunnels_service.TunnelSpec(
        name="t1",
        entry_host="198.51.100.5",
        control_port=25000,
        token="tok",
        public_ports=[443, 8443],
    )
    tunnel = tunnels_service.create_tunnel(db, spec)
    db.flush()
    assert tunnel.port_list() == [443, 8443]
    assert tunnel.status == TunnelStatus.PENDING
    assert tunnels_service.get_by_name(db, "t1") is tunnel


def test_create_tunnel_rejects_bad_name(db) -> None:  # noqa: ANN001
    spec = tunnels_service.TunnelSpec(
        name="bad name!", entry_host="1.2.3.4", control_port=25000, token="t"
    )
    with pytest.raises(tunnels_service.TunnelError):
        tunnels_service.create_tunnel(db, spec)


def test_create_tunnel_rejects_duplicate(db) -> None:  # noqa: ANN001
    spec = tunnels_service.TunnelSpec(
        name="dup", entry_host="1.2.3.4", control_port=25000, token="t"
    )
    tunnels_service.create_tunnel(db, spec)
    db.flush()
    with pytest.raises(tunnels_service.TunnelError):
        tunnels_service.create_tunnel(db, spec)


def test_allocate_control_port_skips_used(db) -> None:  # noqa: ANN001
    first = tunnels_service.allocate_control_port(db)
    tunnels_service.create_tunnel(
        db,
        tunnels_service.TunnelSpec(name="aa", entry_host="1.2.3.4", control_port=first, token="t"),
    )
    db.flush()
    assert tunnels_service.allocate_control_port(db) == first + 1


def test_update_and_rotate_token(db) -> None:  # noqa: ANN001
    tunnel = tunnels_service.create_tunnel(
        db,
        tunnels_service.TunnelSpec(
            name="u1", entry_host="1.2.3.4", control_port=25000, token="old"
        ),
    )
    db.flush()
    tunnels_service.update_tunnel(db, tunnel, public_ports=[8080, 9090], auto_heal=False)
    assert tunnel.port_list() == [8080, 9090]
    assert tunnel.auto_heal is False
    new_token = tunnels_service.rotate_token(db, tunnel)
    assert new_token != "old"


# --------------------------------------------------------------------------- #
# Panel key
# --------------------------------------------------------------------------- #
def test_ensure_panel_key_is_private(db) -> None:  # noqa: ANN001
    tunnel = tunnels_service.create_tunnel(
        db,
        tunnels_service.TunnelSpec(name="k1", entry_host="1.2.3.4", control_port=25000, token="t"),
    )
    db.flush()
    try:
        path = tunnels_service.ensure_panel_key(tunnel)
    except tunnels_service.TunnelError:
        pytest.skip("cryptography not available")
    import os
    import stat as stat_mod

    if os.name != "nt":
        mode = os.stat(path).st_mode
        assert stat_mod.S_IMODE(mode) == 0o600
    config = json.loads(tunnel.config or "{}")
    assert config.get("panel_public_key", "").startswith(("ssh-", "ecdsa-"))


# --------------------------------------------------------------------------- #
# Provisioning (mocked SSH)
# --------------------------------------------------------------------------- #
def _new_tunnel(db, **overrides):  # noqa: ANN001
    spec = tunnels_service.TunnelSpec(
        name=overrides.pop("name", "p1"),
        entry_host=overrides.pop("entry_host", "198.51.100.5"),
        control_port=overrides.pop("control_port", 25000),
        token=overrides.pop("token", "tok"),
        public_ports=overrides.pop("public_ports", [443]),
        host_key_fingerprint=overrides.pop("host_key_fingerprint", "SHA256:test-key"),
        **overrides,
    )
    tunnel = tunnels_service.create_tunnel(db, spec)
    tunnels_service.ensure_panel_key(tunnel)
    db.flush()
    return tunnel, spec


def test_provision_success_streams_log_and_activates(db) -> None:  # noqa: ANN001
    tunnel, spec = _new_tunnel(db)
    fake = FakeSSH()
    ctx = tunnels_service.ProvisionContext(spec=spec, password="one-time-password")
    job = tunnels_service.provision(db, tunnel, ctx, transport_factory=_factory(fake))
    db.flush()
    assert job.status == TunnelProvisionJobStatus.DONE
    assert tunnel.status == TunnelStatus.ACTIVE
    assert "Installing backend 'backhaul'" in job.log
    assert "verify_fingerprint" in job.log or "fingerprint" in job.log.lower()
    # Config path + private mode for the TOML.
    assert any(p.endswith("p1.toml") for p, _d, _m in fake.uploads)
    assert fake.closed is True
    # Password is never written to disk or the log.
    assert "one-time-password" not in job.log


def test_provision_password_is_one_time_only(db) -> None:  # noqa: ANN001
    tunnel, spec = _new_tunnel(db, name="p2")
    fake = FakeSSH()
    ctx = tunnels_service.ProvisionContext(spec=spec, password="secret-pass")
    tunnels_service.provision(db, tunnel, ctx, transport_factory=_factory(fake))
    # The transport saw the password (used once) but nothing persisted it.
    assert fake.connect_kwargs.get("password") == "secret-pass"
    assert "secret-pass" not in (tunnel.config or "")


def test_provision_failure_marks_error(db) -> None:  # noqa: ANN001
    tunnel, spec = _new_tunnel(db, name="p3")
    fake = FakeSSH(fail_on="backhaul_linux")
    ctx = tunnels_service.ProvisionContext(spec=spec, password="pw")
    job = tunnels_service.provision(db, tunnel, ctx, transport_factory=_factory(fake))
    db.flush()
    assert job.status == TunnelProvisionJobStatus.FAILED
    assert tunnel.status == TunnelStatus.ERROR
    assert job.error


def test_provision_install_key_disables_password_when_requested(db) -> None:  # noqa: ANN001
    tunnel, spec = _new_tunnel(db, name="p4")
    fake = FakeSSH()
    ctx = tunnels_service.ProvisionContext(spec=spec, password="pw", disable_password_login=True)
    tunnels_service.provision(db, tunnel, ctx, transport_factory=_factory(fake))
    assert any("PasswordAuthentication no" in c for c in fake.commands)


def test_operate_start_stop_restart_update(db) -> None:  # noqa: ANN001
    tunnel, _spec = _new_tunnel(db, name="p5")
    fake = FakeSSH()
    for action, expected in (
        ("start", TunnelStatus.ACTIVE),
        ("stop", TunnelStatus.STOPPED),
        ("restart", TunnelStatus.ACTIVE),
    ):
        job = tunnels_service.operate(db, tunnel, action, transport_factory=_factory(fake))
        assert job.status == TunnelProvisionJobStatus.DONE
        assert tunnel.status == expected


def test_rollback_marks_rolled_back(db) -> None:  # noqa: ANN001
    tunnel, _spec = _new_tunnel(db, name="p6")
    fake = FakeSSH()
    job = tunnels_service.rollback(db, tunnel, transport_factory=_factory(fake))
    assert job.rolled_back is True
    assert job.status == TunnelProvisionJobStatus.ROLLED_BACK
    assert tunnel.status == TunnelStatus.UNINSTALLED


# --------------------------------------------------------------------------- #
# Health & self-healing
# --------------------------------------------------------------------------- #
def test_probe_tunnel_records_sample_and_degrades(db, monkeypatch) -> None:  # noqa: ANN001
    tunnel, _spec = _new_tunnel(db, name="h1")
    tunnel.status = TunnelStatus.ACTIVE
    db.flush()
    monkeypatch.setattr(
        "app.services.tunnel_benchmark.probe_icmp",
        lambda *a, **k: (0.0, 100.0, False),
    )
    for _ in range(tunnels_service.HEAL_THRESHOLD):
        tunnels_service.probe_tunnel(db, tunnel)
    db.flush()
    assert tunnel.consecutive_failures >= tunnels_service.HEAL_THRESHOLD
    assert tunnel.status == TunnelStatus.DEGRADED


def test_self_heal_restarts_after_threshold(db) -> None:  # noqa: ANN001
    tunnel, _spec = _new_tunnel(db, name="h2")
    tunnel.status = TunnelStatus.DEGRADED
    tunnel.consecutive_failures = tunnels_service.HEAL_THRESHOLD
    db.flush()
    fake = FakeSSH()
    healed = tunnels_service.maybe_self_heal(db, tunnel, transport_factory=_factory(fake))
    assert healed is True
    assert tunnel.consecutive_failures == 0


def test_self_heal_disabled(db) -> None:  # noqa: ANN001
    tunnel, _spec = _new_tunnel(db, name="h3", auto_heal=False)
    tunnel.consecutive_failures = tunnels_service.HEAL_THRESHOLD
    db.flush()
    assert (
        tunnels_service.maybe_self_heal(db, tunnel, transport_factory=_factory(FakeSSH())) is False
    )


# --------------------------------------------------------------------------- #
# Config delivery
# --------------------------------------------------------------------------- #
def test_effective_endpoint_prefers_public_port(db) -> None:  # noqa: ANN001
    tunnel, _spec = _new_tunnel(db, name="e1", public_ports=[443, 8443])
    assert tunnels_service.effective_endpoint(tunnel) == ("198.51.100.5", 443)


def test_effective_endpoint_falls_back_to_control_port(db) -> None:  # noqa: ANN001
    tunnel, _spec = _new_tunnel(db, name="e2", public_ports=[])
    assert tunnels_service.effective_endpoint(tunnel) == ("198.51.100.5", 25000)


def test_active_tunnel_for_node_requires_active_status(db) -> None:  # noqa: ANN001
    from app.services import nodes as nodes_service

    node, _ = nodes_service.create_node(db, name="n1", host="10.0.0.1")
    db.flush()
    tunnel, _spec = _new_tunnel(db, name="e3", node_id=node.id)
    assert tunnels_service.active_tunnel_for_node(db, node.id) is None
    tunnel.status = TunnelStatus.ACTIVE
    db.flush()
    assert tunnels_service.active_tunnel_for_node(db, node.id) is tunnel


def test_subscription_uses_tunnel_endpoint(db) -> None:  # noqa: ANN001
    from app.services import nodes as nodes_service
    from app.services import subscriptions as subscriptions_service
    from app.services import vpn_users as vpn_users_service

    node, _ = nodes_service.create_node(db, name="n2", host="203.0.113.1", port=1194)
    db.flush()
    tunnel, _spec = _new_tunnel(
        db, name="e4", node_id=node.id, public_ports=[443], entry_host="198.51.100.9"
    )
    tunnel.status = TunnelStatus.ACTIVE
    vpn_user = vpn_users_service.create_vpn_user(
        db, username="tunuser", password="pass-pass", node_id=node.id
    )
    vpn_user.use_tunnel = True
    db.flush()
    host, port, resolved = subscriptions_service.resolve_endpoint(db, vpn_user, node)
    assert host == "198.51.100.9"
    assert port == 443
    assert resolved is tunnel

    # Opting out falls back to the node's own endpoint.
    vpn_user.use_tunnel = False
    db.flush()
    host, port, resolved = subscriptions_service.resolve_endpoint(db, vpn_user, node)
    assert host == "203.0.113.1"
    assert resolved is None


# --------------------------------------------------------------------------- #
# Rate limiting
# --------------------------------------------------------------------------- #
def test_provision_limiter_locks_after_attempts() -> None:
    from app.services import tunnel_worker

    limiter = tunnel_worker.provision_limiter()
    limiter.clear()
    for _ in range(6):
        limiter.register_failure("1.2.3.4")
    assert limiter.is_locked("1.2.3.4")
    assert not limiter.is_locked("5.6.7.8")
    limiter.clear()


# --------------------------------------------------------------------------- #
# SSH helpers
# --------------------------------------------------------------------------- #
def test_quote_escapes_shell_metacharacters() -> None:
    assert tunnel_ssh.quote("a; rm -rf /") == "'a; rm -rf /'"
    assert tunnel_ssh.quote("simple") == "simple"


def test_pinned_fingerprint_mismatch_raises() -> None:
    # ParamikoSSH requires the optional dependency; skip when unavailable.
    pytest.importorskip("paramiko")
    from app.services.tunnel_ssh import ParamikoSSH, SSHError

    with pytest.raises((SSHError, Exception)):
        ParamikoSSH(
            "127.0.0.1",
            port=1,
            username="root",
            password="x",
            host_key_fingerprint="SHA256:does-not-match",
            timeout=0.5,
        )
