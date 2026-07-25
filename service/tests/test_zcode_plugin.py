from __future__ import annotations

import json
import re
from pathlib import Path


MARKETPLACE_ROOT = Path(__file__).resolve().parents[2]
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
    assert marketplace["plugins"][0]["version"] == "0.3.1"
    assert manifest["name"] == "family-multi-search"
    assert manifest["version"] == "0.3.1"
    assert manifest == fallback | {"description_i18n": manifest["description_i18n"]}
    assert manifest["skills"] == "./skills/"
    assert manifest["mcpServers"] == "./.mcp.json"
    assert manifest["userConfig"]["family_key"]["sensitive"] is True
    server = mcp["mcpServers"]["family-multi-search"]
    assert server["url"] == "https://mcp-search.bri-king.com/mcp"
    assert server["headers"]["Authorization"] == "Bearer ${user_config.family_key}"
    assert (PLUGIN_ROOT / "skills" / "multi-search-remote" / "SKILL.md").is_file()

    payload = "\n".join(
        path.read_text(encoding="utf-8")
        for root in (MARKETPLACE_ROOT / ".claude-plugin", PLUGIN_ROOT)
        for path in root.rglob("*")
        if path.is_file()
    )
    assert re.search(r"fms_[a-z0-9]{8}_[A-Za-z0-9_-]{32,}", payload) is None
    assert "/Users/" not in payload
    assert ".worktrees" not in payload
