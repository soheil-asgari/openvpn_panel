"""Tunnel manager: tunnels, health samples and provisioning jobs.

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-12 00:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tunnels",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(length=32), nullable=False),
        sa.Column(
            "backend",
            sa.Enum("AUTO", "BACKHAUL", "WIREGUARD", name="tunnelbackendkind"),
            nullable=False,
            server_default="AUTO",
        ),
        sa.Column(
            "transport",
            sa.Enum("AUTO", "TCP", "TCPMUX", "UDP", "WG", name="tunneltransport"),
            nullable=False,
            server_default="AUTO",
        ),
        sa.Column(
            "status",
            sa.Enum(
                "UNKNOWN",
                "PENDING",
                "PROVISIONING",
                "ACTIVE",
                "DEGRADED",
                "STOPPED",
                "ERROR",
                "UNINSTALLED",
                name="tunnelstatus",
            ),
            nullable=False,
            server_default="PENDING",
        ),
        sa.Column("entry_host", sa.String(length=255), nullable=False),
        sa.Column("entry_ssh_port", sa.Integer(), nullable=False, server_default="22"),
        sa.Column("entry_username", sa.String(length=64), nullable=False, server_default="root"),
        sa.Column(
            "entry_auth_method",
            sa.Enum("PASSWORD", "KEY", name="tunnelauthmethod"),
            nullable=False,
            server_default="PASSWORD",
        ),
        sa.Column("host_key_fingerprint", sa.String(length=128), nullable=True),
        sa.Column("panel_key_path", sa.String(length=255), nullable=True),
        sa.Column("exit_host", sa.String(length=255), nullable=True),
        sa.Column("token", sa.String(length=128), nullable=False),
        sa.Column("control_port", sa.Integer(), nullable=False),
        sa.Column("public_ports", sa.Text(), nullable=False, server_default=""),
        sa.Column("web_port", sa.Integer(), nullable=False, server_default="2060"),
        sa.Column("mtu", sa.Integer(), nullable=True),
        sa.Column("mssfix", sa.Integer(), nullable=True),
        sa.Column("backend_version", sa.String(length=64), nullable=True),
        sa.Column("config", sa.Text(), nullable=True),
        sa.Column("node_id", sa.Integer(), nullable=True),
        sa.Column("instance_name", sa.String(length=32), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("auto_heal", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("uptime_seconds", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("owner_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["node_id"], ["nodes.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_tunnels_name", "tunnels", ["name"], unique=True)
    op.create_index("ix_tunnels_status", "tunnels", ["status"])
    op.create_index("ix_tunnels_node_id", "tunnels", ["node_id"])
    op.create_index("ix_tunnels_owner_id", "tunnels", ["owner_id"])

    op.create_table(
        "tunnel_health_samples",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("tunnel_id", sa.Integer(), nullable=False),
        sa.Column("ok", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("latency_ms", sa.Float(), nullable=False, server_default="0"),
        sa.Column("packet_loss", sa.Float(), nullable=False, server_default="0"),
        sa.Column("throughput_kbps", sa.Float(), nullable=False, server_default="0"),
        sa.Column("uptime_seconds", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tunnel_id"], ["tunnels.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_tunnel_health_tunnel_id", "tunnel_health_samples", ["tunnel_id"])
    op.create_index(
        "ix_tunnel_health_tunnel_time", "tunnel_health_samples", ["tunnel_id", "recorded_at"]
    )

    op.create_table(
        "tunnel_provision_jobs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("tunnel_id", sa.Integer(), nullable=True),
        sa.Column("kind", sa.String(length=32), nullable=False, server_default="provision"),
        sa.Column(
            "status",
            sa.Enum(
                "PENDING",
                "RUNNING",
                "DONE",
                "FAILED",
                "ROLLED_BACK",
                name="tunnelprovisionjobstatus",
            ),
            nullable=False,
            server_default="PENDING",
        ),
        sa.Column("step", sa.String(length=64), nullable=True),
        sa.Column("progress", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("log", sa.Text(), nullable=False, server_default=""),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rolled_back", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tunnel_id"], ["tunnels.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_tunnel_jobs_tunnel_id", "tunnel_provision_jobs", ["tunnel_id"])
    op.create_index("ix_tunnel_jobs_status", "tunnel_provision_jobs", ["status"])

    with op.batch_alter_table("vpn_users") as batch:
        batch.add_column(
            sa.Column("use_tunnel", sa.Boolean(), nullable=False, server_default=sa.false())
        )


def downgrade() -> None:
    with op.batch_alter_table("vpn_users") as batch:
        batch.drop_column("use_tunnel")

    op.drop_index("ix_tunnel_jobs_status", table_name="tunnel_provision_jobs")
    op.drop_index("ix_tunnel_jobs_tunnel_id", table_name="tunnel_provision_jobs")
    op.drop_table("tunnel_provision_jobs")

    op.drop_index("ix_tunnel_health_tunnel_time", table_name="tunnel_health_samples")
    op.drop_index("ix_tunnel_health_tunnel_id", table_name="tunnel_health_samples")
    op.drop_table("tunnel_health_samples")

    op.drop_index("ix_tunnels_owner_id", table_name="tunnels")
    op.drop_index("ix_tunnels_node_id", table_name="tunnels")
    op.drop_index("ix_tunnels_status", table_name="tunnels")
    op.drop_index("ix_tunnels_name", table_name="tunnels")
    op.drop_table("tunnels")
