from __future__ import annotations

import asyncio
import secrets
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from tools.family_limits import AdmissionTimeout
from tools.remote_models import sanitize_public


@dataclass
class ResearchJob:
    request_id: str
    owner_key_id: str
    status: str = "queued"
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    result: dict | None = None
    task: asyncio.Task | None = None

    def public(self) -> dict:
        value = {
            "request_id": self.request_id,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
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
        max_pending_per_key: int = 2,
        ttl_seconds: float = 3600,
    ):
        self.max_jobs = max_jobs
        self.max_pending_total = max_pending_total
        self.max_pending_per_key = max_pending_per_key
        self.ttl_seconds = ttl_seconds
        self._jobs: dict[str, ResearchJob] = {}
        self._lock = asyncio.Lock()

    async def _prune(self) -> None:
        cutoff = time.time() - self.ttl_seconds
        expired = [
            request_id
            for request_id, job in self._jobs.items()
            if job.updated_at < cutoff and (job.task is None or job.task.done())
        ]
        for request_id in expired:
            self._jobs.pop(request_id, None)

    async def create(
        self,
        owner_key_id: str,
        runner: Callable[[], Awaitable[dict]],
    ) -> ResearchJob:
        async with self._lock:
            await self._prune()
            pending = [
                job for job in self._jobs.values() if job.status in {"queued", "running"}
            ]
            if len(pending) >= self.max_pending_total:
                raise AdmissionTimeout("research queue is full")
            if sum(job.owner_key_id == owner_key_id for job in pending) >= self.max_pending_per_key:
                raise AdmissionTimeout("per-key research queue is full")
            if len(self._jobs) >= self.max_jobs:
                completed = sorted(
                    (
                        job
                        for job in self._jobs.values()
                        if job.status not in {"queued", "running"}
                    ),
                    key=lambda item: item.updated_at,
                )
                if not completed:
                    raise AdmissionTimeout("research job store is full")
                self._jobs.pop(completed[0].request_id, None)
            request_id = secrets.token_urlsafe(18)
            job = ResearchJob(request_id=request_id, owner_key_id=owner_key_id)
            self._jobs[request_id] = job
            job.task = asyncio.create_task(
                self._run(job, runner), name=f"multi-search-job:{request_id}"
            )
            return job

    async def _run(
        self, job: ResearchJob, runner: Callable[[], Awaitable[dict]]
    ) -> None:
        job.status = "running"
        job.updated_at = time.time()
        try:
            job.result = sanitize_public(await runner())
            job.status = "complete"
        except asyncio.CancelledError:
            job.status = "cancelled"
            raise
        except AdmissionTimeout:
            job.result = {"error": "queue_timeout", "message": "request queue is full"}
            job.status = "queue_timeout"
        except Exception:
            job.result = {"error": "research failed"}
            job.status = "failed"
        finally:
            job.updated_at = time.time()

    async def get(self, request_id: str, owner_key_id: str) -> ResearchJob:
        async with self._lock:
            await self._prune()
            job = self._jobs.get(request_id)
            if job is None or job.owner_key_id != owner_key_id:
                raise JobNotFound(request_id)
            return job

    async def wait(
        self, request_id: str, owner_key_id: str, timeout: float
    ) -> ResearchJob:
        job = await self.get(request_id, owner_key_id)
        if job.task is not None and not job.task.done() and timeout > 0:
            try:
                await asyncio.wait_for(asyncio.shield(job.task), timeout=timeout)
            except TimeoutError:
                pass
        return job

    async def close(self) -> None:
        tasks = [job.task for job in self._jobs.values() if job.task and not job.task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
