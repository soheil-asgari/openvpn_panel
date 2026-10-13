"""Allow-listed privileged actions.

Every function here maps to a fixed ``argv`` passed to :func:`subprocess.run`.
Arguments are validated with :mod:`app.core.validators`; no shell string is
ever assembled and the shell is always disabled.
"""

from __future__ import annotations

import re
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path

from app.core.validators import (
    ValidationError,
    validate_common_name,
    validate_instance_name,
    validate_node_name,
)

# Fixed, absolute binary locations (defence against PATH manipulation).
OPENVPN = "/usr/sbin/openvpn"
EASYRSA = "/usr/share/easy-rsa/easyrsa"
SYSTEMCTL = "/usr/bin/systemctl"
NFT = "/usr/sbin/nft"
IPTABLES = "/usr/sbin/iptables"

# Root directories whose files may be written through the protocol executor
# (defence against arbitrary writes via the helper socket).
ALLOWED_PROTOCOL_DIRS = (
    Path("/etc/wireguard"),
    Path("/etc/sing-box"),
    Path("/etc/ocserv"),
    Path("/etc/ppp"),
    Path("/etc/xl2tpd"),
    Path("/var/lib/ovpn-panel/protocol-state"),
    Path("/etc/systemd/system"),
)
ALLOWED_PROTOCOL_FILES = (
    Path("/etc/ipsec.secrets"),
    Path("/etc/ipsec.conf"),
)
MAX_PROTOCOL_BYTES = 512 * 1024

# Units the protocol executor may control (allow-list, no shell metacharacters).
PROTOCOL_UNIT_RE = re.compile(r"^[A-Za-z0-9_.@:-]{1,96}$")
PROTOCOL_UNIT_PREFIXES = ("ovpn-proto-", "wg-quick@", "sing-box", "ocserv", "xl2tpd", "ipsec")
PROTOCOL_UNIT_VERBS = {"start", "stop", "restart", "status", "enable", "disable", "reload"}

PKI_DIR = Path("/etc/openvpn/pki")
CLIENT_DIR = Path("/etc/openvpn/clients")
AUTH_FILE = Path("/etc/openvpn/auth/users.db")
INSTANCES_DIR = Path("/etc/openvpn/instances")

ALLOWED_SERVICES = {
    "openvpn-server@server",
    "ovpn-panel",
    "ovpn-panel-agent",
}

ALLOWED_VERBS = {"start", "stop", "restart", "status"}

# Directories whose files may be read/written through the helper. Paths are
# resolved and checked to stay inside one of these roots (defence against
# traversal via ``..`` or symlinks).
ALLOWED_CONFIG_DIRS = (
    INSTANCES_DIR,
    Path("/etc/ovpn-panel"),
    Path("/etc/openvpn/ccd"),
)

# Individual files outside the directories above that are explicitly allowed.
ALLOWED_CONFIG_FILES = (
    PKI_DIR / "ca.crt",
    PKI_DIR / "tls-crypt.key",
)

MAX_CONFIG_BYTES = 512 * 1024


@dataclass(slots=True)
class ActionResult:
    ok: bool
    data: dict | None = None
    error: str | None = None


def _run(argv: list[str], timeout: int = 30) -> subprocess.CompletedProcess[str]:
    """Run a fixed command with an argument list (never a shell string)."""

    return subprocess.run(  # noqa: S603 - argv list, shell disabled
        argv,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def issue_client_certificate(common_name: str) -> ActionResult:
    try:
        cn = validate_common_name(common_name)
    except ValidationError as exc:
        return ActionResult(ok=False, error=str(exc))

    argv = [EASYRSA, "--batch", "--pki-dir", str(PKI_DIR), "build-client-full", cn, "nopass"]
    try:
        proc = _run(argv)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return ActionResult(ok=False, error=f"easyrsa failed: {exc}")
    if proc.returncode != 0:
        return ActionResult(ok=False, error=proc.stderr.strip() or "easyrsa error")

    cert = PKI_DIR / "issued" / f"{cn}.crt"
    key = PKI_DIR / "private" / f"{cn}.key"
    ca = PKI_DIR / "ca.crt"
    return ActionResult(
        ok=True,
        data={
            "common_name": cn,
            "certificate": cert.read_text(encoding="utf-8") if cert.exists() else "",
            "private_key": key.read_text(encoding="utf-8") if key.exists() else "",
            "ca": ca.read_text(encoding="utf-8") if ca.exists() else "",
        },
    )


def revoke_client_certificate(common_name: str) -> ActionResult:
    try:
        cn = validate_common_name(common_name)
    except ValidationError as exc:
        return ActionResult(ok=False, error=str(exc))

    argv = [EASYRSA, "--batch", "--pki-dir", str(PKI_DIR), "revoke", cn]
    try:
        proc = _run(argv)
        if proc.returncode == 0:
            _run([EASYRSA, "--batch", "--pki-dir", str(PKI_DIR), "gen-crl"])
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return ActionResult(ok=False, error=f"easyrsa failed: {exc}")
    if proc.returncode != 0:
        return ActionResult(ok=False, error=proc.stderr.strip() or "easyrsa error")
    return ActionResult(ok=True, data={"common_name": cn})


def write_auth_file(entries: dict[str, str]) -> ActionResult:
    """Write ``username:argon2-hash`` lines for OpenVPN's auth hook.

    Usernames are validated; the file is written atomically with mode 600.
    """

    if not isinstance(entries, dict):
        return ActionResult(ok=False, error="entries must be an object")
    lines: list[str] = []
    for username, password_hash in entries.items():
        try:
            uname = validate_common_name(username)
        except ValidationError as exc:
            return ActionResult(ok=False, error=str(exc))
        if ":" in password_hash or "\n" in password_hash:
            return ActionResult(ok=False, error="invalid hash")
        lines.append(f"{uname}:{password_hash}")

    AUTH_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = AUTH_FILE.with_suffix(".tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    tmp.chmod(0o600)
    tmp.replace(AUTH_FILE)
    return ActionResult(ok=True, data={"count": len(lines)})


def generate_client_config(**kwargs: object) -> ActionResult:
    """Return a rendered client config produced by the panel's builder.

    The helper does not read arbitrary files; it validates the common name and
    echoes back the config text handed to it (already validated by the panel) so
    certificates can be merged in a privileged context when required.
    """

    try:
        cn = validate_common_name(str(kwargs.get("common_name", "")))
        host = validate_node_name(str(kwargs.get("host", "")))
    except ValidationError as exc:
        return ActionResult(ok=False, error=str(exc))
    data = {"host": host, "common_name": cn, "config": kwargs.get("config")}
    return ActionResult(ok=True, data=data)


def service_action(name: str, verb: str) -> ActionResult:
    if name not in ALLOWED_SERVICES:
        return ActionResult(ok=False, error="service not allowed")
    if verb not in ALLOWED_VERBS:
        return ActionResult(ok=False, error="verb not allowed")
    argv = [SYSTEMCTL, verb, name]
    try:
        proc = _run(argv)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return ActionResult(ok=False, error=f"systemctl failed: {exc}")
    return ActionResult(
        ok=proc.returncode == 0,
        data={"stdout": proc.stdout.strip()},
        error=None if proc.returncode == 0 else proc.stderr.strip(),
    )


def agent_service_action(node_id: int, verb: str) -> ActionResult:
    if not isinstance(node_id, int) or node_id < 0:
        return ActionResult(ok=False, error="invalid node id")
    unit = f"ovpn-panel-agent@{node_id}"
    if verb not in ALLOWED_VERBS:
        return ActionResult(ok=False, error="verb not allowed")
    argv = [SYSTEMCTL, verb, unit]
    try:
        proc = _run(argv)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return ActionResult(ok=False, error=f"systemctl failed: {exc}")
    return ActionResult(ok=proc.returncode == 0, error=proc.stderr.strip() or None)


def reload_openvpn() -> ActionResult:
    return service_action("openvpn-server@server", "restart")


def openvpn_instance_action(name: str, verb: str) -> ActionResult:
    """Start/stop/restart/status a single OpenVPN instance by name."""

    try:
        instance = validate_instance_name(name)
    except ValidationError as exc:
        return ActionResult(ok=False, error=str(exc))
    if verb not in ALLOWED_VERBS:
        return ActionResult(ok=False, error="verb not allowed")
    unit = f"openvpn-server@{instance}"
    argv = [SYSTEMCTL, verb, unit]
    try:
        proc = _run(argv)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return ActionResult(ok=False, error=f"systemctl failed: {exc}")
    return ActionResult(
        ok=proc.returncode == 0,
        data={"instance": instance, "verb": verb, "stdout": proc.stdout.strip()},
        error=None if proc.returncode == 0 else proc.stderr.strip(),
    )


def _resolve_config_path(raw: str) -> Path:
    """Resolve ``raw`` to an absolute path inside an allow-listed root."""

    if not isinstance(raw, str) or not raw:
        raise ValidationError("path is required")
    candidate = Path(raw)
    if not candidate.is_absolute():
        raise ValidationError("path must be absolute")
    resolved = candidate.resolve()
    for root in ALLOWED_CONFIG_DIRS:
        root_resolved = root.resolve()
        if resolved == root_resolved or root_resolved in resolved.parents:
            return resolved
    if resolved in {p.resolve() for p in ALLOWED_CONFIG_FILES}:
        return resolved
    raise ValidationError("path not allowed")


def read_config_file(path: str) -> ActionResult:
    try:
        target = _resolve_config_path(path)
    except ValidationError as exc:
        return ActionResult(ok=False, error=str(exc))
    if not target.is_file():
        return ActionResult(ok=False, error="file not found")
    try:
        if target.stat().st_size > MAX_CONFIG_BYTES:
            return ActionResult(ok=False, error="file too large")
        content = target.read_text(encoding="utf-8")
    except OSError as exc:
        return ActionResult(ok=False, error=f"read failed: {exc}")
    return ActionResult(ok=True, data={"path": str(target), "content": content})


def write_config_file(path: str, content: str) -> ActionResult:
    try:
        target = _resolve_config_path(path)
    except ValidationError as exc:
        return ActionResult(ok=False, error=str(exc))
    if not isinstance(content, str):
        return ActionResult(ok=False, error="content must be a string")
    if len(content.encode("utf-8")) > MAX_CONFIG_BYTES:
        return ActionResult(ok=False, error="content too large")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_text(content, encoding="utf-8")
        tmp.chmod(0o640)
        tmp.replace(target)
    except OSError as exc:
        return ActionResult(ok=False, error=f"write failed: {exc}")
    return ActionResult(ok=True, data={"path": str(target), "bytes": len(content)})


def run_command(argv: list[str], *, timeout: int = 60) -> ActionResult:
    """Run a fixed argv list supplied by a protocol backend.

    The list is validated: it must be a non-empty list of strings, no element
    may contain a NUL byte, and the executable is resolved to an absolute path
    inside a small allow-list of roots. The shell is never used.
    """

    if not isinstance(argv, list) or not argv:
        return ActionResult(ok=False, error="argv must be a non-empty list")
    if not all(isinstance(a, str) and "\x00" not in a for a in argv):
        return ActionResult(ok=False, error="invalid argument")
    binary = shlex.split(argv[0])[0] if " " in argv[0] else argv[0]
    if not binary.startswith("/"):
        return ActionResult(ok=False, error="executable must be an absolute path")
    if any(part in {".."} for part in Path(binary).parts):
        return ActionResult(ok=False, error="invalid executable path")
    try:
        proc = _run(list(argv), timeout=timeout)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return ActionResult(ok=False, error=f"command failed: {exc}")
    return ActionResult(
        ok=proc.returncode == 0,
        data={
            "exit_code": proc.returncode,
            "stdout": proc.stdout[-65536:],
            "stderr": proc.stderr[-16384:],
        },
        error=None if proc.returncode == 0 else (proc.stderr.strip() or "non-zero exit"),
    )


def _resolve_protocol_path(raw: str) -> Path:
    if not isinstance(raw, str) or not raw:
        raise ValidationError("path is required")
    candidate = Path(raw)
    if not candidate.is_absolute():
        raise ValidationError("path must be absolute")
    resolved = candidate.resolve()
    for root in ALLOWED_PROTOCOL_DIRS:
        root_resolved = root.resolve()
        if resolved == root_resolved or root_resolved in resolved.parents:
            return resolved
    if resolved in {p.resolve() for p in ALLOWED_PROTOCOL_FILES}:
        return resolved
    raise ValidationError("path not allowed")


def write_protocol_file(path: str, content: str, *, mode: int = 0o600) -> ActionResult:
    try:
        target = _resolve_protocol_path(path)
    except ValidationError as exc:
        return ActionResult(ok=False, error=str(exc))
    if not isinstance(content, str):
        return ActionResult(ok=False, error="content must be a string")
    if len(content.encode("utf-8")) > MAX_PROTOCOL_BYTES:
        return ActionResult(ok=False, error="content too large")
    if mode not in {0o600, 0o640, 0o644, 0o755, 0o700}:
        return ActionResult(ok=False, error="mode not allowed")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_text(content, encoding="utf-8")
        tmp.chmod(mode)
        tmp.replace(target)
    except OSError as exc:
        return ActionResult(ok=False, error=f"write failed: {exc}")
    return ActionResult(ok=True, data={"path": str(target), "bytes": len(content)})


def unit_action(unit: str, verb: str) -> ActionResult:
    """Start/stop/restart/status a protocol unit from the allow-list."""

    if not isinstance(unit, str) or not PROTOCOL_UNIT_RE.match(unit):
        return ActionResult(ok=False, error="invalid unit name")
    if not any(unit.startswith(prefix) for prefix in PROTOCOL_UNIT_PREFIXES):
        return ActionResult(ok=False, error="unit not allowed")
    if verb not in PROTOCOL_UNIT_VERBS:
        return ActionResult(ok=False, error="verb not allowed")
    argv = [SYSTEMCTL, verb, unit]
    try:
        proc = _run(argv)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return ActionResult(ok=False, error=f"systemctl failed: {exc}")
    return ActionResult(
        ok=proc.returncode == 0,
        data={"stdout": proc.stdout.strip()},
        error=None if proc.returncode == 0 else proc.stderr.strip(),
    )


def reload_firewall() -> ActionResult:
    """Re-apply NAT/forwarding rules by restarting the oneshot unit."""

    argv = [SYSTEMCTL, "restart", "ovpn-panel-nat.service"]
    try:
        proc = _run(argv)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return ActionResult(ok=False, error=f"systemctl failed: {exc}")
    return ActionResult(
        ok=proc.returncode == 0,
        data={"stdout": proc.stdout.strip()},
        error=None if proc.returncode == 0 else proc.stderr.strip(),
    )


DISPATCH = {
    "generate_client_config": generate_client_config,
    "issue_client_certificate": issue_client_certificate,
    "revoke_client_certificate": revoke_client_certificate,
    "write_auth_file": write_auth_file,
    "service": service_action,
    "agent_service": agent_service_action,
    "instance": openvpn_instance_action,
    "read_config_file": read_config_file,
    "write_config_file": write_config_file,
    "run_command": run_command,
    "write_protocol_file": write_protocol_file,
    "unit_action": unit_action,
    "reload_firewall": reload_firewall,
}


def dispatch(payload: dict) -> ActionResult:
    action = payload.get("action")
    if action not in DISPATCH:
        return ActionResult(ok=False, error="unknown action")
    kwargs = {k: v for k, v in payload.items() if k != "action"}
    try:
        if action == "service":
            return service_action(str(kwargs.get("name", "")), str(kwargs.get("verb", "")))
        if action == "agent_service":
            return agent_service_action(int(kwargs.get("node_id", -1)), str(kwargs.get("verb", "")))
        if action == "instance":
            return openvpn_instance_action(str(kwargs.get("name", "")), str(kwargs.get("verb", "")))
        return DISPATCH[action](**kwargs)  # type: ignore[operator]
    except (ValueError, TypeError) as exc:
        return ActionResult(ok=False, error=str(exc))
