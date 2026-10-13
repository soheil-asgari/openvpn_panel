"""Tests for the HTML pages and session-based auth flow."""

from __future__ import annotations

from app.db import get_session_factory


def _create_admin() -> None:
    from app.models.user import Role
    from app.services import users as users_service

    db = get_session_factory()()
    try:
        users_service.create_user(db, username="admin", password="admin-password", role=Role.ADMIN)
        db.commit()
    finally:
        db.close()


def test_root_redirects_to_login_when_anonymous(client) -> None:  # noqa: ANN001
    response = client.get("/", follow_redirects=False)
    assert response.status_code in (303, 307, 401)
    if response.status_code in (303, 307):
        assert response.headers["location"].endswith("/login")


def test_login_page_renders(client) -> None:  # noqa: ANN001
    response = client.get("/login")
    assert response.status_code == 200
    assert "Sign in" in response.text


def test_full_login_flow(client) -> None:  # noqa: ANN001
    _create_admin()
    response = client.post(
        "/login",
        data={"username": "admin", "password": "admin-password"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "ovpn_session" in response.cookies or "ovpn_session" in client.cookies

    dashboard = client.get("/")
    assert dashboard.status_code == 200
    assert "Dashboard" in dashboard.text


def test_login_failure_shows_error(client) -> None:  # noqa: ANN001
    _create_admin()
    response = client.post("/login", data={"username": "admin", "password": "wrong"})
    assert response.status_code == 200
    assert "Invalid username or password" in response.text


def test_security_headers_present(client) -> None:  # noqa: ANN001
    response = client.get("/login")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"


def _csrf(client) -> str:  # noqa: ANN001
    dashboard = client.get("/")
    return dashboard.text.split('name="csrf-token" content="')[1].split('"')[0]


def test_logout_clears_session(client) -> None:  # noqa: ANN001
    _create_admin()
    client.post("/login", data={"username": "admin", "password": "admin-password"})
    response = client.post("/logout", data={"csrf_token": _csrf(client)}, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].endswith("/login")


def test_csrf_required_for_state_change(client) -> None:  # noqa: ANN001
    _create_admin()
    client.post("/login", data={"username": "admin", "password": "admin-password"})
    # Missing CSRF token must be rejected.
    response = client.post("/preferences", data={"language": "fa", "theme": "dark"})
    assert response.status_code == 403


def test_csrf_valid_allows_preference_change(client) -> None:  # noqa: ANN001
    _create_admin()
    client.post("/login", data={"username": "admin", "password": "admin-password"})
    token = _csrf(client)
    response = client.post(
        "/preferences",
        data={"language": "fa", "theme": "dark", "csrf_token": token},
        follow_redirects=False,
    )
    assert response.status_code == 303


def _login(client) -> None:  # noqa: ANN001
    _create_admin()
    client.post("/login", data={"username": "admin", "password": "admin-password"})


def test_users_page_renders(client) -> None:  # noqa: ANN001
    _login(client)
    response = client.get("/users")
    assert response.status_code == 200
    assert "VPN Users" in response.text


def test_online_page_renders(client) -> None:  # noqa: ANN001
    _login(client)
    response = client.get("/online")
    assert response.status_code == 200
    assert "Online users" in response.text


def test_users_export_csv(client) -> None:  # noqa: ANN001
    _login(client)
    response = client.get("/users/export.csv")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "username" in response.text


def test_bulk_create_partial(client) -> None:  # noqa: ANN001
    _login(client)
    from app.db import get_session_factory
    from app.services import vpn_users as vpn_users_service

    token = _csrf(client)
    response = client.post(
        "/partials/users/bulk",
        data={"prefix": "cli", "count": "3", "csrf_token": token},
    )
    assert response.status_code == 200
    assert "cli01" in response.text

    db = get_session_factory()()
    try:
        assert vpn_users_service.count_vpn_users(db) == 3
    finally:
        db.close()
