"""Protocol orchestration service (Phases 12-15).

Sits between the panel models and the :mod:`app.services.protocols` backends:

* CRUD + install/healthcheck for :class:`ProtocolInstance` rows.
* Per-user credential allocation and enable/disable flags
  (:class:`ProtocolUser`), pushing the state to the owning node.
* Traffic collection and online-session reconciliation back into the VPN user.
* Subscription link aggregation across all enabled protocols.

Every backend call goes through an :class:`~app.services.protocols.base.Executor`
chosen from the instance's node, so the same code path works for the local panel
host (via the privileged helper) and for remote nodes (via the agent API).
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.security import hash_password  # noqa: F401 - re-exported for callers
from app.core.validators import ValidationError, validate_node_name, validate_port
from app.models.base import utcnow
from app.models.node import Node
from app.models.protocol import (
    ProtocolInstance,
    ProtocolInstanceStatus,
    ProtocolUser,
)
from app.models.vpn_user import VpnUser, VpnUserStatus
from app.services import protocols
from app.services.protocols import wireguard as wg_backend
from app.services.protocols.base import (
    HealthResult,
    InstallResult,
    ProtocolError,
    Usage,
    UserConfig,
    UserSpec,
)
from app.services.protocols.executor import get_executor
from app.services.protocols.singbox import generate_reality_keypair


class ProtocolServiceError(ValueError):
    """Domain error for protocol instance/user operations."""


@dataclass(slots=True)
class InstallOutcome:
    ok: bool
    version: str | None = None
    error: str | None = None
    log: list[str] | None = None


# --------------------------------------------------------------------------- #
# Instance CRUD
# --------------------------------------------------------------------------- #
def list_instances(
    db: Session, *, kind: str | None = None, node_id: int | None = None
) -> list[ProtocolInstance]:
    stmt = select(ProtocolInstance).order_by(ProtocolInstance.kind, ProtocolInstance.name)
    if kind:
        stmt = stmt.where(ProtocolInstance.kind == kind)
    if node_id is not None:
        stmt = stmt.where(ProtocolInstance.node_id == node_id)
    return list(db.scalars(stmt))


def get(db: Session, instance_id: int) -> ProtocolInstance | None:
    return db.get(ProtocolInstance, instance_id)


def get_by_name(
    db: Session, kind: str, name: str, *, node_id: int | None = None
) -> ProtocolInstance | None:
    stmt = select(ProtocolInstance).where(
        ProtocolInstance.kind == kind, ProtocolInstance.name == name
    )
    if node_id is not None:
        stmt = stmt.where(ProtocolInstance.node_id == node_id)
    return db.scalar(stmt)


def backend_with_node(instance: ProtocolInstance, node: Node | None):  # noqa: ANN201
    """Instantiate the backend bound to the executor for ``node``."""

    return protocols.get_backend(instance, get_executor(node))


def create_instance(
    db: Session,
    *,
    kind: str,
    name: str,
    node_id: int | None = None,
    port: int = 0,
    address: str | None = None,
    subnet: str | None = None,
    dns: str | None = None,
    mtu: int | None = None,
    host: str | None = None,
    settings: dict | None = None,
    is_default: bool = False,
    owner_id: int | None = None,
) -> ProtocolInstance:
    kind = (kind or "").lower()
    try:
        protocols.get_backend_class(kind)
    except ProtocolError as exc:
        raise ProtocolServiceError(str(exc)) from exc
    try:
        validate_node_name(name)
    except ValidationError as exc:
        raise ProtocolServiceError("instance name must match ^[a-zA-Z0-9_.-]{2,32}$") from exc
    if port:
        validate_port(port)
    if get_by_name(db, kind, name, node_id=node_id) is not None:
        raise ProtocolServiceError("an instance with this name already exists")

    settings = dict(settings or {})
    instance = ProtocolInstance(
        kind=kind,
        name=name,
        node_id=node_id,
        host=host,
        port=port or _default_port(kind),
        address=address,
        subnet=subnet,
        dns=dns,
        mtu=mtu,
        is_default=is_default,
        owner_id=owner_id,
        status=ProtocolInstanceStatus.PENDING,
    )
    instance.settings_dict = _prepare_settings(kind, settings)
    if kind == "wireguard" and not instance.subnet:
        instance.subnet = _allocate_subnet(db)
    if kind == "wireguard" and not instance.address:
        instance.address = _first_host(instance.subnet or "10.66.0.0/24")
    db.add(instance)
    db.flush()
    return instance


def update_instance(db: Session, instance: ProtocolInstance, **fields: object) -> ProtocolInstance:
    if "port" in fields and fields["port"]:
        instance.port = validate_port(fields["port"])  # type: ignore[arg-type]
    for attr in ("address", "subnet", "dns", "host"):
        if attr in fields and fields[attr] is not None:
            setattr(instance, attr, str(fields[attr]))
    if "mtu" in fields and fields["mtu"]:
        instance.mtu = int(fields["mtu"])  # type: ignore[arg-type]
    if "is_active" in fields and fields["is_active"] is not None:
        instance.is_active = bool(fields["is_active"])
    if "is_default" in fields and fields["is_default"] is not None:
        instance.is_default = bool(fields["is_default"])
    if "settings" in fields and isinstance(fields["settings"], dict):
        merged = instance.settings_dict
        merged.update(fields["settings"])  # type: ignore[arg-type]
        instance.settings_dict = merged
    db.flush()
    return instance


def delete_instance(db: Session, instance: ProtocolInstance) -> None:
    for row in db.scalars(
        select(ProtocolUser).where(ProtocolUser.protocol_instance_id == instance.id)
    ):
        db.delete(row)
    db.delete(instance)
    db.flush()


def install_instance(
    db: Session, instance: ProtocolInstance, node: Node | None = None
) -> InstallOutcome:
    backend = backend_with_node(instance, node)
    try:
        result: InstallResult = backend.install()
    except (ProtocolError, NotImplementedError) as exc:
        instance.status = ProtocolInstanceStatus.ERROR
        instance.last_error = str(exc)
        db.flush()
        return InstallOutcome(ok=False, error=str(exc))
    instance.version = result.version
    instance.status = ProtocolInstanceStatus.ACTIVE if result.ok else ProtocolInstanceStatus.ERROR
    instance.last_error = None if result.ok else result.error
    db.flush()
    return InstallOutcome(ok=result.ok, version=result.version, error=result.error, log=result.log)


def healthcheck_instance(
    db: Session, instance: ProtocolInstance, node: Node | None = None
) -> HealthResult:
    backend = backend_with_node(instance, node)
    try:
        result = backend.healthcheck()
    except (ProtocolError, NotImplementedError) as exc:
        result = HealthResult(ok=False, detail=str(exc))
    instance.status = (
        ProtocolInstanceStatus.ACTIVE if result.ok else ProtocolInstanceStatus.DEGRADED
    )
    if result.version:
        instance.version = result.version
    instance.last_error = None if result.ok else result.detail
    instance.last_seen_at = utcnow()
    db.flush()
    return result


# --------------------------------------------------------------------------- #
# Per-user protocol state
# --------------------------------------------------------------------------- #
def protocol_users(db: Session, vpn_user_id: int) -> list[ProtocolUser]:
    return list(db.scalars(select(ProtocolUser).where(ProtocolUser.vpn_user_id == vpn_user_id)))


def enabled_kinds(db: Session, vpn_user_id: int) -> set[str]:
    stmt = select(ProtocolUser.kind).where(
        ProtocolUser.vpn_user_id == vpn_user_id, ProtocolUser.enabled.is_(True)
    )
    return set(db.scalars(stmt))


def _credentials_for(db: Session, instance: ProtocolInstance, vpn_user: VpnUser) -> dict:
    kind = instance.kind
    if kind == "wireguard":
        used = _used_addresses(db, instance.id)
        address = wg_backend.first_free_address(instance.subnet or "10.66.0.0/24", used)
        private = wg_backend.generate_private_key()
        public = wg_backend.public_from_private(private)
        if not public:
            raise ProtocolServiceError(
                "cannot derive WireGuard public key (install 'cryptography')"
            )
        return {
            "private_key": private,
            "public_key": public,
            "address": address,
            "preshared_key": wg_backend.generate_private_key(),
        }
    if kind == "singbox":
        from app.services.protocols.singbox import new_password, new_uuid

        inbound = str(instance.settings_dict.get("inbound_type") or "vless")
        if inbound in {"vmess", "vless"}:
            return {"uuid": new_uuid()}
        if inbound == "shadowsocks":
            return {"password": new_password(32)}
        return {"password": new_password(24)}
    # Password based protocols (ocserv, l2tp, openvpn fallback).
    from app.core.security import generate_password

    return {"password": generate_password(20)}


def _used_addresses(db: Session, instance_id: int) -> set[str]:
    used: set[str] = set()
    for row in db.scalars(
        select(ProtocolUser).where(ProtocolUser.protocol_instance_id == instance_id)
    ):
        address = row.credentials_dict.get("address")
        if address:
            used.add(str(address))
    return used


def ensure_protocol_user(
    db: Session,
    *,
    vpn_user: VpnUser,
    instance: ProtocolInstance,
    node: Node | None = None,
    push: bool = True,
) -> ProtocolUser:
    """Create (or refresh) the per-protocol credential for ``vpn_user``."""

    row = db.scalar(
        select(ProtocolUser).where(
            ProtocolUser.vpn_user_id == vpn_user.id,
            ProtocolUser.protocol_instance_id == instance.id,
        )
    )
    if row is None:
        row = ProtocolUser(
            vpn_user_id=vpn_user.id,
            protocol_instance_id=instance.id,
            kind=instance.kind,
            enabled=True,
        )
        row.credentials_dict = _credentials_for(db, instance, vpn_user)
        db.add(row)
        db.flush()

    if push:
        _push(db, row, instance, vpn_user, node=node, op="create")
    return row


def set_enabled(
    db: Session,
    *,
    vpn_user: VpnUser,
    instance: ProtocolInstance,
    enabled: bool,
    node: Node | None = None,
    push: bool = True,
) -> ProtocolUser:
    """Toggle a user's flag for one protocol (creating the credential if new)."""

    row = ensure_protocol_user(db, vpn_user=vpn_user, instance=instance, node=node, push=False)
    row.enabled = enabled
    db.flush()
    if push:
        _push(db, row, instance, vpn_user, node=node, op="create" if enabled else "disable")
    return row


def delete_protocol_user(
    db: Session, *, vpn_user: VpnUser, instance: ProtocolInstance, node: Node | None = None
) -> None:
    row = db.scalar(
        select(ProtocolUser).where(
            ProtocolUser.vpn_user_id == vpn_user.id,
            ProtocolUser.protocol_instance_id == instance.id,
        )
    )
    if row is None:
        return
    backend = backend_with_node(instance, node)
    with contextlib.suppress(ProtocolError, NotImplementedError):
        backend.delete_user(vpn_user.username)
    db.delete(row)
    db.flush()


def sync_user(db: Session, vpn_user: VpnUser, node: Node | None = None) -> int:
    """Push the user's limits to every protocol they are enabled on."""

    count = 0
    for row in protocol_users(db, vpn_user.id):
        instance = db.get(ProtocolInstance, row.protocol_instance_id)
        if instance is None:
            continue
        _push(db, row, instance, vpn_user, node=node, op="limits")
        count += 1
    return count


def apply_limits(db: Session, vpn_user: VpnUser, node: Node | None = None) -> int:
    return sync_user(db, vpn_user, node=node)


def kick_user(db: Session, vpn_user: VpnUser, node: Node | None = None) -> int:
    """Terminate live sessions for the user on every enabled protocol."""

    killed = 0
    for row in protocol_users(db, vpn_user.id):
        instance = db.get(ProtocolInstance, row.protocol_instance_id)
        if instance is None:
            continue
        backend = backend_with_node(instance, node)
        with contextlib.suppress(ProtocolError, NotImplementedError):
            backend.disable(vpn_user.username)
            killed += 1
        row.is_online = False
    db.flush()
    return killed


def _spec_for(vpn_user: VpnUser, row: ProtocolUser, instance: ProtocolInstance) -> UserSpec:
    creds = row.credentials_dict
    return UserSpec(
        username=vpn_user.username,
        password=str(creds.get("password") or "") or None,
        data_limit_bytes=vpn_user.data_limit_bytes,
        used_bytes=vpn_user.used_bytes,
        speed_limit_kbps=vpn_user.speed_limit_kbps,
        max_connections=vpn_user.max_connections,
        expires_at=vpn_user.effective_expiry,
        enabled=row.enabled and vpn_user.status == VpnUserStatus.ACTIVE,
        extra=creds,
    )


def replay_operation(
    db: Session,
    row: ProtocolUser,
    instance: ProtocolInstance,
    vpn_user: VpnUser,
    *,
    node: Node | None,
    op: str,
) -> None:
    """Public entry point used by the node task queue to replay an operation."""

    _push(db, row, instance, vpn_user, node=node, op=op)


def _push(
    db: Session,
    row: ProtocolUser,
    instance: ProtocolInstance,
    vpn_user: VpnUser,
    *,
    node: Node | None,
    op: str,
) -> None:
    """Apply one operation through the backend, queuing it if the node is down."""

    backend = backend_with_node(instance, node)
    spec = _spec_for(vpn_user, row, instance)
    try:
        if op == "create":
            backend.create_user(spec)
        elif op == "disable":
            backend.disable(vpn_user.username)
        elif op == "delete":
            backend.delete_user(vpn_user.username)
        else:
            backend.set_limits(spec)
        return
    except (ProtocolError, NotImplementedError) as exc:
        _enqueue(db, instance=instance, vpn_user=vpn_user, op=op, error=str(exc))


def _enqueue(
    db: Session, *, instance: ProtocolInstance, vpn_user: VpnUser, op: str, error: str
) -> None:
    from app.services import node_sync

    node_sync.enqueue(
        db,
        node_id=instance.node_id,
        kind="protocol_user",
        payload={
            "instance_id": instance.id,
            "kind": instance.kind,
            "username": vpn_user.username,
            "op": op,
            "error": error[:200],
        },
    )


# --------------------------------------------------------------------------- #
# Traffic / online reconciliation
# --------------------------------------------------------------------------- #
def collect_usage(db: Session, instance: ProtocolInstance, node: Node | None = None) -> int:
    """Pull per-user counters from the protocol and update DB aggregates."""

    backend = backend_with_node(instance, node)
    try:
        usages = backend.usage()
    except (ProtocolError, NotImplementedError):
        return 0
    if not usages:
        return 0

    by_credential = _credential_index(db, instance.id)
    updated = 0
    for usage in usages:
        row = by_credential.get(usage.username)
        if row is None:
            continue
        _apply_usage(db, row, usage)
        updated += 1
    db.flush()
    return updated


def _credential_index(db: Session, instance_id: int) -> dict[str, ProtocolUser]:
    """Map a protocol-side identifier (pubkey/uuid/name) to a ProtocolUser."""

    index: dict[str, ProtocolUser] = {}
    for row in db.scalars(
        select(ProtocolUser).where(ProtocolUser.protocol_instance_id == instance_id)
    ):
        creds = row.credentials_dict
        for key in ("public_key", "uuid", "password", "name"):
            value = creds.get(key)
            if value:
                index[str(value)] = row
    return index


def _apply_usage(db: Session, row: ProtocolUser, usage: Usage) -> None:
    delta_in = max(0, usage.bytes_in - row.bytes_in)
    delta_out = max(0, usage.bytes_out - row.bytes_out)
    row.bytes_in = max(row.bytes_in, usage.bytes_in)
    row.bytes_out = max(row.bytes_out, usage.bytes_out)
    vpn_user = db.get(VpnUser, row.vpn_user_id)
    if vpn_user is not None and (delta_in or delta_out):
        vpn_user.bytes_in += delta_in
        vpn_user.bytes_out += delta_out


def reconcile_online(db: Session, instance: ProtocolInstance, node: Node | None = None) -> int:
    backend = backend_with_node(instance, node)
    try:
        sessions = backend.online()
    except (ProtocolError, NotImplementedError):
        return 0
    index = _credential_index(db, instance.id)
    online_rows: set[int] = set()
    for session in sessions:
        row = index.get(session.username)
        if row is None:
            continue
        row.is_online = True
        row.last_connected_at = utcnow()
        online_rows.add(row.id)
    for row in db.scalars(
        select(ProtocolUser).where(ProtocolUser.protocol_instance_id == instance.id)
    ):
        if row.id not in online_rows:
            row.is_online = False
    db.flush()
    return len(online_rows)


def sync_all(db: Session) -> dict[str, int]:
    """Collect usage + online state for every active instance (scheduler)."""

    summary = {"instances": 0, "usage": 0, "online": 0}
    for instance in list_instances(db):
        if not instance.is_active:
            continue
        node = db.get(Node, instance.node_id) if instance.node_id else None
        summary["instances"] += 1
        summary["usage"] += collect_usage(db, instance, node)
        summary["online"] += reconcile_online(db, instance, node)
    return summary


def usage_by_kind(db: Session, vpn_user_id: int) -> dict[str, int]:
    total = func.sum(ProtocolUser.bytes_in + ProtocolUser.bytes_out)
    stmt = (
        select(ProtocolUser.kind, total)
        .where(ProtocolUser.vpn_user_id == vpn_user_id)
        .group_by(ProtocolUser.kind)
    )
    return {row[0]: int(row[1] or 0) for row in db.execute(stmt)}


# --------------------------------------------------------------------------- #
# Client configs / subscription links
# --------------------------------------------------------------------------- #
def configs_for_user(db: Session, vpn_user: VpnUser, node: Node | None = None) -> list[UserConfig]:
    """Render a client config / share link for every enabled protocol."""

    configs: list[UserConfig] = []
    for row in protocol_users(db, vpn_user.id):
        if not row.enabled:
            continue
        instance = db.get(ProtocolInstance, row.protocol_instance_id)
        if instance is None:
            continue
        backend = backend_with_node(instance, node)
        spec = _spec_for(vpn_user, row, instance)
        spec.extra.setdefault("host", instance.host or (node.host if node else ""))
        spec.extra.setdefault("port", instance.port)
        if instance.kind == "wireguard":
            spec.extra["server_public_key"] = _wg_server_public_key(db, instance, node)
            spec.extra["endpoint_host"] = instance.host or (node.host if node else "")
            spec.extra["endpoint_port"] = instance.port
        try:
            configs.append(backend.config_for_user(spec))
        except (ProtocolError, NotImplementedError):
            continue
    return configs


def aggregated_subscription(configs: list[UserConfig]) -> str:
    """Return a newline-joined subscription body of share links."""

    return "\n".join(c.uri for c in configs if c.uri)


def _wg_server_public_key(db: Session, instance: ProtocolInstance, node: Node | None) -> str:
    backend = backend_with_node(instance, node)
    with contextlib.suppress(ProtocolError, NotImplementedError, AttributeError):
        return backend.server_public_key()  # type: ignore[attr-defined]
    return ""


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _default_port(kind: str) -> int:
    return {
        "openvpn": 1194,
        "wireguard": 51820,
        "ocserv": 443,
        "l2tp": 1701,
        "singbox": 443,
    }.get(kind, 0)


def _prepare_settings(kind: str, settings: dict) -> dict:
    if kind == "singbox":
        inbound = str(settings.get("inbound_type") or "vless").lower()
        security = str(settings.get("security") or "reality").lower()
        settings["inbound_type"] = inbound
        settings["security"] = security
        if inbound == "vless" and security == "reality":
            if not settings.get("reality_private_key"):
                private, public = generate_reality_keypair()
                settings["reality_private_key"] = private
                settings["reality_public_key"] = public
            if not settings.get("reality_short_id"):
                import secrets

                settings["reality_short_id"] = secrets.token_hex(4)
    return settings


def _allocate_subnet(db: Session) -> str:
    """Pick the next ``10.66.N.0/24`` subnet not already in use."""

    used = {
        inst.subnet
        for inst in db.scalars(select(ProtocolInstance).where(ProtocolInstance.kind == "wireguard"))
        if inst.subnet
    }
    for third in range(0, 256):
        candidate = f"10.66.{third}.0/24"
        if candidate not in used:
            return candidate
    raise ProtocolServiceError("no free WireGuard subnet available")


def _first_host(cidr: str) -> str:
    hosts = wg_backend.subnet_hosts(cidr)
    return hosts[0] if hosts else "10.66.0.1"


def ensure_default_openvpn_instance(db: Session, node: Node) -> ProtocolInstance:
    """Guarantee an OpenVPN instance row exists for a node (Phase 12 bridge)."""

    instance = get_by_name(db, "openvpn", node.name, node_id=node.id)
    if instance is not None:
        return instance
    return create_instance(
        db,
        kind="openvpn",
        name=node.name,
        node_id=node.id,
        host=node.host,
        port=node.port,
        is_default=True,
        settings={"cipher": node.cipher, "protocol": node.protocol},
    )


def now() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- #
# High-level lifecycle hooks used by the VPN user service
# --------------------------------------------------------------------------- #
def default_instances(db: Session, node_id: int | None) -> list[ProtocolInstance]:
    """Active, default instances for a node (fallback: all active instances)."""

    stmt = select(ProtocolInstance).where(ProtocolInstance.is_active.is_(True))
    if node_id is not None:
        stmt = stmt.where(ProtocolInstance.node_id == node_id)
    rows = list(db.scalars(stmt))
    defaults = [r for r in rows if r.is_default]
    return defaults or rows


def provision_for_user(db: Session, vpn_user: VpnUser, *, push: bool = True) -> int:
    """Enable the user on the default protocols of their node."""

    created = 0
    for instance in default_instances(db, vpn_user.node_id):
        ensure_protocol_user(db, vpn_user=vpn_user, instance=instance, push=push)
        created += 1
    return created


def deprovision_user(db: Session, vpn_user: VpnUser, node: Node | None = None) -> int:
    """Remove the user from every protocol (on user deletion)."""

    removed = 0
    for row in protocol_users(db, vpn_user.id):
        instance = db.get(ProtocolInstance, row.protocol_instance_id)
        if instance is None:
            continue
        backend = backend_with_node(instance, node)
        with contextlib.suppress(ProtocolError, NotImplementedError):
            backend.delete_user(vpn_user.username)
        db.delete(row)
        removed += 1
    db.flush()
    return removed


def set_user_enabled(
    db: Session, vpn_user: VpnUser, enabled: bool, node: Node | None = None
) -> int:
    """Enable/disable the user across every protocol instance they belong to."""

    affected = 0
    for row in protocol_users(db, vpn_user.id):
        instance = db.get(ProtocolInstance, row.protocol_instance_id)
        if instance is None:
            continue
        row.enabled = enabled
        _push(db, row, instance, vpn_user, node=node, op="create" if enabled else "disable")
        affected += 1
    db.flush()
    return affected
