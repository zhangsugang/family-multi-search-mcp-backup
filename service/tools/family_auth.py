from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import tempfile
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable


KEY_PATTERN = re.compile(r"^fms_([a-z0-9]{8})_([A-Za-z0-9_-]{32,})$")
DEFAULT_SCOPES = frozenset(
    {"search:provider", "search:research", "research:continue", "providers:read"}
)


class AuthenticationError(ValueError):
    pass


class ScopeError(PermissionError):
    pass


class AddressLimitError(PermissionError):
    pass


@dataclass(frozen=True)
class FamilyPrincipal:
    key_id: str
    label: str
    scopes: frozenset[str]
    max_concurrent_research: int
    max_bound_addresses: int = 10
    address_id: str = ""

    @property
    def owner_id(self) -> str:
        return f"{self.key_id}:{self.address_id}" if self.address_id else self.key_id

    def require(self, scope: str) -> None:
        if scope not in self.scopes:
            raise ScopeError("insufficient scope")


class FamilyKeyRegistry:
    def __init__(self, path: Path):
        self.path = Path(path)

    def _load(self) -> dict:
        if not self.path.exists():
            return {"version": 1, "keys": []}
        value = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or not isinstance(value.get("keys"), list):
            raise ValueError("invalid family key registry")
        return value

    def _write(self, value: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.path.parent, 0o700)
        descriptor, temporary = tempfile.mkstemp(
            prefix=".family-keys-", dir=self.path.parent, text=True
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.path)
            os.chmod(self.path, 0o600)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    @staticmethod
    def _digest(secret: str, salt: bytes) -> str:
        return hashlib.scrypt(
            secret.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=32
        ).hex()

    def create(
        self,
        label: str,
        *,
        scopes: Iterable[str] = DEFAULT_SCOPES,
        max_concurrent_research: int = 1,
        max_bound_addresses: int = 10,
    ) -> tuple[str, FamilyPrincipal]:
        clean_label = label.strip()
        if not clean_label:
            raise ValueError("label is required")
        if isinstance(max_concurrent_research, bool) or max_concurrent_research < 1:
            raise ValueError("max_concurrent_research must be positive")
        if isinstance(max_bound_addresses, bool) or max_bound_addresses < 1:
            raise ValueError("max_bound_addresses must be positive")
        registry = self._load()
        existing = {item.get("key_id") for item in registry["keys"]}
        while True:
            key_id = secrets.token_hex(4)
            if key_id not in existing:
                break
        secret = secrets.token_urlsafe(32)
        salt = secrets.token_bytes(16)
        scope_set = frozenset(str(scope) for scope in scopes)
        record = {
            "key_id": key_id,
            "label": clean_label,
            "salt": salt.hex(),
            "verifier_digest": self._digest(secret, salt),
            "scopes": sorted(scope_set),
            "max_concurrent_research": max_concurrent_research,
            "max_bound_addresses": max_bound_addresses,
            "address_digests": [],
            "revoked": False,
            "created_at": datetime.now(UTC).isoformat(),
            "rotated_at": None,
        }
        registry["keys"].append(record)
        self._write(registry)
        principal = FamilyPrincipal(
            key_id,
            clean_label,
            scope_set,
            max_concurrent_research,
            max_bound_addresses,
        )
        return f"fms_{key_id}_{secret}", principal

    def authenticate(self, raw_key: str) -> FamilyPrincipal:
        match = KEY_PATTERN.fullmatch(raw_key.strip())
        if match is None:
            raise AuthenticationError("invalid access key")
        key_id, secret = match.groups()
        for record in self._load()["keys"]:
            if record.get("key_id") != key_id:
                continue
            if record.get("revoked"):
                raise AuthenticationError("invalid access key")
            try:
                salt = bytes.fromhex(record["salt"])
                expected = record["verifier_digest"]
            except (KeyError, TypeError, ValueError):
                break
            actual = self._digest(secret, salt)
            if not hmac.compare_digest(actual, expected):
                break
            return FamilyPrincipal(
                key_id=key_id,
                label=str(record.get("label", key_id)),
                scopes=frozenset(record.get("scopes", [])),
                max_concurrent_research=int(
                    record.get("max_concurrent_research", 1)
                ),
                max_bound_addresses=int(record.get("max_bound_addresses", 10)),
            )
        raise AuthenticationError("invalid access key")

    def authorize_address(
        self, principal: FamilyPrincipal, normalized_address: str
    ) -> FamilyPrincipal:
        if not normalized_address:
            raise AuthenticationError("client address is required")
        registry = self._load()
        for record in registry["keys"]:
            if record.get("key_id") != principal.key_id or record.get("revoked"):
                continue
            try:
                verifier = bytes.fromhex(record["verifier_digest"])
            except (KeyError, TypeError, ValueError) as exc:
                raise AuthenticationError("invalid access key") from exc
            address_id = hmac.new(
                verifier,
                normalized_address.encode("utf-8"),
                hashlib.sha256,
            ).hexdigest()
            bound = list(record.get("address_digests") or [])
            maximum = int(record.get("max_bound_addresses", 10))
            if address_id not in bound:
                if len(bound) >= maximum:
                    raise AddressLimitError("address limit exceeded")
                bound.append(address_id)
                record["address_digests"] = bound
                self._write(registry)
            return replace(
                principal,
                address_id=address_id,
                max_bound_addresses=maximum,
            )
        raise AuthenticationError("invalid access key")

    def list_records(self) -> list[dict]:
        safe = []
        for record in self._load()["keys"]:
            value = {
                key: record.get(key)
                for key in (
                    "key_id",
                    "label",
                    "scopes",
                    "max_concurrent_research",
                    "revoked",
                    "created_at",
                    "rotated_at",
                )
            }
            value["max_bound_addresses"] = int(record.get("max_bound_addresses", 10))
            value["bound_address_count"] = len(record.get("address_digests") or [])
            safe.append(value)
        return safe

    def revoke(self, key_id: str) -> bool:
        registry = self._load()
        changed = False
        for record in registry["keys"]:
            if record.get("key_id") == key_id and not record.get("revoked"):
                record["revoked"] = True
                changed = True
        if changed:
            self._write(registry)
        return changed

    def rotate(self, key_id: str) -> tuple[str, FamilyPrincipal]:
        records = self.list_records()
        current = next((item for item in records if item["key_id"] == key_id), None)
        if current is None:
            raise KeyError("unknown key id")
        self.revoke(key_id)
        scopes = current.get("scopes")
        return self.create(
            str(current["label"]),
            scopes=DEFAULT_SCOPES if scopes is None else scopes,
            max_concurrent_research=int(current["max_concurrent_research"] or 1),
            max_bound_addresses=int(current["max_bound_addresses"] or 10),
        )


def bearer_token(header: str | None) -> str:
    if not header:
        raise AuthenticationError("authorization required")
    parts = header.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise AuthenticationError("authorization required")
    return parts[1]
