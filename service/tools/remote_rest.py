from __future__ import annotations

import json
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from tools.family_auth import AddressLimitError
from tools.family_limits import AdmissionTimeout
from tools.jobs import JobNotFound
from tools.remote_mcp import RemoteComponents
from tools.remote_models import bounded_int, sanitize_public, validate_query


def _principal(request: Request):
    principal = request.scope.get("state", {}).get("principal")
    if principal is None:
        raise PermissionError("authorization required")
    return principal


async def _body(request: Request) -> dict[str, Any]:
    try:
        value = await request.json()
    except json.JSONDecodeError as exc:
        raise ValueError("invalid JSON body") from exc
    if not isinstance(value, dict):
        raise ValueError("JSON body must be an object")
    return value


def build_rest_routes(components: RemoteComponents) -> list[Route]:
    async def healthz(_request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    async def readyz(request: Request) -> JSONResponse:
        principal = _principal(request)
        principal.require("providers:read")
        return JSONResponse({"status": "ready", "key_registry": "available"})

    async def providers(request: Request) -> JSONResponse:
        principal = _principal(request)
        principal.require("providers:read")
        async with components.limiter.slot(principal, "provider"):
            result = components.service.provider_status()
        return JSONResponse(sanitize_public(result))

    async def search(request: Request) -> JSONResponse:
        principal = _principal(request)
        principal.require("search:research")
        body = await _body(request)
        query = validate_query(body.get("query"))
        async with components.limiter.slot(principal, "research"):
            result = await components.service.research_round(
                query=query,
                profile=str(body.get("profile", "general")),
                max_answer=bounded_int(body.get("max_answer"), 2200, 0, 12000),
                max_refs=bounded_int(body.get("max_refs"), 30, 0, 200),
                timeout=bounded_int(body.get("timeout"), 90, 10, 180),
            )
        return JSONResponse(sanitize_public(result))

    async def research(request: Request) -> JSONResponse:
        principal = _principal(request)
        principal.require("search:research")
        body = await _body(request)
        query = validate_query(body.get("query"))
        timeout = bounded_int(body.get("timeout"), 90, 10, 180)
        wait = bounded_int(body.get("wait_seconds"), 3, 0, 20)
        profile = str(body.get("profile", "general"))

        async def runner() -> dict:
            return await components.service.research_round(
                query=query, profile=profile, timeout=timeout
            )

        job = await components.scheduler.submit(principal.owner_id, runner)
        public = await components.scheduler.wait(job.request_id, principal.owner_id, wait)
        status_code = 200 if public["status"] in {"complete", "failed"} else 202
        return JSONResponse(public, status_code=status_code)

    async def get_research(request: Request) -> JSONResponse:
        principal = _principal(request)
        principal.require("search:research")
        public = await components.scheduler.public(
            request.path_params["request_id"], principal.owner_id
        )
        return JSONResponse(public)

    async def continue_research(request: Request) -> JSONResponse:
        principal = _principal(request)
        principal.require("research:continue")
        prior = await components.jobs.get(
            request.path_params["request_id"], principal.owner_id
        )
        body = await _body(request)
        query = validate_query(body.get("query"))
        timeout = bounded_int(body.get("timeout"), 90, 10, 180)
        context = prior.result or {}
        original = str(context.get("query") or context.get("original_query") or "").strip()
        followup = f"基于此前研究对象 {original}，继续研究：{query}" if original else query

        async def runner() -> dict:
            return await components.service.research_round(
                query=followup, timeout=timeout
            )

        job = await components.scheduler.submit(principal.owner_id, runner)
        public = await components.scheduler.public(job.request_id, principal.owner_id)
        return JSONResponse(public, status_code=202)

    return [
        Route("/healthz", healthz, methods=["GET"]),
        Route("/readyz", readyz, methods=["GET"]),
        Route("/v1/providers/status", providers, methods=["GET"]),
        Route("/v1/search", search, methods=["POST"]),
        Route("/v1/research", research, methods=["POST"]),
        Route("/v1/research/{request_id}", get_research, methods=["GET"]),
        Route(
            "/v1/research/{request_id}/continue",
            continue_research,
            methods=["POST"],
        ),
    ]


def error_response(exc: Exception) -> JSONResponse:
    if isinstance(exc, AddressLimitError):
        return JSONResponse(
            {"error": "address_limit_exceeded", "message": "this key already has 10 bound addresses"},
            status_code=403,
        )
    if isinstance(exc, AdmissionTimeout):
        return JSONResponse(
            {"error": "queue_timeout", "message": "request queue is full"},
            status_code=429,
            headers={"Retry-After": "5"},
        )
    if isinstance(exc, JobNotFound):
        return JSONResponse({"error": "not_found"}, status_code=404)
    if isinstance(exc, PermissionError):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    if isinstance(exc, (ValueError, TypeError)):
        return JSONResponse({"error": "invalid_request", "message": str(exc)}, status_code=400)
    return JSONResponse({"error": "internal_error"}, status_code=500)
