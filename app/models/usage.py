"""Periodic usage samples used for charts and accounting."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, IdMixin, utcnow


class UsageSample(Base, IdMixin):
    __tablename__ = "usage_samples"

    vpn_user_id: Mapped[int] = mapped_column(
        ForeignKey("vpn_users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    bytes_in: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    bytes_out: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    __table_args__ = (Index("ix_usage_user_time", "vpn_user_id", "recorded_at"),)
