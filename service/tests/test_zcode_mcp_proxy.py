from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PROXY = REPOSITORY_ROOT / "skill" / "scripts" / "zcode_mcp_proxy.py"


def test_zcode_stdio_proxy_initializes_and_lists_five_tools():
    messages = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "1"},
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    ]
    process = subprocess.run(
        [sys.executable, str(PROXY)],
        input="".join(json.dumps(message) + "\n" for message in messages),
        text=True,
        capture_output=True,
        check=True,
        timeout=10,
    )
    responses = [json.loads(line) for line in process.stdout.splitlines()]

    assert responses[0]["result"]["serverInfo"] == {
        "name": "family-multi-search",
        "version": "0.3.2",
    }
    assert [tool["name"] for tool in responses[1]["result"]["tools"]] == [
        "search_once",
        "research",
        "get_research_result",
        "continue_research",
        "provider_status",
    ]
