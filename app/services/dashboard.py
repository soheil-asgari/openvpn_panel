"""Dashboard aggregation: totals, system metrics, traffic series and top users."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.node import Node
from app.models.node_metric import NodeMetric
from app.models.traffic_log import TrafficLog
from app.models.vpn_user import VpnUser


@dataclass(slots=True)
class TopUser:
    username: str
    used_bytes: int
    is_online: bool


@dataclass(slots=True)
class SystemMetrics:
    """Aggregated resource usage across all nodes (latest sample each)."""

    cpu_percent: float = 0.0
    mem_total: int = 0
    mem_used: int = 0
    disk_total: int = 0
    disk_used: int = 0
    net_rx: int = 0
    net_tx: int = 0
    client_count: int = 0
    nodes_online: int = 0
    nodes_total: int = 0
    available: bool = False

    @property
    def mem_percent(self) -> float:
        return round(self.mem_used / self.mem_total * 100, 1) if self.mem_total else 0.0

    @property
    def disk_percent(self) -> float:
        return round(self.disk_used / self.disk_total * 100, 1) if self.disk_total else 0.0


@dataclass(slots=True)
class DashboardData:
    total_users: int = 0
    online_users: int = 0
    total_nodes: int = 0
    total_bytes: int = 0
    metrics: SystemMetrics = field(default_factory=SystemMetrics)
    top_users: list[TopUser] = field(default_factory=list)
    traffic_labels: list[str] = field(default_factory=list)
    traffic_in: list[int] = field(default_factory=list)
    traffic_out: list[int] = field(default_factory=list)

    @property
    def has_metrics(self) -> bool:
        return self.metrics.available


def _owner_filter(owner_id: int | None):  # noqa: ANN201
    return [] if owner_id is None else [VpnUser.owner_id == owner_id]


def _latest_metrics(db: Session) -> list[NodeMetric]:
    """Latest metric row per node (portable across SQLite/PostgreSQL)."""

    metrics: list[NodeMetric] = []
    for node in db.scalars(select(Node)):
        row = db.scalar(
            select(NodeMetric)
            .where(NodeMetric.node_id == node.id)
            .order_by(NodeMetric.recorded_at.desc())
            .limit(1)
        )
        if row is not None:
            metrics.append(row)
    return metrics


def system_metrics(db: Session) -> SystemMetrics:
    nodes = list(db.scalars(select(Node)))
    metrics = SystemMetrics(nodes_total=len(nodes))
    metrics.nodes_online = sum(1 for n in nodes if n.status.value == "online")
    samples = _latest_metrics(db)
    if not samples:
        return metrics
    metrics.available = True
    for sample in samples:
        metrics.cpu_percent += sample.cpu_percent
        metrics.mem_total += sample.mem_total
        metrics.mem_used += sample.mem_used
        metrics.disk_total += sample.disk_total
        metrics.disk_used += sample.disk_used
        metrics.net_rx += sample.net_rx
        metrics.net_tx += sample.net_tx
        metrics.client_count += sample.client_count
    if samples:
        metrics.cpu_percent = round(metrics.cpu_percent / len(samples), 1)
    return metrics


def top_users(db: Session, *, owner_id: int | None = None, limit: int = 10) -> list[TopUser]:
    stmt = (
        select(VpnUser)
        .order_by((VpnUser.bytes_in + VpnUser.bytes_out).desc(), VpnUser.username.asc())
        .limit(limit)
    )
    stmt = stmt.where(*_owner_filter(owner_id)) if owner_id is not None else stmt
    return [
        TopUser(username=u.username, used_bytes=u.used_bytes, is_online=u.is_online)
        for u in db.scalars(stmt)
    ]


def traffic_series(
    db: Session, *, owner_id: int | None = None, days: int = 14
) -> tuple[list[str], list[int], list[int]]:
    """Daily in/out totals for the last ``days`` days."""

    now = datetime.now(timezone.utc)
    start = (now - timedelta(days=days - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
    stmt = select(TrafficLog).where(TrafficLog.recorded_at >= start)
    if owner_id is not None:
        user_ids = list(db.scalars(select(VpnUser.id).where(VpnUser.owner_id == owner_id)))
        if not user_ids:
            labels = [(start + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days)]
            return labels, [0] * days, [0] * days
        stmt = stmt.where(TrafficLog.vpn_user_id.in_(user_ids))

    buckets: dict[str, list[int]] = {}
    labels = [(start + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days)]
    for label in labels:
        buckets[label] = [0, 0]
    for row in db.scalars(stmt):
        recorded = row.recorded_at
        if recorded.tzinfo is None:
            recorded = recorded.replace(tzinfo=timezone.utc)
        key = recorded.strftime("%Y-%m-%d")
        if key in buckets:
            buckets[key][0] += row.bytes_in
            buckets[key][1] += row.bytes_out
    return labels, [buckets[label][0] for label in labels], [buckets[label][1] for label in labels]


def build(db: Session, *, owner_id: int | None = None) -> DashboardData:
    data = DashboardData()
    data.total_users = int(
        db.scalar(select(func.count(VpnUser.id)).where(*_owner_filter(owner_id))) or 0
    )
    data.online_users = int(
        db.scalar(
            select(func.count(VpnUser.id)).where(
                VpnUser.is_online.is_(True), *_owner_filter(owner_id)
            )
        )
        or 0
    )
    data.total_nodes = int(db.scalar(select(func.count(Node.id))) or 0)
    total_stmt = select(func.coalesce(func.sum(VpnUser.bytes_in + VpnUser.bytes_out), 0)).where(
        *_owner_filter(owner_id)
    )
    data.total_bytes = int(db.scalar(total_stmt) or 0)

    if owner_id is None:
        data.metrics = system_metrics(db)
    else:
        data.metrics = SystemMetrics(nodes_total=data.total_nodes)

    data.top_users = top_users(db, owner_id=owner_id)
    labels, series_in, series_out = traffic_series(db, owner_id=owner_id)
    data.traffic_labels = labels
    data.traffic_in = series_in
    data.traffic_out = series_out
    return data
