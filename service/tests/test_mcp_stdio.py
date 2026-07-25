#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RUNTIME = PROJECT_ROOT / "tools" / "multi_search_mcp.py"
EXPECTED_TOOLS = {
    "doubao_search",
    "tavily_search",
    "yuanbao_search",
    "exa_search",
    "wenxin_search",
    "gemini_search",
    "grok_search",
    "qianwen_search",
    "research_round",
    "search_all",
    "search_status",
}


async def list_runtime_tools(runtime: Path) -> set[str]:
    if not runtime.is_file():
        raise FileNotFoundError(f"runtime not found: {runtime}")
    environment = os.environ.copy()
    server = StdioServerParameters(
        command=sys.executable,
        args=[str(runtime)],
        env=environment,
        cwd=str(runtime.parent.parent),
    )
    async with stdio_client(server) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            result = await session.list_tools()
            return {tool.name for tool in result.tools}


@pytest.mark.asyncio
async def test_runtime_lists_all_public_tools():
    tools = await list_runtime_tools(DEFAULT_RUNTIME)

    assert EXPECTED_TOOLS <= tools


def main() -> None:
    parser = argparse.ArgumentParser(description="Smoke-test a multi-search MCP runtime")
    parser.add_argument("--runtime", type=Path, default=DEFAULT_RUNTIME)
    args = parser.parse_args()
    tools = asyncio.run(list_runtime_tools(args.runtime.expanduser().resolve()))
    missing = EXPECTED_TOOLS - tools
    if missing:
        parser.error(f"missing MCP tools: {', '.join(sorted(missing))}")
    print(f"stdio tools ok: {len(EXPECTED_TOOLS)} required, {len(tools)} listed")


if __name__ == "__main__":
    main()
