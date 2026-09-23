"""Complete portable document snapshots and content fingerprints."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone

from chinalaw import loader, normsources
from chinalaw.admin.categories import current_categories
from chinalaw.admin.errors import LibraryError, require_kind
from chinalaw.contracts import validate_law_payload

LAW_FIELDS = (
    "id",
    "title",
    "short_title",
    "aliases",
    "level",
    "issuing_body",
    "document_number",
    "released_at",
    "effective_at",
    "repealed_at",
    "status",
    "source_url",
    "source_name",
    "source_checked_at",
    "source_hash",
)
NORM_FIELDS = (
    "id",
    "name",
    "short_name",
    "aliases",
    "source_type",
    "authority",
    "binding_scope",
    "jurisdiction",
    "effective_at",
    "repealed_at",
    "source_url",
    "source_name",
    "source_checked_at",
    "source_hash",
)
CLAUSE_FIELDS = ("id", "number", "number_display", "part", "title", "text", "position")
REVISION_FIELDS = ("revision_id", "version_label", "revision_released_at", "revision_notes")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def encode_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def fingerprint(payload: dict | None) -> str | None:
    if payload is None:
        return None
    return hashlib.sha256(encode_json(payload).encode("utf-8")).hexdigest()


def current_payload(conn: sqlite3.Connection, kind: str, identifier: str) -> dict | None:
    """Read a complete snapshot on the caller's transaction/connection.

    Sharing the commit transaction is necessary: a separate service connection
    could read an older snapshot and let a concurrent writer be overwritten.
    """
    require_kind(kind)
    table, fields = ("laws", LAW_FIELDS) if kind == "law" else ("norm_sources", NORM_FIELDS)
    row = conn.execute(f"SELECT * FROM {table} WHERE id = ?", (identifier,)).fetchone()
    if row is None:
        return None
    payload = {field: row[field] for field in fields}
    payload["aliases"] = json.loads(payload["aliases"] or "[]")
    if kind == "norm":
        payload["metadata"] = json.loads(row["metadata_json"] or "{}")
    else:
        payload.update(current_categories(conn, identifier))
    clause_table, key, member = (
        ("articles", "law_id", "articles")
        if kind == "law"
        else ("norm_clauses", "norm_source_id", "clauses")
    )
    rows = conn.execute(
        f"SELECT * FROM {clause_table} WHERE {key} = ? ORDER BY position, id",
        (identifier,),
    ).fetchall()
    payload[member] = [{field: clause[field] for field in CLAUSE_FIELDS} for clause in rows]
    return payload


def portable_payload(payload: dict, kind: str) -> dict:
    """Remove storage-generated clause ids so equivalent input and DB agree."""
    require_kind(kind)
    fields = LAW_FIELDS if kind == "law" else NORM_FIELDS
    result = {field: payload.get(field) for field in fields}
    result["aliases"] = payload.get("aliases") or []
    if kind == "norm":
        result["metadata"] = payload.get("metadata") or {}
    else:
        result["category_ids"] = payload.get("category_ids") or []
        result["categories"] = payload.get("categories") or []
    member = "articles" if kind == "law" else "clauses"
    result[member] = [
        {field: clause.get(field) for field in CLAUSE_FIELDS if field != "id"}
        for clause in payload.get(member, [])
    ]
    return result


def content_fingerprint(payload: dict | None, kind: str) -> str | None:
    """Identity of one document's own content.

    Category *definitions* are shared taxonomy: renaming a category that many
    laws link to must not invalidate each law's review mark or maintenance
    links. Only the association (``category_ids``) belongs to the document.
    The definitions stay in the portable payload for display, diff and restore.
    """
    if payload is None:
        return None
    portable = portable_payload(payload, kind)
    portable.pop("categories", None)
    return fingerprint(portable)


def prepare_payload(kind: str, payload: dict) -> dict:
    """Use the actual ingest validators, never a second set of write rules."""
    require_kind(kind)
    if not isinstance(payload, dict):
        raise LibraryError("invalid_payload", "导入内容必须是一个资料对象。")
    if kind == "law":
        prepared = loader.prepare_law_payload(payload)
        validate_law_payload(prepared, require_articles=True)
    else:
        prepared = _prepare_norm(payload)
    result = portable_payload(prepared, kind)
    if kind == "law":
        for field in REVISION_FIELDS:
            if field in prepared:
                value = prepared[field]
                if value is not None and not isinstance(value, str):
                    raise LibraryError("invalid_revision", "版本标识、标签、日期和备注必须为文本。")
                result[field] = value
    member = "articles" if kind == "law" else "clauses"
    if not result[member]:
        raise LibraryError("empty_document", "没有解析出可入库的正文，请检查原文件。")
    return result


def _prepare_norm(payload: dict) -> dict:
    """Normalize through the canonical importer in an isolated in-memory DB.

    This preserves all established private-norm validation and normalization
    rules. No persistent library is opened or mutated during preparation.
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    try:
        imported = normsources.import_source_from_dict(conn, payload)
        result = normsources.get_latest_revision_snapshot(conn, imported["source_id"])
        if result is None:
            raise LibraryError("normalization_failed", "未能生成完整的规范化预览。")
        return result
    finally:
        conn.close()


def compare_payloads(before: dict | None, after: dict, kind: str) -> dict:
    """Compare full clause text, numbering, structure and document metadata."""
    require_kind(kind)
    member = "articles" if kind == "law" else "clauses"
    old = portable_payload(before, kind) if before else {}
    new = portable_payload(after, kind)
    if kind == "law":
        new.update({field: after[field] for field in REVISION_FIELDS if field in after})
    old_items = old.get(member, [])
    new_items = new[member]
    old_map = _index_clauses(old_items)
    new_map = _index_clauses(new_items)
    added = [clause for token, clause in new_map.items() if token not in old_map]
    removed = [clause for token, clause in old_map.items() if token not in new_map]
    modified = [
        {"number": clause.get("number"), "before": old_map[token], "after": clause}
        for token, clause in new_map.items()
        if token in old_map and old_map[token] != clause
    ]
    metadata = [
        {"field": field, "before": old.get(field), "after": value}
        for field, value in new.items()
        if field != member and old.get(field) != value
    ]
    return {
        "added": added,
        "removed": removed,
        "modified": modified,
        "added_count": len(added),
        "removed_count": len(removed),
        "modified_count": len(modified),
        "metadata": metadata,
        "before_count": len(old_items),
        "after_count": len(new_items),
    }


def _index_clauses(clauses: list[dict]) -> dict[tuple[str, int], dict]:
    # Private documents can restart numbering in different parts. Never drop
    # those clauses through a dict keyed only by number.
    counts: dict[str, int] = {}
    result = {}
    for clause in clauses:
        number = str(clause.get("number") or f"position:{clause['position']}")
        counts[number] = counts.get(number, 0) + 1
        result[(number, counts[number])] = clause
    return result
