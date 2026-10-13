"""Optional notifications via Telegram and generic webhooks.

Alerts are best-effort: a delivery failure is logged and never breaks the
request or scheduled job that triggered it. Events are de-duplicated per user
using a small marker stored in the settings table so a quota alert does not fire
on every poll.
"""

from __future__ import annotations

import json
import logging

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.units import format_size
from app.models.base import utcnow
from app.models.user import Role, User
from app.models.vpn_user import VpnUser
from app.services import settings as settings_service

logger = logging.getLogger(__name__)

EVENT_KEYS = ("notify.telegram_token", "notify.telegram_chat", "notify.webhook_url")
settings_service.DEFAULTS.update(dict.fromkeys(EVENT_KEYS, ""))

QUOTA_THRESHOLD = 0.9  # 90 %
EXPIRY_SOON_HOURS = 48


def _channels(db: Session) -> dict[str, str]:
    return {
        "telegram_token": settings_service.get(db, "notify.telegram_token") or "",
        "telegram_chat": settings_service.get(db, "notify.telegram_chat") or "",
        "webhook_url": settings_service.get(db, "notify.webhook_url") or "",
    }


def send(db: Session, text: str, *, extra: dict | None = None) -> dict[str, bool]:
    """Deliver ``text`` to all configured channels. Returns per-channel status."""

    channels = _channels(db)
    results = {"telegram": False, "webhook": False}
    payload = {"text": text, **(extra or {})}

    token = channels["telegram_token"]
    chat = channels["telegram_chat"]
    if token and chat:
        try:
            with httpx.Client(timeout=10) as client:
                response = client.post(
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    json={"chat_id": chat, "text": text, "parse_mode": "HTML"},
                )
            results["telegram"] = response.status_code == 200
        except httpx.HTTPError as exc:
            logger.warning("telegram notify failed: %s", exc)

    url = channels["webhook_url"]
    if url.startswith(("http://", "https://")):
        try:
            with httpx.Client(timeout=10) as client:
                response = client.post(url, json=payload)
            results["webhook"] = response.status_code < 400
        except httpx.HTTPError as exc:
            logger.warning("webhook notify failed: %s", exc)
    return results


def _mark_once(db: Session, key: str) -> bool:
    """Return ``True`` the first time ``key`` is seen (then remember it)."""

    if settings_service.get(db, key):
        return False
    settings_service.set_value(db, key, utcnow().isoformat())
    return True


def notify_login(db: Session, user: User, ip: str | None) -> None:
    send(
        db,
        f"🔐 Panel login: <b>{user.username}</b> ({user.role.value}) from {ip or 'unknown'}",
        extra={"event": "login", "user": user.username},
    )


def notify_node_down(db: Session, node_name: str) -> None:
    send(
        db,
        f"🔴 Node <b>{node_name}</b> is offline",
        extra={"event": "node_down", "node": node_name},
    )


def notify_node_up(db: Session, node_name: str) -> None:
    send(
        db,
        f"🟢 Node <b>{node_name}</b> is back online",
        extra={"event": "node_up", "node": node_name},
    )


def notify_tunnel(db: Session, tunnel_name: str, message: str, *, event: str) -> None:
    """Alert about a tunnel going down/being self-healed."""

    send(
        db,
        f"🚇 Tunnel <b>{tunnel_name}</b>: {message}",
        extra={"event": event, "tunnel": tunnel_name},
    )


def notify_backup(db: Session, filename: str, ok: bool) -> None:
    icon = "✅" if ok else "⚠️"
    send(db, f"{icon} Backup {filename}", extra={"event": "backup", "ok": ok})


def scan_user_alerts(db: Session) -> int:
    """Emit quota/expiry alerts for users crossing the thresholds."""

    sent = 0
    for vpn_user in db.scalars(select(VpnUser)):
        if _quota_alert(db, vpn_user):
            sent += 1
        if _expiry_alert(db, vpn_user):
            sent += 1
    db.flush()
    return sent


def _quota_alert(db: Session, vpn_user: VpnUser) -> bool:
    limit = vpn_user.data_limit_bytes
    if not limit or vpn_user.used_bytes < limit * QUOTA_THRESHOLD:
        return False
    key = f"notify.sent.quota.{vpn_user.id}.{utcnow().strftime('%Y%m')}"
    if not _mark_once(db, key):
        return False
    send(
        db,
        f"📶 <b>{vpn_user.username}</b> used {format_size(vpn_user.used_bytes)} "
        f"of {format_size(limit)}",
        extra={"event": "quota", "username": vpn_user.username},
    )
    return True


def _expiry_alert(db: Session, vpn_user: VpnUser) -> bool:
    expiry = vpn_user.effective_expiry
    if expiry is None or vpn_user.is_expired:
        return False
    remaining = (expiry - utcnow()).total_seconds()
    if remaining > EXPIRY_SOON_HOURS * 3600:
        return False
    key = f"notify.sent.expiry.{vpn_user.id}.{expiry.date().isoformat()}"
    if not _mark_once(db, key):
        return False
    send(
        db,
        f"⏳ <b>{vpn_user.username}</b> expires at {expiry.isoformat()}",
        extra={"event": "expiry", "username": vpn_user.username},
    )
    return True


def notify_admins(db: Session, text: str, *, extra: dict | None = None) -> None:
    """Send to the global channels (intended for admin-level events)."""

    send(db, text, extra=extra)


def reseller_recipients(db: Session) -> list[User]:
    return list(
        db.scalars(select(User).where(User.role == Role.RESELLER, User.is_active.is_(True)))
    )


def to_json_event(text: str, **fields: object) -> str:
    return json.dumps({"text": text, **fields})
