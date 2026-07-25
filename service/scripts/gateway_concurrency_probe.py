#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from pathlib import Path

import httpx


async def run(url: str, keys: list[str]) -> dict:
    started = time.monotonic()

    async def one(index: int, key: str) -> dict:
        item_started = time.monotonic()
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(
                f"{url.rstrip('/')}/v1/providers/status",
                headers={"Authorization": f"Bearer {key}"},
            )
        return {
            "index": index,
            "status_code": response.status_code,
            "duration_seconds": time.monotonic() - item_started,
        }

    results = await asyncio.gather(*(one(i, key) for i, key in enumerate(keys, 1)))
    durations = [item["duration_seconds"] for item in results]
    ordered = sorted(durations)
    p95_index = max(0, min(len(ordered) - 1, int(len(ordered) * 0.95) - 1))
    return {
        "clients": len(keys),
        "completed": sum(item["status_code"] == 200 for item in results),
        "wall_seconds": time.monotonic() - started,
        "p50_seconds": statistics.median(durations),
        "p95_seconds": ordered[p95_index],
        "status_codes": {
            str(code): sum(item["status_code"] == code for item in results)
            for code in sorted({item["status_code"] for item in results})
        },
        "results": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Probe 20 authenticated gateway clients")
    parser.add_argument("--url", default="https://mcp-search.bri-king.com")
    parser.add_argument("--handoff", type=Path, required=True)
    parser.add_argument("--clients", type=int, default=20)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    values = json.loads(args.handoff.read_text(encoding="utf-8"))["keys"]
    keys = [item["key"] for item in values]
    if not keys:
        parser.error("handoff contains no keys")
    selected = [keys[index % len(keys)] for index in range(args.clients)]
    report = asyncio.run(run(args.url, selected))
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    if report["completed"] != args.clients:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
