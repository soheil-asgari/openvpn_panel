"""VPN user management fields (relative expiry, concurrency, speed limit).

Revision ID: 0004
Revises: 0003
Create Date: 2026-01-04 00:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NEW_COLUMNS = (
    sa.Column("expire_days", sa.Integer(), nullable=True),
    sa.Column("first_connected_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("expired_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("max_connections", sa.Integer(), nullable=True),
    sa.Column("speed_limit_kbps", sa.Integer(), nullable=True),
)


def upgrade() -> None:
    with op.batch_alter_table("vpn_users") as batch:
        for column in _NEW_COLUMNS:
            batch.add_column(column)


def downgrade() -> None:
    with op.batch_alter_table("vpn_users") as batch:
        for column in reversed(_NEW_COLUMNS):
            batch.drop_column(column.name)
