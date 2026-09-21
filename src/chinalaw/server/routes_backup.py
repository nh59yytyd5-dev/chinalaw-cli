"""Owner-only export and a reviewed, conflict-checked restore workflow."""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile

from chinalaw.admin import backups
from chinalaw.admin.errors import LibraryError
from chinalaw.server.dependencies import owner

router = APIRouter(prefix="/api/v1/backups", tags=["library backups"])
EXPORT_CHUNK_BYTES = 1024 * 1024


class RestoreConfirmation(BaseModel):
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")


def exports_dir(state_dir: Path) -> Path:
    directory = state_dir / "exports"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    return directory


def clear_exports(state_dir: Path) -> None:
    """Drop export folders left behind by an earlier process."""
    directory = state_dir / "exports"
    if directory.is_dir():
        for child in directory.iterdir():
            shutil.rmtree(child, ignore_errors=True)


def _stream_then_remove(path: Path, directory: Path) -> Iterator[bytes]:
    try:
        with path.open("rb") as archive:
            yield from iter(lambda: archive.read(EXPORT_CHUNK_BYTES), b"")
    finally:
        # Runs on normal completion, client disconnect and generator close alike.
        shutil.rmtree(directory, ignore_errors=True)


@router.post("")
def backup(request: Request):
    owner(request)
    config = request.app.state.config
    directory = Path(tempfile.mkdtemp(prefix="export-", dir=exports_dir(config.state_dir)))
    try:
        with request.app.state.gate.activity():
            path = directory / "library.zip"
            backups.create_backup(config.db_path, config.artifacts_dir, path)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        return StreamingResponse(
            _stream_then_remove(path, directory),
            media_type="application/zip",
            headers={
                "Content-Length": str(path.stat().st_size),
                "Content-Disposition": f'attachment; filename="chinalaw-{stamp}.zip"',
            },
        )
    except Exception:
        shutil.rmtree(directory, ignore_errors=True)
        raise


@router.post("/restore/preview", status_code=201)
async def preview(request: Request) -> dict:
    owner(request)
    config = request.app.state.config
    form = await request.form(max_files=1, max_fields=1, max_part_size=64 * 1024)
    try:
        file = form.get("file")
        if not isinstance(file, UploadFile):
            raise LibraryError("file_required", "请选择资料库备份 ZIP 文件。")
        with request.app.state.gate.activity():
            return await run_in_threadpool(
                backups.prepare_restore, config.db_path, config.restores_dir, file.file
            )
    finally:
        await form.close()


@router.get("/restore/{identifier}")
def get_preview(identifier: str, request: Request) -> dict:
    owner(request)
    return backups.get_restore(request.app.state.config.restores_dir, identifier)


@router.post("/restore/{identifier}/commit")
def restore(identifier: str, body: RestoreConfirmation, request: Request) -> dict:
    owner(request)
    config = request.app.state.config
    with request.app.state.gate.exclusive():
        return backups.commit_restore(
            config.db_path,
            config.artifacts_dir,
            config.restores_dir,
            identifier,
            expected_fingerprint=body.fingerprint,
        )
