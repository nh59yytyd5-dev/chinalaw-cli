"""Article-level commentary import and query helpers.

Commentary is intentionally local-only metadata over law/article ids. It lets a
separate ``law-data`` rebuild produce book-derived notes without turning this
project into a publisher of copyrighted commentary text.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from chinalaw.db import connect, migrate
from chinalaw.service import get_article, normalize_article_number


def _clean_text(value: object | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _clean_int(value: object | None) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def _clean_float(value: object | None) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def _slug_id(value: str) -> str:
    lowered = value.lower().strip()
    slug = re.sub(r"[^a-z0-9]+", "-", lowered).strip("-")
    if slug:
        return f"book-{slug}"
    digest = hashlib.sha1(value.encode("utf-8")).hexdigest()[:12]
    return f"book-{digest}"


def _commentary_id(book_id: str, law_id: str, article_number: str, position: int) -> str:
    digest = hashlib.sha1(
        f"{book_id}|{law_id}|{article_number}|{position}".encode()
    ).hexdigest()[:12]
    return f"commentary-{digest}"


def _json_obj(value: object | None, *, field: str) -> dict:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be a JSON object")
    return value


def _normalize_book(raw: dict) -> dict:
    title = _clean_text(raw.get("title"))
    if not title:
        raise ValueError("commentary book requires title")
    return {
        "id": _clean_text(raw.get("id")) or _slug_id(title),
        "title": title,
        "author": _clean_text(raw.get("author")),
        "publisher": _clean_text(raw.get("publisher")),
        "edition": _clean_text(raw.get("edition")),
        "published_at": _clean_text(raw.get("published_at")),
        "isbn": _clean_text(raw.get("isbn")),
        "source_name": _clean_text(raw.get("source_name")) or "local-commentary",
        "source_url": _clean_text(raw.get("source_url")),
        "license_scope": _clean_text(raw.get("license_scope")) or "local_only",
        "notes": _clean_text(raw.get("notes")),
        "metadata": _json_obj(raw.get("metadata"), field="book.metadata"),
    }


def _normalize_item(raw: dict, *, book_id: str, position: int) -> dict:
    law_id = _clean_text(raw.get("law_id"))
    article_number = normalize_article_number(
        _clean_text(raw.get("article_number") or raw.get("number")) or ""
    )
    if not law_id:
        raise ValueError(f"commentary item #{position} requires law_id")
    if not article_number:
        raise ValueError(f"commentary item #{position} requires article_number")
    item_id = _clean_text(raw.get("id")) or _commentary_id(
        book_id,
        law_id,
        article_number,
        position,
    )
    return {
        "id": item_id,
        "book_id": book_id,
        "law_id": law_id,
        "law_title": _clean_text(raw.get("law_title")),
        "article_number": article_number,
        "article_number_display": _clean_text(raw.get("article_number_display")),
        "page_start": _clean_int(raw.get("page_start")),
        "page_end": _clean_int(raw.get("page_end")),
        "excerpt": _clean_text(raw.get("excerpt")),
        "summary": _clean_text(raw.get("summary")),
        "ocr_confidence": _clean_float(raw.get("ocr_confidence")),
        "boundary_confidence": _clean_float(raw.get("boundary_confidence")),
        "qa_status": _clean_text(raw.get("qa_status")) or "unchecked",
        "license_scope": _clean_text(raw.get("license_scope")) or "local_only",
        "source_hash": _clean_text(raw.get("source_hash")),
        "metadata": _json_obj(raw.get("metadata"), field=f"items[{position}].metadata"),
        "position": position,
    }


def import_bundle_from_dict(conn: sqlite3.Connection, payload: dict) -> dict:
    """Import one commentary bundle.

    Expected shape::

        {"book": {...}, "items": [{"law_id": "...", "article_number": "143"}]}
    """

    migrate(conn)
    book = _normalize_book(_json_obj(payload.get("book"), field="book"))
    raw_items = payload.get("items") or payload.get("commentaries") or []
    if not isinstance(raw_items, list):
        raise ValueError("commentary bundle items must be a list")
    items = [
        _normalize_item(item, book_id=book["id"], position=index)
        for index, item in enumerate(raw_items, start=1)
        if isinstance(item, dict)
    ]

    conn.execute(
        """
        INSERT INTO commentary_books (
            id, title, author, publisher, edition, published_at, isbn,
            source_name, source_url, license_scope, notes, metadata_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            title=excluded.title,
            author=excluded.author,
            publisher=excluded.publisher,
            edition=excluded.edition,
            published_at=excluded.published_at,
            isbn=excluded.isbn,
            source_name=excluded.source_name,
            source_url=excluded.source_url,
            license_scope=excluded.license_scope,
            notes=excluded.notes,
            metadata_json=excluded.metadata_json,
            updated_at=CURRENT_TIMESTAMP
        """,
        (
            book["id"],
            book["title"],
            book["author"],
            book["publisher"],
            book["edition"],
            book["published_at"],
            book["isbn"],
            book["source_name"],
            book["source_url"],
            book["license_scope"],
            book["notes"],
            json.dumps(book["metadata"], ensure_ascii=False, separators=(",", ":")),
        ),
    )
    conn.execute("DELETE FROM article_commentaries WHERE book_id = ?", (book["id"],))
    for item in items:
        conn.execute(
            """
            INSERT INTO article_commentaries (
                id, book_id, law_id, law_title, article_number, article_number_display,
                page_start, page_end, excerpt, summary, ocr_confidence,
                boundary_confidence, qa_status, license_scope, source_hash,
                metadata_json, position
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item["id"],
                item["book_id"],
                item["law_id"],
                item["law_title"],
                item["article_number"],
                item["article_number_display"],
                item["page_start"],
                item["page_end"],
                item["excerpt"],
                item["summary"],
                item["ocr_confidence"],
                item["boundary_confidence"],
                item["qa_status"],
                item["license_scope"],
                item["source_hash"],
                json.dumps(item["metadata"], ensure_ascii=False, separators=(",", ":")),
                item["position"],
            ),
        )

    return {
        "kind": "commentary_import",
        "book_id": book["id"],
        "book_title": book["title"],
        "items_loaded": len(items),
        "license_scope": book["license_scope"],
        "source_checked_at": datetime.now(timezone.utc).isoformat(),
    }


def import_bundle_file(db_path: Path | str, path: Path | str) -> dict:
    source = Path(path)
    payload = json.loads(source.read_text(encoding="utf-8"))
    with connect(db_path) as conn:
        result = import_bundle_from_dict(conn, payload)
    result["path"] = str(source)
    return result


def list_books(db_path: Path | str) -> list[dict]:
    with connect(db_path) as conn:
        migrate(conn)
        rows = conn.execute(
            """
            SELECT b.*, COUNT(c.id) AS commentary_count
            FROM commentary_books b
            LEFT JOIN article_commentaries c ON c.book_id = b.id
            GROUP BY b.id
            ORDER BY b.title ASC
            """
        ).fetchall()
        return [_book_row(row) | {"commentary_count": row["commentary_count"]} for row in rows]


def get_article_commentary(
    db_path: Path | str,
    law_identifier: str,
    number: str,
    *,
    limit: int = 10,
) -> dict:
    article_payload = get_article(db_path, law_identifier, number)
    law = (article_payload or {}).get("law")
    article = (article_payload or {}).get("article")
    normalized_number = normalize_article_number(number)
    if law is None or article is None:
        return {
            "kind": "article_commentary",
            "found": False,
            "law": law,
            "article": article,
            "requested_law": law_identifier,
            "requested_number": number,
            "commentary_count": 0,
            "commentaries": [],
        }

    with connect(db_path) as conn:
        migrate(conn)
        rows = conn.execute(
            """
            SELECT c.*, b.title AS book_title, b.author AS book_author,
                   b.publisher AS book_publisher, b.edition AS book_edition,
                   b.published_at AS book_published_at, b.isbn AS book_isbn,
                   b.source_name AS book_source_name, b.source_url AS book_source_url,
                   b.license_scope AS book_license_scope
            FROM article_commentaries c
            JOIN commentary_books b ON b.id = c.book_id
            WHERE c.law_id = ? AND c.article_number = ?
            ORDER BY b.title ASC, c.position ASC
            LIMIT ?
            """,
            (
                law.get("id"),
                article.get("number") or normalized_number,
                max(int(limit), 1),
            ),
        ).fetchall()

    commentaries = [_commentary_row(row) for row in rows]
    return {
        "kind": "article_commentary",
        "found": True,
        "law": {
            "id": law.get("id"),
            "title": law.get("title"),
            "short_title": law.get("short_title"),
            "status": law.get("status"),
        },
        "article": {
            "number": article.get("number"),
            "number_display": article.get("number_display"),
            "title": article.get("title"),
            "text": article.get("text"),
        },
        "requested_law": law_identifier,
        "requested_number": number,
        "commentary_count": len(commentaries),
        "commentaries": commentaries,
    }


def _book_row(row: sqlite3.Row) -> dict:
    metadata = _decode_json(row["metadata_json"], default={})
    return {
        "id": row["id"],
        "title": row["title"],
        "author": row["author"],
        "publisher": row["publisher"],
        "edition": row["edition"],
        "published_at": row["published_at"],
        "isbn": row["isbn"],
        "source_name": row["source_name"],
        "source_url": row["source_url"],
        "license_scope": row["license_scope"],
        "notes": row["notes"],
        "metadata": metadata,
    }


def _commentary_row(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "book": {
            "id": row["book_id"],
            "title": row["book_title"],
            "author": row["book_author"],
            "publisher": row["book_publisher"],
            "edition": row["book_edition"],
            "published_at": row["book_published_at"],
            "isbn": row["book_isbn"],
            "source_name": row["book_source_name"],
            "source_url": row["book_source_url"],
            "license_scope": row["book_license_scope"],
        },
        "law_id": row["law_id"],
        "law_title": row["law_title"],
        "article_number": row["article_number"],
        "article_number_display": row["article_number_display"],
        "page_start": row["page_start"],
        "page_end": row["page_end"],
        "excerpt": row["excerpt"],
        "summary": row["summary"],
        "ocr_confidence": row["ocr_confidence"],
        "boundary_confidence": row["boundary_confidence"],
        "qa_status": row["qa_status"],
        "license_scope": row["license_scope"],
        "source_hash": row["source_hash"],
        "metadata": _decode_json(row["metadata_json"], default={}),
        "position": row["position"],
    }


def _decode_json(raw: str | None, *, default: object) -> object:
    try:
        return json.loads(raw) if raw else default
    except json.JSONDecodeError:
        return default
