#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import stat
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.family_auth import DEFAULT_SCOPES, FamilyKeyRegistry


def _write_handoff(path: Path, values: list[dict]) -> None:
    parent = path.parent
    if parent.exists():
        if parent.is_symlink() or not parent.is_dir():
            raise ValueError("handoff parent must be a real directory")
        metadata = parent.stat()
        if metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) & 0o077:
            raise PermissionError("handoff parent must be user-owned with mode 0700")
    else:
        parent.mkdir(parents=True, mode=0o700)
    if path.is_symlink():
        raise ValueError("handoff path must not be a symlink")
    descriptor, temporary = tempfile.mkstemp(prefix=".handoff-", dir=parent, text=True)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump({"version": 1, "keys": values}, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage family multi-search keys")
    parser.add_argument("--registry", type=Path, required=True)
    subparsers = parser.add_subparsers(dest="command", required=True)

    create = subparsers.add_parser("create")
    create.add_argument("--label", required=True)
    create.add_argument("--handoff", type=Path)
    create.add_argument("--show", action="store_true")

    bootstrap = subparsers.add_parser("bootstrap")
    bootstrap.add_argument("--count", type=int, default=10)
    bootstrap.add_argument("--label-prefix", default="family")
    bootstrap.add_argument("--handoff", type=Path, required=True)

    subparsers.add_parser("list")
    revoke = subparsers.add_parser("revoke")
    revoke.add_argument("key_id")
    rotate = subparsers.add_parser("rotate")
    rotate.add_argument("key_id")
    rotate.add_argument("--handoff", type=Path, required=True)
    verify = subparsers.add_parser("verify")
    verify.add_argument("key")

    args = parser.parse_args()
    registry = FamilyKeyRegistry(args.registry.expanduser())

    if args.command == "create":
        raw_key, principal = registry.create(args.label, scopes=DEFAULT_SCOPES)
        if args.handoff:
            _write_handoff(
                args.handoff.expanduser(),
                [{"key_id": principal.key_id, "label": principal.label, "key": raw_key}],
            )
        print(json.dumps({"key_id": principal.key_id, "label": principal.label}))
        if args.show:
            print(raw_key)
        return
    if args.command == "bootstrap":
        if isinstance(args.count, bool) or not 1 <= args.count <= 50:
            parser.error("count must be between 1 and 50")
        values = []
        for index in range(1, args.count + 1):
            raw_key, principal = registry.create(
                f"{args.label_prefix}-{index:02d}", scopes=DEFAULT_SCOPES
            )
            values.append(
                {"key_id": principal.key_id, "label": principal.label, "key": raw_key}
            )
        _write_handoff(args.handoff.expanduser(), values)
        print(json.dumps({"created": len(values), "handoff": str(args.handoff)}))
        return
    if args.command == "list":
        print(json.dumps(registry.list_records(), ensure_ascii=False, indent=2))
        return
    if args.command == "revoke":
        if not registry.revoke(args.key_id):
            parser.error("unknown or already revoked key")
        print(json.dumps({"revoked": args.key_id}))
        return
    if args.command == "rotate":
        raw_key, principal = registry.rotate(args.key_id)
        _write_handoff(
            args.handoff.expanduser(),
            [{"key_id": principal.key_id, "label": principal.label, "key": raw_key}],
        )
        print(json.dumps({"rotated": args.key_id, "new_key_id": principal.key_id}))
        return
    if args.command == "verify":
        principal = registry.authenticate(args.key)
        print(json.dumps({"key_id": principal.key_id, "label": principal.label}))


if __name__ == "__main__":
    main()
