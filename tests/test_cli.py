"""Tests for the ``ovpn-panel`` CLI."""

from __future__ import annotations

import os
import stat

import pytest

from scripts import cli
from scripts.cli import _write_env_file


def test_gen_secrets_writes_env_file(tmp_path) -> None:  # noqa: ANN001
    target = tmp_path / "panel.env"
    code = cli.main(
        [
            "gen-secrets",
            "--output",
            str(target),
            "--database-url",
            "sqlite:///tmp/x.sqlite3",
            "--force",
        ]
    )
    assert code == 0
    content = target.read_text(encoding="utf-8")
    assert "OVPN_SECRET_KEY=" in content
    assert "OVPN_HOOK_TOKEN=" in content


def test_gen_secrets_refuses_overwrite(tmp_path) -> None:  # noqa: ANN001
    target = tmp_path / "panel.env"
    target.write_text("existing", encoding="utf-8")
    code = cli.main(["gen-secrets", "--output", str(target)])
    assert code == 1
    assert target.read_text(encoding="utf-8") == "existing"


@pytest.mark.skipif(os.name == "nt", reason="POSIX file modes are not emulated on Windows")
def test_write_env_file_permissions(tmp_path) -> None:  # noqa: ANN001
    target = tmp_path / "secrets.env"
    _write_env_file({"OVPN_SECRET_KEY": "abc"}, target)
    mode = stat.S_IMODE(target.stat().st_mode)
    assert mode == 0o600


def test_create_admin_and_reset_password() -> None:  # noqa: ANN001
    from app.db import get_session_factory
    from app.services import users as users_service

    assert cli.main(["create-admin", "cliadmin", "--password", "cli-password"]) == 0
    db = get_session_factory()()
    try:
        user = users_service.get_by_username(db, "cliadmin")
        assert user is not None
    finally:
        db.close()

    assert cli.main(["reset-password", "cliadmin", "--password", "new-password"]) == 0
    db = get_session_factory()()
    try:
        user = users_service.get_by_username(db, "cliadmin")
        assert users_service.authenticate(db, "cliadmin", "new-password") is not None
    finally:
        db.close()


def test_create_admin_duplicate_fails() -> None:  # noqa: ANN001
    assert cli.main(["create-admin", "dupadmin", "--password", "cli-password"]) == 0
    assert cli.main(["create-admin", "dupadmin", "--password", "cli-password"]) == 1


def test_reset_password_unknown_user() -> None:  # noqa: ANN001
    assert cli.main(["reset-password", "ghost", "--password", "cli-password"]) == 1


def test_init_db_runs() -> None:  # noqa: ANN001
    assert cli.main(["init-db"]) == 0
