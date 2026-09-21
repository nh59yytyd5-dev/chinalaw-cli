"""Cross-platform process lease released by the OS if the worker crashes."""

from __future__ import annotations

import os
from pathlib import Path

from chinalaw.admin.errors import LibraryError


class ProcessLease:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.stream = None

    def acquire(self) -> None:
        if self.stream is not None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        stream = self.path.open("a+b")
        try:
            if os.name == "nt":
                import msvcrt

                if self.path.stat().st_size == 0:
                    stream.write(b"0")
                    stream.flush()
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError) as exc:
            stream.close()
            raise LibraryError(
                "library_in_use", "此资料库已有维护服务运行，请使用现有服务。", status=409
            ) from exc
        self.stream = stream

    def release(self) -> None:
        if self.stream is None:
            return
        stream, self.stream = self.stream, None
        try:
            if os.name == "nt":
                import msvcrt

                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        finally:
            stream.close()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *args):
        self.release()
