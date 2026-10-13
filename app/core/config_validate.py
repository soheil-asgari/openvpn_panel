"""Validation for OpenVPN ``server.conf`` text.

A pragmatic, line-oriented validator: it does not attempt to be a full OpenVPN
parser, but it rejects the dangerous constructs (shell escapes, plugin paths,
unbalanced inline blocks, unknown top-level directives) that would make a config
unsafe to deploy. Used by the server config editor before restart.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Directives the panel considers safe to appear in a ``server.conf``.
ALLOWED_DIRECTIVES = {
    "port",
    "proto",
    "dev",
    "dev-type",
    "topology",
    "server",
    "server-bridge",
    "push",
    "client-config-dir",
    "keepalive",
    "persist-key",
    "persist-tun",
    "user",
    "group",
    "ca",
    "cert",
    "key",
    "dh",
    "tls-auth",
    "tls-crypt",
    "tls-crypt-v2",
    "tls-server",
    "tls-version-min",
    "tls-cipher",
    "cipher",
    "data-ciphers",
    "auth",
    "tun-mtu",
    "mssfix",
    "fragment",
    "comp-lzo",
    "compress",
    "allow-compression",
    "duplicate-cn",
    "max-clients",
    "ifconfig-pool-persist",
    "management",
    "verb",
    "mute",
    "status",
    "log",
    "log-append",
    "explicit-exit-notify",
    "reneg-sec",
    "auth-nocache",
    "username-as-common-name",
    "auth-user-pass-verify",
    "script-security",
    "client-cert-not-required",
    "verify-client-cert",
    "remote-cert-tls",
    "crl-verify",
    "local",
    "multihome",
    "socket-flags",
    "fast-io",
    "sndbuf",
    "rcvbuf",
    "noswap",
    "mlock",
    "chroot",
    "tmp-dir",
}

# Directives that must never appear in a panel-managed config.
FORBIDDEN_DIRECTIVES = {
    "plugin",
    "up",
    "down",
    "route-up",
    "ipchange",
    "client-connect",
    "client-disconnect",
    "learn-address",
    "auth-user-pass-verify-script",
}

INLINE_BLOCKS = {"ca", "cert", "key", "dh", "tls-auth", "tls-crypt", "tls-crypt-v2"}
LINE_RE = re.compile(r"^\s*([A-Za-z][A-Za-z0-9_-]*)\b(.*)$")

FORBIDDEN_PATTERNS = (
    re.compile(r"[;&|`$]"),  # shell metacharacters in a value
    re.compile(r"\.\./"),  # path traversal
    re.compile(r"/etc/shadow"),
)


@dataclass(slots=True)
class ValidationResult:
    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    directives: set[str] = field(default_factory=set)

    @property
    def summary(self) -> str:
        if self.ok:
            return "valid" if not self.warnings else "; ".join(self.warnings)
        return "; ".join(self.errors)


def validate_server_conf(content: str) -> ValidationResult:
    result = ValidationResult(ok=True)
    if content is None or not content.strip():
        result.ok = False
        result.errors.append("configuration is empty")
        return result
    if len(content.encode("utf-8")) > 512 * 1024:
        result.ok = False
        result.errors.append("configuration is too large")
        return result

    in_block: str | None = None
    for number, raw in enumerate(content.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith(";"):
            continue

        if in_block is not None:
            if line == f"</{in_block}>":
                in_block = None
            elif line.startswith("</"):
                result.ok = False
                result.errors.append(f"line {number}: unexpected closing tag {line!r}")
                in_block = None
            continue

        # Inline block openers (``<ca>``) are checked before generic directives.
        if line.startswith("<") and line.endswith(">"):
            tag = line.strip("<>")
            if tag not in INLINE_BLOCKS:
                result.ok = False
                result.errors.append(f"line {number}: unsupported inline block <{tag}>")
            else:
                in_block = tag
            continue

        match = LINE_RE.match(line)
        if not match:
            result.ok = False
            result.errors.append(f"line {number}: not a directive")
            continue
        directive = match.group(1).lower()
        argument = match.group(2).strip()

        if directive in FORBIDDEN_DIRECTIVES:
            result.ok = False
            result.errors.append(f"line {number}: directive {directive!r} is not allowed")
            continue
        if directive not in ALLOWED_DIRECTIVES:
            result.warnings.append(f"line {number}: unknown directive {directive!r}")

        for pattern in FORBIDDEN_PATTERNS:
            if pattern.search(argument):
                result.ok = False
                result.errors.append(f"line {number}: value contains a forbidden character or path")
                break

        result.directives.add(directive)

    if in_block is not None:
        result.ok = False
        result.errors.append(f"unterminated inline block <{in_block}>")

    if "proto" not in result.directives:
        result.warnings.append("no 'proto' directive")
    if "port" not in result.directives and "local" not in result.directives:
        result.warnings.append("no 'port' directive")
    return result


def quick_apply(content: str, timeout: int = 5) -> ValidationResult:
    """Optionally syntax-check by asking OpenVPN to parse the config.

    Runs ``openvpn --config <tmp> --test`` with a short timeout. On hosts where
    the binary is unavailable the static validation result is returned.
    """

    import shutil
    import subprocess
    import tempfile

    static = validate_server_conf(content)
    if not static.ok:
        return static

    import os

    binary = shutil.which("openvpn") or (
        "/usr/sbin/openvpn" if os.path.exists("/usr/sbin/openvpn") else None
    )
    if binary is None:
        return static
    with tempfile.NamedTemporaryFile("w", suffix=".conf", delete=True) as tmp:
        tmp.write(content)
        tmp.flush()
        try:
            proc = subprocess.run(  # noqa: S603 - fixed argv, shell disabled
                [binary, "--config", tmp.name, "--test"],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return static
    if proc.returncode != 0:
        static.ok = False
        static.errors.append("openvpn --test rejected the configuration")
        if proc.stderr:
            static.errors.append(proc.stderr.strip().splitlines()[-1])
    return static
