"""Multi-node: agent HMAC secret, health metrics and retry queue.

Revision ID: 0007
Revises: 0006
Create Date: 2026-04-01 00:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("nodes") as batch:
        batch.add_column(sa.Column("agent_secret", sa.String(length=128), nullable=True))

    op.create_table(
        "node_metrics",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("node_id", sa.Integer(), nullable=False),
        sa.Column("cpu_percent", sa.Float(), nullable=False, server_default="0"),
        sa.Column("load1", sa.Float(), nullable=False, server_default="0"),
        sa.Column("mem_total", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("mem_used", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("disk_total", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("disk_used", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("net_rx", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("net_tx", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("client_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("uptime_seconds", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["node_id"], ["nodes.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_node_metrics_node_id", "node_metrics", ["node_id"])
    op.create_index("ix_node_metrics_node_time", "node_metrics", ["node_id", "recorded_at"])

    op.create_table(
        "node_tasks",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("node_id", sa.Integer(), nullable=True),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("payload", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.Enum("PENDING", "RUNNING", "DONE", "FAILED", name="nodetaskstatus"),
            nullable=False,
            server_default="PENDING",
        ),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("run_after", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["node_id"], ["nodes.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_node_tasks_node_id", "node_tasks", ["node_id"])
    op.create_index("ix_node_tasks_kind", "node_tasks", ["kind"])
    op.create_index("ix_node_tasks_status", "node_tasks", ["status"])
    op.create_index("ix_node_tasks_run_after", "node_tasks", ["run_after"])


def downgrade() -> None:
    op.drop_index("ix_node_tasks_run_after", table_name="node_tasks")
    op.drop_index("ix_node_tasks_status", table_name="node_tasks")
    op.drop_index("ix_node_tasks_kind", table_name="node_tasks")
    op.drop_index("ix_node_tasks_node_id", table_name="node_tasks")
    op.drop_table("node_tasks")

    op.drop_index("ix_node_metrics_node_time", table_name="node_metrics")
    op.drop_index("ix_node_metrics_node_id", table_name="node_metrics")
    op.drop_table("node_metrics")

    with op.batch_alter_table("nodes") as batch:
        batch.drop_column("agent_secret")
