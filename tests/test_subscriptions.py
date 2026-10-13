"""Tests for subscription tokens, public page, config delivery and QR codes."""

from __future__ import annotations

from app.core import qr
from app.db import get_session_factory


def _admin() -> None:
    from app.models.user import Role
    from app.services import users as users_service

    db = get_session_factory()()
    try:
        users_service.create_user(db, username="admin", password="admin-password", role=Role.ADMIN)
        db.commit()
    finally:
        db.close()


def _make_user(node_id: int | None = None, username: str = "alice") -> int:
    from app.services import vpn_users as vpn_users_service

    db = get_session_factory()()
    try:
        user = vpn_users_service.create_vpn_user(
            db,
            username=username,
            password="alice-password",
            node_id=node_id,
            data_limit_bytes=1024 * 1024 * 100,
        )
        db.commit()
        return user.id
    finally:
        db.close()


def _make_node(name: str = "de") -> int:
    from app.services import nodes as nodes_service

    db = get_session_factory()()
    try:
        node, _ = nodes_service.create_node(
            db, name=name, host="vpn.example.com", port=1194, protocol="udp"
        )
        db.commit()
        return node.id
    finally:
        db.close()


def _token_for(user_id: int) -> str:
    from app.models.vpn_user import VpnUser
    from app.services import subscriptions as subscriptions_service

    db = get_session_factory()()
    try:
        vpn_user = db.get(VpnUser, user_id)
        token = subscriptions_service.ensure_token(db, vpn_user)
        db.commit()
        return token
    finally:
        db.close()


def test_token_is_stable_and_regenerable(db) -> None:
    from app.services import subscriptions as subscriptions_service
    from app.services import vpn_users as vpn_users_service

    user = vpn_users_service.create_vpn_user(db, username="bob", password="password-123")
    db.commit()
    first = subscriptions_service.ensure_token(db, user)
    db.commit()
    assert first
    assert subscriptions_service.ensure_token(db, user) == first
    second = subscriptions_service.regenerate_token(db, user)
    db.commit()
    assert second != first
    assert subscriptions_service.get_by_token(db, first) is None
    assert subscriptions_service.get_by_token(db, second).id == user.id


def test_hidden_nodes_are_excluded(db) -> None:
    from app.services import subscriptions as subscriptions_service
    from app.services import vpn_users as vpn_users_service

    node_a = _make_node("aa")
    node_b = _make_node("bb")
    user = vpn_users_service.create_vpn_user(db, username="carol", password="password-123")
    db.commit()
    assert {n.id for n in subscriptions_service.visible_nodes(db, user)} == {node_a, node_b}

    subscriptions_service.set_hidden_nodes(db, user, {node_b})
    db.commit()
    assert {n.id for n in subscriptions_service.visible_nodes(db, user)} == {node_a}

    subscriptions_service.set_hidden_nodes(db, user, set())
    db.commit()
    assert {n.id for n in subscriptions_service.visible_nodes(db, user)} == {node_a, node_b}


def test_public_subscription_page_and_config(client) -> None:  # noqa: ANN001
    node_id = _make_node("nl")
    user_id = _make_user(node_id)
    token = _token_for(user_id)

    page = client.get(f"/sub/{token}")
    assert page.status_code == 200
    assert "alice" in page.text

    config = client.get(f"/sub/{token}/config/{node_id}.ovpn")
    assert config.status_code == 200
    assert "client" in config.text
    assert "remote vpn.example.com 1194 udp" in config.text
    assert "attachment" in config.headers["content-disposition"]

    qr_response = client.get(f"/sub/{token}/qr.svg?node_id={node_id}")
    assert qr_response.status_code == 200
    assert qr_response.headers["content-type"].startswith("image/svg+xml")
    assert "<svg" in qr_response.text


def test_unknown_token_returns_404(client) -> None:  # noqa: ANN001
    assert client.get("/sub/does-not-exist").status_code == 404


def test_config_endpoint_blocks_hidden_node(client) -> None:  # noqa: ANN001
    from app.models.vpn_user import VpnUser
    from app.services import subscriptions as subscriptions_service

    node_a = _make_node("sa-one")
    node_b = _make_node("sa-two")
    user_id = _make_user(node_a)
    token = _token_for(user_id)

    db = get_session_factory()()
    try:
        vpn_user = db.get(VpnUser, user_id)
        subscriptions_service.set_hidden_nodes(db, vpn_user, {node_b})
        db.commit()
    finally:
        db.close()

    assert client.get(f"/sub/{token}/config/{node_a}.ovpn").status_code == 200
    assert client.get(f"/sub/{token}/config/{node_b}.ovpn").status_code == 404


def test_api_subscription_endpoints(client) -> None:  # noqa: ANN001
    _admin()
    node_id = _make_node("api")
    user_id = _make_user(node_id)

    from app.models.user import User
    from app.services import api_keys as api_keys_service

    db = get_session_factory()()
    try:
        admin = db.query(User).first()
        _key, raw = api_keys_service.create_key(db, admin, name="test")
        db.commit()
    finally:
        db.close()

    headers = {"Authorization": f"Bearer {raw}"}

    info = client.get(f"/api/v1/users/{user_id}/subscription", headers=headers)
    assert info.status_code == 200
    body = info.json()
    assert "/sub/" in body["url"]
    assert body["visible_node_ids"] == [node_id]

    hidden = client.put(
        f"/api/v1/users/{user_id}/subscription/visibility",
        headers=headers,
        json={"node_ids": []},
    )
    assert hidden.status_code == 200
    assert hidden.json()["visible_node_ids"] == []
    assert hidden.json()["hidden_node_ids"] == [node_id]

    regenerated = client.post(f"/api/v1/users/{user_id}/subscription/token", headers=headers)
    assert regenerated.status_code == 200
    assert regenerated.json()["token"] != body["token"]

    bad = client.put(
        f"/api/v1/users/{user_id}/subscription/visibility",
        headers=headers,
        json={"node_ids": [999999]},
    )
    assert bad.status_code == 400


def test_qr_svg_is_scannable_shape() -> None:
    matrix = qr.build_qr_matrix("https://panel.example.com/sub/abc123/config/1.ovpn")
    size = len(matrix)
    assert size == len(matrix[0])
    # Finder pattern: dark outer ring and centre, light separators/ring.
    assert matrix[0][0] is True
    assert matrix[1][1] is False
    assert matrix[3][3] is True
    assert matrix[0][size - 1] is True
    svg = qr.to_svg("hello")
    assert svg.startswith("<svg")
    assert "data:image/svg+xml" in qr.to_data_uri("hello")
