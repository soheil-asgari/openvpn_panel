"""Tests for the central logging configuration."""

from __future__ import annotations

import logging
from pathlib import Path

from app.config import Settings
from app.logging import build_logging_config, configure_logging, reset_logging


def test_build_logging_config_defaults() -> None:
    settings = Settings(log_level="DEBUG")
    config = build_logging_config(settings)
    assert config["root"]["level"] == "DEBUG"
    assert "console" in config["handlers"]
    assert "file" not in config["handlers"]


def test_build_logging_config_with_file(tmp_path: Path) -> None:
    settings = Settings(log_level="INFO", log_file=tmp_path / "panel.log")
    config = build_logging_config(settings)
    assert "file" in config["handlers"]
    assert config["handlers"]["file"]["filename"] == str(tmp_path / "panel.log")


def test_configure_logging_sets_root_level(tmp_path: Path) -> None:
    reset_logging()
    settings = Settings(log_level="WARNING", log_file=tmp_path / "panel.log")
    configure_logging(settings)
    assert logging.getLogger().level == logging.WARNING
    reset_logging()
