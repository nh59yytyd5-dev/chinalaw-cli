"""Read-only, paginated views of an existing public/private library."""

from __future__ import annotations

import json
from pathlib import Path

from chinalaw import models, normsources, service
from chinalaw.admin.errors import LibraryError, require_kind
from chinalaw.admin.payloads import content_fingerprint, current_payload
from chinalaw.db import connect_readonly, get_meta, read_only_operation


def _like(value: str) -> str:
    return "%" + value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _pagination(page: int, page_size: int) -> None:
    if not 1 <= page <= 1_000_000 or not 1 <= page_size <= 100:
        raise LibraryError("invalid_pagination", "页码必须为正数，每页最多 100 条。")


def list_documents(
    db_path: Path | str,
    *,
    kind: str = "law",
    query: str = "",
    page: int = 1,
    page_size: int = 24,
    status: str | None = None,
    category: str | None = None,
    source: str | None = None,
) -> dict:
    """Return inventory metadata; no complete documents are loaded for a page."""
    require_kind(kind)
    _pagination(page, page_size)
    if len(query) > 200:
        raise LibraryError("query_too_long", "检索词最多 200 字。")
    table, title, short, category_column, clause_table, parent = (
        ("laws", "title", "short_title", "level", "articles", "law_id")
        if kind == "law"
        else ("norm_sources", "name", "short_name", "source_type", "norm_clauses", "norm_source_id")
    )
    conditions, parameters = [], []
    if query.strip():
        conditions.append(
            f"(d.{title} LIKE ? ESCAPE '\\' OR d.{short} LIKE ? ESCAPE '\\' "
            "OR d.aliases LIKE ? ESCAPE '\\')"
        )
        parameters.extend([_like(query.strip())] * 3)
    for column, value in ((category_column, category), ("source_name", source)):
        if value:
            conditions.append(f"d.{column} = ?")
            parameters.append(value)
    if status and kind == "law":
        conditions.append("d.status = ?")
        parameters.append(status)
    where = "WHERE " + " AND ".join(conditions) if conditions else ""
    with connect_readonly(db_path) as conn:
        conn.execute("BEGIN")
        total = conn.execute(f"SELECT COUNT(*) FROM {table} d {where}", parameters).fetchone()[0]
        rows = conn.execute(
            f"SELECT d.*, (SELECT COUNT(*) FROM {clause_table} c "
            f"WHERE c.{parent} = d.id) AS clause_count FROM {table} d {where} "
            f"ORDER BY d.{title} COLLATE NOCASE, d.id LIMIT ? OFFSET ?",
            [*parameters, page_size, (page - 1) * page_size],
        ).fetchall()
        items = [_inventory_item(dict(row), kind) for row in rows]
        library_id = get_meta(conn, "library_id")
    return {
        "kind": "library_inventory",
        "document_kind": kind,
        "library_id": library_id,
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": (total + page_size - 1) // page_size,
    }


def _inventory_item(row: dict, kind: str) -> dict:
    item = {
        "id": row["id"],
        "kind": kind,
        "title": row["title"] if kind == "law" else row["name"],
        "short_title": row["short_title"] if kind == "law" else row["short_name"],
        "category": row["level"] if kind == "law" else row["source_type"],
        "status": row.get("status"),
        "source_name": row["source_name"],
        "source_url": row["source_url"],
        "source_checked_at": row["source_checked_at"],
        "effective_at": row["effective_at"],
        "updated_at": row["updated_at"],
        "clause_count": row["clause_count"],
        "aliases": json.loads(row["aliases"] or "[]"),
    }
    if kind == "norm":
        item["binding_note"] = models.norm_source_type_binding_note(row["source_type"])
        item["authority"] = row["authority"]
        item["binding_scope"] = row["binding_scope"]
    return item


def get_document(
    db_path: Path | str, kind: str, identifier: str, *, include_management: bool = True
) -> dict:
    require_kind(kind)
    with connect_readonly(db_path) as conn:
        conn.execute("BEGIN")
        payload = current_payload(conn, kind, identifier)
        if payload is None:
            raise LibraryError("document_not_found", "资料不存在。", status=404)
        digest = content_fingerprint(payload, kind)
        review = conn.execute(
            "SELECT actor, note, reviewed_at FROM library_reviews "
            "WHERE kind = ? AND target_id = ? AND fingerprint = ?",
            (kind, identifier, digest),
        ).fetchone()
        latest = conn.execute(
            "SELECT id, origin_json, created_at FROM library_operations "
            "WHERE kind = ? AND target_id = ? AND after_fingerprint = ? "
            "ORDER BY created_at DESC, id DESC LIMIT 1",
            (kind, identifier, digest),
        ).fetchone()
    if kind == "norm":
        payload["binding_note"] = models.norm_source_type_binding_note(payload["source_type"])
    return {
        "kind": "library_document",
        "document_kind": kind,
        "document": payload,
        "fingerprint": digest,
        "review": dict(review) if review and include_management else None,
        "latest_operation": {
            "id": latest["id"],
            "origin": json.loads(latest["origin_json"]),
            "created_at": latest["created_at"],
        }
        if latest and include_management
        else None,
    }


def revision_document(db_path: Path | str, kind: str, identifier: str, revision: str) -> dict:
    require_kind(kind)
    table, parent, key = (
        ("revisions", "law_id", "id")
        if kind == "law"
        else ("norm_source_revisions", "norm_source_id", "revision")
    )
    with connect_readonly(db_path) as conn:
        fields = (
            "snapshot_json, id, version_label, released_at, notes"
            if kind == "law"
            else "snapshot_json"
        )
        row = conn.execute(
            f"SELECT {fields} FROM {table} WHERE {parent} = ? AND {key} = ?",
            (identifier, revision),
        ).fetchone()
    if row is None:
        raise LibraryError("revision_not_found", "此历史版本不存在。", status=404)
    try:
        payload = json.loads(row["snapshot_json"] or "null")
        member = "articles" if kind == "law" else "clauses"
        if not isinstance(payload, dict) or payload.get("id") != identifier:
            raise ValueError("snapshot identity mismatch")
        clauses = payload.get(member)
        if (
            not isinstance(clauses, list)
            or not clauses
            or any(
                not isinstance(clause, dict) or not isinstance(clause.get("text"), str)
                for clause in clauses
            )
        ):
            raise ValueError("snapshot has no complete text")
    except (ValueError, TypeError) as exc:
        raise LibraryError(
            "revision_unavailable", "此历史版本没有可用的完整快照，请从来源重新取得。", status=409
        ) from exc
    if kind == "law":
        payload.update(
            revision_id=row["id"],
            version_label=row["version_label"],
            revision_released_at=row["released_at"],
            revision_notes=row["notes"],
        )
    return {
        "kind": "library_revision",
        "document_kind": kind,
        "revision": revision,
        "document": payload,
        "fingerprint": content_fingerprint(payload, kind),
    }


def revisions(db_path: Path | str, kind: str, identifier: str) -> dict:
    require_kind(kind)
    with read_only_operation():
        result = (
            service.history(db_path, identifier)
            if kind == "law"
            else normsources.list_revisions(db_path, identifier)
        )
    if result is None:
        raise LibraryError("document_not_found", "资料不存在。", status=404)
    return result


def dashboard(db_path: Path | str) -> dict:
    """Owner-only overview. Query tokens must never call this private aggregate."""
    report = service.status(db_path)
    report.pop("db_path", None)
    report.pop("alias_agent", None)
    with connect_readonly(db_path) as conn:
        report["library_id"] = get_meta(conn, "library_id")
        report["jobs"] = {
            row["state"]: row["count"]
            for row in conn.execute(
                "SELECT state, COUNT(*) AS count FROM library_jobs GROUP BY state"
            )
        }
        report["pending_drafts"] = conn.execute(
            "SELECT COUNT(*) FROM library_drafts WHERE status = 'ready'"
        ).fetchone()[0]
    return report


def search_library(
    db_path: Path | str,
    query: str,
    *,
    include_private: bool = False,
    kind: str = "all",
    limit: int = 20,
) -> dict:
    if kind not in {"all", "law", "article", "norm"} or not 1 <= limit <= 100:
        raise LibraryError("invalid_search", "检索类型或数量不正确。")
    if kind == "norm" and not include_private:
        raise LibraryError("private_access_denied", "此凭据未获私域规范访问权限。", status=403)
    if len(query) > 200:
        raise LibraryError("query_too_long", "检索词最多 200 字。")
    with read_only_operation():
        return service.search(db_path, query, limit=limit, kind=kind, include_norm=include_private)
