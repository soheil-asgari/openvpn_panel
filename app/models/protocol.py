"""Protocol abstraction models (Phase 12-15).

* :class:`ProtocolInstance` — a configured server endpoint of one protocol on a
  node (e.g. ``wg0`` on node 3, or a sing-box VLESS inbound).
* :class:`ProtocolUser` — per-protocol credentials and enable/disable flags for
  a :class:`~app.models.vpn_user.VpnUser`. A user may be enabled on several
  protocols at once; each row carries its own credential material (uuid, keys,
  password) and its own per-protocol traffic counters.
"""

from __future__ import annotations

import enum
import json
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, IdMixin, TimestampMixin


class ProtocolInstanceStatus(str, enum.Enum):
    UNKNOWN = "unknown"
    PENDING = "pending"
    ACTIVE = "active"
    DEGRADED = "degraded"
    STOPPED = "stopped"
    ERROR = "error"


class ProtocolInstance(Base, IdMixin, TimestampMixin):
    """One configured protocol endpoint (interface/inbound) on a node."""

    __tablename__ = "protocol_instances"
    __table_args__ = (
        UniqueConstraint("node_id", "kind", "name", name="uq_protocol_instance_node_kind_name"),
    )

    kind: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(32), nullable=False, index=True)

    node_id: Mapped[int | None] = mapped_column(
        ForeignKey("nodes.id", ondelete="CASCADE"), nullable=True, index=True
    )

    host: Mapped[str | None] = mapped_column(String(255), nullable=True)
    port: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    #: Interface address/subnet (``10.66.0.0/24``) for WG/L2TP.
    address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    subnet: Mapped[str | None] = mapped_column(String(64), nullable=True)
    dns: Mapped[str | None] = mapped_column(String(255), nullable=True)
    mtu: Mapped[int | None] = mapped_column(Integer, nullable=True)

    status: Mapped[ProtocolInstanceStatus] = mapped_column(
        Enum(ProtocolInstanceStatus), default=ProtocolInstanceStatus.UNKNOWN, nullable=False
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    #: Protocol specific options (inbound_type, security, sni, reality keys,
    #: ws path, method, psk, ...). Stored as JSON text for portability.
    settings: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    owner_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )

    @property
    def settings_dict(self) -> dict:
        try:
            data = json.loads(self.settings or "{}")
            return data if isinstance(data, dict) else {}
        except json.JSONDecodeError:
            return {}

    @settings_dict.setter
    def settings_dict(self, value: dict) -> None:
        self.settings = json.dumps(value or {})


class ProtocolUser(Base, IdMixin, TimestampMixin):
    """Per-protocol credential + enable flag for a VPN user."""

    __tablename__ = "protocol_users"
    __table_args__ = (
        UniqueConstraint("vpn_user_id", "protocol_instance_id", name="uq_protocol_user"),
    )

    vpn_user_id: Mapped[int] = mapped_column(
        ForeignKey("vpn_users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    protocol_instance_id: Mapped[int] = mapped_column(
        ForeignKey("protocol_instances.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False, index=True)

    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    #: Protocol credential material (uuid, public/private key, address, psk).
    credentials: Mapped[str] = mapped_column(Text, default="{}", nullable=False)

    #: Per-protocol counters (mirrored into the vpn_user aggregates).
    bytes_in: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    bytes_out: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)

    is_online: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_connected_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    @property
    def credentials_dict(self) -> dict:
        try:
            data = json.loads(self.credentials or "{}")
            return data if isinstance(data, dict) else {}
        except json.JSONDecodeError:
            return {}

    @credentials_dict.setter
    def credentials_dict(self, value: dict) -> None:
        self.credentials = json.dumps(value or {})

    @property
    def used_bytes(self) -> int:
        return self.bytes_in + self.bytes_out
