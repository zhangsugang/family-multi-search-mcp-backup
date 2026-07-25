from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable

from tools.jobs import JobStore, ResearchJob


class ResearchScheduler:
    """Bounded FIFO scheduler for browser-heavy full research rounds."""

    def __init__(self, jobs: JobStore, *, worker_count: int = 2):
        if isinstance(worker_count, bool) or worker_count < 1:
            raise ValueError("worker_count must be positive")
        self.jobs = jobs
        self.worker_count = worker_count
        self._queue: deque[str] = deque()
        self._condition = asyncio.Condition()
        self._workers: list[asyncio.Task] = []
        self._closing = False

    async def start(self) -> None:
        if self._workers:
            return
        self._closing = False
        self._workers = [
            asyncio.create_task(self._worker(index), name=f"research-worker:{index}")
            for index in range(1, self.worker_count + 1)
        ]

    async def submit(
        self,
        owner_key_id: str,
        runner: Callable[[], Awaitable[dict]],
    ) -> ResearchJob:
        if self._closing or not self._workers:
            raise RuntimeError("research scheduler is not running")
        job = await self.jobs.create(owner_key_id, runner)
        async with self._condition:
            self._queue.append(job.request_id)
            self._condition.notify(1)
        return job

    async def _next_request_id(self) -> str:
        async with self._condition:
            await self._condition.wait_for(lambda: self._queue or self._closing)
            if self._closing:
                raise asyncio.CancelledError
            return self._queue.popleft()

    async def _worker(self, _index: int) -> None:
        while True:
            try:
                request_id = await self._next_request_id()
                await self.jobs.run(request_id)
            except asyncio.CancelledError:
                raise

    async def queue_position(self, request_id: str) -> int | None:
        async with self._condition:
            try:
                return list(self._queue).index(request_id) + 1
            except ValueError:
                return None

    async def public(self, request_id: str, owner_key_id: str) -> dict:
        job = await self.jobs.get(request_id, owner_key_id)
        position = await self.queue_position(request_id) if job.status == "queued" else None
        return job.public(queue_position=position)

    async def wait(
        self, request_id: str, owner_key_id: str, timeout: float
    ) -> dict:
        await self.jobs.wait(request_id, owner_key_id, timeout)
        return await self.public(request_id, owner_key_id)

    async def close(self) -> None:
        self._closing = True
        async with self._condition:
            queued = list(self._queue)
            self._queue.clear()
            self._condition.notify_all()
        for request_id in queued:
            await self.jobs.cancel_queued(request_id)
        workers = list(self._workers)
        self._workers.clear()
        for worker in workers:
            worker.cancel()
        if workers:
            await asyncio.gather(*workers, return_exceptions=True)
        await self.jobs.close()
