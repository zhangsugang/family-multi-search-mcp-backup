from __future__ import annotations

import contextvars
from dataclasses import dataclass

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from tools.family_auth import FamilyPrincipal
from tools.family_limits import InvocationLimiter
from tools.jobs import JobStore
from tools.research_scheduler import ResearchScheduler
from tools.remote_models import bounded_int, sanitize_public, validate_query
from tools.search_service import SearchService


CURRENT_PRINCIPAL: contextvars.ContextVar[FamilyPrincipal | None] = contextvars.ContextVar(
    "multi_search_principal", default=None
)


@dataclass(frozen=True)
class RemoteComponents:
    service: SearchService
    limiter: InvocationLimiter
    jobs: JobStore
    scheduler: ResearchScheduler


def require_principal(scope: str) -> FamilyPrincipal:
    principal = CURRENT_PRINCIPAL.get()
    if principal is None:
        raise PermissionError("authorization required")
    principal.require(scope)
    return principal


def build_remote_mcp(components: RemoteComponents) -> FastMCP:
    remote_mcp = FastMCP(
        "family-multi-search-remote",
        instructions=(
            "Eight-source evidence research. Preserve citations, conflicts, unknowns, "
            "provider status, and confidence explanations in final answers."
        ),
        host="127.0.0.1",
        port=8765,
        streamable_http_path="/mcp",
        json_response=True,
        stateless_http=False,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=[
                "127.0.0.1",
                "127.0.0.1:*",
                "localhost",
                "localhost:*",
                "mcp-search.bri-king.com",
            ],
            allowed_origins=["https://mcp-search.bri-king.com"],
        ),
    )

    @remote_mcp.tool()
    async def search_once(
        query: str,
        profile: str = "general",
        max_answer: int = 2200,
        max_refs: int = 30,
        timeout: int = 90,
    ) -> dict:
        """Run one bounded eight-source evidence round and return structured results."""
        principal = require_principal("search:research")
        clean_query = validate_query(query)
        async with components.limiter.slot(principal, "research"):
            result = await components.service.research_round(
                query=clean_query,
                profile=profile,
                max_answer=bounded_int(max_answer, 2200, 0, 12000),
                max_refs=bounded_int(max_refs, 30, 0, 200),
                timeout=bounded_int(timeout, 90, 10, 180),
            )
        return sanitize_public(result)

    @remote_mcp.tool()
    async def research(
        query: str,
        profile: str = "general",
        timeout: int = 90,
        wait_seconds: int = 3,
    ) -> dict:
        """Start a background eight-source research job and optionally wait briefly."""
        principal = require_principal("search:research")
        clean_query = validate_query(query)
        bounded_timeout = bounded_int(timeout, 90, 10, 180)
        wait = bounded_int(wait_seconds, 3, 0, 20)

        async def runner() -> dict:
            return await components.service.research_round(
                query=clean_query,
                profile=profile,
                timeout=bounded_timeout,
            )

        job = await components.scheduler.submit(principal.key_id, runner)
        return await components.scheduler.wait(job.request_id, principal.key_id, wait)

    @remote_mcp.tool()
    async def get_research_result(request_id: str) -> dict:
        """Get an owned background research job."""
        principal = require_principal("search:research")
        return await components.scheduler.public(request_id, principal.key_id)

    @remote_mcp.tool()
    async def continue_research(
        request_id: str,
        query: str,
        timeout: int = 90,
    ) -> dict:
        """Start a follow-up job grounded in an owned prior research result."""
        principal = require_principal("research:continue")
        prior = await components.jobs.get(request_id, principal.key_id)
        clean_query = validate_query(query)
        context = prior.result or {}
        original = str(context.get("query") or context.get("original_query") or "").strip()
        followup = f"基于此前研究对象 {original}，继续研究：{clean_query}" if original else clean_query

        async def runner() -> dict:
            return await components.service.research_round(
                query=followup,
                timeout=bounded_int(timeout, 90, 10, 180),
            )

        job = await components.scheduler.submit(principal.key_id, runner)
        return await components.scheduler.public(job.request_id, principal.key_id)

    @remote_mcp.tool()
    async def provider_status() -> dict:
        """Return a redacted provider configuration summary without starting browsers."""
        principal = require_principal("providers:read")
        async with components.limiter.slot(principal, "provider"):
            return sanitize_public(components.service.provider_status())

    return remote_mcp
