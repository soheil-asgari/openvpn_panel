"""Tests for client configuration rendering."""

from __future__ import annotations

import pytest

from app.core.validators import ValidationError
from app.services import config_builder


def test_build_basic_config() -> None:
    text = config_builder.build_client_config(
        host="vpn.example.com", port=1194, protocol="udp", cipher="AES-256-GCM"
    )
    assert "client" in text
    assert "remote vpn.example.com 1194 udp" in text
    assert "cipher AES-256-GCM" in text


def test_config_embeds_ca() -> None:
    text = config_builder.build_client_config(
        host="vpn.example.com",
        port=443,
        protocol="tcp",
        cipher="AES-256-GCM",
        ca_cert="CERTDATA",
    )
    assert "<ca>" in text and "CERTDATA" in text


def test_config_rejects_bad_protocol() -> None:
    with pytest.raises(ValidationError):
        config_builder.build_client_config(
            host="x", port=1194, protocol="icmp", cipher="AES-256-GCM"
        )


def test_config_rejects_bad_cipher() -> None:
    with pytest.raises(ValidationError):
        config_builder.build_client_config(host="x", port=1194, protocol="udp", cipher="ROT13")


def test_config_rejects_bad_port() -> None:
    with pytest.raises(ValidationError):
        config_builder.build_client_config(
            host="x", port=70000, protocol="udp", cipher="AES-256-GCM"
        )


def test_remote_line_validation() -> None:
    assert config_builder.remote_line("h", "443", "tcp") == "remote h 443 tcp"
