"""Shared resource limits for untrusted legal-source inputs.

The adapters intentionally use only the standard library.  These helpers keep
that property while ensuring network bodies, compressed documents and helper
process output have explicit upper bounds.
"""

from __future__ import annotations

import subprocess
import tempfile
import zipfile
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import BinaryIO

MIB = 1024 * 1024
MAX_TEXT_BYTES = 8 * MIB
MAX_BINARY_BYTES = 64 * MIB
MAX_ALIAS_RESPONSE_BYTES = 1 * MIB
MAX_LOCAL_SOURCE_BYTES = 64 * MIB
MAX_ZIP_ENTRIES = 2_048
MAX_ZIP_ENTRY_BYTES = 32 * MIB
MAX_ZIP_TOTAL_BYTES = 128 * MIB
MAX_ZIP_COMPRESSION_RATIO = 200
SUBPROCESS_TIMEOUT_SECONDS = 45
MAX_SUBPROCESS_OUTPUT_BYTES = 32 * MIB


class ResourceLimitError(ValueError):
    """An input or helper process exceeded an explicit safety limit."""


def _content_length(headers: Mapping[str, object] | object | None) -> int | None:
    if headers is None:
        return None
    getter = getattr(headers, "get", None)
    if not callable(getter):
        return None
    raw = getter("Content-Length")
    if raw is None:
        raw = getter("content-length")
    if raw in (None, ""):
        return None
    try:
        length = int(str(raw).strip())
    except (TypeError, ValueError) as exc:
        raise ResourceLimitError("invalid Content-Length header") from exc
    if length < 0:
        raise ResourceLimitError("negative Content-Length header")
    return length


def read_limited(
    stream: BinaryIO | object,
    *,
    max_bytes: int,
    headers: Mapping[str, object] | object | None = None,
    label: str = "response body",
    chunk_size: int = 64 * 1024,
) -> bytes:
    """Read a byte stream while enforcing header and streaming limits."""

    if max_bytes <= 0:
        raise ValueError("max_bytes must be positive")
    declared = _content_length(headers)
    if declared is not None and declared > max_bytes:
        raise ResourceLimitError(
            f"{label} exceeds limit: Content-Length {declared} > {max_bytes}"
        )

    reader = getattr(stream, "read", None)
    if not callable(reader):
        raise TypeError(f"{label} is not readable")
    chunks: list[bytes] = []
    total = 0
    while True:
        try:
            chunk = reader(min(chunk_size, max_bytes - total + 1))
        except TypeError:
            # A few tiny test doubles expose ``read()`` without a size
            # parameter.  The post-read limit still protects correctness.
            chunk = reader()
        if not chunk:
            break
        if not isinstance(chunk, (bytes, bytearray, memoryview)):
            raise TypeError(f"{label} reader returned non-bytes data")
        data = bytes(chunk)
        total += len(data)
        if total > max_bytes:
            raise ResourceLimitError(
                f"{label} exceeds streaming limit: {total} > {max_bytes}"
            )
        chunks.append(data)
    return b"".join(chunks)


def ensure_file_size(
    path: Path | str,
    *,
    max_bytes: int = MAX_LOCAL_SOURCE_BYTES,
    label: str = "source file",
) -> int:
    source = Path(path)
    size = source.stat().st_size
    if size > max_bytes:
        raise ResourceLimitError(f"{label} exceeds limit: {size} > {max_bytes}")
    return size


def validate_zip_archive(
    archive: zipfile.ZipFile,
    *,
    max_entries: int = MAX_ZIP_ENTRIES,
    max_entry_bytes: int = MAX_ZIP_ENTRY_BYTES,
    max_total_bytes: int = MAX_ZIP_TOTAL_BYTES,
    max_ratio: int = MAX_ZIP_COMPRESSION_RATIO,
) -> None:
    """Reject oversized or suspiciously compressed ZIP/DOCX archives."""

    infos = archive.infolist()
    if len(infos) > max_entries:
        raise ResourceLimitError(
            f"zip archive has too many entries: {len(infos)} > {max_entries}"
        )
    total = 0
    for info in infos:
        if info.file_size < 0 or info.compress_size < 0:
            raise ResourceLimitError("zip archive contains an invalid entry size")
        if info.file_size > max_entry_bytes:
            raise ResourceLimitError(
                f"zip entry {info.filename!r} exceeds limit: "
                f"{info.file_size} > {max_entry_bytes}"
            )
        total += info.file_size
        if total > max_total_bytes:
            raise ResourceLimitError(
                f"zip archive expands beyond limit: {total} > {max_total_bytes}"
            )
        if info.file_size and info.compress_size == 0:
            raise ResourceLimitError(
                f"zip entry {info.filename!r} has an invalid compression ratio"
            )
        if info.compress_size and info.file_size / info.compress_size > max_ratio:
            raise ResourceLimitError(
                f"zip entry {info.filename!r} exceeds compression ratio {max_ratio}:1"
            )


def read_zip_member_limited(
    archive: zipfile.ZipFile,
    member: str,
    *,
    max_bytes: int = MAX_ZIP_ENTRY_BYTES,
) -> bytes:
    validate_zip_archive(archive, max_entry_bytes=max_bytes)
    try:
        info = archive.getinfo(member)
    except KeyError:
        raise
    if info.file_size > max_bytes:
        raise ResourceLimitError(
            f"zip entry {member!r} exceeds limit: {info.file_size} > {max_bytes}"
        )
    with archive.open(info) as stream:
        return read_limited(
            stream,
            max_bytes=max_bytes,
            label=f"zip entry {member!r}",
        )


Runner = Callable[..., subprocess.CompletedProcess]


def run_limited(
    args: Iterable[str],
    *,
    timeout: float = SUBPROCESS_TIMEOUT_SECONDS,
    max_output_bytes: int = MAX_SUBPROCESS_OUTPUT_BYTES,
    runner: Runner = subprocess.run,
) -> subprocess.CompletedProcess[str]:
    """Run a helper with a timeout and disk-backed bounded output capture."""

    argv = [str(item) for item in args]
    if timeout <= 0:
        raise ValueError("subprocess timeout must be positive")
    with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
        try:
            completed = runner(
                argv,
                stdout=stdout_file,
                stderr=stderr_file,
                check=False,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise ResourceLimitError(
                f"external process timed out after {timeout:g}s: {argv[0]}"
            ) from exc

        supplied_stdout = getattr(completed, "stdout", None)
        supplied_stderr = getattr(completed, "stderr", None)
        if supplied_stdout is None:
            stdout_file.seek(0, 2)
            stdout_size = stdout_file.tell()
            stdout_file.seek(0)
            stdout_bytes = stdout_file.read()
        else:
            stdout_bytes = (
                supplied_stdout.encode("utf-8")
                if isinstance(supplied_stdout, str)
                else bytes(supplied_stdout)
            )
            stdout_size = len(stdout_bytes)
        if supplied_stderr is None:
            stderr_file.seek(0, 2)
            stderr_size = stderr_file.tell()
            stderr_file.seek(0)
            stderr_bytes = stderr_file.read()
        else:
            stderr_bytes = (
                supplied_stderr.encode("utf-8")
                if isinstance(supplied_stderr, str)
                else bytes(supplied_stderr)
            )
            stderr_size = len(stderr_bytes)

    if stdout_size > max_output_bytes:
        raise ResourceLimitError(
            f"external process stdout exceeds limit: {stdout_size} > {max_output_bytes}"
        )
    if stderr_size > max_output_bytes:
        raise ResourceLimitError(
            f"external process stderr exceeds limit: {stderr_size} > {max_output_bytes}"
        )
    return subprocess.CompletedProcess(
        args=argv,
        returncode=completed.returncode,
        stdout=stdout_bytes.decode("utf-8", errors="replace"),
        stderr=stderr_bytes.decode("utf-8", errors="replace"),
    )
