"""Owner-only export and a reviewed, conflict-checked restore workflow."""

from __future__ import annotations

import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile

from chinalaw.admin import backups
from chinalaw.admin.errors import LibraryError
from chinalaw.server.dependencies import owner

router = APIRouter(prefix="/api/v1/backups", tags=["library backups"])


class RestoreConfirmation(BaseModel):
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")


@router.post("")
def backup(request: Request):
    owner(request)
    config = request.app.state.config
    directory = Path(tempfile.mkdtemp(prefix="chinalaw-export-"))
    try:
        with request.app.state.gate.activity():
            path = directory / "library.zip"
            backups.create_backup(config.db_path, config.artifacts_dir, path)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        return FileResponse(
            path,
            media_type="application/zip",
            filename=f"chinalaw-{stamp}.zip",
            background=BackgroundTask(shutil.rmtree, directory, ignore_errors=True),
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
