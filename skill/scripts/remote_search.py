#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional


DEFAULT_CONFIG = Path.home() / ".config" / "multi-search-remote" / "config.json"


def load_config(path: Path) -> dict:
    value = {}
    if path.exists():
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            value.update(loaded)
    if os.environ.get("MULTI_SEARCH_URL"):
        value["base_url"] = os.environ["MULTI_SEARCH_URL"]
    if os.environ.get("MULTI_SEARCH_KEY"):
        value["access_key"] = os.environ["MULTI_SEARCH_KEY"]
    base_url = str(value.get("base_url", "https://mcp-search.bri-king.com")).rstrip("/")
    key = str(value.get("access_key", "")).strip()
    if not key:
        raise SystemExit(f"missing access key; run setup.sh or configure {path}")
    return {"base_url": base_url, "access_key": key}


def request(config: dict, method: str, path: str, payload: Optional[dict] = None) -> dict:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {config['access_key']}",
        "User-Agent": "multi-search-remote/0.3.4",
    }
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(
        f"{config['base_url']}{path}", data=body, headers=headers, method=method
    )
    try:
        with urllib.request.urlopen(req, timeout=190) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        content = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"remote request failed ({exc.code}): {content}") from None


def render_markdown(value: dict) -> str:
    result = value.get("result") if isinstance(value.get("result"), dict) else value
    lines = [f"Status: {value.get('status', result.get('status', 'complete'))}"]
    if value.get("request_id"):
        lines.append(f"Request ID: {value['request_id']}")
    summary = result.get("summary") or result.get("evidence_digest", {}).get("summary")
    if summary:
        lines.extend(["", "## Summary", str(summary)])
    claims = result.get("claims", [])
    if claims:
        lines.extend(["", "## Claims"])
        for claim in claims:
            if isinstance(claim, dict):
                text = claim.get("claim") or claim.get("text") or json.dumps(claim, ensure_ascii=False)
                confidence = claim.get("confidence_label") or claim.get("confidence_score")
                lines.append(f"- {text}" + (f" ({confidence})" if confidence is not None else ""))
            else:
                lines.append(f"- {claim}")
    sources = result.get("sources") or result.get("unique_citations") or []
    if sources:
        lines.extend(["", "## Sources"])
        for source in sources[:50]:
            if isinstance(source, dict):
                title = source.get("title") or source.get("url") or "source"
                url = source.get("url")
                lines.append(f"- [{title}]({url})" if url else f"- {title}")
    for key, title in (("conflicts", "Conflicts"), ("unknowns", "Unknowns"), ("verification_queue", "Evidence gaps")):
        items = result.get(key, [])
        if items:
            lines.extend(["", f"## {title}"])
            lines.extend(f"- {json.dumps(item, ensure_ascii=False) if isinstance(item, dict) else item}" for item in items)
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Remote family multi-search client")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--json", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)
    search = sub.add_parser("search")
    search.add_argument("--query", required=True)
    search.add_argument("--timeout", type=int, default=90)
    search.add_argument("--mode", choices=("fast", "balanced", "deep"), default="balanced")
    research = sub.add_parser("research")
    research.add_argument("--query", required=True)
    research.add_argument("--timeout", type=int, default=130)
    research.add_argument("--mode", choices=("fast", "balanced", "deep"), default="deep")
    research.add_argument("--wait", action="store_true")
    get = sub.add_parser("get")
    get.add_argument("request_id")
    get.add_argument("--wait", action="store_true")
    cont = sub.add_parser("continue")
    cont.add_argument("request_id")
    cont.add_argument("--query", required=True)
    cont.add_argument("--timeout", type=int, default=130)
    cont.add_argument("--mode", choices=("fast", "balanced", "deep"), default="deep")
    sub.add_parser("status")
    args = parser.parse_args()
    config = load_config(args.config.expanduser())

    if args.command == "search":
        value = request(
            config,
            "POST",
            "/v1/search",
            {"query": args.query, "timeout": args.timeout, "mode": args.mode},
        )
    elif args.command == "research":
        value = request(
            config,
            "POST",
            "/v1/research",
            {
                "query": args.query,
                "timeout": args.timeout,
                "wait_seconds": 3,
                "mode": args.mode,
            },
        )
    elif args.command == "get":
        value = request(config, "GET", f"/v1/research/{args.request_id}")
    elif args.command == "continue":
        value = request(
            config,
            "POST",
            f"/v1/research/{args.request_id}/continue",
            {"query": args.query, "timeout": args.timeout, "mode": args.mode},
        )
    else:
        value = request(config, "GET", "/v1/providers/status")

    should_wait = bool(getattr(args, "wait", False))
    while should_wait and value.get("status") in {"queued", "running"}:
        time.sleep(2)
        value = request(config, "GET", f"/v1/research/{value['request_id']}")
    print(json.dumps(value, ensure_ascii=False, indent=2) if args.json else render_markdown(value))


if __name__ == "__main__":
    main()
