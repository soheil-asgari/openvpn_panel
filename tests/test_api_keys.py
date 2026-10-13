"""Tests for API key issuance and resolution."""

from __future__ import annotations

from app.services import api_keys as api_keys_service


def test_create_and_resolve_key(db, admin_user) -> None:  # noqa: ANN001
    key, raw = api_keys_service.create_key(db, admin_user, name="ci")
    assert key.prefix in raw
    resolved = api_keys_service.resolve(db, raw)
    assert resolved is not None and resolved.id == admin_user.id


def test_invalid_key_not_resolved(db, admin_user) -> None:  # noqa: ANN001
    api_keys_service.create_key(db, admin_user)
    assert api_keys_service.resolve(db, "ovpn_deadbeef_nope") is None
    assert api_keys_service.resolve(db, "garbage") is None


def test_revoked_key_not_resolved(db, admin_user) -> None:  # noqa: ANN001
    key, raw = api_keys_service.create_key(db, admin_user)
    assert api_keys_service.revoke_key(db, admin_user.id, key.id)
    assert api_keys_service.resolve(db, raw) is None


def test_revoke_wrong_owner_returns_false(db, admin_user, reseller_user) -> None:  # noqa: ANN001
    key, _ = api_keys_service.create_key(db, admin_user)
    assert not api_keys_service.revoke_key(db, reseller_user.id, key.id)


def test_list_keys(db, admin_user) -> None:  # noqa: ANN001
    api_keys_service.create_key(db, admin_user, name="one")
    api_keys_service.create_key(db, admin_user, name="two")
    names = {k.name for k in api_keys_service.list_keys(db, admin_user.id)}
    assert names == {"one", "two"}


def test_api_key_hash_is_not_raw(db, admin_user) -> None:  # noqa: ANN001
    key, raw = api_keys_service.create_key(db, admin_user)
    assert key.key_hash != raw
    assert raw not in key.key_hash
