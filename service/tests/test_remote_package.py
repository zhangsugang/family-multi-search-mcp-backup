from __future__ import annotations

import importlib.util
import re
import tarfile
import zipfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_ROOT / "scripts" / "package_remote_skill.py"


def _module():
    spec = importlib.util.spec_from_file_location("package_remote_skill", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_remote_skill_archives_are_allowlisted_and_secret_free():
    module = _module()
    tar_path, zip_path, checksum_path = module.build()
    expected = {f"multi-search-remote/{item}" for item in module.ALLOWED}

    with tarfile.open(tar_path, "r:gz") as archive:
        tar_names = {member.name for member in archive.getmembers() if member.isfile()}
        payload = b"".join(
            archive.extractfile(member).read()
            for member in archive.getmembers()
            if member.isfile()
        )
    with zipfile.ZipFile(zip_path) as archive:
        zip_names = set(archive.namelist())

    assert tar_names == expected
    assert zip_names == expected
    assert re.search(rb"fms_[a-z0-9]{8}_[A-Za-z0-9_-]{32,}", payload) is None
    assert b"storage-state.json" not in payload
    assert b"conversation_url" not in payload
    assert checksum_path.read_text().count("multi-search-remote-") == 2
