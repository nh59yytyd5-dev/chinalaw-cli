"""Fixture / JSON 法规数据加载器。

读取 data/fixtures/*.json（或传入任意路径），写入 SQLite。
JSON schema：
{
  "id": "...",
  "title": "...",
  "short_title": "...",
  "aliases": [...],
  "level": "law" | "admin_regulation" | ...,
  "status": "current" | "amended" | "repealed" | "seed",
  "issuing_body": "...",
  "document_number": "...",
  "released_at": "YYYY-MM-DD",
  "effective_at": "YYYY-MM-DD",
  "source_url": "https://...",
  "source_name": "flk.npc.gov.cn",
  "source_checked_at": "YYYY-MM-DDTHH:MM:SS+08:00",
  "articles": [
    {"number": "1", "number_display": "第一条", "text": "...", "part": "第一章 总则"}
  ]
}
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path

from chinalaw import cleaning
from chinalaw.contracts import validate_law_payload
from chinalaw.datapaths import builtin_data_dir, builtin_data_search_message
from chinalaw.db import connect, migrate, set_meta
from chinalaw.document_numbers import index_document_number
from chinalaw.search_indexes import (
    delete_article_search_indexes,
    insert_article_search_index,
    replace_law_search_indexes,
)

FIXTURES_DIR = builtin_data_dir("fixtures")


def _article_id(law_id: str, number: str) -> str:
    return f"{law_id}#{number}"


def _content_hash(articles: list[dict]) -> str:
    h = hashlib.sha256()
    for art in articles:
        h.update(art.get("number", "").encode("utf-8"))
        h.update(b"\n")
        h.update(art.get("text", "").encode("utf-8"))
        h.update(b"\n")
    return h.hexdigest()


def _upsert_categories(conn: sqlite3.Connection, categories: list[dict]) -> None:
    for category in categories:
        conn.execute(
            """
            INSERT INTO categories (id, name, parent_id, description)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name=excluded.name,
                parent_id=excluded.parent_id,
                description=excluded.description
            """,
            (
                category["id"],
                category["name"],
                category.get("parent_id"),
                category.get("description"),
            ),
        )


def _replace_law_categories(conn: sqlite3.Connection, law_id: str, category_ids: list[str]) -> None:
    conn.execute("DELETE FROM law_categories WHERE law_id = ?", (law_id,))
    seen = set()
    for category_id in category_ids:
        if not category_id or category_id in seen:
            continue
        seen.add(category_id)
        conn.execute(
            "INSERT INTO law_categories(law_id, category_id) VALUES (?, ?)",
            (law_id, category_id),
        )


def _default_version_label(payload: dict) -> str:
    version_label = payload.get("version_label")
    if version_label:
        return version_label

    released_at = payload.get("released_at")
    effective_at = payload.get("effective_at")
    if released_at and effective_at and released_at != effective_at:
        return f"{released_at} 发布 / {effective_at} 施行"
    if released_at:
        return f"{released_at} 发布版"
    if effective_at:
        return f"{effective_at} 施行版"
    source_checked_at = payload.get("source_checked_at")
    if source_checked_at:
        return f"{source_checked_at[:10]} 快照"
    return "未命名版本"


def _build_snapshot_json(payload: dict, source_hash: str) -> str:
    snapshot = {
        "id": payload["id"],
        "title": payload["title"],
        "short_title": payload.get("short_title"),
        "aliases": payload.get("aliases", []),
        "level": payload["level"],
        "status": payload["status"],
        "issuing_body": payload.get("issuing_body"),
        "document_number": payload.get("document_number"),
        "released_at": payload.get("released_at"),
        "effective_at": payload.get("effective_at"),
        "repealed_at": payload.get("repealed_at"),
        "source_url": payload["source_url"],
        "source_name": payload.get("source_name", "unknown"),
        "source_checked_at": payload.get("source_checked_at"),
        "source_hash": source_hash,
        "articles": payload.get("articles", []),
    }
    return json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))


def _upsert_revision(conn: sqlite3.Connection, payload: dict, source_hash: str) -> None:
    law_id = payload["id"]
    revision_id = payload.get("revision_id") or f"{law_id}@{source_hash[:16]}"
    released_at = (
        payload.get("revision_released_at")
        or payload.get("released_at")
        or payload.get("effective_at")
        or datetime.now(timezone.utc).date().isoformat()
    )
    effective_at = payload.get("effective_at")
    notes = payload.get("revision_notes")
    version_label = _default_version_label(payload)
    snapshot_json = _build_snapshot_json(payload, source_hash)

    conn.execute(
        """
        INSERT INTO revisions (
            id, law_id, version_label, released_at, effective_at, notes, content_hash, snapshot_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            version_label=excluded.version_label,
            released_at=excluded.released_at,
            effective_at=excluded.effective_at,
            notes=excluded.notes,
            content_hash=excluded.content_hash,
            snapshot_json=excluded.snapshot_json
        """,
        (
            revision_id,
            law_id,
            version_label,
            released_at,
            effective_at,
            notes,
            source_hash,
            snapshot_json,
        ),
    )


def refresh_law_metadata(conn: sqlite3.Connection, payload: dict) -> None:
    """Refresh law/source metadata without replacing articles or revisions.

    Upstream effect status and freshness can change while the normative body
    remains byte-for-byte identical.  Sync/fetch use this path for same-hash
    payloads so those changes are visible without rebuilding article FTS rows.
    """

    if not isinstance(payload, dict):
        raise ValueError("canonical law payload must be an object")
    normalized = dict(payload)
    normalized.setdefault("aliases", [])
    normalized.setdefault("source_name", "unknown")
    normalized["articles"] = cleaning.normalize_articles(
        normalized.get("articles", [])
    )
    normalized["source_hash"] = normalized.get("source_hash") or _content_hash(
        normalized["articles"]
    )
    normalized["source_checked_at"] = normalized.get(
        "source_checked_at"
    ) or datetime.now(timezone.utc).isoformat()
    validate_law_payload(normalized, require_articles=False)

    law_id = normalized["id"]
    conn.execute(
        """
        UPDATE laws SET
            title = ?, short_title = ?, aliases = ?, level = ?, issuing_body = ?,
            document_number = ?, released_at = ?, effective_at = ?, repealed_at = ?,
            status = ?, source_url = ?, source_name = ?, source_checked_at = ?,
            source_hash = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (
            normalized["title"],
            normalized.get("short_title"),
            json.dumps(normalized.get("aliases", []), ensure_ascii=False),
            normalized["level"],
            normalized.get("issuing_body"),
            normalized.get("document_number"),
            normalized.get("released_at"),
            normalized.get("effective_at"),
            normalized.get("repealed_at"),
            normalized["status"],
            normalized["source_url"],
            normalized.get("source_name", "unknown"),
            normalized["source_checked_at"],
            normalized["source_hash"],
            law_id,
        ),
    )
    index_document_number(conn, normalized)
    categories = normalized.get("categories", [])
    category_ids = normalized.get("category_ids", [])
    if categories:
        _upsert_categories(conn, categories)
    if category_ids:
        _replace_law_categories(conn, law_id, category_ids)

    replace_law_search_indexes(
        conn,
        law_id=law_id,
        title=normalized["title"],
        short_title=normalized.get("short_title"),
        aliases=normalized.get("aliases", []),
    )


def load_law_from_dict(conn: sqlite3.Connection, payload: dict) -> int:
    """写入单部法规 + 条文 + FTS。返回写入的条文数。"""
    if not isinstance(payload, dict):
        raise ValueError("canonical law payload must be an object")

    payload = dict(payload)
    payload.setdefault("aliases", [])
    payload.setdefault("source_name", "unknown")
    payload["articles"] = cleaning.normalize_articles(payload.get("articles", []))
    payload["source_hash"] = payload.get("source_hash") or _content_hash(
        payload["articles"]
    )
    payload["source_checked_at"] = payload.get(
        "source_checked_at"
    ) or datetime.now(timezone.utc).isoformat()
    validate_law_payload(payload, require_articles=False)

    law_id = payload["id"]
    articles: list[dict] = payload["articles"]

    source_hash = payload["source_hash"]
    source_checked_at = payload["source_checked_at"]
    categories = payload.get("categories", [])
    category_ids = payload.get("category_ids", [])

    conn.execute(
        """
        INSERT INTO laws (
            id, title, short_title, aliases, level, issuing_body,
            document_number, released_at, effective_at, repealed_at,
            status, source_url, source_name, source_checked_at, source_hash
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            title=excluded.title,
            short_title=excluded.short_title,
            aliases=excluded.aliases,
            level=excluded.level,
            issuing_body=excluded.issuing_body,
            document_number=excluded.document_number,
            released_at=excluded.released_at,
            effective_at=excluded.effective_at,
            repealed_at=excluded.repealed_at,
            status=excluded.status,
            source_url=excluded.source_url,
            source_name=excluded.source_name,
            source_checked_at=excluded.source_checked_at,
            source_hash=excluded.source_hash,
            updated_at=CURRENT_TIMESTAMP
        """,
        (
            law_id,
            payload["title"],
            payload.get("short_title"),
            json.dumps(payload.get("aliases", []), ensure_ascii=False),
            payload["level"],
            payload.get("issuing_body"),
            payload.get("document_number"),
            payload.get("released_at"),
            payload.get("effective_at"),
            payload.get("repealed_at"),
            payload["status"],
            payload["source_url"],
            payload.get("source_name", "unknown"),
            source_checked_at,
            source_hash,
        ),
    )
    index_document_number(conn, payload)
    _upsert_revision(conn, payload, source_hash)
    if categories:
        _upsert_categories(conn, categories)
    if category_ids:
        _replace_law_categories(conn, law_id, category_ids)

    replace_law_search_indexes(
        conn,
        law_id=law_id,
        title=payload["title"],
        short_title=payload.get("short_title"),
        aliases=payload.get("aliases", []),
    )

    # articles：全量替换策略（简单可靠，v0.1 适用）
    delete_article_search_indexes(conn, law_id)
    conn.execute("DELETE FROM articles WHERE law_id = ?", (law_id,))

    count = 0
    for pos, art in enumerate(articles, start=1):
        number = str(art["number"])
        number_display = art.get("number_display") or f"第{number}条"
        article_id = art.get("id") or _article_id(law_id, number)
        text = art["text"]
        conn.execute(
            """
            INSERT INTO articles (
                id, law_id, number, number_display, part, title, text, position
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                article_id,
                law_id,
                number,
                number_display,
                art.get("part"),
                art.get("title"),
                text,
                art.get("position", pos),
            ),
        )
        insert_article_search_index(
            conn,
            article_id=article_id,
            law_id=law_id,
            law_title=payload["title"],
            number_display=number_display,
            text=text,
        )
        count += 1

    return count


def load_files(db_path: Path | str, paths: Iterable[Path]) -> dict:
    """加载多个 JSON 文件。返回统计摘要。"""
    file_paths = [Path(path) for path in paths]
    if not file_paths:
        return {
            "laws_loaded": 0,
            "articles_loaded": 0,
            "titles": [],
        }

    total_laws = 0
    total_articles = 0
    loaded: list[str] = []

    with connect(db_path) as conn:
        migrate(conn)
        for p in file_paths:
            payload = cleaning.canonicalize(
                json.loads(Path(p).read_text(encoding="utf-8")),
                source_kind="external_json",
            )
            total_articles += load_law_from_dict(conn, payload)
            total_laws += 1
            loaded.append(payload["title"])
        set_meta(
            conn,
            "last_sync_at",
            datetime.now(timezone.utc).isoformat(),
        )

    return {
        "laws_loaded": total_laws,
        "articles_loaded": total_articles,
        "titles": loaded,
    }


def load_fixtures(db_path: Path | str, fixtures_dir: Path | None = None) -> dict:
    """加载内置 fixture 目录下的所有 *.json。"""
    directory = Path(fixtures_dir) if fixtures_dir else FIXTURES_DIR
    if not directory.exists():
        return {
            "laws_loaded": 0,
            "articles_loaded": 0,
            "titles": [],
            "note": (
                f"fixtures dir missing: {directory}; "
                f"{builtin_data_search_message('fixtures')}"
            ),
        }
    paths = sorted(directory.glob("*.json"))
    if not paths:
        return {
            "laws_loaded": 0,
            "articles_loaded": 0,
            "titles": [],
            "fixtures_dir": str(directory),
            "note": f"fixtures dir contains no JSON files: {directory}",
        }
    result = load_files(db_path, paths)
    result["fixtures_dir"] = str(directory)
    return result
