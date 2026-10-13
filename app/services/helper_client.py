"""Client for the privileged helper.

The panel talks to the helper over a unix domain socket. Only allow-listed
actions are supported; arguments are always passed as structured JSON, never as
concatenated shell strings.
"""

from __future__ import annotations

import json
import socket
from dataclasses import dataclass

from app.config import get_settings
from app.core.validators import ValidationError


class HelperError(RuntimeError):
    """Raised when the helper is unreachable or returns an error."""


@dataclass(slots=True)
class HelperResult:
    ok: bool
    data: dict | None = None
    error: str | None = None


def _call(payload: dict, timeout: float = 10.0) -> HelperResult:
    settings = get_settings()
    if not hasattr(socket, "AF_UNIX"):
        raise HelperError("unix domain sockets are not supported on this platform")
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            sock.connect(str(settings.helper_socket))
            sock.sendall(json.dumps(payload).encode("utf-8") + b"\n")
            chunks: list[bytes] = []
            while True:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                chunks.append(chunk)
                if chunk.endswith(b"\n"):
                    break
    except (TimeoutError, FileNotFoundError, ConnectionRefusedError, OSError) as exc:
        raise HelperError(f"helper unavailable: {exc}") from exc
    raw = b"".join(chunks).decode("utf-8").strip()
    if not raw:
        raise HelperError("empty response from helper")
    try:
        result = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HelperError("malformed response from helper") from exc
    return HelperResult(
        ok=bool(result.get("ok")),
        data=result.get("data"),
        error=result.get("error"),
    )


def generate_client_config(**kwargs: object) -> HelperResult:
    return _call({"action": "generate_client_config", **kwargs})


def issue_client_certificate(common_name: str) -> HelperResult:
    if not common_name:
        raise ValidationError("common_name is required")
    return _call({"action": "issue_client_certificate", "common_name": common_name})


def revoke_client_certificate(common_name: str) -> HelperResult:
    if not common_name:
        raise ValidationError("common_name is required")
    return _call({"action": "revoke_client_certificate", "common_name": common_name})


def write_auth_file(entries: dict[str, str]) -> HelperResult:
    """Persist ``username -> argon2 hash`` entries for OpenVPN auth."""

    return _call({"action": "write_auth_file", "entries": entries})


def service_action(name: str, action: str) -> HelperResult:
    return _call({"action": "service", "name": name, "verb": action})


def agent_service_action(node_id: int, verb: str) -> HelperResult:
    return _call({"action": "agent_service", "node_id": node_id, "verb": verb})


def instance_action(name: str, verb: str) -> HelperResult:
    """Start/stop/restart/status a single OpenVPN instance."""

    if not name:
        raise ValidationError("instance name is required")
    return _call({"action": "instance", "name": name, "verb": verb})


def read_config_file(path: str) -> HelperResult:
    if not path:
        raise ValidationError("path is required")
    return _call({"action": "read_config_file", "path": path})


def write_config_file(path: str, content: str) -> HelperResult:
    if not path:
        raise ValidationError("path is required")
    return _call({"action": "write_config_file", "path": path, "content": content})


def reload_firewall() -> HelperResult:
    return _call({"action": "reload_firewall"})


def run_command(argv: list[str], *, timeout: float = 60.0) -> HelperResult:
    """Run a fixed argv list on the local host through the helper."""

    if not argv:
        raise ValidationError("argv is required")
    return _call({"action": "run_command", "argv": list(argv), "timeout": int(timeout)})


def write_protocol_file(path: str, content: str, *, mode: int = 0o600) -> HelperResult:
    if not path:
        raise ValidationError("path is required")
    return _call(
        {"action": "write_protocol_file", "path": path, "content": content, "mode": int(mode)}
    )


def unit_action(unit: str, verb: str) -> HelperResult:
    if not unit:
        raise ValidationError("unit is required")
    return _call({"action": "unit_action", "unit": unit, "verb": verb})
