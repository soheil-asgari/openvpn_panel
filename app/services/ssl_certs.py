"""Panel TLS certificate management.

Personal-use friendly: the panel records its intent (Let's Encrypt or custom
PEM) in the database and exposes the exact commands an admin runs, rather than
executing privileged ACME tooling itself. Custom certificates can be uploaded
and are stored with mode 600 in the config directory.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.validators import (
    ValidationError,
    validate_domain,
    validate_pem_certificate,
    validate_pem_key,
)
from app.services import settings as settings_service

LETSENCRYPT_DIR = Path("/etc/letsencrypt/live")

KEYS = {
    "ssl.mode": "none",
    "ssl.domain": "",
    "ssl.email": "",
    "ssl.cert_path": "",
    "ssl.key_path": "",
    "ssl.cert_pem": "",
    "ssl.key_pem": "",
    "ssl.updated_at": "",
    "ssl.auto_renew": "true",
}

DEFAULTS = dict(KEYS)
# Merge into the settings service allow-list.
settings_service.DEFAULTS.update(DEFAULTS)


class SslError(ValueError):
    """Raised for invalid TLS configuration."""


@dataclass(slots=True)
class SslStatus:
    mode: str
    domain: str
    email: str
    cert_path: str
    key_path: str
    has_custom_cert: bool
    auto_renew: bool
    updated_at: str

    @property
    def enabled(self) -> bool:
        return self.mode in {"letsencrypt", "custom"}


def _cert_dir() -> Path:
    directory = get_settings().config_dir / "tls"
    return directory


def status(db: Session) -> SslStatus:
    return SslStatus(
        mode=settings_service.get(db, "ssl.mode") or "none",
        domain=settings_service.get(db, "ssl.domain") or "",
        email=settings_service.get(db, "ssl.email") or "",
        cert_path=settings_service.get(db, "ssl.cert_path") or "",
        key_path=settings_service.get(db, "ssl.key_path") or "",
        has_custom_cert=bool(settings_service.get(db, "ssl.cert_pem")),
        auto_renew=(settings_service.get(db, "ssl.auto_renew") or "true") == "true",
        updated_at=settings_service.get(db, "ssl.updated_at") or "",
    )


def configure_letsencrypt(
    db: Session, *, domain: str, email: str, auto_renew: bool = True
) -> SslStatus:
    try:
        domain = validate_domain(domain)
    except ValidationError as exc:
        raise SslError(str(exc)) from exc
    live = LETSENCRYPT_DIR / domain
    settings_service.set_many(
        db,
        {
            "ssl.mode": "letsencrypt",
            "ssl.domain": domain,
            "ssl.email": email.strip(),
            "ssl.cert_path": str(live / "fullchain.pem"),
            "ssl.key_path": str(live / "privkey.pem"),
            "ssl.auto_renew": "true" if auto_renew else "false",
            "ssl.updated_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    return status(db)


def configure_custom(db: Session, *, cert_pem: str, key_pem: str) -> SslStatus:
    try:
        cert_pem = validate_pem_certificate(cert_pem)
        key_pem = validate_pem_key(key_pem)
    except ValidationError as exc:
        raise SslError(str(exc)) from exc
    directory = _cert_dir()
    directory.mkdir(parents=True, exist_ok=True)
    cert_path = directory / "panel.crt"
    key_path = directory / "panel.key"
    cert_path.write_text(cert_pem, encoding="utf-8")
    key_path.write_text(key_pem, encoding="utf-8")
    import os

    os.chmod(cert_path, 0o600)
    os.chmod(key_path, 0o600)
    settings_service.set_many(
        db,
        {
            "ssl.mode": "custom",
            "ssl.cert_path": str(cert_path),
            "ssl.key_path": str(key_path),
            "ssl.cert_pem": cert_pem,
            "ssl.key_pem": key_pem,
            "ssl.updated_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    return status(db)


def disable(db: Session) -> SslStatus:
    settings_service.set_many(
        db,
        {
            "ssl.mode": "none",
            "ssl.updated_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    return status(db)


def letsencrypt_commands(domain: str) -> dict[str, str]:
    """Return the shell commands an admin runs to obtain/renew a certificate."""

    try:
        domain = validate_domain(domain)
    except ValidationError as exc:
        raise SslError(str(exc)) from exc
    return {
        "obtain": (
            f"certbot certonly --nginx -d {domain} "
            f"--non-interactive --agree-tos --keep-until-expiring"
        ),
        "renew": "certbot renew --quiet --deploy-hook 'systemctl reload ovpn-panel'",
        "timer": "systemctl enable --now certbot.timer",
    }


def renewal_service_unit(domain: str) -> str:
    """A systemd timer unit body for automatic renewal (for documentation)."""

    return (
        "[Unit]\n"
        "Description=Renew the panel's Let's Encrypt certificate\n\n"
        "[Timer]\n"
        "OnCalendar=daily\n"
        "Persistent=true\n\n"
        "[Install]\n"
        "WantedBy=timers.target\n"
    )
