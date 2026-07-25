from __future__ import annotations

import asyncio

import pytest

from tools import multi_search_mcp as search_mcp
from tools.research_brief import build_research_brief
from tools.search_queries import build_provider_query_lanes


def test_grok_quota_text_is_detected_and_prompt_echo_is_not_complete():
    quota = "距离限制重置还剩 4小时 7分钟 等待或升级至 SuperGrok 以获取更高上限"

    assert search_mcp._is_grok_quota_text(quota) is True
    assert search_mcp._is_grok_quota_text("正常的 X 搜索结果") is False
    result = search_mcp._adapt_provider_result(
        "grok",
        {"answer": "用户原始提示词", "references": [], "prompt_echo": True},
    )
    assert result["status"] == "failed"


def test_empty_provider_result_is_failed_but_tavily_references_are_complete():
    empty = search_mcp._adapt_provider_result(
        "grok", {"answer": "", "references": []}
    )
    tavily = search_mcp._adapt_provider_result(
        "tavily",
        {
            "answer": "",
            "results": [
                {"title": "Official", "url": "https://example.com", "content": "x"}
            ],
        },
    )

    assert empty["status"] == "failed"
    assert tavily["status"] == "complete"


@pytest.mark.asyncio
async def test_grok_runs_only_specialized_lane(monkeypatch):
    calls = []

    async def fake_collect(
        queries,
        timeout,
        original_query,
        lane_name="single",
        skipped_sources=None,
    ):
        calls.append((lane_name, set(skipped_sources or ())))
        results = {}
        for source_id in search_mcp.provider_ids():
            if source_id in set(skipped_sources or ()):
                results[source_id] = {
                    "status": "skipped",
                    "partial": False,
                    "answer": "",
                    "references": [],
                }
            else:
                results[source_id] = {
                    "status": "complete",
                    "partial": False,
                    "answer": f"{lane_name}-{source_id}",
                    "references": [],
                }
        return {"elapsed_ms": 1, "results": results}

    monkeypatch.setattr(search_mcp, "_collect_provider_results", fake_collect)
    lanes = build_provider_query_lanes(build_research_brief("1970文创园", depth="deep"))

    result = await search_mcp._collect_provider_lanes(
        lanes,
        timeout=1,
        original_query="1970文创园",
    )

    assert calls == [("general", {"grok"}), ("specialized", set())]
    assert result["results"]["grok"]["lane_status"] == {
        "general": "skipped",
        "specialized": "complete",
    }
    assert result["results"]["grok"]["status"] == "complete"
    assert "general-grok" not in result["results"]["grok"]["answer"]
    assert "specialized-grok" in result["results"]["grok"]["answer"]


def test_tavily_query_is_compacted_for_api_limits():
    query = "研究问题：1970文创园。" + "保留来源、发布日期和数据口径。" * 80

    compact = search_mcp._compact_tavily_query(query)

    assert "1970文创园" in compact
    assert len(compact) <= 400


@pytest.mark.asyncio
async def test_tavily_rotates_across_all_keys_on_quota_failures(monkeypatch):
    attempts = []
    statuses = iter((429, 432, 200))

    class Response:
        def __init__(self, status_code):
            self.status_code = status_code

        def raise_for_status(self):
            if self.status_code >= 400:
                raise AssertionError(f"unexpected raise_for_status: {self.status_code}")

        def json(self):
            return {
                "answer": "",
                "results": [
                    {"title": "ok", "url": "https://example.com", "content": "ok"}
                ],
            }

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, _url, json):
            attempts.append((json["api_key"], json["query"]))
            return Response(next(statuses))

    monkeypatch.setattr(
        search_mcp,
        "get_runtime_config",
        lambda: {"tavily_api_keys": ["one", "two", "three", "four", "five"]},
    )
    monkeypatch.setattr(search_mcp.httpx, "AsyncClient", lambda **_kwargs: Client())
    monkeypatch.setattr(search_mcp, "_TAVILY_KEYS_FINGERPRINT", None)
    monkeypatch.setattr(search_mcp, "_TAVILY_KEY_CYCLE", None)

    result = await search_mcp._tavily_query_async("current query")

    assert len(attempts) == 3
    assert [item[0] for item in attempts] == ["one", "two", "three"]
    assert result["results"][0]["title"] == "ok"


def test_mcporter_resolver_uses_homebrew_absolute_path(monkeypatch, tmp_path):
    executable = tmp_path / "mcporter"
    executable.write_text("#!/bin/sh\n")
    executable.chmod(0o755)
    monkeypatch.setattr(search_mcp.shutil, "which", lambda _name: None)
    monkeypatch.setattr(
        search_mcp,
        "_MCPORTER_CANDIDATES",
        (str(executable),),
    )

    assert search_mcp._resolve_mcporter() == str(executable)
