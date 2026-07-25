import asyncio

import pytest

from tools import multi_search_mcp as search_mcp


BROWSER_PROVIDERS = (
    "doubao",
    "yuanbao",
    "wenxin",
    "grok",
    "gemini",
    "qianwen",
)


def test_browser_provider_capacity_and_measured_active_limits():
    assert search_mcp.BROWSER_PROVIDER_CONFIG == {
        "doubao": {"capacity": 20, "active_limit": 10},
        "yuanbao": {"capacity": 20, "active_limit": 10},
        "wenxin": {"capacity": 20, "active_limit": 20},
        "grok": {"capacity": 20, "active_limit": 20},
        "gemini": {"capacity": 20, "active_limit": 10},
        "qianwen": {"capacity": 20, "active_limit": 5},
    }


def test_runtime_config_can_raise_measured_active_limit(monkeypatch):
    monkeypatch.setattr(
        search_mcp,
        "_CONFIG_CACHE",
        {"browser_provider_active_limits": {"doubao": 5}},
    )

    assert search_mcp._browser_provider_active_limit("doubao") == 5


@pytest.mark.asyncio
async def test_provider_pool_exposes_twenty_logical_reservations(monkeypatch):
    monkeypatch.setattr(search_mcp, "_CONFIG_CACHE", {})

    pool = await search_mcp._browser_provider_pool("doubao")

    assert pool.capacity == 20


def test_shared_profile_cdp_endpoints_are_loopback_only():
    assert search_mcp.DOUBAO_CDP_URL == "http://127.0.0.1:9333"
    assert search_mcp.GROK_CDP_URL == "http://127.0.0.1:9555"
    assert search_mcp.GEMINI_CDP_URL == "http://127.0.0.1:9556"
    assert search_mcp.QIANWEN_CDP_URL == "http://127.0.0.1:9557"


def test_grok_lifecycle_state_is_event_loop_scoped():
    async def get_state():
        return search_mcp._grok_lifecycle_state()

    first = asyncio.run(get_state())
    second = asyncio.run(get_state())

    assert first is not second
    assert first["lock"] is not second["lock"]



@pytest.mark.asyncio
async def test_cdp_task_pages_are_new_for_every_request():
    created = []

    class FakeContext:
        async def new_page(self):
            page = object()
            created.append(page)
            return page

    browser = type("FakeBrowser", (), {"contexts": [FakeContext()]})()

    first, second = await asyncio.gather(
        search_mcp._new_browser_task_page(browser, "测试来源"),
        search_mcp._new_browser_task_page(browser, "测试来源"),
    )

    assert first is not second
    assert created == [first, second]


@pytest.mark.asyncio
async def test_provider_startup_is_serialized_without_serializing_queries(monkeypatch):
    active = 0
    peak = 0

    async def fake_to_thread(function):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1

    monkeypatch.setattr(search_mcp.asyncio, "to_thread", fake_to_thread)
    ensure = lambda: None

    await asyncio.gather(
        search_mcp._ensure_browser_ready("gemini", ensure),
        search_mcp._ensure_browser_ready("gemini", ensure),
    )

    assert peak == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider", "tool_name", "adapter_name"),
    [
        ("doubao", "doubao_search", "_doubao_query"),
        ("yuanbao", "yuanbao_search", "_yuanbao_query"),
        ("wenxin", "wenxin_search", "_wenxin_query"),
        ("gemini", "gemini_search", "_gemini_query"),
        ("grok", "grok_search", "_grok_query"),
        ("qianwen", "qianwen_search", "_qianwen_query"),
    ],
)
async def test_direct_browser_tools_allow_measured_parallelism(
    monkeypatch, provider, tool_name, adapter_name
):
    original = search_mcp.BROWSER_PROVIDER_CONFIG[provider]
    monkeypatch.setitem(
        search_mcp.BROWSER_PROVIDER_CONFIG,
        provider,
        {**original, "active_limit": 2},
    )
    monkeypatch.setattr(search_mcp, "_CONFIG_CACHE", {})

    active = 0
    peak = 0
    both_entered = asyncio.Event()
    release = asyncio.Event()

    async def fake_adapter(*args, **kwargs):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        if active == 2:
            both_entered.set()
        await release.wait()
        active -= 1
        return {"answer": "ok", "references": [], "partial": False}

    monkeypatch.setattr(search_mcp, adapter_name, fake_adapter)
    tool = getattr(search_mcp, tool_name)
    tasks = [asyncio.create_task(tool(f"query-{index}")) for index in range(2)]
    await asyncio.wait_for(both_entered.wait(), timeout=1)
    release.set()
    results = await asyncio.gather(*tasks)

    assert peak == 2
    assert all("ok" in result for result in results)


@pytest.mark.asyncio
async def test_active_limit_queues_excess_browser_work(monkeypatch):
    provider = "doubao"
    original = search_mcp.BROWSER_PROVIDER_CONFIG[provider]
    monkeypatch.setitem(
        search_mcp.BROWSER_PROVIDER_CONFIG,
        provider,
        {**original, "active_limit": 2},
    )
    monkeypatch.setattr(search_mcp, "_CONFIG_CACHE", {})

    active = 0
    peak = 0

    async def fake_adapter(*args, **kwargs):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.02)
        active -= 1
        return {"answer": "ok", "references": [], "partial": False}

    monkeypatch.setattr(search_mcp, "_doubao_query", fake_adapter)
    await asyncio.gather(*(search_mcp.doubao_search(str(index)) for index in range(5)))

    assert peak == 2


@pytest.mark.asyncio
async def test_collection_reserves_time_to_reap_timed_out_provider_cleanup(monkeypatch):
    cleaned = 0

    async def slow_provider(*args, **kwargs):
        nonlocal cleaned
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await asyncio.sleep(0.005)
            cleaned += 1
            raise

    for adapter_name in (
        "_doubao_query",
        "_yuanbao_query",
        "_wenxin_query",
        "_tavily_query_async",
        "_exa_query_async",
        "_gemini_query",
        "_grok_query",
        "_qianwen_query",
    ):
        monkeypatch.setattr(search_mcp, adapter_name, slow_provider)
    monkeypatch.setattr(search_mcp, "_CONFIG_CACHE", {})
    queries = {source_id: source_id for source_id in search_mcp.provider_ids()}

    result = await search_mcp._collect_provider_results(
        queries,
        timeout=0.1,
        original_query="公开信息研究",
    )

    assert cleaned == 8
    assert {
        provider["status"] for provider in result["results"].values()
    } == {"timeout"}


@pytest.mark.asyncio
async def test_caller_cancellation_during_timeout_cleanup_reaps_provider_tasks(monkeypatch):
    cleanup_started = asyncio.Event()
    cleanup_release = asyncio.Event()

    async def slow_provider(*args, **kwargs):
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cleanup_started.set()
            await cleanup_release.wait()
            raise

    for adapter_name in (
        "_doubao_query",
        "_yuanbao_query",
        "_wenxin_query",
        "_tavily_query_async",
        "_exa_query_async",
        "_gemini_query",
        "_grok_query",
        "_qianwen_query",
    ):
        monkeypatch.setattr(search_mcp, adapter_name, slow_provider)
    monkeypatch.setattr(search_mcp, "_CONFIG_CACHE", {})
    queries = {source_id: source_id for source_id in search_mcp.provider_ids()}
    collector = asyncio.create_task(
        search_mcp._collect_provider_results(
            queries,
            timeout=0.2,
            original_query="公开信息研究",
        )
    )

    await asyncio.wait_for(cleanup_started.wait(), timeout=1)
    collector.cancel()
    await asyncio.sleep(0)
    assert not collector.done()

    cleanup_release.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(collector, timeout=1)
