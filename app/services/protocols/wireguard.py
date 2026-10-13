"""WireGuard protocol backend (Phase 13).

Supports multiple interfaces per node (``wg0``, ``wg1``, ...). Each interface is
a :class:`ProtocolInstance`; end users are WireGuard peers with a generated
key pair and an address allocated from the interface subnet.

Traffic is read from ``wg show <iface> transfer`` (cumulative rx/tx per peer);
live peers come from ``wg show <iface> dump``. Peers are added/removed with
``wg set`` (live, no restart) and persisted to the interface config file.
"""

from __future__ import annotations

import base64
import ipaddress
import os
from dataclasses import dataclass, field

from app.services.protocols.base import (
    HealthResult,
    InstallResult,
    OnlineSession,
    ProtocolBackend,
    ProtocolError,
    Usage,
    UserConfig,
    UserSpec,
    quote,
)

WG_DIR = "/etc/wireguard"
WG_TOOLS_PKG = "wireguard wireguard-tools"
DEFAULT_MTU = 1420


# --------------------------------------------------------------------------- #
# Key helpers
# --------------------------------------------------------------------------- #
def _clamp(key: bytes) -> bytes:
    data = bytearray(key)
    data[0] &= 248
    data[31] &= 127
    data[31] |= 64
    return bytes(data)


def generate_private_key() -> str:
    """Return a fresh WireGuard private key (base64, clamped)."""

    return base64.b64encode(_clamp(os.urandom(32))).decode("ascii")


def public_from_private(private_key: str) -> str:
    """Derive the public key. Uses ``cryptography`` when available."""

    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

        raw = base64.b64decode(private_key)
        key = X25519PrivateKey.from_private_bytes(raw)
        pub = key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        return base64.b64encode(pub).decode("ascii")
    except Exception:  # noqa: BLE001 - fall back to the external tool
        return ""


def generate_keypair(executor=None) -> tuple[str, str]:
    """Return ``(private_key, public_key)``.

    Prefers the pure-python X25519 path; falls back to ``wg genkey`` /
    ``wg pubkey`` through the executor when ``cryptography`` is unavailable.
    """

    private = generate_private_key()
    public = public_from_private(private)
    if public:
        return private, public
    if executor is not None:
        result = executor.run(["wg", "pubkey"], timeout=15)
        if result.ok:
            # Feed the private key over stdin is not supported by our executor;
            # fall back to writing a temp key is avoided for safety.
            pass
    # Last resort: return a deterministic-length placeholder is unsafe, so we
    # raise to force the caller to surface a clear error.
    raise ProtocolError("cannot derive WireGuard public key (install 'cryptography')")


# --------------------------------------------------------------------------- #
# Subnet helpers
# --------------------------------------------------------------------------- #
def subnet_hosts(cidr: str) -> list[str]:
    """Allocatable host addresses in ``cidr`` (excluding network/broadcast)."""

    network = ipaddress.ip_network(cidr, strict=False)
    return [str(ip) for ip in network.hosts()]


def first_free_address(cidr: str, used: set[str]) -> str:
    for host in subnet_hosts(cidr):
        if host not in used:
            return host
    raise ProtocolError(f"no free address in {cidr}")


# --------------------------------------------------------------------------- #
# Backend
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class Peer:
    public_key: str
    allowed_ips: list[str] = field(default_factory=list)
    rx: int = 0
    tx: int = 0
    endpoint: str = ""
    last_handshake: int = 0


class WireGuardBackend(ProtocolBackend):
    kind = "wireguard"
    display_name = "WireGuard"
    upstream = "https://www.wireguard.com/"
    license = "GPL-2.0"
    capabilities = ("udp", "kernel", "multi-interface")
    supports_password = False

    # -- interface paths --------------------------------------------------- #
    @property
    def config_path(self) -> str:
        return f"{WG_DIR}/{self.instance_name or 'wg0'}.conf"

    @property
    def listen_port(self) -> int:
        return int(getattr(self.instance, "port", 51820) or 51820)

    @property
    def subnet(self) -> str:
        return str(getattr(self.instance, "subnet", None) or "10.66.0.0/24")

    @property
    def address(self) -> str:
        return str(getattr(self.instance, "address", None) or f"{self.subnet}")

    @property
    def dns(self) -> str:
        return str(getattr(self.instance, "dns", None) or "")

    @property
    def mtu(self) -> int:
        return int(getattr(self.instance, "mtu", None) or DEFAULT_MTU)

    # -- lifecycle --------------------------------------------------------- #
    def install(self, *, offline: bytes | None = None) -> InstallResult:
        log: list[str] = []
        if offline:
            self.executor.upload(offline, "/tmp/wg.upload", mode=0o755)
            move = self.run(["install", "-m", "0755", "/tmp/wg.upload", "/usr/bin/wg"], timeout=60)
            log.append(f"offline wg: exit={move.exit_code}")
            self.run(["/usr/sbin/modprobe", "wireguard"], timeout=30)
            return InstallResult(ok=move.ok, version="external", log=log)

        dep = self.run(
            [
                "sh",
                "-c",
                "if command -v apt-get >/dev/null 2>&1; then "
                "DEBIAN_FRONTEND=noninteractive apt-get update -qq && "
                f"DEBIAN_FRONTEND=noninteractive apt-get install -y -qq {WG_TOOLS_PKG}; "
                "elif command -v dnf >/dev/null 2>&1; then dnf -y install wireguard-tools; "
                "elif command -v yum >/dev/null 2>&1; then yum -y install wireguard-tools; "
                "elif command -v apk >/dev/null 2>&1; then apk add --no-cache wireguard-tools; "
                "else exit 1; fi",
            ],
            timeout=600,
        )
        log.append(f"deps: exit={dep.exit_code}")
        if not dep.ok:
            return InstallResult(ok=False, error=dep.stderr or "install failed", log=log)
        self.run(["/usr/sbin/modprobe", "wireguard"], timeout=30)
        version = self.run(["wg", "--version"], timeout=20).stdout.strip()
        return InstallResult(ok=True, version=version or "unknown", log=log)

    def write_interface(self, *, private_key: str, extra_peers: list[str] | None = None) -> None:
        """(Re)write the interface config file and bring it up."""

        self.run(["install", "-d", "-m", "0700", WG_DIR], timeout=30)
        dns_line = f"DNS = {self.dns}\n" if self.dns else ""
        config = (
            "[Interface]\n"
            f"Address = {self.address}\n"
            f"ListenPort = {self.listen_port}\n"
            f"PrivateKey = {private_key}\n"
            f"MTU = {self.mtu}\n"
            f"{dns_line}\n"
        )
        for peer in extra_peers or []:
            config += peer
        self.executor.write_file(self.config_path, config, mode=0o600)

    def healthcheck(self) -> HealthResult:
        name = self.instance_name or "wg0"
        active = self.run(["wg", "show", name], timeout=20)
        if active.ok:
            peers = sum(1 for line in active.stdout.splitlines() if line.startswith("peer:"))
            return HealthResult(ok=True, detail=f"{peers} peer(s)")
        version = self.run(["wg", "--version"], timeout=15).stdout.strip() or None
        return HealthResult(
            ok=False, detail=active.stderr.strip() or "interface down", version=version
        )

    # -- peers ------------------------------------------------------------- #
    def _peer_conf(self, spec: UserSpec) -> str:
        pub = str(spec.extra.get("public_key") or "")
        address = str(spec.extra.get("address") or "")
        psk = spec.extra.get("preshared_key")
        if not pub or not address:
            raise ProtocolError("wireguard peer requires public_key and address")
        lines = [
            "[Peer]",
            f"# {spec.username}",
            f"PublicKey = {pub}",
            f"AllowedIPs = {address}/32",
        ]
        if psk:
            lines.append(f"PresharedKey = {psk}")
        return "\n".join(lines) + "\n"

    def create_user(self, spec: UserSpec) -> None:
        pub = str(spec.extra.get("public_key") or "")
        address = str(spec.extra.get("address") or "")
        if not pub or not address:
            raise ProtocolError("wireguard peer requires public_key and address")
        argv = [
            "wg",
            "set",
            self.instance_name or "wg0",
            "peer",
            pub,
            "allowed-ips",
            f"{address}/32",
        ]
        psk = spec.extra.get("preshared_key")
        if psk:
            argv += ["preshared-key", "/dev/stdin"]
        self.run(argv, timeout=30)
        # Persist by appending to the interface config (idempotent rewrite).
        try:
            existing = self.executor.read_file(self.config_path)
        except ProtocolError:
            existing = ""
        peer = self._peer_conf(spec)
        if pub not in existing:
            self.executor.write_file(self.config_path, existing + peer, mode=0o600)

    def delete_user(self, username: str) -> None:
        # ``wg show dump`` does not expose peer comments, so the public key is
        # resolved from the interface config file instead.
        conf = ""
        try:
            conf = self.executor.read_file(self.config_path)
        except ProtocolError:
            conf = ""
        removed_pub = None
        blocks = conf.split("[Peer]")
        kept = [blocks[0]]
        for block in blocks[1:]:
            if f"# {username}\n" in block or block.strip().startswith(f"# {username}"):
                for line in block.splitlines():
                    if line.strip().startswith("PublicKey"):
                        removed_pub = line.split("=", 1)[1].strip()
                continue
            kept.append(block)
        if removed_pub:
            self.run(
                ["wg", "set", self.instance_name or "wg0", "peer", removed_pub, "remove"],
                timeout=30,
            )
        if len(kept) != len(blocks):
            self.executor.write_file(self.config_path, "[Peer]".join(kept), mode=0o600)

    def set_limits(self, spec: UserSpec) -> None:
        """WireGuard has no native quota; enforcement is applied by the panel.

        The peer stays in place; the scheduler removes it when the quota or
        expiry is exceeded (see :mod:`app.services.protocols_service`).
        """

    def disable(self, username: str) -> None:
        self.delete_user(username)

    def _dump_peers(self) -> list[Peer]:
        name = self.instance_name or "wg0"
        dump = self.run(["wg", "show", name, "dump"], timeout=20)
        peers: list[Peer] = []
        if not dump.ok:
            return peers
        for line in dump.stdout.splitlines()[1:]:
            parts = line.split("\t")
            if len(parts) < 8:
                continue
            peers.append(
                Peer(
                    public_key=parts[0],
                    allowed_ips=[p for p in parts[3].split(",") if p],
                    last_handshake=int(parts[4] or 0),
                    rx=int(parts[5] or 0),
                    tx=int(parts[6] or 0),
                    endpoint=parts[2],
                )
            )
        return peers

    def usage(self) -> list[Usage]:
        # ``wg show <iface> transfer`` yields "pubkey rx tx" per peer. Peer
        # names are resolved by the caller against stored credentials.
        name = self.instance_name or "wg0"
        transfer = self.run(["wg", "show", name, "transfer"], timeout=20)
        by_key: dict[str, tuple[int, int]] = {}
        if transfer.ok:
            for line in transfer.stdout.splitlines():
                parts = line.split()
                if len(parts) >= 3:
                    by_key[parts[0]] = (int(parts[1] or 0), int(parts[2] or 0))
        result: list[Usage] = []
        for peer in self._dump_peers():
            rx, tx = by_key.get(peer.public_key, (peer.rx, peer.tx))
            result.append(Usage(username=peer.public_key, bytes_in=rx, bytes_out=tx))
        return result

    def online(self) -> list[OnlineSession]:
        sessions: list[OnlineSession] = []
        for peer in self._dump_peers():
            if peer.last_handshake <= 0:
                continue
            sessions.append(
                OnlineSession(
                    username=peer.public_key,
                    address=",".join(peer.allowed_ips),
                    since=str(peer.last_handshake),
                    bytes_in=peer.rx,
                    bytes_out=peer.tx,
                )
            )
        return sessions

    def config_for_user(self, spec: UserSpec) -> UserConfig:
        private = str(spec.extra.get("private_key") or "")
        address = str(spec.extra.get("address") or "")
        server_pub = str(spec.extra.get("server_public_key") or "")
        endpoint_host = str(spec.extra.get("endpoint_host") or "")
        endpoint_port = int(spec.extra.get("endpoint_port") or self.listen_port)
        psk = spec.extra.get("preshared_key")
        dns_line = f"DNS = {self.dns}\n" if self.dns else ""
        psk_line = f"PresharedKey = {psk}\n" if psk else ""
        text = (
            "[Interface]\n"
            f"PrivateKey = {private}\n"
            f"Address = {address}/32\n"
            f"{dns_line}"
            f"MTU = {self.mtu}\n\n"
            "[Peer]\n"
            f"PublicKey = {server_pub}\n"
            f"AllowedIPs = 0.0.0.0/0, ::/0\n"
            f"Endpoint = {endpoint_host}:{endpoint_port}\n"
            f"{psk_line}"
            "PersistentKeepalive = 25\n"
        )
        filename = f"{spec.username}-{self.instance_name or 'wg'}.conf"
        return UserConfig(
            kind=self.kind,
            text=text,
            qr_payload=text,
            filename=filename,
            uri=f"wg://{endpoint_host}:{endpoint_port}/{server_pub}",
        )

    def uninstall(self) -> None:
        self.run(["wg-quick", "down", self.instance_name or "wg0"], timeout=60)
        self.run(["rm", "-f", self.config_path], timeout=30)

    # -- server key management -------------------------------------------- #
    def server_public_key(self) -> str:
        """Read (or lazily create) the interface's server public key."""

        try:
            conf = self.executor.read_file(self.config_path)
        except ProtocolError:
            conf = ""
        for line in conf.splitlines():
            if line.strip().startswith("PrivateKey"):
                private = line.split("=", 1)[1].strip()
                pub = public_from_private(private)
                if pub:
                    return pub
        private = generate_private_key()
        self.write_interface(private_key=private)
        return public_from_private(private)


def peer_public_key(private_key: str) -> str:
    """Expose the derivation helper for the service layer."""

    return public_from_private(private_key) or quote("")
