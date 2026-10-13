"""L2TP/IPsec protocol backend (Phase 14).

Combines libreswan (IPsec/IKE) with xl2tpd (L2TP). Per-user credentials live in
``/etc/ppp/chap-secrets``; the IPsec pre-shared key lives in
``/etc/ipsec.secrets``. Session accounting is produced by the ppp hooks the
panel installs (see :mod:`app.services.protocols`), which write per-user traffic
to the same state directory used here.
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
)

CHAP_SECRETS = "/etc/ppp/chap-secrets"
IPSEC_SECRETS = "/etc/ipsec.secrets"
L2TPD_CONF = "/etc/xl2tpd/xl2tpd.conf"
IPSEC_CONF = "/etc/ipsec.conf"


def state_dir() -> str:
    """L2TP per-user state directory, from the panel settings."""

    from app.config import get_settings

    return (get_settings().protocol_state_dir / "l2tp").as_posix()


class L2tpBackend(ProtocolBackend):
    kind = "l2tp"
    display_name = "L2TP/IPsec"
    upstream = "https://libreswan.org/"
    license = "GPL-2.0"
    capabilities = ("ipsec", "l2tp", "psk", "ppp-hooks")

    @property
    def psk(self) -> str:
        return str(self.instance_settings.get("psk") or "")

    @property
    def local_ip(self) -> str:
        return str(getattr(self.instance, "address", None) or "192.168.42.1")

    # -- lifecycle --------------------------------------------------------- #
    def install(self, *, offline: bytes | None = None) -> InstallResult:
        log: list[str] = []
        dep = self.run(
            [
                "sh",
                "-c",
                "if command -v apt-get >/dev/null 2>&1; then "
                "DEBIAN_FRONTEND=noninteractive apt-get update -qq && "
                "DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "
                "libreswan xl2tpd ppp; "
                "elif command -v dnf >/dev/null 2>&1; then "
                "dnf -y install libreswan xl2tpd ppp; "
                "else exit 1; fi",
            ],
            timeout=600,
        )
        log.append(f"deps: exit={dep.exit_code}")
        if not dep.ok:
            return InstallResult(ok=False, error=dep.stderr or "install failed", log=log)
        return InstallResult(ok=True, version="libreswan+xl2tpd", log=log)

    def healthcheck(self) -> HealthResult:
        ipsec = self.run(["ipsec", "status"], timeout=20)
        xl2tpd = self.executor.service("xl2tpd", "status", timeout=20)
        ok = ipsec.ok and xl2tpd.ok
        detail = (ipsec.stdout or ipsec.stderr).strip()[:200]
        return HealthResult(ok=ok, detail=detail)

    # -- secrets management ------------------------------------------------- #
    def set_psk(self, psk: str) -> None:
        if not psk:
            raise ProtocolError("PSK must not be empty")
        content = f': PSK "{psk}"\n'
        self.executor.write_file(IPSEC_SECRETS, content, mode=0o600)
        self.run(["ipsec", "restart"], timeout=60)

    # -- user lifecycle ---------------------------------------------------- #
    def _read_chap(self) -> list[str]:
        try:
            return self.executor.read_file(CHAP_SECRETS).splitlines()
        except ProtocolError:
            return []

    def _write_chap(self, lines: list[str]) -> None:
        self.executor.write_file(CHAP_SECRETS, "\n".join(lines) + "\n", mode=0o600)

    def create_user(self, spec: UserSpec) -> None:
        if not spec.password:
            raise ProtocolError("l2tp requires a password")
        lines = [ln for ln in self._read_chap() if not ln.startswith(f"{spec.username} ")]
        lines.append(f"{spec.username} * {spec.password} *")
        self._write_chap(lines)
        self.set_limits(spec)

    def delete_user(self, username: str) -> None:
        lines = [ln for ln in self._read_chap() if not ln.startswith(f"{username} ")]
        self._write_chap(lines)
        self.disable(username)

    def set_limits(self, spec: UserSpec) -> None:
        import json

        payload = {
            "data_limit_bytes": int(spec.data_limit_bytes or 0),
            "max_connections": int(spec.max_connections or 0),
            "expires_at": spec.expires_at.isoformat() if spec.expires_at else None,
        }
        self.executor.write_file(
            f"{state_dir()}/{spec.username}.json", json.dumps(payload), mode=0o600
        )

    def disable(self, username: str) -> None:
        # Drop any active ppp session for the user.
        self.run(
            [
                "sh",
                "-c",
                f"pkill -f {quote_user(username)} || true",
            ],
            timeout=20,
        )

    def usage(self) -> list[Usage]:
        # Traffic is recorded by the ppp up/down hooks into per-user counter
        # files; read them back here.
        result = self.run(
            ["sh", "-c", f"cat {state_dir()}/*.usage 2>/dev/null || true"], timeout=20
        )
        usages: list[Usage] = []
        for line in result.stdout.splitlines():
            parts = line.split()
            if len(parts) == 3 and parts[1].isdigit() and parts[2].isdigit():
                usages.append(
                    Usage(username=parts[0], bytes_in=int(parts[1]), bytes_out=int(parts[2]))
                )
        return usages

    def online(self) -> list[OnlineSession]:
        result = self.run(["sh", "-c", "ps -eo args= | grep -i pppd || true"], timeout=20)
        sessions: list[OnlineSession] = []
        for line in result.stdout.splitlines():
            if "pppd" not in line:
                continue
            for token in line.split():
                if token.startswith("name:"):
                    sessions.append(OnlineSession(username=token.split(":", 1)[1]))
        return sessions

    def config_for_user(self, spec: UserSpec) -> UserConfig:
        host = str(spec.extra.get("host") or getattr(self.instance, "host", ""))
        text = (
            f"VPN: L2TP/IPsec\n"
            f"Server: {host}\n"
            f"Username: {spec.username}\n"
            f"PSK: {self.psk}\n"
            "Pre-shared key authentication (L2TP over IPsec)\n"
        )
        return UserConfig(
            kind=self.kind,
            text=text,
            qr_payload=text,
            filename=f"{spec.username}-l2tp.txt",
        )


def quote_user(username: str) -> str:
    import shlex

    return shlex.quote(username)
