#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import io
import os
import tarfile
import zipfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = PROJECT_ROOT.parent
SOURCE = REPOSITORY_ROOT / "skill"
OUTPUT = REPOSITORY_ROOT / "output"
ALLOWED = (
    "SKILL.md",
    "README.md",
    "VERSION",
    "setup.sh",
    "scripts/remote_search.py",
    "references/result-schema.md",
    "references/troubleshooting.md",
    "references/qianwen-read-only-policy.md",
    "templates/client-config.json.example",
    "templates/zcode-mcp.json.example",
)
FIXED_TIME = 1_700_000_000


def _files() -> list[tuple[str, bytes, int]]:
    files = []
    for relative in ALLOWED:
        path = SOURCE / relative
        if not path.is_file():
            raise FileNotFoundError(path)
        mode = 0o755 if relative in {"setup.sh", "scripts/remote_search.py"} else 0o644
        files.append((relative, path.read_bytes(), mode))
    return files


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build() -> tuple[Path, Path, Path]:
    version = (SOURCE / "VERSION").read_text(encoding="utf-8").strip()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    tar_path = OUTPUT / f"multi-search-remote-{version}.tar.gz"
    zip_path = OUTPUT / f"multi-search-remote-{version}.zip"
    files = _files()

    with tar_path.open("wb") as raw:
        import gzip

        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=FIXED_TIME) as compressed:
            with tarfile.open(fileobj=compressed, mode="w") as archive:
                for relative, content, mode in files:
                    info = tarfile.TarInfo(f"multi-search-remote/{relative}")
                    info.size = len(content)
                    info.mode = mode
                    info.mtime = FIXED_TIME
                    info.uid = info.gid = 0
                    info.uname = info.gname = "root"
                    archive.addfile(info, io.BytesIO(content))

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for relative, content, mode in files:
            info = zipfile.ZipInfo(f"multi-search-remote/{relative}")
            info.date_time = (2023, 11, 14, 22, 13, 20)
            info.create_system = 3
            info.external_attr = (mode & 0xFFFF) << 16
            archive.writestr(info, content)

    checksum_path = OUTPUT / "SHA256SUMS"
    checksum_path.write_text(
        f"{_sha256(tar_path)}  {tar_path.name}\n{_sha256(zip_path)}  {zip_path.name}\n",
        encoding="utf-8",
    )
    return tar_path, zip_path, checksum_path


if __name__ == "__main__":
    for path in build():
        print(path)
