"""VPN connection sessions.

Revision ID: 0003
Revises: 0002
Create Date: 2026-01-03 00:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "vpn_sessions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "vpn_user_id",
            sa.Integer(),
            sa.ForeignKey("vpn_users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "node_id",
            sa.Integer(),
            sa.ForeignKey("nodes.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("common_name", sa.String(length=64), nullable=False),
        sa.Column("trusted_ip", sa.String(length=45), nullable=True),
        sa.Column("bytes_in", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("bytes_out", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("connected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("disconnected_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_vpn_sessions_vpn_user_id", "vpn_sessions", ["vpn_user_id"])
    op.create_index("ix_vpn_sessions_node_id", "vpn_sessions", ["node_id"])
    op.create_index("ix_vpn_sessions_common_name", "vpn_sessions", ["common_name"])
    op.create_index("ix_vpn_sessions_user_active", "vpn_sessions", ["vpn_user_id", "is_active"])
    op.create_index(
        "ix_vpn_sessions_common_name_active", "vpn_sessions", ["common_name", "is_active"]
    )


def downgrade() -> None:
    op.drop_table("vpn_sessions")
