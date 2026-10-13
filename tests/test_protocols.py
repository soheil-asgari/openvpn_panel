"""Tests for the protocol abstraction and backends (Phases 12-15)."""

from __future__ import annotations

import base64
import json

import pytest

from app.services.protocols.base import ExecResult, UserSpec
from app.services.protocols.l2tp import L2tpBackend
from app.services.protocols.ocserv import OcservBackend
from app.services.protocols.openvpn import OpenVpnBackend
from app.services.protocols.singbox import (
    SingBoxBackend,
    build_share_uri,
    generate_reality_keypair,
)
from app.services.protocols.wireguard import (
    WireGuardBackend,
    generate_private_key,
    public_from_private,
    subnet_hosts,
)


class FakeExecutor:
    """In-memory executor recording commands and files (no OS access)."""

    def __init__(self, *, run_ok: bool = True) -> None:
        self.commands: list[list[str]] = []
        self.files: dict[str, str] = {}
        self.run_ok = run_ok

    def run(self, argv: list[str], *, timeout: float = 60.0) -> ExecResult:
        self.commands.append(list(argv))
        return ExecResult(ok=self.run_ok, stdout="", stderr="" if self.run_ok else "boom")

    def write_file(self, path: str, content: str, *, mode: int = 0o600) -> None:
        self.files[path] = content

    def read_file(self, path: str) -> str:
        if path not in self.files:
            from app.services.protocols.base import ProtocolError

            raise ProtocolError(f"missing: {path}")
        return self.files[path]

    def upload(self, data: bytes, path: str, *, mode: int = 0o600) -> None:
        self.files[path] = data.decode("utf-8", errors="replace")

    def service(self, unit: str, verb: str, *, timeout: float = 60.0) -> ExecResult:
        return self.run(["/usr/bin/systemctl", verb, unit], timeout=timeout)


class FakeInstance:
    """Minimal stand-in for a ProtocolInstance row."""

    def __init__(self, **kwargs: object) -> None:
        self.name = kwargs.get("name", "wg0")
        self.kind = kwargs.get("kind", "wireguard")
        self.port = kwargs.get("port", 51820)
        self.subnet = kwargs.get("subnet", "10.66.0.0/24")
        self.address = kwargs.get("address", "10.66.0.1/24")
        self.dns = kwargs.get("dns", "")
        self.mtu = kwargs.get("mtu", 1420)
        self.host = kwargs.get("host", "vpn.example.com")
        self.node_id = kwargs.get("node_id")
        self.settings = kwargs.get("settings", {})


# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #
def test_backend_registry_catalogue() -> None:
    from app.services import protocols

    kinds = {b["kind"] for b in protocols.backend_metadata()}
    assert {"openvpn", "wireguard", "ocserv", "l2tp", "singbox"} <= kinds
    for entry in protocols.backend_metadata():
        assert entry["license"]
        assert entry["upstream"].startswith("http")


def test_get_backend_class_unknown() -> None:
    from app.services import protocols
    from app.services.protocols.base import ProtocolError

    with pytest.raises(ProtocolError):
        protocols.get_backend_class("nope")


# --------------------------------------------------------------------------- #
# WireGuard
# --------------------------------------------------------------------------- #
def test_wireguard_keypair_roundtrip() -> None:
    private = generate_private_key()
    public = public_from_private(private)
    assert public
    # Both are 32-byte base64 values.
    assert len(base64.b64decode(private)) == 32
    assert len(base64.b64decode(public)) == 32
    assert private != public


def test_wireguard_subnet_helpers() -> None:
    hosts = subnet_hosts("10.66.0.0/29")
    assert hosts[0] == "10.66.0.1"
    assert len(hosts) == 6


def test_wireguard_create_peer_persists() -> None:
    executor = FakeExecutor()
    backend = WireGuardBackend(executor, FakeInstance(name="wg0"))
    private = generate_private_key()
    spec = UserSpec(
        username="alice",
        extra={
            "public_key": public_from_private(private),
            "address": "10.66.0.2",
            "preshared_key": generate_private_key(),
        },
    )
    backend.create_user(spec)
    conf = executor.files["/etc/wireguard/wg0.conf"]
    assert "PublicKey" in conf
    assert "10.66.0.2/32" in conf
    assert any("wg" in cmd[0] for cmd in executor.commands)


def test_wireguard_config_for_user() -> None:
    executor = FakeExecutor()
    backend = WireGuardBackend(executor, FakeInstance(name="wg0", dns="1.1.1.1"))
    private = generate_private_key()
    config = backend.config_for_user(
        UserSpec(
            username="bob",
            extra={
                "private_key": private,
                "address": "10.66.0.3",
                "server_public_key": public_from_private(generate_private_key()),
                "endpoint_host": "vpn.example.com",
                "endpoint_port": 51820,
            },
        )
    )
    assert "[Interface]" in config.text
    assert "Endpoint = vpn.example.com:51820" in config.text
    assert config.qr_payload == config.text
    assert config.filename.endswith(".conf")


def test_wireguard_usage_parses_transfer() -> None:
    class TransferExecutor(FakeExecutor):
        def run(self, argv, *, timeout=60.0):  # noqa: ANN001
            self.commands.append(list(argv))
            if argv[:3] == ["wg", "show", "wg0"] and argv[3] == "dump":
                return ExecResult(
                    ok=True,
                    stdout=(
                        "privkey\tpubserver\t51820\toff\n"
                        "peerkey1\tpsk\t1.2.3.4:5\t10.66.0.2/32\t0\t100\t200\t25\n"
                    ),
                )
            if argv[:4] == ["wg", "show", "wg0", "transfer"]:
                return ExecResult(ok=True, stdout="peerkey1\t1000\t2000\n")
            return ExecResult(ok=True)

    executor = TransferExecutor()
    backend = WireGuardBackend(executor, FakeInstance(name="wg0"))
    usages = backend.usage()
    assert len(usages) == 1
    assert usages[0].bytes_in == 1000
    assert usages[0].bytes_out == 2000


# --------------------------------------------------------------------------- #
# sing-box
# --------------------------------------------------------------------------- #
def test_singbox_share_uris() -> None:
    vless = build_share_uri(
        kind="vless",
        host="h",
        port=443,
        uuid_or_password="u",
        security="reality",
        sni="s",
        public_key="pk",
        short_id="sid",
        label="node",
    )
    assert vless.startswith("vless://u@h:443?")
    assert "security=reality" in vless
    assert "pbk=pk" in vless

    vmess = build_share_uri(kind="vmess", host="h", port=443, uuid_or_password="u", label="node")
    assert vmess.startswith("vmess://")
    decoded = json.loads(base64.b64decode(vmess[len("vmess://") :]).decode("utf-8"))
    assert decoded["add"] == "h"

    ss = build_share_uri(
        kind="shadowsocks",
        host="h",
        port=8388,
        uuid_or_password="pw",
        method="aes-128-gcm",
        label="node",
    )
    assert ss.startswith("ss://")

    trojan = build_share_uri(kind="trojan", host="h", port=443, uuid_or_password="pw", label="node")
    assert trojan.startswith("trojan://")

    hy2 = build_share_uri(kind="hysteria2", host="h", port=443, uuid_or_password="pw", label="node")
    assert hy2.startswith("hysteria2://")


def test_singbox_render_config_has_v2ray_api() -> None:
    executor = FakeExecutor()
    backend = SingBoxBackend(
        executor,
        FakeInstance(
            kind="singbox",
            name="in",
            port=443,
            settings={"inbound_type": "vless", "security": "reality", "sni": "www.bing.com"},
        ),
    )
    config = backend.render_config([{"name": "alice", "uuid": "abc"}])
    assert config["inbounds"][0]["type"] == "vless"
    assert config["inbounds"][0]["tls"]["reality"]["enabled"] is True
    assert config["experimental"]["v2ray_api"]["stats"]["enabled"] is True
    assert config["experimental"]["clash_api"]["external_controller"]


def test_singbox_create_user_writes_config() -> None:
    executor = FakeExecutor()
    backend = SingBoxBackend(
        executor,
        FakeInstance(
            kind="singbox",
            name="in",
            port=443,
            settings={"inbound_type": "vmess", "security": "none"},
        ),
    )
    backend.create_user(UserSpec(username="alice"))
    stored = json.loads(executor.files["/etc/sing-box/in.json"])
    users = stored["inbounds"][0]["users"]
    assert users and users[0]["name"] == "alice"
    assert users[0]["uuid"]


def test_singbox_usage_parses_stats() -> None:
    class StatsExecutor(FakeExecutor):
        def run(self, argv, *, timeout=60.0):  # noqa: ANN001
            self.commands.append(list(argv))
            return ExecResult(
                ok=True,
                stdout=(
                    "user>>>alice>>>traffic>>>uplink 1000\nuser>>>alice>>>traffic>>>downlink 2000\n"
                ),
            )

    backend = SingBoxBackend(StatsExecutor(), FakeInstance(kind="singbox", name="in"))
    usages = backend.usage()
    assert usages[0].username == "alice"
    assert usages[0].bytes_in == 2000
    assert usages[0].bytes_out == 1000


def test_singbox_reality_keypair() -> None:
    private, public = generate_reality_keypair()
    assert private and public and private != public


# --------------------------------------------------------------------------- #
# ocserv / l2tp
# --------------------------------------------------------------------------- #
def test_ocserv_create_and_delete_user() -> None:
    executor = FakeExecutor()
    backend = OcservBackend(executor, FakeInstance(kind="ocserv", name="oc"))
    backend.create_user(UserSpec(username="alice", password="secret123"))
    assert any(cmd[0] == "sh" for cmd in executor.commands)
    backend.delete_user("alice")
    assert any("ocpasswd" in " ".join(cmd) for cmd in executor.commands)


def test_ocserv_usage_parses_json() -> None:
    class JsonExecutor(FakeExecutor):
        def run(self, argv, *, timeout=60.0):  # noqa: ANN001
            self.commands.append(list(argv))
            return ExecResult(
                ok=True,
                stdout='[{"username": "alice", "rx": 111, "tx": 222}]',
            )

    backend = OcservBackend(JsonExecutor(), FakeInstance(kind="ocserv", name="oc"))
    usages = backend.usage()
    assert usages[0].username == "alice"
    assert usages[0].bytes_in == 111
    assert usages[0].bytes_out == 222


def test_l2tp_chap_secrets_lifecycle() -> None:
    executor = FakeExecutor()
    backend = L2tpBackend(executor, FakeInstance(kind="l2tp", name="l2"))
    backend.create_user(UserSpec(username="alice", password="pw12345678"))
    chap = executor.files["/etc/ppp/chap-secrets"]
    assert "alice * pw12345678 *" in chap
    backend.delete_user("alice")
    assert "alice" not in executor.files["/etc/ppp/chap-secrets"]


def test_l2tp_psk_write() -> None:
    executor = FakeExecutor()
    backend = L2tpBackend(executor, FakeInstance(kind="l2tp", name="l2"))
    backend.set_psk("super-secret")
    assert 'PSK "super-secret"' in executor.files["/etc/ipsec.secrets"]


# --------------------------------------------------------------------------- #
# OpenVPN
# --------------------------------------------------------------------------- #
def test_openvpn_config_for_user() -> None:
    backend = OpenVpnBackend(FakeExecutor(), FakeInstance(kind="openvpn", name="server"))
    config = backend.config_for_user(
        UserSpec(
            username="alice",
            extra={
                "host": "vpn.example.com",
                "port": 1194,
                "protocol": "udp",
                "cipher": "AES-256-GCM",
            },
        )
    )
    assert "remote vpn.example.com 1194 udp" in config.text
    assert config.filename.endswith(".ovpn")


def test_openvpn_set_limits_writes_state() -> None:
    executor = FakeExecutor()
    backend = OpenVpnBackend(executor, FakeInstance(kind="openvpn", name="server"))
    backend.set_limits(UserSpec(username="alice", data_limit_bytes=1024))
    from app.config import get_settings

    path = (get_settings().protocol_state_dir / "server" / "alice.json").as_posix()
    payload = json.loads(executor.files[path])
    assert payload["data_limit_bytes"] == 1024
    assert payload["status"] == "active"


# --------------------------------------------------------------------------- #
# Service layer (DB backed)
# --------------------------------------------------------------------------- #
def test_create_wireguard_instance_allocates_subnet(db) -> None:
    from app.services import protocols_service

    first = protocols_service.create_instance(db, kind="wireguard", name="wg0", port=51820)
    second = protocols_service.create_instance(db, kind="wireguard", name="wg1", port=51821)
    assert first.subnet != second.subnet
    assert first.address
    db.commit()


def test_protocol_user_credentials_per_kind(db) -> None:
    from app.models.user import Role
    from app.services import protocols_service
    from app.services import users as users_service
    from app.services import vpn_users as vpn_users_service

    admin = users_service.create_user(
        db, username="admin", password="admin-password", role=Role.ADMIN
    )
    wg = protocols_service.create_instance(
        db, kind="wireguard", name="wg0", port=51820, is_default=True
    )
    sb = protocols_service.create_instance(
        db,
        kind="singbox",
        name="in0",
        port=443,
        is_default=True,
        settings={"inbound_type": "vless", "security": "reality"},
    )
    user = vpn_users_service.create_vpn_user(
        db, username="alice", password="password123", owner=admin
    )
    db.commit()

    wg_row = protocols_service.ensure_protocol_user(db, vpn_user=user, instance=wg, push=False)
    sb_row = protocols_service.ensure_protocol_user(db, vpn_user=user, instance=sb, push=False)
    assert wg_row.credentials_dict.get("public_key")
    assert wg_row.credentials_dict.get("address")
    assert sb_row.credentials_dict.get("uuid")
    assert protocols_service.usage_by_kind(db, user.id) == {"wireguard": 0, "singbox": 0}


def test_subscription_links_include_protocols(db) -> None:
    from app.models.user import Role
    from app.services import protocols_service, subscriptions
    from app.services import users as users_service
    from app.services import vpn_users as vpn_users_service

    admin = users_service.create_user(
        db, username="admin", password="admin-password", role=Role.ADMIN
    )
    instance = protocols_service.create_instance(
        db,
        kind="singbox",
        name="in0",
        port=443,
        is_default=True,
        settings={"inbound_type": "vless", "security": "none"},
    )
    user = vpn_users_service.create_vpn_user(
        db, username="bob", password="password123", owner=admin
    )
    protocols_service.ensure_protocol_user(db, vpn_user=user, instance=instance, push=False)
    db.commit()

    view = subscriptions.build_view(db, user)
    assert view.has_protocol_links
    assert "vless://" in view.aggregated_link


def test_unknown_kind_rejected(db) -> None:
    from app.services import protocols_service

    with pytest.raises(protocols_service.ProtocolServiceError):
        protocols_service.create_instance(db, kind="bogus", name="x")
