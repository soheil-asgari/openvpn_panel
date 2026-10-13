"""Runtime key/value settings stored in the database.

Only a small, fixed allow-list of keys is exposed; unknown keys are ignored on
read (they fall back to the supplied default) so callers never need to guard
against missing rows.
"""

from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.setting import Setting

# Branding keys used by the public subscription page.
BRANDING_KEYS = (
    "branding.name",
    "branding.logo_url",
    "branding.support_url",
    "branding.footer",
)

# General panel settings editable at runtime.
PANEL_KEYS = (
    "panel.title",
    "panel.language",
    "panel.theme",
    "panel.port",
    # Opt-in update check. Disabled by default: the panel contacts the network
    # ONLY when `update.enabled` is true. No telemetry is ever sent.
    "update.enabled",
    "update.repo",
)

# Backup scheduling / remote upload.
BACKUP_KEYS = (
    "backup.schedule",
    "backup.upload",
    "backup.telegram_token",
    "backup.telegram_chat",
    "backup.webhook_url",
    "backup.last_run",
)

DEFAULTS = {
    "branding.name": "OpenVPN Web Panel",
    "branding.logo_url": "",
    "branding.support_url": "",
    "branding.footer": "",
    "panel.title": "OpenVPN Web Panel",
    "panel.language": "en",
    "panel.theme": "system",
    "panel.port": "8080",
    "update.enabled": "false",
    "update.repo": "",
    "backup.schedule": "off",  # off | daily | weekly
    "backup.upload": "none",  # none | telegram | webhook
    "backup.telegram_token": "",
    "backup.telegram_chat": "",
    "backup.webhook_url": "",
    "backup.last_run": "",
}


def get(db: Session, key: str, default: str | None = None) -> str | None:
    row = db.scalar(select(Setting).where(Setting.key == key))
    if row is None or row.value is None:
        return DEFAULTS.get(key, default)
    return row.value


def set_value(db: Session, key: str, value: str | None) -> Setting:
    row = db.scalar(select(Setting).where(Setting.key == key))
    if row is None:
        row = Setting(key=key, value=value)
        db.add(row)
    else:
        row.value = value
    db.flush()
    return row


def set_many(db: Session, values: dict[str, str | None]) -> None:
    for key, value in values.items():
        if key not in DEFAULTS:
            continue
        set_value(db, key, value)


def get_branding(db: Session) -> dict[str, str]:
    branding: dict[str, str] = {}
    for key in BRANDING_KEYS:
        branding[key] = get(db, key) or ""
    if not branding.get("branding.name"):
        branding["branding.name"] = DEFAULTS["branding.name"]
    return branding


def clear(db: Session, key: str) -> None:
    db.execute(delete(Setting).where(Setting.key == key))
    db.flush()
