from __future__ import annotations

import json

import pytest

from tools.family_auth import AddressLimitError, AuthenticationError, FamilyKeyRegistry


def test_family_key_registry_hashes_and_revokes(tmp_path):
    registry = FamilyKeyRegistry(tmp_path / "private" / "family-keys.json")
    raw_key, principal = registry.create("family-1")

    stored = (tmp_path / "private" / "family-keys.json").read_text()
    assert raw_key not in stored
    assert principal.key_id in stored
    assert "verifier_digest" in stored
    assert registry.authenticate(raw_key) == principal
    assert registry.revoke(principal.key_id) is True
    with pytest.raises(AuthenticationError):
        registry.authenticate(raw_key)


def test_family_key_listing_never_returns_verifier(tmp_path):
    registry = FamilyKeyRegistry(tmp_path / "keys.json")
    _raw_key, principal = registry.create("family-2")

    records = registry.list_records()

    assert records[0]["key_id"] == principal.key_id
    serialized = json.dumps(records)
    assert "verifier_digest" not in serialized
    assert "salt" not in serialized


def test_rotation_preserves_explicit_empty_scopes(tmp_path):
    registry = FamilyKeyRegistry(tmp_path / "keys.json")
    _raw_key, principal = registry.create("disabled", scopes=[])

    rotated_key, rotated = registry.rotate(principal.key_id)

    assert rotated.scopes == frozenset()
    assert registry.authenticate(rotated_key).scopes == frozenset()


def test_key_binds_ten_address_digests_without_storing_plain_ips(tmp_path):
    path = tmp_path / "keys.json"
    registry = FamilyKeyRegistry(path)
    raw_key, principal = registry.create("shared", max_bound_addresses=10)

    identities = [
        registry.authorize_address(principal, f"203.0.113.{index}")
        for index in range(1, 11)
    ]
    repeated = registry.authorize_address(principal, "203.0.113.1")

    assert len({item.address_id for item in identities}) == 10
    assert repeated.address_id == identities[0].address_id
    assert repeated.owner_id.startswith(f"{principal.key_id}:")
    with pytest.raises(AddressLimitError):
        registry.authorize_address(principal, "203.0.113.11")
    stored = path.read_text()
    assert raw_key not in stored
    assert "203.0.113." not in stored
    assert registry.list_records()[0]["bound_address_count"] == 10


def test_rotation_preserves_address_limit_and_clears_bindings(tmp_path):
    registry = FamilyKeyRegistry(tmp_path / "keys.json")
    _raw_key, principal = registry.create("shared", max_bound_addresses=7)
    registry.authorize_address(principal, "198.51.100.1")

    rotated_key, rotated = registry.rotate(principal.key_id)

    assert rotated.max_bound_addresses == 7
    assert registry.authenticate(rotated_key).max_bound_addresses == 7
    active = [record for record in registry.list_records() if not record["revoked"]]
    assert active[0]["bound_address_count"] == 0
