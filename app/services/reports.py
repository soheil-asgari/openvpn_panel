"""Usage reporting: per-owner traffic series and totals.

Built on the immutable :class:`~app.models.traffic_log.TrafficLog` rows so it
works for admins (all users) and resellers (only their own users). Supports
daily, weekly, monthly and arbitrary custom date ranges.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.node import Node
from app.models.traffic_log import TrafficLog
from app.models.vpn_user import VpnUser

PRESETS = ("daily", "weekly", "monthly", "custom")


class ReportError(ValueError):
    """Invalid report parameters."""


@dataclass(slots=True)
class Bucket:
    label: str
    bytes_in: int
    bytes_out: int

    @property
    def total(self) -> int:
        return self.bytes_in + self.bytes_out


@dataclass(slots=True)
class Totals:
    bytes_in: int = 0
    bytes_out: int = 0
    users: int = 0

    @property
    def total(self) -> int:
        return self.bytes_in + self.bytes_out


@dataclass(slots=True)
class Report:
    """A resolved usage report ready for rendering."""

    preset: str
    start: datetime
    end: datetime
    buckets: list[Bucket] = field(default_factory=list)
    totals: Totals = field(default_factory=lambda: Totals())
    by_node: list[tuple[str, int]] = field(default_factory=list)
    owner_id: int | None = None
    owner_label: str | None = None

    @property
    def labels(self) -> list[str]:
        return [b.label for b in self.buckets]

    @property
    def series_in(self) -> list[int]:
        return [b.bytes_in for b in self.buckets]

    @property
    def series_out(self) -> list[int]:
        return [b.bytes_out for b in self.buckets]


def _start_of_day(value: date) -> datetime:
    return datetime.combine(value, time.min, tzinfo=timezone.utc)


def _end_of_day(value: date) -> datetime:
    return datetime.combine(value, time.max, tzinfo=timezone.utc)


def _start_of_week(value: date) -> date:
    return value - timedelta(days=value.weekday())


def _start_of_month(value: date) -> date:
    return value.replace(day=1)


def _bucket_stride(preset: str) -> timedelta:
    if preset == "weekly":
        return timedelta(days=7)
    if preset == "monthly":
        return timedelta(days=30)  # label epoch is month-aligned below
    return timedelta(days=1)


def resolve_range(
    preset: str,
    *,
    start: date | None = None,
    end: date | None = None,
    now: datetime | None = None,
) -> tuple[datetime, datetime, str]:
    """Return ``(start_dt, end_dt, grain)`` for a preset or custom range."""

    preset = (preset or "daily").lower()
    if preset not in PRESETS:
        raise ReportError(f"unknown preset: {preset}")
    today = (now or datetime.now(timezone.utc)).date()

    if preset == "custom":
        if start is None or end is None:
            raise ReportError("custom range requires start and end")
        if start > end:
            raise ReportError("start must not be after end")
        return _start_of_day(start), _end_of_day(end), "daily"

    if preset == "daily":
        return _start_of_day(today - timedelta(days=13)), _end_of_day(today), "daily"
    if preset == "weekly":
        first = _start_of_week(today) - timedelta(weeks=7)
        return _start_of_day(first), _end_of_day(today), "weekly"
    # monthly
    first = _start_of_month(today)
    for _ in range(5):
        first = _start_of_month(first - timedelta(days=1))
    return _start_of_day(first), _end_of_day(today), "monthly"


def _bucket_label(value: datetime, grain: str, anchor: datetime) -> str:
    if grain == "monthly":
        return value.strftime("%Y-%m")
    if grain == "weekly":
        week = (value.date() - anchor.date()).days // 7
        return f"W{week + 1}"
    return value.strftime("%Y-%m-%d")


def _iter_buckets(
    start: datetime, end: datetime, grain: str
) -> list[tuple[str, datetime, datetime]]:
    buckets: list[tuple[str, datetime, datetime]] = []
    cursor = start
    index = 0
    while cursor <= end:
        if grain == "monthly":
            next_cursor = (cursor.replace(day=28) + timedelta(days=5)).replace(
                day=1, hour=0, minute=0, second=0, microsecond=0
            )
        elif grain == "weekly":
            next_cursor = cursor + timedelta(days=7)
        else:
            next_cursor = cursor + timedelta(days=1)
        label = _bucket_label(cursor, grain, start)
        buckets.append((label, cursor, min(next_cursor, end + timedelta(microseconds=1))))
        cursor = next_cursor
        index += 1
        if index > 400:  # safety valve
            break
    return buckets


def _owner_user_ids(db: Session, owner_id: int | None) -> list[int]:
    stmt = select(VpnUser.id)
    if owner_id is not None:
        stmt = stmt.where(VpnUser.owner_id == owner_id)
    return list(db.scalars(stmt))


def build_report(
    db: Session,
    *,
    owner_id: int | None = None,
    preset: str = "daily",
    start: date | None = None,
    end: date | None = None,
    now: datetime | None = None,
    owner_label: str | None = None,
) -> Report:
    start_dt, end_dt, grain = resolve_range(preset, start=start, end=end, now=now)
    user_ids = _owner_user_ids(db, owner_id)

    report = Report(
        preset=preset,
        start=start_dt,
        end=end_dt,
        owner_id=owner_id,
        owner_label=owner_label,
    )
    if not user_ids:
        report.buckets = [
            Bucket(label=label, bytes_in=0, bytes_out=0)
            for label, _s, _e in _iter_buckets(start_dt, end_dt, grain)
        ]
        return report

    rows = list(
        db.scalars(
            select(TrafficLog).where(
                TrafficLog.vpn_user_id.in_(user_ids),
                TrafficLog.recorded_at >= start_dt,
                TrafficLog.recorded_at <= end_dt,
            )
        )
    )

    bucket_ranges = _iter_buckets(start_dt, end_dt, grain)
    buckets = [Bucket(label=label, bytes_in=0, bytes_out=0) for label, _s, _e in bucket_ranges]
    totals = Totals()

    def _aware(value: datetime) -> datetime:
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)

    for row in rows:
        recorded = _aware(row.recorded_at)
        for index, (_label, b_start, b_end) in enumerate(bucket_ranges):
            if b_start <= recorded < b_end:
                buckets[index].bytes_in += row.bytes_in
                buckets[index].bytes_out += row.bytes_out
                break
        totals.bytes_in += row.bytes_in
        totals.bytes_out += row.bytes_out

    totals.users = len(user_ids)
    report.buckets = buckets
    report.totals = totals

    node_rows = db.execute(
        select(Node.name, func.coalesce(func.sum(TrafficLog.bytes_in + TrafficLog.bytes_out), 0))
        .select_from(TrafficLog)
        .join(Node, Node.id == TrafficLog.node_id, isouter=True)
        .where(
            TrafficLog.vpn_user_id.in_(user_ids),
            TrafficLog.recorded_at >= start_dt,
            TrafficLog.recorded_at <= end_dt,
        )
        .group_by(Node.name)
    ).all()
    report.by_node = [((name or "—"), int(total or 0)) for name, total in node_rows]
    return report
