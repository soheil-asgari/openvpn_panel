"""VPN connection-session lifecycle (open on connect, close on disconnect)."""

from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models.base import utcnow
from app.models.traffic_log import TrafficLog
from app.models.vpn_session import VpnSession
from app.models.vpn_user import VpnUser


def open_session(
    db: Session,
    *,
    vpn_user: VpnUser,
    common_name: str,
    trusted_ip: str | None = None,
    node_id: int | None = None,
) -> VpnSession:
    """Record a new connection for ``vpn_user`` and mark them online."""

    now = utcnow()
    session = VpnSession(
        vpn_user_id=vpn_user.id,
        node_id=node_id if node_id is not None else vpn_user.node_id,
        common_name=common_name,
        trusted_ip=trusted_ip,
        is_active=True,
        connected_at=now,
    )
    db.add(session)

    if vpn_user.first_connected_at is None:
        vpn_user.first_connected_at = now

    vpn_user.is_online = True
    vpn_user.connected_ip = trusted_ip
    vpn_user.last_connected_at = now
    db.flush()
    return session


def close_session(
    db: Session,
    *,
    vpn_user: VpnUser,
    common_name: str,
    bytes_received: int = 0,
    bytes_sent: int = 0,
) -> VpnSession | None:
    """Close the most recent open session and account its final traffic.

    Adds a :class:`TrafficLog` row, updates the aggregate counters on the VPN
    user and reflects the online flag. Returns the closed session, if any.
    """

    bytes_in = max(0, int(bytes_received))
    bytes_out = max(0, int(bytes_sent))

    session = db.scalar(
        select(VpnSession)
        .where(
            VpnSession.vpn_user_id == vpn_user.id,
            VpnSession.is_active.is_(True),
        )
        .order_by(VpnSession.connected_at.desc())
    )
    if session is not None:
        session.is_active = False
        session.disconnected_at = utcnow()
        session.bytes_in = bytes_in
        session.bytes_out = bytes_out

    vpn_user.bytes_in += bytes_in
    vpn_user.bytes_out += bytes_out

    db.add(
        TrafficLog(
            vpn_user_id=vpn_user.id,
            node_id=session.node_id if session is not None else vpn_user.node_id,
            bytes_in=bytes_in,
            bytes_out=bytes_out,
        )
    )

    if not has_other_active(db, vpn_user.id, exclude_id=session.id if session else None):
        vpn_user.is_online = False
        vpn_user.connected_ip = None

    from app.services import vpn_users as vpn_users_service

    vpn_users_service.enforce_limits(db, vpn_user)

    db.flush()
    return session


def account_traffic(db: Session, *, vpn_user: VpnUser, bytes_in: int, bytes_out: int) -> None:
    """Add live counters without closing the session (polling job)."""

    delta_in = max(0, int(bytes_in))
    delta_out = max(0, int(bytes_out))
    vpn_user.bytes_in += delta_in
    vpn_user.bytes_out += delta_out
    db.add(
        TrafficLog(
            vpn_user_id=vpn_user.id,
            node_id=vpn_user.node_id,
            bytes_in=delta_in,
            bytes_out=delta_out,
        )
    )
    db.flush()


def has_other_active(db: Session, vpn_user_id: int, *, exclude_id: int | None = None) -> bool:
    stmt = select(VpnSession.id).where(
        VpnSession.vpn_user_id == vpn_user_id,
        VpnSession.is_active.is_(True),
    )
    if exclude_id is not None:
        stmt = stmt.where(VpnSession.id != exclude_id)
    return db.scalar(stmt.limit(1)) is not None


def close_all_for_user(db: Session, vpn_user_id: int) -> int:
    """Force-close every open session (e.g. after an admin disconnect)."""

    result = db.execute(
        update(VpnSession)
        .where(VpnSession.vpn_user_id == vpn_user_id, VpnSession.is_active.is_(True))
        .values(is_active=False, disconnected_at=utcnow())
    )
    db.flush()
    return int(result.rowcount or 0)


def active_sessions(db: Session, vpn_user_id: int) -> list[VpnSession]:
    return list(
        db.scalars(
            select(VpnSession)
            .where(VpnSession.vpn_user_id == vpn_user_id, VpnSession.is_active.is_(True))
            .order_by(VpnSession.connected_at.desc())
        )
    )


def list_online_users(db: Session, *, owner_id: int | None = None) -> list[VpnUser]:
    stmt = select(VpnUser).where(VpnUser.is_online.is_(True)).order_by(VpnUser.username)
    if owner_id is not None:
        stmt = stmt.where(VpnUser.owner_id == owner_id)
    return list(db.scalars(stmt))


def terminate_active_sessions(db: Session, vpn_user: VpnUser) -> int:
    """Kill live OpenVPN connections and close their DB sessions.

    The kill is best-effort: the management socket may be unavailable in tests
    or when the node is offline. Returns the number of sessions closed.
    """

    import contextlib

    from app.services.ovpn import ManagementError, ManagementInterface

    cn = vpn_user.common_name or vpn_user.username
    # Best-effort: the socket may be unavailable (node offline, non-POSIX host
    # in tests). Any connection/parse failure must not abort the DB update.
    with contextlib.suppress(ManagementError, OSError, AttributeError):
        ManagementInterface().kill(cn)

    closed = close_all_for_user(db, vpn_user.id)
    vpn_user.is_online = False
    vpn_user.connected_ip = None
    db.flush()
    return closed
