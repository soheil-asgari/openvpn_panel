"""Initial schema.

Revision ID: 0001
Revises:
Create Date: 2026-01-01 00:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("username", sa.String(length=32), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("role", sa.Enum("ADMIN", "RESELLER", name="role"), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("language", sa.String(length=8), nullable=False, server_default="en"),
        sa.Column("theme", sa.String(length=16), nullable=False, server_default="system"),
        sa.Column(
            "parent_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("quota_users", sa.BigInteger(), nullable=True),
        sa.Column("credit", sa.BigInteger(), nullable=True),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("username", name="uq_users_username"),
    )
    op.create_index("ix_users_username", "users", ["username"])
    op.create_index("ix_users_parent_id", "users", ["parent_id"])

    op.create_table(
        "nodes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=32), nullable=False),
        sa.Column("host", sa.String(length=255), nullable=False),
        sa.Column("port", sa.Integer(), nullable=False, server_default="1194"),
        sa.Column("protocol", sa.String(length=8), nullable=False, server_default="udp"),
        sa.Column("cipher", sa.String(length=32), nullable=False, server_default="AES-256-GCM"),
        sa.Column("subnet", sa.String(length=64), nullable=False, server_default="10.8.0.0/24"),
        sa.Column("agent_url", sa.String(length=255), nullable=True),
        sa.Column("agent_token_hash", sa.String(length=255), nullable=True),
        sa.Column(
            "status",
            sa.Enum("UNKNOWN", "ONLINE", "OFFLINE", name="nodestatus"),
            nullable=False,
            server_default="UNKNOWN",
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("config", sa.Text(), nullable=True),
        sa.Column(
            "owner_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("name", name="uq_nodes_name"),
    )
    op.create_index("ix_nodes_name", "nodes", ["name"])
    op.create_index("ix_nodes_owner_id", "nodes", ["owner_id"])

    op.create_table(
        "vpn_users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("username", sa.String(length=32), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("common_name", sa.String(length=64), nullable=True),
        sa.Column(
            "status",
            sa.Enum("ACTIVE", "DISABLED", "EXPIRED", name="vpnuserstatus"),
            nullable=False,
            server_default="ACTIVE",
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("data_limit_bytes", sa.BigInteger(), nullable=True),
        sa.Column("bytes_in", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("bytes_out", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("is_online", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("last_connected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("connected_ip", sa.String(length=45), nullable=True),
        sa.Column(
            "node_id", sa.Integer(), sa.ForeignKey("nodes.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column(
            "owner_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("username", name="uq_vpn_users_username"),
    )
    op.create_index("ix_vpn_users_username", "vpn_users", ["username"])
    op.create_index("ix_vpn_users_common_name", "vpn_users", ["common_name"])
    op.create_index("ix_vpn_users_node_id", "vpn_users", ["node_id"])
    op.create_index("ix_vpn_users_owner_id", "vpn_users", ["owner_id"])

    op.create_table(
        "sessions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("session_id", sa.String(length=64), nullable=False),
        sa.Column(
            "user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("csrf_token", sa.String(length=64), nullable=False),
        sa.Column("user_agent", sa.String(length=255), nullable=True),
        sa.Column("ip_address", sa.String(length=45), nullable=True),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("last_seen", sa.Float(), nullable=False),
        sa.Column("expires_at", sa.Float(), nullable=False),
        sa.UniqueConstraint("session_id", name="uq_sessions_session_id"),
    )
    op.create_index("ix_sessions_session_id", "sessions", ["session_id"])
    op.create_index("ix_sessions_user_id", "sessions", ["user_id"])
    op.create_index("ix_sessions_user_expires", "sessions", ["user_id", "expires_at"])

    op.create_table(
        "api_keys",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("prefix", sa.String(length=16), nullable=False),
        sa.Column("key_hash", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=True),
        sa.Column(
            "user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("prefix", name="uq_api_keys_prefix"),
    )
    op.create_index("ix_api_keys_prefix", "api_keys", ["prefix"])
    op.create_index("ix_api_keys_user_id", "api_keys", ["user_id"])

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "actor_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("actor_label", sa.String(length=64), nullable=True),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("target_type", sa.String(length=32), nullable=True),
        sa.Column("target_id", sa.Integer(), nullable=True),
        sa.Column("ip_address", sa.String(length=45), nullable=True),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_audit_logs_action", "audit_logs", ["action"])
    op.create_index("ix_audit_logs_actor_id", "audit_logs", ["actor_id"])

    op.create_table(
        "usage_samples",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "vpn_user_id",
            sa.Integer(),
            sa.ForeignKey("vpn_users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("bytes_in", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("bytes_out", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_usage_samples_vpn_user_id", "usage_samples", ["vpn_user_id"])
    op.create_index("ix_usage_user_time", "usage_samples", ["vpn_user_id", "recorded_at"])


def downgrade() -> None:
    for table in (
        "usage_samples",
        "audit_logs",
        "api_keys",
        "sessions",
        "vpn_users",
        "nodes",
        "users",
    ):
        op.drop_table(table)
