"""Tests for the multi-node features: HMAC auth, install command, sync queue."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.core import hmac_auth
from app.models.node import Node, NodeStatus
from app.models.node_task import NodeTaskStatus
from app.services import node_client, node_install


# --------------------------------------------------------------------------- #
# HMAC signing
# --------------------------------------------------------------------------- #
def test_hmac_roundtrip() -> None:
    headers = hmac_auth.build_headers("s3cret", method="POST", path="/users", body=b"{}")
    assert hmac_auth.verify(
        "s3cret",
        method="POST",
        path="/users",
        timestamp=headers[hmac_auth.TIMESTAMP_HEADER],
        signature=headers[hmac_auth.SIGNATURE_HEADER],
        body=b"{}",
    )


def test_hmac_rejects_wrong_secret_and_body() -> None:
    headers = hmac_auth.build_headers("s3cret", method="GET", path="/health")
    assert not hmac_auth.verify(
        "other",
        method="GET",
        path="/health",
        timestamp=headers[hmac_auth.TIMESTAMP_HEADER],
        signature=headers[hmac_auth.SIGNATURE_HEADER],
    )
    assert not hmac_auth.verify(
        "s3cret",
        method="GET",
        path="/health",
        timestamp=headers[hmac_auth.TIMESTAMP_HEADER],
        signature=headers[hmac_auth.SIGNATURE_HEADER],
        body=b"tampered",
    )


def test_hmac_rejects_stale_timestamp() -> None:
    import time

    old = str(int(time.time()) - 10_000)
    signature = hmac_auth.sign("s", method="GET", path="/x", timestamp=old)
    assert not hmac_auth.verify(
        "s", method="GET", path="/x", timestamp=old, signature=signature, window=300
    )


def test_hmac_rejects_missing_fields() -> None:
    assert not hmac_auth.verify("s", method="GET", path="/x", timestamp=None, signature="abc")
    assert not hmac_auth.verify("s", method="GET", path="/x", timestamp="abc", signature="abc")


# --------------------------------------------------------------------------- #
# Install command
# --------------------------------------------------------------------------- #
def test_node_agent_port_is_stable(db) -> None:  # noqa: ANN001
    from app.services import nodes as nodes_service

    node, _ = nodes_service.create_node(db, name="de1", host="10.0.0.1")
    db.flush()
    assert node_install.agent_port(node) == node_install.agent_port(node)
    assert 8090 <= node_install.agent_port(node) <= 8099


def test_install_command_contains_secret(db) -> None:  # noqa: ANN001
    from app.services import nodes as nodes_service

    node, _ = nodes_service.create_node(
        db, name="de1", host="vpn.example.com", agent_url="http://vpn.example.com:8091"
    )
    db.flush()
    assert node.agent_secret
    command = node_install.build_install_command(node, panel_url="https://panel.example.com")
    assert node.agent_secret in command
    assert "install.sh" in command
    assert "--node_name de1" in command
    assert "panel.example.com" in command


def test_install_script_is_shell() -> None:
    script = node_install.build_install_script()
    assert script.startswith("#!/usr/bin/env bash")
    assert "OVPN_AGENT_HMAC_SECRET" in script
    assert "systemctl enable --now" in script


def test_create_node_generates_secret(db) -> None:  # noqa: ANN001
    from app.services import nodes as nodes_service

    node, token = nodes_service.create_node(
        db, name="de1", host="1.2.3.4", agent_url="http://1.2.3.4:8090"
    )
    assert token
    assert node.agent_secret
    assert len(node.agent_secret) >= 16


# --------------------------------------------------------------------------- #
# User serialization & client
# --------------------------------------------------------------------------- #
def test_serialize_user(db) -> None:  # noqa: ANN001
    from app.services import node_sync
    from app.services import vpn_users as vpn_users_service

    vpn_user = vpn_users_service.create_vpn_user(
        db, username="client1", password="client-pass", data_limit_bytes=1000, speed_limit_kbps=512
    )
    payload = node_sync.serialize_user(vpn_user)
    assert payload["username"] == "client1"
    assert payload["common_name"] == "client1"
    assert payload["data_limit_bytes"] == 1000
    assert payload["speed_limit_kbps"] == 512
    assert payload["status"] == "active"


def test_node_client_requires_agent_url(db) -> None:  # noqa: ANN001
    from app.services import nodes as nodes_service

    node, _ = nodes_service.create_node(db, name="de1", host="10.0.0.1")
    db.flush()
    with pytest.raises(node_client.NodeClientError):
        node_client.NodeAgentClient(node)


# --------------------------------------------------------------------------- #
# Retry queue
# --------------------------------------------------------------------------- #
def test_enqueue_and_retry_on_unreachable(db) -> None:  # noqa: ANN001
    from app.services import node_sync
    from app.services import nodes as nodes_service
    from app.services import vpn_users as vpn_users_service

    node, _ = nodes_service.create_node(
        db, name="de1", host="10.255.255.1", agent_url="http://10.255.255.1:9"
    )
    db.flush()
    vpn_user = vpn_users_service.create_vpn_user(
        db, username="client1", password="client-pass", node_id=node.id
    )
    pushed = node_sync.push_user(db, vpn_user)
    assert pushed is False

    tasks = node_sync.pending_tasks(db, node_id=node.id)
    assert len(tasks) == 1
    assert tasks[0].kind == "sync_user"
    assert tasks[0].status == NodeTaskStatus.PENDING

    # Dispatching against an unreachable node schedules a retry.
    task = tasks[0]
    assert node_sync.dispatch_task(db, task) is False
    assert task.attempts == 1
    assert task.status == NodeTaskStatus.PENDING
    assert task.run_after > datetime.now(timezone.utc)


def test_task_exhausts_attempts(db) -> None:  # noqa: ANN001
    from app.services import node_sync

    task = node_sync.enqueue(db, node_id=None, kind="sync_user", payload={"username": "x"})
    task.max_attempts = 1
    db.flush()
    assert node_sync.dispatch_task(db, task) is False
    assert task.status == NodeTaskStatus.FAILED


def test_run_pending_returns_success_count(db) -> None:  # noqa: ANN001
    from app.services import node_sync

    node_sync.enqueue(db, node_id=None, kind="sync_user", payload={})
    db.flush()
    assert node_sync.run_pending(db) == 0  # node missing -> failed, not success


def test_reconcile_without_agent(db) -> None:  # noqa: ANN001
    from app.services import node_sync
    from app.services import nodes as nodes_service

    node, _ = nodes_service.create_node(db, name="de1", host="10.0.0.1")
    db.flush()
    assert node_sync.reconcile_node(db, node) == (0, 0)


def test_collect_metrics_marks_offline(db) -> None:  # noqa: ANN001
    from app.services import node_sync
    from app.services import nodes as nodes_service

    node, _ = nodes_service.create_node(
        db, name="de1", host="10.255.255.1", agent_url="http://10.255.255.1:9"
    )
    db.flush()
    assert node_sync.collect_metrics(db, node) is False
    assert node.status == NodeStatus.OFFLINE


def test_latest_metric_none_initially(db) -> None:  # noqa: ANN001
    from app.services import node_sync
    from app.services import nodes as nodes_service

    node, _ = nodes_service.create_node(db, name="de1", host="10.0.0.1")
    db.flush()
    assert node_sync.latest_metric(db, node.id) is None


# --------------------------------------------------------------------------- #
# API
# --------------------------------------------------------------------------- #
def _admin_key() -> str:
    from app.db import get_session_factory
    from app.models.user import Role
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


def test_api_install_command(client) -> None:  # noqa: ANN001
    raw = _admin_key()
    headers = {"Authorization": f"Bearer {raw}"}
    node = client.post(
        "/api/v1/nodes",
        json={"name": "de1", "host": "vpn.example.com", "agent_url": "http://vpn.example.com:8090"},
        headers=headers,
    ).json()

    command = client.get(f"/api/v1/nodes/{node['id']}/install-command", headers=headers)
    assert command.status_code == 200
    body = command.json()
    assert "install.sh" in body["command"]
    assert body["install_script_url"].endswith("/nodes/agent/install.sh")


def test_api_node_config_roundtrip(client) -> None:  # noqa: ANN001
    raw = _admin_key()
    headers = {"Authorization": f"Bearer {raw}"}
    node = client.post(
        "/api/v1/nodes",
        json={"name": "de1", "host": "10.0.0.1"},
        headers=headers,
    ).json()

    saved = client.put(
        f"/api/v1/nodes/{node['id']}/config",
        json={"content": "port 1194\nproto udp\n"},
        headers=headers,
    )
    assert saved.status_code == 200

    fetched = client.get(f"/api/v1/nodes/{node['id']}/config", headers=headers)
    assert fetched.status_code == 200
    assert "port 1194" in fetched.json()["content"]


def test_api_node_health_and_tasks(client) -> None:  # noqa: ANN001
    raw = _admin_key()
    headers = {"Authorization": f"Bearer {raw}"}
    node = client.post(
        "/api/v1/nodes",
        json={"name": "de1", "host": "10.255.255.1", "agent_url": "http://10.255.255.1:9"},
        headers=headers,
    ).json()

    health = client.get(f"/api/v1/nodes/{node['id']}/health", headers=headers)
    assert health.status_code == 200
    assert health.json()["reachable"] is False
    assert health.json()["status"] == "offline"

    tasks = client.get(f"/api/v1/nodes/{node['id']}/tasks", headers=headers)
    assert tasks.status_code == 200
    assert isinstance(tasks.json(), list)


def test_api_node_endpoints_require_admin(client) -> None:  # noqa: ANN001
    from app.db import get_session_factory
    from app.models.user import Role
    from app.services import api_keys as api_keys_service
    from app.services import users as users_service

    db = get_session_factory()()
    try:
        reseller = users_service.create_user(
            db, username="reseller", password="reseller-pass", role=Role.RESELLER
        )
        _, raw = api_keys_service.create_key(db, reseller, name="test")
        db.commit()
    finally:
        db.close()
    headers = {"Authorization": f"Bearer {raw}"}
    assert client.get("/api/v1/nodes/1/health", headers=headers).status_code == 403
    assert client.get("/api/v1/nodes/1/install-command", headers=headers).status_code == 403


def test_agent_install_script_requires_admin(client) -> None:  # noqa: ANN001
    assert client.get("/nodes/agent/install.sh", follow_redirects=False).status_code in (303, 401)


def test_nodes_page_renders(client) -> None:  # noqa: ANN001
    from app.db import get_session_factory
    from app.models.user import Role
    from app.services import users as users_service

    db = get_session_factory()()
    try:
        users_service.create_user(db, username="admin", password="admin-password", role=Role.ADMIN)
        db.commit()
    finally:
        db.close()
    client.post("/login", data={"username": "admin", "password": "admin-password"})
    response = client.get("/nodes")
    assert response.status_code == 200
    assert "Nodes" in response.text


def test_node_detail_page_renders(client) -> None:  # noqa: ANN001
    from app.db import get_session_factory
    from app.models.user import Role
    from app.services import nodes as nodes_service
    from app.services import users as users_service

    db = get_session_factory()()
    try:
        users_service.create_user(db, username="admin", password="admin-password", role=Role.ADMIN)
        node, _ = nodes_service.create_node(db, name="de1", host="10.0.0.1")
        db.commit()
        node_id = node.id
    finally:
        db.close()

    client.post("/login", data={"username": "admin", "password": "admin-password"})
    response = client.get(f"/nodes/{node_id}")
    assert response.status_code == 200
    assert "Install the node agent" in response.text


def test_metrics_persisted_and_reported(db) -> None:  # noqa: ANN001
    from app.models.node_metric import NodeMetric
    from app.services import node_sync
    from app.services import nodes as nodes_service

    node, _ = nodes_service.create_node(db, name="de1", host="10.0.0.1")
    db.flush()
    db.add(
        NodeMetric(
            node_id=node.id,
            cpu_percent=12.5,
            mem_total=1000,
            mem_used=400,
            client_count=3,
        )
    )
    db.flush()
    metric = node_sync.latest_metric(db, node.id)
    assert metric is not None
    assert metric.cpu_percent == 12.5
    assert metric.client_count == 3


def test_node_task_model_defaults(db) -> None:  # noqa: ANN001
    from app.models.node_task import NodeTask
    from app.services import nodes as nodes_service

    node, _ = nodes_service.create_node(db, name="de1", host="10.0.0.1")
    db.flush()
    task = NodeTask(node_id=node.id, kind="reload")
    db.add(task)
    db.flush()
    assert task.status == NodeTaskStatus.PENDING
    assert task.max_attempts == 10
    assert isinstance(node, Node)
