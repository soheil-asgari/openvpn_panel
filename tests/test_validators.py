"""Tests for strict input validation."""

from __future__ import annotations

import pytest

from app.core.validators import (
    ValidationError,
    validate_cipher,
    validate_common_name,
    validate_email,
    validate_port,
    validate_protocol,
    validate_username,
)


@pytest.mark.parametrize("value", ["alice", "bob_1", "a.b-c", "ABC123", "x" * 32])
def test_valid_usernames(value: str) -> None:
    assert validate_username(value) == value


@pytest.mark.parametrize(
    "value",
    ["ab", "x" * 33, "has space", "semi;colon", "quote'", "$(whoami)", "slash/", "back\\slash", ""],
)
def test_invalid_usernames(value: str) -> None:
    with pytest.raises(ValidationError):
        validate_username(value)


def test_shell_metacharacters_are_rejected() -> None:
    for payload in ["a;rm -rf /", "a && ls", "a|b", "a`id`", "a$(id)", "a\nb"]:
        with pytest.raises(ValidationError):
            validate_username(payload)


def test_common_name() -> None:
    assert validate_common_name("client_01") == "client_01"
    with pytest.raises(ValidationError):
        validate_common_name("bad name")
    with pytest.raises(ValidationError):
        validate_common_name("")


def test_email() -> None:
    assert validate_email("user@example.com") == "user@example.com"
    for bad in ["no-at-sign", "a@b", "a b@c.com", ""]:
        with pytest.raises(ValidationError):
            validate_email(bad)


def test_port() -> None:
    assert validate_port("1194") == 1194
    assert validate_port(443) == 443
    for bad in [0, 65536, -1, "abc", ""]:
        with pytest.raises(ValidationError):
            validate_port(bad)


def test_protocol_and_cipher() -> None:
    assert validate_protocol("UDP") == "udp"
    assert validate_cipher("AES-256-GCM") == "AES-256-GCM"
    with pytest.raises(ValidationError):
        validate_protocol("icmp")
    with pytest.raises(ValidationError):
        validate_cipher("AES-256-CBC")
