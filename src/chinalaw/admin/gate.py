"""Coordinate server maintenance with active imports without blocking readers."""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager

from chinalaw.admin.errors import LibraryError

EXCLUSIVE_WAIT_SECONDS = 10.0


class MaintenanceGate:
    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._active = 0
        self._exclusive = False

    @contextmanager
    def activity(self) -> Iterator[None]:
        with self._condition:
            if self._exclusive:
                raise LibraryError("maintenance_busy", "资料库正在恢复，请稍后重试。", status=409)
            self._active += 1
        try:
            yield
        finally:
            with self._condition:
                self._active -= 1
                self._condition.notify_all()

    @contextmanager
    def exclusive(self, timeout: float = EXCLUSIVE_WAIT_SECONDS) -> Iterator[None]:
        """Refuse new activity, then wait briefly for running activity to finish."""
        with self._condition:
            if self._exclusive:
                raise LibraryError("maintenance_busy", "资料库正在恢复，请稍后重试。", status=409)
            self._exclusive = True
            deadline = time.monotonic() + timeout
            while self._active:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._condition.wait(remaining)
            if self._active:
                self._exclusive = False
                self._condition.notify_all()
                raise LibraryError(
                    "maintenance_busy", "仍有维护操作运行，请等待处理结束后恢复。", status=409
                )
        try:
            yield
        finally:
            with self._condition:
                self._exclusive = False
                self._condition.notify_all()
