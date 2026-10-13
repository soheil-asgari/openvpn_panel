"""Per-user subscription visibility overrides.

A row means the given node is *hidden* from that user's public subscription
page. By default every active node is visible, so only overrides are stored.
"""

from __future__ import annotations

from sqlalchemy import ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, IdMixin, TimestampMixin


class VpnUserHiddenNode(Base, IdMixin, TimestampMixin):
    __tablename__ = "vpn_user_hidden_nodes"
    __table_args__ = (UniqueConstraint("vpn_user_id", "node_id", name="uq_vpn_user_hidden_node"),)

    vpn_user_id: Mapped[int] = mapped_column(
        ForeignKey("vpn_users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    node_id: Mapped[int] = mapped_column(ForeignKey("nodes.id", ondelete="CASCADE"), nullable=False)
