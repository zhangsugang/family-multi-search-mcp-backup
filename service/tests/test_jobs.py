from __future__ import annotations

import asyncio

import pytest

from tools.family_limits import AdmissionTimeout
from tools.jobs import JobNotFound, JobStore


@pytest.mark.asyncio
async def test_job_store_isolates_owners_and_sanitizes_results():
    store = JobStore()

    async def runner():
        return {
            "query": "example",
            "conversation_url": "https://private.invalid/chat/1",
            "nested": {"cdp_url": "http://127.0.0.1:9557"},
        }

    job = await store.create("owner-a", runner)
    job = await store.wait(job.request_id, "owner-a", 1)

    assert job.status == "complete"
    assert "conversation_url" not in job.public()["result"]
    assert "cdp_url" not in job.public()["result"]["nested"]
    with pytest.raises(JobNotFound):
        await store.get(job.request_id, "owner-b")


@pytest.mark.asyncio
async def test_job_store_bounds_pending_jobs_per_key():
    store = JobStore(max_pending_per_key=2)
    release = asyncio.Event()

    async def runner():
        await release.wait()
        return {"status": "complete"}

    await store.create("owner-a", runner)
    await store.create("owner-a", runner)
    with pytest.raises(AdmissionTimeout):
        await store.create("owner-a", runner)

    release.set()
    await store.close()
