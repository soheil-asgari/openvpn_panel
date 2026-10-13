"""Tests for the panel user service."""

from __future__ import annotations

import pytest

from app.models.user import Role
from app.services import users as users_service


def test_create_and_authenticate(db) -> None:  # noqa: ANN001
    user = users_service.create_user(db, username="bob", password="secret-pass")
    assert user.role == Role.RESELLER
    assert users_service.authenticate(db, "bob", "secret-pass") is not None
    assert users_service.authenticate(db, "bob", "wrong") is None


def test_duplicate_username_rejected(db) -> None:  # noqa: ANN001
    users_service.create_user(db, username="bob", password="secret-pass")
    with pytest.raises(users_service.UserError):
        users_service.create_user(db, username="bob", password="secret-pass")


def test_invalid_username_rejected(db) -> None:  # noqa: ANN001
    from app.core.validators import ValidationError

    with pytest.raises(ValidationError):
        users_service.create_user(db, username="a;b", password="secret-pass")


def test_inactive_user_cannot_authenticate(db) -> None:  # noqa: ANN001
    user = users_service.create_user(db, username="bob", password="secret-pass")
    user.is_active = False
    db.flush()
    assert users_service.authenticate(db, "bob", "secret-pass") is None


def test_set_password_requires_length(db, admin_user) -> None:  # noqa: ANN001
    with pytest.raises(users_service.UserError):
        users_service.set_password(db, admin_user, "short")


def test_admin_flag(db, admin_user, reseller_user) -> None:  # noqa: ANN001
    assert admin_user.is_admin
    assert not reseller_user.is_admin


def test_list_resellers(db, reseller_user) -> None:  # noqa: ANN001
    rows = users_service.list_users(db, only_resellers=True)
    assert [u.username for u in rows] == ["reseller"]


def test_create_with_missing_parent_rejected(db) -> None:  # noqa: ANN001
    with pytest.raises(users_service.UserError):
        users_service.create_user(db, username="child", password="secret-pass", parent_id=999)
