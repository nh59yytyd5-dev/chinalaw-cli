"""Bounded, literal excerpts over already ranked and authorized search results."""

from __future__ import annotations

import hashlib
import re
from typing import Literal

from chinalaw.service import normalize_article_number

SearchView = Literal["full", "brief"]
EXCERPT_CHARS = 240


def _excerpt(text: str, query: str, hit: dict) -> dict:
    terms = [query, *re.split(r"\s+", query.strip()), *(hit.get("fuzzy", {}).get("matched") or [])]
    matches = (
        re.search(re.escape(term), text, re.IGNORECASE)
        for term in sorted(set(terms), key=lambda value: (-len(value), value)) if term
    )
    match = next((match for match in matches if match is not None), None)
    start = max(0, match.start() - EXCERPT_CHARS // 4) if match else 0
    start = min(start, max(0, len(text) - EXCERPT_CHARS))
    end = min(len(text), start + EXCERPT_CHARS)
    return {
        "text": text[start:end], "start_char": start, "end_char": end,
        "total_chars": len(text), "truncated": start != 0 or end != len(text),
    }


def _brief_hit(hit: dict, group: str, index: int, query: str, full: dict) -> dict:
    text = hit.get("text")
    if not isinstance(text, str):
        return hit
    result = {key: value for key, value in hit.items() if key != "text"}
    result["excerpt"] = _excerpt(text, query, hit)
    version = {"basis": "stored_record", "sha256": hashlib.sha256(text.encode()).hexdigest()}
    if group == "article_hits" and normalize_article_number(hit.get("number", "")):
        args = {"law": hit["law_id"], "number": hit["number"], "detail": "compact"}
        # Normal keyword search reads stored article rows. Its as_of ranks those
        # rows; forwarding it to article would select a different work revision.
        # Explicit citation hits, in contrast, already came from get_article_as_of.
        as_of = full["arguments"].get("as_of")
        if hit.get("match_mode") == "citation" and as_of:
            args["as_of"] = as_of
            version.update(basis="as_of", as_of=as_of)
        result["read"] = {"tool": "chinalaw_article", "arguments": args}
    else:
        # Norm IDs can collide with public IDs; arbitrary source numbering may
        # not be accepted by article. Recover via the same scoped search instead
        # of issuing an ambiguous public-first lookup or inventing an offset.
        result["read"] = {**full, "result_path": [group, index]}
        if group == "norm_clause_hits":
            version["basis"] = "private_record"
    result["text_version"] = version
    return result


def search_view(payload: dict, *, arguments: dict, view: SearchView = "full") -> dict:
    if view not in {"full", "brief"}:
        raise ValueError("view must be full or brief")
    if view == "full":
        return payload
    full = {"tool": "chinalaw_search", "arguments": {**arguments, "view": "full"}}
    result = dict(payload)
    for group in ("article_hits", "norm_clause_hits"):
        if group in payload:
            result[group] = [
                _brief_hit(hit, group, index, payload.get("query", ""), full)
                for index, hit in enumerate(payload[group])
            ]
    result["view"] = {"mode": "brief", "excerpt_max_chars": EXCERPT_CHARS, "full": full}
    return result
