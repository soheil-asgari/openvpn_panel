"""Node (OpenVPN server) service."""

from __future__ import annotations

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import hmac_auth
from app.core.security import generate_token, hash_password
from app.core.validators import (
    validate_cipher,
    validate_hostname,
    validate_node_name,
    validate_port,
    validate_protocol,
)
from app.models.node import Node, NodeStatus


class NodeError(ValueError):
    """Domain error for node operations."""


def get(db: Session, node_id: int) -> Node | None:
    return db.get(Node, node_id)


def get_by_name(db: Session, name: str) -> Node | None:
    return db.scalar(select(Node).where(Node.name == name))


def list_nodes(
    db: Session, *, owner_id: int | None = None, active_only: bool = False
) -> list[Node]:
    stmt = select(Node).order_by(Node.name)
    if owner_id is not None:
        stmt = stmt.where(Node.owner_id == owner_id)
    if active_only:
        stmt = stmt.where(Node.is_active.is_(True))
    return list(db.scalars(stmt))


def create_node(
    db: Session,
    *,
    name: str,
    host: str,
    port: int = 1194,
    protocol: str = "udp",
    cipher: str = "AES-256-GCM",
    subnet: str = "10.8.0.0/24",
    agent_url: str | None = None,
    owner_id: int | None = None,
) -> tuple[Node, str | None]:
    """Create a node. Returns ``(node, agent_token)`` where the token is only
    available at creation time."""

    validate_node_name(name)
    validate_hostname(host)
    port = validate_port(port)
    protocol = validate_protocol(protocol)
    cipher = validate_cipher(cipher)
    if get_by_name(db, name) is not None:
        raise NodeError("node name already exists")

    agent_token: str | None = None
    token_hash: str | None = None
    agent_secret: str | None = None
    if agent_url:
        if not agent_url.startswith(("http://", "https://")):
            raise NodeError("agent_url must start with http:// or https://")
        agent_token = generate_token(32)
        token_hash = hash_password(agent_token)
        agent_secret = hmac_auth.generate_secret()

    node = Node(
        name=name,
        host=host,
        port=port,
        protocol=protocol,
        cipher=cipher,
        subnet=subnet,
        agent_url=agent_url,
        agent_token_hash=token_hash,
        agent_secret=agent_secret,
        owner_id=owner_id,
        status=NodeStatus.UNKNOWN,
    )
    db.add(node)
    db.flush()
    return node, agent_token


def update_node(db: Session, node: Node, **fields: object) -> Node:
    if "host" in fields and fields["host"]:
        node.host = validate_hostname(str(fields["host"]))
    if "port" in fields and fields["port"] is not None:
        node.port = validate_port(fields["port"])  # type: ignore[arg-type]
    if "protocol" in fields and fields["protocol"]:
        node.protocol = validate_protocol(str(fields["protocol"]))
    if "cipher" in fields and fields["cipher"]:
        node.cipher = validate_cipher(str(fields["cipher"]))
    if "subnet" in fields and fields["subnet"]:
        node.subnet = str(fields["subnet"])
    if "is_active" in fields and fields["is_active"] is not None:
        node.is_active = bool(fields["is_active"])
    db.flush()
    return node


def delete_node(db: Session, node: Node) -> None:
    db.delete(node)
    db.flush()


async def check_health(node: Node, timeout: float = 5.0) -> NodeStatus:
    """Ping the node agent's ``/health`` endpoint."""

    if not node.agent_url:
        return NodeStatus.UNKNOWN
    url = node.agent_url.rstrip("/") + "/health"
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url)
        return NodeStatus.ONLINE if resp.status_code == 200 else NodeStatus.OFFLINE
    except httpx.HTTPError:
        return NodeStatus.OFFLINE
