"""Server-side session service backed by the database."""

from __future__ import annotations

import secrets
import time

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.session import Session as SessionModel


def create_session(
    db: Session,
    *,
    user_id: int,
    max_age: int,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> SessionModel:
    now = time.time()
    session = SessionModel(
        session_id=secrets.token_urlsafe(32),
        user_id=user_id,
        csrf_token=secrets.token_urlsafe(32),
        ip_address=ip_address,
        user_agent=(user_agent or "")[:255] or None,
        created_at=now,
        last_seen=now,
        expires_at=now + max_age,
    )
    db.add(session)
    db.flush()
    return session


def get_session(db: Session, session_id: str) -> SessionModel | None:
    session = db.scalar(select(SessionModel).where(SessionModel.session_id == session_id))
    if session is None:
        return None
    if session.expires_at < time.time():
        db.delete(session)
        db.flush()
        return None
    return session


def touch(db: Session, session: SessionModel, max_age: int) -> None:
    now = time.time()
    session.last_seen = now
    session.expires_at = now + max_age
    db.flush()


def delete_session(db: Session, session_id: str) -> None:
    db.execute(delete(SessionModel).where(SessionModel.session_id == session_id))
    db.flush()


def delete_user_sessions(db: Session, user_id: int, except_id: str | None = None) -> None:
    stmt = delete(SessionModel).where(SessionModel.user_id == user_id)
    if except_id:
        stmt = stmt.where(SessionModel.session_id != except_id)
    db.execute(stmt)
    db.flush()


def purge_expired(db: Session) -> int:
    result = db.execute(delete(SessionModel).where(SessionModel.expires_at < time.time()))
    db.flush()
    return int(result.rowcount or 0)
