"""Persistent, bounded acquisition jobs. Workers prepare drafts, never confirm them."""

from __future__ import annotations

import json
import logging
import threading
import uuid
from pathlib import Path

from chinalaw import fetch
from chinalaw.admin import ingest
from chinalaw.admin.errors import LibraryError, require_kind
from chinalaw.admin.gate import MaintenanceGate
from chinalaw.admin.lease import ProcessLease
from chinalaw.admin.payloads import encode_json, utc_now
from chinalaw.db import connect, connect_readonly

LOG = logging.getLogger(__name__)
MAX_QUEUED_JOBS = 50
JOB_FIELDS = {
    "import": {"artifact_id", "kind", "metadata"},
    "fetch": {"query", "source", "prefer_id"},
}
REQUIRED = {"import": {"artifact_id", "kind"}, "fetch": {"query"}}
FINISHED_STATES = {"completed", "failed", "interrupted", "cancelled"}
# Source problems are reported as such; the generic internal-error text is
# reserved for defects. Mirrors the HTTP mapping in ``server.app``.
SOURCE_FAILURES = {
    fetch.FetchNotFoundError: (
        "source_not_found",
        "来源没有找到匹配的资料，请检查名称或换一个来源。",
    ),
    fetch.FetchAmbiguousError: (
        "source_ambiguous",
        "来源返回多个候选，请先查找候选并选择具体资料。",
    ),
    fetch.FetchError: ("source_unavailable", "来源暂时不可用，请稍后重试。"),
}


def create_job(
    db_path: Path | str, action: str, arguments: dict, *, parent_id: str | None = None
) -> dict:
    if action not in JOB_FIELDS or not isinstance(arguments, dict):
        raise LibraryError("invalid_job", "不支持此维护任务。")
    if set(arguments) - JOB_FIELDS[action]:
        raise LibraryError("invalid_job_arguments", "维护任务包含不支持的参数。")
    _check_arguments(action, arguments)
    encoded = encode_json(arguments)
    if len(encoded.encode("utf-8")) > 32 * 1024:
        raise LibraryError("job_arguments_too_large", "任务参数过长。", status=413)
    identifier = uuid.uuid4().hex
    with connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        queued = conn.execute(
            "SELECT COUNT(*) FROM library_jobs WHERE state IN ('queued', 'running')"
        ).fetchone()[0]
        if queued >= MAX_QUEUED_JOBS:
            raise LibraryError("queue_full", "维护队列已满，请等待已有任务处理。", status=429)
        conn.execute(
            "INSERT INTO library_jobs "
            "(id, action, arguments_json, state, message, parent_id, created_at) "
            "VALUES (?, ?, ?, 'queued', '等待处理', ?, ?)",
            (identifier, action, encoded, parent_id, utc_now()),
        )
    return get_job(db_path, identifier)


def _check_arguments(action: str, arguments: dict) -> None:
    invalid = LibraryError("invalid_arguments", "任务参数不完整。", status=400)
    for name in REQUIRED[action]:
        if not isinstance(arguments.get(name), str) or not arguments[name]:
            raise invalid
    if action == "import":
        require_kind(arguments["kind"])
        metadata = arguments.get("metadata")
        if metadata is not None and not isinstance(metadata, dict):
            raise invalid
    else:
        for name in ("source", "prefer_id"):
            value = arguments.get(name)
            if value is not None and not isinstance(value, str):
                raise invalid


def _job_payload(row) -> dict:
    item = dict(row)
    for name in ("arguments", "result", "error"):
        raw = item.pop(name + "_json")
        item[name] = json.loads(raw) if raw else None
    item["cancel_requested"] = bool(item["cancel_requested"])
    return item


def get_job(db_path: Path | str, identifier: str) -> dict:
    with connect_readonly(db_path) as conn:
        row = conn.execute("SELECT * FROM library_jobs WHERE id = ?", (identifier,)).fetchone()
    if row is None:
        raise LibraryError("job_not_found", "维护任务不存在。", status=404)
    return _job_payload(row)


def list_jobs(db_path: Path | str, *, limit: int = 100) -> list[dict]:
    if not 1 <= limit <= 100:
        raise LibraryError("invalid_limit", "数量应在 1–100 之间。")
    with connect_readonly(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM library_jobs ORDER BY created_at DESC, id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [_job_payload(row) for row in rows]


def cancel_job(db_path: Path | str, identifier: str) -> dict:
    with connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM library_jobs WHERE id = ?", (identifier,)).fetchone()
        if row is None:
            raise LibraryError("job_not_found", "维护任务不存在。", status=404)
        if row["state"] in FINISHED_STATES:
            raise LibraryError("job_finished", "任务已结束，不能取消。", status=409)
        if row["state"] == "running":
            conn.execute(
                "UPDATE library_jobs SET cancel_requested = 1, "
                "message = '正在取消，等待当前处理结束' "
                "WHERE id = ?",
                (identifier,),
            )
        else:
            if row["draft_id"]:
                conn.execute(
                    "UPDATE library_drafts SET status = 'cancelled' "
                    "WHERE id = ? AND status = 'ready'",
                    (row["draft_id"],),
                )
            conn.execute(
                "UPDATE library_jobs SET state = 'cancelled', cancel_requested = 1, "
                "message = '已取消', finished_at = ? WHERE id = ?",
                (utc_now(), identifier),
            )
    return get_job(db_path, identifier)


def retry_job(db_path: Path | str, identifier: str) -> dict:
    original = get_job(db_path, identifier)
    if original["state"] not in {"failed", "cancelled", "interrupted"}:
        raise LibraryError("job_not_retryable", "仅失败、中断或已取消的任务可以重试。", status=409)
    return create_job(db_path, original["action"], original["arguments"], parent_id=identifier)


class JobWorker:
    def __init__(
        self,
        db_path: Path | str,
        artifacts_root: Path | str,
        *,
        gate: MaintenanceGate | None = None,
    ):
        self.db_path = Path(db_path)
        self.artifacts_root = Path(artifacts_root)
        self.lease = ProcessLease(self.db_path.with_name(self.db_path.name + ".worker.lock"))
        self.wake = threading.Event()
        self.stopping = threading.Event()
        self.thread: threading.Thread | None = None
        self.gate = gate or MaintenanceGate()

    def start(self) -> None:
        if self.thread is not None:
            return
        self.lease.acquire()
        try:
            with connect(self.db_path) as conn:
                conn.execute(
                    "UPDATE library_drafts SET status = 'cancelled' WHERE status = 'ready' "
                    "AND id IN (SELECT draft_id FROM library_jobs "
                    "WHERE state IN ('running', 'queued'))"
                )
                conn.execute(
                    "UPDATE library_jobs SET state = 'interrupted', "
                    "message = '服务曾停止，请手动重试', "
                    "finished_at = ? WHERE state IN ('running', 'queued')",
                    (utc_now(),),
                )
            self.thread = threading.Thread(
                target=self._loop, name="chinalaw-library-worker", daemon=True
            )
            self.thread.start()
        except Exception:
            self.lease.release()
            raise

    def notify(self) -> None:
        self.wake.set()

    def stop(self) -> None:
        self.stopping.set()
        self.wake.set()
        if self.thread is not None:
            self.thread.join(timeout=5)

    def _loop(self) -> None:
        try:
            while not self.stopping.is_set():
                try:
                    busy = self.run_once()
                except Exception:
                    # A locked database or transient I/O error must not end the
                    # worker; the claimed job (if any) stays recoverable.
                    LOG.exception("Library worker iteration failed; retrying shortly")
                    self.wake.wait(timeout=5)
                    self.wake.clear()
                    continue
                if not busy:
                    self.wake.wait(timeout=1)
                    self.wake.clear()
        finally:
            self.lease.release()

    def _claim(self) -> dict | None:
        with connect(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM library_jobs WHERE state = 'queued' ORDER BY created_at, id LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            phase = "fetching" if row["action"] == "fetch" else "parsing"
            message = "正在获取来源文本" if phase == "fetching" else "正在解析文件并准备完整预览"
            conn.execute(
                "UPDATE library_jobs SET state = 'running', phase = ?, message = ?, started_at = ? "
                "WHERE id = ?",
                (phase, message, utc_now(), row["id"]),
            )
        return _job_payload(row)

    def run_once(self) -> bool:
        """Execute one queued job; also useful for non-threaded integration tests."""
        try:
            with self.gate.activity():
                return self._run_once()
        except LibraryError as exc:
            if exc.code != "maintenance_busy":
                raise
            return False

    def _run_once(self) -> bool:
        job = self._claim()
        if job is None:
            return False
        try:
            if job["action"] == "import":
                result = ingest.import_artifact(
                    self.db_path, self.artifacts_root, job_id=job["id"], **job["arguments"]
                )
            else:
                result = ingest.fetch_draft(self.db_path, job_id=job["id"], **job["arguments"])
            self._finish(job, result)
        except Exception as exc:
            try:
                self._fail(job, exc)
            except Exception:
                LOG.exception("Could not record failure of library job %s", job["id"])
        return True

    def _finish(self, job: dict, draft: dict) -> None:
        with connect(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT cancel_requested FROM library_jobs WHERE id = ?", (job["id"],)
            ).fetchone()
            current = conn.execute(
                "SELECT status FROM library_drafts WHERE id = ?", (draft["id"],)
            ).fetchone()[0]
            if current != "committed" and (row["cancel_requested"] or self.stopping.is_set()):
                state = "interrupted" if self.stopping.is_set() else "cancelled"
                message = (
                    "处理已中断，当前正文未改变"
                    if self.stopping.is_set()
                    else "已取消，当前正文未改变"
                )
                conn.execute(
                    "UPDATE library_drafts SET status = 'cancelled' "
                    "WHERE id = ? AND status = 'ready'",
                    (draft["id"],),
                )
            else:
                state = "completed" if current == "committed" else "awaiting_confirmation"
                if current == "cancelled":
                    state = "cancelled"
                message = {"completed": "已确认入库", "cancelled": "已取消入库"}.get(
                    state, "预览已就绪，等待人工确认"
                )
            summary = {
                "draft_id": draft["id"],
                "target_id": draft["target_id"],
                "document_kind": draft["document_kind"],
                "title": draft["document"].get("title") or draft["document"].get("name"),
            }
            conn.execute(
                "UPDATE library_jobs SET state = ?, phase = 'prepared', message = ?, draft_id = ?, "
                "result_json = ?, finished_at = ? WHERE id = ?",
                (state, message, draft["id"], encode_json(summary), utc_now(), job["id"]),
            )

    def _fail(self, job: dict, exc: Exception) -> None:
        state = "interrupted" if self.stopping.is_set() else "failed"
        if isinstance(exc, LibraryError):
            error = {"code": exc.code, "message": str(exc)[:1000]}
        elif isinstance(exc, fetch.FetchError):
            code, message = SOURCE_FAILURES.get(type(exc), SOURCE_FAILURES[fetch.FetchError])
            error = {
                "code": code,
                "message": message,
                "detail": f"{type(exc).__name__}: {str(exc)[:500]}",
            }
        else:
            # Never surface raw Python exception text as a user-facing message.
            error = {
                "code": "internal_error",
                "message": "处理时发生内部错误，请重试或检查文件。",
                "detail": f"{type(exc).__name__}: {str(exc)[:500]}",
            }
        with connect(self.db_path) as conn:
            conn.execute(
                "UPDATE library_jobs SET state = ?, phase = 'failed', message = ?, error_json = ?, "
                "finished_at = ? WHERE id = ?",
                (state, "处理失败，当前正文未改变", encode_json(error), utc_now(), job["id"]),
            )
