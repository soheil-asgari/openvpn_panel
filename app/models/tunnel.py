"""Tunnel manager models.

A *tunnel* is a reverse/forward tunnel that carries an OpenVPN instance's
traffic between an **entry** host (the one users connect to) and an **exit**
host (where the OpenVPN server actually listens). The panel provisions the
entry host over SSH, installs a backend (Backhaul or WireGuard), and exposes
the configured ``public_ports`` so that rendered ``.ovpn`` files and the
subscription page advertise ``entry_host:public_port``.

Three tables back this feature:

* :class:`Tunnel` — the provisioned tunnel and its last-known state.
* :class:`TunnelHealthSample` — periodic end-to-end probe results (charts +
  self-healing decisions).
* :class:`TunnelProvisionJob` — a single provisioning run with a live log,
  step marker and progress, so the UI can stream progress and offer retry.
"""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, IdMixin, TimestampMixin, utcnow


class TunnelBackendKind(str, enum.Enum):
    """Which backend program terminates the tunnel."""

    AUTO = "auto"
    BACKHAUL = "backhaul"
    WIREGUARD = "wireguard"


class TunnelTransport(str, enum.Enum):
    """Transport used by the tunnel backend."""

    AUTO = "auto"
    TCP = "tcp"
    TCPMUX = "tcpmux"
    UDP = "udp"
    WG = "wg"


class TunnelStatus(str, enum.Enum):
    UNKNOWN = "unknown"
    PENDING = "pending"
    PROVISIONING = "provisioning"
    ACTIVE = "active"
    DEGRADED = "degraded"
    STOPPED = "stopped"
    ERROR = "error"
    UNINSTALLED = "uninstalled"


class TunnelAuthMethod(str, enum.Enum):
    """How the panel authenticates to the entry host over SSH."""

    PASSWORD = "password"
    KEY = "key"


class Tunnel(Base, IdMixin, TimestampMixin):
    __tablename__ = "tunnels"

    name: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)

    backend: Mapped[TunnelBackendKind] = mapped_column(
        Enum(TunnelBackendKind), default=TunnelBackendKind.AUTO, nullable=False
    )
    transport: Mapped[TunnelTransport] = mapped_column(
        Enum(TunnelTransport), default=TunnelTransport.AUTO, nullable=False
    )
    status: Mapped[TunnelStatus] = mapped_column(
        Enum(TunnelStatus), default=TunnelStatus.PENDING, nullable=False, index=True
    )

    # Entry host (public side users connect to) --------------------------------
    entry_host: Mapped[str] = mapped_column(String(255), nullable=False)
    entry_ssh_port: Mapped[int] = mapped_column(Integer, default=22, nullable=False)
    entry_username: Mapped[str] = mapped_column(String(64), default="root", nullable=False)
    entry_auth_method: Mapped[TunnelAuthMethod] = mapped_column(
        Enum(TunnelAuthMethod), default=TunnelAuthMethod.PASSWORD, nullable=False
    )
    # SHA256 fingerprint of the entry host key the admin confirmed. Until it is
    # set the panel refuses to provision (defence against MITM on first connect).
    host_key_fingerprint: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Path (on the panel host, mode 600) to the private key installed by the
    # panel for passwordless reconnects. The password itself is never stored.
    panel_key_path: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Exit side / payload ------------------------------------------------------
    exit_host: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Random shared secret used to authenticate the two tunnel endpoints.
    token: Mapped[str] = mapped_column(String(128), nullable=False)
    # Public control port (Backhaul bind / WireGuard listen).
    control_port: Mapped[int] = mapped_column(Integer, nullable=False)
    # Comma-separated list of forwarded ports exposed on the entry host.
    public_ports: Mapped[str] = mapped_column(Text, default="", nullable=False)
    # Backhaul status API port (local only).
    web_port: Mapped[int] = mapped_column(Integer, default=2060, nullable=False)

    # MTU tuning applied to the tunnel and the attached instance.
    mtu: Mapped[int | None] = mapped_column(Integer, nullable=True)
    mssfix: Mapped[int | None] = mapped_column(Integer, nullable=True)

    backend_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    config: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Attachment to an OpenVPN instance ---------------------------------------
    node_id: Mapped[int | None] = mapped_column(
        ForeignKey("nodes.id", ondelete="SET NULL"), nullable=True, index=True
    )
    instance_name: Mapped[str | None] = mapped_column(String(32), nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    auto_heal: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    uptime_seconds: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)

    owner_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # -- helpers ---------------------------------------------------------------
    def port_list(self) -> list[int]:
        ports: list[int] = []
        for chunk in (self.public_ports or "").replace(",", " ").split():
            if chunk.isdigit():
                ports.append(int(chunk))
        return ports


class TunnelHealthSample(Base, IdMixin):
    """A single end-to-end probe result for a tunnel."""

    __tablename__ = "tunnel_health_samples"

    tunnel_id: Mapped[int] = mapped_column(
        ForeignKey("tunnels.id", ondelete="CASCADE"), nullable=False, index=True
    )
    ok: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    latency_ms: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    packet_loss: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    throughput_kbps: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    uptime_seconds: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class TunnelProvisionJobStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"


class TunnelProvisionJob(Base, IdMixin, TimestampMixin):
    """One provisioning run, with a streamed log and step/progress markers."""

    __tablename__ = "tunnel_provision_jobs"

    tunnel_id: Mapped[int | None] = mapped_column(
        ForeignKey("tunnels.id", ondelete="CASCADE"), nullable=True, index=True
    )
    kind: Mapped[str] = mapped_column(String(32), default="provision", nullable=False)
    status: Mapped[TunnelProvisionJobStatus] = mapped_column(
        Enum(TunnelProvisionJobStatus),
        default=TunnelProvisionJobStatus.PENDING,
        nullable=False,
        index=True,
    )
    step: Mapped[str | None] = mapped_column(String(64), nullable=True)
    progress: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    log: Mapped[str] = mapped_column(Text, default="", nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    rolled_back: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
