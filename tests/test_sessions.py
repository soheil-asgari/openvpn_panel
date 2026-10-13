"""Tests for server-side sessions and cookie signing."""

from __future__ import annotations

import time

from app.config import get_settings
from app.core.sessions import build_signer
from app.services import sessions as sessions_service


def test_create_and_get_session(db, admin_user) -> None:  # noqa: ANN001
    settings = get_settings()
    session = sessions_service.create_session(
        db, user_id=admin_user.id, max_age=settings.session_max_age
    )
    fetched = sessions_service.get_session(db, session.session_id)
    assert fetched is not None
    assert fetched.user_id == admin_user.id
    assert fetched.csrf_token


def test_expired_session_is_removed(db, admin_user) -> None:  # noqa: ANN001
    session = sessions_service.create_session(db, user_id=admin_user.id, max_age=-1)
    assert sessions_service.get_session(db, session.session_id) is None


def test_delete_user_sessions(db, admin_user) -> None:  # noqa: ANN001
    first = sessions_service.create_session(db, user_id=admin_user.id, max_age=3600)
    second = sessions_service.create_session(db, user_id=admin_user.id, max_age=3600)
    sessions_service.delete_user_sessions(db, admin_user.id, except_id=first.session_id)
    assert sessions_service.get_session(db, first.session_id) is not None
    assert sessions_service.get_session(db, second.session_id) is None


def test_purge_expired(db, admin_user) -> None:  # noqa: ANN001
    sessions_service.create_session(db, user_id=admin_user.id, max_age=-1)
    assert sessions_service.purge_expired(db) >= 1


def test_signer_roundtrip() -> None:
    settings = get_settings()
    signer = build_signer(settings)
    token = signer.sign("session-id")
    assert signer.verify(token) == "session-id"


def test_signer_rejects_tampering() -> None:
    settings = get_settings()
    signer = build_signer(settings)
    token = signer.sign("session-id")
    assert signer.verify(token + "x") is None


def test_touch_extends_expiry(db, admin_user) -> None:  # noqa: ANN001
    session = sessions_service.create_session(db, user_id=admin_user.id, max_age=1)
    original = session.expires_at
    time.sleep(0.01)
    sessions_service.touch(db, session, max_age=3600)
    assert session.expires_at > original
