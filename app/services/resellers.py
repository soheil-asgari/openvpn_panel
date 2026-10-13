"""Reseller (sub-admin) service: CRUD, limits and node allow-lists.

Enforcement is centralised here so every entry point (web, API, hooks) shares
identical server-side checks.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.models.reseller import ResellerAllowedNode
from app.models.user import Role, User
from app.models.vpn_user import VpnUser


class ResellerError(ValueError):
    """Domain error for reseller operations."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_aware(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def is_expired(user: User) -> bool:
    """Whether a reseller account has passed its expiry instant."""

    expires = _as_aware(user.expires_at)
    return expires is not None and expires < _now()


def is_active(user: User) -> bool:
    return bool(user.is_active) and not is_expired(user)


# --------------------------------------------------------------------------- #
# CRUD
# --------------------------------------------------------------------------- #
def list_resellers(db: Session, *, parent_id: int | None = None) -> list[User]:
    stmt = select(User).where(User.role == Role.RESELLER).order_by(User.username)
    if parent_id is not None:
        stmt = stmt.where(User.parent_id == parent_id)
    return list(db.scalars(stmt))


def get(db: Session, user_id: int) -> User | None:
    user = db.get(User, user_id)
    if user is None or user.role != Role.RESELLER:
        return None
    return user


def create_reseller(
    db: Session,
    *,
    username: str,
    password: str,
    email: str | None = None,
    parent_id: int | None = None,
    quota_users: int | None = None,
    max_data_bytes: int | None = None,
    credit: int | None = None,
    expires_at: datetime | None = None,
    node_ids: set[int] | None = None,
) -> User:
    from app.services import users as users_service

    reseller = users_service.create_user(
        db,
        username=username,
        password=password,
        role=Role.RESELLER,
        email=email,
        parent_id=parent_id,
        quota_users=quota_users,
        credit=credit,
    )
    reseller.max_data_bytes = max_data_bytes
    reseller.expires_at = _as_aware(expires_at)
    if node_ids:
        set_allowed_nodes(db, reseller, node_ids)
    db.flush()
    return reseller


def update_reseller(
    db: Session,
    reseller: User,
    *,
    password: str | None = None,
    email: str | None = None,
    quota_users: int | None = None,
    max_data_bytes: int | None = None,
    credit: int | None = None,
    expires_at: datetime | None = None,
    is_active: bool | None = None,
    node_ids: set[int] | None = None,
) -> User:
    from app.services import users as users_service

    if password:
        users_service.set_password(db, reseller, password)
    if email is not None:
        reseller.email = email or None
    if quota_users is not None:
        reseller.quota_users = quota_users or None
    if max_data_bytes is not None:
        reseller.max_data_bytes = max_data_bytes or None
    if credit is not None:
        reseller.credit = credit
    if expires_at is not None:
        reseller.expires_at = _as_aware(expires_at) or None
    if is_active is not None:
        reseller.is_active = is_active
    if node_ids is not None:
        set_allowed_nodes(db, reseller, node_ids)
    db.flush()
    return reseller


def delete_reseller(db: Session, reseller: User) -> None:
    """Delete a reseller and detach its owned VPN users (they stay active)."""

    db.execute(
        update(VpnUser)
        .where(VpnUser.owner_id == reseller.id)
        .values(owner_id=None)
        .execution_options(synchronize_session="auto")
    )
    db.delete(reseller)
    db.flush()


# --------------------------------------------------------------------------- #
# Node allow-list
# --------------------------------------------------------------------------- #
def allowed_node_ids(db: Session, reseller: User) -> set[int]:
    stmt = select(ResellerAllowedNode.node_id).where(ResellerAllowedNode.user_id == reseller.id)
    return set(db.scalars(stmt))


def set_allowed_nodes(db: Session, reseller: User, node_ids: set[int]) -> None:
    existing = list(
        db.scalars(select(ResellerAllowedNode).where(ResellerAllowedNode.user_id == reseller.id))
    )
    for row in existing:
        if row.node_id not in node_ids:
            db.delete(row)
    already = {row.node_id for row in existing}
    for node_id in node_ids - already:
        db.add(ResellerAllowedNode(user_id=reseller.id, node_id=node_id))
    # An explicit set (even empty) means the allow-list is enforced.
    reseller.restrict_nodes = True
    db.flush()


def allowed_nodes(db: Session, reseller: User) -> list:
    """Nodes a reseller may use.

    If the reseller has never been restricted (``restrict_nodes`` false), every
    active node is allowed for backwards compatibility.
    """

    from app.models.node import Node

    if reseller.role == Role.ADMIN or not reseller.restrict_nodes:
        return list(db.scalars(select(Node).where(Node.is_active.is_(True)).order_by(Node.name)))
    ids = allowed_node_ids(db, reseller)
    if not ids:
        return []
    return list(
        db.scalars(
            select(Node).where(Node.is_active.is_(True), Node.id.in_(ids)).order_by(Node.name)
        )
    )


def node_allowed(db: Session, reseller: User, node_id: int | None) -> bool:
    if reseller.role == Role.ADMIN or not reseller.restrict_nodes:
        return True
    if node_id is None:
        # Unassigned users fall back to the first allowed node; allow here.
        return True
    return node_id in allowed_node_ids(db, reseller)


def enforce_node_allowed(db: Session, reseller: User, node_id: int | None) -> None:
    if not node_allowed(db, reseller, node_id):
        raise ResellerError("node is not allowed for this reseller")


# --------------------------------------------------------------------------- #
# Limit enforcement
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class Usage:
    """Aggregated figures for a reseller."""

    user_count: int
    data_bytes: int
    quota_users: int | None
    max_data_bytes: int | None

    @property
    def users_remaining(self) -> int | None:
        if self.quota_users is None:
            return None
        return max(0, self.quota_users - self.user_count)

    @property
    def data_remaining(self) -> int | None:
        if self.max_data_bytes is None:
            return None
        return max(0, self.max_data_bytes - self.data_bytes)


def usage(db: Session, reseller: User) -> Usage:
    count = int(
        db.scalar(select(func.count(VpnUser.id)).where(VpnUser.owner_id == reseller.id)) or 0
    )
    total = int(
        db.scalar(
            select(func.coalesce(func.sum(VpnUser.bytes_in + VpnUser.bytes_out), 0)).where(
                VpnUser.owner_id == reseller.id
            )
        )
        or 0
    )
    return Usage(
        user_count=count,
        data_bytes=total,
        quota_users=reseller.quota_users,
        max_data_bytes=reseller.max_data_bytes,
    )


def enforce_can_create(db: Session, owner: User | None, additional: int = 1) -> None:
    """Raise if creating ``additional`` users would breach the owner's limits."""

    if owner is None or owner.role != Role.RESELLER:
        return
    if not is_active(owner):
        raise ResellerError("reseller account is inactive or expired")
    if owner.quota_users is not None:
        current = int(
            db.scalar(select(func.count(VpnUser.id)).where(VpnUser.owner_id == owner.id)) or 0
        )
        if current + additional > owner.quota_users:
            raise ResellerError("reseller user quota exceeded")
    if owner.max_data_bytes is not None:
        total = int(
            db.scalar(
                select(func.coalesce(func.sum(VpnUser.bytes_in + VpnUser.bytes_out), 0)).where(
                    VpnUser.owner_id == owner.id
                )
            )
            or 0
        )
        if total >= owner.max_data_bytes:
            raise ResellerError("reseller data quota exceeded")


def enforce_data_quota(db: Session, reseller: User) -> None:
    """Raise if the reseller has already used up the aggregate data quota."""

    if reseller.role != Role.RESELLER or reseller.max_data_bytes is None:
        return
    current = usage(db, reseller)
    if current.data_bytes >= reseller.max_data_bytes:
        raise ResellerError("reseller data quota exceeded")


def remaining_data(db: Session, reseller: User) -> int | None:
    """Bytes still available under the reseller quota (``None`` = unlimited)."""

    if reseller.max_data_bytes is None:
        return None
    return max(0, reseller.max_data_bytes - usage(db, reseller).data_bytes)
