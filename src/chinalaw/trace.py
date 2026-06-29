"""Trace 子系统：跨两个时点追溯同一法规中的某条/某段文本。

从 service.py 拆出（见 docs/decisions/ADR-0009-module-boundaries.md）。
纯启发式、只读、非破坏性：读取已落库的版本快照与同名 sibling 行，对条文文本打分。

对上：`trace_article_as_of` 由 cli 调用；service.py 末尾 re-export 该函数保持
`chinalaw.service.trace_article_as_of` 向后兼容（cli 调用点与测试 patch 依赖此路径）。
对下：复用 service 的 row 映射 / 版本解析 / 条号归一化等基础 helper。
"""

from __future__ import annotations

import re
import shlex
import sqlite3
from datetime import date
from difflib import SequenceMatcher
from pathlib import Path

from chinalaw.db import connect, migrate
from chinalaw.service import (
    _LAW_RESOLUTION_ORDER,
    _articles_by_number,
    _build_law_from_revision_snapshot,
    _fetch_revisions,
    _law_reference_payload,
    _parse_iso_date,
    _resolve_law_row,
    _revision_without_snapshot,
    _row_to_article,
    _row_to_law,
    normalize_article_number,
)

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
