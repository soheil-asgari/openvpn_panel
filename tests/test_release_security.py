"""Release-phase security checks: authorization on every API route."""

from __future__ import annotations

from app.db import get_session_factory
from app.models.user import Role

# Node *management/monitoring* is admin-only; listing nodes (``nodes:read``)
# is allowed for resellers per the permission matrix.
ADMIN_ONLY_PATHS = (
    "/api/v1/resellers",
    "/api/v1/nodes/1/health",
    "/api/v1/nodes/1/install-command",
    "/api/v1/nodes/1/config",
    "/api/v1/nodes/1/tasks",
)


def _reseller_key() -> str:
    from app.services import api_keys as api_keys_service
    from app.services import users as users_service

    db = get_session_factory()()
    try:
        reseller = users_service.get_by_username(db, "reseller")
        if reseller is None:
            reseller = users_service.create_user(
                db, username="reseller", password="reseller-pass", role=Role.RESELLER
            )
        _, raw = api_keys_service.create_key(db, reseller, name="test")
        db.commit()
        return raw
    finally:
        db.close()


def _headers(raw: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {raw}"}


def test_reseller_cannot_reach_admin_routes(client) -> None:  # noqa: ANN001
    raw = _reseller_key()
    for path in ADMIN_ONLY_PATHS:
        response = client.get(path, headers=_headers(raw))
        assert response.status_code == 403, f"{path} -> {response.status_code}"


def test_reseller_may_list_nodes(client) -> None:  # noqa: ANN001
    raw = _reseller_key()
    assert client.get("/api/v1/nodes", headers=_headers(raw)).status_code == 200


def test_all_api_routes_require_auth(client) -> None:  # noqa: ANN001
    """Every /api/v1 route must reject anonymous access (401/403/422)."""

    from app.main import app

    for route in app.routes:
        path = getattr(route, "path", "")
        methods = getattr(route, "methods", set()) or set()
        if not path.startswith("/api/v1"):
            continue
        if "GET" not in methods:
            continue
        concrete = path.replace("{vpn_user_id}", "1").replace("{node_id}", "1")
        concrete = concrete.replace("{reseller_id}", "1").replace("{key_id}", "1")
        response = client.get(concrete)
        assert response.status_code in (401, 403, 404, 422), f"{concrete} -> {response.status_code}"


def test_docs_and_openapi_exposed(client) -> None:  # noqa: ANN001
    assert client.get("/api/openapi.json").status_code == 200
    assert client.get("/api/docs").status_code == 200


def test_openapi_covers_versioned_api(client) -> None:  # noqa: ANN001
    spec = client.get("/api/openapi.json").json()
    paths = spec["paths"]
    assert any(p.startswith("/api/v1/users") for p in paths)
    assert any("/stats" in p for p in paths)


def test_private_files_are_not_served(client) -> None:  # noqa: ANN001
    for path in (
        "/app/config.py",
        "/etc/ovpn-panel/panel.env",
        "/../app/config.py",
        "/static/../app/config.py",
    ):
        assert client.get(path).status_code in (403, 404)


def test_csrf_middleware_present() -> None:
    from app.main import app

    names = {getattr(m.cls, "__name__", "") for m in app.user_middleware}
    assert "CSRFMiddleware" in names
    assert "SecurityHeadersMiddleware" in names
    assert "TrustedHostsMiddleware" in names


def test_subscription_and_hooks_are_csrf_exempt() -> None:
    from app.middleware import CSRF_EXEMPT_PREFIXES

    assert "/api/v1" in CSRF_EXEMPT_PREFIXES
    assert "/hooks" in CSRF_EXEMPT_PREFIXES
    assert "/sub" in CSRF_EXEMPT_PREFIXES


def test_version_endpoint_requires_auth(client) -> None:  # noqa: ANN001
    assert client.get("/api/v1/version").status_code in (401, 403)


def test_version_endpoint_opt_in_off_by_default(client) -> None:  # noqa: ANN001
    from app import __version__
    from app.services import api_keys as api_keys_service
    from app.services import users as users_service

    db = get_session_factory()()
    try:
        admin = users_service.get_by_username(db, "admin")
        if admin is None:
            admin = users_service.create_user(
                db, username="admin", password="admin-pass", role=Role.ADMIN
            )
        _, raw = api_keys_service.create_key(db, admin, name="ver")
        db.commit()
    finally:
        db.close()

    resp = client.get("/api/v1/version", headers=_headers(raw))
    assert resp.status_code == 200
    body = resp.json()
    assert body["version"] == __version__
    # Update check is disabled by default: no network call, no latest version.
    assert body["update_check_enabled"] is False
    assert body["latest"] is None


def test_backup_archive_is_encrypted() -> None:
    from app.core import crypto
    from app.services import backup

    result = backup.create_backup("release-test-password", include_pki=False)
    # The plaintext database must not leak into the ciphertext.
    assert b"CREATE TABLE" not in result.payload
    assert crypto.is_container(result.payload)
