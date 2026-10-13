"""Reseller (sub-admin) node allow-list.

A row grants a reseller access to a specific node. When a reseller has no rows,
the allow-list is treated as *unrestricted* for backwards compatibility with
accounts created before this feature existed; admins can still pin a reseller
to an empty explicit set by using the UI/API (which writes rows).
"""

from __future__ import annotations

from sqlalchemy import ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, IdMixin, TimestampMixin


class ResellerAllowedNode(Base, IdMixin, TimestampMixin):
    __tablename__ = "reseller_allowed_nodes"
    __table_args__ = (UniqueConstraint("user_id", "node_id", name="uq_reseller_allowed_node"),)

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    node_id: Mapped[int] = mapped_column(ForeignKey("nodes.id", ondelete="CASCADE"), nullable=False)
