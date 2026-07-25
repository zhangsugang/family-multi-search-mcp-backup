from contextlib import asynccontextmanager
import importlib.util
from pathlib import Path
import re

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "live_concurrency_probe.py"
SPEC = importlib.util.spec_from_file_location("live_concurrency_probe", SCRIPT)
probe = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(probe)


def test_build_job_query_contains_only_its_unique_marker():
    query = probe.build_job_query("1970文创园", "基础信息", "LIVE-DOUBAO-2-01")

    assert set(probe.MARKER_PATTERN.findall(query)) == {"LIVE-DOUBAO-2-01"}
    assert "公开信息" in query
    assert "不要执行交易" in query


def test_percentile_uses_nearest_rank():
    assert probe.percentile([1.0, 2.0, 3.0, 4.0], 0.5) == 2.0
    assert probe.percentile([1.0, 2.0, 3.0, 4.0], 0.95) == 4.0


@pytest.mark.asyncio
async def test_run_level_passes_only_distinct_isolated_results():
    class FakeSearch:
        _CONFIG_CACHE = {}

        @staticmethod
        def get_runtime_config():
            return {}

        @staticmethod
        @asynccontextmanager
        async def _browser_provider_slot(provider):
            yield

        @staticmethod
        async def _doubao_query(query, timeout=120):
            marker = re.search(r"LIVE-[A-Z]+-\d+-\d+", query).group(0)
            return {
                "answer": f"result {marker}",
                "conversation_url": f"https://example.test/{marker}",
                "partial": False,
            }

    summary = await probe.run_level(
        FakeSearch,
        provider="doubao",
        level=2,
        base_query="1970文创园",
        timeout=1,
    )

    assert summary["passed"] is True
    assert summary["completed"] == 2
    assert summary["distinct_conversation_urls"] == 2
