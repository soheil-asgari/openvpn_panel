"""Local OpenVPN ``server.conf`` editor with safe apply and rollback.

Flow for :func:`apply`:

1. Validate the new text statically (and with ``openvpn --test`` when available).
2. Read the current config so it can be restored.
3. Write the new config through the privileged helper (atomic, allow-listed).
4. Restart the instance through the helper.
5. If the restart fails, write the previous config back and restart again.

The helper is the only component allowed to touch files under
``/etc/openvpn/``; all writes here go through it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.core.config_validate import ValidationResult, validate_server_conf
from app.core.validators import validate_instance_name
from app.services import helper_client

DEFAULT_CONFIG_PATH = "/etc/openvpn/instances/server.conf"


@dataclass(slots=True)
class ApplyResult:
    ok: bool
    message: str
    validation: ValidationResult
    rolled_back: bool = False


def read_config(path: str = DEFAULT_CONFIG_PATH) -> str:
    """Read the current config via the helper; '' when unavailable."""

    import contextlib

    with contextlib.suppress(Exception):
        validate_instance_name(_instance_from_path(path))
    try:
        result = helper_client.read_config_file(path)
    except helper_client.HelperError:
        return ""
    if result.ok and result.data:
        return str(result.data.get("content", ""))
    return ""


def _instance_from_path(path: str) -> str:
    return Path(path).stem


def validate(content: str) -> ValidationResult:
    return validate_server_conf(content)


def apply(
    content: str,
    *,
    path: str = DEFAULT_CONFIG_PATH,
    instance: str | None = None,
    restart: bool = True,
) -> ApplyResult:
    """Validate, write and (optionally) restart, rolling back on failure."""

    validation = validate_server_conf(content)
    if not validation.ok:
        return ApplyResult(ok=False, message=validation.summary, validation=validation)

    instance = instance or _instance_from_path(path)
    try:
        validate_instance_name(instance)
    except Exception as exc:  # noqa: BLE001
        return ApplyResult(ok=False, message=str(exc), validation=validation)

    previous = read_config(path)

    try:
        write_result = helper_client.write_config_file(path, content)
    except helper_client.HelperError as exc:
        return ApplyResult(ok=False, message=str(exc), validation=validation)
    if not write_result.ok:
        return ApplyResult(
            ok=False, message=write_result.error or "write failed", validation=validation
        )

    if not restart:
        return ApplyResult(ok=True, message="saved", validation=validation)

    try:
        restart_result = helper_client.instance_action(instance, "restart")
    except helper_client.HelperError as exc:
        return _rollback(path, previous, validation, str(exc))

    if not restart_result.ok:
        return _rollback(path, previous, validation, restart_result.error or "restart failed")
    return ApplyResult(ok=True, message="applied", validation=validation)


def _rollback(path: str, previous: str, validation: ValidationResult, reason: str) -> ApplyResult:
    rolled_back = False
    if previous:
        try:
            restore = helper_client.write_config_file(path, previous)
            if restore.ok:
                helper_client.instance_action(_instance_from_path(path), "restart")
                rolled_back = True
        except helper_client.HelperError:
            rolled_back = False
    return ApplyResult(
        ok=False,
        message=f"apply failed ({reason}); rolled back={rolled_back}",
        validation=validation,
        rolled_back=rolled_back,
    )
