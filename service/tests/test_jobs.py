from __future__ import annotations

import asyncio

import pytest

from tools.family_limits import AdmissionTimeout
from tools.jobs import JobNotFound, JobStore


@pytest.mark.asyncio
async def test_job_store_keeps_jobs_queued_until_run_and_isolates_owners():
    store = JobStore()

    async def runner():
        return {
            "query": "example",
            "conversation_url": "https://private.invalid/chat/1",
            "nested": {"cdp_url": "http://127.0.0.1:9557"},
        }

    job = await store.create("owner-a", runner)
    assert job.status == "queued"
    with pytest.raises(JobNotFound):
        await store.get(job.request_id, "owner-b")

    await store.run(job.request_id)
    completed = await store.wait(job.request_id, "owner-a", 1)

    assert completed.status == "complete"
    assert "conversation_url" not in completed.public()["result"]
    assert "cdp_url" not in completed.public()["result"]["nested"]


@pytest.mark.asyncio
async def test_job_store_allows_only_one_unfinished_job_per_owner():
    store = JobStore(max_pending_per_owner=1)

    async def runner():
        return {"status": "complete"}

    first = await store.create("owner-a", runner)
    with pytest.raises(AdmissionTimeout):
        await store.create("owner-a", runner)

    await store.run(first.request_id)
    second = await store.create("owner-a", runner)
    assert second.status == "queued"


@pytest.mark.asyncio
async def test_job_store_close_cancels_queued_jobs():
    store = JobStore()

    async def runner():
        return {"status": "complete"}

    job = await store.create("owner-a", runner)
    await store.close()

    assert (await store.get(job.request_id, "owner-a")).status == "cancelled"
