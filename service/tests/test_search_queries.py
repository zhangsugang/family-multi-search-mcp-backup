import asyncio

import pytest

from tools import multi_search_mcp as search_mcp
from tools.research_brief import build_coverage_matrix, build_research_brief
from tools.search_provider_registry import provider_ids
from tools.search_queries import build_provider_queries, build_provider_query_lanes


def test_place_query_extends_into_tourism_and_business_dimensions():
    brief = build_research_brief("1970文创园", depth="deep")
    lanes = build_provider_query_lanes(brief)

    assert set(lanes["doubao"]) == {"general", "specialized"}
    assert "抖音" in lanes["doubao"]["specialized"]
    assert "团购" in lanes["doubao"]["specialized"]
    assert "微信公众号" in lanes["yuanbao"]["specialized"]
    assert "X" in lanes["grok"]["specialized"]
    assert "YouTube" in lanes["gemini"]["specialized"]
    assert "淘宝" in lanes["qianwen"]["specialized"]

    dimensions = set(brief["required_dimensions"] + brief["optional_dimensions"])
    assert {"基础信息", "旅游攻略", "投资运营", "法人", "实际控制人", "客流热度"} <= dimensions
    assert len(brief["optional_dimensions"]) <= 8


def test_provider_lanes_keep_general_and_ecosystem_prompts_distinct():
    lanes = build_provider_query_lanes(
        build_research_brief("1970文创园", depth="deep")
    )

    for source_id in provider_ids():
        assert lanes[source_id]["general"] != lanes[source_id]["specialized"]
        assert "1970文创园" in lanes[source_id]["general"]
        assert "1970文创园" in lanes[source_id]["specialized"]


@pytest.mark.asyncio
async def test_dual_provider_lanes_launch_concurrently_and_merge(monkeypatch):
    entered = 0
    both_entered = asyncio.Event()
    calls = []

    async def fake_collect(queries, timeout, original_query, lane_name="single"):
        nonlocal entered
        calls.append((lane_name, queries))
        entered += 1
        if entered == 2:
            both_entered.set()
        await asyncio.wait_for(both_entered.wait(), timeout=1)
        return {
            "elapsed_ms": 1,
            "results": {
                source_id: {
                    "status": "complete",
                    "partial": False,
                    "answer": f"{lane_name}-{source_id}",
                    "references": [],
                }
                for source_id in provider_ids()
            },
        }

    monkeypatch.setattr(search_mcp, "_collect_provider_results", fake_collect)
    lanes = build_provider_query_lanes(
        build_research_brief("1970文创园", depth="deep")
    )

    result = await search_mcp._collect_provider_lanes(
        lanes,
        timeout=1,
        original_query="1970文创园",
    )

    assert {lane_name for lane_name, _ in calls} == {"general", "specialized"}
    assert result["results"]["doubao"]["status"] == "complete"
    assert "general-doubao" in result["results"]["doubao"]["answer"]
    assert "specialized-doubao" in result["results"]["doubao"]["answer"]


def test_complete_lane_citation_keeps_its_corroboration_eligibility():
    merged = search_mcp._merge_provider_lane_results(
        {
            "status": "complete",
            "partial": False,
            "answer": "complete answer",
            "references": [{"title": "Official", "url": "https://example.com/a"}],
        },
        {
            "status": "timeout",
            "partial": False,
            "answer": "",
            "references": [],
        },
    )

    assert merged["status"] == "partial"
    assert merged["references"] == [
        {
            "title": "Official",
            "url": "https://example.com/a",
            "lane": "general",
            "source_status": "complete",
            "eligible_for_corroboration": True,
        }
    ]

    research = search_mcp.build_research_round(
        query="test",
        profile_name="general",
        profiles=search_mcp.load_profiles(search_mcp.EVIDENCE_PROFILE_FILE),
        elapsed_ms=1,
        results={"tavily": merged},
        research_brief={
            "query": "test",
            "research_kind": "general",
            "required_dimensions": ["evidence"],
        },
    )
    assert research["providers"]["tavily"]["eligible_for_corroboration"] is False
    assert research["providers"]["tavily"]["citations"][0][
        "eligible_for_corroboration"
    ] is True
    assert research["coverage_matrix"][0]["status"] == "covered"


def test_place_dimension_coverage_maps_canonical_signals_to_display_dimensions():
    brief = build_research_brief("1970文创园", depth="deep")
    matrix = build_coverage_matrix(
        brief,
        [
            {
                "is_resolved": True,
                "eligible_for_corroboration": True,
                "signals": ["traffic"],
                "claim_scope": ["short_video"],
            },
            {
                "is_resolved": True,
                "eligible_for_corroboration": True,
                "signals": ["package"],
                "claim_scope": ["coupon"],
            },
        ],
        [],
    )
    status = {entry["dimension"]: entry["status"] for entry in matrix}

    assert status["客流热度"] == "covered"
    assert status["团购活动"] == "covered"
    assert "投资运营" in status
    assert "实际控制人" in status


def test_qianwen_query_is_read_only_and_ecosystem_targeted():
    queries = build_provider_queries(
        "1970文创园",
        profile_name="balanced",
        research_brief=None,
        source_ids=provider_ids(),
    )
    prompt = queries["qianwen"]
    assert "淘宝" in prompt
    assert "飞猪" in prompt
    assert "高德" in prompt
    assert "饿了么" in prompt
    assert "不得下单" in prompt
    assert "不得查询个人支付宝" in prompt
