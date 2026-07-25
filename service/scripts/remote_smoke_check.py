#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import os

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


REQUIRED_TOOLS = {
    "search_once",
    "research",
    "continue_research",
    "get_research_result",
    "provider_status",
}


async def check(url: str, key: str) -> None:
    base = url.removesuffix("/mcp")
    async with httpx.AsyncClient(timeout=20) as health_client:
        response = await health_client.get(f"{base}/healthz")
        response.raise_for_status()
        if response.json() != {"status": "ok"}:
            raise RuntimeError("unexpected health response")
    async with httpx.AsyncClient(
        headers={"Authorization": f"Bearer {key}"}, timeout=30
    ) as client:
        async with streamable_http_client(url, http_client=client) as (
            read_stream,
            write_stream,
            _session_id,
        ):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                result = await session.list_tools()
    names = {tool.name for tool in result.tools}
    missing = REQUIRED_TOOLS - names
    if missing:
        raise RuntimeError(f"missing remote tools: {', '.join(sorted(missing))}")
    print(f"remote MCP ok: {len(names)} tools")


def main() -> None:
    parser = argparse.ArgumentParser(description="Smoke-test remote multi-search MCP")
    parser.add_argument("--url", default="https://mcp-search.bri-king.com/mcp")
    args = parser.parse_args()
    key = os.environ.get("MULTI_SEARCH_KEY", "").strip()
    if not key:
        parser.error("MULTI_SEARCH_KEY is required")
    asyncio.run(check(args.url, key))


if __name__ == "__main__":
    main()
