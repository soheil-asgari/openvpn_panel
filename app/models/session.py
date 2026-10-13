"""Server-side sessions (survive restarts)."""

from __future__ import annotations

import time

from sqlalchemy import Float, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, IdMixin


class Session(Base, IdMixin):
    __tablename__ = "sessions"

    session_id: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    csrf_token: Mapped[str] = mapped_column(String(64), nullable=False)
    user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)

    created_at: Mapped[float] = mapped_column(Float, default=lambda: time.time(), nullable=False)
    last_seen: Mapped[float] = mapped_column(Float, default=lambda: time.time(), nullable=False)
    expires_at: Mapped[float] = mapped_column(Float, nullable=False)

    __table_args__ = (Index("ix_sessions_user_expires", "user_id", "expires_at"),)
