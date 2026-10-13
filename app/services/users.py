"""Panel user (admin/reseller) service."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.security import hash_password, verify_password
from app.core.validators import validate_email, validate_username
from app.models.user import Role, User
from app.models.vpn_user import VpnUser


class UserError(ValueError):
    """Domain error for user operations."""


def get_by_username(db: Session, username: str) -> User | None:
    return db.scalar(select(User).where(User.username == username))


def get(db: Session, user_id: int) -> User | None:
    return db.get(User, user_id)


def list_users(
    db: Session, *, parent_id: int | None = None, only_resellers: bool = False
) -> list[User]:
    stmt = select(User).order_by(User.username)
    if parent_id is not None:
        stmt = stmt.where(User.parent_id == parent_id)
    if only_resellers:
        stmt = stmt.where(User.role == Role.RESELLER)
    return list(db.scalars(stmt))


def _count_owned(db: Session, owner_id: int) -> int:
    return int(db.scalar(select(func.count(VpnUser.id)).where(VpnUser.owner_id == owner_id)) or 0)


def create_user(
    db: Session,
    *,
    username: str,
    password: str,
    role: Role = Role.RESELLER,
    email: str | None = None,
    parent_id: int | None = None,
    quota_users: int | None = None,
    credit: int | None = None,
    language: str = "en",
) -> User:
    validate_username(username)
    if email:
        validate_email(email)
    if get_by_username(db, username) is not None:
        raise UserError("username already exists")
    if parent_id is not None and db.get(User, parent_id) is None:
        raise UserError("parent user not found")

    user = User(
        username=username,
        email=email,
        password_hash=hash_password(password),
        role=role,
        parent_id=parent_id,
        quota_users=quota_users,
        credit=credit,
        language=language,
    )
    db.add(user)
    db.flush()
    return user


def set_password(db: Session, user: User, password: str) -> None:
    if not password or len(password) < 8:
        raise UserError("password must be at least 8 characters")
    user.password_hash = hash_password(password)
    db.flush()


def authenticate(db: Session, username: str, password: str) -> User | None:
    user = get_by_username(db, username)
    if user is None or not user.is_active:
        return None
    if not verify_password(password, user.password_hash):
        return None
    user.last_login_at = datetime.now(timezone.utc)
    db.flush()
    return user
