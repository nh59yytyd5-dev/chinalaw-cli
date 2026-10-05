"""Literal recovery suggestions; never change or broaden the executed query."""

from __future__ import annotations

import re

from chinalaw.document_numbers import DOCUMENT_NUMBER_INLINE_RE


def empty_search_guidance(query: str, law_filter: dict | None, arguments: dict) -> dict:
    """Explain zero hits and suggest at most three explicit follow-up operations."""
    unresolved = (law_filter or {}).get("unresolved", [])
    if unresolved:
        return {
            "code": "unresolved_law_scope",
            "message": "限定的法规名未全部解析，请先核对 law_filter 中的候选；未改搜全库。",
            "next_steps": [
                {"tool": "resolve", "arguments": {"name": name}} for name in unresolved[:3]
            ],
        }

    suggestions = []
    number = DOCUMENT_NUMBER_INLINE_RE.search(query)
    if number:
        title = (query[:number.start()] + " " + query[number.end():]).strip(" 《》；;，,")
        if len(title) >= 2:
            suggestions.append(title)
    # Use only literal, user-supplied whitespace-separated terms. These are
    # suggestions, not evidence of coverage, semantic synonyms, or automatic OR.
    terms = list(dict.fromkeys(re.split(r"\s+", query)))
    if len(terms) > 1:
        suggestions.extend(term for term in terms if len(term) >= 2 and term != query)
    suggestions = list(dict.fromkeys(suggestions))[:3]
    message = (
        "本次条件下未命中，不等于不存在相关规定。多个检索词需要在同一条文或其法规标题中"
        "同时匹配；请减少检索词、改用法条原文的说法，或用正式名称定位后按条读取。"
    )
    if number:
        message += "标题与文号可分开检索，再核对返回元数据中的文号。"
    if any(arguments.get(key) for key in ("level", "status", "region", "in_laws", "in_part")):
        message += "以下建议保留原筛选条件；如需放宽，请明确修改相应条件。"
    return {
        "code": "no_match_under_filters",
        "message": message,
        "next_steps": [
            {"tool": "search", "arguments": {**arguments, "query": text}}
            for text in suggestions
        ],
    }
