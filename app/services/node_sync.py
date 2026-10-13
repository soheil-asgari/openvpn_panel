"""Sync VPN users/limits to nodes and reconcile them when nodes come online.

Operations against an unreachable node are persisted to the ``node_tasks``
retry queue and replayed by the scheduler until they succeed or exhaust their
attempt budget.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.node import Node, NodeStatus
from app.models.node_task import NodeTask, NodeTaskStatus
from app.models.vpn_user import VpnUser
from app.services import node_client

RETRY_BACKOFF_SECONDS = 60


def _now() -> datetime:
    return datetime.now(timezone.utc)


def serialize_user(vpn_user: VpnUser) -> dict:
    """The payload a node needs to enforce a VPN user locally."""

    return {
        "username": vpn_user.username,
        "common_name": vpn_user.common_name or vpn_user.username,
        "status": vpn_user.status.value,
        "data_limit_bytes": vpn_user.data_limit_bytes or 0,
        "used_bytes": vpn_user.used_bytes,
        "speed_limit_kbps": vpn_user.speed_limit_kbps or 0,
        "max_connections": vpn_user.max_connections or 0,
        "expires_at": vpn_user.effective_expiry.isoformat() if vpn_user.effective_expiry else None,
    }


# --------------------------------------------------------------------------- #
# Retry queue
# --------------------------------------------------------------------------- #
def enqueue(
    db: Session,
    *,
    node_id: int | None,
    kind: str,
    payload: dict | None = None,
    run_after: datetime | None = None,
) -> NodeTask:
    task = NodeTask(
        node_id=node_id,
        kind=kind,
        payload=json.dumps(payload) if payload is not None else None,
        status=NodeTaskStatus.PENDING,
        run_after=run_after or _now(),
    )
    db.add(task)
    db.flush()
    return task


def pending_tasks(db: Session, *, node_id: int | None = None, limit: int = 100) -> list[NodeTask]:
    stmt = (
        select(NodeTask)
        .where(
            NodeTask.status == NodeTaskStatus.PENDING,
            NodeTask.run_after <= _now(),
        )
        .order_by(NodeTask.run_after.asc())
        .limit(limit)
    )
    if node_id is not None:
        stmt = stmt.where(NodeTask.node_id == node_id)
    return list(db.scalars(stmt))


def _reschedule(task: NodeTask, error: str) -> None:
    task.attempts += 1
    task.last_error = error[:500]
    if task.attempts >= task.max_attempts:
        task.status = NodeTaskStatus.FAILED
        task.completed_at = _now()
    else:
        task.status = NodeTaskStatus.PENDING
        task.run_after = _now() + timedelta(seconds=RETRY_BACKOFF_SECONDS * task.attempts)


def dispatch_task(db: Session, task: NodeTask) -> bool:
    """Execute a queued task. Returns ``True`` on success."""

    node = db.get(Node, task.node_id) if task.node_id else None
    if node is None:
        task.status = NodeTaskStatus.FAILED
        task.last_error = "node not found"
        task.completed_at = _now()
        return False
    payload = json.loads(task.payload) if task.payload else {}
    try:
        if task.kind == "sync_user":
            result = node_client.NodeAgentClient(node).sync_user(payload)
        elif task.kind == "delete_user":
            result = node_client.NodeAgentClient(node).delete_user(payload.get("username", ""))
        elif task.kind == "kill":
            result = node_client.NodeAgentClient(node).kill(payload.get("common_name", ""))
        elif task.kind == "reload":
            result = node_client.NodeAgentClient(node).reload()
        elif task.kind == "protocol_user":
            return _dispatch_protocol_user(db, node, payload)
        else:
            _reschedule(task, f"unknown task kind: {task.kind}")
            return False
    except node_client.NodeClientError as exc:
        _reschedule(task, str(exc))
        return False
    if not result.ok:
        _reschedule(task, result.error or "agent error")
        return False
    task.status = NodeTaskStatus.DONE
    task.attempts += 1
    task.completed_at = _now()
    task.last_error = None
    return True


def _dispatch_protocol_user(db: Session, node: Node, payload: dict) -> bool:
    """Replay a queued per-protocol user operation through the backend."""

    from app.models.protocol import ProtocolInstance, ProtocolUser
    from app.services import protocols_service

    instance = db.get(ProtocolInstance, int(payload.get("instance_id") or 0))
    username = str(payload.get("username") or "")
    op = str(payload.get("op") or "create")
    if instance is None or not username:
        return True  # nothing to do; treat as success to drain the queue
    vpn_user = db.scalar(select(VpnUser).where(VpnUser.username == username))
    if vpn_user is None:
        return True
    row = db.scalar(
        select(ProtocolUser).where(
            ProtocolUser.vpn_user_id == vpn_user.id,
            ProtocolUser.protocol_instance_id == instance.id,
        )
    )
    if row is None:
        return True
    protocols_service.replay_operation(db, row, instance, vpn_user, node=node, op=op)
    return True


def run_pending(db: Session, *, limit: int = 50) -> int:
    """Process due tasks; returns the number that succeeded."""

    successes = 0
    for task in pending_tasks(db, limit=limit):
        if dispatch_task(db, task):
            successes += 1
    db.flush()
    return successes


# --------------------------------------------------------------------------- #
# Pushing state to nodes
# --------------------------------------------------------------------------- #
def push_user(db: Session, vpn_user: VpnUser, *, queue_on_failure: bool = True) -> bool:
    """Sync a single user to their node (queued on failure)."""

    if vpn_user.node_id is None:
        return False
    node = db.get(Node, vpn_user.node_id)
    if node is None or not node.agent_url:
        return False
    payload = serialize_user(vpn_user)
    try:
        result = node_client.NodeAgentClient(node).sync_user(payload)
    except node_client.NodeClientError:
        if queue_on_failure:
            enqueue(db, node_id=node.id, kind="sync_user", payload=payload)
        return False
    if result.ok:
        return True
    if queue_on_failure:
        enqueue(db, node_id=node.id, kind="sync_user", payload=payload)
    return False


def remove_user(db: Session, vpn_user: VpnUser, *, queue_on_failure: bool = True) -> bool:
    if vpn_user.node_id is None:
        return False
    node = db.get(Node, vpn_user.node_id)
    if node is None or not node.agent_url:
        return False
    try:
        result = node_client.NodeAgentClient(node).delete_user(vpn_user.username)
    except node_client.NodeClientError:
        if queue_on_failure:
            enqueue(
                db, node_id=node.id, kind="delete_user", payload={"username": vpn_user.username}
            )
        return False
    if not result.ok and queue_on_failure:
        enqueue(db, node_id=node.id, kind="delete_user", payload={"username": vpn_user.username})
    return result.ok


def reconcile_node(db: Session, node: Node) -> tuple[int, int]:
    """Push all users assigned to a node. Returns ``(synced, queued)``."""

    if not node.agent_url:
        return 0, 0
    users = list(db.scalars(select(VpnUser).where(VpnUser.node_id == node.id)))
    synced = 0
    queued = 0
    for vpn_user in users:
        if push_user(db, vpn_user, queue_on_failure=False):
            synced += 1
        else:
            enqueue(db, node_id=node.id, kind="sync_user", payload=serialize_user(vpn_user))
            queued += 1
    db.flush()
    return synced, queued


# --------------------------------------------------------------------------- #
# Collecting state back from nodes
# --------------------------------------------------------------------------- #
def collect_metrics(db: Session, node: Node) -> bool:
    """Fetch a health sample from a node and persist it."""

    from app.models.node_metric import NodeMetric

    if not node.agent_url:
        return False
    client = node_client.NodeAgentClient(node)
    try:
        health = client.health()
    except node_client.NodeClientError:
        node.status = NodeStatus.OFFLINE
        db.flush()
        return False
    if not health.ok:
        node.status = NodeStatus.OFFLINE
        db.flush()
        return False

    node.status = NodeStatus.ONLINE
    node.last_seen_at = _now()
    try:
        metrics = client.metrics()
    except node_client.NodeClientError:
        metrics = node_client.AgentResult(ok=False)
    if metrics.ok:
        payload = metrics.data
        db.add(
            NodeMetric(
                node_id=node.id,
                cpu_percent=float(payload.get("cpu_percent") or 0.0),
                load1=float(payload.get("load1") or 0.0),
                mem_total=int(payload.get("mem_total") or 0),
                mem_used=int(payload.get("mem_used") or 0),
                disk_total=int(payload.get("disk_total") or 0),
                disk_used=int(payload.get("disk_used") or 0),
                net_rx=int(payload.get("net_rx") or 0),
                net_tx=int(payload.get("net_tx") or 0),
                client_count=int(payload.get("client_count") or 0),
                uptime_seconds=int(payload.get("uptime_seconds") or 0),
            )
        )
    db.flush()
    return True


def latest_metric(db: Session, node_id: int):  # noqa: ANN201
    from app.models.node_metric import NodeMetric

    return db.scalar(
        select(NodeMetric)
        .where(NodeMetric.node_id == node_id)
        .order_by(NodeMetric.recorded_at.desc())
        .limit(1)
    )
