from __future__ import annotations

import json
import re
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MONOREPO_ROOT = PROJECT_ROOT.parents[1]
if (MONOREPO_ROOT / "packs" / "family-multi-search-marketplace").is_dir():
    MARKETPLACE_ROOT = MONOREPO_ROOT / "packs" / "family-multi-search-marketplace"
else:
    MARKETPLACE_ROOT = PROJECT_ROOT.parent
PLUGIN_ROOT = MARKETPLACE_ROOT / "plugins" / "family-multi-search"


def test_zcode_marketplace_bundles_mcp_and_skill_without_real_key():
    marketplace = json.loads(
        (MARKETPLACE_ROOT / ".claude-plugin" / "marketplace.json").read_text()
    )
    manifest = json.loads(
        (PLUGIN_ROOT / ".zcode-plugin" / "plugin.json").read_text()
    )
    fallback = json.loads(
        (PLUGIN_ROOT / ".claude-plugin" / "plugin.json").read_text()
    )
    mcp = json.loads((PLUGIN_ROOT / ".mcp.json").read_text())

    assert marketplace["name"] == "family-multi-search"
    assert marketplace["plugins"][0]["name"] == "family-multi-search"
    assert marketplace["plugins"][0]["version"] == "0.3.3"
    assert manifest["name"] == "family-multi-search"
    assert manifest["version"] == "0.3.3"
    assert manifest == fallback | {"description_i18n": manifest["description_i18n"]}
    assert manifest["skills"] == "./skills/"
    assert manifest["mcpServers"] == "./.mcp.json"
    assert "userConfig" not in manifest
    server = mcp["mcpServers"]["family-multi-search"]
    assert server["type"] == "stdio"
    assert server["command"] == "python3"
    assert server["args"] == [
        "${ZCODE_PLUGIN_ROOT}/skills/multi-search-remote/scripts/zcode_mcp_proxy.py"
    ]
    assert server["enabled"] is True
    assert (PLUGIN_ROOT / "skills" / "multi-search-remote" / "SKILL.md").is_file()
    assert (
        PLUGIN_ROOT
        / "skills"
        / "multi-search-remote"
        / "scripts"
        / "zcode_mcp_proxy.py"
    ).is_file()

    scan_paths = [
        MARKETPLACE_ROOT / ".claude-plugin" / "marketplace.json",
        MARKETPLACE_ROOT / "install-zcode.sh",
        *(
            path
            for path in PLUGIN_ROOT.rglob("*")
            if path.is_file()
        ),
    ]
    payload = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore") for path in scan_paths
    )
    assert re.search(r"fms_[a-z0-9]{8}_[A-Za-z0-9_-]{32,}", payload) is None
    assert "/Users/" not in payload
    assert ".worktrees" not in payload
