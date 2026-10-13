"""Cisco AnyConnect (ocserv) protocol backend (Phase 14).

Users are managed through ``ocpasswd`` (per-user password) and the running
server through ``occtl`` (limits, kick/disconnect, session accounting).
"""

from __future__ import annotations

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

OCSERV = "/usr/sbin/ocserv"
OCPASSWD = "/usr/bin/ocpasswd"
OCCTL = "/usr/bin/occtl"
OCPASSWD_FILE = "/etc/ocserv/ocpasswd"
OCSERV_DIR = "/etc/ocserv"


class OcservBackend(ProtocolBackend):
    kind = "ocserv"
    display_name = "Cisco AnyConnect (ocserv)"
    upstream = "https://gitlab.com/openconnect/ocserv"
    license = "GPL-2.0"
    capabilities = ("tls", "anyconnect", "occtl", "per-user-password")

    @property
    def passwd_file(self) -> str:
        return str(self.instance_settings.get("passwd_file") or OCPASSWD_FILE)

    @property
    def port(self) -> int:
        return int(getattr(self.instance, "port", 443) or 443)

    # -- lifecycle --------------------------------------------------------- #
    def install(self, *, offline: bytes | None = None) -> InstallResult:
        log: list[str] = []
        dep = self.run(
            [
                "sh",
                "-c",
                "if command -v apt-get >/dev/null 2>&1; then "
                "DEBIAN_FRONTEND=noninteractive apt-get update -qq && "
                "DEBIAN_FRONTEND=noninteractive apt-get install -y -qq ocserv; "
                "elif command -v dnf >/dev/null 2>&1; then dnf -y install ocserv; "
                "else exit 1; fi",
            ],
            timeout=600,
        )
        log.append(f"deps: exit={dep.exit_code}")
        if not dep.ok:
            return InstallResult(ok=False, error=dep.stderr or "install failed", log=log)
        version = self.run([OCSERV, "--version"], timeout=20).stdout.strip()
        return InstallResult(ok=True, version=version or "unknown", log=log)

    def healthcheck(self) -> HealthResult:
        result = self.run([OCCTL, "show", "status"], timeout=20)
        if result.ok:
            return HealthResult(ok=True, detail=result.stdout.strip()[:200])
        return HealthResult(ok=False, detail=result.stderr.strip() or "ocserv not running")

    # -- user lifecycle ---------------------------------------------------- #
    def _run_ocpasswd(self, args: list[object]) -> None:
        result = self.run([OCPASSWD, "-c", self.passwd_file, *args], timeout=30)
        if not result.ok:
            raise ProtocolError(result.stderr.strip() or "ocpasswd failed")

    def create_user(self, spec: UserSpec) -> None:
        if not spec.password:
            raise ProtocolError("ocserv requires a password")
        # ``ocpasswd -c <file> <user>`` reads the password from stdin; the
        # executor does not support stdin, so use the ``-c`` hash-free form via
        # the documented non-interactive pipe through a fixed template.
        result = self.run(
            [
                "sh",
                "-c",
                f"printf '%s\\n%s\\n' {quote(spec.password)} {quote(spec.password)} "
                f"| {OCPASSWD} -c {quote(self.passwd_file)} {quote(spec.username)}",
            ],
            timeout=30,
        )
        if not result.ok:
            raise ProtocolError(result.stderr.strip() or "ocpasswd failed")
        if not spec.enabled:
            self.disable(spec.username)

    def delete_user(self, username: str) -> None:
        result = self.run([OCPASSWD, "-c", self.passwd_file, "-d", username], timeout=30)
        if not result.ok and "not found" not in result.stderr.lower():
            raise ProtocolError(result.stderr.strip() or "ocpasswd delete failed")
        self.disable(username)

    def set_limits(self, spec: UserSpec) -> None:
        # ocserv enforces per-user limits via occtl at connect time; store the
        # desired values so the scheduler can apply them (see protocols_service).
        import json

        payload = {
            "data_limit_bytes": int(spec.data_limit_bytes or 0),
            "max_connections": int(spec.max_connections or 0),
            "speed_limit_kbps": int(spec.speed_limit_kbps or 0),
        }
        self.executor.write_file(
            f"/var/lib/ovpn-panel/protocol-state/ocserv/{spec.username}.json",
            json.dumps(payload),
            mode=0o600,
        )

    def disable(self, username: str) -> None:
        self.run([OCCTL, "disconnect", "user", username], timeout=20)

    def usage(self) -> list[Usage]:
        result = self.run([OCCTL, "-j", "show", "users"], timeout=20)
        if not result.ok:
            return []
        return _parse_occtl_users(result.stdout, with_traffic=True)

    def online(self) -> list[OnlineSession]:
        result = self.run([OCCTL, "-j", "show", "users"], timeout=20)
        if not result.ok:
            return []
        sessions: list[OnlineSession] = []
        for usage in _parse_occtl_users(result.stdout, with_traffic=True):
            sessions.append(
                OnlineSession(
                    username=usage.username,
                    bytes_in=usage.bytes_in,
                    bytes_out=usage.bytes_out,
                )
            )
        return sessions

    def config_for_user(self, spec: UserSpec) -> UserConfig:
        host = str(spec.extra.get("host") or getattr(self.instance, "host", ""))
        port = int(spec.extra.get("port") or self.port)
        text = f"{host}:{port}"
        return UserConfig(
            kind=self.kind,
            text=text,
            qr_payload=text,
            filename=f"{spec.username}-anyconnect.txt",
        )


def _parse_occtl_users(raw: str, *, with_traffic: bool = False) -> list[Usage]:
    """Parse ``occtl -j show users`` JSON (tolerant of the plain table too)."""

    import json

    usages: list[Usage] = []
    raw = raw.strip()
    if raw.startswith("["):
        try:
            entries = json.loads(raw)
        except json.JSONDecodeError:
            entries = []
        for entry in entries:
            username = str(entry.get("username") or entry.get("Username") or "")
            if not username:
                continue
            usages.append(
                Usage(
                    username=username,
                    bytes_in=int(entry.get("rx") or entry.get("bytes_in") or 0),
                    bytes_out=int(entry.get("tx") or entry.get("bytes_out") or 0),
                )
            )
        return usages
    # Plain table fallback: "user  vpn_ip  ...  rx  tx".
    for line in raw.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] not in {"user", "username"}:
            usages.append(Usage(username=parts[0]))
    return usages
