"""Shared pytest fixtures.

Each test run uses a throwaway SQLite database. The schema is recreated before
every test so the suite is deterministic and hermetic.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest


@pytest.fixture(scope="session", autouse=True)
def _tmp_env() -> Iterator[None]:
    tmpdir = tempfile.mkdtemp(prefix="ovpn-panel-tests-")
    os.environ["OVPN_PANEL_CONFIG_DIR"] = tmpdir
    os.environ["OVPN_DATABASE_URL"] = f"sqlite:///{Path(tmpdir) / 'test.sqlite3'}"
    os.environ["OVPN_SECRET_KEY"] = "test-secret-key-not-for-production"
    os.environ["OVPN_SESSION_SECURE"] = "false"
    os.environ["OVPN_DISABLE_SCHEDULER"] = "true"
    os.environ["OVPN_HOOK_TOKEN"] = "test-hook-token"
    os.environ["OVPN_TRUSTED_HOSTS"] = '["*"]'

    # Discard any settings/engine cached during test collection (some modules
    # import the app at import time, before these variables are set).
    from app.config import reset_settings_cache
    from app.db import reset_engine

    reset_settings_cache()
    reset_engine()
    yield
    reset_settings_cache()
    reset_engine()


@pytest.fixture(autouse=True)
def _schema(_tmp_env: None) -> Iterator[None]:
    """Recreate all tables before each test."""

    from app.db import get_engine
    from app.models import Base

    engine = get_engine()
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield


@pytest.fixture
def db(_schema: None) -> Iterator["object"]:
    from app.db import get_session_factory

    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def admin_user(db):  # noqa: ANN001
    from app.models.user import Role
    from app.services import users as users_service

    user = users_service.create_user(
        db, username="admin", password="admin-password", role=Role.ADMIN
    )
    db.commit()
    return user


@pytest.fixture
def reseller_user(db):  # noqa: ANN001
    from app.models.user import Role
    from app.services import users as users_service

    user = users_service.create_user(
        db, username="reseller", password="reseller-password", role=Role.RESELLER
    )
    db.commit()
    return user


@pytest.fixture
def client(_schema: None):  # noqa: ANN001
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client
