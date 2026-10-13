"""Traffic accounting history per VPN user and node."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, IdMixin, utcnow


class TrafficLog(Base, IdMixin):
    """One immutable traffic sample for a VPN user (per active connection)."""

    __tablename__ = "traffic_logs"

    vpn_user_id: Mapped[int] = mapped_column(
        ForeignKey("vpn_users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    node_id: Mapped[int | None] = mapped_column(
        ForeignKey("nodes.id", ondelete="SET NULL"), nullable=True, index=True
    )

    bytes_in: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    bytes_out: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)

    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    __table_args__ = (Index("ix_traffic_user_time", "vpn_user_id", "recorded_at"),)
