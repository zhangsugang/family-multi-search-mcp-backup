#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
import threading
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Optional


PRIVATE_CONFIG = Path.home() / ".config" / "multi-search-remote" / "config.json"
ZCODE_CONFIG = Path.home() / ".zcode" / "cli" / "config.json"


def load_config() -> dict[str, str]:
    if PRIVATE_CONFIG.is_file():
        value = json.loads(PRIVATE_CONFIG.read_text(encoding="utf-8"))
        key = str(value.get("access_key", "")).strip()
        base_url = str(
            value.get("base_url", "https://mcp-search.bri-king.com")
        ).rstrip("/")
        if key:
            return {"access_key": key, "base_url": base_url}
    if ZCODE_CONFIG.is_file():
        value = json.loads(ZCODE_CONFIG.read_text(encoding="utf-8"))
        server = value.get("mcp", {}).get("servers", {}).get("multi-search", {})
        authorization = str(server.get("headers", {}).get("Authorization", ""))
        if authorization.startswith("Bearer "):
            return {
                "access_key": authorization.removeprefix("Bearer ").strip(),
                "base_url": "https://mcp-search.bri-king.com",
            }
    raise RuntimeError("family Key is not configured; run skill/setup.sh --client zcode")


def request(method: str, path: str, payload: Optional[dict] = None) -> dict:
    config = load_config()
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {config['access_key']}",
        "User-Agent": "family-multi-search-zcode-proxy/0.3.5",
    }
    if body is not None:
        headers["Content-Type"] = "application/json"
    operation = urllib.request.Request(
        config["base_url"] + path,
        data=body,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(operation, timeout=190) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        message = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"remote request failed ({exc.code}): {message}") from None
    except urllib.error.URLError as exc:
        raise RuntimeError(f"remote service unavailable: {exc.reason}") from None


TOOLS = [
    {
        "name": "search_once",
        "description": "Run one bounded eight-source evidence research round.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "profile": {"type": "string", "default": "general"},
                "max_answer": {"type": "integer", "default": 2200},
                "max_refs": {"type": "integer", "default": 30},
                "timeout": {"type": "integer", "default": 90},
                "mode": {
                    "type": "string",
                    "enum": ["fast", "balanced", "deep"],
                    "default": "balanced",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "research_round",
        "description": "Compatibility alias for one deep evidence research round.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "profile": {"type": "string", "default": "general"},
                "max_answer": {"type": "integer", "default": 2200},
                "max_refs": {"type": "integer", "default": 30},
                "timeout": {"type": "integer", "default": 130},
            },
            "required": ["query"],
        },
    },
    {
        "name": "research",
        "description": "Start a queued eight-source background research job.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "profile": {"type": "string", "default": "general"},
                "timeout": {"type": "integer", "default": 130},
                "wait_seconds": {"type": "integer", "default": 3},
                "mode": {
                    "type": "string",
                    "enum": ["fast", "balanced", "deep"],
                    "default": "deep",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_research_result",
        "description": "Get a queued or completed owned research job.",
        "inputSchema": {
            "type": "object",
            "properties": {"request_id": {"type": "string"}},
            "required": ["request_id"],
        },
    },
    {
        "name": "continue_research",
        "description": "Start a follow-up job grounded in a prior owned result.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "request_id": {"type": "string"},
                "query": {"type": "string"},
                "timeout": {"type": "integer", "default": 130},
                "mode": {
                    "type": "string",
                    "enum": ["fast", "balanced", "deep"],
                    "default": "deep",
                },
            },
            "required": ["request_id", "query"],
        },
    },
    {
        "name": "provider_status",
        "description": "Return the redacted eight-source provider status.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def call_tool(name: str, arguments: dict[str, Any]) -> dict:
    if name == "search_once":
        return request("POST", "/v1/search", arguments)
    if name == "research_round":
        return request("POST", "/v1/search", {**arguments, "mode": "deep"})
    if name == "research":
        return request("POST", "/v1/research", arguments)
    if name == "get_research_result":
        return request("GET", f"/v1/research/{arguments['request_id']}")
    if name == "continue_research":
        request_id = arguments["request_id"]
        payload = {key: value for key, value in arguments.items() if key != "request_id"}
        return request("POST", f"/v1/research/{request_id}/continue", payload)
    if name == "provider_status":
        return request("GET", "/v1/providers/status")
    raise ValueError(f"unknown tool: {name}")


_SEND_LOCK = threading.Lock()


def _worker_count() -> int:
    raw = os.environ.get("MULTI_SEARCH_PROXY_WORKERS", "20")
    try:
        value = int(raw)
    except ValueError:
        return 20
    return min(64, max(1, value))


def send(message: dict) -> None:
    payload = json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n"
    with _SEND_LOCK:
        sys.stdout.write(payload)
        sys.stdout.flush()


def result(request_id: Any, value: dict) -> None:
    send({"jsonrpc": "2.0", "id": request_id, "result": value})


def error(request_id: Any, code: int, message: str) -> None:
    send({"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}})


def handle(message: dict) -> None:
    method = message.get("method")
    request_id = message.get("id")
    if method == "initialize":
        protocol = message.get("params", {}).get("protocolVersion", "2025-03-26")
        result(
            request_id,
            {
                "protocolVersion": protocol,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "family-multi-search", "version": "0.3.5"},
                "instructions": "Preserve citations, conflicts, unknowns, and confidence explanations.",
            },
        )
        return
    if method in {"notifications/initialized", "notifications/cancelled"}:
        return
    if method == "ping":
        result(request_id, {})
        return
    if method == "tools/list":
        result(request_id, {"tools": TOOLS})
        return
    if method == "tools/call":
        params = message.get("params", {})
        try:
            value = call_tool(str(params.get("name", "")), dict(params.get("arguments") or {}))
            result(
                request_id,
                {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(value, ensure_ascii=False),
                        }
                    ],
                    "structuredContent": value,
                    "isError": False,
                },
            )
        except Exception as exc:
            result(
                request_id,
                {
                    "content": [{"type": "text", "text": str(exc)}],
                    "isError": True,
                },
            )
        return
    if request_id is not None:
        error(request_id, -32601, f"method not found: {method}")


def main() -> None:
    with ThreadPoolExecutor(
        max_workers=_worker_count(),
        thread_name_prefix="family-search",
    ) as executor:
        for line in sys.stdin:
            try:
                message = json.loads(line)
                if not isinstance(message, dict):
                    continue
                if message.get("method") == "tools/call":
                    executor.submit(handle, message)
                else:
                    handle(message)
            except Exception as exc:
                error(None, -32700, str(exc))


if __name__ == "__main__":
    main()
