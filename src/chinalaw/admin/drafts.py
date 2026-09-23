"""Freeze, compare and atomically commit exactly the document a human reviewed."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from chinalaw import loader, normsources
from chinalaw.admin import artifacts
from chinalaw.admin.categories import (
    check_category_conflicts,
    freeze_categories,
    has_category_conflict,
)
from chinalaw.admin.errors import LibraryError, require_kind
from chinalaw.admin.payloads import (
    REVISION_FIELDS,
    compare_payloads,
    content_fingerprint,
    current_payload,
    encode_json,
    fingerprint,
    portable_payload,
    prepare_payload,
    utc_now,
)
from chinalaw.db import connect, connect_readonly, migrate

MAX_DRAFT_BYTES = 32 * 1024 * 1024
# A preview can be confirmed for one day. Keep it a further week so a closed tab
# or a repeated confirmation still finds it, then drop it: frozen payloads are the
# largest rows in the library that are not legal content, and every backup would
# otherwise carry all of them forever. Confirmed content lives on in
# ``library_operations``.
DRAFT_TTL = timedelta(days=1)
DRAFT_RETENTION = timedelta(days=7)


def create_draft(
    db_path: Path | str,
    kind: str,
    payload: dict,
    *,
    origin: dict | None = None,
    warnings: list[dict] | None = None,
    job_id: str | None = None,
) -> dict:
    prepared = prepare_payload(kind, payload)
    identifier = prepared["id"]
    draft_id = uuid.uuid4().hex
    now = datetime.now(timezone.utc)
    origin = origin or {}
    sweep_drafts(db_path, now=now)
    with connect(db_path) as conn:
        migrate(conn)
        conn.execute("BEGIN IMMEDIATE")
        _check_artifacts(conn, origin)
        before = current_payload(conn, kind, identifier)
        if kind == "law":
            freeze_categories(conn, prepared, before)
        else:
            check_name_conflict(conn, prepared)
        serialized = encode_json(prepared)
        if len(serialized.encode("utf-8")) > MAX_DRAFT_BYTES:
            raise LibraryError("document_too_large", "资料超过单次导入的大小限制。", status=413)
        digest = fingerprint(prepared)
        conn.execute(
            "INSERT INTO library_drafts "
            "(id, kind, target_id, payload_json, before_json, fingerprint, base_fingerprint, "
            "origin_json, warnings_json, created_at, expires_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                draft_id,
                kind,
                identifier,
                serialized,
                encode_json(portable_payload(before, kind)) if before is not None else None,
                digest,
                content_fingerprint(before, kind),
                encode_json(origin),
                encode_json(warnings or []),
                now.isoformat(),
                (now + DRAFT_TTL).isoformat(),
            ),
        )
        if job_id is not None:
            updated = conn.execute(
                "UPDATE library_jobs SET draft_id = ? WHERE id = ? AND state = 'running'",
                (draft_id, job_id),
            ).rowcount
            if updated != 1:
                raise LibraryError("job_not_running", "维护任务已停止，不能发布预览。", status=409)
    return get_draft(db_path, draft_id)


def _check_artifacts(conn: sqlite3.Connection, origin: dict) -> None:
    ids = origin.get("artifact_ids", [])
    if not isinstance(ids, list) or len(ids) > 10:
        raise LibraryError("invalid_artifacts", "来源附件清单不正确。")
    for identifier in ids:
        if (
            not isinstance(identifier, str)
            or conn.execute(
                "SELECT 1 FROM library_artifacts WHERE id = ?", (identifier,)
            ).fetchone()
            is None
        ):
            raise LibraryError("artifact_not_found", "来源附件不存在。", status=404)


def norm_name_taken_by(conn: sqlite3.Connection, payload: dict) -> str | None:
    """Id of another private norm already using this payload's name.

    ``norm_sources.name`` is unique so the reader can address a norm by name.
    Writing such a payload would fail the constraint at the last step; report
    it while the reviewer can still choose the existing id or another name.
    """
    row = conn.execute(
        "SELECT id FROM norm_sources WHERE name = ? AND id != ?",
        (payload["name"], payload["id"]),
    ).fetchone()
    return row["id"] if row is not None else None


def check_name_conflict(conn: sqlite3.Connection, payload: dict) -> None:
    other = norm_name_taken_by(conn, payload)
    if other is not None:
        raise LibraryError(
            "name_conflict",
            f"资料名称已被另一份私域规范（{other}）使用。"
            "要更新那份规范请使用它的标识，否则请换一个名称。",
            status=409,
        )


def _stale(expires_at: str, cutoff: datetime) -> bool:
    try:
        return datetime.fromisoformat(expires_at) <= cutoff
    except (TypeError, ValueError):
        return True


def sweep_drafts(db_path: Path | str, *, now: datetime | None = None) -> int:
    """Delete previews whose confirmation window closed more than a week ago.

    Applies to every status: confirmed content is already recorded in
    ``library_operations`` and cancelled or expired previews cannot be used.
    Jobs keep their result summary but no longer link to a removed preview.
    Returns how many previews were removed.
    """
    cutoff = (now or datetime.now(timezone.utc)) - DRAFT_RETENTION
    with connect(db_path) as conn:
        migrate(conn)
        conn.execute("BEGIN IMMEDIATE")
        stale = [
            row["id"]
            for row in conn.execute("SELECT id, expires_at FROM library_drafts")
            if _stale(row["expires_at"], cutoff)
        ]
        for start in range(0, len(stale), 500):
            batch = stale[start : start + 500]
            marks = ", ".join("?" for _ in batch)
            conn.execute(
                f"UPDATE library_jobs SET draft_id = NULL WHERE draft_id IN ({marks})", batch
            )
            conn.execute(f"DELETE FROM library_drafts WHERE id IN ({marks})", batch)
    return len(stale)


def _row(conn: sqlite3.Connection, draft_id: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM library_drafts WHERE id = ?", (draft_id,)).fetchone()
    if row is None:
        raise LibraryError("draft_not_found", "待确认资料不存在。", status=404)
    return row


def get_draft(db_path: Path | str, draft_id: str) -> dict:
    with connect_readonly(db_path) as conn:
        conn.execute("BEGIN")
        row = _row(conn, draft_id)
        current = current_payload(conn, row["kind"], row["target_id"])
        payload = json.loads(row["payload_json"])
        # Shared state changed after the preview was frozen (taxonomy for laws,
        # another norm taking the name): commit would refuse it, so tell the
        # reviewer now rather than at the last click.
        blocked = (
            has_category_conflict(conn, payload)
            if row["kind"] == "law"
            else norm_name_taken_by(conn, payload) is not None
        )
    before = json.loads(row["before_json"]) if row["before_json"] else None
    return {
        "kind": "library_draft",
        "id": row["id"],
        "document_kind": row["kind"],
        "target_id": row["target_id"],
        "status": row["status"],
        "document": payload,
        "before": before,
        "fingerprint": row["fingerprint"],
        "base_fingerprint": row["base_fingerprint"],
        "origin": json.loads(row["origin_json"]),
        "warnings": json.loads(row["warnings_json"]),
        "diff": compare_payloads(before, payload, row["kind"]),
        "conflict": content_fingerprint(current, row["kind"]) != row["base_fingerprint"]
        or blocked,
        "expired": datetime.fromisoformat(row["expires_at"]) <= datetime.now(timezone.utc),
        "created_at": row["created_at"],
        "expires_at": row["expires_at"],
        "operation_id": row["operation_id"],
    }


def list_drafts(db_path: Path | str, *, limit: int = 100) -> list[dict]:
    if not 1 <= limit <= 100:
        raise LibraryError("invalid_limit", "数量应在 1–100 之间。")
    with connect_readonly(db_path) as conn:
        rows = conn.execute(
            "SELECT id, kind, target_id, status, created_at, expires_at, operation_id, "
            "payload_json FROM library_drafts WHERE status = 'ready' "
            "ORDER BY created_at DESC, id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        payload = json.loads(item.pop("payload_json"))
        item["title"] = payload.get("title") or payload.get("name")
        result.append(item)
    return result


def commit_draft(
    db_path: Path | str,
    draft_id: str,
    *,
    expected_fingerprint: str,
    actor: str = "owner",
    artifacts_root: Path | str | None = None,
) -> dict:
    """Optimistic concurrency, idempotency and content verification in one transaction."""
    with connect(db_path) as conn:
        migrate(conn)
        conn.execute("BEGIN IMMEDIATE")
        row = _row(conn, draft_id)
        if row["fingerprint"] != expected_fingerprint:
            raise LibraryError("draft_changed", "确认内容不一致，请重新打开预览。", status=409)
        if row["status"] == "committed":
            return _commit_result(row, row["operation_id"], repeated=True)
        _check_ready(row)
        cancelled = conn.execute(
            "SELECT 1 FROM library_jobs WHERE draft_id = ? "
            "AND (cancel_requested = 1 OR state IN ('cancelled', 'interrupted'))",
            (draft_id,),
        ).fetchone()
        if cancelled:
            raise LibraryError(
                "job_cancelled", "维护任务已取消或中断，请重新准备资料。", status=409
            )
        origin = json.loads(row["origin_json"])
        root = artifacts_root or Path(db_path).with_name(Path(db_path).name + ".assets")
        for artifact_id in origin.get("artifact_ids", []):
            artifacts.get_artifact(db_path, root, artifact_id)
        kind, target = row["kind"], row["target_id"]
        before = current_payload(conn, kind, target)
        if content_fingerprint(before, kind) != row["base_fingerprint"]:
            raise LibraryError(
                "content_conflict", "资料已被其他操作更新，请重新生成预览后确认。", status=409
            )
        payload = json.loads(row["payload_json"])
        if fingerprint(payload) != row["fingerprint"]:
            raise LibraryError("draft_corrupted", "待确认资料校验失败，请重新导入。", status=409)
        expected_content = content_fingerprint(payload, kind)
        if kind == "law":
            check_category_conflicts(conn, payload)
            loader.load_law_from_dict(conn, payload)
        else:
            check_name_conflict(conn, payload)
            normsources.import_source_from_dict(conn, payload)
        after = current_payload(conn, kind, target)
        if after is None or content_fingerprint(after, kind) != expected_content:
            raise LibraryError(
                "commit_mismatch", "写入内容与预览不一致，已撤销本次写入。", status=500
            )
        operation_id = _record_operation(conn, row, before, after, actor)
        conn.execute(
            "UPDATE library_drafts SET status = 'committed', operation_id = ? WHERE id = ?",
            (operation_id, draft_id),
        )
        conn.execute(
            "UPDATE library_jobs SET state = 'completed', phase = 'committed', "
            "message = '已确认入库', finished_at = ? WHERE draft_id = ?",
            (utc_now(), draft_id),
        )
    return _commit_result(row, operation_id, repeated=False)


def _check_ready(row: sqlite3.Row) -> None:
    if row["status"] != "ready":
        raise LibraryError("draft_not_ready", "此预览已取消，不能继续入库。", status=409)
    if datetime.fromisoformat(row["expires_at"]) <= datetime.now(timezone.utc):
        raise LibraryError("draft_expired", "预览已过期，请重新生成并核对。", status=410)


def _record_operation(
    conn: sqlite3.Connection, row: sqlite3.Row, before: dict | None, after: dict, actor: str
) -> str:
    operation_id = uuid.uuid4().hex
    after_snapshot = portable_payload(after, row["kind"])
    if row["kind"] == "law":
        confirmed = json.loads(row["payload_json"])
        after_snapshot.update(
            {field: confirmed[field] for field in REVISION_FIELDS if field in confirmed}
        )
    before_snapshot = None
    if before is not None:
        before_snapshot = portable_payload(before, row["kind"])
        if row["kind"] == "law":
            before_snapshot.update(_revision_identity(conn, before))
    origin = json.loads(row["origin_json"])
    action = (
        "restore"
        if origin.get("restore_operation") or origin.get("restore_revision")
        else ("replace" if before is not None else "import")
    )
    conn.execute(
        "INSERT INTO library_operations (id, kind, target_id, action, draft_id, before_json, "
        "after_json, before_fingerprint, after_fingerprint, origin_json, actor, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            operation_id,
            row["kind"],
            row["target_id"],
            action,
            row["id"],
            encode_json(before_snapshot) if before_snapshot is not None else None,
            encode_json(after_snapshot),
            row["base_fingerprint"],
            content_fingerprint(after, row["kind"]),
            row["origin_json"],
            actor,
            utc_now(),
        ),
    )
    return operation_id


def _revision_identity(conn: sqlite3.Connection, before: dict) -> dict:
    """Recover the stored revision that matches the pre-operation content.

    `revisions.content_hash` holds the law's `source_hash`. Without this, restoring
    "before" content would insert a fresh `{law_id}@{hash}` revision instead of
    re-pointing at the existing one.
    """
    if not before.get("id") or not before.get("source_hash"):
        return {}
    row = conn.execute(
        "SELECT id, version_label, released_at, notes FROM revisions "
        "WHERE law_id = ? AND content_hash = ? ORDER BY released_at DESC, id DESC LIMIT 1",
        (before["id"], before["source_hash"]),
    ).fetchone()
    if row is None:
        return {}
    return {
        "revision_id": row["id"],
        "version_label": row["version_label"],
        "revision_released_at": row["released_at"],
        "revision_notes": row["notes"],
    }


def _commit_result(row: sqlite3.Row, operation_id: str, *, repeated: bool) -> dict:
    return {
        "kind": "library_commit",
        "draft_id": row["id"],
        "document_kind": row["kind"],
        "target_id": row["target_id"],
        "operation_id": operation_id,
        "fingerprint": row["fingerprint"],
        "repeated": repeated,
    }


def cancel_draft(db_path: Path | str, draft_id: str) -> dict:
    with connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = _row(conn, draft_id)
        if row["status"] == "committed":
            raise LibraryError("already_committed", "此资料已入库，不能取消预览。", status=409)
        conn.execute("UPDATE library_drafts SET status = 'cancelled' WHERE id = ?", (draft_id,))
        conn.execute(
            "UPDATE library_jobs SET state = 'cancelled', finished_at = ?, message = '已取消入库' "
            "WHERE draft_id = ?",
            (utc_now(), draft_id),
        )
    return {"kind": "library_draft_cancelled", "id": draft_id}


def mark_reviewed(
    db_path: Path | str,
    kind: str,
    identifier: str,
    *,
    expected_fingerprint: str,
    note: str = "",
    actor: str = "owner",
) -> dict:
    require_kind(kind)
    if len(note) > 2000:
        raise LibraryError("note_too_long", "核对备注最多 2,000 字。")
    now = utc_now()
    with connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        current = current_payload(conn, kind, identifier)
        if current is None:
            raise LibraryError("document_not_found", "资料不存在。", status=404)
        if content_fingerprint(current, kind) != expected_fingerprint:
            raise LibraryError("content_conflict", "内容已更新，请核对新版本。", status=409)
        conn.execute(
            "INSERT INTO library_reviews (kind, target_id, fingerprint, actor, note, reviewed_at) "
            "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(kind, target_id, fingerprint) DO UPDATE SET "
            "actor = excluded.actor, note = excluded.note, reviewed_at = excluded.reviewed_at",
            (kind, identifier, expected_fingerprint, actor, note, now),
        )
    return {"kind": "library_review", "fingerprint": expected_fingerprint, "reviewed_at": now}


def list_operations(db_path: Path | str, kind: str, identifier: str) -> list[dict]:
    require_kind(kind)
    with connect_readonly(db_path) as conn:
        rows = conn.execute(
            "SELECT id, kind, target_id, action, draft_id, actor, created_at, "
            "before_fingerprint, after_fingerprint, origin_json FROM library_operations "
            "WHERE kind = ? AND target_id = ? ORDER BY created_at DESC, id DESC LIMIT 100",
            (kind, identifier),
        ).fetchall()
    items = []
    for row in rows:
        item = dict(row)
        item["origin"] = json.loads(item.pop("origin_json"))
        items.append(item)
    return items


def restore_operation(db_path: Path | str, operation_id: str, *, side: str = "after") -> dict:
    if side not in {"before", "after"}:
        raise LibraryError("invalid_restore_side", "请选择恢复操作前或操作后的内容。")
    with connect_readonly(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM library_operations WHERE id = ?", (operation_id,)
        ).fetchone()
    if row is None:
        raise LibraryError("operation_not_found", "维护记录不存在。", status=404)
    raw = row[f"{side}_json"]
    if raw is None:
        raise LibraryError("no_previous_content", "此操作前没有可恢复的正文。")
    origin = {"restore_operation": operation_id, "side": side}
    if side == "after":
        origin.update(json.loads(row["origin_json"]))
    return create_draft(db_path, row["kind"], json.loads(raw), origin=origin)


def restore_revision(db_path: Path | str, kind: str, identifier: str, revision: str) -> dict:
    from chinalaw.admin.catalog import revision_document

    payload = revision_document(db_path, kind, identifier, revision)["document"]
    return create_draft(db_path, kind, payload, origin={"restore_revision": revision})
