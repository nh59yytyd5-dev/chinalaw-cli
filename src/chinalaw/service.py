"""Service 层：封装检索与读取逻辑。

对上：供 cli.py（及未来的 SDK / MCP adapter）调用，入参出参均为 JSON 安全的 Python dict。
对下：直接用 sqlite3 打开 DB 查询，不再经过 ORM。
"""

from __future__ import annotations

import json
import os
import re
import shlex
import sqlite3
from datetime import date, datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path

from chinalaw.aliases import display_short_title, merge_law_aliases
from chinalaw.db import connect, get_meta, migrate

# ---------- 辅助 ----------

_CHINESE_NUMERALS = {
    "零": 0, "〇": 0,
    "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "两": 2,
    "十": 10, "百": 100, "千": 1000, "万": 10000,
}


# GB/T 规定的嵌套书名号 〈〉 与外层 《》 在民间引用中混用极广。
# DB 入库统一用《》，因此在 resolve 入口对外层不可见的 〈〉 做一次保守替换：
# 仅当输入既无《也无》时不动；其他情况一律把 〈→《、〉→》。
# 这是 punctuation normalization，不是 alias —— 不会把 A 法识别成 B 法。
_NESTED_BRACKET_PAIRS = (("〈", "《"), ("〉", "》"))


def normalize_law_identifier(raw: str | None) -> str:
    """归一化用户传入的法规标识。

    目前只处理嵌套书名号 〈〉 → 《》 这一类纯标点变体，
    不做任何"近似匹配 / 别名映射"——那是 aliases 表的职责。
    """

    if raw is None:
        return ""
    text = str(raw).strip()
    if not text:
        return ""
    for src, dst in _NESTED_BRACKET_PAIRS:
        if src in text:
            text = text.replace(src, dst)
    return text


def normalize_article_number(raw: str) -> str:
    """把条款号归一为统一格式。

    普通条款归一为阿拉伯数字串，如：
    - '第七十一条' -> '71'
    - '第71条' -> '71'

    插入条款归一为 '<base>-<inserted>'，如：
    - '第十四条之一' -> '14-1'
    - '第14条之1' -> '14-1'
    - '14-1' -> '14-1'
    """
    if raw is None:
        return ""
    s = re.sub(r"\s+", "", str(raw))
    if not s:
        return ""

    dotted = re.fullmatch(r"第?(?P<number>[0-9]+(?:\.[0-9]+)+)条?", s)
    if dotted:
        return dotted.group("number")

    inserted = re.fullmatch(
        r"第?(?P<base>[0-9]+|[〇零一二三四五六七八九十百千万两]+)条之"
        r"(?P<inserted>[0-9]+|[〇零一二三四五六七八九十百千万两]+)",
        s,
    )
    if inserted:
        base = _number_like_to_arabic(inserted.group("base"))
        suffix = _number_like_to_arabic(inserted.group("inserted"))
        if base and suffix:
            return f"{base}-{suffix}"

    inserted_numeric = re.fullmatch(r"第?(?P<base>[0-9]+)[-－—](?P<inserted>[0-9]+)条?", s)
    if inserted_numeric:
        return f"{int(inserted_numeric.group('base'))}-{int(inserted_numeric.group('inserted'))}"

    return _number_like_to_arabic(s)


def _number_like_to_arabic(raw: str) -> str:
    s = str(raw).replace("第", "").replace("条", "").strip()
    if not s:
        return ""
    digits = re.sub(r"[^0-9]", "", s)
    if digits and not re.search(r"[\u4e00-\u9fff]", s):
        return str(int(digits))
    if re.search(r"[\u4e00-\u9fff]", s):
        return _chinese_to_arabic(s)
    return s


def _chinese_to_arabic(s: str) -> str:
    s = s.replace("第", "").replace("条", "").strip()
    total = 0
    current = 0
    for ch in s:
        v = _CHINESE_NUMERALS.get(ch)
        if v is None:
            continue
        if v >= 10:
            if current == 0:
                current = 1
            total += current * v
            current = 0
        else:
            current = v
    total += current
    return str(total) if total else s


def _articles_coverage(article_count: int | None, status: str | None = None) -> str:
    """根据条款数量派生数据覆盖状态。

    "stub"      = 法规已建索引但条款未入库（仅 metadata）
    "seed"      = 只有少量核心条款，不保证完整
    "populated" = 至少有 1 条条款（agent 可正常引用）
    """
    if status == "seed":
        return "seed"
    if article_count is None:
        return "unknown"
    return "populated" if article_count > 0 else "stub"


def _row_to_law(row: sqlite3.Row, *, article_count: int | None = None) -> dict:
    aliases = row["aliases"]
    try:
        aliases_list = json.loads(aliases) if aliases else []
    except json.JSONDecodeError:
        aliases_list = []
    title = row["title"]
    short_title = display_short_title(title, row["short_title"])
    aliases_list = merge_law_aliases(title, short_title, aliases_list)
    payload = {
        "id": row["id"],
        "title": title,
        "short_title": short_title,
        "aliases": aliases_list,
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
        "freshness_days": _freshness_days(row["source_checked_at"]),
    }
    if article_count is not None:
        payload["article_count"] = article_count
        payload["articles_coverage"] = _articles_coverage(article_count, row["status"])
    return payload


def _count_articles_for_law(conn: sqlite3.Connection, law_id: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) FROM articles WHERE law_id = ?", (law_id,)
    ).fetchone()
    return int(row[0]) if row else 0


_LAW_RESOLUTION_ORDER = """
    CASE WHEN l.status = 'seed' THEN 1 ELSE 0 END,
    COUNT(a.id) DESC,
    CASE l.status
        WHEN 'current' THEN 0
        WHEN 'amended' THEN 1
        WHEN 'pending_effective' THEN 2
        WHEN 'repealed' THEN 3
        ELSE 4
    END,
    COALESCE(l.effective_at, l.released_at, '') DESC,
    LENGTH(l.title) ASC,
    l.rowid DESC
"""

_SHORT_NORMATIVE_SUFFIXES = (
    "法",
    "条例",
    "规定",
    "解释",
    "规则",
    "办法",
    "决定",
    "通知",
    "纪要",
    "意见",
    "批复",
)


def _row_to_article(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "law_id": row["law_id"],
        "number": row["number"],
        "number_display": row["number_display"],
        "part": row["part"],
        "title": row["title"],
        "text": row["text"],
        "position": row["position"],
    }


def _row_to_revision(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "law_id": row["law_id"],
        "version_label": row["version_label"],
        "released_at": row["released_at"],
        "effective_at": row["effective_at"],
        "notes": row["notes"],
        "content_hash": row["content_hash"],
        "snapshot_json": row["snapshot_json"],
    }


def _revision_without_snapshot(revision: dict | None) -> dict | None:
    if revision is None:
        return None
    public = dict(revision)
    public.pop("snapshot_json", None)
    return public


def _law_without_revision_snapshots(law: dict | None) -> dict | None:
    if law is None:
        return None
    public = dict(law)
    if "revisions" in public:
        public["revisions"] = [
            _revision_without_snapshot(revision)
            for revision in public.get("revisions") or []
        ]
    if "current_revision" in public:
        public["current_revision"] = _revision_without_snapshot(
            public.get("current_revision")
        )
    if "selected_revision" in public:
        public["selected_revision"] = _revision_without_snapshot(
            public.get("selected_revision")
        )
    return public


def _law_reference_payload(law: dict | None) -> dict | None:
    public = _law_without_revision_snapshots(law)
    if public is not None:
        public.pop("articles", None)
    return public


def _row_to_category(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "parent_id": row["parent_id"],
        "description": row["description"],
    }


def _row_to_norm_source(row: sqlite3.Row) -> dict:
    aliases = row["aliases"]
    metadata_json = row["metadata_json"]
    try:
        aliases_list = json.loads(aliases) if aliases else []
    except json.JSONDecodeError:
        aliases_list = []
    try:
        metadata = json.loads(metadata_json) if metadata_json else {}
    except json.JSONDecodeError:
        metadata = {}
    return {
        "id": row["id"],
        "name": row["name"],
        "short_name": row["short_name"],
        "aliases": aliases_list,
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
        "freshness_days": _freshness_days(row["source_checked_at"]),
    }


def _resolve_norm_source_row(
    conn: sqlite3.Connection, identifier: str
) -> sqlite3.Row | None:
    """Resolve a private norm source by id / name / short_name / alias."""
    identifier = identifier.strip()
    if not identifier:
        return None
    exact_alias_pattern = _like_pattern(json.dumps(identifier, ensure_ascii=False))
    fuzzy_pattern = _like_pattern(identifier)
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
            END,
            LENGTH(name) ASC
        LIMIT 1
        """,
        (
            identifier,
            identifier,
            identifier,
            exact_alias_pattern,
            identifier,
            identifier,
            identifier,
        ),
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
        (fuzzy_pattern, fuzzy_pattern, fuzzy_pattern),
    ).fetchone()


def _norm_source_row_to_law_shape(row: sqlite3.Row) -> dict:
    """Wrap a norm_source row as a law-shaped dict so existing formatters work."""
    try:
        aliases = json.loads(row["aliases"]) if row["aliases"] else []
    except (json.JSONDecodeError, TypeError):
        aliases = []
    name = row["name"]
    short_name = row["short_name"] or name
    return {
        "id": row["id"],
        "title": name,
        "short_title": short_name,
        "aliases": aliases,
        "level": row["source_type"] or "norm_source",
        "status": "active",
        "issuing_body": row["authority"],
        "document_number": None,
        "released_at": row["effective_at"],
        "effective_at": row["effective_at"],
        "repealed_at": row["repealed_at"],
        "source_url": row["source_url"],
        "source_name": row["source_name"],
        "source_checked_at": row["source_checked_at"],
        "source_hash": row["source_hash"],
        "freshness_days": _freshness_days(row["source_checked_at"]),
        "via": "norm_fallback",
    }


def _norm_clause_row_to_article_shape(row: sqlite3.Row, source_id: str) -> dict:
    """Wrap a norm_clause row as an article-shaped dict."""
    return {
        "id": row["id"],
        "law_id": source_id,
        "number": row["number"],
        "number_display": row["number_display"],
        "part": None,
        "title": row["title"],
        "text": row["text"],
        "position": row["position"],
        "via": "norm_fallback",
    }


def _fetch_norm_clause_row(
    conn: sqlite3.Connection, source_id: str, normalized_number: str
) -> sqlite3.Row | None:
    if not normalized_number:
        return None
    return conn.execute(
        """
        SELECT *
        FROM norm_clauses
        WHERE norm_source_id = ? AND number = ?
        LIMIT 1
        """,
        (source_id, normalized_number),
    ).fetchone()


def _row_to_norm_clause(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "norm_source_id": row["norm_source_id"],
        "number": row["number"],
        "number_display": row["number_display"],
        "title": row["title"],
        "text": row["text"],
        "position": row["position"],
    }


def _freshness_days(iso: str | None) -> int | None:
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - dt
    return max(delta.days, 0)


def _parse_iso_date(raw: str | None) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _split_search_terms(raw: str) -> list[str]:
    return [term for term in re.split(r"\s+", raw.strip()) if term]


def _should_use_fts(terms: list[str]) -> bool:
    return bool(terms) and all(len(term) >= 3 for term in terms)


def _to_fts_query(raw: str) -> str:
    terms = []
    for term in _split_search_terms(raw):
        cleaned = term.replace('"', " ").strip()
        if cleaned:
            terms.append(f'"{cleaned}"')
    if not terms:
        return '""'
    return " AND ".join(terms)


def _like_pattern(raw: str) -> str:
    escaped = raw.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")
    return f"%{escaped}%"


def _looks_like_short_normative_name(raw: str) -> bool:
    text = re.sub(r"\s+", "", raw or "")
    return 2 <= len(text) <= 4 and any(
        text.endswith(suffix) for suffix in _SHORT_NORMATIVE_SUFFIXES
    )


def _build_like_all_clause(column: str, terms: list[str]) -> tuple[str, list[str]]:
    clauses = [f"{column} LIKE ? ESCAPE '\\'" for _ in terms]
    params = [_like_pattern(term) for term in terms]
    return " AND ".join(clauses), params


def _build_law_like_clause(terms: list[str]) -> tuple[str, list[str]]:
    clauses: list[str] = []
    params: list[str] = []
    for term in terms:
        pattern = _like_pattern(term)
        clauses.append(
            "(l.title LIKE ? ESCAPE '\\' OR "
            "l.short_title LIKE ? ESCAPE '\\' OR "
            "l.aliases LIKE ? ESCAPE '\\')"
        )
        params.extend((pattern, pattern, pattern))
    return " AND ".join(clauses), params


def _build_norm_source_like_clause(terms: list[str]) -> tuple[str, list[str]]:
    clauses: list[str] = []
    params: list[str] = []
    for term in terms:
        pattern = _like_pattern(term)
        clauses.append(
            "(n.name LIKE ? ESCAPE '\\' OR "
            "n.short_name LIKE ? ESCAPE '\\' OR "
            "n.aliases LIKE ? ESCAPE '\\' OR "
            "n.authority LIKE ? ESCAPE '\\')"
        )
        params.extend((pattern, pattern, pattern, pattern))
    return " AND ".join(clauses), params


def _resolve_law_row_with_via(
    conn: sqlite3.Connection,
    identifier: str,
) -> tuple[sqlite3.Row | None, str | None]:
    """Like ``_resolve_law_row`` but also returns which path matched.

    via 取值参见 ``docs/ALIAS_SYSTEM_SPEC.md`` §3.2.2：
    ``id_match`` / ``title_match`` / ``short_title_match`` / ``alias_exact``
    / ``alias_derived`` / ``like_fallback``。
    """

    identifier = normalize_law_identifier(identifier)
    if not identifier:
        return None, None
    exact_alias_pattern = _like_pattern(json.dumps(identifier, ensure_ascii=False))
    fuzzy_pattern = _like_pattern(identifier)

    row = conn.execute(
        f"""
        SELECT l.*
        FROM laws l
        LEFT JOIN articles a ON a.law_id = l.id
        WHERE l.id = ? OR l.title = ? OR l.short_title = ? OR l.aliases LIKE ? ESCAPE '\\'
        GROUP BY l.id
        ORDER BY
            CASE
                WHEN l.id = ? THEN 0
                WHEN l.title = ? THEN 1
                WHEN l.short_title = ? THEN 2
                ELSE 3
            END,
            {_LAW_RESOLUTION_ORDER}
        LIMIT 1
        """,
        (
            identifier,
            identifier,
            identifier,
            exact_alias_pattern,
            identifier,
            identifier,
            identifier,
        ),
    ).fetchone()
    if row is not None:
        if row["id"] == identifier:
            return row, "id_match"
        if row["title"] == identifier:
            return row, "title_match"
        if row["short_title"] == identifier:
            return row, "short_title_match"
        return row, "alias_exact"

    row = _resolve_law_row_by_derived_alias(conn, identifier)
    if row is not None:
        return row, "alias_derived"

    if _looks_like_short_normative_name(identifier):
        return None, None

    row = conn.execute(
        f"""
        SELECT l.*
        FROM laws l
        LEFT JOIN articles a ON a.law_id = l.id
        WHERE l.title LIKE ? ESCAPE '\\'
           OR l.short_title LIKE ? ESCAPE '\\'
           OR l.aliases LIKE ? ESCAPE '\\'
        GROUP BY l.id
        ORDER BY {_LAW_RESOLUTION_ORDER}
        LIMIT 1
        """,
        (fuzzy_pattern, fuzzy_pattern, fuzzy_pattern),
    ).fetchone()
    if row is not None:
        return row, "like_fallback"
    return None, None


def _resolve_law_row(conn: sqlite3.Connection, identifier: str) -> sqlite3.Row | None:
    row, _via = _resolve_law_row_with_via(conn, identifier)
    return row


def _aliases_for_law_row(row: sqlite3.Row) -> list[str]:
    try:
        aliases = json.loads(row["aliases"]) if row["aliases"] else []
    except json.JSONDecodeError:
        aliases = []
    short_title = display_short_title(row["title"], row["short_title"])
    return merge_law_aliases(row["title"], short_title, aliases)


def _resolve_law_row_by_derived_alias(
    conn: sqlite3.Connection,
    identifier: str,
) -> sqlite3.Row | None:
    rows = conn.execute(
        f"""
        SELECT l.*
        FROM laws l
        LEFT JOIN articles a ON a.law_id = l.id
        GROUP BY l.id
        ORDER BY {_LAW_RESOLUTION_ORDER}
        """
    ).fetchall()
    for row in rows:
        if identifier in _aliases_for_law_row(row):
            return row
    return None


def _fetch_revisions(conn: sqlite3.Connection, law_id: str) -> list[dict]:
    rows = conn.execute(
        """
        SELECT *
        FROM revisions
        WHERE law_id = ?
        ORDER BY COALESCE(effective_at, released_at, '') DESC, rowid DESC
        """,
        (law_id,),
    ).fetchall()
    return [_row_to_revision(row) for row in rows]


def _fetch_categories_for_law(conn: sqlite3.Connection, law_id: str) -> list[dict]:
    rows = conn.execute(
        """
        SELECT c.*
        FROM categories c
        JOIN law_categories lc ON lc.category_id = c.id
        WHERE lc.law_id = ?
        ORDER BY c.id
        """,
        (law_id,),
    ).fetchall()
    categories = [_row_to_category(row) for row in rows]
    filtered = [
        category
        for category in categories
        if category.get("name") and category.get("name") != "未命名分类"
    ]
    deduped: list[dict] = []
    last_name = None
    for category in filtered:
        if category["name"] == last_name:
            continue
        deduped.append(category)
        last_name = category["name"]
    return deduped


def _law_summary_by_id(conn: sqlite3.Connection, law_id: str | None) -> dict | None:
    if not law_id:
        return None
    row = conn.execute("SELECT * FROM laws WHERE id = ?", (law_id,)).fetchone()
    if row is None:
        return None
    return _row_to_law(row, article_count=_count_articles_for_law(conn, law_id))


def _find_law_siblings(db_path: Path | str, law: dict, *, limit: int = 5) -> list[dict]:
    """Find same-title / same-short-title candidates for alias collision recovery."""

    law_id = law.get("id")
    values = [
        value
        for value in (
            law.get("title"),
            law.get("short_title"),
            *(law.get("aliases") or []),
        )
        if isinstance(value, str) and value.strip()
    ]
    if not law_id or not values:
        return []

    exact_clauses: list[str] = []
    exact_params: list[str] = []
    alias_clauses: list[str] = []
    alias_params: list[str] = []
    for value in dict.fromkeys(values):
        exact_clauses.append("(l.title = ? OR l.short_title = ?)")
        exact_params.extend((value, value))
        alias_clauses.append("l.aliases LIKE ? ESCAPE '\\'")
        alias_params.append(_like_pattern(json.dumps(value, ensure_ascii=False)))
    where = " OR ".join([*exact_clauses, *alias_clauses])
    with connect(db_path) as conn:
        migrate(conn)
        rows = conn.execute(
            f"""
            SELECT l.*, COUNT(a.id) AS _article_count
            FROM laws l
            LEFT JOIN articles a ON a.law_id = l.id
            WHERE l.id <> ? AND ({where})
            GROUP BY l.id
            ORDER BY {_LAW_RESOLUTION_ORDER}
            LIMIT ?
            """,
            (law_id, *exact_params, *alias_params, limit),
        ).fetchall()
    return [
        _row_to_law(row, article_count=int(row["_article_count"] or 0))
        for row in rows
    ]


def _json_from_row(value: str | None) -> dict:
    if not value:
        return {}
    try:
        loaded = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _warning(code: str, message: str, *, severity: str = "warning") -> dict:
    return {
        "severity": severity,
        "code": code,
        "message": message,
    }


def _relation_row_to_dict(
    conn: sqlite3.Connection,
    row: sqlite3.Row,
    *,
    focus_law_id: str,
) -> dict:
    direction = "outgoing" if row["from_law_id"] == focus_law_id else "incoming"
    return {
        "id": row["id"],
        "relation_type": row["relation_type"],
        "direction": direction,
        "from_law_id": row["from_law_id"],
        "from_law_title": row["from_law_title"],
        "from_law": _law_summary_by_id(conn, row["from_law_id"]),
        "to_law_id": row["to_law_id"],
        "to_law_title": row["to_law_title"],
        "to_law": _law_summary_by_id(conn, row["to_law_id"]),
        "effective_at": row["effective_at"],
        "source_name": row["source_name"],
        "source_url": row["source_url"],
        "source_checked_at": row["source_checked_at"],
        "notes": row["notes"],
        "metadata": _json_from_row(row["metadata_json"]),
    }


def _law_needs_fetch(law: dict | None, law_id: str | None, law_title: str | None) -> dict | None:
    if not law_id:
        return None
    if law is None:
        return {
            "law_id": law_id,
            "law_title": law_title,
            "reason": "missing_law",
        }
    if law.get("articles_coverage") in {"stub", "seed"} or law.get("status") == "seed":
        return {
            "law_id": law_id,
            "law_title": law.get("title") or law_title,
            "reason": "seed_law" if law.get("status") == "seed" else "stub_law",
        }
    return None


def _rule_row_to_dict(conn: sqlite3.Connection, row: sqlite3.Row) -> dict:
    primary_law = _law_summary_by_id(conn, row["primary_law_id"])
    fallback_law = _law_summary_by_id(conn, row["fallback_law_id"])
    needs_fetch = [
        item for item in (
            _law_needs_fetch(primary_law, row["primary_law_id"], row["primary_law_title"]),
            _law_needs_fetch(fallback_law, row["fallback_law_id"], row["fallback_law_title"]),
        )
        if item is not None
    ]
    warnings = []
    for item in needs_fetch:
        if item["reason"] == "missing_law":
            warnings.append(
                _warning(
                    "law_missing",
                    f"{item['law_id']} 尚未入库，需要先 fetch / sync 后才能引用条文。",
                )
            )
        elif item["reason"] == "stub_law":
            warnings.append(
                _warning(
                    "law_stub",
                    f"{item['law_id']} 仅有 metadata，缺少条文正文，需要 fetch 补全。",
                )
            )
        elif item["reason"] == "seed_law":
            warnings.append(
                _warning(
                    "law_seed",
                    f"{item['law_id']} 仅为 seed 样例数据，不保证全文完整，需要 fetch 补全。",
                )
            )
    return {
        "id": row["id"],
        "topic": row["topic"],
        "domain": row["domain"],
        "primary_law_id": row["primary_law_id"],
        "primary_law_title": row["primary_law_title"],
        "primary_law": primary_law,
        "fallback_law_id": row["fallback_law_id"],
        "fallback_law_title": row["fallback_law_title"],
        "fallback_law": fallback_law,
        "effective_from": row["effective_from"],
        "effective_to": row["effective_to"],
        "rule_text": row["rule_text"],
        "transition_text": row["transition_text"],
        "source_name": row["source_name"],
        "source_url": row["source_url"],
        "source_checked_at": row["source_checked_at"],
        "confidence": row["confidence"],
        "metadata": _json_from_row(row["metadata_json"]),
        "needs_fetch": needs_fetch,
        "warnings": warnings,
    }


def _unique_warnings(warnings: list[dict]) -> list[dict]:
    seen: set[tuple[str, str]] = set()
    unique = []
    for warning in warnings:
        key = (warning.get("code", ""), warning.get("message", ""))
        if key in seen:
            continue
        seen.add(key)
        unique.append(warning)
    return unique


def _revision_sort_date(revision: dict) -> date | None:
    return _parse_iso_date(revision.get("effective_at")) or _parse_iso_date(
        revision.get("released_at")
    )


def _select_revision_as_of(revisions: list[dict], as_of: date) -> dict | None:
    applicable = [
        revision
        for revision in revisions
        if (_revision_sort_date(revision) is not None and _revision_sort_date(revision) <= as_of)
    ]
    if not applicable:
        return None
    applicable.sort(key=lambda revision: _revision_sort_date(revision) or date.min, reverse=True)
    return applicable[0]


def _snapshot_to_law(snapshot: dict) -> dict:
    return {
        "id": snapshot["id"],
        "title": snapshot["title"],
        "short_title": snapshot.get("short_title"),
        "aliases": snapshot.get("aliases", []),
        "level": snapshot["level"],
        "status": snapshot["status"],
        "issuing_body": snapshot.get("issuing_body"),
        "document_number": snapshot.get("document_number"),
        "released_at": snapshot.get("released_at"),
        "effective_at": snapshot.get("effective_at"),
        "repealed_at": snapshot.get("repealed_at"),
        "source_url": snapshot["source_url"],
        "source_name": snapshot.get("source_name", "unknown"),
        "source_checked_at": snapshot.get("source_checked_at"),
        "source_hash": snapshot.get("source_hash"),
        "freshness_days": _freshness_days(snapshot.get("source_checked_at")),
        "articles": snapshot.get("articles", []),
        "article_count": len(snapshot.get("articles", [])),
        "articles_coverage": _articles_coverage(
            len(snapshot.get("articles", [])), snapshot.get("status")
        ),
    }


def _build_law_from_revision_snapshot(
    law_row: sqlite3.Row,
    revisions: list[dict],
    revision: dict,
) -> dict | None:
    snapshot_json = revision.get("snapshot_json")
    if snapshot_json:
        snapshot = json.loads(snapshot_json)
        law = _snapshot_to_law(snapshot)
    elif revision.get("content_hash") == law_row["source_hash"]:
        law = _row_to_law(law_row, article_count=0)
        law["articles"] = []
        law["article_count"] = 0
    else:
        return None

    law["revisions"] = revisions
    law["revision_count"] = len(revisions)
    law["current_revision"] = revisions[0] if revisions else None
    law["selected_revision"] = revision
    return law


def _articles_by_number(articles: list[dict]) -> dict[str, dict]:
    return {article["number"]: article for article in articles if article.get("number")}


def _article_number_sort_key(value: str) -> tuple[int, str]:
    return len(value), value


def _compare_articles(before_law: dict, after_law: dict) -> dict:
    before_articles = _articles_by_number(before_law.get("articles", []))
    after_articles = _articles_by_number(after_law.get("articles", []))

    added = []
    removed = []
    changed = []

    for number in sorted(
        after_articles.keys() - before_articles.keys(),
        key=_article_number_sort_key,
    ):
        added.append(after_articles[number])
    for number in sorted(
        before_articles.keys() - after_articles.keys(),
        key=_article_number_sort_key,
    ):
        removed.append(before_articles[number])
    for number in sorted(
        before_articles.keys() & after_articles.keys(),
        key=_article_number_sort_key,
    ):
        before_article = before_articles[number]
        after_article = after_articles[number]
        text_changed = before_article.get("text") != after_article.get("text")
        part_changed = before_article.get("part") != after_article.get("part")
        if text_changed or part_changed:
            changed.append(
                {
                    "number": number,
                    "number_display": (
                        after_article.get("number_display")
                        or before_article.get("number_display")
                    ),
                    "before": before_article,
                    "after": after_article,
                }
            )

    return {
        "added": added,
        "removed": removed,
        "changed": changed,
    }


_TRACE_TEXT_DROP_RE = re.compile(r"[\s　，。；：、,.!?！？（）()《》〈〉“”\"'‘’【】\[\]、；:：]+")
_TRACE_ITEM_RE = re.compile(
    r"[（(](?P<label>[一二三四五六七八九十百千万两\d]+)[）)]"
    r"(?P<text>.*?)(?=(?:\n?[（(][一二三四五六七八九十百千万两\d]+[）)])|\Z)",
    re.S,
)


def _trace_normalize_text(text: str | None) -> str:
    return _TRACE_TEXT_DROP_RE.sub("", str(text or "")).strip()


def _trace_text_similarity(left: str | None, right: str | None) -> float:
    left_norm = _trace_normalize_text(left)
    right_norm = _trace_normalize_text(right)
    if not left_norm or not right_norm:
        return 0.0
    if left_norm == right_norm:
        return 1.0
    if left_norm in right_norm or right_norm in left_norm:
        shorter = min(len(left_norm), len(right_norm))
        longer = max(len(left_norm), len(right_norm))
        return max(0.72, min(0.96, shorter / longer + 0.12))
    return SequenceMatcher(None, left_norm, right_norm).ratio()


def _trace_query_score(query: str, text: str | None) -> float:
    query_norm = _trace_normalize_text(query)
    text_norm = _trace_normalize_text(text)
    if not query_norm or not text_norm:
        return 0.0
    if query_norm in text_norm:
        return min(0.95, 0.7 + len(query_norm) / max(len(text_norm), 1))
    return SequenceMatcher(None, query_norm, text_norm).ratio()


def _trace_parse_items(items: str | list[str] | None) -> list[str]:
    if items is None:
        return []
    if isinstance(items, str):
        raw_items = re.split(r"[,，、\s]+", items)
    else:
        raw_items = [str(item) for item in items]
    normalized: list[str] = []
    for item in raw_items:
        norm = normalize_article_number(item)
        if norm and norm not in normalized:
            normalized.append(norm)
    return normalized


def _trace_extract_article_items(text: str | None) -> dict[str, str]:
    result: dict[str, str] = {}
    for match in _TRACE_ITEM_RE.finditer(str(text or "")):
        label = normalize_article_number(match.group("label"))
        body = match.group("text").strip()
        if label and body:
            result[label] = body
    return result


def _trace_item_payload(article: dict | None, item_numbers: list[str]) -> list[dict]:
    if not article or not item_numbers:
        return []
    extracted = _trace_extract_article_items(article.get("text"))
    return [
        {
            "number": item,
            "found": item in extracted,
            "text": extracted.get(item),
        }
        for item in item_numbers
    ]


def _trace_items_similarity(
    source_article: dict,
    target_article: dict,
    item_numbers: list[str],
) -> float | None:
    if not item_numbers:
        return None
    source_items = _trace_extract_article_items(source_article.get("text"))
    target_items = _trace_extract_article_items(target_article.get("text"))
    scores = []
    for item in item_numbers:
        source_text = source_items.get(item)
        target_text = target_items.get(item)
        if source_text is None or target_text is None:
            continue
        scores.append(_trace_text_similarity(source_text, target_text))
    if not scores:
        return None
    return sum(scores) / len(scores)


def _trace_law_from_current_row(conn: sqlite3.Connection, row: sqlite3.Row) -> dict:
    articles = conn.execute(
        "SELECT * FROM articles WHERE law_id = ? ORDER BY position",
        (row["id"],),
    ).fetchall()
    law = _row_to_law(row, article_count=len(articles))
    law["articles"] = [_row_to_article(article) for article in articles]
    revisions = _fetch_revisions(conn, row["id"])
    law["revisions"] = revisions
    law["revision_count"] = len(revisions)
    law["current_revision"] = revisions[0] if revisions else None
    law["selected_revision"] = next(
        (
            revision
            for revision in revisions
            if revision.get("content_hash") == row["source_hash"]
        ),
        law["current_revision"],
    )
    return law


def _trace_candidate_rows(
    conn: sqlite3.Connection,
    anchor_row: sqlite3.Row,
) -> list[sqlite3.Row]:
    """Return same-title public-law rows that may represent historical versions."""

    return conn.execute(
        f"""
        SELECT l.*, COUNT(a.id) AS _article_count
        FROM laws l
        LEFT JOIN articles a ON a.law_id = l.id
        WHERE l.title = ?
        GROUP BY l.id
        ORDER BY COALESCE(l.effective_at, l.released_at, '') DESC,
                 {_LAW_RESOLUTION_ORDER}
        """,
        (anchor_row["title"],),
    ).fetchall()


def _trace_law_versions(
    conn: sqlite3.Connection,
    anchor_row: sqlite3.Row,
) -> list[dict]:
    versions: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for row in _trace_candidate_rows(conn, anchor_row):
        revisions = _fetch_revisions(conn, row["id"])
        for revision in revisions:
            law = _build_law_from_revision_snapshot(row, revisions, revision)
            if not law or not law.get("articles"):
                continue
            key = (law["id"], revision.get("content_hash") or revision["id"])
            if key in seen:
                continue
            seen.add(key)
            versions.append(law)

        law = _trace_law_from_current_row(conn, row)
        if not law.get("articles"):
            continue
        selected = law.get("selected_revision") or {}
        key = (law["id"], selected.get("content_hash") or law.get("source_hash") or law["id"])
        if key in seen:
            continue
        seen.add(key)
        versions.append(law)
    return versions


def _trace_law_sort_date(law: dict) -> date | None:
    selected = law.get("selected_revision") or {}
    return (
        _parse_iso_date(selected.get("effective_at"))
        or _parse_iso_date(selected.get("released_at"))
        or _parse_iso_date(law.get("effective_at"))
        or _parse_iso_date(law.get("released_at"))
    )


def _trace_select_version(versions: list[dict], as_of: date) -> dict | None:
    applicable = [
        law
        for law in versions
        if _trace_law_sort_date(law) is not None and _trace_law_sort_date(law) <= as_of
    ]
    applicable.sort(
        key=lambda law: (
            _trace_law_sort_date(law) or date.min,
            int(law.get("article_count") or 0),
        ),
        reverse=True,
    )
    return applicable[0] if applicable else None


def _trace_version_payload(law: dict | None) -> dict | None:
    if law is None:
        return None
    sort_date = _trace_law_sort_date(law)
    return {
        "law": _trace_law_reference(law),
        "revision": _revision_without_snapshot(law.get("selected_revision")),
        "as_of_version_date": sort_date.isoformat() if sort_date else None,
        "article_count": law.get("article_count"),
    }


def _trace_law_reference(law: dict | None) -> dict | None:
    ref = _law_reference_payload(law)
    if ref is None:
        return None
    for key in ("revisions", "current_revision", "selected_revision", "categories"):
        ref.pop(key, None)
    return ref


def _trace_version_chain(
    versions: list[dict],
    from_as_of: date,
    to_as_of: date,
) -> list[dict]:
    start, end = sorted((from_as_of, to_as_of))
    chain = []
    for law in versions:
        sort_date = _trace_law_sort_date(law)
        if sort_date is None or sort_date < start or sort_date > end:
            continue
        chain.append(_trace_version_payload(law))
    chain.sort(
        key=lambda item: item.get("as_of_version_date") or "",
        reverse=from_as_of > to_as_of,
    )
    return chain


def _trace_article_by_number(law: dict, number: str | None) -> dict | None:
    if not number:
        return None
    normalized = normalize_article_number(number)
    if not normalized:
        return None
    article_map = _articles_by_number(law.get("articles", []))
    return article_map.get(normalized)


def _trace_article_by_text(law: dict, text: str | None) -> tuple[dict | None, float]:
    query = (text or "").strip()
    if not query:
        return None, 0.0
    best_article = None
    best_score = 0.0
    for article in law.get("articles") or []:
        score = _trace_query_score(query, article.get("text"))
        if score > best_score:
            best_article = article
            best_score = score
    return best_article, best_score


def _trace_candidate_status(
    source_article: dict,
    target_article: dict,
    *,
    similarity: float,
) -> str:
    number_changed = source_article.get("number") != target_article.get("number")
    text_changed = (
        _trace_normalize_text(source_article.get("text"))
        != _trace_normalize_text(target_article.get("text"))
    )
    part_changed = source_article.get("part") != target_article.get("part")
    if not number_changed and not text_changed and not part_changed:
        return "unchanged"
    if number_changed and not text_changed:
        return "renumbered"
    if not number_changed and text_changed:
        return "amended"
    if part_changed and similarity >= 0.72:
        return "moved"
    if number_changed and similarity >= 0.72:
        return "renumbered"
    return "amended"


def _trace_candidate_score(
    source_article: dict,
    target_article: dict,
    target_article_count: int,
    item_numbers: list[str],
) -> dict:
    text_similarity = _trace_text_similarity(
        source_article.get("text"), target_article.get("text")
    )
    item_similarity = _trace_items_similarity(
        source_article, target_article, item_numbers
    )
    base_similarity = max(text_similarity, item_similarity or 0.0)
    same_number = (
        source_article.get("number")
        and source_article.get("number") == target_article.get("number")
    )
    number_bonus = (
        0.05
        if same_number
        else 0.0
    )
    try:
        position_delta = abs(
            int(source_article.get("position") or 0)
            - int(target_article.get("position") or 0)
        )
    except (TypeError, ValueError):
        position_delta = target_article_count
    position_bonus = max(
        0.0,
        0.03 * (1.0 - min(position_delta, target_article_count) / max(target_article_count, 1)),
    )
    confidence = min(1.0, base_similarity + number_bonus + position_bonus)
    status = _trace_candidate_status(
        source_article,
        target_article,
        similarity=base_similarity,
    )
    return {
        "article": target_article,
        "status": status,
        "confidence": round(confidence, 4),
        "similarity": round(base_similarity, 4),
        "text_similarity": round(text_similarity, 4),
        "item_similarity": round(item_similarity, 4) if item_similarity is not None else None,
    }


def _trace_match_candidates(
    source_article: dict,
    target_law: dict,
    *,
    item_numbers: list[str],
    limit: int,
) -> list[dict]:
    target_articles = target_law.get("articles") or []
    candidates = [
        _trace_candidate_score(
            source_article,
            target_article,
            len(target_articles),
            item_numbers,
        )
        for target_article in target_articles
    ]
    candidates.sort(
        key=lambda item: (
            item["confidence"],
            item["similarity"],
            -abs(
                int((item["article"] or {}).get("position") or 0)
                - int(source_article.get("position") or 0)
            ),
        ),
        reverse=True,
    )
    return candidates[: max(limit, 1)]


def _trace_evidence(
    source_article: dict,
    target_article: dict | None,
    best: dict | None,
    item_numbers: list[str],
) -> list[str]:
    if target_article is None or best is None:
        return ["未找到足够相似的目标版本条文；不得静默认定对应关系。"]
    evidence = []
    similarity = best.get("similarity") or 0
    if similarity >= 0.98:
        evidence.append("条文文本基本一致")
    elif similarity >= 0.85:
        evidence.append("条文文本高度相似，可能存在少量文字调整")
    elif similarity >= 0.72:
        evidence.append("条文文本中度相似，需要人工复核")
    else:
        evidence.append("条文文本相似度偏低，仅作为候选")
    if source_article.get("number") != target_article.get("number"):
        evidence.append(
            f"条号从{source_article.get('number_display') or source_article.get('number')}"
            f"变为{target_article.get('number_display') or target_article.get('number')}"
        )
    else:
        evidence.append("条号未变化")
    if source_article.get("part") != target_article.get("part"):
        evidence.append("所在编章节目发生变化")
    if item_numbers and best.get("item_similarity") is not None:
        if best["item_similarity"] >= 0.9:
            evidence.append("指定项文本高度一致")
        else:
            evidence.append("指定项文本存在差异或缺失")
    return evidence


def trace_article_as_of(
    db_path: Path | str,
    identifier: str,
    number: str | None = None,
    *,
    text: str | None = None,
    from_as_of: str,
    to_as_of: str,
    items: str | list[str] | None = None,
    limit: int = 5,
) -> dict | None:
    """Trace one article/text fragment across two versions of the same law.

    This is intentionally heuristic and non-destructive: it reads populated
    version snapshots and same-title sibling rows, scores article text
    similarity, and returns low-confidence candidates instead of guessing.
    """

    name = (identifier or "").strip()
    from_date = _parse_iso_date(from_as_of)
    to_date = _parse_iso_date(to_as_of)
    item_numbers = _trace_parse_items(items)
    payload_base = {
        "kind": "law_article_trace",
        "ok": False,
        "input": {
            "name": name,
            "number": number,
            "text": text,
            "from_as_of": from_as_of,
            "to_as_of": to_as_of,
            "items": item_numbers,
        },
    }
    if not name:
        return None
    if from_date is None or to_date is None:
        return {
            **payload_base,
            "error": "invalid_date",
            "message": "--from-as-of / --to-as-of must use YYYY-MM-DD",
        }
    if not (number or (text or "").strip()):
        return {
            **payload_base,
            "error": "missing_article_or_text",
            "message": "trace requires an article number or --text",
        }

    with connect(db_path) as conn:
        migrate(conn)
        row = _resolve_law_row(conn, name)
        if row is None:
            return None
        versions = _trace_law_versions(conn, row)
        from_law = _trace_select_version(versions, from_date)
        to_law = _trace_select_version(versions, to_date)

    available_versions = _trace_version_chain(versions, from_date, to_date)
    if from_law is None or to_law is None:
        missing_side = "from" if from_law is None else "to"
        return {
            **payload_base,
            "law": _trace_law_reference(_row_to_law(row)),
            "error": "version_not_found_as_of",
            "missing_side": missing_side,
            "from": _trace_version_payload(from_law),
            "to": _trace_version_payload(to_law),
            "available_versions": available_versions,
            "hint": (
                "本地缺少该时点的完整版本。先运行 "
                f"`chinalaw fetch {shlex.quote(name)} --status amended --list-matches` "
                "查看旧版本候选，再用 `--prefer-bbbs` 补全需要的版本。"
            ),
        }

    source_article = _trace_article_by_number(from_law, number)
    source_match_score = 1.0 if source_article is not None else 0.0
    if source_article is None:
        source_article, source_match_score = _trace_article_by_text(from_law, text)
    if source_article is None or source_match_score < 0.18:
        return {
            **payload_base,
            "law": _trace_law_reference(from_law),
            "error": "source_article_not_found",
            "from": _trace_version_payload(from_law),
            "to": _trace_version_payload(to_law),
            "available_versions": available_versions,
            "message": "指定条号或文本片段未能在起始版本中定位。",
        }

    candidates = _trace_match_candidates(
        source_article,
        to_law,
        item_numbers=item_numbers,
        limit=limit,
    )
    best = candidates[0] if candidates else None
    target_article = best.get("article") if best else None
    confidence = float(best.get("confidence") or 0.0) if best else 0.0
    ok = confidence >= 0.72
    status = best.get("status") if ok and best else "deleted"
    evidence = _trace_evidence(source_article, target_article, best, item_numbers)

    source_items = _trace_item_payload(source_article, item_numbers)
    target_items = _trace_item_payload(target_article, item_numbers)
    diff = {
        "number_changed": bool(
            target_article and source_article.get("number") != target_article.get("number")
        ),
        "text_changed": bool(
            target_article
            and _trace_normalize_text(source_article.get("text"))
            != _trace_normalize_text(target_article.get("text"))
        ),
        "part_changed": bool(
            target_article and source_article.get("part") != target_article.get("part")
        ),
        "similarity": round(float(best.get("similarity") or 0.0), 4) if best else 0.0,
        "confidence": round(confidence, 4),
    }
    public_candidates = [
        {
            "article": item["article"],
            "status": item["status"],
            "confidence": item["confidence"],
            "similarity": item["similarity"],
            "text_similarity": item["text_similarity"],
            "item_similarity": item["item_similarity"],
        }
        for item in candidates
    ]
    return {
        **payload_base,
        "ok": ok,
        "law": _trace_law_reference(to_law),
        "from": {
            **(_trace_version_payload(from_law) or {}),
            "as_of": from_as_of,
            "article": source_article,
            "items": source_items,
            "source_match_score": round(source_match_score, 4),
        },
        "to": {
            **(_trace_version_payload(to_law) or {}),
            "as_of": to_as_of,
            "article": target_article if ok else None,
            "items": target_items if ok else [],
        },
        "status": status,
        "confidence": round(confidence, 4),
        "evidence": evidence,
        "diff": diff,
        "candidates": public_candidates,
        "available_versions": available_versions,
        "warning": None if ok else "low_confidence_or_deleted",
    }


def _empty_search_result(
    query: str,
    kind: str,
    *,
    law_filter: dict | None = None,
    in_part: str | None = None,
) -> dict:
    return _with_search_counts(
        {
            "query": query,
            "kind": kind,
            "article_hits": [],
            "law_hits": [],
            "norm_clause_hits": [],
            "norm_source_hits": [],
            "strategy": "empty",
            "law_filter": law_filter,
            "in_part": in_part,
        }
    )


def _with_search_counts(result: dict) -> dict:
    """Inject hit counts so agents can read total / per-kind without len()."""
    article_hits = result.get("article_hits") or []
    law_hits = result.get("law_hits") or []
    norm_clause_hits = result.get("norm_clause_hits") or []
    norm_source_hits = result.get("norm_source_hits") or []
    counts = {
        "article": len(article_hits),
        "law": len(law_hits),
        "norm_clause": len(norm_clause_hits),
        "norm_source": len(norm_source_hits),
    }
    counts["total"] = sum(counts.values())
    result["counts"] = counts
    return result


def _article_match_kind(text: str, terms: list[str]) -> str:
    """Classify whether a hit is likely a direct answer or only contextual."""

    if not terms:
        return "relevant"
    stripped = text.strip()
    positions = [stripped.find(term) for term in terms if term]
    if positions and all(pos >= 0 for pos in positions) and max(positions) <= 80:
        return "primary"
    return "relevant"


def _article_hit_from_row(row: sqlite3.Row, terms: list[str]) -> dict:
    return {
        "law_id": row["law_id"],
        "law_title": row["law_title"],
        "law_short_title": display_short_title(row["law_title"], row["law_short_title"]),
        "law_status": row["law_status"],
        "number": row["number"],
        "number_display": row["number_display"],
        "part": row["part"],
        "text": row["text"],
        "source_url": row["law_source_url"],
        "freshness_days": _freshness_days(row["law_source_checked_at"]),
        "score": row["score"],
        "match_kind": _article_match_kind(row["text"], terms),
    }


def _in_clause(column: str, values: list[str] | None) -> tuple[str, list[str]]:
    if values is None:
        return "", []
    if not values:
        return " AND 0", []
    placeholders = ", ".join("?" for _ in values)
    return f" AND {column} IN ({placeholders})", list(values)


def _search_articles(
    conn: sqlite3.Connection,
    *,
    query: str,
    terms: list[str],
    use_fts: bool,
    limit: int,
    law_ids: list[str] | None = None,
    in_part: str | None = None,
) -> list[dict]:
    law_filter, law_params = _in_clause("a.law_id", law_ids)
    part_filter = ""
    part_params: list[str] = []
    if in_part:
        part_filter = " AND a.part LIKE ? ESCAPE '\\'"
        part_params = [_like_pattern(in_part)]
    if use_fts:
        rows = conn.execute(
            f"""
            SELECT a.id, a.law_id, a.number, a.number_display, a.part,
                   a.title, a.text, a.position,
                   l.title AS law_title, l.short_title AS law_short_title,
                   l.status AS law_status, l.source_url AS law_source_url,
                   l.source_checked_at AS law_source_checked_at,
                   bm25(articles_fts) AS score
            FROM articles_fts
            JOIN articles a ON a.id = articles_fts.article_id
            JOIN laws l ON l.id = a.law_id
            WHERE articles_fts MATCH ?
              {law_filter}{part_filter}
            ORDER BY score
            LIMIT ?
            """,
            (_to_fts_query(query), *law_params, *part_params, limit),
        ).fetchall()
    else:
        where, params = _build_like_all_clause("a.text", terms)
        rows = conn.execute(
            f"""
            SELECT a.id, a.law_id, a.number, a.number_display, a.part,
                   a.title, a.text, a.position,
                   l.title AS law_title, l.short_title AS law_short_title,
                   l.status AS law_status, l.source_url AS law_source_url,
                   l.source_checked_at AS law_source_checked_at,
                   0.0 AS score
            FROM articles a
            JOIN laws l ON l.id = a.law_id
            WHERE {where}
              {law_filter}{part_filter}
            ORDER BY l.released_at DESC, a.position ASC
            LIMIT ?
            """,
            [*params, *law_params, *part_params, limit],
        ).fetchall()
    return [_article_hit_from_row(row, terms) for row in rows]


def _search_laws(
    conn: sqlite3.Connection,
    *,
    query: str,
    terms: list[str],
    use_fts: bool,
    limit: int,
    law_ids: list[str] | None = None,
) -> list[dict]:
    law_filter, law_params = _in_clause("l.id", law_ids)
    if use_fts:
        rows = conn.execute(
            f"""
            SELECT l.*, bm25(laws_fts) AS score
            FROM laws_fts
            JOIN laws l ON l.id = laws_fts.law_id
            WHERE laws_fts MATCH ?
              {law_filter}
            ORDER BY score
            LIMIT ?
            """,
            (_to_fts_query(query), *law_params, limit),
        ).fetchall()
    else:
        where, params = _build_law_like_clause(terms)
        rows = conn.execute(
            f"""
            SELECT l.*, 0.0 AS score
            FROM laws l
            WHERE {where}
              {law_filter}
            ORDER BY LENGTH(l.title) ASC
            LIMIT ?
            """,
            [*params, *law_params, limit],
        ).fetchall()

    hits = []
    for row in rows:
        payload = _row_to_law(
            row,
            article_count=_count_articles_for_law(conn, row["id"]),
        )
        payload["score"] = row["score"]
        hits.append(payload)
    return hits


def _norm_clause_hit_from_row(row: sqlite3.Row) -> dict:
    return {
        "norm_source_id": row["norm_source_id"],
        "norm_source_name": row["norm_source_name"],
        "norm_source_short_name": row["norm_source_short_name"],
        "norm_source_type": row["norm_source_type"],
        "number": row["number"],
        "number_display": row["number_display"],
        "title": row["title"],
        "text": row["text"],
        "source_url": row["norm_source_url"],
        "freshness_days": _freshness_days(row["norm_source_checked_at"]),
        "score": row["score"],
    }


def _search_norm_clauses(
    conn: sqlite3.Connection,
    *,
    query: str,
    terms: list[str],
    use_fts: bool,
    limit: int,
) -> list[dict]:
    if use_fts:
        rows = conn.execute(
            """
            SELECT c.id, c.norm_source_id, c.number, c.number_display, c.title, c.text, c.position,
                   n.name AS norm_source_name, n.short_name AS norm_source_short_name,
                   n.source_type AS norm_source_type, n.source_url AS norm_source_url,
                   n.source_checked_at AS norm_source_checked_at,
                   bm25(norm_clauses_fts) AS score
            FROM norm_clauses_fts
            JOIN norm_clauses c ON c.id = norm_clauses_fts.clause_id
            JOIN norm_sources n ON n.id = c.norm_source_id
            WHERE norm_clauses_fts MATCH ?
            ORDER BY score
            LIMIT ?
            """,
            (_to_fts_query(query), limit),
        ).fetchall()
    else:
        where, params = _build_like_all_clause("c.text", terms)
        rows = conn.execute(
            f"""
            SELECT c.id, c.norm_source_id, c.number, c.number_display, c.title, c.text, c.position,
                   n.name AS norm_source_name, n.short_name AS norm_source_short_name,
                   n.source_type AS norm_source_type, n.source_url AS norm_source_url,
                   n.source_checked_at AS norm_source_checked_at,
                   0.0 AS score
            FROM norm_clauses c
            JOIN norm_sources n ON n.id = c.norm_source_id
            WHERE {where}
            ORDER BY n.effective_at DESC, c.position ASC
            LIMIT ?
            """,
            [*params, limit],
        ).fetchall()
    return [_norm_clause_hit_from_row(row) for row in rows]


def _search_norm_sources(
    conn: sqlite3.Connection,
    *,
    query: str,
    terms: list[str],
    use_fts: bool,
    limit: int,
) -> list[dict]:
    if use_fts:
        rows = conn.execute(
            """
            SELECT n.*, bm25(norm_sources_fts) AS score
            FROM norm_sources_fts
            JOIN norm_sources n ON n.id = norm_sources_fts.norm_source_id
            WHERE norm_sources_fts MATCH ?
            ORDER BY score
            LIMIT ?
            """,
            (_to_fts_query(query), limit),
        ).fetchall()
    else:
        where, params = _build_norm_source_like_clause(terms)
        rows = conn.execute(
            f"""
            SELECT n.*, 0.0 AS score
            FROM norm_sources n
            WHERE {where}
            ORDER BY LENGTH(n.name) ASC
            LIMIT ?
            """,
            [*params, limit],
        ).fetchall()

    hits = []
    for row in rows:
        payload = _row_to_norm_source(row)
        payload["score"] = row["score"]
        hits.append(payload)
    return hits


def _split_filter_values(raw: list[str] | str | None) -> list[str] | None:
    if raw is None:
        return None
    if isinstance(raw, str):
        pieces = re.split(r"[,，]", raw)
    else:
        pieces = []
        for item in raw:
            pieces.extend(re.split(r"[,，]", item))
    values = [piece.strip() for piece in pieces if piece and piece.strip()]
    return values


def _resolve_law_filter(
    conn: sqlite3.Connection,
    in_laws: list[str] | str | None,
) -> tuple[list[str] | None, dict | None]:
    requested = _split_filter_values(in_laws)
    if requested is None:
        return None, None

    resolved = []
    unresolved = []
    law_ids: list[str] = []
    seen_ids: set[str] = set()
    for identifier in requested:
        row = _resolve_law_row(conn, identifier)
        if row is None:
            unresolved.append(identifier)
            continue
        law_id = row["id"]
        if law_id not in seen_ids:
            seen_ids.add(law_id)
            law_ids.append(law_id)
        resolved.append(
            {
                "requested": identifier,
                "id": law_id,
                "title": row["title"],
                "short_title": display_short_title(row["title"], row["short_title"]),
            }
        )

    return law_ids, {
        "requested": requested,
        "resolved": resolved,
        "unresolved": unresolved,
    }


# ---------- 对外接口 ----------

def search(
    db_path: Path | str,
    query: str,
    limit: int = 20,
    kind: str = "all",
    in_laws: list[str] | str | None = None,
    in_part: str | None = None,
) -> dict:
    """混合检索：同时在 articles_fts 与 laws_fts 上跑 FTS5 查询。

    trigram tokenizer 要求每个 token 至少 3 个字符，因此对于 1~2 字短查询
    （如"过错"/"工资"），自动回退到 SQL LIKE 子串匹配。

    ``in_part`` 仅作用于 article_hits（章节字段在条文表上），用于在长法
    （如民法典 1260 条）内按编/章/节文本进一步限定检索。
    """
    query = query.strip()
    in_part = in_part.strip() if in_part else None

    terms = _split_search_terms(query)
    use_fts = _should_use_fts(terms)
    strategy = "fts5" if use_fts else "like"

    with connect(db_path) as conn:
        migrate(conn)
        law_ids, law_filter = _resolve_law_filter(conn, in_laws)
        if not query:
            return _empty_search_result(
                query, kind, law_filter=law_filter, in_part=in_part
            )
        article_hits = (
            _search_articles(
                conn,
                query=query,
                terms=terms,
                use_fts=use_fts,
                limit=limit,
                law_ids=law_ids,
                in_part=in_part,
            )
            if kind in ("article", "all")
            else []
        )
        law_hits = (
            _search_laws(
                conn,
                query=query,
                terms=terms,
                use_fts=use_fts,
                limit=limit,
                law_ids=law_ids,
            )
            if kind in ("law", "all") and not in_part
            else []
        )
        if kind in ("norm", "all") and law_filter is None and not in_part:
            norm_clause_hits = _search_norm_clauses(
                conn,
                query=query,
                terms=terms,
                use_fts=use_fts,
                limit=limit,
            )
            norm_source_hits = _search_norm_sources(
                conn,
                query=query,
                terms=terms,
                use_fts=use_fts,
                limit=limit,
            )
        else:
            norm_clause_hits = []
            norm_source_hits = []

    return _with_search_counts(
        {
            "query": query,
            "kind": kind,
            "strategy": strategy,
            "law_filter": law_filter,
            "in_part": in_part,
            "article_hits": article_hits,
            "law_hits": law_hits,
            "norm_clause_hits": norm_clause_hits,
            "norm_source_hits": norm_source_hits,
        }
    )


def get_law(db_path: Path | str, identifier: str) -> dict | None:
    """按 id / title / short_title / alias 精确或模糊匹配一部法规。"""
    return _get_law_internal(db_path, identifier)


def resolve(db_path: Path | str, identifier: str) -> dict:
    """把用户俗称 / 全名 / 模糊名解析回官方记录。

    返回扁平 dict，含 ``input`` / ``matched`` / ``via`` 三个必填字段；
    命中时还含 ``official_title`` / ``short_title`` / ``aliases`` / ``id``
    / ``level`` / ``status`` / ``issuing_body`` / ``released_at`` /
    ``effective_at``。via 取值参见 ``docs/ALIAS_SYSTEM_SPEC.md`` §3.2.2：
    ``id_match`` / ``title_match`` / ``short_title_match`` / ``alias_exact``
    / ``alias_derived`` / ``like_fallback``。

    与 ``get_law`` 的区别：
    - ``get_law`` 返回完整法规（条款 / 修订 / 分类），重；
    - ``resolve`` 只回元数据 + 命中路径，轻；用于 agent / 用户校验"我说的
      这个名字到底对应哪部官方法规"。
    """

    raw = (identifier or "").strip()
    base: dict = {"input": raw, "matched": False, "via": None}
    if not raw:
        return base

    with connect(db_path) as conn:
        migrate(conn)
        row, via = _resolve_law_row_with_via(conn, raw)

    if row is None:
        return base

    aliases_payload = _aliases_for_law_row(row)
    return {
        "input": raw,
        "matched": True,
        "via": via,
        "id": row["id"],
        "official_title": row["title"],
        "short_title": display_short_title(row["title"], row["short_title"]),
        "aliases": aliases_payload,
        "level": row["level"],
        "status": row["status"],
        "issuing_body": row["issuing_body"],
        "released_at": row["released_at"],
        "effective_at": row["effective_at"],
    }


def get_law_as_of(db_path: Path | str, identifier: str, as_of: str) -> dict | None:
    parsed = _parse_iso_date(as_of)
    if parsed is None:
        return None
    return _get_law_internal(db_path, identifier, as_of=parsed)


def _get_law_internal(
    db_path: Path | str,
    identifier: str,
    *,
    as_of: date | None = None,
) -> dict | None:
    identifier = identifier.strip()
    if not identifier:
        return None

    with connect(db_path) as conn:
        migrate(conn)
        row = _resolve_law_row(conn, identifier)
        if row is None:
            return None

        revisions = _fetch_revisions(conn, row["id"])
        categories = _fetch_categories_for_law(conn, row["id"])
        if as_of is not None:
            selected = _select_revision_as_of(revisions, as_of)
            if selected is None:
                return None
            law = _build_law_from_revision_snapshot(row, revisions, selected)
            if law is not None:
                law["categories"] = categories
            return _law_without_revision_snapshots(law)

        law = _row_to_law(row)
        articles = conn.execute(
            "SELECT * FROM articles WHERE law_id = ? ORDER BY position",
            (row["id"],),
        ).fetchall()
        law["articles"] = [_row_to_article(a) for a in articles]
        law["article_count"] = len(articles)
        law["articles_coverage"] = _articles_coverage(len(articles), row["status"])
        law["revisions"] = revisions
        law["revision_count"] = len(revisions)
        law["current_revision"] = revisions[0] if revisions else None
        law["selected_revision"] = law["current_revision"]
        law["categories"] = categories
        return _law_without_revision_snapshots(law)


def get_article(
    db_path: Path | str,
    law_identifier: str,
    number: str,
    *,
    include_norm: bool = True,
) -> dict | None:
    return _get_article_internal(
        db_path, law_identifier, number, include_norm=include_norm
    )


def get_article_as_of(
    db_path: Path | str,
    law_identifier: str,
    number: str,
    as_of: str,
    *,
    include_norm: bool = True,
) -> dict | None:
    parsed = _parse_iso_date(as_of)
    if parsed is None:
        return None
    # as_of 仅用于公开法规版本快照；norm fallback 不附带版本概念。
    return _get_article_internal(
        db_path,
        law_identifier,
        number,
        as_of=parsed,
        include_norm=include_norm,
    )


def diagnose_article_miss(
    db_path: Path | str,
    law_identifier: str,
    number: str,
    *,
    as_of: str | None = None,
) -> dict:
    """Diagnose why ``get_article(name, num)`` returned ``None``.

    Returns a single payload describing a narrow failure bucket together with
    ready-to-run next commands. The aim is that callers (agents and humans) can
    pick the next move without a separate ``status`` / ``laws`` round-trip.
    """
    name = (law_identifier or "").strip()
    num = str(number or "").strip()
    safe_name = shlex.quote(name)
    safe_num = shlex.quote(num)
    as_of_value = (as_of or "").strip()
    fallback = ["court_gongbao", "court_main", "spp_gov_cn"]
    fallback_text = " / ".join(fallback)
    fetch_cmd = (
        f"chinalaw fetch {safe_name} --article {safe_num} --source flk_npc"
    )
    history_cmd = f"chinalaw history {safe_name} --format md"

    if as_of_value:
        parsed_as_of = _parse_iso_date(as_of_value)
        if parsed_as_of is None:
            return {
                "reason": "invalid_as_of",
                "law_id": None,
                "as_of": as_of_value,
                "hint": (
                    f"`--as-of` 必须使用 YYYY-MM-DD；当前值为 {as_of_value!r}。"
                    "修正日期后重试，不要 fetch 当前法来修复日期格式问题。"
                ),
            }
    else:
        parsed_as_of = None

    law = _get_law_internal(db_path, name)
    if law is None:
        return {
            "reason": "law_missing",
            "law_id": None,
            "as_of": as_of_value or None,
            "hint": (
                f"该法规未入库，先 `{fetch_cmd}` 抓全文，flk_npc 失败再换 "
                f"{fallback_text}。"
            ),
            "suggested_fetch": fetch_cmd,
            "fallback_sources": fallback,
        }

    if parsed_as_of is not None:
        law_as_of = _get_law_internal(db_path, name, as_of=parsed_as_of)
        if law_as_of is None:
            return {
                "reason": "version_not_found_as_of",
                "law_id": law.get("id"),
                "as_of": as_of_value,
                "hint": (
                    f"法规已入库，但本地没有 {as_of_value} 时点可用版本。"
                    f"先 `{history_cmd}` 查看版本；不要用 fetch 当前版本替代"
                    "该时点判断。"
                ),
                "suggested_history": history_cmd,
            }
        law = law_as_of

    sibling_laws = _find_law_siblings(db_path, law)
    sibling_article_cmds = [
        f"chinalaw article {shlex.quote(item['id'])} {safe_num} --format json"
        for item in sibling_laws
    ]

    if parsed_as_of is not None:
        return {
            "reason": "article_null_as_of",
            "law_id": law.get("id"),
            "as_of": as_of_value,
            "hint": (
                f"法规在 {as_of_value} 的版本已命中，但条 {number} 未命中。"
                f"先 `{history_cmd}` 核对该时点版本，再检查条号是否属于"
                "其他版本；不要直接用当前版本条文替代。"
            ),
            "suggested_history": history_cmd,
            "sibling_laws": sibling_laws,
            "suggested_sibling_articles": sibling_article_cmds,
        }

    if law.get("articles_coverage") in {"stub", "seed"} or law.get("status") == "seed":
        reason = "law_seed" if law.get("status") == "seed" else "law_stub"
        label = (
            "seed 样例数据，不保证全文完整"
            if reason == "law_seed"
            else "metadata（articles_coverage=stub），缺正文"
        )
        sibling_hint = (
            "；本地另有同名候选，可先用 sibling_laws 中 article_count 更高的 id 直接查询"
            if sibling_laws
            else ""
        )
        return {
            "reason": reason,
            "law_id": law.get("id"),
            "as_of": as_of_value or None,
            "hint": (
                f"该法规仅为{label}，先 `{fetch_cmd}` 补条文{sibling_hint}。"
            ),
            "suggested_fetch": fetch_cmd,
            "fallback_sources": fallback,
            "sibling_laws": sibling_laws,
            "suggested_sibling_articles": sibling_article_cmds,
        }

    outline_cmd = f"chinalaw outline {safe_name} --format md"
    sibling_hint = (
        "；本地另有同名候选，可先用 sibling_laws 中 article_count 更高的 id 直接查询"
        if sibling_laws
        else ""
    )
    return {
        "reason": "article_null",
        "law_id": law.get("id"),
        "as_of": None,
        "hint": (
            f"法规已入库但条 {number} 未命中。先 `{outline_cmd}` 核对条号写法；"
            f"若条号正确仍 null，再 `{fetch_cmd} --force` 重抓清洗{sibling_hint}。"
        ),
        "suggested_outline": outline_cmd,
        "suggested_fetch": fetch_cmd + " --force",
        "sibling_laws": sibling_laws,
        "suggested_sibling_articles": sibling_article_cmds,
    }


def _build_norm_article_payload(
    conn: sqlite3.Connection,
    law_identifier: str,
    number: str,
) -> dict | None:
    """Article fallback：从 norm_sources / norm_clauses 取条款，包装成 article 形态。"""
    source_row = _resolve_norm_source_row(conn, law_identifier)
    if source_row is None:
        return None
    norm = normalize_article_number(number)
    clause_row = (
        _fetch_norm_clause_row(conn, source_row["id"], norm) if norm else None
    )
    # 容错：number_display 或 title 命中
    if clause_row is None:
        clause_row = conn.execute(
            """
            SELECT *
            FROM norm_clauses
            WHERE norm_source_id = ? AND (number_display = ? OR title = ?)
            LIMIT 1
            """,
            (source_row["id"], number, number),
        ).fetchone()
    law = _norm_source_row_to_law_shape(source_row)
    article = (
        _norm_clause_row_to_article_shape(clause_row, source_row["id"])
        if clause_row is not None
        else None
    )
    return {
        "law": law,
        "article": article,
        "item": article,
        "requested_number": number,
        "via": "norm_fallback",
    }


def _build_norm_articles_payload(
    conn: sqlite3.Connection,
    law_identifier: str,
    pairs: list[tuple[str, str]],
) -> dict | None:
    """get_articles 的 norm fallback：批量取多条款。"""
    source_row = _resolve_norm_source_row(conn, law_identifier)
    if source_row is None:
        return None
    source_id = source_row["id"]
    normalized_numbers = [normalized for _, normalized in pairs if normalized]
    article_map: dict[str, dict] = {}
    if normalized_numbers:
        placeholders = ", ".join("?" for _ in normalized_numbers)
        clause_rows = conn.execute(
            f"""
            SELECT *
            FROM norm_clauses
            WHERE norm_source_id = ? AND number IN ({placeholders})
            """,
            [source_id, *normalized_numbers],
        ).fetchall()
        for clause_row in clause_rows:
            article_map[clause_row["number"]] = _norm_clause_row_to_article_shape(
                clause_row, source_id
            )

    # 容错：number_display / title 兜底（每个原始 token 单独尝试）
    items: list[dict] = []
    for requested, normalized in pairs:
        article = article_map.get(normalized)
        if article is None and requested:
            extra = conn.execute(
                """
                SELECT *
                FROM norm_clauses
                WHERE norm_source_id = ? AND (number_display = ? OR title = ?)
                LIMIT 1
                """,
                (source_id, requested, requested),
            ).fetchone()
            if extra is not None:
                article = _norm_clause_row_to_article_shape(extra, source_id)
        items.append(
            {
                "requested_number": requested,
                "number": normalized,
                "found": article is not None,
                "article": article,
            }
        )
    missing = [item for item in items if not item["found"]]
    law = _norm_source_row_to_law_shape(source_row)
    return {
        "kind": "law_articles",
        "law": law,
        "as_of": None,
        "requested_numbers": [requested for requested, _ in pairs],
        "normalized_numbers": [normalized for _, normalized in pairs],
        "item_count": len(items),
        "found_count": len(items) - len(missing),
        "missing_count": len(missing),
        "items": items,
        "articles": items,
        "via": "norm_fallback",
    }


def _expand_article_number_token(token: str) -> list[tuple[str, str]]:
    value = token.strip()
    if not value:
        return []

    range_match = re.fullmatch(r"(.+?)[\-－—~～至到](.+)", value)
    if range_match and "之" not in value:
        start_raw = range_match.group(1)
        end_raw = range_match.group(2)
        start = normalize_article_number(start_raw)
        end = normalize_article_number(end_raw)
        if start.isdigit() and end.isdigit():
            start_int = int(start)
            end_int = int(end)
            if start_int <= end_int:
                return [
                    (str(number), str(number))
                    for number in range(start_int, end_int + 1)
                ]

    normalized = normalize_article_number(value)
    return [(value, normalized)] if normalized else []


def parse_article_number_spec(raw: str | list[str]) -> list[tuple[str, str]]:
    """Parse a batch article spec into (requested, normalized) pairs."""

    if isinstance(raw, str):
        tokens = re.split(r"[,，、\s]+", raw.strip())
    else:
        tokens = []
        for item in raw:
            tokens.extend(re.split(r"[,，、\s]+", str(item).strip()))

    pairs: list[tuple[str, str]] = []
    seen_numbers: set[str] = set()
    for token in tokens:
        for requested, normalized in _expand_article_number_token(token):
            if not normalized or normalized in seen_numbers:
                continue
            seen_numbers.add(normalized)
            pairs.append((requested, normalized))
    return pairs


def get_articles(
    db_path: Path | str,
    law_identifier: str,
    numbers: str | list[str],
    *,
    as_of: str | None = None,
    include_norm: bool = True,
) -> dict | None:
    pairs = parse_article_number_spec(numbers)
    if not pairs:
        return None

    parsed_as_of = _parse_iso_date(as_of) if as_of else None
    if as_of and parsed_as_of is None:
        return None

    with connect(db_path) as conn:
        migrate(conn)
        row = _resolve_law_row(conn, law_identifier)
        if row is None:
            # 公开法规未命中——尝试 norm fallback（仅当未指定 as_of）
            if include_norm and parsed_as_of is None:
                fallback = _build_norm_articles_payload(
                    conn, law_identifier, pairs
                )
                if fallback is not None:
                    return fallback
            return None

        revisions = _fetch_revisions(conn, row["id"])
        categories = _fetch_categories_for_law(conn, row["id"])
        selected_as_of = parsed_as_of.isoformat() if parsed_as_of else None

        if parsed_as_of is not None:
            selected = _select_revision_as_of(revisions, parsed_as_of)
            if selected is None:
                return None
            law = _build_law_from_revision_snapshot(row, revisions, selected)
            if law is None:
                return None
            law["categories"] = categories
            article_map = _articles_by_number(law.get("articles", []))
        else:
            law = _row_to_law(
                row,
                article_count=_count_articles_for_law(conn, row["id"]),
            )
            law["revisions"] = revisions
            law["revision_count"] = len(revisions)
            law["current_revision"] = revisions[0] if revisions else None
            law["selected_revision"] = law["current_revision"]
            law["categories"] = categories
            normalized_numbers = [normalized for _, normalized in pairs]
            placeholders = ", ".join("?" for _ in normalized_numbers)
            article_rows = conn.execute(
                f"""
                SELECT *
                FROM articles
                WHERE law_id = ? AND number IN ({placeholders})
                """,
                [row["id"], *normalized_numbers],
            ).fetchall()
            article_map = {
                article["number"]: _row_to_article(article)
                for article in article_rows
            }

        items = []
        for requested, normalized in pairs:
            article = article_map.get(normalized)
            items.append(
                {
                    "requested_number": requested,
                    "number": normalized,
                    "found": article is not None,
                    "article": article,
                }
            )

        missing = [item for item in items if not item["found"]]
        return {
            "kind": "law_articles",
            "law": _law_reference_payload(law),
            "as_of": selected_as_of,
            "requested_numbers": [requested for requested, _ in pairs],
            "normalized_numbers": [normalized for _, normalized in pairs],
            "item_count": len(items),
            "found_count": len(items) - len(missing),
            "missing_count": len(missing),
            "items": items,
            "articles": items,
        }


def parse_articles_batch_spec(raw: str) -> list[tuple[str, str]]:
    """解析多法批量 spec：``law1:nums1;law2:nums2``。

    分隔符兼容半角 / 全角分号；law 与 numbers 之间用 ``:`` 或 ``：``。返回
    ``[(law_name, numbers_spec), ...]``，保持输入顺序，跳过空白片段。无法解析
    的片段 numbers 部分留空，由上层报错。
    """

    if raw is None:
        return []
    text = str(raw).strip()
    if not text:
        return []
    sections: list[tuple[str, str]] = []
    for part in re.split(r"[;；]+", text):
        chunk = part.strip()
        if not chunk:
            continue
        match = re.split(r"[:：]", chunk, maxsplit=1)
        if len(match) != 2:
            sections.append((chunk, ""))
            continue
        law_name = match[0].strip()
        numbers = match[1].strip()
        if law_name:
            sections.append((law_name, numbers))
    return sections


def get_articles_batch(
    db_path: Path | str,
    batch_spec: str,
    *,
    as_of: str | None = None,
    include_norm: bool = True,
) -> dict | None:
    """跨多部法规一次取条。"""

    entries = parse_articles_batch_spec(batch_spec)
    if not entries:
        return None
    sections: list[dict] = []
    item_count = 0
    found_count = 0
    missing_count = 0
    failed_section_count = 0
    for law_name, numbers in entries:
        if not numbers:
            failed_section_count += 1
            sections.append(
                {
                    "name": law_name,
                    "numbers_spec": numbers,
                    "result": None,
                    "error": "missing_numbers",
                    "ok": False,
                }
            )
            continue
        result = get_articles(
            db_path, law_name, numbers, as_of=as_of, include_norm=include_norm
        )
        section_missing_count = result.get("missing_count", 0) if result is not None else 0
        section_ok = result is not None and section_missing_count == 0
        if result is None:
            failed_section_count += 1
        sections.append(
            {
                "name": law_name,
                "numbers_spec": numbers,
                "result": result,
                "error": None if result is not None else "law_not_found",
                "ok": section_ok,
            }
        )
        if result is not None:
            item_count += result.get("item_count", 0)
            found_count += result.get("found_count", 0)
            missing_count += result.get("missing_count", 0)
    error_count = failed_section_count + missing_count
    return {
        "kind": "law_articles_batch",
        "ok": error_count == 0,
        "as_of": as_of,
        "law_count": len(sections),
        "item_count": item_count,
        "found_count": found_count,
        "missing_count": missing_count,
        "failed_section_count": failed_section_count,
        "error_count": error_count,
        "sections": sections,
    }


def _get_article_internal(
    db_path: Path | str,
    law_identifier: str,
    number: str,
    *,
    as_of: date | None = None,
    include_norm: bool = True,
) -> dict | None:
    norm = normalize_article_number(number)
    if not norm:
        # number 在公开法规规则下无法归一；仍尝试 norm fallback（norm 编号可任意字符串）
        if include_norm and as_of is None:
            with connect(db_path) as conn:
                migrate(conn)
                fallback = _build_norm_article_payload(conn, law_identifier, number)
                if fallback is not None:
                    return fallback
        return None
    with connect(db_path) as conn:
        migrate(conn)
        row = _resolve_law_row(conn, law_identifier)
        if row is None:
            # 公开法规未命中——尝试 norm fallback（仅当未指定 as_of）
            if include_norm and as_of is None:
                fallback = _build_norm_article_payload(conn, law_identifier, number)
                if fallback is not None:
                    return fallback
            return None

        law = _row_to_law(
            row, article_count=_count_articles_for_law(conn, row["id"])
        )
        revisions = _fetch_revisions(conn, row["id"])
        categories = _fetch_categories_for_law(conn, row["id"])
        if as_of is not None:
            selected = _select_revision_as_of(revisions, as_of)
            if selected is None:
                return None
            law_from_revision = _build_law_from_revision_snapshot(row, revisions, selected)
            if law_from_revision is None:
                return None
            law_from_revision["categories"] = categories
            article = next(
                (
                    item
                    for item in law_from_revision.get("articles", [])
                    if item.get("number") == norm
                ),
                None,
            )
            return {
                "law": _law_reference_payload(law_from_revision),
                "article": article,
                "item": article,
                "requested_number": number,
            }

        law["revisions"] = revisions
        law["revision_count"] = len(revisions)
        law["current_revision"] = revisions[0] if revisions else None
        law["selected_revision"] = law["current_revision"]
        law["categories"] = categories
        art = conn.execute(
            "SELECT * FROM articles WHERE law_id = ? AND number = ?",
            (row["id"], norm),
        ).fetchone()
        public_law = _law_reference_payload(law)
        if art is None:
            return {
                "law": public_law,
                "article": None,
                "item": None,
                "requested_number": number,
            }
        article_payload = _row_to_article(art)
        return {
            "law": public_law,
            "article": article_payload,
            "item": article_payload,
            "requested_number": number,
        }


def outline_law(
    db_path: Path | str,
    identifier: str,
    *,
    part: str | None = None,
    preview_chars: int = 80,
    with_text: bool = False,
) -> dict | None:
    identifier = identifier.strip()
    if not identifier:
        return None
    preview_chars = max(0, int(preview_chars))

    with connect(db_path) as conn:
        migrate(conn)
        row = _resolve_law_row(conn, identifier)
        if row is None:
            return None

        law = _row_to_law(
            row,
            article_count=_count_articles_for_law(conn, row["id"]),
        )
        params: list[str] = [row["id"]]
        where = "law_id = ?"
        if part:
            where += " AND part LIKE ? ESCAPE '\\'"
            params.append(_like_pattern(part))
        articles = conn.execute(
            f"""
            SELECT *
            FROM articles
            WHERE {where}
            ORDER BY position
            """,
            params,
        ).fetchall()

    items = []
    for article in articles:
        article_payload = _row_to_article(article)
        full_text = (article_payload.get("text") or "").strip()
        preview_source = full_text.replace("\n", " ")
        preview = preview_source[:preview_chars] if preview_chars else ""
        item = {
            "number": article["number"],
            "number_display": article["number_display"],
            "part": article["part"],
            "title": article["title"],
            "position": article["position"],
            "text_preview": preview,
            "text_truncated": len(preview_source) > len(preview),
        }
        if with_text:
            item["text"] = full_text
            item["text_length"] = len(full_text)
            item["text_truncated"] = False
            item["article"] = article_payload
            item["found"] = True
            item["requested_number"] = article["number_display"] or article["number"]
        items.append(item)

    payload = {
        "kind": "law_outline",
        "law": law,
        "part_filter": part,
        "preview_chars": preview_chars,
        "text_mode": "full" if with_text else "preview",
        "full_text": bool(with_text),
        "article_count": law.get("article_count"),
        "item_count": len(items),
        "items": items,
        "articles": items,
    }
    if with_text:
        payload["with_text"] = True
        payload["found_count"] = len(items)
        payload["missing_count"] = 0
    return payload


_CHINESE_DIGITS = "零一二三四五六七八九"
_CHINESE_UNITS = ["", "十", "百", "千"]


def _arabic_to_chinese_numeral(value: int) -> str:
    """阿拉伯数字 → 中国法律实务条号常用中文写法。

    例：``1 -> 一``、``10 -> 十``、``11 -> 十一``、``20 -> 二十``、
    ``100 -> 一百``、``110 -> 一百一十``、``111 -> 一百一十一``、
    ``522 -> 五百二十二``、``1260 -> 一千二百六十``。
    """

    if value <= 0:
        return ""
    if value < 10:
        return _CHINESE_DIGITS[value]
    if value < 20:
        # 第十条 / 第十一条 / 第十九条——条号写法不带前缀「一」
        return "十" + (_CHINESE_DIGITS[value - 10] if value > 10 else "")
    digits = str(value)
    length = len(digits)
    chars: list[str] = []
    for index, ch in enumerate(digits):
        digit = int(ch)
        unit = _CHINESE_UNITS[length - index - 1]
        if digit == 0:
            if chars and chars[-1] != "零":
                chars.append("零")
        else:
            chars.append(_CHINESE_DIGITS[digit] + unit)
    while chars and chars[-1] == "零":
        chars.pop()
    return "".join(chars)


def _law_name_candidates(law_row: sqlite3.Row | dict) -> list[str]:
    if hasattr(law_row, "keys"):
        title = law_row["title"]
        short = law_row["short_title"]
        aliases_json = law_row["aliases"]
    else:
        title = law_row.get("title")
        short = law_row.get("short_title")
        aliases_json = law_row.get("aliases")
    short = display_short_title(title, short)
    try:
        raw_aliases = json.loads(aliases_json) if aliases_json else []
    except json.JSONDecodeError:
        raw_aliases = []
    aliases_list = merge_law_aliases(title, short, raw_aliases or [])
    candidates: list[str] = []
    seen: set[str] = set()
    for value in [short, title, *aliases_list]:
        if not value:
            continue
        text = str(value).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        candidates.append(text)
    return candidates


def _build_cited_by_pattern(law_names: list[str], number_arabic: str) -> re.Pattern[str]:
    """对目标 (law_names, 条号) 构造正则：匹配带书名的「《X》第N条 / X第N条」。

    阿拉伯与中文数字两种写法均覆盖；书名号 `《》` 可有可无。
    """

    chinese = _arabic_to_chinese_numeral(int(number_arabic))
    number_alts = [number_arabic]
    if chinese and chinese != number_arabic:
        number_alts.append(chinese)
    number_pattern = "|".join(re.escape(num) for num in number_alts)
    name_pattern = "|".join(re.escape(name) for name in law_names)
    if not name_pattern:
        return re.compile(r"(?!.*)")  # 不匹配
    return re.compile(
        rf"(?:《\s*(?:{name_pattern})\s*》|(?:{name_pattern}))"
        rf"\s*第\s*(?:{number_pattern})\s*条"
    )


def _hit_snippet(text: str, match: re.Match[str], context: int = 30) -> str:
    start = max(0, match.start() - context)
    end = min(len(text), match.end() + context)
    snippet = text[start:end].replace("\n", " ")
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(text) else ""
    return f"{prefix}{snippet}{suffix}"


def find_cited_by(
    db_path: Path | str,
    law_identifier: str,
    number: str,
    *,
    in_laws: list[str] | str | None = None,
    include_self: bool = False,
    limit: int = 50,
) -> dict | None:
    """扫描全库条文正文，找出引用「``law_identifier`` 第 ``number`` 条」的他法条文。

    - 仅识别绝对引用：``《民法典》第522条`` / ``民法典第五百二十二条``；
      不识别「前条」/「本法第N条」等相对引用（后续版本扩展）
    - 默认排除同部法规自引（``include_self=False``）
    - 仅扫描 ``in_laws`` 限定的法规（同 ``search --in`` 语义）
    """

    identifier = (law_identifier or "").strip()
    if not identifier:
        return None
    normalized_number = normalize_article_number(number)
    if not normalized_number or "-" in normalized_number:
        # 暂不支持插入条款号「14-1」的反向引用——文本里很少这样写
        return None
    try:
        number_int = int(normalized_number)
    except ValueError:
        return None

    requested_limit = max(1, min(int(limit), 500))

    with connect(db_path) as conn:
        migrate(conn)
        target_row = _resolve_law_row(conn, identifier)
        if target_row is None:
            return None
        target_law_id = target_row["id"]
        target_law = _row_to_law(
            target_row,
            article_count=_count_articles_for_law(conn, target_law_id),
        )
        target_article_row = conn.execute(
            "SELECT * FROM articles WHERE law_id = ? AND number = ? LIMIT 1",
            (target_law_id, normalized_number),
        ).fetchone()
        target_article = (
            _row_to_article(target_article_row) if target_article_row else None
        )

        scan_law_ids, law_filter = _resolve_law_filter(conn, in_laws)
        host_filter_clauses: list[str] = []
        host_params: list[str] = []
        if scan_law_ids is not None:
            if not scan_law_ids:
                return {
                    "kind": "law_article_cited_by",
                    "target": {
                        "law": _law_reference_payload(target_law),
                        "article": target_article,
                        "requested_number": number,
                        "normalized_number": normalized_number,
                    },
                    "include_self": include_self,
                    "limit": requested_limit,
                    "law_filter": law_filter,
                    "scanned_count": 0,
                    "hit_count": 0,
                    "hits": [],
                }
            placeholders = ", ".join("?" for _ in scan_law_ids)
            host_filter_clauses.append(f"a.law_id IN ({placeholders})")
            host_params.extend(scan_law_ids)
        if not include_self:
            host_filter_clauses.append("a.law_id != ?")
            host_params.append(target_law_id)

        where = (
            "WHERE " + " AND ".join(host_filter_clauses)
            if host_filter_clauses
            else ""
        )
        candidate_rows = conn.execute(
            f"""
            SELECT a.*, l.title AS law_title, l.short_title AS law_short_title,
                   l.source_url AS law_source_url
            FROM articles a
            JOIN laws l ON l.id = a.law_id
            {where}
            ORDER BY l.released_at DESC, a.position ASC
            """,
            host_params,
        ).fetchall()

        target_names = _law_name_candidates(target_row)
        pattern = _build_cited_by_pattern(target_names, normalized_number)
        hits: list[dict] = []
        scanned = 0
        for row in candidate_rows:
            scanned += 1
            text = row["text"] or ""
            match = pattern.search(text)
            if match is None:
                continue
            host_law_payload = {
                "id": row["law_id"],
                "title": row["law_title"],
                "short_title": display_short_title(
                    row["law_title"], row["law_short_title"]
                ),
                "source_url": row["law_source_url"],
            }
            hits.append(
                {
                    "law": host_law_payload,
                    "article": _row_to_article(row),
                    "matched_text": match.group(0),
                    "snippet": _hit_snippet(text, match),
                }
            )
            if len(hits) >= requested_limit:
                break

    return {
        "kind": "law_article_cited_by",
        "target": {
            "law": _law_reference_payload(target_law),
            "article": target_article,
            "requested_number": number,
            "normalized_number": normalized_number,
            "name_candidates": target_names,
            "number_int": number_int,
        },
        "include_self": include_self,
        "limit": requested_limit,
        "law_filter": law_filter,
        "scanned_count": scanned,
        "hit_count": len(hits),
        "hits": hits,
    }


def parse_cited_by_spec(raw: str) -> tuple[str, str] | None:
    """解析 ``民法典:522`` / ``民法典：第522条`` 形式的 spec。"""

    if not raw:
        return None
    text = raw.strip()
    if not text:
        return None
    for sep in (":", "：", " "):
        if sep in text:
            law_part, _, number_part = text.partition(sep)
            law_part = law_part.strip()
            number_part = number_part.strip()
            if law_part and number_part:
                return law_part, number_part
            return None
    return None


def list_laws(
    db_path: Path | str,
    level: str | None = None,
    status: str | None = None,
    limit: int = 50,
) -> list[dict]:
    clauses: list[str] = []
    params: list[str] = []
    if level:
        clauses.append("level = ?")
        params.append(level)
    if status:
        clauses.append("status = ?")
        params.append(status)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(str(limit))

    with connect(db_path) as conn:
        migrate(conn)
        rows = conn.execute(
            f"""
            SELECT laws.*, (
                SELECT COUNT(*) FROM articles WHERE articles.law_id = laws.id
            ) AS _article_count
            FROM laws
            {where}
            ORDER BY released_at DESC, title ASC
            LIMIT ?
            """,
            params,
        ).fetchall()
        return [
            _row_to_law(r, article_count=int(r["_article_count"])) for r in rows
        ]


def relation(db_path: Path | str, identifier: str) -> dict:
    """查看一部规范与其它规范的显式关系。

    关系数据来自人工维护的本地规则文件；它只说明可追溯线索，不自动得出适用结论。
    """
    identifier = identifier.strip()
    if not identifier:
        return {
            "kind": "law_relation_result",
            "identifier": identifier,
            "law": None,
            "relation_count": 0,
            "relations": [],
            "warnings": [_warning("empty_identifier", "请提供法规名称或 law_id。")],
        }

    with connect(db_path) as conn:
        migrate(conn)
        row = _resolve_law_row(conn, identifier)
        focus_id = row["id"] if row is not None else identifier
        law = (
            _row_to_law(row, article_count=_count_articles_for_law(conn, row["id"]))
            if row is not None
            else None
        )
        rows = conn.execute(
            """
            SELECT *
            FROM law_relations
            WHERE from_law_id = ? OR to_law_id = ?
            ORDER BY COALESCE(effective_at, '') DESC, relation_type, id
            """,
            (focus_id, focus_id),
        ).fetchall()
        warnings = [
            _warning(
                "grounding_only",
                "relation 只返回规范关系线索，不等同于具体案件的适用法律结论。",
            )
        ]
        if law is None and not rows:
            warnings.append(
                _warning(
                    "law_not_found",
                    f"未在本地库中找到 {identifier}，也没有直接匹配该 id 的关系记录。",
                )
            )
        return {
            "kind": "law_relation_result",
            "identifier": identifier,
            "law": law,
            "relation_count": len(rows),
            "relations": [
                _relation_row_to_dict(conn, rel, focus_law_id=focus_id)
                for rel in rows
            ],
            "warnings": warnings,
        }


def applicable(
    db_path: Path | str,
    *,
    as_of: str,
    topic: str | None = None,
    law: str | None = None,
    domain: str | None = None,
) -> dict:
    """按日期 / 主题 / 规范返回适用规则线索。

    这是 grounding 工具：输出需要阅读的规则、旧法线索和 fetch 提示，不输出最终法律意见。
    """
    parsed = _parse_iso_date(as_of)
    base_warnings = [
        _warning(
            "not_legal_conclusion",
            "applicable 只提供检索线索，不能替代律师或法务对时间效力的最终判断。",
        )
    ]
    if parsed is None:
        return {
            "kind": "applicability_result",
            "ok": False,
            "as_of": as_of,
            "topic": topic,
            "law": law,
            "domain": domain,
            "match_count": 0,
            "matches": [],
            "warnings": [
                *base_warnings,
                _warning("invalid_date", "日期必须使用 YYYY-MM-DD 格式。", severity="error"),
            ],
        }

    with connect(db_path) as conn:
        migrate(conn)
        clauses = [
            "(effective_from IS NULL OR effective_from <= ?)",
            "(effective_to IS NULL OR effective_to >= ?)",
        ]
        params: list[str] = [parsed.isoformat(), parsed.isoformat()]

        if topic:
            clauses.append("(topic = ? OR topic LIKE ? ESCAPE '\\')")
            params.extend([topic, _like_pattern(topic)])
        if domain:
            clauses.append("(domain = ? OR domain = 'all')")
            params.append(domain)

        resolved_law = None
        law_filter = None
        if law:
            law_row = _resolve_law_row(conn, law)
            if law_row is not None:
                resolved_law = _row_to_law(
                    law_row,
                    article_count=_count_articles_for_law(conn, law_row["id"]),
                )
                law_filter = law_row["id"]
            else:
                law_filter = law
            clauses.append(
                "(primary_law_id = ? OR fallback_law_id = ? OR "
                "primary_law_title LIKE ? ESCAPE '\\' OR "
                "fallback_law_title LIKE ? ESCAPE '\\')"
            )
            params.extend([
                law_filter,
                law_filter,
                _like_pattern(law),
                _like_pattern(law),
            ])

        where = " AND ".join(clauses)
        rows = conn.execute(
            f"""
            SELECT *
            FROM applicability_rules
            WHERE {where}
            ORDER BY
                CASE WHEN domain = ? THEN 0 WHEN domain = 'all' THEN 1 ELSE 2 END,
                COALESCE(effective_from, '') DESC,
                topic ASC,
                id ASC
            """,
            [*params, domain or "all"],
        ).fetchall()

        matches = [_rule_row_to_dict(conn, row) for row in rows]
        warnings = [*base_warnings]
        if not matches:
            warnings.append(
                _warning(
                    "no_applicability_rule",
                    "没有命中本地时间效力规则；应回到 search/history/fetch 或上游数据库继续检索。",
                )
            )
        if law and resolved_law is None:
            warnings.append(
                _warning(
                    "law_filter_not_resolved",
                    f"law 过滤条件 {law} 未解析为本地法规，只按原始文本匹配规则。",
                )
            )
        for match in matches:
            warnings.extend(match.get("warnings") or [])

    return {
        "kind": "applicability_result",
        "ok": True,
        "as_of": parsed.isoformat(),
        "topic": topic,
        "law": resolved_law or law,
        "domain": domain,
        "match_count": len(matches),
        "matches": matches,
        "warnings": _unique_warnings(warnings),
    }


def status(db_path: Path | str) -> dict:
    with connect(db_path) as conn:
        migrate(conn)
        law_count = conn.execute("SELECT COUNT(*) FROM laws").fetchone()[0]
        article_count = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
        revision_count = conn.execute("SELECT COUNT(*) FROM revisions").fetchone()[0]
        category_count = conn.execute("SELECT COUNT(*) FROM categories").fetchone()[0]
        norm_pack_count = conn.execute("SELECT COUNT(*) FROM norm_packs").fetchone()[0]
        norm_source_count = conn.execute("SELECT COUNT(*) FROM norm_sources").fetchone()[0]
        norm_clause_count = conn.execute("SELECT COUNT(*) FROM norm_clauses").fetchone()[0]
        law_relation_count = conn.execute("SELECT COUNT(*) FROM law_relations").fetchone()[0]
        applicability_rule_count = conn.execute(
            "SELECT COUNT(*) FROM applicability_rules"
        ).fetchone()[0]
        last_sync = get_meta(conn, "last_sync_at")
        last_applicability_sync = get_meta(conn, "last_applicability_sync_at")
        schema_version = get_meta(conn, "schema_version")

        by_level_rows = conn.execute(
            "SELECT level, COUNT(*) AS c FROM laws GROUP BY level ORDER BY c DESC"
        ).fetchall()
        by_level = [{"level": r["level"], "count": r["c"]} for r in by_level_rows]

        by_status_rows = conn.execute(
            "SELECT status, COUNT(*) AS c FROM laws GROUP BY status"
        ).fetchall()
        by_status = [{"status": r["status"], "count": r["c"]} for r in by_status_rows]

        oldest_check = conn.execute(
            "SELECT MIN(source_checked_at) FROM laws"
        ).fetchone()[0]

        stub_law_rows = conn.execute(
            """
            SELECT id, title, short_title
            FROM laws
            WHERE status <> 'seed'
              AND NOT EXISTS (
                SELECT 1 FROM articles WHERE articles.law_id = laws.id
            )
            ORDER BY title
            """
        ).fetchall()
        seed_law_rows = conn.execute(
            """
            SELECT id, title, short_title
            FROM laws
            WHERE status = 'seed'
            ORDER BY title
            """
        ).fetchall()
        stub_laws = [
            {
                "id": r["id"],
                "title": r["title"],
                "short_title": r["short_title"],
            }
            for r in stub_law_rows
        ]
        seed_laws = [
            {
                "id": r["id"],
                "title": r["title"],
                "short_title": r["short_title"],
            }
            for r in seed_law_rows
        ]
        populated_count = max(law_count - len(stub_laws) - len(seed_laws), 0)
        by_articles_coverage = []
        if populated_count:
            by_articles_coverage.append(
                {"coverage": "populated", "count": populated_count}
            )
        if seed_laws:
            by_articles_coverage.append(
                {"coverage": "seed", "count": len(seed_laws)}
            )
        if stub_laws:
            by_articles_coverage.append(
                {"coverage": "stub", "count": len(stub_laws)}
            )

    return {
        "db_path": str(db_path),
        "schema_version": int(schema_version) if schema_version else 0,
        "laws": law_count,
        "articles": article_count,
        "revisions": revision_count,
        "categories": category_count,
        "norm_packs": norm_pack_count,
        "norm_sources": norm_source_count,
        "norm_clauses": norm_clause_count,
        "law_relations": law_relation_count,
        "applicability_rules": applicability_rule_count,
        "last_sync_at": last_sync,
        "last_applicability_sync_at": last_applicability_sync,
        "oldest_source_checked_at": oldest_check,
        "oldest_freshness_days": _freshness_days(oldest_check),
        "by_level": by_level,
        "by_status": by_status,
        "by_articles_coverage": by_articles_coverage,
        "stub_laws": stub_laws,
        "seed_laws": seed_laws,
        "alias_agent": "enabled" if os.environ.get("CHINALAW_USE_ALIAS_AGENT") else "disabled",
    }


def history(db_path: Path | str, identifier: str) -> dict | None:
    identifier = identifier.strip()
    if not identifier:
        return None

    with connect(db_path) as conn:
        migrate(conn)
        row = _resolve_law_row(conn, identifier)
        if row is None:
            return None
        law = _row_to_law(row, article_count=_count_articles_for_law(conn, row["id"]))
        revisions = _fetch_revisions(conn, row["id"])
        return {
            "law": law,
            "revisions": [
                _revision_without_snapshot(revision) for revision in revisions
            ],
            "revision_count": len(revisions),
            "current_revision": _revision_without_snapshot(
                revisions[0] if revisions else None
            ),
        }


def diff_law_as_of(
    db_path: Path | str,
    identifier: str,
    from_as_of: str,
    to_as_of: str,
) -> dict | None:
    before = get_law_as_of(db_path, identifier, from_as_of)
    after = get_law_as_of(db_path, identifier, to_as_of)
    if before is None or after is None:
        return None
    article_diff = _compare_articles(before, after)
    return {
        "law_id": after["id"],
        "title": after["title"],
        "from_as_of": from_as_of,
        "to_as_of": to_as_of,
        "from_revision": before.get("selected_revision"),
        "to_revision": after.get("selected_revision"),
        "added": article_diff["added"],
        "removed": article_diff["removed"],
        "changed": article_diff["changed"],
        "summary": {
            "added": len(article_diff["added"]),
            "removed": len(article_diff["removed"]),
            "changed": len(article_diff["changed"]),
        },
    }
