"""Coordinate server maintenance with active imports without blocking readers."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager

from chinalaw.admin.errors import LibraryError


class MaintenanceGate:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active = 0
        self._exclusive = False

    @contextmanager
    def activity(self) -> Iterator[None]:
        with self._lock:
            if self._exclusive:
                raise LibraryError("maintenance_busy", "资料库正在恢复，请稍后重试。", status=409)
            self._active += 1
        try:
            yield
        finally:
            with self._lock:
                self._active -= 1

    @contextmanager
    def exclusive(self) -> Iterator[None]:
        with self._lock:
            if self._exclusive or self._active:
                raise LibraryError(
                    "maintenance_busy", "仍有维护操作运行，请等待处理结束后恢复。", status=409
                )
            self._exclusive = True
        try:
            yield
        finally:
            with self._lock:
                self._exclusive = False
