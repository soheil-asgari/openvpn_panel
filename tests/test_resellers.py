"""Tests for the reseller (sub-admin) system: CRUD, limits, reports and audit."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.core import permissions
from app.models.user import Role
from app.models.vpn_user import VpnUserStatus


def _admin(db):  # noqa: ANN001
    from app.services import users as users_service

    user = users_service.create_user(
        db, username="admin", password="admin-password", role=Role.ADMIN
    )
    db.flush()
    return user


def _node(db, name: str):  # noqa: ANN001
    from app.services import nodes as nodes_service

    node, _ = nodes_service.create_node(db, name=name, host="vpn.example.com")
    db.flush()
    return node


# --------------------------------------------------------------------------- #
# Permission matrix
# --------------------------------------------------------------------------- #
def test_permission_matrix_admins_have_everything() -> None:
    for permission in permissions.Permission:
        assert permissions.can(Role.ADMIN, permission)


def test_permission_matrix_resellers_cannot_manage_nodes() -> None:
    assert permissions.can(Role.RESELLER, permissions.Permission.MANAGE_USERS)
    assert not permissions.can(Role.RESELLER, permissions.Permission.MANAGE_NODES)
    assert not permissions.can(Role.RESELLER, permissions.Permission.MANAGE_RESELLERS)
    assert not permissions.can(Role.RESELLER, permissions.Permission.VIEW_AUDIT)


def test_require_raises_without_permission() -> None:
    with pytest.raises(permissions.PermissionError_):
        permissions.require(Role.RESELLER, permissions.Permission.MANAGE_NODES)


def test_matrix_rows_shape() -> None:
    rows = permissions.matrix_rows()
    assert rows
    assert {"permission", "label", "roles"} <= set(rows[0])
    assert set(rows[0]["roles"]) == {"admin", "reseller"}


# --------------------------------------------------------------------------- #
# CRUD & limits
# --------------------------------------------------------------------------- #
def test_create_and_update_reseller(db) -> None:  # noqa: ANN001
    from app.services import resellers as resellers_service

    _admin(db)
    reseller = resellers_service.create_reseller(
        db, username="reseller1", password="reseller-pass", quota_users=10
    )
    assert reseller.role == Role.RESELLER
    assert reseller.quota_users == 10

    resellers_service.update_reseller(db, reseller, quota_users=5, max_data_bytes=1000)
    assert reseller.quota_users == 5
    assert reseller.max_data_bytes == 1000


def test_reseller_list_excludes_admins(db) -> None:  # noqa: ANN001
    from app.services import resellers as resellers_service

    _admin(db)
    resellers_service.create_reseller(db, username="reseller1", password="reseller-pass")
    assert [r.username for r in resellers_service.list_resellers(db)] == ["reseller1"]


def test_user_quota_enforced_server_side(db) -> None:  # noqa: ANN001
    from app.services import resellers as resellers_service
    from app.services import vpn_users as vpn_users_service

    _admin(db)
    reseller = resellers_service.create_reseller(
        db, username="reseller1", password="reseller-pass", quota_users=2
    )
    vpn_users_service.create_vpn_user(
        db, username="client1", password="client-pass", owner=reseller
    )
    vpn_users_service.create_vpn_user(
        db, username="client2", password="client-pass", owner=reseller
    )
    with pytest.raises(vpn_users_service.VpnUserError):
        vpn_users_service.create_vpn_user(
            db, username="client3", password="client-pass", owner=reseller
        )


def test_expired_reseller_cannot_create_users(db) -> None:  # noqa: ANN001
    from app.services import resellers as resellers_service
    from app.services import vpn_users as vpn_users_service

    _admin(db)
    reseller = resellers_service.create_reseller(
        db,
        username="reseller1",
        password="reseller-pass",
        expires_at=datetime.now(timezone.utc) - timedelta(days=1),
    )
    assert resellers_service.is_expired(reseller)
    with pytest.raises(vpn_users_service.VpnUserError):
        vpn_users_service.create_vpn_user(
            db, username="client1", password="client-pass", owner=reseller
        )


def test_expired_reseller_users_not_connectable(db) -> None:  # noqa: ANN001
    from app.services import resellers as resellers_service
    from app.services import vpn_users as vpn_users_service

    _admin(db)
    reseller = resellers_service.create_reseller(db, username="reseller1", password="reseller-pass")
    vpn_user = vpn_users_service.create_vpn_user(
        db, username="client1", password="client-pass", owner=reseller
    )
    assert vpn_users_service.is_connectable(
        vpn_user, owner_active=vpn_users_service.owner_active(db, vpn_user)
    )
    reseller.expires_at = datetime.now(timezone.utc) - timedelta(days=1)
    db.flush()
    assert not vpn_users_service.is_connectable(
        vpn_user, owner_active=vpn_users_service.owner_active(db, vpn_user)
    )


def test_node_allow_list_enforced(db) -> None:  # noqa: ANN001
    from app.services import resellers as resellers_service
    from app.services import vpn_users as vpn_users_service

    _admin(db)
    allowed = _node(db, "de1")
    blocked = _node(db, "de2")
    reseller = resellers_service.create_reseller(
        db, username="reseller1", password="reseller-pass", node_ids={allowed.id}
    )
    assert [n.id for n in resellers_service.allowed_nodes(db, reseller)] == [allowed.id]

    vpn_users_service.create_vpn_user(
        db, username="client1", password="client-pass", owner=reseller, node_id=allowed.id
    )
    with pytest.raises(vpn_users_service.VpnUserError):
        vpn_users_service.create_vpn_user(
            db, username="client2", password="client-pass", owner=reseller, node_id=blocked.id
        )


def test_unrestricted_reseller_sees_all_nodes(db) -> None:  # noqa: ANN001
    from app.services import resellers as resellers_service

    _admin(db)
    _node(db, "de1")
    _node(db, "de2")
    reseller = resellers_service.create_reseller(db, username="reseller1", password="reseller-pass")
    assert len(resellers_service.allowed_nodes(db, reseller)) == 2


def test_delete_reseller_unassigns_users(db) -> None:  # noqa: ANN001
    from app.services import resellers as resellers_service
    from app.services import vpn_users as vpn_users_service

    _admin(db)
    reseller = resellers_service.create_reseller(db, username="reseller1", password="reseller-pass")
    vpn_user = vpn_users_service.create_vpn_user(
        db, username="client1", password="client-pass", owner=reseller
    )
    resellers_service.delete_reseller(db, reseller)
    db.flush()
    assert vpn_user.owner_id is None
    assert vpn_users_service.get(db, vpn_user.id) is not None


def test_usage_aggregates(db) -> None:  # noqa: ANN001
    from app.services import resellers as resellers_service
    from app.services import vpn_users as vpn_users_service

    _admin(db)
    reseller = resellers_service.create_reseller(
        db, username="reseller1", password="reseller-pass", quota_users=5, max_data_bytes=1000
    )
    first = vpn_users_service.create_vpn_user(
        db, username="client1", password="client-pass", owner=reseller
    )
    second = vpn_users_service.create_vpn_user(
        db, username="client2", password="client-pass", owner=reseller
    )
    first.bytes_in, first.bytes_out = 100, 50
    second.bytes_in = 25
    db.flush()
    summary = resellers_service.usage(db, reseller)
    assert summary.user_count == 2
    assert summary.data_bytes == 175
    assert summary.users_remaining == 3
    assert summary.data_remaining == 825


# --------------------------------------------------------------------------- #
# Reports
# --------------------------------------------------------------------------- #
def test_report_daily_buckets_and_totals(db) -> None:  # noqa: ANN001
    from app.models.traffic_log import TrafficLog
    from app.services import reports as reports_service
    from app.services import resellers as resellers_service
    from app.services import vpn_users as vpn_users_service

    _admin(db)
    reseller = resellers_service.create_reseller(db, username="reseller1", password="reseller-pass")
    vpn_user = vpn_users_service.create_vpn_user(
        db, username="client1", password="client-pass", owner=reseller
    )
    now = datetime.now(timezone.utc)
    db.add(TrafficLog(vpn_user_id=vpn_user.id, bytes_in=10, bytes_out=5, recorded_at=now))
    db.add(
        TrafficLog(
            vpn_user_id=vpn_user.id,
            bytes_in=20,
            bytes_out=10,
            recorded_at=now - timedelta(days=1),
        )
    )
    db.flush()

    report = reports_service.build_report(db, owner_id=reseller.id, preset="daily", now=now)
    assert report.preset == "daily"
    assert len(report.buckets) == 14
    assert report.totals.bytes_in == 30
    assert report.totals.bytes_out == 15
    assert report.totals.users == 1
    assert report.series_in[-1] == 10  # today


def test_report_excludes_other_owners(db) -> None:  # noqa: ANN001
    from app.models.traffic_log import TrafficLog
    from app.services import reports as reports_service
    from app.services import resellers as resellers_service
    from app.services import vpn_users as vpn_users_service

    _admin(db)
    reseller = resellers_service.create_reseller(db, username="reseller1", password="reseller-pass")
    mine = vpn_users_service.create_vpn_user(
        db, username="client1", password="client-pass", owner=reseller
    )
    other = vpn_users_service.create_vpn_user(db, username="client2", password="client-pass")
    now = datetime.now(timezone.utc)
    db.add(TrafficLog(vpn_user_id=mine.id, bytes_in=10, recorded_at=now))
    db.add(TrafficLog(vpn_user_id=other.id, bytes_in=999, recorded_at=now))
    db.flush()

    report = reports_service.build_report(db, owner_id=reseller.id, preset="daily", now=now)
    assert report.totals.bytes_in == 10


def test_report_custom_range_validation() -> None:
    from datetime import date

    from app.services import reports as reports_service

    with pytest.raises(reports_service.ReportError):
        reports_service.resolve_range("custom", start=None, end=date.today())
    with pytest.raises(reports_service.ReportError):
        reports_service.resolve_range("nonsense")
    start, end, grain = reports_service.resolve_range(
        "custom", start=date(2026, 1, 1), end=date(2026, 1, 10)
    )
    assert grain == "daily"
    assert start < end


def test_report_weekly_and_monthly_presets() -> None:
    from app.services import reports as reports_service

    weekly = reports_service.resolve_range("weekly")
    assert weekly[2] == "weekly"
    monthly = reports_service.resolve_range("monthly")
    assert monthly[2] == "monthly"


# --------------------------------------------------------------------------- #
# API + audit
# --------------------------------------------------------------------------- #
def _admin_key() -> str:
    from app.db import get_session_factory
    from app.services import api_keys as api_keys_service
    from app.services import users as users_service

    db = get_session_factory()()
    try:
        admin = users_service.create_user(
            db, username="admin", password="admin-password", role=Role.ADMIN
        )
        _, raw = api_keys_service.create_key(db, admin, name="test")
        db.commit()
        return raw
    finally:
        db.close()


def test_api_reseller_crud_and_limits(client) -> None:  # noqa: ANN001
    key = _admin_key()
    headers = {"Authorization": f"Bearer {key}"}

    created = client.post(
        "/api/v1/resellers",
        json={"username": "reseller1", "password": "reseller-pass", "quota_users": 3},
        headers=headers,
    )
    assert created.status_code == 201
    reseller_id = created.json()["id"]
    assert created.json()["quota_users"] == 3

    patched = client.patch(
        f"/api/v1/resellers/{reseller_id}",
        json={"quota_users": 7, "max_data_bytes": 2048},
        headers=headers,
    )
    assert patched.status_code == 200
    assert patched.json()["quota_users"] == 7
    assert patched.json()["max_data_bytes"] == 2048

    usage = client.get(f"/api/v1/resellers/{reseller_id}/usage", headers=headers)
    assert usage.status_code == 200
    assert usage.json()["quota_users"] == 7
    assert usage.json()["user_count"] == 0

    listing = client.get("/api/v1/resellers", headers=headers)
    assert [r["username"] for r in listing.json()] == ["reseller1"]

    deleted = client.delete(f"/api/v1/resellers/{reseller_id}", headers=headers)
    assert deleted.status_code == 200


def test_api_reseller_endpoints_require_admin(client) -> None:  # noqa: ANN001
    from app.db import get_session_factory
    from app.services import api_keys as api_keys_service
    from app.services import users as users_service

    db = get_session_factory()()
    try:
        reseller = users_service.create_user(
            db, username="reseller1", password="reseller-pass", role=Role.RESELLER
        )
        _, raw = api_keys_service.create_key(db, reseller, name="test")
        db.commit()
    finally:
        db.close()

    headers = {"Authorization": f"Bearer {raw}"}
    assert client.get("/api/v1/resellers", headers=headers).status_code == 403
    assert client.get("/api/v1/reports", headers=headers).status_code == 200


def test_api_reports_scoped_to_reseller(client) -> None:  # noqa: ANN001
    key = _admin_key()
    headers = {"Authorization": f"Bearer {key}"}
    created = client.post(
        "/api/v1/resellers",
        json={"username": "reseller1", "password": "reseller-pass"},
        headers=headers,
    ).json()

    report = client.get("/api/v1/reports?preset=weekly", headers=headers)
    assert report.status_code == 200
    assert report.json()["preset"] == "weekly"
    assert len(report.json()["labels"]) == len(report.json()["bytes_in"])

    bad = client.get("/api/v1/reports?preset=nonsense", headers=headers)
    assert bad.status_code == 400

    usage = client.get(f"/api/v1/resellers/{created['id']}/usage", headers=headers)
    assert usage.json()["allowed_node_ids"] == []


def test_audit_log_records_and_lists(db) -> None:  # noqa: ANN001
    from app.services import audit as audit_service

    admin = _admin(db)
    audit_service.record(
        db, action="user.create", actor=admin, target_type="vpn_user", target_id=1, detail="x"
    )
    audit_service.record(db, action="reseller.create", actor=admin, target_id=2)
    db.flush()

    entries, total = audit_service.list_entries(db, limit=10)
    assert total == 2
    assert entries[0].action == "reseller.create"  # most recent first

    filtered, filtered_total = audit_service.list_entries(db, action="user.create")
    assert filtered_total == 1
    assert filtered[0].actor_label == "admin"

    assert set(audit_service.distinct_actions(db)) == {"user.create", "reseller.create"}


def test_reseller_web_pages_render(client) -> None:  # noqa: ANN001
    from app.db import get_session_factory
    from app.services import users as users_service

    db = get_session_factory()()
    try:
        users_service.create_user(db, username="admin", password="admin-password", role=Role.ADMIN)
        db.commit()
    finally:
        db.close()

    login = client.post(
        "/login", data={"username": "admin", "password": "admin-password"}, follow_redirects=False
    )
    assert login.status_code == 303

    for path, text in (
        ("/resellers", "Resellers"),
        ("/reports", "Reports"),
        ("/audit", "Audit log"),
        ("/permissions", "Permission matrix"),
    ):
        response = client.get(path)
        assert response.status_code == 200, path
        assert text in response.text


def test_reseller_create_partial(client) -> None:  # noqa: ANN001
    from app.db import get_session_factory
    from app.services import users as users_service

    db = get_session_factory()()
    try:
        users_service.create_user(db, username="admin", password="admin-password", role=Role.ADMIN)
        db.commit()
    finally:
        db.close()

    client.post("/login", data={"username": "admin", "password": "admin-password"})
    dashboard = client.get("/")
    csrf = dashboard.text.split('name="csrf-token" content="')[1].split('"')[0]

    response = client.post(
        "/partials/resellers",
        data={"username": "reseller1", "password": "reseller-pass", "csrf_token": csrf},
    )
    assert response.status_code == 200
    assert "reseller1" in response.text

    from app.db import get_session_factory as factory

    db = factory()()
    try:
        from app.models.user import User

        created = db.query(User).filter_by(username="reseller1").first()
        assert created is not None
        assert created.role == Role.RESELLER
    finally:
        db.close()


def test_disabled_reseller_blocks_connections(db) -> None:  # noqa: ANN001
    from app.services import resellers as resellers_service
    from app.services import vpn_users as vpn_users_service

    _admin(db)
    reseller = resellers_service.create_reseller(db, username="reseller1", password="reseller-pass")
    vpn_user = vpn_users_service.create_vpn_user(
        db, username="client1", password="client-pass", owner=reseller
    )
    reseller.is_active = False
    db.flush()
    assert not vpn_users_service.is_connectable(
        vpn_user, owner_active=vpn_users_service.owner_active(db, vpn_user)
    )
    assert vpn_user.status == VpnUserStatus.ACTIVE
