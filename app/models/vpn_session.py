"""OpenVPN connection sessions (one row per client connection).

A row is opened by the ``client-connect`` hook and closed by the
``client-disconnect`` hook, which also records the final byte counters. This
gives an auditable per-connection history independent of the aggregate
``vpn_users.bytes_in``/``bytes_out`` counters.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, IdMixin, utcnow


class VpnSession(Base, IdMixin):
    """A single OpenVPN client connection."""

    __tablename__ = "vpn_sessions"

    vpn_user_id: Mapped[int] = mapped_column(
        ForeignKey("vpn_users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    node_id: Mapped[int | None] = mapped_column(
        ForeignKey("nodes.id", ondelete="SET NULL"), nullable=True, index=True
    )

    common_name: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    trusted_ip: Mapped[str | None] = mapped_column(String(45), nullable=True)

    bytes_in: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    bytes_out: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    connected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    disconnected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_vpn_sessions_user_active", "vpn_user_id", "is_active"),
        Index("ix_vpn_sessions_common_name_active", "common_name", "is_active"),
    )
