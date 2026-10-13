"""VPN (OpenVPN) end users managed by the panel."""

from __future__ import annotations

import enum
from datetime import datetime, timedelta, timezone

from sqlalchemy import BigInteger, Boolean, DateTime, Enum, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, IdMixin, TimestampMixin, utcnow


class VpnUserStatus(str, enum.Enum):
    ACTIVE = "active"
    DISABLED = "disabled"
    EXPIRED = "expired"


class VpnUser(Base, IdMixin, TimestampMixin):
    """A VPN credential (username/password + common name)."""

    __tablename__ = "vpn_users"

    username: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    common_name: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    status: Mapped[VpnUserStatus] = mapped_column(
        Enum(VpnUserStatus), default=VpnUserStatus.ACTIVE, nullable=False
    )

    # Expiry: either an absolute date (``expires_at``) or a relative duration
    # expressed in days counted from the first successful connection
    # (``expire_days`` + ``first_connected_at``).
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expire_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    first_connected_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    expired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    data_limit_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    # Per-user concurrency override (``None`` falls back to the global setting,
    # ``0`` means unlimited).
    max_connections: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Optional per-user speed limit in kbit/s (``None``/``0`` means unlimited).
    speed_limit_kbps: Mapped[int | None] = mapped_column(Integer, nullable=True)

    bytes_in: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    bytes_out: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)

    is_online: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_connected_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    connected_ip: Mapped[str | None] = mapped_column(String(45), nullable=True)

    node_id: Mapped[int | None] = mapped_column(
        ForeignKey("nodes.id", ondelete="SET NULL"), nullable=True, index=True
    )
    owner_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # Secret token used by the public subscription URL (``/sub/<token>``). The
    # raw value is stored so lookups stay a single indexed query; treat it as a
    # bearer secret and rotate it with :func:`regenerate_token`.
    subscription_token: Mapped[str | None] = mapped_column(
        String(64), unique=True, index=True, nullable=True
    )

    # When true (and the user's node has an active tunnel attached), the
    # rendered ``.ovpn``/subscription advertisement uses the tunnel's
    # ``entry_host:public_port`` instead of the node's exit host. Per-user
    # override of the platform-wide default.
    use_tunnel: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    node = relationship("Node", lazy="joined")

    @property
    def used_bytes(self) -> int:
        return self.bytes_in + self.bytes_out

    @property
    def effective_expiry(self) -> datetime | None:
        """Absolute expiry instant, resolving relative day-based expiry."""

        if self.expires_at is not None:
            return _as_aware(self.expires_at)
        if self.expire_days is not None and self.first_connected_at is not None:
            return _as_aware(self.first_connected_at) + timedelta(days=self.expire_days)
        return None

    @property
    def is_expired(self) -> bool:
        expires = self.effective_expiry
        if expires is None:
            return False
        return expires < utcnow()

    @property
    def quota_exceeded(self) -> bool:
        return self.data_limit_bytes is not None and self.used_bytes >= self.data_limit_bytes

    @property
    def has_usable_limit(self) -> bool:
        """A relative or absolute expiry that is still pending."""

        return self.effective_expiry is not None and not self.is_expired


def _as_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value
