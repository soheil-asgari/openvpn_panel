"""Tests for password hashing, tokens and API keys."""

from __future__ import annotations

from app.core.security import (
    constant_time_compare,
    generate_api_key,
    generate_password,
    hash_password,
    needs_rehash,
    verify_password,
)


def test_password_hash_roundtrip() -> None:
    hashed = hash_password("correct horse battery staple")
    assert hashed.startswith("$argon2id$")
    assert verify_password("correct horse battery staple", hashed)
    assert not verify_password("wrong password", hashed)


def test_hashes_are_salted() -> None:
    assert hash_password("same") != hash_password("same")


def test_empty_password_rejected() -> None:
    import pytest

    with pytest.raises(ValueError):
        hash_password("")


def test_needs_rehash_is_false_for_fresh_hash() -> None:
    assert needs_rehash(hash_password("secret")) is False


def test_api_key_format() -> None:
    prefix, raw = generate_api_key()
    assert len(prefix) == 8
    assert raw.startswith("ovpn_")
    assert raw.split("_")[1] == prefix


def test_generate_password_defaults() -> None:
    password = generate_password()
    assert len(password) == 20
    assert generate_password() != password


def test_constant_time_compare() -> None:
    assert constant_time_compare("abc", "abc")
    assert not constant_time_compare("abc", "abd")
