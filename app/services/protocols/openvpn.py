"""OpenVPN protocol backend — the reference implementation (Phase 12).

Wraps the existing panel building blocks (management interface, easy-rsa PKI,
auth file) behind the :class:`ProtocolBackend` interface. Nothing here changes
the OpenVPN behaviour already shipped; it merely exposes it through the common
contract so additional protocols can be added without touching the user service.
"""

from __future__ import annotations

import contextlib
import json

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
from app.services.protocols.executor import Executor  # noqa: F401 - typing only

STATE_DIR = "/var/lib/ovpn-panel/protocol-state"


class OpenVpnBackend(ProtocolBackend):
    kind = "openvpn"
    display_name = "OpenVPN"
    upstream = "https://openvpn.net/community/"
    license = "GPL-2.0"
    capabilities = ("tcp", "udp", "certificates", "management-interface")

    # -- lifecycle --------------------------------------------------------- #
    def install(self, *, offline: bytes | None = None) -> InstallResult:
        log: list[str] = []
        dep = self.run(
            [
                "sh",
                "-c",
                "if command -v apt-get >/dev/null 2>&1; then "
                "DEBIAN_FRONTEND=noninteractive apt-get update -qq && "
                "DEBIAN_FRONTEND=noninteractive apt-get install -y -qq openvpn easy-rsa; "
                "elif command -v dnf >/dev/null 2>&1; then dnf -y install openvpn easy-rsa; "
                "else exit 1; fi",
            ],
            timeout=600,
        )
        log.append(f"deps: exit={dep.exit_code}")
        if not dep.ok:
            return InstallResult(ok=False, error=dep.stderr or "install failed", log=log)
        version = self.run(["openvpn", "--version"], timeout=20).stdout.splitlines()
        return InstallResult(ok=True, version=version[0] if version else "unknown", log=log)

    def healthcheck(self) -> HealthResult:
        from app.services.ovpn import ManagementError, ManagementInterface

        try:
            status = ManagementInterface().status()
        except (ManagementError, OSError) as exc:
            return HealthResult(ok=False, detail=str(exc))
        return HealthResult(ok=True, detail=f"{status.online_count} client(s)")

    # -- user lifecycle ---------------------------------------------------- #
    def _state_path(self, username: str) -> str:
        return f"{STATE_DIR}/{self.instance_name or 'server'}/{username}.json"

    def create_user(self, spec: UserSpec) -> None:
        from app.services import helper_client

        cn = str(spec.extra.get("common_name") or spec.username)
        try:
            result = helper_client.issue_client_certificate(cn)
        except helper_client.HelperError as exc:
            raise ProtocolError(str(exc)) from exc
        if not result.ok:
            raise ProtocolError(result.error or "certificate issuance failed")
        self.set_limits(spec)

    def delete_user(self, username: str) -> None:
        from app.services import helper_client

        cn = str(username)
        with contextlib.suppress(helper_client.HelperError):
            helper_client.revoke_client_certificate(cn)
        self.executor.run(["rm", "-f", self._state_path(username)], timeout=20)

    def set_limits(self, spec: UserSpec) -> None:
        payload = {
            "username": spec.username,
            "status": "active" if spec.enabled else "disabled",
            "data_limit_bytes": spec.data_limit_bytes or 0,
            "used_bytes": spec.used_bytes,
            "speed_limit_kbps": spec.speed_limit_kbps or 0,
            "max_connections": spec.max_connections or 0,
            "expires_at": spec.expires_at.isoformat() if spec.expires_at else None,
        }
        self.executor.write_file(self._state_path(spec.username), json.dumps(payload), mode=0o600)

    def disable(self, username: str) -> None:
        from app.services.ovpn import ManagementError, ManagementInterface

        with contextlib.suppress(ManagementError, OSError):
            ManagementInterface().kill(username)

    def usage(self) -> list[Usage]:
        from app.services.ovpn import ManagementError, ManagementInterface

        try:
            clients = ManagementInterface().connected_clients()
        except (ManagementError, OSError):
            return []
        return [
            Usage(username=c.common_name, bytes_in=c.bytes_received, bytes_out=c.bytes_sent)
            for c in clients
        ]

    def online(self) -> list[OnlineSession]:
        from app.services.ovpn import ManagementError, ManagementInterface

        try:
            clients = ManagementInterface().connected_clients()
        except (ManagementError, OSError):
            return []
        return [
            OnlineSession(
                username=c.common_name,
                address=c.address,
                since=c.connected_since,
                bytes_in=c.bytes_received,
                bytes_out=c.bytes_sent,
            )
            for c in clients
        ]

    def config_for_user(self, spec: UserSpec) -> UserConfig:
        from app.services import config_builder

        host = str(spec.extra.get("host") or "127.0.0.1")
        port = int(spec.extra.get("port") or 1194)
        protocol = str(spec.extra.get("protocol") or "udp")
        cipher = str(spec.extra.get("cipher") or "AES-256-GCM")
        text = config_builder.build_client_config(
            host=host, port=port, protocol=protocol, cipher=cipher
        )
        return UserConfig(
            kind=self.kind,
            text=text,
            qr_payload=text,
            filename=f"{spec.username}.ovpn",
        )
