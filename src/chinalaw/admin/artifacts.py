"""Bounded, content-addressed source files; no user supplied server paths."""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from io import BytesIO
from pathlib import Path
from typing import BinaryIO

from chinalaw.admin.errors import LibraryError
from chinalaw.admin.payloads import utc_now
from chinalaw.db import connect, connect_readonly

MAX_UPLOAD_BYTES = 20 * 1024 * 1024
MEDIA_TYPES = {
    ".json": "application/json",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pdf": "application/pdf",
}
_HASH = re.compile(r"[0-9a-f]{64}")


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def blob_path(root: Path | str, digest: str) -> Path:
    if _HASH.fullmatch(digest) is None:
        raise LibraryError("invalid_artifact_hash", "来源附件校验值不正确。")
    root = Path(root).resolve()
    result = root / digest[:2] / digest
    if not result.resolve().is_relative_to(root):
        raise LibraryError("invalid_artifact_path", "来源附件路径不正确。")
    return result


def save_upload(
    db_path: Path | str,
    root: Path | str,
    filename: str,
    stream: BinaryIO,
) -> dict:
    name = filename.replace("\\", "/").rsplit("/", 1)[-1].strip()
    if not name or len(name) > 200 or any(ord(char) < 32 for char in name):
        raise LibraryError("invalid_filename", "文件名不正确。")
    suffix = Path(name).suffix.lower()
    if suffix not in MEDIA_TYPES:
        raise LibraryError(
            "unsupported_file", "支持 JSON、TXT、Markdown、DOCX 和文本型 PDF。", status=415
        )
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=root, prefix="upload-", delete=False) as output:
            temporary = Path(output.name)
            digest, size = hashlib.sha256(), 0
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise LibraryError("upload_too_large", "单个来源文件最多 20 MiB。", status=413)
                digest.update(chunk)
                output.write(chunk)
            if not size:
                raise LibraryError("empty_file", "文件为空。")
            output.flush()
            os.fsync(output.fileno())
        sha256 = digest.hexdigest()
        destination = blob_path(root, sha256)
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.replace(temporary, destination)
        temporary = None
        identifier = uuid.uuid4().hex
        with connect(db_path) as conn:
            conn.execute(
                "INSERT INTO library_artifacts(id, filename, media_type, size, sha256, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (identifier, name, MEDIA_TYPES[suffix], size, sha256, utc_now()),
            )
        return {
            "id": identifier,
            "filename": name,
            "media_type": MEDIA_TYPES[suffix],
            "size": size,
            "sha256": sha256,
        }
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def save_text(db_path: Path | str, root: Path | str, filename: str, text: str) -> dict:
    return save_upload(db_path, root, filename, BytesIO(text.encode("utf-8")))


def get_artifact(db_path: Path | str, root: Path | str, identifier: str) -> tuple[dict, Path]:
    with connect_readonly(db_path) as conn:
        row = conn.execute("SELECT * FROM library_artifacts WHERE id = ?", (identifier,)).fetchone()
    if row is None:
        raise LibraryError("artifact_not_found", "来源附件不存在。", status=404)
    metadata = dict(row)
    path = blob_path(root, metadata["sha256"])
    if not path.is_file() or path.stat().st_size != metadata["size"]:
        raise LibraryError("artifact_missing", "来源原件缺失或大小不符，请重新上传。", status=409)
    if file_digest(path) != metadata["sha256"]:
        raise LibraryError("artifact_corrupted", "来源原件校验失败，请重新上传。", status=409)
    return metadata, path


@contextmanager
def source_file(
    db_path: Path | str, root: Path | str, identifier: str
) -> Iterator[tuple[dict, Path]]:
    """Existing readers use filename extensions; provide an ephemeral safe copy."""
    metadata, blob = get_artifact(db_path, root, identifier)
    with tempfile.TemporaryDirectory(prefix="chinalaw-source-") as directory:
        path = Path(directory) / ("source" + Path(metadata["filename"]).suffix.lower())
        with blob.open("rb") as source, path.open("wb") as target:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                target.write(chunk)
        yield metadata, path


def artifact_manifest(db_path: Path | str) -> list[dict]:
    with connect_readonly(db_path) as conn:
        return [dict(row) for row in conn.execute("SELECT * FROM library_artifacts ORDER BY id")]
