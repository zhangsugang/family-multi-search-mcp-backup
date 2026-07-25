from __future__ import annotations

import json

import pytest

from tools.family_auth import AuthenticationError, FamilyKeyRegistry


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
