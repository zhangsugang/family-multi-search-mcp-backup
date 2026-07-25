import asyncio

import pytest

from tools.browser_pool import BrowserSessionPool


def test_constructor_rejects_empty_name_and_boolean_or_nonpositive_capacity():
    async def create():
        return object()

    async def destroy(value):
        pass

    with pytest.raises(ValueError, match="name"):
        BrowserSessionPool("", 1, create, destroy)
    with pytest.raises(ValueError, match="capacity"):
        BrowserSessionPool("test", True, create, destroy)
    with pytest.raises(ValueError, match="capacity"):
        BrowserSessionPool("test", 0, create, destroy)


@pytest.mark.asyncio
async def test_start_rejects_boolean_prewarm():
    async def create():
        return object()

    async def destroy(value):
        pass

    pool = BrowserSessionPool("test", 1, create, destroy)
    with pytest.raises(ValueError, match="prewarm"):
        await pool.start(prewarm=True)


@pytest.mark.asyncio
async def test_sessions_are_exclusive_and_replaced_after_use():
    created = []
    destroyed = []

    async def create():
        value = f"session-{len(created)}"
        created.append(value)
        return value

    async def destroy(value):
        destroyed.append(value)

    pool = BrowserSessionPool("test", 2, create, destroy)
    await pool.start(prewarm=2)

    async with pool.session() as first, pool.session() as second:
        assert first != second

    assert len(created) == 4
    assert sorted(destroyed) == ["session-0", "session-1"]
    await pool.close()


@pytest.mark.asyncio
async def test_cancelled_session_is_destroyed_and_replenished():
    created = []
    destroyed = []

    async def create():
        value = f"session-{len(created)}"
        created.append(value)
        return value

    async def destroy(value):
        destroyed.append(value)

    pool = BrowserSessionPool("test", 1, create, destroy)
    await pool.start(prewarm=1)

    async def worker():
        async with pool.session():
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await worker()

    assert destroyed == ["session-0"]
    assert created == ["session-0", "session-1"]
    await pool.close()


@pytest.mark.asyncio
async def test_zero_prewarm_lazily_creates_without_waiting_on_an_empty_queue():
    created = []
    destroyed = []

    async def create():
        value = object()
        created.append(value)
        return value

    async def destroy(value):
        destroyed.append(value)

    pool = BrowserSessionPool("lazy", 2, create, destroy)
    await pool.start(prewarm=0)

    async def use_one():
        async with pool.session() as value:
            return value

    first, second = await asyncio.wait_for(
        asyncio.gather(use_one(), use_one()), timeout=1
    )

    assert first is not second
    assert len(created) == 4
    assert destroyed == [first, second]
    await pool.close()


@pytest.mark.asyncio
async def test_partial_prewarm_lazily_fills_remaining_capacity():
    created = []

    async def create():
        value = object()
        created.append(value)
        return value

    async def destroy(value):
        pass

    pool = BrowserSessionPool("partial", 2, create, destroy)
    await pool.start(prewarm=1)

    async def use_two():
        async with pool.session() as first, pool.session() as second:
            assert first is not second

    await asyncio.wait_for(use_two(), timeout=1)
    assert len(created) == 4
    await pool.close()


@pytest.mark.asyncio
async def test_real_task_cancellation_finishes_cleanup_before_releasing_capacity():
    created = []
    destroyed = []
    entered = asyncio.Event()
    destroy_started = asyncio.Event()
    allow_destroy = asyncio.Event()
    waiter_entered = asyncio.Event()

    async def create():
        value = object()
        created.append(value)
        return value

    async def destroy(value):
        destroy_started.set()
        await allow_destroy.wait()
        destroyed.append(value)

    pool = BrowserSessionPool("cancel", 1, create, destroy)
    await pool.start(prewarm=1)

    async def cancelled_worker():
        async with pool.session():
            entered.set()
            await asyncio.Event().wait()

    async def waiting_worker():
        async with pool.session():
            waiter_entered.set()

    worker = asyncio.create_task(cancelled_worker())
    await entered.wait()
    waiter = asyncio.create_task(waiting_worker())
    worker.cancel()
    await destroy_started.wait()

    await asyncio.sleep(0)
    assert not worker.done()
    assert not waiter_entered.is_set()
    assert len(created) == 1

    allow_destroy.set()
    with pytest.raises(asyncio.CancelledError):
        await worker
    await asyncio.wait_for(waiter, timeout=1)

    assert len(created) == 3
    assert destroyed[:2] == created[:2]
    await pool.close()


@pytest.mark.asyncio
async def test_cancellation_during_cleanup_finishes_replacement_and_wins_over_error():
    created = []
    destroy_started = asyncio.Event()
    allow_destroy = asyncio.Event()
    first_destroy = True

    async def create():
        value = object()
        created.append(value)
        return value

    async def destroy(value):
        nonlocal first_destroy
        destroy_started.set()
        await allow_destroy.wait()
        if first_destroy:
            first_destroy = False
            raise RuntimeError("destroy failed")

    pool = BrowserSessionPool("cleanup-cancel", 1, create, destroy)
    await pool.start(prewarm=1)

    async def worker():
        async with pool.session():
            pass

    task = asyncio.create_task(worker())
    await destroy_started.wait()
    task.cancel()
    allow_destroy.set()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(created) == 2

    async with pool.session() as replacement:
        assert replacement is created[1]
    await pool.close()


@pytest.mark.asyncio
async def test_body_exception_is_preserved_after_replacement():
    created = []
    destroyed = []

    async def create():
        value = object()
        created.append(value)
        return value

    async def destroy(value):
        destroyed.append(value)

    pool = BrowserSessionPool("body-error", 1, create, destroy)
    await pool.start(prewarm=1)

    with pytest.raises(LookupError, match="body failed"):
        async with pool.session():
            raise LookupError("body failed")

    assert destroyed == [created[0]]
    assert len(created) == 2
    async with pool.session() as replacement:
        assert replacement is created[1]
    await pool.close()


class _EqualSession:
    def __eq__(self, other):
        return isinstance(other, _EqualSession)


@pytest.mark.asyncio
async def test_replace_uses_identity_and_rejects_foreign_equal_sessions():
    created = []
    destroyed = []

    async def create():
        value = _EqualSession()
        created.append(value)
        return value

    async def destroy(value):
        destroyed.append(value)

    pool = BrowserSessionPool("identity", 1, create, destroy)
    await pool.start(prewarm=1)

    async with pool.session() as checked_out:
        foreign = _EqualSession()
        assert foreign == checked_out
        with pytest.raises(ValueError, match="not checked out"):
            await pool.replace(foreign)

    assert destroyed == [created[0]]
    await pool.close()


@pytest.mark.asyncio
async def test_public_replace_returns_once_and_rejects_double_replacement():
    created = []
    destroyed = []

    async def create():
        value = object()
        created.append(value)
        return value

    async def destroy(value):
        destroyed.append(value)

    pool = BrowserSessionPool("replace", 1, create, destroy)
    await pool.start(prewarm=1)

    async with pool.session() as checked_out:
        await pool.replace(checked_out)
        with pytest.raises(ValueError, match="not checked out"):
            await pool.replace(checked_out)

    assert len(created) == 2
    assert destroyed == [checked_out]
    await pool.close()


@pytest.mark.asyncio
async def test_replacement_must_not_requeue_the_retired_object():
    session = object()
    destroyed = []

    async def create():
        return session

    async def destroy(value):
        destroyed.append(value)

    pool = BrowserSessionPool("same-object", 1, create, destroy)
    await pool.start(prewarm=1)

    with pytest.raises(RuntimeError, match="retired session"):
        async with pool.session():
            pass

    assert destroyed == [session]
    await pool.close()


@pytest.mark.asyncio
async def test_startup_failure_rolls_back_and_allows_retry():
    created = []
    destroyed = []
    fail = True

    async def create():
        nonlocal fail
        if fail and created:
            raise RuntimeError("startup failed")
        value = object()
        created.append(value)
        return value

    async def destroy(value):
        destroyed.append(value)

    pool = BrowserSessionPool("startup", 2, create, destroy)

    with pytest.raises(RuntimeError, match="startup failed"):
        await pool.start(prewarm=2)

    assert destroyed == [created[0]]
    fail = False
    await pool.start(prewarm=2)
    async with pool.session() as first, pool.session() as second:
        assert first is not second
    await pool.close()


@pytest.mark.asyncio
async def test_startup_cancellation_rolls_back_and_allows_retry():
    created = []
    destroyed = []
    second_create_started = asyncio.Event()
    block_second_create = True

    async def create():
        nonlocal block_second_create
        if block_second_create and created:
            second_create_started.set()
            await asyncio.Event().wait()
        value = object()
        created.append(value)
        return value

    async def destroy(value):
        destroyed.append(value)

    pool = BrowserSessionPool("startup-cancel", 2, create, destroy)
    starting = asyncio.create_task(pool.start(prewarm=2))
    await second_create_started.wait()
    starting.cancel()

    with pytest.raises(asyncio.CancelledError):
        await starting

    assert len(created) == 1
    assert destroyed == created
    with pytest.raises(RuntimeError, match="not been started"):
        async with pool.session():
            pass

    block_second_create = False
    await pool.start(prewarm=2)
    async with pool.session() as first, pool.session() as second:
        assert first is not second
    await pool.close()


@pytest.mark.asyncio
async def test_replacement_create_failure_releases_capacity_for_lazy_recovery():
    created = []
    create_calls = 0

    async def create():
        nonlocal create_calls
        create_calls += 1
        if create_calls == 2:
            raise RuntimeError("replacement failed")
        value = object()
        created.append(value)
        return value

    async def destroy(value):
        pass

    pool = BrowserSessionPool("create-failure", 1, create, destroy)
    await pool.start(prewarm=1)

    with pytest.raises(RuntimeError, match="replacement failed"):
        async with pool.session():
            pass

    async def recover():
        async with pool.session() as value:
            return value

    recovered = await asyncio.wait_for(recover(), timeout=1)
    assert recovered is created[1]
    await pool.close()


@pytest.mark.asyncio
async def test_destroy_failure_still_replenishes_and_releases_capacity():
    created = []
    destroy_calls = 0

    async def create():
        value = object()
        created.append(value)
        return value

    async def destroy(value):
        nonlocal destroy_calls
        destroy_calls += 1
        if destroy_calls == 1:
            raise RuntimeError("destroy failed")

    pool = BrowserSessionPool("destroy-failure", 1, create, destroy)
    await pool.start(prewarm=1)

    with pytest.raises(RuntimeError, match="destroy failed"):
        async with pool.session():
            pass

    async def recover():
        async with pool.session() as value:
            return value

    recovered = await asyncio.wait_for(recover(), timeout=1)
    assert recovered is created[1]
    await pool.close()


@pytest.mark.asyncio
async def test_body_exception_wins_over_cleanup_error_and_pool_recovers():
    created = []
    first_destroy = True

    async def create():
        value = object()
        created.append(value)
        return value

    async def destroy(value):
        nonlocal first_destroy
        if first_destroy:
            first_destroy = False
            raise RuntimeError("cleanup failed")

    pool = BrowserSessionPool("error-priority", 1, create, destroy)
    await pool.start(prewarm=1)

    with pytest.raises(KeyError, match="body failed"):
        async with pool.session():
            raise KeyError("body failed")

    async with pool.session() as replacement:
        assert replacement is created[1]
    await pool.close()


@pytest.mark.asyncio
async def test_concurrent_and_repeated_close_destroy_idle_sessions_once():
    created = []
    destroyed = []
    destroy_started = asyncio.Event()
    allow_destroy = asyncio.Event()

    async def create():
        value = object()
        created.append(value)
        return value

    async def destroy(value):
        destroy_started.set()
        await allow_destroy.wait()
        destroyed.append(value)

    pool = BrowserSessionPool("close", 1, create, destroy)
    await pool.start(prewarm=1)

    first_close = asyncio.create_task(pool.close())
    await destroy_started.wait()
    second_close = asyncio.create_task(pool.close())
    await asyncio.sleep(0)
    assert not first_close.done()
    assert not second_close.done()

    allow_destroy.set()
    await asyncio.gather(first_close, second_close)
    await pool.close()

    assert destroyed == created


@pytest.mark.asyncio
async def test_close_waits_for_active_checkout_cleanup_without_replenishing():
    created = []
    destroyed = []
    entered = asyncio.Event()
    release_body = asyncio.Event()

    async def create():
        value = object()
        created.append(value)
        return value

    async def destroy(value):
        destroyed.append(value)

    pool = BrowserSessionPool("active-close", 1, create, destroy)
    await pool.start(prewarm=1)

    async def worker():
        async with pool.session():
            entered.set()
            await release_body.wait()

    active = asyncio.create_task(worker())
    await entered.wait()
    closing = asyncio.create_task(pool.close())
    await asyncio.sleep(0)
    assert not closing.done()

    release_body.set()
    await asyncio.gather(active, closing)

    assert created == [destroyed[0]]
    await pool.close()


@pytest.mark.asyncio
async def test_replacement_created_during_close_is_destroyed_not_enqueued():
    created = []
    destroyed = []
    replacement_started = asyncio.Event()
    allow_replacement = asyncio.Event()

    async def create():
        if created:
            replacement_started.set()
            await allow_replacement.wait()
        value = object()
        created.append(value)
        return value

    async def destroy(value):
        destroyed.append(value)

    pool = BrowserSessionPool("close-race", 1, create, destroy)
    await pool.start(prewarm=1)

    async def worker():
        async with pool.session():
            pass

    active = asyncio.create_task(worker())
    await replacement_started.wait()
    closing = asyncio.create_task(pool.close())
    await asyncio.sleep(0)
    assert not closing.done()

    allow_replacement.set()
    await asyncio.gather(active, closing)

    assert len(created) == 2
    assert sorted(map(id, destroyed)) == sorted(map(id, created))


@pytest.mark.asyncio
async def test_waiting_checkout_is_rejected_when_close_begins():
    entered = asyncio.Event()
    release_body = asyncio.Event()

    async def create():
        return object()

    async def destroy(value):
        pass

    pool = BrowserSessionPool("waiting-close", 1, create, destroy)
    await pool.start(prewarm=1)

    async def active_worker():
        async with pool.session():
            entered.set()
            await release_body.wait()

    async def waiting_worker():
        async with pool.session():
            pass

    active = asyncio.create_task(active_worker())
    await entered.wait()
    waiting = asyncio.create_task(waiting_worker())
    await asyncio.sleep(0)
    closing = asyncio.create_task(pool.close())
    release_body.set()

    await active
    with pytest.raises(RuntimeError, match="closed"):
        await asyncio.wait_for(waiting, timeout=1)
    await closing
