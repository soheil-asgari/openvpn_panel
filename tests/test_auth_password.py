"""Tests for login and the change-password flow."""

from __future__ import annotations

from app.db import get_session_factory
from app.models.user import Role
from app.services import users as users_service

PASSWORD = "admin-password"


def _create_admin() -> None:
    db = get_session_factory()()
    try:
        users_service.create_user(db, username="admin", password=PASSWORD, role=Role.ADMIN)
        db.commit()
    finally:
        db.close()


def _csrf(client) -> str:  # noqa: ANN001
    dashboard = client.get("/")
    return dashboard.text.split('name="csrf-token" content="')[1].split('"')[0]


def _login(client) -> None:  # noqa: ANN001
    _create_admin()
    response = client.post(
        "/login", data={"username": "admin", "password": PASSWORD}, follow_redirects=False
    )
    assert response.status_code == 303


def _verify_admin_password(password: str) -> bool:
    db = get_session_factory()()
    try:
        return users_service.authenticate(db, "admin", password) is not None
    finally:
        db.close()


def test_change_password_success(client) -> None:  # noqa: ANN001
    _login(client)
    token = _csrf(client)
    response = client.post(
        "/change-password",
        data={
            "current_password": PASSWORD,
            "new_password": "new-admin-password",
            "confirm_password": "new-admin-password",
            "csrf_token": token,
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "saved=password" in response.headers["location"]
    assert _verify_admin_password("new-admin-password")


def test_change_password_wrong_current(client) -> None:  # noqa: ANN001
    _login(client)
    token = _csrf(client)
    response = client.post(
        "/change-password",
        data={
            "current_password": "nope",
            "new_password": "new-admin-password",
            "confirm_password": "new-admin-password",
            "csrf_token": token,
        },
        follow_redirects=False,
    )
    assert "error=current" in response.headers["location"]
    assert _verify_admin_password(PASSWORD)


def test_change_password_mismatch(client) -> None:  # noqa: ANN001
    _login(client)
    token = _csrf(client)
    response = client.post(
        "/change-password",
        data={
            "current_password": PASSWORD,
            "new_password": "new-admin-password",
            "confirm_password": "different-password",
            "csrf_token": token,
        },
        follow_redirects=False,
    )
    assert "error=mismatch" in response.headers["location"]
    assert _verify_admin_password(PASSWORD)


def test_change_password_too_short(client) -> None:  # noqa: ANN001
    _login(client)
    token = _csrf(client)
    response = client.post(
        "/change-password",
        data={
            "current_password": PASSWORD,
            "new_password": "short",
            "confirm_password": "short",
            "csrf_token": token,
        },
        follow_redirects=False,
    )
    assert "error=weak" in response.headers["location"]
    assert _verify_admin_password(PASSWORD)


def test_change_password_requires_csrf(client) -> None:  # noqa: ANN001
    _login(client)
    response = client.post(
        "/change-password",
        data={
            "current_password": PASSWORD,
            "new_password": "new-admin-password",
            "confirm_password": "new-admin-password",
        },
    )
    assert response.status_code == 403
    assert _verify_admin_password(PASSWORD)
