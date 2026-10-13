"""Strict input validation helpers.

Every value that is later handed to a subprocess or the filesystem MUST be
validated here first. Shell commands are never built by string concatenation;
this module is the single source of truth for accepted formats.
"""

from __future__ import annotations

import ipaddress
import re

USERNAME_RE = re.compile(r"^[a-zA-Z0-9_.-]{3,32}$")
NODE_NAME_RE = re.compile(r"^[a-zA-Z0-9_.-]{2,32}$")
INSTANCE_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]{1,32}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
API_KEY_PREFIX_RE = re.compile(r"^[a-f0-9]{8}$")
COMMON_NAME_RE = re.compile(r"^[a-zA-Z0-9_.-]{1,64}$")
# Hostnames for TLS (Let's Encrypt): labels of alphanumerics and hyphens.
DOMAIN_RE = re.compile(
    r"^(?=.{1,253}$)([a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)(\."
    r"[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)+$"
)
PROTOCOLS = {"udp", "tcp"}
CIPHERS = {"AES-256-GCM", "AES-128-GCM", "CHACHA20-POLY1305"}


class ValidationError(ValueError):
    """Raised when an input value does not match the allow-listed format."""


def validate_username(value: str) -> str:
    if not isinstance(value, str) or not USERNAME_RE.match(value):
        raise ValidationError("username must match ^[a-zA-Z0-9_.-]{3,32}$")
    return value


def validate_common_name(value: str) -> str:
    if not isinstance(value, str) or not COMMON_NAME_RE.match(value):
        raise ValidationError("invalid common name")
    return value


def validate_node_name(value: str) -> str:
    if not isinstance(value, str) or not NODE_NAME_RE.match(value):
        raise ValidationError("node name must match ^[a-zA-Z0-9_.-]{2,32}$")
    return value


def validate_instance_name(value: str) -> str:
    """Validate an OpenVPN instance name (``openvpn-server@<name>``)."""

    if not isinstance(value, str) or not INSTANCE_NAME_RE.match(value):
        raise ValidationError("instance name must match ^[a-zA-Z0-9_-]{1,32}$")
    return value


def validate_email(value: str) -> str:
    if not isinstance(value, str) or not EMAIL_RE.match(value):
        raise ValidationError("invalid email address")
    return value


def validate_protocol(value: str) -> str:
    value = (value or "").lower()
    if value not in PROTOCOLS:
        raise ValidationError(f"protocol must be one of {sorted(PROTOCOLS)}")
    return value


def validate_cipher(value: str) -> str:
    if value not in CIPHERS:
        raise ValidationError(f"cipher must be one of {sorted(CIPHERS)}")
    return value


def validate_port(value: int | str) -> int:
    try:
        port = int(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError("port must be an integer") from exc
    if not 1 <= port <= 65535:
        raise ValidationError("port must be between 1 and 65535")
    return port


def validate_ip_or_host(value: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 255:
        raise ValidationError("invalid host")
    try:
        ipaddress.ip_address(value)
        return value
    except ValueError:
        pass
    if re.match(r"^[a-zA-Z0-9.-]+$", value):
        return value
    raise ValidationError("invalid host")


def validate_hostname(value: str) -> str:
    return validate_ip_or_host(value)


def validate_domain(value: str) -> str:
    """Validate a public DNS name suitable for a TLS certificate."""

    if not isinstance(value, str) or not DOMAIN_RE.match(value.strip()):
        raise ValidationError("invalid domain name")
    return value.strip().lower()


def validate_pem_certificate(value: str) -> str:
    """Ensure a certificate looks like a PEM X.509 block."""

    if not isinstance(value, str) or "BEGIN CERTIFICATE" not in value:
        raise ValidationError("invalid certificate (expected PEM)")
    if "END CERTIFICATE" not in value:
        raise ValidationError("invalid certificate (unterminated PEM)")
    if len(value) > 512 * 1024:
        raise ValidationError("certificate too large")
    return value


def validate_pem_key(value: str) -> str:
    """Ensure a private key looks like a PEM key block."""

    if not isinstance(value, str) or "BEGIN" not in value or "PRIVATE KEY" not in value:
        raise ValidationError("invalid private key (expected PEM)")
    if len(value) > 512 * 1024:
        raise ValidationError("private key too large")
    return value
