from __future__ import annotations

import httpx
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from tools.family_auth import FamilyKeyRegistry
from tools.remote_gateway import create_app


class FakeSearchService:
    async def research_round(self, **kwargs):
        return {"query": kwargs["query"], "claims": [], "sources": []}

    def provider_status(self):
        return {"status": "ready", "providers": ["doubao", "qianwen"]}


@pytest.mark.asyncio
async def test_authenticated_streamable_http_lists_remote_tools(tmp_path):
    registry = FamilyKeyRegistry(tmp_path / "family-keys.json")
    key, _principal = registry.create("mcp-client")
    app = create_app(registry=registry, service=FakeSearchService())
    transport = httpx.ASGITransport(app=app)
    async with app.inner_app.router.lifespan_context(app.inner_app):
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://127.0.0.1",
            headers={"Authorization": f"Bearer {key}"},
        ) as client:
            async with streamable_http_client(
                "http://127.0.0.1/mcp", http_client=client
            ) as (read_stream, write_stream, _session_id):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    result = await session.list_tools()
                    status = await session.call_tool("provider_status", {})

    assert not status.isError
    assert {tool.name for tool in result.tools} == {
        "search_once",
        "research",
        "continue_research",
        "get_research_result",
        "provider_status",
    }
