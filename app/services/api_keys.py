"""API key management for the REST API."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import generate_api_key, hash_password, verify_password
from app.models.api_key import SCOPES, ApiKey
from app.models.user import User


def normalise_scopes(scopes: list[str] | None) -> str:
    """Validate and canonicalise a scope list into the stored comma string."""

    if not scopes:
        return ""
    cleaned = {s.strip() for s in scopes if s and s.strip()}
    unknown = cleaned - set(SCOPES)
    if unknown:
        raise ValueError(f"unknown scopes: {sorted(unknown)}")
    return ",".join(sorted(cleaned))


def list_keys(db: Session, user_id: int) -> list[ApiKey]:
    return list(
        db.scalars(
            select(ApiKey).where(ApiKey.user_id == user_id).order_by(ApiKey.created_at.desc())
        )
    )


def create_key(
    db: Session,
    user: User,
    name: str | None = None,
    *,
    scopes: list[str] | None = None,
    expires_at: datetime | None = None,
) -> tuple[ApiKey, str]:
    prefix, raw = generate_api_key()
    key = ApiKey(
        prefix=prefix,
        key_hash=hash_password(raw),
        name=name,
        user_id=user.id,
        scopes=normalise_scopes(scopes),
        expires_at=expires_at,
    )
    db.add(key)
    db.flush()
    return key, raw


def revoke_key(db: Session, user_id: int, key_id: int) -> bool:
    key = db.get(ApiKey, key_id)
    if key is None or key.user_id != user_id:
        return False
    key.is_active = False
    db.flush()
    return True


def rotate_key(db: Session, user_id: int, key_id: int) -> tuple[ApiKey, str] | None:
    """Replace a key's secret in place, keeping its name/scopes/expiry."""

    key = db.get(ApiKey, key_id)
    if key is None or key.user_id != user_id:
        return None
    prefix, raw = generate_api_key()
    key.prefix = prefix
    key.key_hash = hash_password(raw)
    key.is_active = True
    db.flush()
    return key, raw


def key_expired(key: ApiKey) -> bool:
    if key.expires_at is None:
        return False
    expires = key.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    return expires < datetime.now(timezone.utc)


def _parse(raw: str) -> str | None:
    # Expected format: ovpn_<8hex>_<secret>
    parts = raw.split("_", 2)
    if len(parts) != 3 or parts[0] != "ovpn" or len(parts[1]) != 8:
        return None
    return parts[1]


def resolve_key(db: Session, raw: str) -> tuple[ApiKey, User] | None:
    """Return ``(key, user)`` for a raw API key, or ``None``."""

    prefix = _parse(raw)
    if prefix is None:
        return None
    key = db.scalar(select(ApiKey).where(ApiKey.prefix == prefix, ApiKey.is_active.is_(True)))
    if key is None or not verify_password(raw, key.key_hash) or key_expired(key):
        return None
    key.last_used_at = datetime.now(timezone.utc)
    db.flush()
    user = db.get(User, key.user_id)
    if user is None:
        return None
    return key, user


def resolve(db: Session, raw: str) -> User | None:
    """Return the :class:`User` owning a raw API key, or ``None``."""

    resolved = resolve_key(db, raw)
    return resolved[1] if resolved else None
