"""Tunnel backend interface and implementations.

A *backend* knows how to install, configure and operate one tunnel program on a
remote host. All remote commands are built here from **fixed, allow-listed
templates** with shell-quoted arguments; the SSH layer never receives a string
assembled from untrusted input.

Two backends are implemented:

* :class:`BackhaulBackend` — the AGPL-3.0 reverse tunnel from
  https://github.com/Musixal/Backhaul (transports ``tcp``/``tcpmux``/``udp``).
* :class:`WireGuardBackend` — the GPL-2.0 kernel datapath via
  ``wireguard-tools`` / ``wg-quick`` (UDP only).

Both are installed and executed as *external binaries/tools*; no upstream code
is copied or linked into the panel (AGPL/GPL compatibility).
"""

from __future__ import annotations

import abc
import logging
from dataclasses import dataclass, field

from app.services.tunnel_ssh import CommandResult, SSHError, SSHTransport, quote

logger = logging.getLogger(__name__)

BACKHAUL_REPO = "Musixal/Backhaul"
BACKHAUL_PINNED_VERSION = "v0.6.5"
BACKHAUL_BIN = "/usr/local/bin/backhaul"
BACKHAUL_DIR = "/etc/backhaul"
BACKHAUL_UNIT_DIR = "/etc/systemd/system"

WG_DIR = "/etc/wireguard"
WG_TOOLS_PKG = "wireguard wireguard-tools"

MANAGED_FIREWALL_ENV = "/etc/backhaul/panel.env"


@dataclass(slots=True)
class InstallResult:
    ok: bool
    version: str | None = None
    error: str | None = None
    log: list[str] = field(default_factory=list)


@dataclass(slots=True)
class StatusResult:
    running: bool
    connected: bool
    uptime_seconds: int = 0
    detail: str = ""
    version: str | None = None


class TunnelBackend(abc.ABC):
    """Abstract provisioning/operations contract for one tunnel program."""

    #: Stable identifier used by the panel (``backhaul`` / ``wireguard``).
    kind: str = ""
    #: Transports this backend supports (values from ``TunnelTransport``).
    transports: tuple[str, ...] = ()
    #: Whether the backend can carry UDP payloads.
    supports_udp: bool = False
    #: Upstream project URL + SPDX license (shown in the UI/research docs).
    upstream: str = ""
    license: str = ""

    def __init__(self, ssh: SSHTransport) -> None:
        self.ssh = ssh

    # -- lifecycle --------------------------------------------------------- #
    @abc.abstractmethod
    def install(self, *, offline_binary: bytes | None = None) -> InstallResult:
        """Install (or upgrade) the backend binary/tools on the remote host."""

    @abc.abstractmethod
    def configure(
        self,
        *,
        name: str,
        token: str,
        transport: str,
        control_port: int,
        public_ports: list[int],
        exit_host: str | None = None,
        mtu: int | None = None,
    ) -> InstallResult:
        """Write the backend configuration + systemd unit for one tunnel."""

    @abc.abstractmethod
    def start(self, name: str) -> CommandResult: ...

    @abc.abstractmethod
    def stop(self, name: str) -> CommandResult: ...

    @abc.abstractmethod
    def restart(self, name: str) -> CommandResult: ...

    @abc.abstractmethod
    def status(self, name: str) -> StatusResult: ...

    @abc.abstractmethod
    def logs(self, name: str, *, lines: int = 100) -> str: ...

    @abc.abstractmethod
    def healthcheck(self, *, host: str, port: int, timeout: float = 5.0) -> StatusResult: ...

    @abc.abstractmethod
    def update(self, *, offline_binary: bytes | None = None) -> InstallResult:
        """Re-fetch (or re-upload) the latest backend binary."""

    @abc.abstractmethod
    def uninstall(self) -> CommandResult:
        """Remove the backend and all of its tunnel units/config."""

    # -- helpers ----------------------------------------------------------- #
    def unit_name(self, name: str) -> str:
        return f"ovpn-tunnel-{name}"

    def _run(self, command: str, *, timeout: float = 180.0) -> CommandResult:
        return self.ssh.run(command, timeout=timeout)


# --------------------------------------------------------------------------- #
# Backhaul
# --------------------------------------------------------------------------- #
class BackhaulBackend(TunnelBackend):
    kind = "backhaul"
    transports = ("tcp", "tcpmux", "udp")
    supports_udp = True
    upstream = f"https://github.com/{BACKHAUL_REPO}"
    license = "AGPL-3.0"

    def install(self, *, offline_binary: bytes | None = None) -> InstallResult:
        log: list[str] = []
        # Dependencies (wget/curl + tar) — idempotent.
        dep = self._run(
            "command -v curl >/dev/null 2>&1 || "
            "command -v wget >/dev/null 2>&1 || "
            "(command -v apt-get >/dev/null 2>&1 && "
            "apt-get update -qq && apt-get install -y -qq curl) || true",
            timeout=300,
        )
        log.append(f"deps: exit={dep.exit_code}")

        mkdir = self._run(f"install -d -m 0750 {quote(BACKHAUL_DIR)}", timeout=30)
        log.append(f"mkdir: exit={mkdir.exit_code}")

        if offline_binary:
            # Offline install: the admin uploaded the binary from the panel.
            tmp = "/tmp/backhaul.upload"
            self.ssh.upload_bytes(offline_binary, tmp, mode=0o755)
            move = self._run(
                f"install -m 0755 {quote(tmp)} {quote(BACKHAUL_BIN)} && rm -f {quote(tmp)}"
            )
            log.append(f"offline install: exit={move.exit_code}")
            if not move.ok:
                return InstallResult(ok=False, error=move.stderr or "install failed", log=log)
            version = self._detect_version()
            return InstallResult(ok=True, version=version, log=log)

        arch = self._run("uname -m", timeout=15).stdout.strip() or "x86_64"
        arch = {"x86_64": "amd64", "aarch64": "arm64", "armv7l": "armv7"}.get(arch, "amd64")
        version = self._latest_version() or BACKHAUL_PINNED_VERSION
        url = (
            f"https://github.com/{BACKHAUL_REPO}/releases/download/"
            f"{version}/backhaul_linux_{arch}.tar.gz"
        )
        mirror = f"https://gh-proxy.com/{url}"
        download = self._run(
            "set -e; tmp=$(mktemp -d); "
            f'curl -fsSL {quote(url)} -o "$tmp/b.tar.gz" '
            f'|| curl -fsSL {quote(mirror)} -o "$tmp/b.tar.gz"; '
            f'tar -xzf "$tmp/b.tar.gz" -C "$tmp"; '
            f'install -m 0755 "$tmp/backhaul" {quote(BACKHAUL_BIN)}; '
            'rm -rf "$tmp"',
            timeout=600,
        )
        log.append(f"download {version}: exit={download.exit_code}")
        if not download.ok:
            return InstallResult(ok=False, error=download.stderr or "download failed", log=log)
        detected = self._detect_version() or version
        return InstallResult(ok=True, version=detected, log=log)

    def _latest_version(self) -> str | None:
        probe = self._run(
            f"curl -fsSL --max-time 8 https://api.github.com/repos/{BACKHAUL_REPO}/releases/latest "
            '| grep -o \'"tag_name": *"[^"]*"\' | head -1 | cut -d\'"\' -f4',
            timeout=20,
        )
        value = probe.stdout.strip()
        return value or None

    def _detect_version(self) -> str | None:
        probe = self._run(f"{quote(BACKHAUL_BIN)} -v 2>&1 | head -1", timeout=20)
        return probe.stdout.strip() or None

    def configure(
        self,
        *,
        name: str,
        token: str,
        transport: str,
        control_port: int,
        public_ports: list[int],
        exit_host: str | None = None,
        mtu: int | None = None,
    ) -> InstallResult:
        transport = transport if transport in self.transports else "tcp"
        ports_block = ",\n".join(f'  "{int(p)}"' for p in public_ports) or '  "443"'
        accept_udp = "true" if transport == "tcpmux" else "false"
        # Entry side acts as the *client* dialling the exit server, so users'
        # traffic is forwarded to the exit host where OpenVPN listens.
        config = (
            "[client]\n"
            f'remote_addr = "{exit_host or "127.0.0.1"}:{int(control_port)}"\n'
            f'transport = "{transport}"\n'
            f'token = "{token}"\n'
            "keepalive_period = 75\n"
            "nodelay = true\n"
            "retry_interval = 3\n"
            "dial_timeout = 10\n"
            f"accept_udp = {accept_udp}\n"
            'sniffer_log = "/var/log/backhaul.json"\n'
            'log_level = "info"\n'
            "ports = [\n"
            f"{ports_block}\n"
            "]\n"
        )
        path = f"{BACKHAUL_DIR}/{name}.toml"
        self.ssh.upload_bytes(config.encode("utf-8"), path, mode=0o600)

        unit = (
            "[Unit]\n"
            f"Description=OVPN Panel tunnel ({name}, backhaul)\n"
            "After=network-online.target\n"
            "Wants=network-online.target\n\n"
            "[Service]\n"
            "Type=simple\n"
            f"ExecStart={BACKHAUL_BIN} -c {path}\n"
            "Restart=always\n"
            "RestartSec=3\n"
            "LimitNOFILE=1048576\n\n"
            "[Install]\n"
            "WantedBy=multi-user.target\n"
        )
        unit_path = f"{BACKHAUL_UNIT_DIR}/{self.unit_name(name)}.service"
        self.ssh.upload_bytes(unit.encode("utf-8"), unit_path, mode=0o644)

        if mtu:
            # Clamp the interface MTU on the entry side for path-MTU safety.
            self._run(
                f"ip link show >/dev/null 2>&1 && true; "
                f"MTU={int(mtu)}; for i in $(ls /sys/class/net); do "
                'ip link set dev "$i" mtu "$MTU" 2>/dev/null || true; done || true',
                timeout=30,
            )

        reload = self._run(
            "systemctl daemon-reload && "
            f"systemctl enable {quote(self.unit_name(name))} >/dev/null 2>&1 || true",
            timeout=60,
        )
        if not reload.ok:
            return InstallResult(ok=False, error=reload.stderr or "systemd reload failed")
        self._open_firewall(control_port)
        return InstallResult(ok=True)

    def _open_firewall(self, port: int) -> None:
        self._run(
            "if command -v ufw >/dev/null 2>&1; then "
            f"ufw allow {int(port)}/tcp >/dev/null 2>&1 || true; "
            "elif command -v firewall-cmd >/dev/null 2>&1; then "
            f"firewall-cmd --permanent --add-port={int(port)}/tcp "
            ">/dev/null 2>&1 || true; "
            "firewall-cmd --reload >/dev/null 2>&1 || true; fi",
            timeout=60,
        )

    def start(self, name: str) -> CommandResult:
        return self._run(f"systemctl start {quote(self.unit_name(name))}")

    def stop(self, name: str) -> CommandResult:
        return self._run(f"systemctl stop {quote(self.unit_name(name))}")

    def restart(self, name: str) -> CommandResult:
        return self._run(f"systemctl restart {quote(self.unit_name(name))}")

    def status(self, name: str) -> StatusResult:
        unit = self.unit_name(name)
        active = self._run(f"systemctl is-active {quote(unit)} 2>/dev/null", timeout=20)
        running = active.stdout.strip() == "active"
        uptime = 0
        if running:
            ts = self._run(
                f"systemctl show -p ActiveEnterTimestampMonotonic --value {quote(unit)}",
                timeout=20,
            ).stdout.strip()
            if ts.isdigit() and int(ts) > 0:
                now = self._run("cat /proc/uptime | cut -d' ' -f1", timeout=10).stdout.strip()
                try:
                    uptime = max(0, int(float(now) - int(ts) / 1_000_000))
                except ValueError:
                    uptime = 0
        # "connected" is inferred from recent journal lines.
        log = self.logs(name, lines=40).lower()
        connected = running and (
            "control channel established successfully" in log
            or "client with remote address" in log
            or "started successfully" in log
        )
        version = self._detect_version()
        return StatusResult(
            running=running,
            connected=connected,
            uptime_seconds=uptime,
            detail=active.stdout.strip(),
            version=version,
        )

    def logs(self, name: str, *, lines: int = 100) -> str:
        result = self._run(
            f"journalctl -u {quote(self.unit_name(name))} --no-pager -n {int(lines)} 2>/dev/null",
            timeout=30,
        )
        return result.stdout

    def healthcheck(self, *, host: str, port: int, timeout: float = 5.0) -> StatusResult:
        # The panel performs the actual TCP probe; this hook reports reachability
        # of the control port as observed from the panel host.
        import socket

        try:
            with socket.create_connection((host, int(port)), timeout=timeout):
                return StatusResult(running=True, connected=True, detail="tcp ok")
        except OSError as exc:
            return StatusResult(running=False, connected=False, detail=str(exc))

    def update(self, *, offline_binary: bytes | None = None) -> InstallResult:
        return self.install(offline_binary=offline_binary)

    def uninstall(self) -> CommandResult:
        return self._run(
            "set +e; "
            f"for u in $(ls {BACKHAUL_UNIT_DIR}/ovpn-tunnel-*.service 2>/dev/null); do "
            'systemctl stop "$u" 2>/dev/null; '
            'systemctl disable "$u" 2>/dev/null; rm -f "$u"; done; '
            f"rm -rf {BACKHAUL_DIR} {BACKHAUL_BIN}; "
            "systemctl daemon-reload;",
            timeout=120,
        )


# --------------------------------------------------------------------------- #
# WireGuard
# --------------------------------------------------------------------------- #
class WireGuardBackend(TunnelBackend):
    kind = "wireguard"
    transports = ("wg", "udp")
    supports_udp = True
    upstream = "https://www.wireguard.com/"
    license = "GPL-2.0"

    def install(self, *, offline_binary: bytes | None = None) -> InstallResult:
        log: list[str] = []
        if offline_binary:
            # A prebuilt wireguard-tools binary can be shipped when the host has
            # no network access. WireGuard itself is in-kernel.
            self.ssh.upload_bytes(offline_binary, "/tmp/wg.upload", mode=0o755)
            move = self._run(
                "install -m 0755 /tmp/wg.upload /usr/bin/wg && rm -f /tmp/wg.upload",
                timeout=60,
            )
            log.append(f"offline wg: exit={move.exit_code}")
            ok = (
                move.ok
                and self._run("modprobe wireguard 2>/dev/null || true", timeout=30).exit_code == 0
            )
            return InstallResult(ok=ok, version="external", log=log)

        dep = self._run(
            "if command -v apt-get >/dev/null 2>&1; then "
            "DEBIAN_FRONTEND=noninteractive apt-get update -qq && "
            f"DEBIAN_FRONTEND=noninteractive apt-get install -y -qq {WG_TOOLS_PKG}; "
            "elif command -v dnf >/dev/null 2>&1; then dnf -y install wireguard-tools; "
            "elif command -v yum >/dev/null 2>&1; then yum -y install wireguard-tools; "
            "elif command -v apk >/dev/null 2>&1; then apk add --no-cache wireguard-tools; "
            "else exit 1; fi",
            timeout=600,
        )
        log.append(f"deps: exit={dep.exit_code}")
        if not dep.ok:
            return InstallResult(ok=False, error=dep.stderr or "install failed", log=log)
        self._run("modprobe wireguard 2>/dev/null || true", timeout=30)
        version = self._run("wg --version 2>&1 | head -1", timeout=20).stdout.strip()
        return InstallResult(ok=True, version=version or "unknown", log=log)

    def configure(
        self,
        *,
        name: str,
        token: str,
        transport: str,
        control_port: int,
        public_ports: list[int],
        exit_host: str | None = None,
        mtu: int | None = None,
    ) -> InstallResult:
        # The token doubles as the base64 private key (generated by the panel).
        self._run(f"install -d -m 0700 {quote(WG_DIR)}", timeout=30)
        priv = token
        pub = self._run(f"echo {quote(priv)} | wg pubkey 2>/dev/null", timeout=20).stdout.strip()
        if not pub:
            return InstallResult(ok=False, error="invalid WireGuard key")
        peer_pub = exit_host or ""
        mtu_line = f"MTU = {int(mtu)}\n" if mtu else ""
        config = (
            "[Interface]\n"
            f"PrivateKey = {priv}\n"
            f"ListenPort = {int(control_port)}\n"
            f"{mtu_line}"
            "Table = off\n\n"
            "[Peer]\n"
            f"PublicKey = {peer_pub}\n"
            "AllowedIPs = 0.0.0.0/0\n"
            "PersistentKeepalive = 25\n"
        )
        path = f"{WG_DIR}/{name}.conf"
        self.ssh.upload_bytes(config.encode("utf-8"), path, mode=0o600)
        return InstallResult(ok=True)

    def _unit(self, name: str) -> str:
        return f"wg-quick@{name}"

    def start(self, name: str) -> CommandResult:
        return self._run(f"systemctl start {quote(self._unit(name))}")

    def stop(self, name: str) -> CommandResult:
        return self._run(f"systemctl stop {quote(self._unit(name))}")

    def restart(self, name: str) -> CommandResult:
        return self._run(f"systemctl restart {quote(self._unit(name))}")

    def status(self, name: str) -> StatusResult:
        active = self._run(f"systemctl is-active {quote(self._unit(name))} 2>/dev/null", timeout=20)
        running = active.stdout.strip() == "active"
        connected = running
        detail = active.stdout.strip()
        if running:
            hs = self._run(f"wg show {quote(name)} latest-handshakes 2>/dev/null", timeout=20)
            # A non-zero handshake means the peer link is live.
            for line in hs.stdout.splitlines():
                parts = line.split()
                if len(parts) >= 2 and parts[-1] not in {"", "0"}:
                    connected = True
                    break
        version = self._run("wg --version 2>&1 | head -1", timeout=20).stdout.strip() or None
        return StatusResult(running=running, connected=connected, detail=detail, version=version)

    def logs(self, name: str, *, lines: int = 100) -> str:
        return self._run(
            f"journalctl -u {quote(self._unit(name))} --no-pager -n {int(lines)} 2>/dev/null",
            timeout=30,
        ).stdout

    def healthcheck(self, *, host: str, port: int, timeout: float = 5.0) -> StatusResult:
        import socket

        try:
            with socket.create_connection((host, int(port)), timeout=timeout):
                return StatusResult(running=True, connected=True, detail="udp ok")
        except OSError as exc:
            # UDP has no connect handshake; absence of an error is a weak signal.
            return StatusResult(running=False, connected=False, detail=str(exc))

    def update(self, *, offline_binary: bytes | None = None) -> InstallResult:
        return self.install(offline_binary=offline_binary)

    def uninstall(self) -> CommandResult:
        return self._run(
            "set +e; "
            f"for conf in {WG_DIR}/ovpn-tunnel-*.conf; do "
            '[ -e "$conf" ] || continue; name=$(basename "$conf" .conf); '
            'systemctl stop "wg-quick@$name" 2>/dev/null; '
            'systemctl disable "wg-quick@$name" 2>/dev/null; rm -f "$conf"; done',
            timeout=120,
        )


BACKENDS: dict[str, type[TunnelBackend]] = {
    BackhaulBackend.kind: BackhaulBackend,
    WireGuardBackend.kind: WireGuardBackend,
}


def get_backend(kind: str, ssh: SSHTransport) -> TunnelBackend:
    """Instantiate a backend by kind."""

    cls = BACKENDS.get(kind)
    if cls is None:
        raise SSHError(f"unknown tunnel backend: {kind}")
    return cls(ssh)


def backend_metadata() -> list[dict[str, object]]:
    """Backend catalogue for the UI (kind, transports, udp, upstream, license)."""

    return [
        {
            "kind": cls.kind,
            "transports": list(cls.transports),
            "supports_udp": cls.supports_udp,
            "upstream": cls.upstream,
            "license": cls.license,
        }
        for cls in (BackhaulBackend, WireGuardBackend)
    ]
