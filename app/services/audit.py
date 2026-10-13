"""Audit logging helpers."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.audit import AuditLog
from app.models.user import User


def record(
    db: Session,
    *,
    action: str,
    actor: User | None = None,
    actor_label: str | None = None,
    target_type: str | None = None,
    target_id: int | None = None,
    ip_address: str | None = None,
    detail: str | None = None,
) -> AuditLog:
    entry = AuditLog(
        actor_id=actor.id if actor else None,
        actor_label=actor_label or (actor.username if actor else None),
        action=action,
        target_type=target_type,
        target_id=target_id,
        ip_address=ip_address,
        detail=detail,
    )
    db.add(entry)
    db.flush()
    return entry


def list_entries(
    db: Session,
    *,
    action: str | None = None,
    actor_id: int | None = None,
    target_type: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[AuditLog], int]:
    """Return ``(entries, total)`` most-recent-first for the audit viewer."""

    stmt = select(AuditLog)
    count_stmt = select(func.count(AuditLog.id))
    if action:
        stmt = stmt.where(AuditLog.action == action)
        count_stmt = count_stmt.where(AuditLog.action == action)
    if actor_id:
        stmt = stmt.where(AuditLog.actor_id == actor_id)
        count_stmt = count_stmt.where(AuditLog.actor_id == actor_id)
    if target_type:
        stmt = stmt.where(AuditLog.target_type == target_type)
        count_stmt = count_stmt.where(AuditLog.target_type == target_type)
    stmt = stmt.order_by(AuditLog.created_at.desc(), AuditLog.id.desc()).limit(limit).offset(offset)
    entries = list(db.scalars(stmt))
    total = int(db.scalar(count_stmt) or 0)
    return entries, total


def distinct_actions(db: Session) -> list[str]:
    rows = db.scalars(select(AuditLog.action).distinct().order_by(AuditLog.action))
    return [row for row in rows if row]
