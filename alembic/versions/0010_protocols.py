"""Protocol abstraction: protocol_instances and protocol_users.

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-12 00:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "protocol_instances",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=32), nullable=False),
        sa.Column("node_id", sa.Integer(), nullable=True),
        sa.Column("host", sa.String(length=255), nullable=True),
        sa.Column("port", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("address", sa.String(length=64), nullable=True),
        sa.Column("subnet", sa.String(length=64), nullable=True),
        sa.Column("dns", sa.String(length=255), nullable=True),
        sa.Column("mtu", sa.Integer(), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "UNKNOWN",
                "PENDING",
                "ACTIVE",
                "DEGRADED",
                "STOPPED",
                "ERROR",
                name="protocolinstancestatus",
            ),
            nullable=False,
            server_default="UNKNOWN",
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("settings", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("version", sa.String(length=64), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("owner_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["node_id"], ["nodes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_protocol_instances_kind", "protocol_instances", ["kind"])
    op.create_index("ix_protocol_instances_name", "protocol_instances", ["name"])
    op.create_index("ix_protocol_instances_node_id", "protocol_instances", ["node_id"])
    op.create_index("ix_protocol_instances_owner_id", "protocol_instances", ["owner_id"])
    op.create_index(
        "uq_protocol_instance_node_kind_name",
        "protocol_instances",
        ["node_id", "kind", "name"],
        unique=True,
    )

    op.create_table(
        "protocol_users",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("vpn_user_id", sa.Integer(), nullable=False),
        sa.Column("protocol_instance_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("credentials", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("bytes_in", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("bytes_out", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("is_online", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("last_connected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["vpn_user_id"], ["vpn_users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["protocol_instance_id"], ["protocol_instances.id"], ondelete="CASCADE"
        ),
    )
    op.create_index("ix_protocol_users_vpn_user_id", "protocol_users", ["vpn_user_id"])
    op.create_index(
        "ix_protocol_users_protocol_instance_id", "protocol_users", ["protocol_instance_id"]
    )
    op.create_index("ix_protocol_users_kind", "protocol_users", ["kind"])
    op.create_index(
        "uq_protocol_user", "protocol_users", ["vpn_user_id", "protocol_instance_id"], unique=True
    )


def downgrade() -> None:
    op.drop_index("uq_protocol_user", table_name="protocol_users")
    op.drop_index("ix_protocol_users_kind", table_name="protocol_users")
    op.drop_index("ix_protocol_users_protocol_instance_id", table_name="protocol_users")
    op.drop_index("ix_protocol_users_vpn_user_id", table_name="protocol_users")
    op.drop_table("protocol_users")

    op.drop_index("uq_protocol_instance_node_kind_name", table_name="protocol_instances")
    op.drop_index("ix_protocol_instances_owner_id", table_name="protocol_instances")
    op.drop_index("ix_protocol_instances_node_id", table_name="protocol_instances")
    op.drop_index("ix_protocol_instances_name", table_name="protocol_instances")
    op.drop_index("ix_protocol_instances_kind", table_name="protocol_instances")
    op.drop_table("protocol_instances")
