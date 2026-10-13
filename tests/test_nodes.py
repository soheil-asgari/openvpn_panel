"""Tests for the node service."""

from __future__ import annotations

import pytest

from app.core.validators import ValidationError
from app.models.node import NodeStatus
from app.services import nodes as nodes_service


def test_create_node(db) -> None:  # noqa: ANN001
    node, token = nodes_service.create_node(db, name="de1", host="10.0.0.1")
    assert node.port == 1194
    assert node.protocol == "udp"
    assert token is None  # no agent url => no token


def test_create_node_with_agent(db) -> None:  # noqa: ANN001
    node, token = nodes_service.create_node(
        db, name="de1", host="vpn.example.com", agent_url="http://127.0.0.1:8090"
    )
    assert token
    assert node.agent_token_hash
    assert token not in node.agent_token_hash  # stored hashed


def test_duplicate_node_name(db) -> None:  # noqa: ANN001
    nodes_service.create_node(db, name="de1", host="10.0.0.1")
    with pytest.raises(nodes_service.NodeError):
        nodes_service.create_node(db, name="de1", host="10.0.0.2")


def test_invalid_node_name(db) -> None:  # noqa: ANN001
    with pytest.raises(ValidationError):
        nodes_service.create_node(db, name="bad name!", host="10.0.0.1")


def test_invalid_host_rejected(db) -> None:  # noqa: ANN001
    with pytest.raises(ValidationError):
        nodes_service.create_node(db, name="de1", host="bad host;rm -rf /")


def test_invalid_agent_url(db) -> None:  # noqa: ANN001
    with pytest.raises(nodes_service.NodeError):
        nodes_service.create_node(db, name="de1", host="10.0.0.1", agent_url="ftp://x")


def test_update_node(db) -> None:  # noqa: ANN001
    node, _ = nodes_service.create_node(db, name="de1", host="10.0.0.1")
    nodes_service.update_node(db, node, host="10.0.0.9", port=443, is_active=False)
    assert node.host == "10.0.0.9"
    assert node.port == 443
    assert node.is_active is False


def test_update_rejects_bad_protocol(db) -> None:  # noqa: ANN001
    node, _ = nodes_service.create_node(db, name="de1", host="10.0.0.1")
    with pytest.raises(ValidationError):
        nodes_service.update_node(db, node, protocol="icmp")


def test_delete_node(db) -> None:  # noqa: ANN001
    node, _ = nodes_service.create_node(db, name="de1", host="10.0.0.1")
    nodes_service.delete_node(db, node)
    assert nodes_service.get(db, node.id) is None


def test_default_status_unknown(db) -> None:  # noqa: ANN001
    node, _ = nodes_service.create_node(db, name="de1", host="10.0.0.1")
    assert node.status == NodeStatus.UNKNOWN
