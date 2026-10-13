"""Node health samples collected from the node agents."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Float, ForeignKey, Index, Integer
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, IdMixin, utcnow


class NodeMetric(Base, IdMixin):
    """A single health sample for a node (CPU/RAM/network/clients)."""

    __tablename__ = "node_metrics"

    node_id: Mapped[int] = mapped_column(
        ForeignKey("nodes.id", ondelete="CASCADE"), nullable=False, index=True
    )

    cpu_percent: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    load1: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    mem_total: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    mem_used: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    disk_total: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    disk_used: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)

    net_rx: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    net_tx: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)

    client_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    uptime_seconds: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)

    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    __table_args__ = (Index("ix_node_metrics_node_time", "node_id", "recorded_at"),)
