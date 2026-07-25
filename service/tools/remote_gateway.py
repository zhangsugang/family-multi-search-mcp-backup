from __future__ import annotations

import ipaddress
import os
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from starlette.applications import Starlette
from starlette.routing import Mount

from tools import multi_search_mcp as runtime
from tools.family_auth import (
    AddressLimitError,
    AuthenticationError,
    FamilyKeyRegistry,
    bearer_token,
)
from tools.family_limits import AdmissionTimeout, InvocationLimiter
from tools.jobs import JobNotFound, JobStore
from tools.research_scheduler import ResearchScheduler
from tools.remote_mcp import (
    CURRENT_PRINCIPAL,
    RemoteComponents,
    build_remote_mcp,
)
from tools.remote_rest import build_rest_routes, error_response
from tools.search_service import SearchService


def _normalized_client_address(scope, headers: dict[str, str]) -> str:
    client = scope.get("client")
    if not isinstance(client, (tuple, list)) or not client:
        raise AuthenticationError("client address is required")
    try:
        direct = ipaddress.ip_address(str(client[0]))
    except ValueError as exc:
        raise AuthenticationError("invalid client address") from exc
    candidate = headers.get("cf-connecting-ip") if direct.is_loopback else None
    try:
        address = ipaddress.ip_address(candidate or str(direct))
    except ValueError as exc:
        raise AuthenticationError("invalid client address") from exc
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    return address.compressed


class FamilyAuthenticationMiddleware:
    def __init__(self, app, registry: FamilyKeyRegistry):
        self.app = app
        self.registry = registry

    async def __call__(self, scope, receive, send):
        if scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return
        if scope.get("path") == "/healthz":
            await self.app(scope, receive, send)
            return
        headers = {
            key.decode("latin-1").lower(): value.decode("latin-1")
            for key, value in scope.get("headers", [])
        }
        try:
            principal = self.registry.authenticate(
                bearer_token(headers.get("authorization"))
            )
            principal = self.registry.authorize_address(
                principal,
                _normalized_client_address(scope, headers),
            )
        except AddressLimitError as exc:
            response = error_response(exc)
            await response(scope, receive, send)
            return
        except AuthenticationError:
            response = error_response(PermissionError("authorization required"))
            response.status_code = 401
            response.headers["WWW-Authenticate"] = "Bearer"
            await response(scope, receive, send)
            return
        scope.setdefault("state", {})["principal"] = principal
        token = CURRENT_PRINCIPAL.set(principal)
        try:
            await self.app(scope, receive, send)
        finally:
            CURRENT_PRINCIPAL.reset(token)


def _registry_path() -> Path:
    configured = os.environ.get("MULTI_SEARCH_FAMILY_KEYS", "").strip()
    if configured:
        return Path(configured).expanduser()
    return runtime._private_root() / "search-mcp" / "family-keys.json"


def create_app(
    *,
    registry: FamilyKeyRegistry | None = None,
    service: SearchService | None = None,
    limiter: InvocationLimiter | None = None,
    jobs: JobStore | None = None,
    scheduler: ResearchScheduler | None = None,
):
    registry = registry or FamilyKeyRegistry(_registry_path())
    job_store = jobs or JobStore()
    research_scheduler = scheduler or ResearchScheduler(job_store, worker_count=2)
    components = RemoteComponents(
        service=service or SearchService(runtime),
        limiter=limiter or InvocationLimiter(),
        jobs=job_store,
        scheduler=research_scheduler,
    )
    remote_mcp = build_remote_mcp(components)
    mcp_app = remote_mcp.streamable_http_app()

    @asynccontextmanager
    async def lifespan(_app):
        async with remote_mcp.session_manager.run():
            await components.scheduler.start()
            try:
                yield
            finally:
                await components.scheduler.close()

    async def handle_error(_request, exc):
        return error_response(exc)

    application = Starlette(
        routes=[*build_rest_routes(components), Mount("/", app=mcp_app)],
        lifespan=lifespan,
        exception_handlers={
            JobNotFound: handle_error,
            AdmissionTimeout: handle_error,
            PermissionError: handle_error,
            ValueError: handle_error,
            TypeError: handle_error,
        },
    )
    application.state.components = components
    application.state.registry = registry
    application.state.remote_mcp = remote_mcp
    wrapped = FamilyAuthenticationMiddleware(application, registry)
    wrapped.inner_app = application
    return wrapped


def main() -> None:
    uvicorn.run(
        create_app(),
        host="127.0.0.1",
        port=8765,
        workers=1,
        log_level="info",
        access_log=False,
    )


if __name__ == "__main__":
    main()
