"""Authenticated human maintenance workflows."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile

from chinalaw.admin import artifacts, drafts, ingest, jobs
from chinalaw.admin.errors import LibraryError
from chinalaw.server.dependencies import maintenance_activity, owner

router = APIRouter(
    prefix="/api/v1", tags=["human maintenance"], dependencies=[Depends(maintenance_activity)]
)


class NewDraft(BaseModel):
    kind: Literal["law", "norm"]
    payload: dict[str, Any]


class Confirmation(BaseModel):
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")


class NewJob(BaseModel):
    action: Literal["import", "fetch"]
    arguments: dict[str, Any]


class CandidateQuery(BaseModel):
    query: str = Field(min_length=1, max_length=200)
    source: str = Field(default="flk_npc", min_length=1, max_length=80)


class Review(Confirmation):
    kind: Literal["law", "norm"]
    id: str = Field(min_length=1, max_length=500)
    note: str = Field(default="", max_length=2000)


class RestoreOperation(BaseModel):
    side: Literal["before", "after"] = "after"


@router.post("/uploads", status_code=201)
async def upload(request: Request) -> dict:
    owner(request)
    form = await request.form(max_files=1, max_fields=2, max_part_size=64 * 1024)
    try:
        file = form.get("file")
        if not isinstance(file, UploadFile) or not file.filename:
            raise LibraryError("file_required", "请选择来源文件。")
        config = request.app.state.config
        return await run_in_threadpool(
            artifacts.save_upload,
            config.db_path,
            config.artifacts_dir,
            file.filename,
            file.file,
        )
    finally:
        await form.close()


@router.get("/artifacts/{identifier}")
def artifact(identifier: str, request: Request):
    owner(request)
    config = request.app.state.config
    metadata, path = artifacts.get_artifact(config.db_path, config.artifacts_dir, identifier)
    return FileResponse(path, media_type=metadata["media_type"], filename=metadata["filename"])


@router.get("/artifacts/{identifier}/text")
def artifact_text(identifier: str, request: Request) -> dict:
    owner(request)
    config = request.app.state.config
    metadata, path = artifacts.get_artifact(config.db_path, config.artifacts_dir, identifier)
    if metadata["media_type"] not in {"application/json", "text/plain", "text/markdown"}:
        raise LibraryError("binary_artifact", "此文件请下载原件查看。", status=415)
    try:
        text = path.read_text(encoding="utf-8-sig")
    except UnicodeError as exc:
        raise LibraryError("source_encoding", "原件不是 UTF-8 文本，请下载后查看。") from exc
    return {"text": text, "filename": metadata["filename"]}


@router.post("/sources/search")
def source_candidates(body: CandidateQuery, request: Request) -> dict:
    owner(request)
    return ingest.fetch_candidates(request.app.state.config.db_path, body.query, source=body.source)


def _worker(request: Request):
    value = request.app.state.worker
    if value is None or value.thread is None or not value.thread.is_alive():
        raise LibraryError("worker_unavailable", "维护执行器未运行，请重启服务后重试。", status=503)
    return value


@router.post("/jobs", status_code=202)
def new_job(body: NewJob, request: Request) -> dict:
    owner(request)
    worker = _worker(request)
    result = jobs.create_job(request.app.state.config.db_path, body.action, body.arguments)
    worker.notify()
    return result


@router.get("/jobs")
def job_list(request: Request) -> dict:
    owner(request)
    return {"items": jobs.list_jobs(request.app.state.config.db_path)}


@router.get("/jobs/{identifier}")
def job(identifier: str, request: Request) -> dict:
    owner(request)
    return jobs.get_job(request.app.state.config.db_path, identifier)


@router.post("/jobs/{identifier}/cancel")
def cancel_job(identifier: str, request: Request) -> dict:
    owner(request)
    return jobs.cancel_job(request.app.state.config.db_path, identifier)


@router.post("/jobs/{identifier}/retry", status_code=202)
def retry_job(identifier: str, request: Request) -> dict:
    owner(request)
    worker = _worker(request)
    result = jobs.retry_job(request.app.state.config.db_path, identifier)
    worker.notify()
    return result


@router.get("/drafts")
def draft_list(request: Request) -> dict:
    owner(request)
    return {"items": drafts.list_drafts(request.app.state.config.db_path)}


@router.post("/drafts", status_code=201)
def new_draft(body: NewDraft, request: Request) -> dict:
    owner(request)
    return drafts.create_draft(
        request.app.state.config.db_path,
        body.kind,
        body.payload,
        origin={"type": "manual_json"},
    )


@router.get("/drafts/{identifier}")
def draft(identifier: str, request: Request) -> dict:
    owner(request)
    return drafts.get_draft(request.app.state.config.db_path, identifier)


@router.post("/drafts/{identifier}/commit")
def commit_draft(body: Confirmation, identifier: str, request: Request) -> dict:
    actor = owner(request)
    return drafts.commit_draft(
        request.app.state.config.db_path,
        identifier,
        expected_fingerprint=body.fingerprint,
        actor=actor.subject,
        artifacts_root=request.app.state.config.artifacts_dir,
    )


@router.post("/drafts/{identifier}/cancel")
def cancel_draft(identifier: str, request: Request) -> dict:
    owner(request)
    return drafts.cancel_draft(request.app.state.config.db_path, identifier)


@router.post("/reviews")
def review(body: Review, request: Request) -> dict:
    actor = owner(request)
    return drafts.mark_reviewed(
        request.app.state.config.db_path,
        body.kind,
        body.id,
        expected_fingerprint=body.fingerprint,
        note=body.note,
        actor=actor.subject,
    )


@router.get("/operations")
def operations(request: Request, kind: str, id: str) -> dict:
    owner(request)
    return {"items": drafts.list_operations(request.app.state.config.db_path, kind, id)}


@router.post("/operations/{identifier}/restore", status_code=201)
def restore_operation(body: RestoreOperation, identifier: str, request: Request) -> dict:
    owner(request)
    return drafts.restore_operation(request.app.state.config.db_path, identifier, side=body.side)


class RestoreRevision(BaseModel):
    kind: Literal["law", "norm"]
    id: str = Field(min_length=1, max_length=500)
    revision: str = Field(min_length=1, max_length=500)


@router.post("/revisions/restore", status_code=201)
def restore_revision(body: RestoreRevision, request: Request) -> dict:
    return drafts.restore_revision(
        request.app.state.config.db_path, body.kind, body.id, body.revision
    )
