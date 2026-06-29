"""输出格式化：JSON / Markdown。

两种格式互为等价信息量，agent 默认吃 JSON，人眼阅读用 Markdown。
"""

from __future__ import annotations

import json
import re
from typing import Any


def to_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _law_label(law: dict) -> str:
    short = law.get("short_title")
    if short:
        return str(short)
    title = law.get("title") or law.get("law_title") or ""
    return f"《{title}》" if title else "未命名法规"


def _article_number_label(article: dict, *, number_style: str = "display") -> str:
    if number_style == "arabic":
        number = article.get("number") or article.get("number_display") or ""
        return f"第{number}条" if number else ""
    if number_style == "section":
        number = article.get("number") or article.get("number_display") or ""
        return f"§{number}" if number else ""
    return article.get("number_display") or article.get("number") or ""


def _freshness_label(law: dict) -> str | None:
    fd = law.get("freshness_days")
    if fd is not None:
        return f"核查 {fd} 天前"
    checked = law.get("source_checked_at")
    if checked:
        return f"核查日期 {str(checked)[:10]}"
    return None


def _compact_article_footer(law: dict) -> str | None:
    parts: list[str] = []
    if law.get("status"):
        parts.append(str(law["status"]))
    effective_at = law.get("effective_at")
    if effective_at:
        parts.append(f"{effective_at} 施行")
    freshness = _freshness_label(law)
    if freshness:
        parts.append(freshness)
    return f"[{'｜'.join(parts)}]" if parts else None


def search_to_markdown(result: dict) -> str:
    lines: list[str] = []
    query = result.get("query", "")
    lines.append(f"# 检索：{query}")
    if result.get("in_part"):
        lines.append(f"_章节限定：{result.get('in_part')}_")
    counts = result.get("counts") or {}
    if counts:
        summary_bits = []
        if counts.get("article"):
            summary_bits.append(f"条文 {counts['article']}")
        if counts.get("law"):
            summary_bits.append(f"法规 {counts['law']}")
        if counts.get("norm_clause"):
            summary_bits.append(f"私域条款 {counts['norm_clause']}")
        if counts.get("norm_source"):
            summary_bits.append(f"私域规范 {counts['norm_source']}")
        if summary_bits:
            lines.append("")
            lines.append(
                f"_命中合计 {counts.get('total', 0)}：" + " / ".join(summary_bits) + "_"
            )
    lines.append("")

    law_hits = result.get("law_hits", [])
    if law_hits:
        lines.append(f"## 匹配法规（{len(law_hits)}）")
        for h in law_hits:
            title = h.get("title")
            short = h.get("short_title")
            label = f"{title}" + (f"（{short}）" if short else "")
            lines.append(f"- **{label}** — {h.get('status')}　[来源]({h.get('source_url')})")
            lines.append("")

    norm_source_hits = result.get("norm_source_hits", [])
    if norm_source_hits:
        lines.append(f"## 匹配私域规范（{len(norm_source_hits)}）")
        for h in norm_source_hits:
            name = h.get("name")
            short = h.get("short_name")
            label = f"{name}" + (f"（{short}）" if short else "")
            lines.append(
                f"- **{label}** — {h.get('source_type')} / {h.get('authority') or 'authority 未知'}"
            )
        lines.append("")

    article_hits = result.get("article_hits", [])
    if article_hits:
        lines.append(f"## 匹配条文（{len(article_hits)}）")
        for h in article_hits:
            title = h.get("law_short_title") or f"《{h.get('law_title')}》"
            num = h.get("number_display")
            text = h.get("text", "").strip()
            lines.append(f"### {title} {num}")
            lines.append("")
            lines.append(f"> {text}")
            lines.append("")
            lines.append(
                f"- 来源：{h.get('source_url')}"
                f"　（距上次核查：{h.get('freshness_days')} 天）"
            )
            if h.get("match_kind"):
                lines.append(f"- 匹配类型：{h.get('match_kind')}")
            lines.append("")

    norm_clause_hits = result.get("norm_clause_hits", [])
    if norm_clause_hits:
        lines.append(f"## 匹配私域规范条款（{len(norm_clause_hits)}）")
        for h in norm_clause_hits:
            title = h.get("norm_source_name")
            num = h.get("number_display") or h.get("number") or "未编号条款"
            text = h.get("text", "").strip()
            lines.append(f"### {title} {num}")
            lines.append("")
            lines.append(f"> {text}")
            lines.append("")
            lines.append(
                f"- 类型：{h.get('norm_source_type')}"
                f"　（距上次核查：{h.get('freshness_days')} 天）"
            )
            lines.append("")

    if not law_hits and not article_hits and not norm_source_hits and not norm_clause_hits:
        lines.append("_无匹配结果。_")

    return "\n".join(lines).rstrip() + "\n"


def source_verify_to_markdown(report: dict) -> str:
    source = report.get("source") or ""
    status = "通过" if report.get("ok") else "失败"
    lines = [
        f"# 数据源 verify：{source}",
        "",
        f"- 状态：{status}",
        f"- 查询：{report.get('query')}",
        f"- 条文：{report.get('article') or '未检查'}",
        f"- 核查时间：{report.get('checked_at')}",
        "",
        "## 步骤",
    ]
    for step in report.get("steps") or []:
        mark = "OK" if step.get("ok") else "FAIL"
        lines.append(f"- [{mark}] {step.get('step')}: {step.get('message')}")

    candidates = report.get("candidates") or []
    if candidates:
        lines.extend(["", f"## 候选（{len(candidates)}）"])
        for candidate in candidates:
            lines.append(
                f"- {candidate.get('title')} / bbbs={candidate.get('bbbs')} / "
                f"status={candidate.get('status')} / released={candidate.get('released_at')}"
            )

    law = report.get("law") or {}
    if law:
        lines.extend(
            [
                "",
                "## 清洗结果",
                f"- 法规：{law.get('title')}",
                f"- 状态：{law.get('status')}",
                f"- 条文数：{law.get('article_count')}",
                f"- 来源：{law.get('source_url')}",
                f"- Hash：{law.get('source_hash')}",
            ]
        )

    article = report.get("article_match") or {}
    if article:
        lines.extend(
            [
                "",
                f"## 条文定位：{article.get('number_display') or article.get('number')}",
                "",
                f"> {article.get('text_preview')}",
            ]
        )

    return "\n".join(lines).rstrip() + "\n"


def ensure_to_markdown(report: dict) -> str:
    status = "通过" if report.get("ok") else "部分失败"
    lines = [
        "# ensure 补库报告",
        "",
        f"- 状态：{status}",
        f"- 数据源：{report.get('source')}",
        f"- 数据库：`{report.get('db_path')}`",
        f"- 请求：{report.get('requested_count', 0)} / 去重后 {report.get('unique_count', 0)}",
        f"- 本地已有：{report.get('present_count', 0)}",
        f"- 内置 fixture 入库：{report.get('fixture_loaded_count', 0)}",
        f"- 新补入库：{report.get('fetched_count', 0)}",
        f"- 跳过 / 无须处理：{report.get('skipped_count', 0)}",
        f"- 失败：{report.get('failed_count', 0)}",
        "",
        "## 明细",
    ]
    for item in report.get("items") or []:
        name = item.get("name") or ""
        status = item.get("status") or ""
        reason = item.get("reason") or ""
        lines.append(f"- {name} — {status} / {reason}")
        if item.get("message"):
            lines.append(f"  {item.get('message')}")
        fetch = item.get("fetch") or {}
        if fetch:
            matched_id = fetch.get("matched_id") or fetch.get("matched_bbbs")
            lines.append(
                f"  匹配：{fetch.get('matched_title')} / id={matched_id} / "
                f"条文数={fetch.get('article_count')}"
            )
        candidates = item.get("candidates") or []
        if candidates:
            preview = "; ".join(
                f"{c.get('title')}({c.get('status')}, id={c.get('id') or c.get('bbbs')})"
                for c in candidates[:3]
            )
            lines.append(f"  候选：{preview}")
    return "\n".join(lines).rstrip() + "\n"


def rebuild_clean_to_markdown(report: dict) -> str:
    status = "通过" if report.get("ok") else "失败"
    lines = [
        "# rebuild-clean 报告",
        "",
        f"- 状态：{status}",
        f"- 数据库：`{report.get('db_path')}`",
        f"- dry-run：{report.get('dry_run')}",
        f"- cleaning schema：{report.get('cleaning_schema_version')}",
        f"- 法规数：{report.get('law_count', 0)}",
        f"- 私域规范数：{report.get('norm_count', 0)}",
        f"- 有变化：{report.get('changed_count', 0)}",
        f"- 已重建：{report.get('rebuilt_count', 0)}",
        f"- 跳过：{report.get('skipped_count', 0)}",
        f"- 错误：{report.get('error_count', 0)}",
    ]
    items = report.get("items") or []
    if items:
        lines.extend(["", "## 明细"])
        for item in items:
            mark = "changed" if item.get("changed") else "unchanged"
            if item.get("kind") == "norm_source":
                lines.append(
                    f"- {item.get('title')} (`{item.get('norm_source_id')}`) — {mark}; "
                    f"clauses {item.get('clause_count_before', 0)}"
                    f"→{item.get('clause_count_after', 0)}; "
                    f"text_changed={item.get('clause_text_changed_count', 0)}; "
                    f"number_changed={item.get('clause_number_changed_count', 0)}"
                )
            else:
                lines.append(
                    f"- {item.get('title')} (`{item.get('law_id')}`) — {mark}; "
                    f"aliases {len(item.get('aliases_before') or [])}"
                    f"→{len(item.get('aliases_after') or [])}; "
                    f"text_changed={item.get('article_text_changed_count', 0)}; "
                    f"part_changed={item.get('article_part_changed_count', 0)}"
                )
    errors = report.get("errors") or []
    if errors:
        lines.extend(["", "## 错误"])
        for error in errors:
            lines.append(
                f"- {error.get('title')} (`{error.get('law_id') or error.get('norm_source_id')}`): "
                f"{error.get('error')} {error.get('message')}"
            )
    return "\n".join(lines).rstrip() + "\n"


def commentary_import_to_markdown(payload: dict) -> str:
    return (
        "# commentary import\n"
        f"- book：{payload.get('book_title')} (`{payload.get('book_id')}`)\n"
        f"- items：{payload.get('items_loaded', 0)}\n"
        f"- license：{payload.get('license_scope')}\n"
    )


def commentary_books_to_markdown(books: list[dict]) -> str:
    if not books:
        return "_未导入 commentary 书目。_\n"
    lines = ["# commentary books", ""]
    for book in books:
        parts = [book.get("title") or book.get("id")]
        if book.get("author"):
            parts.append(str(book["author"]))
        if book.get("publisher"):
            parts.append(str(book["publisher"]))
        lines.append(
            f"- {' / '.join(part for part in parts if part)} "
            f"(`{book.get('id')}`): {book.get('commentary_count', 0)} 条"
        )
    return "\n".join(lines) + "\n"


def commentary_article_to_markdown(payload: dict) -> str:
    if not payload.get("found"):
        return "_未找到指定法规 / 条文，无法查询 commentary。_\n"
    law = payload.get("law") or {}
    article = payload.get("article") or {}
    article_label = article.get("number_display") or article.get("number")
    lines = [
        f"# {law.get('short_title') or law.get('title')} {article_label}",
        f"- commentary：{payload.get('commentary_count', 0)} 条",
    ]
    if not payload.get("commentaries"):
        lines.append("")
        lines.append("_本地暂无该条 commentary。_")
        return "\n".join(lines) + "\n"
    for item in payload.get("commentaries") or []:
        book = item.get("book") or {}
        page = ""
        if item.get("page_start"):
            if item.get("page_end") and item.get("page_end") != item.get("page_start"):
                page = f", pp. {item.get('page_start')}-{item.get('page_end')}"
            else:
                page = f", p. {item.get('page_start')}"
        lines.extend(
            [
                "",
                f"## {book.get('title')} ({item.get('qa_status')})",
                f"- 来源：{book.get('source_name')}{page}",
                f"- 授权边界：{item.get('license_scope') or book.get('license_scope')}",
            ]
        )
        if item.get("summary"):
            lines.append(f"- 摘要：{item.get('summary')}")
        if item.get("excerpt"):
            lines.append("")
            lines.append(f"> {item.get('excerpt')}")
    return "\n".join(lines) + "\n"


def law_to_markdown(law: dict) -> str:
    if law is None:
        return "_未找到该法规。_\n"
    lines: list[str] = []
    title = law.get("title")
    short = law.get("short_title")
    lines.append(f"# 《{title}》" + (f"（{short}）" if short else ""))
    lines.append("")
    lines.append(f"- 效力级别：{law.get('level')}")
    lines.append(f"- 状态：{law.get('status')}")
    if law.get("issuing_body"):
        lines.append(f"- 制定机关：{law.get('issuing_body')}")
    if law.get("document_number"):
        lines.append(f"- 发文号：{law.get('document_number')}")
    if law.get("released_at"):
        lines.append(f"- 发布日期：{law.get('released_at')}")
    if law.get("effective_at"):
        lines.append(f"- 施行日期：{law.get('effective_at')}")
    current_revision = law.get("current_revision")
    if current_revision:
        lines.append(f"- 当前版本：{current_revision.get('version_label')}")
    revision_count = law.get("revision_count")
    if revision_count:
        lines.append(f"- 版本数：{revision_count}")
    lines.append(f"- 来源：{law.get('source_url')}")
    fd = law.get("freshness_days")
    if fd is not None:
        lines.append(f"- 最后核查：{law.get('source_checked_at')}（{fd} 天前）")
    coverage = law.get("articles_coverage")
    if coverage == "stub":
        lines.append(
            "- 数据覆盖：**stub**（仅 metadata，条文尚未入库；按 docs/DATA_INDEX.md §3 补全）"
        )
    elif coverage == "seed":
        article_count = law.get("article_count")
        suffix = f"，当前仅 {article_count} 条" if article_count is not None else ""
        lines.append(
            "- 数据覆盖：**seed**（样例 / 核心条款，不保证全文完整"
            f"{suffix}；引用前优先 fetch 补全）"
        )
    elif coverage == "populated":
        article_count = law.get("article_count")
        if article_count is not None:
            lines.append(f"- 数据覆盖：populated（{article_count} 条已入库）")
    lines.append("")
    articles = law.get("articles") or []
    if articles:
        lines.append(f"## 条文（共 {len(articles)} 条）")
        lines.append("")
        for a in articles:
            header = a.get("number_display")
            if a.get("title"):
                header += f"（{a.get('title')}）"
            if a.get("part"):
                lines.append(f"### {a.get('part')}")
            lines.append(f"**{header}**")
            lines.append("")
            lines.append(a.get("text", "").strip())
            lines.append("")
    revisions = law.get("revisions") or []
    if revisions:
        lines.append("## 版本记录")
        for revision in revisions[:5]:
            released = revision.get("released_at") or "日期未知"
            effective = revision.get("effective_at")
            label = revision.get("version_label")
            if effective and effective != released:
                lines.append(f"- {label} — {released} / {effective}")
            else:
                lines.append(f"- {label} — {released}")
    return "\n".join(lines).rstrip() + "\n"


def article_to_markdown(
    payload: dict,
    *,
    footer: str = "full",
    number_style: str = "display",
    with_title: bool = False,
) -> str:
    if payload is None or payload.get("article") is None:
        law = (payload or {}).get("law")
        if law:
            return (
                f"_在《{law.get('title')}》中未找到第 "
                f"{(payload or {}).get('requested_number')} 条。_\n"
            )
        return "_未找到指定法规或条文。_\n"

    law = payload["law"]
    article = payload["article"]
    lines: list[str] = []
    num = _article_number_label(article, number_style=number_style)
    title_suffix = ""
    if with_title:
        article_title = (article.get("title") or "").strip()
        if article_title:
            title_suffix = f" 【{article_title}】"
    lines.append(f"## {_law_label(law)} {num}{title_suffix}")
    lines.append("")
    lines.append(f"> {article.get('text', '').strip()}")

    if footer == "none":
        return "\n".join(lines) + "\n"

    if footer == "compact":
        compact = _compact_article_footer(law)
        if compact:
            lines.append("")
            lines.append(compact)
        return "\n".join(lines) + "\n"

    lines.append("")
    lines.append("---")
    lines.append(f"- 状态：{law.get('status')}")
    current_revision = law.get("current_revision")
    if current_revision:
        lines.append(f"- 当前版本：{current_revision.get('version_label')}")
    selected_revision = law.get("selected_revision")
    if selected_revision and selected_revision != current_revision:
        lines.append(f"- 历史版本：{selected_revision.get('version_label')}")
    lines.append(f"- 来源：{law.get('source_url')}")
    freshness = _freshness_label(law)
    if freshness:
        lines.append(f"- 最后核查：{freshness}")
    return "\n".join(lines) + "\n"


def articles_to_markdown(
    payload: dict,
    *,
    number_style: str = "display",
    footer: str = "full",
    with_title: bool = False,
) -> str:
    if payload is None or payload.get("law") is None:
        return "_未找到指定法规或条文列表。_\n"
    law = payload["law"]
    lines: list[str] = []
    if footer != "none":
        lines.extend(
            [
                f"# {_law_label(law)} 批量取条",
                "",
                f"- 请求：{payload.get('item_count', 0)} 条",
                f"- 命中：{payload.get('found_count', 0)} 条",
                f"- 缺失：{payload.get('missing_count', 0)} 条",
            ]
        )
        if payload.get("as_of"):
            lines.append(f"- 时点：{payload.get('as_of')}")
        lines.append("")
    for item in payload.get("items") or []:
        article = item.get("article")
        if article is None:
            lines.append(
                f"## {item.get('requested_number')}（未找到，normalized={item.get('number')}）"
            )
            lines.append("")
            continue
        title_suffix = ""
        if with_title:
            article_title = (article.get("title") or "").strip()
            if article_title:
                title_suffix = f" 【{article_title}】"
        lines.append(
            f"## {_article_number_label(article, number_style=number_style)}{title_suffix}"
        )
        if article.get("part"):
            lines.append(f"_位置：{article.get('part')}_")
        lines.append("")
        lines.append(f"> {article.get('text', '').strip()}")
        lines.append("")
    if footer == "compact":
        compact = _compact_article_footer(law)
        if compact:
            lines.append(compact)
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _short_law_label(law: dict | None) -> str:
    if not law:
        return ""
    return (
        (law.get("short_title") or "").strip()
        or (law.get("title") or "").strip()
        or (law.get("id") or "").strip()
    )


def _collapse_text(text: str | None) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def _article_inline_line(law: dict, article: dict) -> str:
    short = _short_law_label(law)
    number = article.get("number") or article.get("number_display") or ""
    text = _collapse_text(article.get("text"))
    if short and number:
        prefix = f"{short}§{number}"
    elif number:
        prefix = f"§{number}"
    else:
        prefix = short
    return f"{prefix} {text}".rstrip()


def article_to_bare(payload: dict | None) -> str:
    """只输出条文正文，无 markdown header / footer / 引用号。"""

    if payload is None or payload.get("article") is None:
        return ""
    return (payload["article"].get("text") or "").strip() + "\n"


def article_to_inline(payload: dict | None) -> str:
    """单行 ``<short_title>§<number> <text>`` 形式，便于直接拼笔记 / grep。"""

    if payload is None or payload.get("article") is None:
        return ""
    return _article_inline_line(payload.get("law") or {}, payload["article"]) + "\n"


def article_to_card(payload: dict | None) -> str:
    """Agent-facing compact card for a single article lookup.

    It keeps the text and provenance in two stable lines so agents do not need
    to pipe JSON into ad-hoc Python for routine citation extraction.
    """

    if payload is None or payload.get("article") is None:
        name = (payload or {}).get("name") or ((payload or {}).get("law") or {}).get("title")
        number = (payload or {}).get("number") or (payload or {}).get("requested_number")
        reason = (payload or {}).get("reason") or "not_found"
        hint = (payload or {}).get("hint")
        lines = [f"not_found: {name or '?'} §{number or '?'} ({reason})"]
        if hint:
            lines.append(f"hint: {hint}")
        return "\n".join(lines) + "\n"

    law = payload.get("law") or {}
    article = payload["article"]
    title = law.get("title") or law.get("short_title") or law.get("id") or "?"
    number = article.get("number") or article.get("number_display") or "?"
    text = _collapse_text(article.get("text"))
    source_parts = [
        str(part)
        for part in (
            law.get("status"),
            law.get("source_name"),
            law.get("source_url"),
            _freshness_label(law),
        )
        if part
    ]
    lines = [f"《{title}》§{number}: {text}"]
    if source_parts:
        lines.append("source: " + " | ".join(source_parts))
    return "\n".join(lines) + "\n"


def articles_to_bare(payload: dict | None) -> str:
    if payload is None:
        return ""
    blocks: list[str] = []
    for item in payload.get("items") or []:
        article = item.get("article")
        if article is None:
            continue
        text = (article.get("text") or "").strip()
        if text:
            blocks.append(text)
    return ("\n\n".join(blocks) + "\n") if blocks else ""


def articles_to_inline(payload: dict | None) -> str:
    if payload is None or payload.get("law") is None:
        return ""
    law = payload["law"]
    lines: list[str] = []
    for item in payload.get("items") or []:
        article = item.get("article")
        if article is None:
            continue
        lines.append(_article_inline_line(law, article))
    return ("\n".join(lines) + "\n") if lines else ""


def articles_batch_to_markdown(
    payload: dict | None,
    *,
    number_style: str = "display",
    footer: str = "full",
    with_title: bool = False,
) -> str:
    """多法批量取条 Markdown 输出。"""

    if payload is None:
        return "_未找到指定法规或条文列表。_\n"
    sections = payload.get("sections") or []
    if not sections:
        return "_未找到指定法规或条文列表。_\n"
    parts: list[str] = []
    if footer != "none":
        parts.append("# 多法批量取条")
        parts.append("")
        parts.append(f"- 状态：{'通过' if payload.get('ok') else '部分失败'}")
        parts.append(f"- 法规数：{payload.get('law_count', 0)}")
        parts.append(f"- 请求总数：{payload.get('item_count', 0)}")
        parts.append(f"- 命中：{payload.get('found_count', 0)}")
        parts.append(f"- 缺失：{payload.get('missing_count', 0)}")
        parts.append(f"- 失败分组：{payload.get('failed_section_count', 0)}")
        if payload.get("as_of"):
            parts.append(f"- 时点：{payload.get('as_of')}")
        parts.append("")
    for section in sections:
        result = section.get("result")
        if result is None or result.get("law") is None:
            label = "未提供条号" if section.get("error") == "missing_numbers" else "未找到法规"
            parts.append(f"## {section.get('name')}（{label}，跳过）")
            parts.append("")
            continue
        # 复用单法 articles_to_markdown 渲染，但 footer 一律 none，避免重复汇总头
        body = articles_to_markdown(
            result,
            number_style=number_style,
            footer="none",
            with_title=with_title,
        )
        parts.append(f"## {_law_label(result['law'])}")
        parts.append("")
        parts.append(body.rstrip())
        parts.append("")
    if footer == "compact":
        parts.append("")
        parts.append(
            f"[共 {payload.get('law_count', 0)} 部法规 / 命中 "
            f"{payload.get('found_count', 0)} 条 / 缺失 "
            f"{payload.get('missing_count', 0)} 条 / 失败分组 "
            f"{payload.get('failed_section_count', 0)}]"
        )
    return "\n".join(parts).rstrip() + "\n"


def articles_batch_to_bare(payload: dict | None) -> str:
    if payload is None:
        return ""
    blocks: list[str] = []
    for section in payload.get("sections") or []:
        result = section.get("result")
        if result is None:
            continue
        text = articles_to_bare(result)
        if text.strip():
            blocks.append(text.rstrip())
    return ("\n\n".join(blocks) + "\n") if blocks else ""


def articles_batch_to_inline(payload: dict | None) -> str:
    if payload is None:
        return ""
    lines: list[str] = []
    for section in payload.get("sections") or []:
        result = section.get("result")
        if result is None:
            continue
        text = articles_to_inline(result)
        if text.strip():
            lines.append(text.rstrip())
    return ("\n".join(lines) + "\n") if lines else ""


def outline_to_markdown(payload: dict) -> str:
    if payload is None or payload.get("law") is None:
        return "_未找到指定法规。_\n"
    law = payload["law"]
    lines = [
        f"# {_law_label(law)} 条文目录",
        "",
        f"- 条文总数：{payload.get('article_count', 0)}",
        f"- 返回条目：{payload.get('item_count', 0)}",
    ]
    if payload.get("part_filter"):
        lines.append(f"- 章节过滤：{payload.get('part_filter')}")
    lines.append("")
    for item in payload.get("items") or []:
        label = item.get("number_display") or item.get("number")
        if item.get("part"):
            label = f"{label} / {item.get('part')}"
        preview = item.get("text_preview") or ""
        lines.append(f"- **{label}**：{preview}")
    return "\n".join(lines).rstrip() + "\n"


def outline_to_markdown_with_text(
    payload: dict | None,
    *,
    number_style: str = "display",
    footer: str = "full",
    with_title: bool = False,
) -> str:
    """outline --with-text 的 markdown 输出：每条以 ``## 第N条`` + 正文呈现，
    复用 ``articles_to_markdown`` 的渲染口径，仅替换 header 为 outline 语义。
    """

    if payload is None or payload.get("law") is None:
        return "_未找到指定法规或条文列表。_\n"
    law = payload["law"]
    lines: list[str] = []
    if footer != "none":
        lines.extend(
            [
                f"# {_law_label(law)} 章节正文",
                "",
                f"- 章节过滤：{payload.get('part_filter') or '（全部）'}",
                f"- 条文总数：{payload.get('article_count', 0)}",
                f"- 返回条目：{payload.get('item_count', 0)}",
            ]
        )
        lines.append("")
    for item in payload.get("items") or []:
        article = item.get("article")
        if article is None:
            continue
        title_suffix = ""
        if with_title:
            article_title = (article.get("title") or "").strip()
            if article_title:
                title_suffix = f" 【{article_title}】"
        lines.append(
            f"## {_article_number_label(article, number_style=number_style)}{title_suffix}"
        )
        if article.get("part"):
            lines.append(f"_位置：{article.get('part')}_")
        lines.append("")
        lines.append(f"> {(article.get('text') or '').strip()}")
        lines.append("")
    if footer == "compact":
        compact = _compact_article_footer(law)
        if compact:
            lines.append(compact)
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def cited_by_to_markdown(payload: dict | None) -> str:
    if payload is None:
        return "_无 cited-by 输出。_\n"
    if payload.get("error"):
        return (
            f"_cited-by 失败（{payload.get('error')}）：{payload.get('message')}_\n"
        )
    target = payload.get("target") or {}
    target_law = target.get("law") or {}
    target_article = target.get("article") or {}
    if target_article:
        target_number = (
            target_article.get("number_display")
            or f"第{target_article.get('number', '')}条"
        )
    else:
        raw = target.get("normalized_number") or target.get("requested_number") or ""
        target_number = f"第{raw}条"
    lines = [
        f"# 引用追溯：{_law_label(target_law)} {target_number}",
        "",
        f"- 目标条文：{target_number}（normalized={target.get('normalized_number')}）",
        f"- 命中：{payload.get('hit_count', 0)}",
        f"- 扫描法规条文：{payload.get('scanned_count', 0)}",
        f"- 同部法规自引：{'包含' if payload.get('include_self') else '排除'}",
    ]
    law_filter = payload.get("law_filter") or {}
    if law_filter.get("resolved"):
        names = ", ".join(
            item.get("short_title") or item.get("title") or ""
            for item in law_filter["resolved"]
        )
        lines.append(f"- 范围限定：{names}")
    if law_filter.get("unresolved"):
        lines.append(f"- 未识别名称：{', '.join(law_filter['unresolved'])}")
    lines.append("")
    hits = payload.get("hits") or []
    if not hits:
        lines.append("_未在已入库条文里找到引用。_")
        return "\n".join(lines).rstrip() + "\n"
    lines.append(f"## 引用条文（{len(hits)}）")
    for hit in hits:
        host_law = hit.get("law") or {}
        article = hit.get("article") or {}
        host_label = host_law.get("short_title") or host_law.get("title") or host_law.get("id")
        article_number = article.get("number_display") or article.get("number") or "未知条号"
        lines.append("")
        lines.append(f"### {host_label} {article_number}")
        if article.get("part"):
            lines.append(f"_位置：{article.get('part')}_")
        if hit.get("snippet"):
            lines.append(f"> {hit.get('snippet')}")
        if hit.get("matched_text"):
            lines.append(f"- 匹配：`{hit.get('matched_text')}`")
    return "\n".join(lines).rstrip() + "\n"


def list_to_markdown(laws: list[dict]) -> str:
    if not laws:
        return "_无匹配法规。_\n"
    lines: list[str] = [f"# 法规列表（{len(laws)}）", ""]
    for law in laws:
        title = law.get("title")
        short = law.get("short_title")
        label = title + (f"（{short}）" if short else "")
        lines.append(
            f"- {label} — {law.get('level')}/{law.get('status')} — "
            f"{law.get('released_at') or '发布日期未知'}"
        )
    return "\n".join(lines) + "\n"


def status_to_markdown(report: dict) -> str:
    lines: list[str] = ["# chinalaw 数据健康", ""]
    lines.append(f"- 数据库：`{report.get('db_path')}`")
    lines.append(f"- schema 版本：{report.get('schema_version')}")
    lines.append(f"- 法规数：{report.get('laws')}")
    lines.append(f"- 条文数：{report.get('articles')}")
    lines.append(f"- 版本数：{report.get('revisions')}")
    lines.append(f"- 规范包数：{report.get('norm_packs', 0)}")
    lines.append(f"- 私域规范数：{report.get('norm_sources', 0)}")
    lines.append(f"- 私域规范条款数：{report.get('norm_clauses', 0)}")
    lines.append(f"- 规范关系数：{report.get('law_relations', 0)}")
    lines.append(f"- 时间效力规则数：{report.get('applicability_rules', 0)}")
    lines.append(f"- 最近一次同步：{report.get('last_sync_at') or '尚未同步'}")
    if report.get("last_applicability_sync_at"):
        lines.append(f"- 最近一次时间效力规则同步：{report.get('last_applicability_sync_at')}")
    fd = report.get("oldest_freshness_days")
    if fd is not None:
        lines.append(
            f"- 最旧核查时间：{report.get('oldest_source_checked_at')}（{fd} 天前）"
        )
    alias_agent = report.get("alias_agent")
    if alias_agent:
        lines.append(
            f"- alias_agent：{alias_agent}（启用方式：`export CHINALAW_USE_ALIAS_AGENT=1`）"
        )
    by_level = report.get("by_level") or []
    if by_level:
        lines.append("")
        lines.append("## 按效力级别")
        for item in by_level:
            lines.append(f"- {item['level']}: {item['count']}")
    by_status = report.get("by_status") or []
    if by_status:
        lines.append("")
        lines.append("## 按状态")
        for item in by_status:
            lines.append(f"- {item['status']}: {item['count']}")
    by_coverage = report.get("by_articles_coverage") or []
    if by_coverage:
        lines.append("")
        lines.append("## 按数据覆盖")
        for item in by_coverage:
            lines.append(f"- {item['coverage']}: {item['count']}")
    stub_laws = report.get("stub_laws") or []
    if stub_laws:
        lines.append("")
        lines.append(f"## Stub 法规（仅 metadata，待补全条文，{len(stub_laws)}）")
        for sl in stub_laws:
            label = sl.get("title")
            short = sl.get("short_title")
            if short:
                label = f"{label}（{short}）"
            lines.append(f"- `{sl.get('id')}` — {label}")
    seed_laws = report.get("seed_laws") or []
    if seed_laws:
        lines.append("")
        lines.append(f"## Seed 法规（样例条文，不保证全文，{len(seed_laws)}）")
        for sl in seed_laws:
            label = sl.get("title")
            short = sl.get("short_title")
            if short:
                label = f"{label}（{short}）"
            lines.append(f"- `{sl.get('id')}` — {label}")
    return "\n".join(lines) + "\n"


def probe_to_markdown(report: dict) -> str:
    lines: list[str] = ["# 数据源探测", ""]
    lines.append(f"- 来源：`{report.get('source')}`")
    lines.append(f"- 状态码：{report.get('status_code')}")
    if report.get("title"):
        lines.append(f"- 标题：{report.get('title')}")
    lines.append(f"- 页面形态：{report.get('page_shape')}")
    lines.append(f"- 首页：{report.get('final_url') or report.get('homepage_url')}")
    if report.get("main_script_url"):
        lines.append(f"- 主脚本：{report.get('main_script_url')}")
    if report.get("stylesheet_url"):
        lines.append(f"- 样式表：{report.get('stylesheet_url')}")
    if report.get("source_last_modified"):
        lines.append(f"- Last-Modified：{report.get('source_last_modified')}")
    if report.get("source_etag"):
        lines.append(f"- ETag：{report.get('source_etag')}")
    if report.get("error"):
        lines.append(f"- 错误：{report.get('error')}")
    sections = report.get("detected_sections") or []
    if sections:
        lines.append("")
        lines.append("## 探测到的核心栏目")
        for section in sections:
            lines.append(f"- {section}")
    return "\n".join(lines) + "\n"


def history_to_markdown(payload: dict) -> str:
    if payload is None:
        return "_未找到该法规。_\n"
    law = payload["law"]
    revisions = payload.get("revisions") or []
    lines = [f"# 《{law.get('title')}》版本历史", ""]
    lines.append(f"- 版本数：{payload.get('revision_count', 0)}")
    current_revision = payload.get("current_revision")
    if current_revision:
        lines.append(f"- 当前版本：{current_revision.get('version_label')}")
    lines.append("")
    if not revisions:
        lines.append("_暂无版本记录。_")
        return "\n".join(lines) + "\n"
    for revision in revisions:
        released = revision.get("released_at") or "日期未知"
        effective = revision.get("effective_at")
        label = revision.get("version_label")
        if effective and effective != released:
            lines.append(f"- {label} — {released} / {effective}")
        else:
            lines.append(f"- {label} — {released}")
    return "\n".join(lines) + "\n"


def diff_to_markdown(payload: dict) -> str:
    if payload is None:
        return "_未找到该法规或指定版本。_\n"
    lines = [f"# 《{payload.get('title')}》版本差异", ""]
    lines.append(f"- 起始时点：{payload.get('from_as_of')}")
    lines.append(f"- 目标时点：{payload.get('to_as_of')}")
    if payload.get("from_revision"):
        lines.append(f"- 起始版本：{payload['from_revision'].get('version_label')}")
    if payload.get("to_revision"):
        lines.append(f"- 目标版本：{payload['to_revision'].get('version_label')}")
    summary = payload.get("summary") or {}
    lines.append(f"- 新增条文：{summary.get('added', 0)}")
    lines.append(f"- 删除条文：{summary.get('removed', 0)}")
    lines.append(f"- 修改条文：{summary.get('changed', 0)}")

    added = payload.get("added") or []
    if added:
        lines.append("")
        lines.append("## 新增条文")
        for article in added[:10]:
            lines.append(f"- {article.get('number_display')}")

    removed = payload.get("removed") or []
    if removed:
        lines.append("")
        lines.append("## 删除条文")
        for article in removed[:10]:
            lines.append(f"- {article.get('number_display')}")

    changed = payload.get("changed") or []
    if changed:
        lines.append("")
        lines.append("## 修改条文")
        for item in changed[:10]:
            before_text = (item.get("before") or {}).get("text", "").strip()
            after_text = (item.get("after") or {}).get("text", "").strip()
            lines.append(f"### {item.get('number_display')}")
            lines.append("")
            lines.append(f"- 变更前：{before_text}")
            lines.append(f"- 变更后：{after_text}")
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def trace_to_markdown(payload: dict | None) -> str:
    if payload is None:
        return "_未找到该法规。_\n"
    input_payload = payload.get("input") or {}
    law = payload.get("law") or {}
    title = law.get("title") or input_payload.get("name") or "未解析法规"
    lines = [f"# 条文追溯：{title}", ""]
    lines.append(f"- 起始时点：{input_payload.get('from_as_of')}")
    lines.append(f"- 目标时点：{input_payload.get('to_as_of')}")
    if input_payload.get("number"):
        lines.append(f"- 输入条号：{input_payload.get('number')}")
    if input_payload.get("text"):
        lines.append(f"- 输入文本：{input_payload.get('text')}")
    if input_payload.get("items"):
        lines.append(f"- 指定项号：{', '.join(input_payload.get('items') or [])}")
    if payload.get("error"):
        lines.append("")
        message = payload.get("message") or payload.get("hint") or ""
        lines.append(f"! {payload.get('error')}: {message}")
        versions = payload.get("available_versions") or []
        if versions:
            lines.append("")
            lines.append("## 本地可用版本")
            for version in versions:
                version_law = version.get("law") or {}
                revision = version.get("revision") or {}
                label = revision.get("version_label") or version_law.get("title")
                lines.append(
                    f"- {version.get('as_of_version_date') or '?'}："
                    f"`{version_law.get('id')}` {label}"
                )
        return "\n".join(lines).rstrip() + "\n"

    lines.append(f"- 状态：{payload.get('status')}")
    lines.append(f"- 置信度：{payload.get('confidence')}")
    if payload.get("warning"):
        lines.append(f"- 警告：{payload.get('warning')}")

    from_payload = payload.get("from") or {}
    to_payload = payload.get("to") or {}
    source_article = from_payload.get("article") or {}
    target_article = to_payload.get("article") or {}
    lines.append("")
    lines.append("## 对应关系")
    lines.append(
        f"- 起始：{source_article.get('number_display') or source_article.get('number') or '?'}"
        f"（{from_payload.get('as_of')}）"
    )
    if target_article:
        lines.append(
            f"- 目标：{target_article.get('number_display') or target_article.get('number')}"
            f"（{to_payload.get('as_of')}）"
        )
    else:
        lines.append("- 目标：未达到可信阈值；请查看候选并人工复核")

    evidence = payload.get("evidence") or []
    if evidence:
        lines.append("")
        lines.append("## 依据")
        for item in evidence:
            lines.append(f"- {item}")

    diff = payload.get("diff") or {}
    lines.append("")
    lines.append("## 差异")
    lines.append(f"- 条号变化：{bool(diff.get('number_changed'))}")
    lines.append(f"- 文本变化：{bool(diff.get('text_changed'))}")
    lines.append(f"- 位置变化：{bool(diff.get('part_changed'))}")
    lines.append(f"- 文本相似度：{diff.get('similarity')}")

    candidates = payload.get("candidates") or []
    if candidates:
        lines.append("")
        lines.append("## 候选")
        for item in candidates[:5]:
            article = item.get("article") or {}
            lines.append(
                f"- {article.get('number_display') or article.get('number')}: "
                f"{item.get('status')} / confidence={item.get('confidence')}"
            )
    return "\n".join(lines).rstrip() + "\n"


def relation_to_markdown(payload: dict) -> str:
    law = payload.get("law") or {}
    title = law.get("title") or payload.get("identifier") or "未解析法规"
    lines = [f"# 规范关系：{title}", ""]
    for warning in payload.get("warnings") or []:
        lines.append(
            f"- [{warning.get('severity')}] {warning.get('code')}: "
            f"{warning.get('message')}"
        )
    relations = payload.get("relations") or []
    if not relations:
        lines.append("")
        lines.append("_暂无关系记录。_")
        return "\n".join(lines) + "\n"
    lines.append("")
    lines.append(f"## 关系（{len(relations)}）")
    for item in relations:
        from_title = item.get("from_law_title") or item.get("from_law_id")
        to_title = item.get("to_law_title") or item.get("to_law_id")
        lines.append(
            f"- `{item.get('relation_type')}`：{from_title} -> {to_title}"
            f"（{item.get('effective_at') or '生效时间未知'}）"
        )
        if item.get("notes"):
            lines.append(f"  说明：{item.get('notes')}")
    return "\n".join(lines) + "\n"


def applicable_to_markdown(payload: dict) -> str:
    lines = [f"# 时间效力线索：{payload.get('as_of')}", ""]
    if payload.get("topic"):
        lines.append(f"- 主题：{payload.get('topic')}")
    if payload.get("domain"):
        lines.append(f"- 场景：{payload.get('domain')}")
    law = payload.get("law")
    if isinstance(law, dict):
        lines.append(f"- 规范过滤：{law.get('title')}")
    elif law:
        lines.append(f"- 规范过滤：{law}")
    lines.append(f"- 命中规则：{payload.get('match_count', 0)}")
    warnings = payload.get("warnings") or []
    if warnings:
        lines.append("")
        lines.append("## 警告")
        for warning in warnings:
            lines.append(
                f"- [{warning.get('severity')}] {warning.get('code')}: "
                f"{warning.get('message')}"
            )
    matches = payload.get("matches") or []
    if not matches:
        return "\n".join(lines) + "\n"
    lines.append("")
    lines.append("## 规则")
    for rule in matches:
        lines.append(f"### {rule.get('topic')} / {rule.get('domain')}")
        lines.append("")
        lines.append(rule.get("rule_text", "").strip())
        if rule.get("transition_text"):
            lines.append("")
            lines.append(f"> {rule.get('transition_text')}")
        lines.append("")
        lines.append(
            f"- 主规范：`{rule.get('primary_law_id')}` "
            f"{rule.get('primary_law_title') or ''}"
        )
        if rule.get("fallback_law_id"):
            lines.append(
                f"- 旧法/备用线索：`{rule.get('fallback_law_id')}` "
                f"{rule.get('fallback_law_title') or ''}"
            )
        if rule.get("needs_fetch"):
            lines.append(
                "- 需补全："
                + "、".join(
                    f"{item.get('law_id')}({item.get('reason')})"
                    for item in rule.get("needs_fetch") or []
                )
            )
        source_url = rule.get("source_url") or "未填写 URL"
        lines.append(f"- 来源：{rule.get('source_name')} / {source_url}")
    return "\n".join(lines).rstrip() + "\n"


def pack_list_to_markdown(packs: list[dict]) -> str:
    if not packs:
        return "_暂无规范包。_\n"
    lines = [f"# 规范包列表（{len(packs)}）", ""]
    for pack in packs:
        summary = pack.get("summary") or "无摘要"
        lines.append(
            f"- **{pack.get('name')}** — {pack.get('item_count', 0)} 项 — "
            f"{pack.get('version_policy')} — {summary}"
        )
    return "\n".join(lines) + "\n"


def pack_to_markdown(pack: dict) -> str:
    if pack is None:
        return "_未找到该规范包。_\n"
    lines = [f"# 规范包：{pack.get('name')}", ""]
    lines.append(f"- ID：`{pack.get('id')}`")
    lines.append(f"- 版本策略：{pack.get('version_policy')}")
    lines.append(f"- 来源方式：{pack.get('source_kind')}")
    lines.append(f"- 项数：{pack.get('item_count', 0)}")
    resolved_item_count = pack.get("resolved_item_count")
    if resolved_item_count is not None:
        lines.append(f"- 已解析项：{resolved_item_count}")
    if pack.get("maintainer"):
        lines.append(f"- 维护者：{pack.get('maintainer')}")
    if pack.get("summary"):
        lines.append(f"- 摘要：{pack.get('summary')}")
    if pack.get("scope"):
        lines.append(f"- 适用范围：{pack.get('scope')}")
    lines.append("")

    items = pack.get("items") or []
    if not items:
        lines.append("_该规范包暂无成员。_")
        return "\n".join(lines) + "\n"

    lines.append("## 成员")
    for item in items:
        if item["item_type"] == "article":
            label = (
                f"{item.get('law_title') or item.get('law_id') or '未命名法规'}"
                f" {item.get('article_number_display') or item.get('article_number')}"
            )
        elif item["item_type"] == "law":
            label = item.get("law_title") or item.get("law_id") or "未命名法规"
        elif item["item_type"] == "norm_clause":
            source_label = (
                item.get("norm_source_name")
                or item.get("norm_source_id")
                or "未命名私域规范"
            )
            clause_label = (
                item.get("clause_number_display") or item.get("clause_number")
            )
            label = (
                f"{source_label} {clause_label}"
                if clause_label
                else source_label
            )
        elif item["item_type"] == "norm_source":
            label = (
                item.get("norm_source_name")
                or item.get("norm_source_id")
                or "未命名私域规范"
            )
        else:
            label = item.get("reference_text") or "未命名引用"
        lines.append(f"- **[{item.get('role')}]** {label}")
        if item.get("reason"):
            lines.append(f"  理由：{item.get('reason')}")
        if item.get("note"):
            lines.append(f"  备注：{item.get('note')}")
        resolved = item.get("resolved")
        if resolved and resolved.get("kind") == "article":
            article = resolved.get("article") or {}
            text = (article.get("text") or "").strip()
            if text:
                lines.append(f"  条文：{text}")
        elif resolved and resolved.get("kind") == "law":
            law = resolved.get("law") or {}
            lines.append(
                f"  已解析：{law.get('title')} / {law.get('status')} / {law.get('level')}"
            )
        elif resolved and resolved.get("kind") == "norm_clause":
            clause = resolved.get("clause") or {}
            text = (clause.get("text") or "").strip()
            if text:
                lines.append(f"  条款：{text}")
        elif resolved and resolved.get("kind") == "norm_source":
            source = resolved.get("source") or {}
            lines.append(
                f"  已解析：{source.get('name')} / {source.get('source_type')} / "
                f"{source.get('authority') or '制定主体未知'}"
            )
    return "\n".join(lines) + "\n"


def pack_import_to_markdown(payload: dict) -> str:
    return (
        f"已导入规范包：{payload.get('name')} "
        f"（{payload.get('items_loaded', 0)} 项）\n"
    )


def pack_item_add_to_markdown(payload: dict) -> str:
    action = "已添加" if payload.get("added") else "已存在，跳过"
    item = payload.get("item") or {}
    lines = [
        f"# 规范包成员：{action}",
        "",
        f"- 规范包：{payload.get('name')} (`{payload.get('pack_id')}`)",
        f"- 成员数：{payload.get('item_count', 0)}",
        f"- 类型：{item.get('item_type')}",
        f"- 角色：{item.get('role')}",
    ]
    if item.get("law_title") or item.get("law_id"):
        lines.append(f"- 法规：{item.get('law_title') or item.get('law_id')}")
    if item.get("article_number_display") or item.get("article_number"):
        lines.append(
            f"- 条文：{item.get('article_number_display') or item.get('article_number')}"
        )
    if item.get("norm_source_name") or item.get("norm_source_id"):
        lines.append(
            f"- 私域规范：{item.get('norm_source_name') or item.get('norm_source_id')}"
        )
    if item.get("clause_number_display") or item.get("clause_number"):
        lines.append(
            f"- 私域条款：{item.get('clause_number_display') or item.get('clause_number')}"
        )
    if item.get("reference_text"):
        lines.append(f"- 参考：{item.get('reference_text')}")
    if item.get("reason"):
        lines.append(f"- 理由：{item.get('reason')}")
    if item.get("note"):
        lines.append(f"- 备注：{item.get('note')}")
    return "\n".join(lines).rstrip() + "\n"


def pack_validation_to_markdown(report: dict) -> str:
    if report is None:
        return "_未找到该规范包。_\n"
    status = "通过" if report.get("ok") else "未通过"
    lines = [f"# 规范包校验：{report.get('name')}", ""]
    lines.append(f"- 状态：{status}")
    lines.append(f"- 成员数：{report.get('item_count', 0)}")
    lines.append(
        f"- 需解析成员：{report.get('resolved_item_count', 0)} / "
        f"{report.get('required_item_count', 0)}"
    )
    lines.append(f"- 错误：{report.get('error_count', 0)}")
    lines.append(f"- 警告：{report.get('warning_count', 0)}")
    issues = report.get("issues") or []
    if issues:
        lines.append("")
        lines.append("## 问题")
        for issue in issues:
            location = ""
            if issue.get("position") is not None:
                location = f"（第 {issue.get('position')} 项）"
            lines.append(
                f"- [{issue.get('severity')}] {issue.get('code')}{location}: "
                f"{issue.get('message')}"
            )
    return "\n".join(lines) + "\n"


def audit_to_markdown(report: dict) -> str:
    if report is None:
        return "_审查报告不可用。_\n"
    status = "通过" if report.get("ok") else "未通过"
    title = report.get("target") or report.get("path") or report.get("kind") or "audit"
    lines = [f"# 引用审查：{title}", ""]
    lines.append(f"- 状态：{status}")
    lines.append(f"- 引用数：{report.get('citation_count', 0)}")
    lines.append(f"- 已解析：{report.get('resolved_count', 0)}")
    lines.append(f"- 文本核对：{report.get('checked_text_count', 0)}")
    lines.append(f"- 错误：{report.get('error_count', 0)}")
    lines.append(f"- 警告：{report.get('warning_count', 0)}")
    if report.get("as_of"):
        lines.append(f"- 时点：{report.get('as_of')}")
    if report.get("strict"):
        lines.append("- 严格模式：是")
    if report.get("snapshot_path"):
        lines.append(f"- 快照：{report.get('snapshot_path')}")
        lines.append(f"- 快照记录数：{report.get('snapshot_record_count', 0)}")
    grounding_counts = report.get("grounding_counts") or {}
    if grounding_counts:
        lines.append(
            "- Grounding："
            f"verified={grounding_counts.get('verified', 0)} / "
            f"retrieved_only={grounding_counts.get('retrieved_only', 0)} / "
            f"ungrounded={grounding_counts.get('ungrounded', 0)}"
        )

    issues = report.get("issues") or []
    if issues:
        lines.append("")
        lines.append("## 全局问题")
        for issue in issues:
            lines.append(
                f"- [{issue.get('severity')}] {issue.get('code')}: {issue.get('message')}"
            )

    citations = report.get("citations") or []
    if citations:
        lines.append("")
        lines.append("## 引用明细")
        for index, citation in enumerate(citations, start=1):
            mark = "OK" if citation.get("ok") else "FAIL"
            law = citation.get("law") or {}
            article = citation.get("article") or {}
            law_title = law.get("title") or citation.get("law_input") or "?"
            number = (
                article.get("number_display")
                or citation.get("number_input")
                or citation.get("number")
                or "?"
            )
            lines.append(f"### {index}. [{mark}] 《{law_title}》{number}")
            lines.append("")
            lines.append(f"- 原文：{citation.get('raw')}")
            lines.append(f"- 解析：{citation.get('resolved')}")
            if citation.get("status"):
                lines.append(f"- 状态：{citation.get('status')}")
            grounding = citation.get("grounding") or {}
            if grounding:
                evidence = grounding.get("evidence_id") or "无"
                lines.append(
                    f"- Grounding：{grounding.get('status')} "
                    f"(evidence={evidence}, command={grounding.get('command') or '无'})"
                )
            text_match = citation.get("text_match") or {}
            lines.append(f"- 文本核对：{text_match.get('kind')}")
            if text_match.get("similarity") is not None:
                lines.append(f"- 相似度：{text_match.get('similarity')}")
            if citation.get("suggested_command"):
                lines.append(f"- 复核命令：`{citation.get('suggested_command')}`")
            for issue in citation.get("issues") or []:
                lines.append(
                    f"- [{issue.get('severity')}] {issue.get('code')}: "
                    f"{issue.get('message')}"
                )
            if article.get("text"):
                preview = _collapse_text(article.get("text"))
                if len(preview) > 180:
                    preview = preview[:180] + "…"
                lines.append("")
                lines.append(f"> {preview}")
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def norm_source_list_to_markdown(sources: list[dict]) -> str:
    if not sources:
        return "_暂无私域规范。_\n"
    lines = [f"# 私域规范列表（{len(sources)}）", ""]
    for source in sources:
        summary = source.get("binding_scope") or "适用范围未填写"
        lines.append(
            f"- **{source.get('name')}** — {source.get('source_type')} — "
            f"{source.get('clause_count', 0)} 条 — {summary}"
        )
    return "\n".join(lines) + "\n"


def norm_source_to_markdown(source: dict) -> str:
    if source is None:
        return "_未找到该私域规范。_\n"
    lines = [f"# 私域规范：{source.get('name')}", ""]
    lines.append(f"- ID：`{source.get('id')}`")
    lines.append(f"- 类型：{source.get('source_type')}")
    if source.get("authority"):
        lines.append(f"- 制定主体：{source.get('authority')}")
    if source.get("binding_scope"):
        lines.append(f"- 约束范围：{source.get('binding_scope')}")
    if source.get("jurisdiction"):
        lines.append(f"- 适用区域：{source.get('jurisdiction')}")
    if source.get("effective_at"):
        lines.append(f"- 生效时间：{source.get('effective_at')}")
    if source.get("repealed_at"):
        lines.append(f"- 失效时间：{source.get('repealed_at')}")
    lines.append(f"- 条款数：{source.get('clause_count', 0)}")
    lines.append("")
    clauses = source.get("clauses") or []
    if clauses:
        lines.append("## 条款")
        for clause in clauses:
            label = (
                clause.get("number_display")
                or clause.get("number")
                or f"第 {clause.get('position')} 项"
            )
            lines.append(f"### {label}")
            lines.append("")
            lines.append(clause.get("text", "").strip())
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def norm_clause_to_markdown(payload: dict) -> str:
    if payload is None or payload.get("clause") is None:
        source = (payload or {}).get("source")
        if source:
            return (
                f"_在《{source.get('name')}》中未找到第 "
                f"{(payload or {}).get('requested_number')} 条。_\n"
            )
        return "_未找到指定私域规范或条款。_\n"
    source = payload["source"]
    clause = payload["clause"]
    label = clause.get("number_display") or clause.get("number") or ""
    lines = [f"## {source.get('name')} {label}", ""]
    lines.append(f"> {clause.get('text', '').strip()}")
    lines.append("")
    lines.append("---")
    lines.append(f"- 类型：{source.get('source_type')}")
    if source.get("authority"):
        lines.append(f"- 制定主体：{source.get('authority')}")
    if source.get("binding_scope"):
        lines.append(f"- 约束范围：{source.get('binding_scope')}")
    return "\n".join(lines) + "\n"


def norm_source_import_to_markdown(payload: dict) -> str:
    lines: list[str] = [
        f"已导入私域规范：{payload.get('name')} "
        f"（{payload.get('clauses_loaded', 0)} 条）"
    ]
    warnings = payload.get("warnings") or []
    if warnings:
        lines.append("")
        lines.append(f"## 切分告警（{len(warnings)}）")
        for warning in warnings:
            lines.append(f"- [{warning.get('code')}] {warning.get('message')}")
    return "\n".join(lines) + "\n"


def norm_ingest_preview_to_markdown(payload: dict) -> str:
    if payload is None:
        return "_切分预览不可用。_\n"
    lines: list[str] = [
        f"# 私域规范切分预览：{payload.get('name')}",
        "",
        f"- 来源：`{payload.get('path')}`",
        f"- 格式：{payload.get('ingest_format')}",
        f"- 切分条数：{payload.get('clause_count', 0)}",
    ]
    short = payload.get("short_name")
    if short:
        lines.append(f"- 简称：{short}")
    if payload.get("id"):
        lines.append(f"- ID：{payload.get('id')}")
    warnings = payload.get("warnings") or []
    if warnings:
        lines.append("")
        lines.append(f"## 切分告警（{len(warnings)}）")
        for warning in warnings:
            lines.append(f"- [{warning.get('code')}] {warning.get('message')}")
    clauses = payload.get("clauses") or []
    if clauses:
        lines.append("")
        lines.append(f"## 切分明细（前 {min(len(clauses), 50)} 条）")
        for item in clauses[:50]:
            number = item.get("number_display") or item.get("number") or "未编号"
            preview = (item.get("preview") or "").strip()
            char_count = item.get("char_count") or 0
            lines.append(
                f"- **第 {item.get('position')} 项** {number}（{char_count} 字）："
                f"{preview}"
            )
        if len(clauses) > 50:
            lines.append(f"- … 后续 {len(clauses) - 50} 条已省略")
    lines.append("")
    lines.append("_dry-run：未入库，可使用 `chinalaw norm ingest` 不带 --dry-run 重跑导入。_")
    return "\n".join(lines).rstrip() + "\n"


def fetch_to_markdown(payload: dict) -> str:
    if payload.get("kind") == "law_fetch_candidates":
        lines = [f"# fetch 候选：{payload.get('name', '')}", ""]
        for cand in payload.get("candidates") or []:
            lines.append(
                f"- {cand.get('title', '')} — id={cand.get('id') or cand.get('bbbs', '')}"
                f" / released={cand.get('released_at', '')}"
                f" / status={cand.get('status', '')}"
            )
        return "\n".join(lines) + "\n"

    matched = payload.get("matched_title") or payload.get("name", "")
    matched_id = payload.get("matched_id") or payload.get("matched_bbbs", "")
    actions = []
    if payload.get("loaded"):
        actions.append("loaded")
    if payload.get("skipped"):
        actions.append("skipped (same source_hash)")
    if payload.get("dry_run"):
        actions.append("dry-run")
    if payload.get("wrote_fixture"):
        actions.append(f"wrote_fixture={payload['wrote_fixture']}")
    action_text = ", ".join(actions) or "no-op"

    lines = [
        f"# fetch:{payload.get('name', '')} → {matched}",
        f"- id: `{matched_id}`",
        f"- 动作: {action_text}",
        f"- 条文数: {payload.get('article_count', 0)}",
    ]
    article = payload.get("article")
    if article:
        text = (article.get("text") or "").strip().replace("\n", " ")
        if len(text) > 200:
            text = text[:200] + "…"
        lines.append("")
        lines.append(f"## {article.get('number_display', '')}")
        if article.get("part"):
            lines.append(f"_位置：{article['part']}_")
        lines.append("")
        lines.append(f"> {text}")
    return "\n".join(lines) + "\n"


def sync_to_markdown(payload: dict) -> str:
    if payload.get("kind") == "applicability_import":
        return (
            f"时间效力规则同步完成：{payload.get('relations_loaded', 0)} 条关系 / "
            f"{payload.get('rules_loaded', 0)} 条规则\n"
            + "\n".join(f"- {topic}" for topic in payload.get("topics", []))
            + "\n"
        )
    return (
        f"同步完成：{payload['laws_loaded']} 部法规 / "
        f"{payload['articles_loaded']} 条条文\n"
        + "\n".join(f"- {t}" for t in payload.get("titles", []))
        + "\n"
    )


def init_to_markdown(payload: dict) -> str:
    status = "通过" if payload.get("ok") else "需要处理"
    sync = payload.get("fixture_sync") or {}
    doctor_report = payload.get("doctor") or {}
    lines = [
        "# chinalaw init",
        "",
        f"- 状态：{status}",
        f"- 数据库：`{payload.get('db_path')}`",
        f"- 加载法规：{sync.get('laws_loaded', 0)} 部",
        f"- 加载条文：{sync.get('articles_loaded', 0)} 条",
        f"- doctor errors：{doctor_report.get('error_count', 0)}",
        f"- doctor warnings：{doctor_report.get('warning_count', 0)}",
    ]
    next_commands = payload.get("next_commands") or []
    if next_commands:
        lines.extend(["", "## Next"])
        lines.extend(f"- `{command}`" for command in next_commands)
    return "\n".join(lines) + "\n"


def corpus_to_markdown(payload: dict) -> str:
    kind = payload.get("kind")
    if kind == "recommended_corpus_profiles":
        lines = [
            "# recommended corpus profiles",
            "",
            f"- schema_version: {payload.get('schema_version')}",
            f"- as_of: {payload.get('as_of')}",
            f"- path: `{payload.get('path')}`",
            f"- profiles: {payload.get('profile_count', 0)}",
            "",
            "## Profiles",
        ]
        for profile in payload.get("profiles") or []:
            deps = ", ".join(profile.get("dependencies") or []) or "none"
            aliases = ", ".join(profile.get("aliases") or []) or "none"
            lines.append(
                f"- `{profile.get('name')}` ({profile.get('priority')}) — "
                f"{profile.get('entry_count', 0)} entries / "
                f"{profile.get('installable_count', 0)} installable / "
                f"deps: {deps} / aliases: {aliases}"
            )
        return "\n".join(lines).rstrip() + "\n"

    lines = [
        "# recommended corpus profile",
        "",
        f"- requested: {', '.join(payload.get('requested_profiles') or [])}",
        f"- included: {', '.join(payload.get('included_profiles') or [])}",
        f"- dependencies: {'yes' if payload.get('include_dependencies') else 'no'}",
        f"- entries: {payload.get('entry_count', 0)}",
        "",
        "## Entries",
    ]
    for entry in payload.get("entries") or []:
        installable = (
            entry.get("installable", True)
            and entry.get("source_status", "supported") == "supported"
        )
        status = "installable" if installable else entry.get("source_status", "unsupported")
        lines.append(
            f"- `{entry.get('profile')}` {entry.get('title')}（{entry.get('short_title') or ''}）"
            f" — {entry.get('priority')} / {entry.get('primary_source')} / {status}"
        )
        if entry.get("needs_verification"):
            lines.append("  needs_verification: true")
    return "\n".join(lines).rstrip() + "\n"


def sources_to_markdown(payload: dict) -> str:
    if payload.get("kind") == "source_coverage_source":
        source = payload.get("source") or {}
        commands = source.get("commands") or {}
        lines = [
            f"# source: {source.get('id')}",
            "",
            f"- name: {source.get('name')}",
            f"- class: {source.get('coverage_class')}",
            f"- authority_layer: {source.get('authority_layer')}",
            f"- adapter_status: {source.get('adapter_status')}",
            f"- maturity: {source.get('maturity')}",
            f"- public_v2: {source.get('public_v2')}",
            "",
            "## Commands",
        ]
        for name, status in commands.items():
            lines.append(f"- `{name}`: {status}")
        urls = source.get("urls") or []
        if urls:
            lines.extend(["", "## URLs"])
            lines.extend(f"- {url}" for url in urls)
        scope = source.get("content_scope") or []
        if scope:
            lines.extend(["", "## Scope"])
            lines.extend(f"- {item}" for item in scope)
        limitations = source.get("limitations") or []
        if limitations:
            lines.extend(["", "## Limitations"])
            lines.extend(f"- {item}" for item in limitations)
        return "\n".join(lines).rstrip() + "\n"

    lines = [
        "# source coverage",
        "",
        f"- schema_version: {payload.get('schema_version')}",
        f"- as_of: {payload.get('as_of')}",
        f"- sources: {payload.get('source_count', 0)}",
        "",
        "## Sources",
    ]
    for source in payload.get("sources") or []:
        commands = source.get("commands") or {}
        supported = [
            name
            for name in ("fetch", "discover", "sync", "verify_source")
            if commands.get(name) == "supported"
        ]
        supported_text = ", ".join(supported) if supported else "none"
        lines.append(
            f"- `{source.get('id')}` — {source.get('coverage_class')} / "
            f"{source.get('maturity')} / public_v2={source.get('public_v2')} / "
            f"{supported_text}"
        )
    return "\n".join(lines).rstrip() + "\n"


def schema_to_markdown(payload: dict) -> str:
    kind = payload.get("kind")
    if kind == "cli_schema_index":
        lines = [
            "# chinalaw schema",
            "",
            f"- schema_version: {payload.get('schema_version')}",
            f"- commands: {payload.get('command_count', 0)}",
            "",
            "## Global Flags",
        ]
        for flag in payload.get("global_flags") or []:
            lines.append(f"- `{flag.get('name')}` — {flag.get('description') or ''}")
        lines.extend(
            [
                "",
                "## Commands",
            ]
        )
        for command in payload.get("commands") or []:
            lines.append(
                f"- `{command.get('path')}` — {command.get('risk')} / {command.get('summary')}"
            )
        return "\n".join(lines).rstrip() + "\n"

    if kind == "mcp_schema":
        budget = (payload.get("context_budget") or {}).get("target_tools_list_chars")
        lines = [
            "# chinalaw MCP schema",
            "",
            f"- tools: {payload.get('tool_count', 0)}",
            f"- context budget: {budget} chars",
            "",
            "## Tools",
        ]
        for tool in payload.get("tools") or []:
            lines.append(
                f"- `{tool.get('name')}` — {tool.get('risk')} / {tool.get('cli_equivalent')}"
            )
        return "\n".join(lines).rstrip() + "\n"

    command = payload.get("command") or {}
    lines = [
        f"# schema: {command.get('path') or payload.get('target') or ''}",
        "",
        f"- summary: {command.get('summary')}",
        f"- risk: {command.get('risk')}",
        f"- side_effect: {command.get('side_effect')}",
        f"- network: {command.get('network')}",
        f"- authority_boundary: {command.get('authority_boundary')}",
        f"- output: {command.get('json_output', {}).get('kind')}",
        "",
        "## Arguments",
    ]
    positionals = command.get("positional") or []
    flags = command.get("flags") or []
    if positionals:
        for arg in positionals:
            required = "required" if arg.get("required") else "optional"
            lines.append(f"- `{arg.get('name')}` ({required}) — {arg.get('description') or ''}")
    if flags:
        for arg in flags:
            required = "required" if arg.get("required") else "optional"
            lines.append(f"- `{arg.get('name')}` ({required}) — {arg.get('description') or ''}")
    if not positionals and not flags:
        lines.append("- 无")
    exit_codes = command.get("exit_codes") or {}
    if exit_codes:
        lines.extend(["", "## Exit Codes"])
        for code, meaning in exit_codes.items():
            lines.append(f"- `{code}`: {meaning}")
    misuse = command.get("common_misuse") or []
    if misuse:
        lines.extend(["", "## Common Misuse"])
        for item in misuse:
            lines.append(f"- {item}")
    follow_ups = command.get("suggested_follow_ups") or []
    if follow_ups:
        lines.extend(["", "## Suggested Follow-ups"])
        for item in follow_ups:
            lines.append(f"- `{item}`")
    return "\n".join(lines).rstrip() + "\n"


def doctor_to_markdown(payload: dict) -> str:
    status = "通过" if payload.get("ok") else "失败"
    lines = [
        "# chinalaw doctor",
        "",
        f"- 状态：{status}",
        f"- strict：{payload.get('strict')}",
        f"- 数据库：`{payload.get('db_path')}`",
        f"- errors：{payload.get('error_count', 0)}",
        f"- warnings：{payload.get('warning_count', 0)}",
        "",
        "## Checks",
    ]
    for check in payload.get("checks") or []:
        lines.append(
            f"- [{str(check.get('status') or '').upper()}] "
            f"{check.get('name')}: {check.get('message')}"
        )
        if check.get("hint"):
            lines.append(f"  hint: {check['hint']}")
    return "\n".join(lines).rstrip() + "\n"


def snapshot_to_markdown(payload: dict) -> str:
    lines = [
        "# chinalaw snapshot",
        f"- 项目：{payload.get('project_path') or '无'}",
        f"- 快照：{payload.get('snapshot_path') or '无'}",
        f"- 已启用：{'是' if payload.get('exists') else '否'}",
        f"- 记录数：{payload.get('record_count', 0)}",
        f"- 写入模式：{payload.get('write_mode') or '无'}",
    ]
    if payload.get("first_timestamp"):
        lines.append(f"- 首条：{payload.get('first_timestamp')}")
    if payload.get("last_timestamp"):
        lines.append(f"- 末条：{payload.get('last_timestamp')}")
    commands = payload.get("commands") or {}
    if commands:
        lines.append("")
        lines.append("## Commands")
        for command, count in sorted(commands.items()):
            lines.append(f"- {command}: {count}")
    evidence_levels = payload.get("evidence_levels") or {}
    if evidence_levels:
        lines.append("")
        lines.append("## Evidence Levels")
        for level, count in sorted(evidence_levels.items()):
            lines.append(f"- {level}: {count}")
    return "\n".join(lines) + "\n"


_LEVEL_LABELS = {
    "law": "法律",
    "judicial_interpretation": "司法解释",
    "judicial_meeting_minutes": "司法会议纪要",
    "administrative_regulation": "行政法规",
    "departmental_rule": "部门规章",
    "local_regulation": "地方性法规",
    "constitution": "宪法",
}

_VIA_LABELS = {
    "id_match": "id 精确",
    "title_match": "全名精确",
    "short_title_match": "短称精确",
    "alias_exact": "alias 列表精确",
    "alias_derived": "规则派生 alias",
    "like_fallback": "模糊匹配（最后兜底）",
}


def resolve_to_markdown(payload: dict) -> str:
    if not payload.get("matched"):
        lines = [
            f"- 输入：{payload.get('input') or '—'}",
            "- 命中：未找到",
            "- 提示：试 `chinalaw fetch <俗称> --list-matches` 列候选",
        ]
        return "\n".join(lines) + "\n"

    level = payload.get("level") or "?"
    via = payload.get("via") or "?"
    issuer = payload.get("issuing_body") or "?"
    lines = [
        f"- 输入：{payload.get('input')}",
        f"- 正式：{payload.get('official_title')}",
        f"- 短称：{payload.get('short_title') or '—'}",
        f"- 效力层级：{_LEVEL_LABELS.get(level, level)}（{issuer}发布）",
        f"- 状态：{payload.get('status') or '?'}",
        f"- 命中路径：{via}（{_VIA_LABELS.get(via, '?')}）",
    ]
    return "\n".join(lines) + "\n"


def discover_to_markdown(payload: dict) -> str:
    lines = [
        f"# discover：{payload.get('source', '')}",
        f"- 查询：{payload.get('query') or '(空)'}",
        f"- 状态：{payload.get('status') or '(全部)'}",
        f"- 候选数：{len(payload.get('candidates') or [])}",
        "",
    ]
    for cand in payload.get("candidates") or []:
        lines.append(
            f"- {cand.get('title', '')} — id=`{cand.get('id') or cand.get('bbbs', '')}`"
            f" / released={cand.get('released_at', '')}"
            f" / status={cand.get('status', '')}"
        )
    return "\n".join(lines) + "\n"
