"""Provider-independent pool for isolated browser-like sessions."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Generic, TypeVar


SessionT = TypeVar("SessionT")


class BrowserSessionPool(Generic[SessionT]):
    """Manage disposable sessions up to a fixed logical capacity.

    Sessions are never reused after a checkout. Returning a checkout destroys it
    and, while the pool remains open, creates one clean replacement.
    """

    def __init__(
        self,
        name: str,
        capacity: int,
        create_session: Callable[[], Awaitable[SessionT]],
        destroy_session: Callable[[SessionT], Awaitable[None]],
    ) -> None:
        if not isinstance(name, str) or not name.strip():
            raise ValueError("name must be a non-empty string")
        if isinstance(capacity, bool) or not isinstance(capacity, int) or capacity <= 0:
            raise ValueError("capacity must be a positive integer")

        self.name = name
        self.capacity = capacity
        self._create_session = create_session
        self._destroy_session = destroy_session

        self._sessions: asyncio.Queue[SessionT] = asyncio.Queue(maxsize=capacity)
        self._permits = asyncio.Semaphore(capacity)
        self._state_lock = asyncio.Lock()
        self._active_zero = asyncio.Event()
        self._active_zero.set()

        self._known: dict[int, SessionT] = {}
        self._active: dict[int, SessionT] = {}
        self._active_operations = 0

        self._started = False
        self._start_task: asyncio.Task[None] | None = None
        self._closing = False
        self._closed = False
        self._close_task: asyncio.Task[None] | None = None

    async def start(self, prewarm: int) -> None:
        """Start the pool and optionally create some idle sessions."""
        if (
            isinstance(prewarm, bool)
            or not isinstance(prewarm, int)
            or not 0 <= prewarm <= self.capacity
        ):
            raise ValueError("prewarm must be an integer between zero and capacity")

        async with self._state_lock:
            if self._closing or self._closed:
                raise RuntimeError(f"pool {self.name!r} is closed")
            if self._started:
                return
            task = self._start_task
            if task is None:
                task = asyncio.create_task(self._start(prewarm))
                self._start_task = task

        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            await self._cancel_and_wait(task)
            raise

    async def _start(self, prewarm: int) -> None:
        created: list[SessionT] = []
        failure: BaseException | None = None
        try:
            for _ in range(prewarm):
                async with self._state_lock:
                    if self._closing:
                        raise RuntimeError(f"pool {self.name!r} is closed")

                candidate = await self._create_session()
                if any(existing is candidate for existing in created):
                    raise RuntimeError("create_session returned the same session twice")
                created.append(candidate)

            async with self._state_lock:
                if self._closing:
                    raise RuntimeError(f"pool {self.name!r} is closed")
                for candidate in created:
                    self._register_known(candidate)
                    self._sessions.put_nowait(candidate)
                self._started = True
        except BaseException as exc:
            failure = exc
            for candidate in created:
                try:
                    await self._destroy_session(candidate)
                except BaseException:
                    pass
            raise
        finally:
            async with self._state_lock:
                if failure is not None:
                    while not self._sessions.empty():
                        candidate = self._sessions.get_nowait()
                        self._forget_known(candidate)
                    self._started = False
                if self._start_task is asyncio.current_task():
                    self._start_task = None

    @asynccontextmanager
    async def session(self) -> AsyncIterator[SessionT]:
        """Check out one exclusive session and dispose of it on exit."""
        value = await self._checkout()
        body_error: BaseException | None = None
        try:
            yield value
        except BaseException as exc:
            body_error = exc
            raise
        finally:
            cleanup = asyncio.create_task(self._return_if_active(value))
            try:
                await self._await_task(cleanup)
            except BaseException:
                if body_error is None:
                    raise

    async def replace(self, session: SessionT) -> None:
        """Dispose of an active checkout and replace it exactly once."""
        async with self._state_lock:
            current = self._active.get(id(session))
            if current is not session:
                raise ValueError("session is not checked out from this pool")
            del self._active[id(session)]

        cleanup = asyncio.create_task(self._return_claimed(session))
        await self._await_task(cleanup)

    async def close(self) -> None:
        """Close once, waiting for startup and active cleanup to finish."""
        async with self._state_lock:
            task = self._close_task
            if task is None:
                self._closing = True
                task = asyncio.create_task(self._close(self._start_task))
                self._close_task = task

        await self._await_task(task)

    async def _checkout(self) -> SessionT:
        await self._ensure_started()
        await self._permits.acquire()
        operation_started = False

        try:
            async with self._state_lock:
                if self._closing or self._closed:
                    raise RuntimeError(f"pool {self.name!r} is closed")
                self._active_operations += 1
                self._active_zero.clear()
                operation_started = True
                try:
                    value = self._sessions.get_nowait()
                except asyncio.QueueEmpty:
                    value = None
                else:
                    current = self._known.get(id(value))
                    if current is not value:
                        raise RuntimeError("session queue identity is inconsistent")
                    self._active[id(value)] = value
                    return value

            candidate = await self._create_for_checkout()
            dispose = False
            async with self._state_lock:
                if self._closing or self._closed:
                    dispose = True
                else:
                    self._register_known(candidate)
                    self._active[id(candidate)] = candidate
                    return candidate

            if dispose:
                await self._destroy_session(candidate)
                raise RuntimeError(f"pool {self.name!r} is closed")
            raise AssertionError("unreachable")
        except BaseException:
            if operation_started:
                await self._finish_operation()
            else:
                self._permits.release()
            raise

    async def _create_for_checkout(self) -> SessionT:
        task = asyncio.ensure_future(self._create_session())
        cancelled = False

        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                cancelled = True
            except BaseException:
                if cancelled:
                    break
                raise

        if cancelled:
            if not task.cancelled() and task.exception() is None:
                candidate = task.result()
                destroy = asyncio.ensure_future(self._destroy_session(candidate))
                try:
                    await self._await_task(destroy)
                except BaseException:
                    pass
            elif not task.cancelled():
                task.exception()
            raise asyncio.CancelledError

        return task.result()

    async def _return_if_active(self, session: SessionT) -> None:
        async with self._state_lock:
            current = self._active.get(id(session))
            if current is not session:
                return
            del self._active[id(session)]

        await self._return_claimed(session)

    async def _return_claimed(self, session: SessionT) -> None:
        first_error: BaseException | None = None
        replacement: SessionT | None = None
        dispose_replacement = False

        try:
            try:
                await self._destroy_session(session)
            except BaseException as exc:
                first_error = exc

            async with self._state_lock:
                self._forget_known(session)
                should_replace = not self._closing and not self._closed

            if should_replace:
                try:
                    replacement = await self._create_session()
                except BaseException as exc:
                    if first_error is None:
                        first_error = exc
                else:
                    if replacement is session:
                        replacement = None
                        if first_error is None:
                            first_error = RuntimeError(
                                "create_session returned the retired session"
                            )
                    else:
                        async with self._state_lock:
                            if self._closing or self._closed:
                                dispose_replacement = True
                            elif id(replacement) in self._known:
                                if first_error is None:
                                    first_error = RuntimeError(
                                        "create_session returned an existing session"
                                    )
                            else:
                                try:
                                    self._sessions.put_nowait(replacement)
                                except asyncio.QueueFull:
                                    dispose_replacement = True
                                    if first_error is None:
                                        first_error = RuntimeError(
                                            "session queue overflow"
                                        )
                                else:
                                    self._known[id(replacement)] = replacement

            if dispose_replacement and replacement is not None:
                try:
                    await self._destroy_session(replacement)
                except BaseException as exc:
                    if first_error is None:
                        first_error = exc
        finally:
            await self._finish_operation()

        if first_error is not None:
            raise first_error

    async def _finish_operation(self) -> None:
        async with self._state_lock:
            self._active_operations -= 1
            if self._active_operations == 0:
                self._active_zero.set()
        self._permits.release()

    async def _ensure_started(self) -> None:
        while True:
            async with self._state_lock:
                if self._closing or self._closed:
                    raise RuntimeError(f"pool {self.name!r} is closed")
                if self._started:
                    return
                task = self._start_task
                if task is None:
                    raise RuntimeError(f"pool {self.name!r} has not been started")
            await self._await_task(task)

    async def _close(self, start_task: asyncio.Task[None] | None) -> None:
        first_error: BaseException | None = None
        try:
            if start_task is not None:
                try:
                    await asyncio.shield(start_task)
                except BaseException:
                    pass

            idle = await self._drain_idle()
            for candidate in idle:
                try:
                    await self._destroy_session(candidate)
                except BaseException as exc:
                    if first_error is None:
                        first_error = exc

            await self._active_zero.wait()

            late_idle = await self._drain_idle()
            for candidate in late_idle:
                try:
                    await self._destroy_session(candidate)
                except BaseException as exc:
                    if first_error is None:
                        first_error = exc
        finally:
            async with self._state_lock:
                self._closed = True

        if first_error is not None:
            raise first_error

    async def _drain_idle(self) -> list[SessionT]:
        async with self._state_lock:
            idle = []
            while not self._sessions.empty():
                candidate = self._sessions.get_nowait()
                self._forget_known(candidate)
                idle.append(candidate)
            return idle

    def _register_known(self, session: SessionT) -> None:
        existing = self._known.get(id(session))
        if existing is not None:
            raise RuntimeError("create_session returned an existing session")
        self._known[id(session)] = session

    def _forget_known(self, session: SessionT) -> None:
        if self._known.get(id(session)) is session:
            del self._known[id(session)]

    @staticmethod
    async def _cancel_and_wait(task: asyncio.Task[None]) -> None:
        task.cancel()
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except BaseException:
                break
        if not task.cancelled():
            try:
                task.exception()
            except BaseException:
                pass

    @staticmethod
    async def _await_task(task: asyncio.Future[SessionT]) -> SessionT:
        cancelled = False
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                cancelled = True
            except BaseException:
                if cancelled:
                    break
                raise

        if cancelled:
            if not task.cancelled():
                task.exception()
            raise asyncio.CancelledError
        return task.result()
