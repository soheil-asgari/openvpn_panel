"""Panel users (admins and resellers)."""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Enum, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, IdMixin, TimestampMixin


class Role(str, enum.Enum):
    ADMIN = "admin"
    RESELLER = "reseller"


class User(Base, IdMixin, TimestampMixin):
    """A panel login: either a full admin or a reseller (sub-admin)."""

    __tablename__ = "users"

    username: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[Role] = mapped_column(Enum(Role), default=Role.RESELLER, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    language: Mapped[str] = mapped_column(String(8), default="en", nullable=False)
    theme: Mapped[str] = mapped_column(String(16), default="system", nullable=False)

    # Reseller hierarchy: a reseller belongs to a parent (admin or reseller).
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # Reseller quotas.
    quota_users: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    credit: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # Aggregate data quota across all owned VPN users (``None`` = unlimited).
    max_data_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # Reseller account expiry; ``None`` = never expires.
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # When true, the reseller's node allow-list is empty on purpose (deny all).
    restrict_nodes: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    parent: Mapped["User | None"] = relationship("User", remote_side="User.id", backref="children")

    @property
    def is_admin(self) -> bool:
        return self.role == Role.ADMIN
