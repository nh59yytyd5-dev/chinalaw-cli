"""Shared runtime contracts for canonical public-law payloads.

The project intentionally avoids a runtime validation dependency.  This module
therefore provides the small, explicit checks that every ingest path needs
before a payload can reach SQLite.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from urllib.parse import urlsplit

from chinalaw.models import LawLevel, LawStatus
from chinalaw.service import normalize_article_number

LAW_LEVEL_VALUES = frozenset(item.value for item in LawLevel)
LAW_STATUS_VALUES = frozenset(item.value for item in LawStatus)

_LOCAL_SOURCE_SCHEMES = {
    "file",
    "local-file",
    "local-maintained-from-official-text",
    "mcp",
}
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_DATETIME_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}"
    r"(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})"
)
_SYMBOLIC_ARTICLE_NUMBERS = {"正文", "序言"}


def _required_text(payload: dict, field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(
            f"canonical law payload field {field!r} must be a non-empty string"
        )
    return value.strip()


def _optional_text(payload: dict, field: str) -> None:
    value = payload.get(field)
    if value is not None and not isinstance(value, str):
        raise ValueError(f"canonical law payload field {field!r} must be a string or null")


def validate_iso_date_value(
    value: object,
    field: str,
    *,
    allow_none: bool = True,
) -> None:
    """Validate one strict YYYY-MM-DD value for shared ingest contracts."""

    if value is None and allow_none:
        return
    if not isinstance(value, str) or _DATE_RE.fullmatch(value) is None:
        raise ValueError(f"{field} must be an ISO date or null")
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field} must use YYYY-MM-DD") from exc


def _iso_date(payload: dict, field: str) -> None:
    validate_iso_date_value(
        payload.get(field),
        f"canonical law payload field {field!r}",
    )


def validate_iso_datetime_value(value: object, field: str) -> None:
    """Validate one timezone-aware ISO datetime for shared ingest contracts."""

    if not isinstance(value, str) or _DATETIME_RE.fullmatch(value.strip()) is None:
        raise ValueError(f"{field} must be an ISO datetime")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO datetime") from exc
    if parsed.utcoffset() is None:
        raise ValueError(f"{field} must include a UTC offset")


def _iso_datetime(payload: dict, field: str) -> None:
    validate_iso_datetime_value(
        payload.get(field),
        f"canonical law payload field {field!r}",
    )


def validate_source_url_value(
    value: object,
    field: str,
    *,
    local_schemes: frozenset[str] | set[str] = _LOCAL_SOURCE_SCHEMES,
) -> None:
    """Validate an HTTP(S) or explicitly approved local source URL."""

    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    parsed = urlsplit(value)
    if not parsed.scheme:
        raise ValueError(f"{field} must include a scheme")
    if parsed.scheme in local_schemes:
        return
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(
            f"{field} must be http(s) or an approved local source"
        )


def _source_url(payload: dict) -> None:
    validate_source_url_value(
        payload.get("source_url"),
        "canonical law payload source_url",
    )


def _enum_text(payload: dict, field: str, allowed: frozenset[str]) -> str:
    value = _required_text(payload, field)
    if value not in allowed:
        raise ValueError(
            f"canonical law payload {field} must be one of {sorted(allowed)}: "
            f"{value!r}"
        )
    return value


def _validate_aliases(payload: dict) -> None:
    aliases = payload.get("aliases")
    if not isinstance(aliases, list) or any(
        not isinstance(item, str) for item in aliases
    ):
        raise ValueError("canonical law payload aliases must be an array of strings")


def _validate_article_shape(article: object, index: int) -> tuple[dict, str, int]:
    if not isinstance(article, dict):
        raise ValueError(f"canonical law article #{index} must be an object")
    number = article.get("number")
    if not isinstance(number, str) or not number.strip():
        raise ValueError(f"canonical law article #{index} requires a non-empty number")
    if number not in _SYMBOLIC_ARTICLE_NUMBERS and normalize_article_number(number) != number:
        raise ValueError(
            f"canonical law article #{index} number must be normalized: {number!r}"
        )
    text = article.get("text")
    if not isinstance(text, str) or not text.strip():
        raise ValueError(f"canonical law article {number!r} requires non-empty text")
    position = article.get("position")
    if not isinstance(position, int) or isinstance(position, bool) or position < 1:
        raise ValueError(
            f"canonical law article {number!r} requires a positive integer position"
        )
    return article, number, position


def _validate_article_optional_fields(article: dict, number: str) -> None:
    for field in ("number_display", "part", "title"):
        value = article.get(field)
        if value is not None and not isinstance(value, str):
            raise ValueError(
                f"canonical law article {number!r} field {field!r} "
                "must be a string or null"
            )
    article_id = article.get("id")
    if article_id is not None and (
        not isinstance(article_id, str) or not article_id.strip()
    ):
        raise ValueError(
            f"canonical law article {number!r} field 'id' "
            "must be a non-empty string or null"
        )
    number_display = article.get("number_display")
    if isinstance(number_display, str) and number not in _SYMBOLIC_ARTICLE_NUMBERS:
        display_number = normalize_article_number(number_display)
        if display_number and display_number != number:
            raise ValueError(
                f"canonical law article {number!r} number_display disagrees with number"
            )


def _validate_articles(payload: dict, *, require_articles: bool) -> None:
    articles = payload.get("articles")
    if not isinstance(articles, list):
        raise ValueError("canonical law payload articles must be an array")
    if require_articles and not articles:
        raise ValueError("canonical public-law payload must contain at least one article")

    seen_numbers: set[str] = set()
    seen_positions: set[int] = set()
    for index, raw_article in enumerate(articles, start=1):
        article, number, position = _validate_article_shape(raw_article, index)
        if number in seen_numbers:
            raise ValueError(
                f"canonical law payload contains duplicate article number {number!r}"
            )
        if position in seen_positions:
            raise ValueError(
                f"canonical law payload contains duplicate article position {position}"
            )
        seen_numbers.add(number)
        seen_positions.add(position)
        _validate_article_optional_fields(article, number)

    if seen_positions != set(range(1, len(articles) + 1)):
        raise ValueError("canonical law article positions must be contiguous 1..N")


def _validate_category(category: object, index: int) -> None:
    if not isinstance(category, dict):
        raise ValueError(f"canonical law category #{index} must be an object")
    for field in ("id", "name"):
        value = category.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(
                f"canonical law category #{index} field {field!r} "
                "must be a non-empty string"
            )
    for field in ("parent_id", "description"):
        value = category.get(field)
        if value is not None and not isinstance(value, str):
            raise ValueError(
                f"canonical law category #{index} field {field!r} "
                "must be a string or null"
            )


def _validate_categories(payload: dict) -> None:
    categories = payload.get("categories", [])
    if not isinstance(categories, list):
        raise ValueError("canonical law payload categories must be an array")
    for index, category in enumerate(categories, start=1):
        _validate_category(category, index)

    category_ids = payload.get("category_ids", [])
    if not isinstance(category_ids, list) or any(
        not isinstance(item, str) or not item.strip() for item in category_ids
    ):
        raise ValueError(
            "canonical law payload category_ids must be an array of non-empty strings"
        )


def validate_law_payload(payload: dict, *, require_articles: bool = False) -> dict:
    """Validate a normalized canonical law payload and return it unchanged."""

    if not isinstance(payload, dict):
        raise ValueError("canonical law payload must be an object")

    for field in ("id", "title", "source_name", "source_hash"):
        _required_text(payload, field)
    _source_url(payload)
    _iso_datetime(payload, "source_checked_at")

    _enum_text(payload, "level", LAW_LEVEL_VALUES)
    _enum_text(payload, "status", LAW_STATUS_VALUES)

    for field in (
        "short_title",
        "issuing_body",
        "document_number",
        "version_label",
        "revision_id",
        "revision_notes",
    ):
        _optional_text(payload, field)
    for field in ("released_at", "effective_at", "repealed_at", "revision_released_at"):
        _iso_date(payload, field)

    _validate_aliases(payload)
    _validate_articles(payload, require_articles=require_articles)
    _validate_categories(payload)
    return payload
