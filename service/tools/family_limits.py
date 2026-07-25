from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass

from tools.family_auth import FamilyPrincipal


class AdmissionTimeout(TimeoutError):
    pass


@dataclass
class _PerKeyState:
    limit: int
    semaphore: asyncio.Semaphore


class InvocationLimiter:
    def __init__(
        self,
        *,
        global_research_limit: int = 2,
        provider_limit: int = 20,
        queue_timeout: float = 30.0,
    ):
        self._global_research = asyncio.Semaphore(global_research_limit)
        self._provider = asyncio.Semaphore(provider_limit)
        self._per_address: dict[str, _PerKeyState] = {}
        self.queue_timeout = queue_timeout

    def _address_semaphore(self, principal: FamilyPrincipal) -> asyncio.Semaphore:
        identity = principal.owner_id
        state = self._per_address.get(identity)
        if state is None or state.limit != principal.max_concurrent_research:
            state = _PerKeyState(
                principal.max_concurrent_research,
                asyncio.Semaphore(principal.max_concurrent_research),
            )
            self._per_address[identity] = state
        return state.semaphore

    async def _acquire(self, semaphore: asyncio.Semaphore) -> None:
        try:
            await asyncio.wait_for(semaphore.acquire(), timeout=self.queue_timeout)
        except TimeoutError as exc:
            raise AdmissionTimeout("request queue is full") from exc

    @asynccontextmanager
    async def slot(self, principal: FamilyPrincipal, category: str):
        if category == "research":
            address_semaphore = self._address_semaphore(principal)
            await self._acquire(address_semaphore)
            try:
                await self._acquire(self._global_research)
            except BaseException:
                address_semaphore.release()
                raise
            try:
                yield
            finally:
                self._global_research.release()
                address_semaphore.release()
            return
        if category == "provider":
            await self._acquire(self._provider)
            try:
                yield
            finally:
                self._provider.release()
            return
        raise ValueError("unknown invocation category")
