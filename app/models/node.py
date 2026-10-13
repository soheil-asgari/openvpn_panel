"""OpenVPN nodes (servers) managed by the panel."""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, IdMixin, TimestampMixin


class NodeStatus(str, enum.Enum):
    UNKNOWN = "unknown"
    ONLINE = "online"
    OFFLINE = "offline"


class Node(Base, IdMixin, TimestampMixin):
    """A remote (or local) OpenVPN node reached through its agent."""

    __tablename__ = "nodes"

    name: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)
    host: Mapped[str] = mapped_column(String(255), nullable=False)
    port: Mapped[int] = mapped_column(Integer, default=1194, nullable=False)
    protocol: Mapped[str] = mapped_column(String(8), default="udp", nullable=False)
    cipher: Mapped[str] = mapped_column(String(32), default="AES-256-GCM", nullable=False)
    subnet: Mapped[str] = mapped_column(String(64), default="10.8.0.0/24", nullable=False)

    agent_url: Mapped[str | None] = mapped_column(String(255), nullable=True)
    agent_token_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Shared secret used to sign panel→agent requests (HMAC-SHA256). Stored in
    # plaintext because the panel must reproduce it when calling out. The DB is
    # mode-600 on disk; rotate by re-running the install command.
    agent_secret: Mapped[str | None] = mapped_column(String(128), nullable=True)

    status: Mapped[NodeStatus] = mapped_column(
        Enum(NodeStatus), default=NodeStatus.UNKNOWN, nullable=False
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    config: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Reseller ownership (NULL means owned by the platform admin).
    owner_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
