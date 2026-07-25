from __future__ import annotations

import asyncio
import secrets
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from tools.family_limits import AdmissionTimeout
from tools.remote_models import sanitize_public


@dataclass
class ResearchJob:
    request_id: str
    owner_id: str
    runner: Callable[[], Awaitable[dict]] = field(repr=False)
    status: str = "queued"
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    updated_at: float = field(default_factory=time.time)
    result: dict | None = None
    done: asyncio.Event = field(default_factory=asyncio.Event, repr=False)

    def public(self, *, queue_position: int | None = None) -> dict:
        value = {
            "request_id": self.request_id,
            "status": self.status,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
        }
        if self.status == "queued" and queue_position is not None:
            value["queue_position"] = queue_position
        if self.result is not None:
            value["result"] = sanitize_public(self.result)
        return value


class JobNotFound(KeyError):
    pass


class JobStore:
    def __init__(
        self,
        *,
        max_jobs: int = 200,
        max_pending_total: int = 20,
        max_pending_per_owner: int = 1,
        ttl_seconds: float = 3600,
    ):
        self.max_jobs = max_jobs
        self.max_pending_total = max_pending_total
        self.max_pending_per_owner = max_pending_per_owner
        self.ttl_seconds = ttl_seconds
        self._jobs: dict[str, ResearchJob] = {}
        self._lock = asyncio.Lock()

    async def _prune_locked(self) -> None:
        cutoff = time.time() - self.ttl_seconds
        expired = [
            request_id
            for request_id, job in self._jobs.items()
            if job.updated_at < cutoff and job.status not in {"queued", "running"}
        ]
        for request_id in expired:
            self._jobs.pop(request_id, None)

    async def create(
        self,
        owner_id: str,
        runner: Callable[[], Awaitable[dict]],
    ) -> ResearchJob:
        async with self._lock:
            await self._prune_locked()
            pending = [
                job for job in self._jobs.values() if job.status in {"queued", "running"}
            ]
            if len(pending) >= self.max_pending_total:
                raise AdmissionTimeout("research queue is full")
            if sum(job.owner_id == owner_id for job in pending) >= self.max_pending_per_owner:
                raise AdmissionTimeout("this address already has unfinished research")
            if len(self._jobs) >= self.max_jobs:
                completed = sorted(
                    (job for job in self._jobs.values() if job.status not in {"queued", "running"}),
                    key=lambda item: item.updated_at,
                )
                if not completed:
                    raise AdmissionTimeout("research job store is full")
                self._jobs.pop(completed[0].request_id, None)
            request_id = secrets.token_urlsafe(18)
            job = ResearchJob(
                request_id=request_id,
                owner_id=owner_id,
                runner=runner,
            )
            self._jobs[request_id] = job
            return job

    async def run(self, request_id: str) -> None:
        async with self._lock:
            job = self._jobs.get(request_id)
            if job is None or job.status != "queued":
                return
            job.status = "running"
            job.started_at = time.time()
            job.updated_at = job.started_at
        try:
            result = sanitize_public(await job.runner())
            async with self._lock:
                job.result = result
                job.status = "complete"
        except asyncio.CancelledError:
            async with self._lock:
                job.status = "cancelled"
                job.result = {"error": "cancelled"}
            raise
        except Exception:
            async with self._lock:
                job.result = {"error": "research failed"}
                job.status = "failed"
        finally:
            async with self._lock:
                job.updated_at = time.time()
                job.done.set()

    async def get(self, request_id: str, owner_id: str) -> ResearchJob:
        async with self._lock:
            await self._prune_locked()
            job = self._jobs.get(request_id)
            if job is None or job.owner_id != owner_id:
                raise JobNotFound(request_id)
            return job

    async def get_internal(self, request_id: str) -> ResearchJob | None:
        async with self._lock:
            return self._jobs.get(request_id)

    async def wait(
        self, request_id: str, owner_id: str, timeout: float
    ) -> ResearchJob:
        job = await self.get(request_id, owner_id)
        if job.status in {"queued", "running"} and timeout > 0:
            try:
                await asyncio.wait_for(job.done.wait(), timeout=timeout)
            except TimeoutError:
                pass
        return await self.get(request_id, owner_id)

    async def cancel_queued(self, request_id: str) -> None:
        async with self._lock:
            job = self._jobs.get(request_id)
            if job is not None and job.status == "queued":
                job.status = "cancelled"
                job.result = {"error": "cancelled"}
                job.updated_at = time.time()
                job.done.set()

    async def close(self) -> None:
        async with self._lock:
            for job in self._jobs.values():
                if job.status == "queued":
                    job.status = "cancelled"
                    job.result = {"error": "service stopped"}
                    job.updated_at = time.time()
                    job.done.set()
