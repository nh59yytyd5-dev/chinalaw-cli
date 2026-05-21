"""Public cleaning rebuild workflow.

This module exists to keep agents and contributors on supported paths. When the
cleaning rules improve, callers should run ``chinalaw rebuild-clean`` instead
of reading SQLite tables directly or importing private parsing helpers.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from chinalaw import cleaning, normsources
from chinalaw.db import connect, migrate, set_meta
from chinalaw.loader import load_law_from_dict
from chinalaw.service import _resolve_law_row


def rebuild_clean(
    db_path: Path | str,
    *,
    law: str | None = None,
    norm: str | None = None,
    dry_run: bool = False,
    limit: int | None = None,
) -> dict:
    """Re-run the current cleaning pipeline over stored law snapshots.

    ``source_hash`` represents upstream content identity, so a cleaning-only
    change may need to rewrite DB rows even when the upstream hash is unchanged.
    This function rebuilds from ``revisions.snapshot_json`` when available, and
    falls back to current DB rows for older databases.
    """

    if law and norm:
        return {
            "kind": "rebuild_clean",
            "ok": False,
            "found": False,
            "db_path": str(db_path),
            "requested_law": law,
            "requested_norm": norm,
            "dry_run": bool(dry_run),
            "cleaning_schema_version": cleaning.CLEANING_SCHEMA_VERSION,
            "law_count": 0,
            "norm_count": 0,
            "rebuilt_count": 0,
            "changed_count": 0,
            "skipped_count": 0,
            "error_count": 1,
            "items": [],
            "errors": [
                {
                    "error": "InvalidArgument",
                    "message": "--law and --norm are mutually exclusive",
                }
            ],
        }
    if norm:
        return _rebuild_norm_clean(db_path, norm=norm, dry_run=dry_run, limit=limit)

    with connect(db_path) as conn:
        migrate(conn)
        rows = _select_law_rows(conn, law=law, limit=limit)
        if law and not rows:
            return {
                "kind": "rebuild_clean",
                "ok": False,
                "found": False,
                "db_path": str(db_path),
                "requested_law": law,
                "requested_norm": None,
                "dry_run": bool(dry_run),
                "cleaning_schema_version": cleaning.CLEANING_SCHEMA_VERSION,
                "law_count": 0,
                "norm_count": 0,
                "rebuilt_count": 0,
                "changed_count": 0,
                "skipped_count": 0,
                "error_count": 0,
                "items": [],
                "errors": [],
            }

        items: list[dict] = []
        errors: list[dict] = []
        rebuilt_count = 0
        changed_count = 0
        skipped_count = 0

        for row in rows:
            try:
                current_payload = _payload_for_rebuild(conn, row)
                cleaned = cleaning.canonicalize(current_payload, source_kind="external_json")
                comparison = _compare_payloads(current_payload, cleaned)
                changed = comparison["changed"]
                loaded = False
                if changed and not dry_run:
                    load_law_from_dict(conn, cleaned)
                    loaded = True
                    rebuilt_count += 1
                elif changed:
                    rebuilt_count += 1
                else:
                    skipped_count += 1
                if changed:
                    changed_count += 1

                items.append(
                    {
                        "law_id": row["id"],
                        "title": row["title"],
                        "changed": changed,
                        "loaded": loaded,
                        "article_count": len(cleaned.get("articles") or []),
                        **comparison,
                    }
                )
            except Exception as exc:  # pragma: no cover - defensive per-law isolation
                errors.append(
                    {
                        "law_id": row["id"],
                        "title": row["title"],
                        "error": exc.__class__.__name__,
                        "message": str(exc),
                    }
                )

        if not dry_run:
            checked_at = datetime.now(timezone.utc).isoformat()
            set_meta(conn, "cleaning_schema_version", str(cleaning.CLEANING_SCHEMA_VERSION))
            set_meta(conn, "cleaning:last_rebuild_at", checked_at)

        return {
            "kind": "rebuild_clean",
            "ok": not errors,
            "found": True,
            "db_path": str(db_path),
            "requested_law": law,
            "requested_norm": None,
            "dry_run": bool(dry_run),
            "cleaning_schema_version": cleaning.CLEANING_SCHEMA_VERSION,
            "law_count": len(rows),
            "norm_count": 0,
            "rebuilt_count": rebuilt_count,
            "changed_count": changed_count,
            "skipped_count": skipped_count,
            "error_count": len(errors),
            "items": items,
            "errors": errors,
        }


def _rebuild_norm_clean(
    db_path: Path | str,
    *,
    norm: str,
    dry_run: bool,
    limit: int | None,
) -> dict:
    with connect(db_path) as conn:
        migrate(conn)
        rows = _select_norm_rows(conn, norm=norm, limit=limit)
        if norm and not rows:
            return {
                "kind": "rebuild_clean",
                "ok": False,
                "found": False,
                "db_path": str(db_path),
                "requested_law": None,
                "requested_norm": norm,
                "dry_run": bool(dry_run),
                "cleaning_schema_version": cleaning.CLEANING_SCHEMA_VERSION,
                "law_count": 0,
                "norm_count": 0,
                "rebuilt_count": 0,
                "changed_count": 0,
                "skipped_count": 0,
                "error_count": 0,
                "items": [],
                "errors": [],
            }

        items: list[dict] = []
        errors: list[dict] = []
        rebuilt_count = 0
        changed_count = 0
        skipped_count = 0

        for row in rows:
            try:
                current_payload = _norm_payload_from_current_rows(conn, row)
                cleaned = _norm_payload_for_rebuild(row)
                comparison = _compare_norm_payloads(current_payload, cleaned)
                changed = comparison["changed"]
                loaded = False
                if changed and not dry_run:
                    normsources.import_source_from_dict(conn, cleaned)
                    loaded = True
                    rebuilt_count += 1
                elif changed:
                    rebuilt_count += 1
                else:
                    skipped_count += 1
                if changed:
                    changed_count += 1

                items.append(
                    {
                        "kind": "norm_source",
                        "norm_source_id": row["id"],
                        "title": row["name"],
                        "changed": changed,
                        "loaded": loaded,
                        **comparison,
                    }
                )
            except Exception as exc:  # pragma: no cover - defensive per-norm isolation
                errors.append(
                    {
                        "kind": "norm_source",
                        "norm_source_id": row["id"],
                        "title": row["name"],
                        "error": exc.__class__.__name__,
                        "message": str(exc),
                    }
                )

        if not dry_run:
            checked_at = datetime.now(timezone.utc).isoformat()
            set_meta(conn, "cleaning_schema_version", str(cleaning.CLEANING_SCHEMA_VERSION))
            set_meta(conn, "cleaning:last_norm_rebuild_at", checked_at)

        return {
            "kind": "rebuild_clean",
            "ok": not errors,
            "found": True,
            "db_path": str(db_path),
            "requested_law": None,
            "requested_norm": norm,
            "dry_run": bool(dry_run),
            "cleaning_schema_version": cleaning.CLEANING_SCHEMA_VERSION,
            "law_count": 0,
            "norm_count": len(rows),
            "rebuilt_count": rebuilt_count,
            "changed_count": changed_count,
            "skipped_count": skipped_count,
            "error_count": len(errors),
            "items": items,
            "errors": errors,
        }


def _select_law_rows(
    conn: sqlite3.Connection,
    *,
    law: str | None,
    limit: int | None,
) -> list[sqlite3.Row]:
    if law:
        row = _resolve_law_row(conn, law)
        return [row] if row is not None else []

    sql = "SELECT * FROM laws ORDER BY title ASC, released_at DESC"
    params: tuple[int, ...] = ()
    if limit is not None:
        sql += " LIMIT ?"
        params = (max(int(limit), 0),)
    return list(conn.execute(sql, params).fetchall())


def _select_norm_rows(
    conn: sqlite3.Connection,
    *,
    norm: str | None,
    limit: int | None,
) -> list[sqlite3.Row]:
    if norm:
        rows = list(
            conn.execute(
                """
                SELECT *
                FROM norm_sources
                WHERE id = ? OR name = ? OR short_name = ?
                ORDER BY name ASC
                """,
                (norm, norm, norm),
            ).fetchall()
        )
        if rows:
            return rows
        pattern = f"%{norm}%"
        candidates = list(
            conn.execute(
                """
                SELECT *
                FROM norm_sources
                WHERE name LIKE ? OR short_name LIKE ? OR aliases LIKE ?
                ORDER BY name ASC
                """,
                (pattern, pattern, pattern),
            ).fetchall()
        )
        return candidates[:1]

    sql = "SELECT * FROM norm_sources ORDER BY name ASC"
    params: tuple[int, ...] = ()
    if limit is not None:
        sql += " LIMIT ?"
        params = (max(int(limit), 0),)
    return list(conn.execute(sql, params).fetchall())


def _payload_for_rebuild(conn: sqlite3.Connection, row: sqlite3.Row) -> dict:
    snapshot = _latest_snapshot(conn, row["id"])
    if snapshot is not None:
        return snapshot
    return _payload_from_current_rows(conn, row)


def _latest_snapshot(conn: sqlite3.Connection, law_id: str) -> dict | None:
    row = conn.execute(
        """
        SELECT snapshot_json
        FROM revisions
        WHERE law_id = ? AND snapshot_json IS NOT NULL AND snapshot_json != ''
        ORDER BY COALESCE(effective_at, released_at, '') DESC, rowid DESC
        LIMIT 1
        """,
        (law_id,),
    ).fetchone()
    if row is None:
        return None
    try:
        payload = json.loads(row["snapshot_json"])
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _payload_from_current_rows(conn: sqlite3.Connection, row: sqlite3.Row) -> dict:
    articles = [
        {
            "number": article["number"],
            "number_display": article["number_display"],
            "part": article["part"],
            "title": article["title"],
            "text": article["text"],
            "position": article["position"],
        }
        for article in conn.execute(
            """
            SELECT number, number_display, part, title, text, position
            FROM articles
            WHERE law_id = ?
            ORDER BY position ASC, number ASC
            """,
            (row["id"],),
        ).fetchall()
    ]
    return {
        "id": row["id"],
        "title": row["title"],
        "short_title": row["short_title"],
        "aliases": _decode_aliases(row["aliases"]),
        "level": row["level"],
        "status": row["status"],
        "issuing_body": row["issuing_body"],
        "document_number": row["document_number"],
        "released_at": row["released_at"],
        "effective_at": row["effective_at"],
        "repealed_at": row["repealed_at"],
        "source_url": row["source_url"],
        "source_name": row["source_name"],
        "source_checked_at": row["source_checked_at"],
        "source_hash": row["source_hash"],
        "articles": articles,
    }


def _decode_aliases(raw: str | None) -> list[str]:
    try:
        aliases = json.loads(raw) if raw else []
    except json.JSONDecodeError:
        return []
    return [alias for alias in aliases if isinstance(alias, str)]


def _decode_metadata(raw: str | None) -> dict:
    try:
        metadata = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        return {}
    return metadata if isinstance(metadata, dict) else {}


def _norm_payload_from_current_rows(conn: sqlite3.Connection, row: sqlite3.Row) -> dict:
    clauses = [
        {
            "number": clause["number"],
            "number_display": clause["number_display"],
            "title": clause["title"],
            "text": clause["text"],
            "position": clause["position"],
        }
        for clause in conn.execute(
            """
            SELECT number, number_display, title, text, position
            FROM norm_clauses
            WHERE norm_source_id = ?
            ORDER BY position ASC
            """,
            (row["id"],),
        ).fetchall()
    ]
    return {
        "id": row["id"],
        "name": row["name"],
        "short_name": row["short_name"],
        "aliases": _decode_aliases(row["aliases"]),
        "source_type": row["source_type"],
        "authority": row["authority"],
        "binding_scope": row["binding_scope"],
        "jurisdiction": row["jurisdiction"],
        "effective_at": row["effective_at"],
        "repealed_at": row["repealed_at"],
        "source_url": row["source_url"],
        "source_name": row["source_name"],
        "source_checked_at": row["source_checked_at"],
        "source_hash": row["source_hash"],
        "metadata": _decode_metadata(row["metadata_json"]),
        "clauses": clauses,
    }


def _norm_payload_for_rebuild(row: sqlite3.Row) -> dict:
    metadata = _decode_metadata(row["metadata_json"])
    ingest = metadata.get("ingest") if isinstance(metadata.get("ingest"), dict) else {}
    source_path_raw = ingest.get("path")
    if not source_path_raw:
        raise ValueError("norm source has no ingest.path metadata; re-ingest the source file")
    source_path = Path(source_path_raw).expanduser()
    if not source_path.exists():
        raise FileNotFoundError(f"norm source file not found: {source_path}")
    text = normsources.read_source_text(source_path)
    return normsources.build_source_from_text(
        text,
        name=row["name"],
        source_id=row["id"],
        short_name=row["short_name"],
        source_type=row["source_type"],
        authority=row["authority"],
        binding_scope=row["binding_scope"],
        jurisdiction=row["jurisdiction"],
        effective_at=row["effective_at"],
        repealed_at=row["repealed_at"],
        source_url=row["source_url"],
        source_name=row["source_name"],
        source_checked_at=row["source_checked_at"],
        source_hash=row["source_hash"],
        aliases=_decode_aliases(row["aliases"]),
        metadata=metadata,
    )


def _normalized_norm_clauses(payload: dict) -> list[dict]:
    out: list[dict] = []
    for position, clause in enumerate(payload.get("clauses") or [], start=1):
        number_display = clause.get("number_display") or clause.get("number")
        out.append(
            {
                "number": normsources.normalize_clause_number(clause.get("number")),
                "number_display": number_display,
                "title": clause.get("title"),
                "text": (clause.get("text") or "").strip(),
                "position": position,
            }
        )
    return out


def _compare_norm_payloads(before: dict, after: dict) -> dict:
    before_clauses = _normalized_norm_clauses(before)
    after_clauses = _normalized_norm_clauses(after)
    max_len = max(len(before_clauses), len(after_clauses))
    clause_text_changed_count = 0
    clause_number_changed_count = 0
    for index in range(max_len):
        before_clause = before_clauses[index] if index < len(before_clauses) else {}
        after_clause = after_clauses[index] if index < len(after_clauses) else {}
        if (before_clause.get("text") or "") != (after_clause.get("text") or ""):
            clause_text_changed_count += 1
        if (
            before_clause.get("number"),
            before_clause.get("number_display"),
        ) != (
            after_clause.get("number"),
            after_clause.get("number_display"),
        ):
            clause_number_changed_count += 1
    changed = (
        len(before_clauses) != len(after_clauses)
        or clause_text_changed_count > 0
        or clause_number_changed_count > 0
    )
    return {
        "changed": changed,
        "clause_count_before": len(before_clauses),
        "clause_count_after": len(after_clauses),
        "clause_text_changed_count": clause_text_changed_count,
        "clause_number_changed_count": clause_number_changed_count,
    }


def _compare_payloads(before: dict, after: dict) -> dict:
    before_articles = {str(a.get("number")): a for a in before.get("articles") or []}
    after_articles = {str(a.get("number")): a for a in after.get("articles") or []}
    all_numbers = sorted(before_articles.keys() | after_articles.keys())

    article_text_changed_count = 0
    article_part_changed_count = 0
    article_title_changed_count = 0
    for number in all_numbers:
        before_article = before_articles.get(number) or {}
        after_article = after_articles.get(number) or {}
        if (before_article.get("text") or "") != (after_article.get("text") or ""):
            article_text_changed_count += 1
        if (before_article.get("part") or "") != (after_article.get("part") or ""):
            article_part_changed_count += 1
        if (before_article.get("title") or "") != (after_article.get("title") or ""):
            article_title_changed_count += 1

    aliases_before = list(before.get("aliases") or [])
    aliases_after = list(after.get("aliases") or [])
    short_before = before.get("short_title")
    short_after = after.get("short_title")
    changed = any(
        (
            aliases_before != aliases_after,
            short_before != short_after,
            article_text_changed_count > 0,
            article_part_changed_count > 0,
            article_title_changed_count > 0,
            len(before_articles) != len(after_articles),
        )
    )
    return {
        "changed": changed,
        "short_title_before": short_before,
        "short_title_after": short_after,
        "aliases_before": aliases_before,
        "aliases_after": aliases_after,
        "article_text_changed_count": article_text_changed_count,
        "article_part_changed_count": article_part_changed_count,
        "article_title_changed_count": article_title_changed_count,
    }
