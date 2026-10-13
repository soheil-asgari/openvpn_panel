"""Node agent configuration."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

CONFIG_DIR = Path(os.environ.get("OVPN_AGENT_CONFIG_DIR", "/etc/ovpn-panel"))
ENV_FILE = CONFIG_DIR / "agent.env"


class AgentSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE),
        env_file_encoding="utf-8",
        env_prefix="OVPN_AGENT_",
        extra="ignore",
    )

    token_hash: str = ""
    # Shared secret for HMAC-SHA256 request signing (preferred over token_hash).
    hmac_secret: str = ""
    node_name: str = "node"
    management_socket: Path = Path("/run/openvpn/server.sock")
    listen_host: str = "127.0.0.1"
    listen_port: int = 8090

    # Seconds of clock skew tolerated on signed requests.
    signature_window: int = 300

    # Directory where per-user local state is materialised for OpenVPN to read.
    state_dir: Path = Path("/etc/openvpn/ccd")
    config_file: Path = Path("/etc/openvpn/instances/server.conf")


@lru_cache
def get_agent_settings() -> AgentSettings:
    return AgentSettings()
