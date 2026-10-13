"""Subscription tokens and per-user node visibility.

Revision ID: 0005
Revises: 0004
Create Date: 2026-02-01 00:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("vpn_users") as batch:
        batch.add_column(sa.Column("subscription_token", sa.String(length=64), nullable=True))
        batch.create_index(
            "ix_vpn_users_subscription_token",
            ["subscription_token"],
            unique=True,
        )

    op.create_table(
        "vpn_user_hidden_nodes",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("vpn_user_id", sa.Integer(), nullable=False),
        sa.Column("node_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["vpn_user_id"], ["vpn_users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["node_id"], ["nodes.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("vpn_user_id", "node_id", name="uq_vpn_user_hidden_node"),
    )
    op.create_index(
        "ix_vpn_user_hidden_nodes_vpn_user_id",
        "vpn_user_hidden_nodes",
        ["vpn_user_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_vpn_user_hidden_nodes_vpn_user_id", table_name="vpn_user_hidden_nodes")
    op.drop_table("vpn_user_hidden_nodes")
    with op.batch_alter_table("vpn_users") as batch:
        batch.drop_index("ix_vpn_users_subscription_token")
        batch.drop_column("subscription_token")
