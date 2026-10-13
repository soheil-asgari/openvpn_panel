"""Application configuration.

All secrets and environment specific values are read from
``/etc/ovpn-panel/panel.env`` (mode 600), from an optional
``/etc/ovpn-panel/config.toml``, or from environment variables prefixed with
``OVPN_``. Priority (highest first): explicit arguments, environment variables,
``panel.env``, ``config.toml``. Nothing here contains a license key, activation
or telemetry logic by design.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

try:  # pragma: no cover - guarded for pydantic-settings versions without TOML
    from pydantic_settings import TomlConfigSettingsSource
except ImportError:  # pragma: no cover
    TomlConfigSettingsSource = None  # type: ignore[assignment,misc]

CONFIG_DIR = Path(os.environ.get("OVPN_PANEL_CONFIG_DIR", "/etc/ovpn-panel"))
ENV_FILE = CONFIG_DIR / "panel.env"
TOML_FILE = CONFIG_DIR / "config.toml"


class Settings(BaseSettings):
    """Runtime settings for the panel."""

    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE),
        env_file_encoding="utf-8",
        env_prefix="OVPN_",
        extra="ignore",
    )

    # Core secrets / database -------------------------------------------------
    secret_key: str = "insecure-development-key-change-me"
    database_url: str = "sqlite:///./data/panel.sqlite3"

    # Paths ------------------------------------------------------------------
    data_dir: Path = Path("/var/lib/ovpn-panel")
    config_dir: Path = CONFIG_DIR
    helper_socket: Path = Path("/run/ovpn-panel/helper.sock")

    # Local unix socket used by OpenVPN's auth-user-pass-verify hook.
    auth_socket: Path = Path("/run/ovpn-panel/auth.sock")

    # Sessions ---------------------------------------------------------------
    session_cookie: str = "ovpn_session"
    session_secure: bool = True
    session_max_age: int = 60 * 60 * 24 * 7  # 7 days

    # CSRF -------------------------------------------------------------------
    csrf_cookie: str = "ovpn_csrf"

    # Login rate limiting ----------------------------------------------------
    login_max_attempts: int = 5
    login_window_seconds: int = 300

    # OpenVPN integration ----------------------------------------------------
    ovpn_management_socket: Path = Path("/run/openvpn/server.sock")
    ovpn_status_interval: int = 30

    # Maximum concurrent connections allowed per VPN user (0 = unlimited).
    max_concurrent_connections: int = 0

    # Shared secret used by the OpenVPN hook scripts to call back into the panel.
    hook_token: str = ""

    # Background enforcement ---------------------------------------------------
    # How often live traffic is polled from the management interface (seconds).
    ovpn_traffic_poll_interval: int = 60
    # Auto-delete VPN users this many days after they expired (0 = disabled).
    auto_delete_expired_days: int = 0

    # Toggles used by tests and one-off scripts.
    disable_scheduler: bool = False

    # Defaults for created VPN users ----------------------------------------
    default_language: str = "en"
    default_theme: str = "system"

    trusted_hosts: list[str] = Field(default_factory=lambda: ["*"])

    # Optional secret URL prefix for the admin UI (e.g. ``admin_path = "panel"``
    # serves the web UI under ``/panel``). Empty disables the prefix.
    admin_path: str = ""

    # Logging ----------------------------------------------------------------
    log_level: str = "INFO"
    log_file: Path | None = None

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings,  # noqa: ANN001
        env_settings,  # noqa: ANN001
        dotenv_settings,  # noqa: ANN001
        file_secret_settings,  # noqa: ANN001
    ) -> tuple:
        sources = [init_settings, env_settings, dotenv_settings]
        if TomlConfigSettingsSource is not None and TOML_FILE.is_file():
            sources.append(TomlConfigSettingsSource(settings_cls, toml_file=TOML_FILE))
        sources.append(file_secret_settings)
        return tuple(sources)

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def admin_prefix(self) -> str:
        """Normalised admin URL prefix (``""`` when disabled)."""

        path = (self.admin_path or "").strip().strip("/")
        return f"/{path}" if path else ""


@lru_cache
def get_settings() -> Settings:
    """Return a cached :class:`Settings` instance."""

    return Settings()


def reset_settings_cache() -> None:
    """Clear the cached settings (used by tests)."""

    get_settings.cache_clear()
