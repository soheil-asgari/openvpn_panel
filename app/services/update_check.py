"""Opt-in update check — the ONLY place the panel contacts the network for
version information.

Design rules (release hardening):

* **Off by default.** Nothing is fetched unless the operator explicitly enables
  it in Settings.
* **No telemetry / phone-home.** The request carries no identifiers, no usage
  data, no hostname — it simply reads a public release list.
* **Failures are silent.** A network error returns ``check_failed`` and never
  breaks a request or logs a warning storm.

The repository URL is read from settings (``update.repo``), which defaults to the
project's public repository (``soheil-asgari/openvpn_panel``) and can be changed
by the operator. The feature stays inert until explicitly enabled.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app import __version__

logger = logging.getLogger(__name__)

TIMEOUT = 6.0


@dataclass
class UpdateStatus:
    enabled: bool
    current: str
    latest: str | None = None
    update_available: bool = False
    url: str | None = None
    error: str | None = None


def _parse_version(value: str) -> tuple[int, ...]:
    core = value.strip().lstrip("v").split("-")[0].split("+")[0]
    parts: list[int] = []
    for chunk in core.split("."):
        try:
            parts.append(int(chunk))
        except ValueError:
            parts.append(0)
    return tuple(parts)


def is_newer(latest: str, current: str) -> bool:
    return _parse_version(latest) > _parse_version(current)


def check_for_update(*, enabled: bool, repo: str) -> UpdateStatus:
    """Check the project's own releases for a newer version.

    ``enabled`` and ``repo`` are supplied by the caller from settings so this
    module performs no hidden lookups. When disabled (the default) no request is
    made at all.
    """

    status = UpdateStatus(enabled=enabled, current=__version__)
    if not enabled:
        return status
    if not repo or "/" not in repo:
        status.error = "not_configured"
        return status

    try:
        import httpx

        response = httpx.get(
            f"https://api.github.com/repos/{repo}/releases/latest",
            timeout=TIMEOUT,
            headers={"Accept": "application/vnd.github+json"},
        )
        response.raise_for_status()
        data = response.json()
    except Exception as exc:  # noqa: BLE001 - never let an update check break a request
        logger.debug("update check failed: %s", exc)
        status.error = "check_failed"
        return status

    latest = str(data.get("tag_name") or "").strip()
    status.latest = latest or None
    status.url = data.get("html_url")
    if latest:
        status.update_available = is_newer(latest, __version__)
    return status
