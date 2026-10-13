"""Tests for the settings loader (environment, panel.env and config.toml)."""

from __future__ import annotations

from pathlib import Path

import pytest


def _fresh_settings(**env: str):  # noqa: ANN001
    from app.config import Settings

    return Settings(**env)


def test_defaults_and_admin_prefix_empty() -> None:
    settings = _fresh_settings(admin_path="")
    assert settings.admin_prefix == ""
    assert settings.session_cookie == "ovpn_session"
    assert settings.login_max_attempts == 5


def test_admin_prefix_normalisation() -> None:
    assert _fresh_settings(admin_path="panel").admin_prefix == "/panel"
    assert _fresh_settings(admin_path="/panel/").admin_prefix == "/panel"
    assert _fresh_settings(admin_path="  secret  ").admin_prefix == "/secret"


def test_toml_config_file(monkeypatch, tmp_path: Path) -> None:  # noqa: ANN001
    toml = tmp_path / "config.toml"
    toml.write_text(
        'log_level = "DEBUG"\nadmin_path = "manage"\ndefault_language = "fa"\n',
        encoding="utf-8",
    )

    import app.config as config_module

    monkeypatch.setattr(config_module, "TOML_FILE", toml)
    settings = config_module.Settings()
    assert settings.log_level == "DEBUG"
    assert settings.admin_prefix == "/manage"
    assert settings.default_language == "fa"


def test_missing_toml_is_ignored(monkeypatch, tmp_path: Path) -> None:  # noqa: ANN001
    import app.config as config_module

    monkeypatch.setattr(config_module, "TOML_FILE", tmp_path / "nope.toml")
    settings = config_module.Settings()
    assert settings.log_level == "INFO"


def test_env_overrides_toml(monkeypatch, tmp_path: Path) -> None:  # noqa: ANN001
    toml = tmp_path / "config.toml"
    toml.write_text('log_level = "DEBUG"\n', encoding="utf-8")

    import app.config as config_module

    monkeypatch.setattr(config_module, "TOML_FILE", toml)
    monkeypatch.setenv("OVPN_LOG_LEVEL", "WARNING")
    settings = config_module.Settings()
    assert settings.log_level == "WARNING"


@pytest.mark.parametrize("value", ["", "/", "///"])
def test_admin_prefix_disabled_values(value: str) -> None:
    assert _fresh_settings(admin_path=value).admin_prefix == ""
