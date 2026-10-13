"""Build OpenVPN client configuration text.

Pure functions that render ``.ovpn`` files from validated node/CA material.
No shell is involved; the resulting bytes are written by the client.
"""

from __future__ import annotations

from app.core.validators import validate_cipher, validate_port, validate_protocol

DEFAULT_CA_NOTE = "# CA certificate is embedded below by the privileged helper."


def remote_line(host: str, port: int, protocol: str) -> str:
    protocol = validate_protocol(protocol)
    port = validate_port(port)
    return f"remote {host} {port} {protocol}"


def build_client_config(
    *,
    host: str,
    port: int,
    protocol: str,
    cipher: str,
    ca_cert: str | None = None,
    tls_auth: str | None = None,
    extra: str | None = None,
) -> str:
    cipher = validate_cipher(cipher)
    lines: list[str] = [
        "client",
        "dev tun",
        "proto " + validate_protocol(protocol),
        remote_line(host, port, protocol),
        "resolv-retry infinite",
        "nobind",
        "persist-key",
        "persist-tun",
        "remote-cert-tls server",
        f"cipher {cipher}",
        "auth SHA256",
        "verb 3",
    ]
    if tls_auth:
        lines.append("key-direction 1")
    if extra:
        lines.extend(line for line in extra.splitlines() if line.strip())
    if ca_cert:
        lines.extend(["<ca>", ca_cert.strip(), "</ca>"])
    if tls_auth:
        lines.extend(["<tls-auth>", tls_auth.strip(), "</tls-auth>"])
    return "\n".join(lines) + "\n"
