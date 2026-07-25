from __future__ import annotations

import asyncio

import pytest

from tools.family_limits import AdmissionTimeout
from tools.jobs import JobStore
from tools.research_scheduler import ResearchScheduler


async def _wait_until(predicate, timeout: float = 1.0):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() >= deadline:
            raise TimeoutError("condition was not reached")
        await asyncio.sleep(0.005)


@pytest.mark.asyncio
async def test_five_users_queue_fairly_with_two_active_workers():
    store = JobStore(max_pending_total=10, max_pending_per_key=1)
    scheduler = ResearchScheduler(store, worker_count=2)
    release = asyncio.Event()
    active = 0
    maximum = 0
    started: list[int] = []
    lock = asyncio.Lock()

    def runner(index: int):
        async def run():
            nonlocal active, maximum
            async with lock:
                active += 1
                maximum = max(maximum, active)
                started.append(index)
            await release.wait()
            async with lock:
                active -= 1
            return {"index": index}

        return run

    await scheduler.start()
    jobs = [
        await scheduler.submit(f"owner-{index}", runner(index))
        for index in range(5)
    ]
    await _wait_until(lambda: len(started) == 2)

    initial = [
        await scheduler.public(job.request_id, f"owner-{index}")
        for index, job in enumerate(jobs)
    ]
    assert [item["status"] for item in initial] == [
        "running",
        "running",
        "queued",
        "queued",
        "queued",
    ]
    assert [item.get("queue_position") for item in initial] == [None, None, 1, 2, 3]

    release.set()
    completed = await asyncio.gather(
        *(
            scheduler.wait(job.request_id, f"owner-{index}", 2)
            for index, job in enumerate(jobs)
        )
    )

    assert all(item["status"] == "complete" for item in completed)
    assert maximum == 2
    assert started == [0, 1, 2, 3, 4]
    await scheduler.close()


@pytest.mark.asyncio
async def test_scheduler_rejects_second_unfinished_job_for_same_key():
    store = JobStore(max_pending_per_key=1)
    scheduler = ResearchScheduler(store, worker_count=1)
    release = asyncio.Event()

    async def runner():
        await release.wait()
        return {"status": "complete"}

    await scheduler.start()
    await scheduler.submit("owner", runner)
    with pytest.raises(AdmissionTimeout):
        await scheduler.submit("owner", runner)
    release.set()
    await scheduler.close()
