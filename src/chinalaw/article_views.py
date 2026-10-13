"""Lossless article presentation shared by the HTTP and stdio MCP adapters.

Selection, authorization and diagnosis belong to the existing query path. Only
explicitly redundant or separately retrievable fields are omitted by compact.
"""

from __future__ import annotations

from typing import Literal

ArticleDetail = Literal["full", "compact"]


def article_view(
    payload: dict, *, law: str, number: str, as_of: str | None = None,
    detail: ArticleDetail = "full",
) -> dict:
    if detail not in {"full", "compact"}:
        raise ValueError("detail must be full or compact")
    if detail == "full":
        return payload

    result = dict(payload)
    omitted = []
    if isinstance(payload.get("article"), dict) and payload.get("item") == payload["article"]:
        result.pop("item")
        omitted.append("item")
    if isinstance(payload.get("law"), dict):
        result["law"] = dict(payload["law"])
        for field in ("revisions", "work_versions"):
            if field in result["law"]:
                result["law"].pop(field)
                omitted.append("law." + field)

    arguments = {"law": law, "number": number, "detail": "full"}
    if as_of is not None:
        arguments["as_of"] = as_of
    result["view"] = {
        "detail": "compact",
        "omitted_fields": omitted,
        "full": {"tool": "chinalaw_article", "arguments": arguments},
    }
    return result
