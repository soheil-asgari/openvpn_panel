"""Reseller limits and node allow-list.

Revision ID: 0006
Revises: 0005
Create Date: 2026-03-01 00:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("max_data_bytes", sa.BigInteger(), nullable=True))
        batch.add_column(sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(
            sa.Column(
                "restrict_nodes",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )

    op.create_table(
        "reseller_allowed_nodes",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("node_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["node_id"], ["nodes.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("user_id", "node_id", name="uq_reseller_allowed_node"),
    )
    op.create_index("ix_reseller_allowed_nodes_user_id", "reseller_allowed_nodes", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_reseller_allowed_nodes_user_id", table_name="reseller_allowed_nodes")
    op.drop_table("reseller_allowed_nodes")
    with op.batch_alter_table("users") as batch:
        batch.drop_column("restrict_nodes")
        batch.drop_column("expires_at")
        batch.drop_column("max_data_bytes")
