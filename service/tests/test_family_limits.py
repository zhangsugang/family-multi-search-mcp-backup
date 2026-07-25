from __future__ import annotations

import asyncio

import pytest

from tools.family_auth import FamilyPrincipal
from tools.family_limits import InvocationLimiter


@pytest.mark.asyncio
async def test_research_limiter_caps_global_concurrency():
    limiter = InvocationLimiter(global_research_limit=2, queue_timeout=2)
    active = 0
    maximum = 0
    lock = asyncio.Lock()

    async def run(index: int):
        nonlocal active, maximum
        principal = FamilyPrincipal(
            key_id=f"key-{index}",
            label=str(index),
            scopes=frozenset({"search:research"}),
            max_concurrent_research=1,
        )
        async with limiter.slot(principal, "research"):
            async with lock:
                active += 1
                maximum = max(maximum, active)
            await asyncio.sleep(0.02)
            async with lock:
                active -= 1

    await asyncio.gather(*(run(index) for index in range(20)))

    assert maximum == 2
    assert active == 0


@pytest.mark.asyncio
async def test_research_limiter_releases_after_cancellation():
    limiter = InvocationLimiter(global_research_limit=1, queue_timeout=1)
    principal = FamilyPrincipal(
        "one", "one", frozenset({"search:research"}), 1
    )
    entered = asyncio.Event()

    async def blocked():
        async with limiter.slot(principal, "research"):
            entered.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(blocked())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    async with limiter.slot(principal, "research"):
        pass
