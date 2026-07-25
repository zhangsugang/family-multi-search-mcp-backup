#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import re
import resource
import statistics
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

PROVIDERS = ("doubao", "yuanbao", "wenxin", "grok", "gemini", "qianwen")
DIMENSIONS = (
    "基础信息", "旅游攻略", "交通停车", "团购活动", "客流热度",
    "投资运营", "运营主体", "法人", "实际控制人", "招商业态",
    "用户评价", "近期活动", "风险争议", "同名实体消歧", "官方来源",
    "抖音头条", "公众号视频号", "X实时舆情", "YouTube Reddit", "阿里生态",
)
MARKER_PATTERN = re.compile(r"LIVE-[A-Z]+-\d+-\d+")


def percentile(values: list[float], percentile_value: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, math.ceil(percentile_value * len(ordered)) - 1)
    return round(ordered[index], 3)


def build_job_query(base_query: str, dimension: str, marker: str) -> str:
    return (
        f"{base_query}\n"
        f"只研究公开信息，研究维度：{dimension}。"
        f"请在最终回答中原样包含唯一测试标记 {marker}，不要执行交易或查询个人记录。"
    )


async def run_provider_job(search, provider: str, level: int, index: int,
                           base_query: str, timeout: int) -> dict:
    marker = f"LIVE-{provider.upper()}-{level}-{index + 1:02d}"
    dimension = DIMENSIONS[index % len(DIMENSIONS)]
    query = build_job_query(base_query, dimension, marker)
    started = time.monotonic()
    try:
        async with search._browser_provider_slot(provider):
            adapter = getattr(search, f"_{provider}_query")
            if provider == "qianwen":
                result = await adapter(
                    query,
                    timeout=timeout,
                    policy_intent=f"研究公开项目信息：{base_query}",
                )
            else:
                result = await adapter(query, timeout=timeout)
        answer = result.get("answer") if isinstance(result, dict) else ""
        answer = answer if isinstance(answer, str) else ""
        conversation_url = result.get("conversation_url", "")
        conversation_url = conversation_url if isinstance(conversation_url, str) else ""
        markers = set(MARKER_PATTERN.findall(answer))
        foreign = sorted(found for found in markers if found != marker)
        return {
            "index": index,
            "marker": marker,
            "dimension": dimension,
            "completed": bool(answer.strip()),
            "own_marker_present": marker in answer,
            "foreign_marker_present": bool(foreign),
            "foreign_marker_count": len(foreign),
            "conversation_url": conversation_url,
            "partial": bool(result.get("partial")),
            "duration_seconds": round(time.monotonic() - started, 3),
            "failure_class": None,
        }
    except BaseException as error:
        if isinstance(error, (KeyboardInterrupt, SystemExit)):
            raise
        return {
            "index": index,
            "marker": marker,
            "dimension": dimension,
            "completed": False,
            "own_marker_present": False,
            "foreign_marker_present": False,
            "foreign_marker_count": 0,
            "conversation_url": "",
            "partial": False,
            "duration_seconds": round(time.monotonic() - started, 3),
            "failure_class": type(error).__name__,
        }


async def run_level(search, provider: str, level: int, base_query: str,
                    timeout: int) -> dict:
    runtime_config = dict(search.get_runtime_config())
    limits = dict(runtime_config.get("browser_provider_active_limits", {}))
    limits[provider] = level
    runtime_config["browser_provider_active_limits"] = limits
    search._CONFIG_CACHE = runtime_config

    wall_started = time.monotonic()
    results = await asyncio.gather(*(
        run_provider_job(search, provider, level, index, base_query, timeout)
        for index in range(level)
    ))
    wall_seconds = round(time.monotonic() - wall_started, 3)
    durations = [result["duration_seconds"] for result in results]
    completed = sum(bool(result["completed"]) for result in results)
    urls = [result["conversation_url"] for result in results if result["conversation_url"]]
    distinct_urls = len(set(urls))
    isolated = all(
        result["own_marker_present"] and not result["foreign_marker_present"]
        for result in results
    )
    passed = completed == level and isolated and distinct_urls == level
    failures = Counter(
        result["failure_class"] or "marker_or_url_gate"
        for result in results
        if not (
            result["completed"]
            and result["own_marker_present"]
            and not result["foreign_marker_present"]
            and result["conversation_url"]
        )
    )
    return {
        "provider": provider,
        "level": level,
        "started": level,
        "completed": completed,
        "failed": level - completed,
        "isolated": isolated,
        "distinct_conversation_urls": distinct_urls,
        "passed": passed,
        "wall_seconds": wall_seconds,
        "p50_seconds": round(statistics.median(durations), 3) if durations else 0.0,
        "p95_seconds": percentile(durations, 0.95),
        "failure_classes": dict(sorted(failures.items())),
        "process_max_rss": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "results": results,
    }


async def run(args: argparse.Namespace) -> Path:
    os.environ["MULTI_SEARCH_PRIVATE_ROOT"] = str(args.private_root)
    from tools import multi_search_mcp as search

    levels = [int(value) for value in args.levels.split(",") if value.strip()]
    if not levels or any(level < 1 or level > 20 for level in levels):
        raise ValueError("levels must contain integers from 1 through 20")

    summaries = []
    for level in levels:
        summary = await run_level(
            search,
            provider=args.provider,
            level=level,
            base_query=args.query,
            timeout=args.timeout,
        )
        summaries.append(summary)
        print(
            f"{args.provider} level={level} completed={summary['completed']}/{level} "
            f"isolated={summary['isolated']} urls={summary['distinct_conversation_urls']} "
            f"wall={summary['wall_seconds']}s passed={summary['passed']}"
        )
        if not summary["passed"]:
            break

    args.output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    output_path = args.output_dir / f"{args.provider}-{timestamp}.json"
    output = {
        "provider": args.provider,
        "query_label": args.query,
        "requested_levels": levels,
        "highest_passing_level": max(
            (summary["level"] for summary in summaries if summary["passed"]),
            default=0,
        ),
        "levels": summaries,
    }
    output_path.write_text(
        json.dumps(output, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.chmod(output_path, 0o600)
    return output_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Run staged isolated browser-provider probes")
    parser.add_argument("--provider", required=True, choices=PROVIDERS)
    parser.add_argument("--levels", default="2,5,10,20")
    parser.add_argument("--query", default="1970文创园")
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument(
        "--private-root",
        type=Path,
        default=Path.home() / ".zcode/mcp/multi-search-mcp/private",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("/tmp/family-multi-search-probes"),
    )
    args = parser.parse_args()
    output_path = asyncio.run(run(args))
    print(f"probe summary: {output_path}")


if __name__ == "__main__":
    main()
