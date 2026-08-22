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

from chinalaw.contracts import validate_norm_source_type_value
from chinalaw.db import connect, migrate
from chinalaw.models import (
    LEGACY_NORM_SOURCE_TYPE_MAP,
    NormSourceType,
    normalize_norm_source_type,
)
from chinalaw.resource_limits import (
    ensure_file_size,
    read_zip_member_limited,
    run_limited,
    validate_zip_archive,
)
from chinalaw.search_indexes import (
    delete_norm_clause_search_indexes,
    delete_norm_source_search_index,
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
    source_type: str = "internal_governance",
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
    # 存量库可能残留已废弃的旧 source_type：输出归一后的新值并附原值。
    source_type, legacy_source_type = normalize_norm_source_type(row["source_type"])
    item = {
        "kind": "norm_source",
        "id": row["id"],
        "name": row["name"],
        "short_name": row["short_name"],
        "aliases": aliases,
        "source_type": source_type,
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
    if legacy_source_type is not None:
        item["legacy_source_type"] = legacy_source_type
    return item


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


def _record_norm_revision(
    conn: sqlite3.Connection,
    source_id: str,
    snapshot: dict,
) -> int:
    """把一次成功导入的规范化 payload 落入 ``norm_source_revisions``。

    快照是 rebuild 脱离原文件、history / diff 的唯一事实来源；写入失败必须
    fail loud（同事务回滚导入），不允许静默吞掉。返回新 revision 号。
    """

    row = conn.execute(
        "SELECT COALESCE(MAX(revision), 0) FROM norm_source_revisions "
        "WHERE norm_source_id = ?",
        (source_id,),
    ).fetchone()
    next_revision = int(row[0]) + 1
    conn.execute(
        """
        INSERT INTO norm_source_revisions (
            norm_source_id, revision, snapshot_json
        ) VALUES (?, ?, ?)
        """,
        (
            source_id,
            next_revision,
            json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")),
        ),
    )
    return next_revision


def get_latest_revision_snapshot(
    conn: sqlite3.Connection,
    source_id: str,
) -> dict | None:
    """取某私域规范最新 revision 的规范化 payload；无快照或快照损坏返回 None。"""

    row = conn.execute(
        """
        SELECT snapshot_json
        FROM norm_source_revisions
        WHERE norm_source_id = ?
        ORDER BY revision DESC
        LIMIT 1
        """,
        (source_id,),
    ).fetchone()
    if row is None:
        return None
    try:
        payload = json.loads(row["snapshot_json"])
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def import_source_from_dict(conn: sqlite3.Connection, payload: dict) -> dict:
    migrate(conn)
    name = _clean_text(payload.get("name"))
    if not name:
        raise ValueError("norm source requires name")
    source_id = _clean_text(payload.get("id")) or _slug_id(name)
    clauses = payload.get("clauses")
    if not isinstance(clauses, list):
        raise ValueError("norm source requires clauses list")
    # source_type 是受控枚举：已废弃的旧值按 LEGACY 映射归一并继续导入
    # （返回 payload 附 deprecation_warning），完全未知的值 fail loud，
    # 缺省落 internal_governance。
    source_type = _clean_text(payload.get("source_type"))
    legacy_source_type: str | None = None
    if source_type is not None:
        mapped = LEGACY_NORM_SOURCE_TYPE_MAP.get(source_type)
        if mapped is not None:
            legacy_source_type = source_type
            source_type = mapped
        source_type = validate_norm_source_type_value(source_type)
    else:
        source_type = NormSourceType.INTERNAL_GOVERNANCE.value
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
    source_name = _clean_text(payload.get("source_name")) or "local-file"
    source_url_value = _clean_text(payload.get("source_url"))
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
            source_type,
            _clean_text(payload.get("authority")),
            _clean_text(payload.get("binding_scope")),
            _clean_text(payload.get("jurisdiction")),
            _clean_text(payload.get("effective_at")),
            _clean_text(payload.get("repealed_at")),
            source_url_value,
            source_name,
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

    # 同事务落全量规范化快照：rebuild 脱离原文件、history/diff 的事实来源。
    snapshot = {
        "id": source_id,
        "name": name,
        "short_name": _clean_text(payload.get("short_name")),
        "aliases": aliases,
        "source_type": source_type,
        "authority": _clean_text(payload.get("authority")),
        "binding_scope": _clean_text(payload.get("binding_scope")),
        "jurisdiction": _clean_text(payload.get("jurisdiction")),
        "effective_at": _clean_text(payload.get("effective_at")),
        "repealed_at": _clean_text(payload.get("repealed_at")),
        "source_url": source_url_value,
        "source_name": source_name,
        "source_checked_at": source_checked_at,
        "source_hash": source_hash,
        "metadata": metadata,
        "clauses": [
            {
                "number": clause["number"],
                "number_display": clause["number_display"],
                "title": clause["title"],
                "text": clause["text"],
                "position": clause["position"],
            }
            for clause in normalized_clauses
        ],
    }
    revision = _record_norm_revision(conn, source_id, snapshot)

    result = {
        "kind": "norm_source_import",
        "source_id": source_id,
        "name": name,
        "clauses_loaded": len(normalized_clauses),
        "source_type": source_type,
        "revision": revision,
    }
    if legacy_source_type is not None:
        # fail loud 精神：旧值映射显式告知，不静默吞掉。
        result["legacy_source_type"] = legacy_source_type
        result["deprecation_warning"] = (
            f"source_type 旧值 {legacy_source_type!r} 已废弃，"
            f"已自动映射为 {source_type!r}；"
            "请改用新受控枚举值（见 docs/CONTRACT.md §2.9）。"
        )
    return result


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
    source_type: str = "internal_governance",
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


NORM_EXPORT_NOTICE = "私域规范数据，请勿上传公开仓库或外部服务"


def export_source(
    db_path: Path | str,
    identifier: str,
    *,
    metadata_only: bool = False,
) -> dict | None:
    """导出私域规范。``metadata_only=True`` 时仅导出元数据与条款号/标题清单，
    不含条款正文 text。"""
    source = get_source(db_path, identifier)
    if source is None:
        return None
    clauses = source.get("clauses", [])
    if metadata_only:
        clauses = [
            {key: value for key, value in clause.items() if key != "text"}
            for clause in clauses
        ]
    return {
        "kind": "norm_source",
        "sensitivity": "private",
        "notice": NORM_EXPORT_NOTICE,
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
        "clauses": clauses,
    }


def delete_source(db_path: Path | str, identifier: str) -> dict | None:
    """删除一个私域规范及其 clauses / 快照 / FTS 索引。

    无交互确认（仓库先例）；调用方负责确认。未命中返回 None。
    删除顺序：clause FTS → source FTS → ``DELETE FROM norm_sources``
    （ON DELETE CASCADE 连带清理 norm_clauses / norm_source_revisions /
    *_fts_rows 映射行）。
    """

    with connect(db_path) as conn:
        migrate(conn)
        row = _resolve_source_row(conn, identifier)
        if row is None:
            return None
        source_id = row["id"]
        clauses_deleted = int(
            conn.execute(
                "SELECT COUNT(*) FROM norm_clauses WHERE norm_source_id = ?",
                (source_id,),
            ).fetchone()[0]
        )
        revisions_deleted = int(
            conn.execute(
                "SELECT COUNT(*) FROM norm_source_revisions WHERE norm_source_id = ?",
                (source_id,),
            ).fetchone()[0]
        )
        delete_norm_clause_search_indexes(conn, source_id)
        delete_norm_source_search_index(conn, source_id)
        conn.execute("DELETE FROM norm_sources WHERE id = ?", (source_id,))
        return {
            "kind": "norm_source_delete",
            "ok": True,
            "id": source_id,
            "name": row["name"],
            "clauses_deleted": clauses_deleted,
            "revisions_deleted": revisions_deleted,
        }


def _revision_row_to_item(row: sqlite3.Row) -> dict:
    snapshot: dict = {}
    try:
        decoded = json.loads(row["snapshot_json"])
        if isinstance(decoded, dict):
            snapshot = decoded
    except json.JSONDecodeError:
        snapshot = {}
    return {
        "revision": row["revision"],
        "created_at": row["created_at"],
        "clause_count": len(snapshot.get("clauses") or []),
        "source_hash": snapshot.get("source_hash"),
    }


def list_revisions(db_path: Path | str, identifier: str) -> dict | None:
    """列出一个私域规范的全部快照 revision（norm history）。"""

    with connect(db_path) as conn:
        migrate(conn)
        row = _resolve_source_row(conn, identifier)
        if row is None:
            return None
        revisions = [
            _revision_row_to_item(revision_row)
            for revision_row in conn.execute(
                """
                SELECT revision, snapshot_json, created_at
                FROM norm_source_revisions
                WHERE norm_source_id = ?
                ORDER BY revision ASC
                """,
                (row["id"],),
            ).fetchall()
        ]
        return {
            "kind": "norm_source_history",
            "id": row["id"],
            "name": row["name"],
            "revision_count": len(revisions),
            "revisions": revisions,
        }


def _load_revision_snapshot(
    conn: sqlite3.Connection,
    source_id: str,
    revision: int,
) -> dict | None:
    row = conn.execute(
        """
        SELECT snapshot_json
        FROM norm_source_revisions
        WHERE norm_source_id = ? AND revision = ?
        """,
        (source_id, revision),
    ).fetchone()
    if row is None:
        return None
    try:
        payload = json.loads(row["snapshot_json"])
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _current_source_payload(conn: sqlite3.Connection, row: sqlite3.Row) -> dict:
    payload = _source_row_to_dict(row)
    payload["clauses"] = [
        _clause_row_to_dict(clause)
        for clause in conn.execute(
            """
            SELECT *
            FROM norm_clauses
            WHERE norm_source_id = ?
            ORDER BY position ASC
            """,
            (row["id"],),
        ).fetchall()
    ]
    return payload


def compare_norm_clause_payloads(before: dict, after: dict) -> dict:
    """按 position 对齐比较两份规范化 norm payload 的条款差异。

    与 ``rebuild._compare_norm_payloads`` 同一对齐语义（位置即事实），但输出
    面向 ``norm diff``：增 / 删 / 改计数 + 变更条款号清单。
    """

    def _clauses(payload: dict) -> list[dict]:
        out: list[dict] = []
        for position, clause in enumerate(payload.get("clauses") or [], start=1):
            out.append(
                {
                    "number": normalize_clause_number(clause.get("number")),
                    "number_display": clause.get("number_display")
                    or clause.get("number"),
                    "title": clause.get("title"),
                    "text": (clause.get("text") or "").strip(),
                    "position": position,
                }
            )
        return out

    def _label(clause: dict) -> str:
        return (
            clause.get("number_display")
            or clause.get("number")
            or f"第 {clause.get('position')} 项"
        )

    before_clauses = _clauses(before)
    after_clauses = _clauses(after)
    max_len = max(len(before_clauses), len(after_clauses))
    added: list[str] = []
    removed: list[str] = []
    modified: list[str] = []
    for index in range(max_len):
        before_clause = before_clauses[index] if index < len(before_clauses) else None
        after_clause = after_clauses[index] if index < len(after_clauses) else None
        if before_clause is None:
            added.append(_label(after_clause))
            continue
        if after_clause is None:
            removed.append(_label(before_clause))
            continue
        if (
            before_clause["text"] != after_clause["text"]
            or (before_clause["number"], before_clause["number_display"])
            != (after_clause["number"], after_clause["number_display"])
            or (before_clause.get("title") or "")
            != (after_clause.get("title") or "")
        ):
            modified.append(_label(after_clause))
    return {
        "changed": bool(added or removed or modified),
        "clause_count_before": len(before_clauses),
        "clause_count_after": len(after_clauses),
        "added_count": len(added),
        "removed_count": len(removed),
        "modified_count": len(modified),
        "added": added,
        "removed": removed,
        "modified": modified,
    }


def diff_revisions(
    db_path: Path | str,
    identifier: str,
    *,
    from_revision: int | None = None,
    to_revision: int | None = None,
) -> dict | None:
    """``norm diff``：默认比最新 revision 与当前库内容；显式 --from/--to 则比两个
    历史 revision。未命中规范返回 None；revision 不存在返回带 error 的 payload。"""

    with connect(db_path) as conn:
        migrate(conn)
        row = _resolve_source_row(conn, identifier)
        if row is None:
            return None
        source_id = row["id"]

        base = {
            "kind": "norm_source_diff",
            "id": source_id,
            "name": row["name"],
        }
        if from_revision is None and to_revision is None:
            latest = conn.execute(
                """
                SELECT revision
                FROM norm_source_revisions
                WHERE norm_source_id = ?
                ORDER BY revision DESC
                LIMIT 1
                """,
                (source_id,),
            ).fetchone()
            if latest is None:
                return {
                    **base,
                    "error": "no_revisions",
                    "message": "该私域规范没有任何快照，无法 diff",
                }
            before = _load_revision_snapshot(conn, source_id, int(latest["revision"]))
            after = _current_source_payload(conn, row)
            from_label: str | int = int(latest["revision"])
            to_label: str | int = "current"
        else:
            if from_revision is None or to_revision is None:
                raise ValueError("norm diff requires both --from and --to")
            before = _load_revision_snapshot(conn, source_id, int(from_revision))
            after = _load_revision_snapshot(conn, source_id, int(to_revision))
            if before is None or after is None:
                missing = [
                    str(rev)
                    for rev, snap in ((from_revision, before), (to_revision, after))
                    if snap is None
                ]
                return {
                    **base,
                    "error": "revision_not_found",
                    "message": f"revision 不存在：{', '.join(missing)}",
                }
            from_label = int(from_revision)
            to_label = int(to_revision)

        comparison = compare_norm_clause_payloads(before, after)
        return {
            **base,
            "from": from_label,
            "to": to_label,
            **comparison,
        }
