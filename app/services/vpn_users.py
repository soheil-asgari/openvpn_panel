"""VPN end-user service: create, update, enable/disable and quota checks."""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.security import generate_password, hash_password
from app.core.validators import ValidationError, validate_username
from app.models.user import Role, User
from app.models.vpn_user import VpnUser, VpnUserStatus

# Sortable columns exposed to the UI/API (whitelist -> column).
SORT_COLUMNS = {
    "username": VpnUser.username,
    "status": VpnUser.status,
    "created_at": VpnUser.created_at,
    "expires_at": VpnUser.expires_at,
    "bytes_in": VpnUser.bytes_in,
    "bytes_out": VpnUser.bytes_out,
    "last_connected_at": VpnUser.last_connected_at,
}

STATUS_FILTERS = {"active", "disabled", "expired", "online", "offline"}


class VpnUserError(ValueError):
    """Domain error for VPN user operations."""


@dataclass(slots=True)
class NewCredential:
    """A freshly created credential (plaintext password returned once)."""

    user: VpnUser
    password: str


@dataclass(slots=True)
class FilterSpec:
    """Filter/sort/pagination parameters for listings and exports."""

    owner_id: int | None = None
    node_id: int | None = None
    search: str | None = None
    status: str | None = None
    sort_by: str = "username"
    sort_dir: str = "asc"
    limit: int = 100
    offset: int = 0
    user_ids: list[int] = field(default_factory=list)


def _enforce_node_allowed(db: Session, owner: User | None, node_id: int | None) -> None:
    """Reject node assignment when the owner's allow-list forbids it."""

    if owner is None or owner.role != Role.RESELLER or node_id is None:
        return
    from app.services import resellers as resellers_service

    try:
        resellers_service.enforce_node_allowed(db, owner, node_id)
    except resellers_service.ResellerError as exc:
        raise VpnUserError(str(exc)) from exc


def get(db: Session, vpn_user_id: int) -> VpnUser | None:
    return db.get(VpnUser, vpn_user_id)


def get_by_username(db: Session, username: str) -> VpnUser | None:
    return db.scalar(select(VpnUser).where(VpnUser.username == username))


def _apply_filters(stmt, spec: FilterSpec):  # noqa: ANN001
    if spec.owner_id is not None:
        stmt = stmt.where(VpnUser.owner_id == spec.owner_id)
    if spec.node_id is not None:
        stmt = stmt.where(VpnUser.node_id == spec.node_id)
    if spec.search:
        pattern = f"%{spec.search}%"
        stmt = stmt.where(or_(VpnUser.username.ilike(pattern), VpnUser.common_name.ilike(pattern)))
    if spec.status:
        status = spec.status.lower()
        if status == "online":
            stmt = stmt.where(VpnUser.is_online.is_(True))
        elif status == "offline":
            stmt = stmt.where(VpnUser.is_online.is_(False))
        elif status in {s.value for s in VpnUserStatus}:
            stmt = stmt.where(VpnUser.status == VpnUserStatus(status))
    if spec.user_ids:
        stmt = stmt.where(VpnUser.id.in_(spec.user_ids))
    return stmt


def _apply_sort(stmt, spec: FilterSpec):  # noqa: ANN001
    column = SORT_COLUMNS.get(spec.sort_by, VpnUser.username)
    if spec.sort_dir.lower() == "desc":
        return stmt.order_by(column.desc(), VpnUser.id.desc())
    return stmt.order_by(column.asc(), VpnUser.id.asc())


def list_vpn_users(
    db: Session,
    *,
    owner_id: int | None = None,
    node_id: int | None = None,
    search: str | None = None,
    status: str | None = None,
    sort_by: str = "username",
    sort_dir: str = "asc",
    limit: int = 100,
    offset: int = 0,
) -> list[VpnUser]:
    spec = FilterSpec(
        owner_id=owner_id,
        node_id=node_id,
        search=search,
        status=status,
        sort_by=sort_by,
        sort_dir=sort_dir,
        limit=limit,
        offset=offset,
    )
    stmt = _apply_sort(_apply_filters(select(VpnUser), spec), spec)
    stmt = stmt.limit(limit).offset(offset)
    return list(db.scalars(stmt))


def count_vpn_users(
    db: Session,
    *,
    owner_id: int | None = None,
    search: str | None = None,
    status: str | None = None,
    node_id: int | None = None,
) -> int:
    spec = FilterSpec(owner_id=owner_id, node_id=node_id, search=search, status=status)
    stmt = _apply_filters(select(func.count(VpnUser.id)), spec)
    return int(db.scalar(stmt) or 0)


def export_csv(db: Session, *, spec: FilterSpec | None = None) -> str:
    """Return a CSV document with the (filtered) user listing."""

    spec = spec or FilterSpec(limit=100000)
    spec.limit = spec.limit or 100000
    stmt = _apply_sort(_apply_filters(select(VpnUser), spec), spec).limit(spec.limit)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "id",
            "username",
            "status",
            "online",
            "used_bytes",
            "bytes_in",
            "bytes_out",
            "data_limit_bytes",
            "expires_at",
            "first_connected_at",
            "last_connected_at",
            "node_id",
            "owner_id",
        ]
    )
    for user in db.scalars(stmt):
        writer.writerow(
            [
                user.id,
                user.username,
                user.status.value,
                "yes" if user.is_online else "no",
                user.used_bytes,
                user.bytes_in,
                user.bytes_out,
                user.data_limit_bytes if user.data_limit_bytes is not None else "",
                _iso(user.effective_expiry),
                _iso(user.first_connected_at),
                _iso(user.last_connected_at),
                user.node_id if user.node_id is not None else "",
                user.owner_id if user.owner_id is not None else "",
            ]
        )
    return buffer.getvalue()


def _iso(value: datetime | None) -> str:
    return value.isoformat() if value else ""


def _enforce_quota(db: Session, owner: User | None, additional: int = 1) -> None:
    """Enforce reseller limits (active account, user count, aggregate data)."""

    if owner is None or owner.role != Role.RESELLER:
        return
    from app.services import resellers as resellers_service

    try:
        resellers_service.enforce_can_create(db, owner, additional=additional)
    except resellers_service.ResellerError as exc:
        raise VpnUserError(str(exc)) from exc


def create_vpn_user(
    db: Session,
    *,
    username: str,
    password: str,
    owner: User | None = None,
    node_id: int | None = None,
    expires_at: datetime | None = None,
    expire_days: int | None = None,
    data_limit_bytes: int | None = None,
    max_connections: int | None = None,
    speed_limit_kbps: int | None = None,
    common_name: str | None = None,
) -> VpnUser:
    validate_username(username)
    if get_by_username(db, username) is not None:
        raise VpnUserError("username already exists")
    _enforce_quota(db, owner)
    _enforce_node_allowed(db, owner, node_id)
    user = _build_vpn_user(
        username=username,
        password=password,
        owner=owner,
        node_id=node_id,
        expires_at=expires_at,
        expire_days=expire_days,
        data_limit_bytes=data_limit_bytes,
        max_connections=max_connections,
        speed_limit_kbps=speed_limit_kbps,
        common_name=common_name,
    )
    db.add(user)
    db.flush()
    _provision_protocols(db, user)
    return user


def _provision_protocols(db: Session, user: VpnUser) -> None:
    """Enable a new user on their node's default protocol instances.

    Best-effort: a missing/offline node must never fail user creation — the
    operation is queued for retry by the protocol service.
    """

    try:
        from app.services import protocols_service

        protocols_service.provision_for_user(db, user)
    except Exception:  # noqa: BLE001 - protocol layer must not block user CRUD
        pass


def _build_vpn_user(
    *,
    username: str,
    password: str,
    owner: User | None,
    node_id: int | None,
    expires_at: datetime | None,
    expire_days: int | None,
    data_limit_bytes: int | None,
    max_connections: int | None,
    speed_limit_kbps: int | None,
    common_name: str | None,
) -> VpnUser:
    return VpnUser(
        username=username,
        password_hash=hash_password(password),
        common_name=common_name or username,
        owner_id=owner.id if owner else None,
        node_id=node_id,
        expires_at=_normalise_dt(expires_at),
        expire_days=expire_days,
        data_limit_bytes=data_limit_bytes,
        max_connections=max_connections,
        speed_limit_kbps=speed_limit_kbps,
        status=VpnUserStatus.ACTIVE,
    )


def _normalise_dt(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def bulk_create(
    db: Session,
    *,
    prefix: str,
    count: int,
    owner: User | None = None,
    node_id: int | None = None,
    expires_at: datetime | None = None,
    expire_days: int | None = None,
    data_limit_bytes: int | None = None,
    max_connections: int | None = None,
    speed_limit_kbps: int | None = None,
    password_length: int = 20,
) -> list[NewCredential]:
    """Create ``count`` users named ``<prefix><n>`` with random passwords."""

    _enforce_node_allowed(db, owner, node_id)
    if count < 1:
        raise VpnUserError("count must be at least 1")
    if count > 1000:
        raise VpnUserError("count must not exceed 1000")
    base = prefix.rstrip("-_.")
    if not base:
        raise VpnUserError("prefix is required")

    _enforce_quota(db, owner, additional=count)

    created: list[NewCredential] = []
    width = max(2, len(str(count)))
    for index in range(1, count + 1):
        username = f"{base}{str(index).zfill(width)}"
        try:
            validate_username(username)
        except ValidationError as exc:
            raise VpnUserError(f"generated username invalid: {username}") from exc
        if get_by_username(db, username) is not None:
            raise VpnUserError(f"username already exists: {username}")
        password = generate_password(password_length)
        user = _build_vpn_user(
            username=username,
            password=password,
            owner=owner,
            node_id=node_id,
            expires_at=expires_at,
            expire_days=expire_days,
            data_limit_bytes=data_limit_bytes,
            max_connections=max_connections,
            speed_limit_kbps=speed_limit_kbps,
            common_name=None,
        )
        db.add(user)
        created.append(NewCredential(user=user, password=password))
    db.flush()
    return created


def bulk_delete(db: Session, user_ids: list[int], *, owner_id: int | None = None) -> int:
    users = _load_scoped(db, user_ids, owner_id=owner_id)
    for user in users:
        _deprovision_protocols(db, user)
        db.delete(user)
    db.flush()
    return len(users)


def _deprovision_protocols(db: Session, user: VpnUser) -> None:
    try:
        from app.services import protocols_service

        protocols_service.deprovision_user(db, user)
    except Exception:  # noqa: BLE001
        pass


def bulk_set_status(
    db: Session, user_ids: list[int], status: VpnUserStatus, *, owner_id: int | None = None
) -> list[VpnUser]:
    users = _load_scoped(db, user_ids, owner_id=owner_id)
    for user in users:
        user.status = status
        if status == VpnUserStatus.DISABLED:
            user.is_online = False
            user.connected_ip = None
    db.flush()
    return users


def bulk_extend(
    db: Session,
    user_ids: list[int],
    *,
    days: int | None = None,
    expires_at: datetime | None = None,
    owner_id: int | None = None,
) -> list[VpnUser]:
    """Extend expiry by ``days`` or set an absolute ``expires_at``."""

    if days is None and expires_at is None:
        raise VpnUserError("either days or expires_at is required")
    users = _load_scoped(db, user_ids, owner_id=owner_id)
    absolute = _normalise_dt(expires_at)
    now = datetime.now(timezone.utc)
    for user in users:
        if absolute is not None:
            user.expires_at = absolute
            user.expire_days = None
        else:
            base = user.expires_at or now
            base = _normalise_dt(base) or now
            if base < now:
                base = now
            user.expires_at = base + timedelta(days=int(days or 0))
        if user.status == VpnUserStatus.EXPIRED and not user.is_expired:
            user.status = VpnUserStatus.ACTIVE
    db.flush()
    return users


def bulk_reset_traffic(db: Session, user_ids: list[int], *, owner_id: int | None = None) -> int:
    users = _load_scoped(db, user_ids, owner_id=owner_id)
    for user in users:
        user.bytes_in = 0
        user.bytes_out = 0
    db.flush()
    return len(users)


def _load_scoped(db: Session, user_ids: list[int], *, owner_id: int | None) -> list[VpnUser]:
    if not user_ids:
        return []
    stmt = select(VpnUser).where(VpnUser.id.in_(user_ids))
    if owner_id is not None:
        stmt = stmt.where(VpnUser.owner_id == owner_id)
    return list(db.scalars(stmt))


def set_status(db: Session, vpn_user: VpnUser, status: VpnUserStatus) -> VpnUser:
    vpn_user.status = status
    if status == VpnUserStatus.ACTIVE:
        vpn_user.expired_at = None
    if status == VpnUserStatus.DISABLED:
        vpn_user.is_online = False
        vpn_user.connected_ip = None
    db.flush()
    _apply_protocol_state(db, vpn_user, enabled=status == VpnUserStatus.ACTIVE)
    return vpn_user


def _apply_protocol_state(db: Session, user: VpnUser, *, enabled: bool) -> None:
    try:
        from app.services import protocols_service

        protocols_service.set_user_enabled(db, user, enabled)
    except Exception:  # noqa: BLE001
        pass


def set_password(db: Session, vpn_user: VpnUser, password: str) -> str:
    if not password or len(password) < 8:
        raise VpnUserError("password must be at least 8 characters")
    vpn_user.password_hash = hash_password(password)
    db.flush()
    return password


def reset_password(db: Session, vpn_user: VpnUser, *, length: int = 20) -> str:
    """Generate and store a new random password, returning it in plaintext."""

    return set_password(db, vpn_user, generate_password(length))


def reset_usage(db: Session, vpn_user: VpnUser) -> None:
    vpn_user.bytes_in = 0
    vpn_user.bytes_out = 0
    db.flush()


def delete_vpn_user(db: Session, vpn_user: VpnUser) -> None:
    _deprovision_protocols(db, vpn_user)
    db.delete(vpn_user)
    db.flush()


def is_connectable(vpn_user: VpnUser, *, owner_active: bool = True) -> bool:
    """Return whether a user may currently connect (ignoring concurrency).

    ``owner_active`` reflects the owning reseller's account state; a disabled or
    expired sub-admin blocks all of their users server-side.
    """

    if not owner_active:
        return False
    if vpn_user.status != VpnUserStatus.ACTIVE:
        return False
    return not (vpn_user.is_expired or vpn_user.quota_exceeded)


def owner_active(db: Session, vpn_user: VpnUser) -> bool:
    """Whether the owning panel user (reseller) may still grant connections."""

    if vpn_user.owner_id is None:
        return True
    from app.models.user import User as PanelUser
    from app.services import resellers as resellers_service

    owner = db.get(PanelUser, vpn_user.owner_id)
    if owner is None:
        return True
    return resellers_service.is_active(owner)


def effective_connection_limit(db: Session, vpn_user: VpnUser, default_limit: int) -> int:
    """Resolve the per-user concurrency limit (falls back to ``default_limit``)."""

    if vpn_user.max_connections is not None:
        return vpn_user.max_connections
    return default_limit


def mark_first_connection(db: Session, vpn_user: VpnUser, when: datetime | None = None) -> None:
    """Record the start of the relative expiry window on first connect."""

    if vpn_user.first_connected_at is None:
        vpn_user.first_connected_at = when or datetime.now(timezone.utc)
        db.flush()


def count_active_sessions(db: Session, vpn_user_id: int) -> int:
    """Number of currently open OpenVPN sessions for a VPN user."""

    from app.models.vpn_session import VpnSession

    stmt = select(func.count(VpnSession.id)).where(
        VpnSession.vpn_user_id == vpn_user_id,
        VpnSession.is_active.is_(True),
    )
    return int(db.scalar(stmt) or 0)


def exceeds_concurrent_limit(db: Session, vpn_user: VpnUser, limit: int) -> bool:
    """Return whether adding one connection would exceed ``limit``.

    ``limit`` of ``0`` (or less) means unlimited.
    """

    if limit <= 0:
        return False
    return count_active_sessions(db, vpn_user.id) >= limit


def enforce_limits(db: Session, vpn_user: VpnUser) -> bool:
    """Mark the user expired if its quota/expiry is exceeded.

    Returns ``True`` when the status changed.
    """

    if vpn_user.status == VpnUserStatus.ACTIVE and (vpn_user.is_expired or vpn_user.quota_exceeded):
        vpn_user.status = VpnUserStatus.EXPIRED
        if vpn_user.expired_at is None:
            vpn_user.expired_at = datetime.now(timezone.utc)
        vpn_user.is_online = False
        return True
    return False
