"""Backup and restore of the database, configuration and PKI.

Produces a single encrypted archive (see :mod:`app.core.crypto`) containing:

* ``database.sql`` for SQLite (a vacuum-safe dump) or ``database.json`` for
  other backends,
* ``config/`` with the panel config directory and OpenVPN instance configs,
* ``pki/`` with the easy-rsa material when readable,
* ``manifest.json`` describing the contents.

Restore is defensive: contents are written only to allow-listed destinations.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import tarfile
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.config import get_settings
from app.core import crypto

MANIFEST_NAME = "manifest.json"
MAX_RESTORE_BYTES = 512 * 1024 * 1024


class BackupError(ValueError):
    """Raised when a backup cannot be created or restored."""


@dataclass(slots=True)
class BackupResult:
    filename: str
    payload: bytes
    entries: list[str]
    size: int
    created_at: datetime


def _dump_database() -> tuple[str, bytes]:
    """Return ``(relative_name, bytes)`` for the database snapshot."""

    settings = get_settings()
    if settings.is_sqlite:
        import sqlite3

        url = settings.database_url
        db_path = url.replace("sqlite:///", "", 1)
        if not os.path.isabs(db_path):
            db_path = os.path.abspath(db_path)
        if not os.path.exists(db_path):
            return "database.sql", b""
        # Copy via the backup API so a WAL database is consistent.
        with tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            source = sqlite3.connect(db_path)
            dest = sqlite3.connect(tmp_path)
            with dest:
                source.backup(dest)
            source.close()
            dest.close()
            return "database.sqlite3", Path(tmp_path).read_bytes()
        finally:
            with contextlib.suppress(OSError):
                os.unlink(tmp_path)

    # Non-SQLite backends: dump the ORM tables to JSON (portable, restore-aware).
    from app.db import get_session_factory
    from app.models import Base

    db = get_session_factory()()
    try:
        payload: dict[str, list[dict]] = {}
        for table in Base.metadata.sorted_tables:
            rows = db.execute(table.select()).mappings().all()
            payload[table.name] = [{k: _jsonable(v) for k, v in row.items()} for row in rows]
        return "database.json", json.dumps(payload, indent=2).encode("utf-8")
    finally:
        db.close()


def _jsonable(value: object) -> object:
    if isinstance(value, (datetime,)):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _collect_dirs(include_pki: bool) -> dict[str, Path]:
    settings = get_settings()
    dirs: dict[str, Path] = {"config": settings.config_dir}
    if include_pki:
        dirs["pki"] = Path("/etc/openvpn/pki")
    return dirs


def create_backup(
    password: str,
    *,
    include_pki: bool = True,
    include_configs: bool = True,
) -> BackupResult:
    """Build an encrypted backup archive in memory."""

    if not password:
        raise BackupError("a backup password is required")

    entries: list[str] = []
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        name, data = _dump_database()
        _add_bytes(tar, name, data)
        entries.append(name)

        if include_configs:
            for prefix, directory in _collect_dirs(include_pki=include_pki).items():
                if not directory.exists():
                    continue
                for path in sorted(directory.rglob("*")):
                    if not path.is_file():
                        continue
                    try:
                        rel = f"{prefix}/{path.relative_to(directory)}"
                        _add_bytes(tar, rel, path.read_bytes())
                        entries.append(rel)
                    except OSError:
                        continue

        manifest = {
            "format": 1,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "include_pki": include_pki,
            "include_configs": include_configs,
            "entries": entries,
        }
        _add_bytes(tar, MANIFEST_NAME, json.dumps(manifest, indent=2).encode("utf-8"))

    raw = buffer.getvalue()
    encrypted = crypto.encrypt(raw, password)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return BackupResult(
        filename=f"ovpn-panel-backup-{stamp}.ovpnbak",
        payload=encrypted,
        entries=entries,
        size=len(encrypted),
        created_at=datetime.now(timezone.utc),
    )


def _add_bytes(tar: tarfile.TarFile, name: str, data: bytes) -> None:
    info = tarfile.TarInfo(name=name)
    info.size = len(data)
    info.mode = 0o600
    info.mtime = int(datetime.now(timezone.utc).timestamp())
    tar.addfile(info, io.BytesIO(data))


def read_manifest(payload: bytes, password: str) -> dict:
    """Decrypt a container and return its manifest without restoring."""

    raw = crypto.decrypt(payload, password)
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tar:
        member = tar.extractfile(MANIFEST_NAME)
        if member is None:
            raise BackupError("archive has no manifest")
        return json.loads(member.read().decode("utf-8"))


def restore_backup(payload: bytes, password: str, *, restore_configs: bool = False) -> dict:
    """Decrypt and validate an archive. Returns the manifest.

    By default only the database snapshot is materialised (to a sibling file)
    because overwriting a live database or PKI while the panel runs is unsafe.
    Config restoration is opt-in.
    """

    if len(payload) > MAX_RESTORE_BYTES:
        raise BackupError("archive too large")
    raw = crypto.decrypt(payload, password)
    settings = get_settings()
    extracted: list[str] = []

    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tar:
        member = tar.extractfile(MANIFEST_NAME)
        manifest = json.loads(member.read().decode("utf-8")) if member else {}
        names = tar.getnames()
        if "database.sqlite3" in names:
            data = tar.extractfile("database.sqlite3")
            if data is not None:
                target = _restore_database_target(settings)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data.read())
                os.chmod(target, 0o600)
                extracted.append(str(target))
        if restore_configs:
            for name in names:
                if name in {MANIFEST_NAME, "database.sqlite3"} or not name.startswith("config/"):
                    continue
                _restore_config_member(tar, name, settings.config_dir)
                extracted.append(name)
    return {"manifest": manifest, "extracted": extracted}


def _restore_database_target(settings) -> Path:  # noqa: ANN001
    url = settings.database_url
    db_path = url.replace("sqlite:///", "", 1)
    if not os.path.isabs(db_path):
        db_path = os.path.abspath(db_path)
    return Path(db_path + ".restored")


def _restore_config_member(tar: tarfile.TarFile, name: str, config_dir: Path) -> None:
    rel = name[len("config/") :]
    if not rel or ".." in Path(rel).parts:
        raise BackupError("unsafe archive member")
    target = (config_dir / rel).resolve()
    root = config_dir.resolve()
    if root != target and root not in target.parents:
        raise BackupError("unsafe archive member")
    member = tar.extractfile(name)
    if member is None:
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(member.read())
    os.chmod(target, 0o600)


# --------------------------------------------------------------------------- #
# Remote upload
# --------------------------------------------------------------------------- #
def upload_telegram(payload: bytes, filename: str, token: str, chat_id: str) -> bool:
    """Send the archive to a Telegram chat. Returns success."""

    import httpx

    if not token or not chat_id:
        return False
    url = f"https://api.telegram.org/bot{token}/sendDocument"
    try:
        with httpx.Client(timeout=30) as client:
            response = client.post(
                url,
                data={"chat_id": chat_id},
                files={"document": (filename, payload, "application/octet-stream")},
            )
        return response.status_code == 200
    except httpx.HTTPError:
        return False


def upload_webhook(payload: bytes, filename: str, webhook_url: str) -> bool:
    """POST the archive to an arbitrary webhook URL. Returns success."""

    import httpx

    if not webhook_url.startswith(("http://", "https://")):
        return False
    try:
        with httpx.Client(timeout=30) as client:
            response = client.post(
                webhook_url,
                files={"file": (filename, payload, "application/octet-stream")},
            )
        return response.status_code < 400
    except httpx.HTTPError:
        return False
