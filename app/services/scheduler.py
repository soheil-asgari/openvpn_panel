"""Background jobs scheduled with APScheduler (thread pool).

Jobs:
* sync node status / online clients through the management interface
* poll live traffic and enforce per-user quota/expiry (auto-disable)
* expire VPN users whose validity ended
* auto-delete users that have been expired for N days
* record usage samples for charts
* purge expired panel sessions
* send notifications (node up/down, quota nearly finished, expiring soon)
* run scheduled encrypted backups and optionally upload them
"""

from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler

from app.config import get_settings
from app.db import session_scope

logger = logging.getLogger(__name__)
_scheduler: BackgroundScheduler | None = None


def sync_nodes() -> None:
    """Collect health/metrics from agents and reconcile the retry queue."""

    from app.services import node_sync
    from app.services import nodes as nodes_service

    with session_scope() as db:
        for node in nodes_service.list_nodes(db, active_only=True):
            if node.agent_url:
                node_sync.collect_metrics(db, node)
                # Bring an offline node back in sync once it is reachable again.
                if node.status.value == "online":
                    node_sync.reconcile_node(db, node)
        node_sync.run_pending(db)


def notify_node_status() -> None:
    """Emit down/up alerts for nodes whose status changed since the last run."""

    from app.services import notifications
    from app.services import settings as settings_service

    with session_scope() as db:
        from app.services import nodes as nodes_service

        for node in nodes_service.list_nodes(db, active_only=True):
            marker_key = f"notify.node_status.{node.id}"
            previous = settings_service.get(db, marker_key) or ""
            current = node.status.value
            if current not in {"online", "offline"} or current == previous:
                continue
            if current == "offline":
                notifications.notify_node_down(db, node.name)
            elif previous == "offline":
                notifications.notify_node_up(db, node.name)
            settings_service.set_value(db, marker_key, current)


def user_alerts() -> None:
    from app.services import notifications

    with session_scope() as db:
        notifications.scan_user_alerts(db)


def scheduled_backup() -> None:
    """Create an encrypted backup on the configured schedule (opt-in)."""

    import secrets

    from app.services import backup
    from app.services import settings as settings_service

    with session_scope() as db:
        schedule = settings_service.get(db, "backup.schedule") or "off"
        if schedule not in {"daily", "weekly"}:
            return
        password = secrets.token_urlsafe(24)
        try:
            result = backup.create_backup(password)
        except backup.BackupError as exc:
            logger.warning("scheduled backup failed: %s", exc)
            return
        settings_service.set_value(db, "backup.last_run", result.created_at.isoformat())
        upload = settings_service.get(db, "backup.upload") or "none"
        ok = True
        if upload == "telegram":
            ok = backup.upload_telegram(
                result.payload,
                result.filename,
                settings_service.get(db, "backup.telegram_token") or "",
                settings_service.get(db, "backup.telegram_chat") or "",
            )
        elif upload == "webhook":
            ok = backup.upload_webhook(
                result.payload,
                result.filename,
                settings_service.get(db, "backup.webhook_url") or "",
            )
        from app.services import notifications

        notifications.notify_backup(db, result.filename, ok)
        # Persist the archive for local retention.
        target = get_settings().backups_dir
        target.mkdir(parents=True, exist_ok=True)
        (target / result.filename).write_bytes(result.payload)


def _apply_online_state(db, node_id: int, online: set[str]) -> None:  # noqa: ANN001
    from sqlalchemy import select

    from app.models.vpn_user import VpnUser

    users = list(db.scalars(select(VpnUser).where(VpnUser.node_id == node_id)))
    for user in users:
        user.is_online = user.username in online


def poll_traffic() -> None:
    """Poll live client counters, update active sessions and enforce limits."""

    from sqlalchemy import select

    from app.models.vpn_session import VpnSession
    from app.models.vpn_user import VpnUser
    from app.services import vpn_sessions as vpn_sessions_service
    from app.services import vpn_users as vpn_users_service
    from app.services.ovpn import ManagementError, ManagementInterface

    try:
        clients = ManagementInterface().connected_clients()
    except ManagementError as exc:
        logger.debug("traffic poll skipped: %s", exc)
        return

    with session_scope() as db:
        for client in clients:
            user = db.scalar(select(VpnUser).where(VpnUser.username == client.common_name))
            if user is None:
                continue
            user.is_online = True
            session = db.scalar(
                select(VpnSession)
                .where(
                    VpnSession.vpn_user_id == user.id,
                    VpnSession.is_active.is_(True),
                )
                .order_by(VpnSession.connected_at.desc())
            )
            if session is not None:
                session.bytes_in = max(0, int(client.bytes_received))
                session.bytes_out = max(0, int(client.bytes_sent))
            if vpn_users_service.enforce_limits(db, user):
                # Quota/expiry exceeded: force the connection closed.
                vpn_sessions_service.terminate_active_sessions(db, user)
                logger.info("user %s auto-disabled (limit reached)", user.username)


def expire_users() -> None:
    from sqlalchemy import select

    from app.models.vpn_user import VpnUser, VpnUserStatus
    from app.services import vpn_users as vpn_users_service

    with session_scope() as db:
        for user in db.scalars(select(VpnUser).where(VpnUser.status == VpnUserStatus.ACTIVE)):
            if vpn_users_service.enforce_limits(db, user):
                logger.info("user %s expired", user.username)


def auto_delete_expired() -> None:
    """Delete users that expired more than N days ago (N=0 disables)."""

    days = get_settings().auto_delete_expired_days
    if days <= 0:
        return

    from datetime import timedelta

    from sqlalchemy import select

    from app.models.base import utcnow
    from app.models.vpn_user import VpnUser, VpnUserStatus

    cutoff = utcnow() - timedelta(days=days)
    with session_scope() as db:
        stmt = select(VpnUser).where(
            VpnUser.status == VpnUserStatus.EXPIRED, VpnUser.expired_at.isnot(None)
        )
        for user in list(db.scalars(stmt)):
            expired_at = user.expired_at
            if expired_at is not None and expired_at.tzinfo is None:
                from datetime import timezone

                expired_at = expired_at.replace(tzinfo=timezone.utc)
            if expired_at is not None and expired_at < cutoff:
                db.delete(user)
                logger.info("auto-deleted expired user %s", user.username)


def record_usage() -> None:
    from sqlalchemy import select

    from app.models.usage import UsageSample
    from app.models.vpn_user import VpnUser

    with session_scope() as db:
        for user in db.scalars(select(VpnUser).where(VpnUser.is_online.is_(True))):
            db.add(
                UsageSample(
                    vpn_user_id=user.id,
                    bytes_in=user.bytes_in,
                    bytes_out=user.bytes_out,
                )
            )


def purge_sessions() -> None:
    from app.services import sessions as sessions_service

    with session_scope() as db:
        sessions_service.purge_expired(db)


def sync_protocols() -> None:
    """Collect per-protocol usage/online state and enforce protocol limits."""

    from app.services import protocols_service

    with session_scope() as db:
        try:
            protocols_service.sync_all(db)
        except Exception as exc:  # noqa: BLE001 - one bad backend must not stop the rest
            logger.warning("protocol sync failed: %s", exc)


def tunnel_health() -> None:
    """Probe every active tunnel end-to-end and self-heal failures."""

    from app.services import tunnels as tunnels_service

    with session_scope() as db:
        for tunnel in tunnels_service.list_tunnels(db, active_only=True):
            if tunnel.status.value in {"uninstalled", "pending"}:
                continue
            try:
                tunnels_service.probe_tunnel(db, tunnel)
                tunnels_service.maybe_self_heal(db, tunnel)
            except Exception as exc:  # noqa: BLE001 - one bad tunnel must not stop the rest
                logger.warning("tunnel health probe failed for %s: %s", tunnel.name, exc)


def start_scheduler() -> BackgroundScheduler:
    global _scheduler
    if _scheduler is not None:
        return _scheduler
    settings = get_settings()
    interval = settings.ovpn_status_interval
    poll_interval = settings.ovpn_traffic_poll_interval
    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(sync_nodes, "interval", seconds=interval, id="sync_nodes")
    scheduler.add_job(poll_traffic, "interval", seconds=poll_interval, id="poll_traffic")
    scheduler.add_job(expire_users, "interval", seconds=60, id="expire_users")
    scheduler.add_job(auto_delete_expired, "interval", seconds=3600, id="auto_delete_expired")
    scheduler.add_job(record_usage, "interval", seconds=300, id="record_usage")
    scheduler.add_job(purge_sessions, "interval", seconds=600, id="purge_sessions")
    scheduler.add_job(notify_node_status, "interval", seconds=interval, id="notify_node_status")
    scheduler.add_job(user_alerts, "interval", seconds=900, id="user_alerts")
    scheduler.add_job(scheduled_backup, "interval", seconds=3600, id="scheduled_backup")
    scheduler.add_job(tunnel_health, "interval", seconds=interval * 2, id="tunnel_health")
    scheduler.add_job(sync_protocols, "interval", seconds=poll_interval, id="sync_protocols")
    scheduler.start()
    _scheduler = scheduler
    logger.info("scheduler started")
    return scheduler


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
