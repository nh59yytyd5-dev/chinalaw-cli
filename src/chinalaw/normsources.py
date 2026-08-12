"""私域规范来源读写。"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import sqlite3
import subprocess
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from chinalaw.db import connect, migrate
from chinalaw.resource_limits import (
    ensure_file_size,
    read_zip_member_limited,
    run_limited,
    validate_zip_archive,
)
from chinalaw.search_indexes import (
    delete_norm_clause_search_indexes,
    insert_norm_clause_search_index,
    replace_norm_source_search_index,
)
from chinalaw.service import normalize_article_number


def normalize_clause_number(raw: str | None) -> str:
    if raw is None:
        return ""
    text = re.sub(r"\s+", "", str(raw))
    if not text:
        return ""
    if (
        text.startswith("第")
        or "条" in text
        or re.fullmatch(r"[0-9]+(?:[-－—][0-9]+)?", text)
    ):
        return normalize_article_number(text)
    return text


def _clean_text(value: object | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _slug_id(value: str) -> str:
    lowered = value.lower().strip()
    slug = re.sub(r"[^a-z0-9]+", "-", lowered).strip("-")
    if slug:
        return slug
    digest = hashlib.sha1(value.encode("utf-8")).hexdigest()[:12]
    return f"norm-{digest}"


def _clause_id(source_id: str, position: int) -> str:
    return f"{source_id}:clause:{position}"


def _content_hash(payload: dict) -> str:
    digest = hashlib.sha256()
    normalized = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    digest.update(normalized.encode("utf-8"))
    return digest.hexdigest()


def _clean_string_list(value: object | None, *, field: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"norm source {field} must be a list")
    result: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = _clean_text(item)
        if text and text not in seen:
            result.append(text)
            seen.add(text)
    return result


def _merge_metadata(base: dict, extra: dict | None) -> dict:
    """Recursively merge caller metadata into generated ingest metadata."""

    if not extra:
        return dict(base)
    if not isinstance(extra, dict):
        raise ValueError("norm source metadata must be an object")
    merged = dict(base)
    for key, value in extra.items():
        if (
            isinstance(value, dict)
            and isinstance(merged.get(key), dict)
        ):
            merged[key] = _merge_metadata(merged[key], value)
        else:
            merged[key] = value
    return merged


def _read_docx_text(path: Path) -> str:
    ensure_file_size(path, label="DOCX norm source")
    with zipfile.ZipFile(path) as zf:
        validate_zip_archive(zf)
        xml = read_zip_member_limited(zf, "word/document.xml")
    root = ET.fromstring(xml)
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    paragraphs: list[str] = []
    for para in root.findall(".//w:p", ns):
        chunks = [node.text or "" for node in para.findall(".//w:t", ns)]
        text = "".join(chunks).strip()
        if text:
            paragraphs.append(text)
    return "\n".join(paragraphs)


def _read_plain_text(path: Path) -> str:
    ensure_file_size(path, label="text norm source")
    return path.read_text(encoding="utf-8")


def _read_pdf_text(path: Path) -> str:
    ensure_file_size(path, label="PDF norm source")
    pdftotext = shutil.which("pdftotext")
    if not pdftotext:
        raise ValueError("pdf norm source ingestion requires pdftotext")
    result = run_limited(
        [pdftotext, "-layout", str(path), "-"],
        runner=subprocess.run,
    )
    if result.returncode != 0:
        message = (result.stderr or "").strip() or "pdftotext failed"
        raise ValueError(f"pdf text extraction failed: {message}")
    return result.stdout


def read_source_text(path: Path | str) -> str:
    source_path = Path(path)
    suffix = source_path.suffix.lower()
    if suffix == ".docx":
        return _read_docx_text(source_path)
    if suffix in {".txt", ".md"}:
        return _read_plain_text(source_path)
    if suffix == ".pdf":
        return _read_pdf_text(source_path)
    raise ValueError("only .txt, .md, .docx and .pdf norm source ingestion is supported")


def _match_clause_heading(line: str) -> tuple[str, str] | None:
    chinese = re.match(
        r"^(第[一二三四五六七八九十百千万零〇两\d]+条"
        r"(?:之[一二三四五六七八九十\d]+)?)(?:[：:、.\s　-]*(.*))?$",
        line,
    )
    if chinese:
        return chinese.group(1), (chinese.group(2) or "").strip()
    numeric = re.match(r"^([0-9]+(?:\.[0-9]+)+)(?:[、.．\s　:：]+)(.+)$", line)
    if numeric:
        return numeric.group(1), numeric.group(2).strip()
    numeric = re.match(r"^([0-9]+)(?:[、.:：]+)(.+)$", line)
    if numeric:
        return numeric.group(1), numeric.group(2).strip()
    return None


def _extract_bracketed_title(text: str) -> str | None:
    match = re.match(r"^[【\[]([^】\]]{1,120})[】\]]", text.strip())
    if not match:
        return None
    return match.group(1).strip() or None


_MARKDOWN_PREFIX_RE = re.compile(r"^[\s#>*+\-]+")


def _strip_markdown_prefix(line: str) -> str:
    """剥离 markdown 标题 / 列表 / 引用前缀，保留正文。

    例：``## 第30条【条名】`` -> ``第30条【条名】``；
    ``- 第三条 ...`` -> ``第三条 ...``；``> 第一条`` -> ``第一条``。
    """

    stripped = _MARKDOWN_PREFIX_RE.sub("", line).strip()
    if stripped.startswith("**") and stripped.endswith("**") and len(stripped) > 4:
        stripped = stripped[2:-2].strip()
    return stripped


def clauses_from_text(text: str) -> list[dict]:
    lines = [line.strip() for line in text.splitlines()]
    clauses: list[dict] = []
    current: dict | None = None
    buffer: list[str] = []
    leading_buffer: list[str] = []
    saw_heading = False

    def flush() -> None:
        nonlocal current, buffer
        if current is None:
            return
        body = "\n".join(part for part in buffer if part).strip()
        if body:
            current["text"] = body
            clauses.append(current)
        current = None
        buffer = []

    for raw_line in lines:
        if not raw_line:
            continue
        candidate = _strip_markdown_prefix(raw_line)
        is_markdown_header = raw_line.startswith("#")
        heading = _match_clause_heading(candidate) if candidate else None
        if heading is not None:
            saw_heading = True
            flush()
            number, rest = heading
            current = {
                "number": number,
                "number_display": number,
            }
            title = _extract_bracketed_title(rest)
            if title:
                current["title"] = title
            buffer = [rest] if rest else []
            continue
        if is_markdown_header:
            # 不匹配条款标题的 markdown header（如 `# 文档标题`、`## 引言`）
            # 视为文档结构，不混入正文 buffer
            continue
        if current is None:
            if not saw_heading:
                # Keep leading preamble only as a fallback for documents with no
                # recognizable clause headings. Official reprints often put
                # issuer/order/source metadata in blockquotes before 第一条;
                # those lines must not become clause #0 once real clauses exist.
                leading_buffer.append(raw_line)
                continue
            current = {"number": None, "number_display": None}
        buffer.append(raw_line)

    flush()
    if clauses:
        return clauses
    stripped = "\n".join(leading_buffer).strip() if leading_buffer else text.strip()
    if not stripped:
        raise ValueError("norm source text is empty")
    return [{"number": None, "number_display": None, "text": stripped}]


def analyze_split_quality(text: str, clauses: list[dict]) -> list[dict]:
    """对切分结果做启发式质量检查，给出可见警告。

    场景：用户用 ``## 第N条【...】`` 标题写 100+ 条 markdown，但因 cleaning
    规则未识别而切成 1 项——本函数把这种「文本量大却切不出条」的异常显式标
    记，避免 agent 默默吃掉问题源材料。
    """

    warnings: list[dict] = []
    body = text.strip()
    if not body:
        return warnings

    nonempty_lines = [line for line in body.splitlines() if line.strip()]
    char_count = len(body)
    line_count = len(nonempty_lines)
    clause_count = len(clauses)
    has_numbered = any((c.get("number") or "").strip() for c in clauses)

    if clause_count == 1 and (char_count >= 1000 or line_count >= 20):
        warnings.append(
            {
                "code": "single_clause_large_text",
                "message": (
                    f"切分仅产出 1 条，但原文 {char_count} 字 / {line_count} 行——"
                    "可能未识别条款标题（如带 markdown 前缀的 `## 第N条`、"
                    "`第一条`、`1.`、`1.1`），请确认源材料标题格式。"
                ),
                "char_count": char_count,
                "line_count": line_count,
                "clause_count": clause_count,
            }
        )
    elif clause_count > 0 and not has_numbered:
        warnings.append(
            {
                "code": "no_numbered_clauses",
                "message": (
                    f"切分得到 {clause_count} 段但全部无编号——切分依赖空行而非"
                    "条款标题，可能不便后续按编号引用。"
                ),
                "char_count": char_count,
                "line_count": line_count,
                "clause_count": clause_count,
            }
        )

    return warnings


def build_source_from_text(
    text: str,
    *,
    name: str,
    source_id: str | None = None,
    short_name: str | None = None,
    source_type: str = "private_policy",
    authority: str | None = None,
    binding_scope: str | None = None,
    jurisdiction: str | None = None,
    effective_at: str | None = None,
    repealed_at: str | None = None,
    source_url: str | None = None,
    source_name: str = "local-text",
    source_checked_at: str | None = None,
    source_hash: str | None = None,
    aliases: list[str] | None = None,
    metadata: dict | None = None,
) -> dict:
    clauses = clauses_from_text(text)
    payload = {
        "name": name,
        "short_name": short_name,
        "aliases": aliases or [],
        "source_type": source_type,
        "authority": authority,
        "binding_scope": binding_scope,
        "jurisdiction": jurisdiction,
        "effective_at": effective_at,
        "repealed_at": repealed_at,
        "source_url": source_url,
        "source_name": source_name,
        "clauses": clauses,
        "metadata": metadata or {},
    }
    if source_id:
        payload["id"] = source_id
    if source_checked_at:
        payload["source_checked_at"] = source_checked_at
    if source_hash:
        payload["source_hash"] = source_hash
    return payload


def _normalize_clause(clause: dict, position: int, source_id: str) -> dict:
    text = _clean_text(clause.get("text"))
    if not text:
        raise ValueError("norm clause requires text")
    number_display = _clean_text(clause.get("number_display")) or _clean_text(
        clause.get("number")
    )
    return {
        "id": _clean_text(clause.get("id")) or _clause_id(source_id, position),
        "number": normalize_clause_number(clause.get("number")),
        "number_display": number_display,
        "title": _clean_text(clause.get("title")),
        "text": text,
        "position": position,
    }


def _source_row_to_dict(row: sqlite3.Row) -> dict:
    aliases_json = row["aliases"]
    metadata_json = row["metadata_json"]
    try:
        aliases = json.loads(aliases_json) if aliases_json else []
    except json.JSONDecodeError:
        aliases = []
    try:
        metadata = json.loads(metadata_json) if metadata_json else {}
    except json.JSONDecodeError:
        metadata = {}
    return {
        "kind": "norm_source",
        "id": row["id"],
        "name": row["name"],
        "short_name": row["short_name"],
        "aliases": aliases,
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
        "metadata": metadata,
    }


def _clause_row_to_dict(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "norm_source_id": row["norm_source_id"],
        "number": row["number"],
        "number_display": row["number_display"],
        "title": row["title"],
        "text": row["text"],
        "position": row["position"],
    }


def _resolve_source_row(conn: sqlite3.Connection, identifier: str) -> sqlite3.Row | None:
    exact = _clean_text(identifier)
    if not exact:
        return None
    exact_alias = f'%"{exact}"%'
    escaped = exact.replace("%", r"\%").replace("_", r"\_")
    fuzzy = f"%{escaped}%"
    row = conn.execute(
        """
        SELECT *
        FROM norm_sources
        WHERE id = ? OR name = ? OR short_name = ? OR aliases LIKE ? ESCAPE '\\'
        ORDER BY
            CASE
                WHEN id = ? THEN 0
                WHEN name = ? THEN 1
                WHEN short_name = ? THEN 2
                ELSE 3
            END
        LIMIT 1
        """,
        (exact, exact, exact, exact_alias, exact, exact, exact),
    ).fetchone()
    if row is not None:
        return row
    return conn.execute(
        """
        SELECT *
        FROM norm_sources
        WHERE name LIKE ? ESCAPE '\\'
           OR short_name LIKE ? ESCAPE '\\'
           OR aliases LIKE ? ESCAPE '\\'
        ORDER BY LENGTH(name) ASC
        LIMIT 1
        """,
        (fuzzy, fuzzy, fuzzy),
    ).fetchone()


def import_source_from_dict(conn: sqlite3.Connection, payload: dict) -> dict:
    migrate(conn)
    name = _clean_text(payload.get("name"))
    if not name:
        raise ValueError("norm source requires name")
    source_id = _clean_text(payload.get("id")) or _slug_id(name)
    clauses = payload.get("clauses")
    if not isinstance(clauses, list):
        raise ValueError("norm source requires clauses list")
    aliases = _clean_string_list(payload.get("aliases"), field="aliases")
    normalized_clauses = [
        _normalize_clause(clause, position, source_id)
        for position, clause in enumerate(clauses, start=1)
    ]
    metadata = payload.get("metadata") or {}
    if not isinstance(metadata, dict):
        raise ValueError("norm source metadata must be an object")
    source_checked_at = _clean_text(payload.get("source_checked_at")) or datetime.now(
        timezone.utc
    ).isoformat()
    source_hash = _clean_text(payload.get("source_hash")) or _content_hash(
        {
            "name": name,
            "short_name": payload.get("short_name"),
            "aliases": aliases,
            "source_type": payload.get("source_type"),
            "authority": payload.get("authority"),
            "binding_scope": payload.get("binding_scope"),
            "jurisdiction": payload.get("jurisdiction"),
            "effective_at": payload.get("effective_at"),
            "repealed_at": payload.get("repealed_at"),
            "clauses": normalized_clauses,
            "metadata": metadata,
        }
    )

    conn.execute(
        """
        INSERT INTO norm_sources (
            id, name, short_name, aliases, source_type, authority,
            binding_scope, jurisdiction, effective_at, repealed_at,
            source_url, source_name, source_checked_at, source_hash, metadata_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            name=excluded.name,
            short_name=excluded.short_name,
            aliases=excluded.aliases,
            source_type=excluded.source_type,
            authority=excluded.authority,
            binding_scope=excluded.binding_scope,
            jurisdiction=excluded.jurisdiction,
            effective_at=excluded.effective_at,
            repealed_at=excluded.repealed_at,
            source_url=excluded.source_url,
            source_name=excluded.source_name,
            source_checked_at=excluded.source_checked_at,
            source_hash=excluded.source_hash,
            metadata_json=excluded.metadata_json,
            updated_at=CURRENT_TIMESTAMP
        """,
        (
            source_id,
            name,
            _clean_text(payload.get("short_name")),
            json.dumps(aliases, ensure_ascii=False),
            _clean_text(payload.get("source_type")) or "private_policy",
            _clean_text(payload.get("authority")),
            _clean_text(payload.get("binding_scope")),
            _clean_text(payload.get("jurisdiction")),
            _clean_text(payload.get("effective_at")),
            _clean_text(payload.get("repealed_at")),
            _clean_text(payload.get("source_url")),
            _clean_text(payload.get("source_name")) or "local-file",
            source_checked_at,
            source_hash,
            json.dumps(metadata, ensure_ascii=False, separators=(",", ":")),
        ),
    )

    replace_norm_source_search_index(
        conn,
        source_id=source_id,
        name=name,
        short_name=_clean_text(payload.get("short_name")),
        aliases=aliases,
    )

    delete_norm_clause_search_indexes(conn, source_id)
    conn.execute("DELETE FROM norm_clauses WHERE norm_source_id = ?", (source_id,))
    for clause in normalized_clauses:
        conn.execute(
            """
            INSERT INTO norm_clauses (
                id, norm_source_id, number, number_display, title, text, position
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                clause["id"],
                source_id,
                clause["number"],
                clause["number_display"],
                clause["title"],
                clause["text"],
                clause["position"],
            ),
        )
        insert_norm_clause_search_index(
            conn,
            clause_id=clause["id"],
            source_id=source_id,
            source_name=name,
            number_display=clause["number_display"] or clause["number"] or "",
            text=clause["text"],
        )

    return {
        "kind": "norm_source_import",
        "source_id": source_id,
        "name": name,
        "clauses_loaded": len(normalized_clauses),
        "source_type": _clean_text(payload.get("source_type")) or "private_policy",
    }


def import_source_file(db_path: Path | str, path: Path | str) -> dict:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    with connect(db_path) as conn:
        result = import_source_from_dict(conn, payload)
    result["path"] = str(path)
    return result


def import_text_source_file(
    db_path: Path | str,
    path: Path | str,
    *,
    name: str,
    source_id: str | None = None,
    short_name: str | None = None,
    source_type: str = "private_policy",
    authority: str | None = None,
    binding_scope: str | None = None,
    jurisdiction: str | None = None,
    effective_at: str | None = None,
    repealed_at: str | None = None,
    source_url: str | None = None,
    source_name: str | None = None,
    source_checked_at: str | None = None,
    source_hash: str | None = None,
    aliases: list[str] | None = None,
    metadata: dict | None = None,
    dry_run: bool = False,
) -> dict:
    source_path = Path(path)
    text = read_source_text(source_path)
    generated_metadata = {
        "ingest": {
            "path": str(source_path),
            "format": source_path.suffix.lower().lstrip(".") or "text",
        }
    }
    merged_metadata = _merge_metadata(generated_metadata, metadata)
    payload = build_source_from_text(
        text,
        name=name,
        source_id=source_id,
        short_name=short_name,
        source_type=source_type,
        authority=authority,
        binding_scope=binding_scope,
        jurisdiction=jurisdiction,
        effective_at=effective_at,
        repealed_at=repealed_at,
        source_url=source_url,
        source_name=source_name or str(source_path),
        source_checked_at=source_checked_at,
        source_hash=source_hash,
        aliases=aliases,
        metadata=merged_metadata,
    )
    clauses = payload.get("clauses") or []
    warnings = analyze_split_quality(text, clauses)

    if dry_run:
        previews: list[dict] = []
        for index, clause in enumerate(clauses, start=1):
            text_body = (clause.get("text") or "").strip().replace("\n", " ")
            previews.append(
                {
                    "position": index,
                    "number": clause.get("number"),
                    "number_display": clause.get("number_display"),
                    "title": clause.get("title"),
                    "preview": text_body[:120],
                    "char_count": len(clause.get("text") or ""),
                }
            )
        return {
            "kind": "norm_ingest_preview",
            "path": str(source_path),
            "ingest_format": merged_metadata["ingest"]["format"],
            "name": name,
            "short_name": short_name,
            "id": source_id,
            "aliases": aliases or [],
            "metadata": merged_metadata,
            "clause_count": len(clauses),
            "warnings": warnings,
            "clauses": previews,
            "dry_run": True,
        }

    with connect(db_path) as conn:
        result = import_source_from_dict(conn, payload)
    result["path"] = str(source_path)
    result["ingest_format"] = merged_metadata["ingest"]["format"]
    result["warnings"] = warnings
    return result


def list_sources(db_path: Path | str) -> list[dict]:
    with connect(db_path) as conn:
        migrate(conn)
        rows = conn.execute(
            """
            SELECT s.*, COUNT(c.id) AS clause_count
            FROM norm_sources s
            LEFT JOIN norm_clauses c ON c.norm_source_id = s.id
            GROUP BY s.id
            ORDER BY s.name ASC
            """
        ).fetchall()
        result = []
        for row in rows:
            item = _source_row_to_dict(row)
            item["clause_count"] = row["clause_count"]
            result.append(item)
        return result


def get_source(db_path: Path | str, identifier: str) -> dict | None:
    with connect(db_path) as conn:
        migrate(conn)
        row = _resolve_source_row(conn, identifier)
        if row is None:
            return None
        source = _source_row_to_dict(row)
        clauses = conn.execute(
            """
            SELECT *
            FROM norm_clauses
            WHERE norm_source_id = ?
            ORDER BY position ASC
            """,
            (row["id"],),
        ).fetchall()
        source["clauses"] = [_clause_row_to_dict(clause) for clause in clauses]
        source["clause_count"] = len(clauses)
        return source


def get_clause(db_path: Path | str, identifier: str, number: str) -> dict | None:
    norm = normalize_clause_number(number)
    if not norm:
        return None
    with connect(db_path) as conn:
        migrate(conn)
        row = _resolve_source_row(conn, identifier)
        if row is None:
            return None
        source = _source_row_to_dict(row)
        clause = conn.execute(
            """
            SELECT *
            FROM norm_clauses
            WHERE norm_source_id = ? AND number = ?
            """,
            (row["id"], norm),
        ).fetchone()
        if clause is not None:
            return {
                "source": source,
                "clause": _clause_row_to_dict(clause),
                "requested_number": number,
                "match_strategy": "number",
            }
        clauses = conn.execute(
            """
            SELECT *
            FROM norm_clauses
            WHERE norm_source_id = ? AND (number_display = ? OR title = ?)
            LIMIT 1
            """,
            (row["id"], number, number),
        ).fetchone()
        if clauses is not None:
            return {
                "source": source,
                "clause": _clause_row_to_dict(clauses),
                "requested_number": number,
                "match_strategy": "display_or_title",
            }

        # Position 兜底：number 为纯正整数时按"第 N 项"语义取第 N 条
        # 对齐 `norm show` 的"项"显示语义；命中时通过 match_strategy 标记区分
        if norm.isdigit():
            position = int(norm)
            if position >= 1:
                by_position = conn.execute(
                    """
                    SELECT *
                    FROM norm_clauses
                    WHERE norm_source_id = ? AND position = ?
                    """,
                    (row["id"], position),
                ).fetchone()
                if by_position is not None:
                    return {
                        "source": source,
                        "clause": _clause_row_to_dict(by_position),
                        "requested_number": number,
                        "match_strategy": "position",
                    }

        return {
            "source": source,
            "clause": None,
            "requested_number": number,
            "match_strategy": None,
        }


def export_source(db_path: Path | str, identifier: str) -> dict | None:
    source = get_source(db_path, identifier)
    if source is None:
        return None
    return {
        "kind": "norm_source",
        "id": source["id"],
        "name": source["name"],
        "short_name": source.get("short_name"),
        "aliases": source.get("aliases", []),
        "source_type": source.get("source_type"),
        "authority": source.get("authority"),
        "binding_scope": source.get("binding_scope"),
        "jurisdiction": source.get("jurisdiction"),
        "effective_at": source.get("effective_at"),
        "repealed_at": source.get("repealed_at"),
        "source_url": source.get("source_url"),
        "source_name": source.get("source_name"),
        "source_checked_at": source.get("source_checked_at"),
        "source_hash": source.get("source_hash"),
        "metadata": source.get("metadata", {}),
        "clauses": source.get("clauses", []),
    }
