"""Central logging configuration.

Configures the root logger (console + optional rotating file) from the panel
settings. Called once from the application factory.
"""

from __future__ import annotations

import logging
from logging.config import dictConfig
from typing import Any

from app.config import Settings, get_settings

_configured = False


def build_logging_config(settings: Settings) -> dict[str, Any]:
    """Return a ``dictConfig`` mapping derived from ``settings``."""

    level = (settings.log_level or "INFO").upper()
    handlers: dict[str, dict[str, Any]] = {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "standard",
            "stream": "ext://sys.stderr",
        }
    }
    handler_names = ["console"]

    if settings.log_file:
        handlers["file"] = {
            "class": "logging.handlers.RotatingFileHandler",
            "formatter": "standard",
            "filename": str(settings.log_file),
            "maxBytes": 5 * 1024 * 1024,
            "backupCount": 3,
            "encoding": "utf-8",
        }
        handler_names.append("file")

    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "standard": {
                "format": "%(asctime)s %(levelname)-8s [%(name)s] %(message)s",
            }
        },
        "handlers": handlers,
        "root": {"level": level, "handlers": handler_names},
        "loggers": {
            "uvicorn.access": {"level": "WARNING", "handlers": handler_names, "propagate": False},
            "uvicorn.error": {"level": level, "handlers": handler_names, "propagate": False},
        },
    }


def configure_logging(settings: Settings | None = None) -> None:
    """Apply the logging configuration (idempotent)."""

    global _configured
    if settings is None:
        settings = get_settings()
    dictConfig(build_logging_config(settings))
    _configured = True


def reset_logging() -> None:
    """Reset the ``configured`` flag (used by tests)."""

    global _configured
    _configured = False


__all__ = ["build_logging_config", "configure_logging", "reset_logging"]

# Silence "imported but unused" for the module-level logger name used elsewhere.
logging.getLogger(__name__)
