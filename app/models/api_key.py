"""Per-user API keys for the REST API (scoped, revocable, hashed)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, IdMixin, TimestampMixin

# Scope names recognised by the panel. An empty scope string means "inherit the
# owner's full permission set" (legacy behaviour for keys created before scopes).
SCOPES = (
    "users:read",
    "users:write",
    "nodes:read",
    "nodes:write",
    "resellers:read",
    "resellers:write",
    "reports:read",
    "admin",
)


class ApiKey(Base, IdMixin, TimestampMixin):
    __tablename__ = "api_keys"

    # Short, non-secret identifier shown in listings.
    prefix: Mapped[str] = mapped_column(String(16), unique=True, index=True, nullable=False)
    # argon2 hash of the full key; the raw value is shown once at creation.
    key_hash: Mapped[str] = mapped_column(String(255), nullable=False)

    name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Comma-separated scopes; empty = inherit all of the owner's permissions.
    scopes: Mapped[str] = mapped_column(Text, default="", nullable=False)

    # Optional hard expiry for the key itself.
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def scope_set(self) -> set[str]:
        return {s.strip() for s in (self.scopes or "").split(",") if s.strip()}
