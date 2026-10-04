"""Bound heavy searches without occupying threads while they wait for admission."""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

import anyio

from chinalaw.admin.errors import LibraryError

T = TypeVar("T")


class SearchBusy(LibraryError):
    def __init__(self) -> None:
        super().__init__(
            "search_busy", "检索队列已满，请稍后重试。", status=429,
            details={"retry_after": 1},
        )


def reject_search():
    raise SearchBusy()


class SearchExecutor:
    """One event-loop-owned budget shared by HTTP and MCP in a single worker.

    AnyIO retains a running thread's permit until it finishes, even when its
    caller is cancelled. Waiting callers consume no worker threads. A separate
    limiter leaves the default pool available to authentication and light tools.
    """

    def __init__(self, concurrency: int, queue: int) -> None:
        self.capacity = concurrency + queue
        self.outstanding = 0
        self._limiter = anyio.CapacityLimiter(concurrency)

    async def run(self, call: Callable[[], T]) -> T:
        if self.outstanding >= self.capacity:
            raise SearchBusy()
        self.outstanding += 1
        try:
            return await anyio.to_thread.run_sync(call, limiter=self._limiter)
        finally:
            self.outstanding -= 1

    async def run_logged(self, call, logged):
        """Audit admission failures through the same adapter as executed calls."""
        try:
            return await self.run(lambda: logged(call))
        except SearchBusy:
            return await anyio.to_thread.run_sync(lambda: logged(reject_search))
