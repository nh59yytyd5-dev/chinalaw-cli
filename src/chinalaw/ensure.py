"""Local-first law availability workflow.

``ensure`` is the operational wrapper for day-to-day use:
check the local SQLite cache first, fetch only missing/stub public laws, and
return a compact report that is safe for agents to consume.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Iterable
from pathlib import Path

from chinalaw import cleaning, corpus, loader, service
from chinalaw import fetch as fetch_mod

_KNOWN_EXTENSIONS = {".pdf", ".doc", ".docx", ".txt", ".md", ".json"}
_YEAR_SUFFIX_RE = re.compile(r"\s*[（(](?:19|20)\d{2}年?[）)]\s*$")
_WHITESPACE_RE = re.compile(r"\s+")
_SOURCE_RATE_LIMIT_MARKERS = (
    "anti-bot",
    "challenge",
    "rate limit",
    "rate_limit",
    "too many requests",
    "status=429",
    "http 429",
)
_SOURCE_RATE_LIMIT_RETRY_HINT = (
    "停止本轮同一来源批量补库；稍后重试，或改用单部 ensure/fetch 按需补全。"
)


def normalize_law_name(raw: str | Path) -> str:
    """Normalize a user supplied law name or filename into a fetch query.

    The function is intentionally conservative: it strips common file
    extensions and a trailing year marker such as ``（2024年）`` but keeps
    semantic suffixes like ``解释（一）``.
    """

    text = str(raw).strip()
    if not text:
        return ""

    maybe_path = Path(text)
    if maybe_path.suffix.lower() in _KNOWN_EXTENSIONS:
        text = maybe_path.stem

    text = _WHITESPACE_RE.sub(" ", text).strip()
    text = _YEAR_SUFFIX_RE.sub("", text).strip()
    return text


def collect_names(
    *,
    names: Iterable[str] = (),
    from_file: str | Path | None = None,
    from_dir: str | Path | None = None,
) -> list[str]:
    """Collect law names from argv, a line-based file, and/or directory filenames.

    ``from_dir`` never reads file bodies; it only derives law names from direct
    child filenames. This preserves the public-law vs private-material boundary.
    """

    collected: list[str] = []

    for name in names:
        _append_name(collected, name)

    if from_file is not None:
        path = Path(from_file)
        if not path.is_file():
            raise ValueError(f"from_file is not a file: {path}")
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            _append_name(collected, stripped)

    if from_dir is not None:
        directory = Path(from_dir)
        if not directory.is_dir():
            raise ValueError(f"from_dir is not a directory: {directory}")
        for child in sorted(directory.iterdir(), key=lambda p: p.name):
            if not child.is_file() or child.name.startswith("."):
                continue
            _append_name(collected, child.name)

    return collected


def ensure_laws(
    db_path: str | Path,
    names: Iterable[str],
    *,
    source: str = "flk_npc",
    limit: int = 5,
    interval: float = 1.0,
) -> dict:
    """Ensure public laws are populated in the local DB.

    A populated local law is never fetched. Missing laws, stub laws, and seed
    sample laws are fetched through ``fetch_law`` and validated to contain at
    least one article.
    """

    requested = [normalize_law_name(name) for name in names]
    requested = [name for name in requested if name]
    if not requested:
        raise ValueError("ensure requires at least one law name")
    if interval < 0:
        raise ValueError("interval must be >= 0")

    unique_names, duplicate_count = _dedupe(requested)
    items: list[dict] = []
    present_count = 0
    fetched_count = 0
    skipped_count = 0
    failed_count = 0
    fetch_attempt_count = 0

    for name in unique_names:
        existing = service.get_law(db_path, name)
        if _is_populated(existing):
            present_count += 1
            items.append(
                {
                    "name": name,
                    "status": "present",
                    "reason": "already_populated",
                    "law": _summarize_law(existing),
                }
            )
            continue

        reason = (
            "seed_law"
            if existing and existing.get("status") == "seed"
            else "stub_law" if existing else "missing_law"
        )
        if fetch_attempt_count and interval > 0:
            time.sleep(interval)
        fetch_attempt_count += 1

        try:
            fetched = fetch_mod.fetch_law(
                db_path,
                name,
                source=source,
                limit=limit,
            )
        except fetch_mod.FetchError as exc:
            failed_count += 1
            item = {
                "name": name,
                "status": "failed",
                "reason": reason,
                "error": exc.__class__.__name__,
                "message": str(exc),
            }
            candidates = getattr(exc, "candidates", None)
            if candidates:
                item["candidates"] = candidates
            if existing:
                item["law"] = _summarize_law(existing)
            items.append(item)
            continue

        article_count = int(fetched.get("article_count") or 0)
        if article_count <= 0:
            failed_count += 1
            items.append(
                {
                    "name": name,
                    "status": "failed",
                    "reason": "empty_articles",
                    "message": "fetch returned a law payload with zero articles",
                    "fetch": _summarize_fetch(fetched),
                }
            )
            continue

        if fetched.get("loaded"):
            fetched_count += 1
            status = "fetched"
        elif fetched.get("skipped"):
            skipped_count += 1
            status = "skipped"
        else:
            # Defensive fallback for future fetch actions that still resolve data.
            skipped_count += 1
            status = "resolved"

        items.append(
            {
                "name": name,
                "status": status,
                "reason": reason,
                "fetch": _summarize_fetch(fetched),
                "law": _summarize_law(fetched.get("law") or {}),
            }
        )

    return {
        "kind": "law_ensure",
        "ok": failed_count == 0,
        "source": source,
        "db_path": str(db_path),
        "requested_count": len(requested),
        "unique_count": len(unique_names),
        "skipped_duplicate_count": duplicate_count,
        "present_count": present_count,
        "fetched_count": fetched_count,
        "skipped_count": skipped_count,
        "failed_count": failed_count,
        "fetch_attempt_count": fetch_attempt_count,
        "items": items,
    }


def ensure_corpus_profiles(
    db_path: str | Path,
    profiles: Iterable[str],
    *,
    include_dependencies: bool = True,
    limit: int = 5,
    interval: float = 1.0,
) -> dict:
    """Ensure all installable entries in recommended corpus profiles.

    Unlike ``ensure_laws()``, each corpus entry carries its own source. This is
    the public path for "install the criminal/company/labor profile" workflows.
    """

    if interval < 0:
        raise ValueError("interval must be >= 0")

    resolved = corpus.resolve_profiles(
        list(profiles),
        include_dependencies=include_dependencies,
    )
    entries = resolved.get("entries") or []
    items: list[dict] = []
    present_count = 0
    fixture_loaded_count = 0
    fetched_count = 0
    skipped_count = 0
    failed_count = 0
    rate_limited_count = 0
    fetch_attempt_count = 0
    source_rate_limited: dict[str, str] = {}

    for entry in entries:
        title = (entry.get("title") or "").strip()
        if not title:
            skipped_count += 1
            items.append(
                {
                    "name": entry.get("id") or "(untitled)",
                    "status": "skipped",
                    "reason": "missing_title",
                    "profile": entry.get("profile"),
                }
            )
            continue

        source = (entry.get("primary_source") or "").strip()
        item_base = {
            "name": title,
            "profile": entry.get("profile"),
            "corpus_id": entry.get("id"),
            "priority": entry.get("priority"),
            "source": source,
        }
        fetch_status = (entry.get("fetch_status") or "").strip() or None
        if fetch_status:
            item_base["fetch_status"] = fetch_status

        source_status = entry.get("source_status", "supported")
        if not entry.get("installable", True):
            skip_reason = entry.get("skip_reason") or (
                "manual_review" if source_status == "manual_review" else "not_installable"
            )
            skipped_count += 1
            items.append(
                {
                    **item_base,
                    "status": "skipped",
                    "reason": skip_reason,
                    "message": entry.get("notes"),
                }
            )
            continue

        if source_status != "supported" or source not in fetch_mod.FETCH_SOURCES:
            skip_reason = (
                "manual_review"
                if source_status == "manual_review" and source in fetch_mod.FETCH_SOURCES
                else "unsupported_source"
            )
            skipped_count += 1
            items.append(
                {
                    **item_base,
                    "status": "skipped",
                    "reason": skip_reason,
                    "message": entry.get("notes"),
                }
            )
            continue

        existing = _resolve_existing_law(db_path, entry)
        if _is_populated(existing):
            present_count += 1
            items.append(
                {
                    **item_base,
                    "status": "present",
                    "reason": "already_populated",
                    "law": _summarize_law(existing),
                }
            )
            continue

        fixture_result = _load_builtin_fixture_if_available(db_path, entry)
        if _is_populated(fixture_result):
            fixture_loaded_count += 1
            items.append(
                {
                    **item_base,
                    "status": "loaded_fixture",
                    "reason": "builtin_fixture",
                    "law": _summarize_law(fixture_result),
                }
            )
            continue

        reason = (
            "seed_law"
            if existing and existing.get("status") == "seed"
            else "stub_law" if existing else "missing_law"
        )
        if source in source_rate_limited:
            skipped_count += 1
            rate_limited_count += 1
            items.append(
                {
                    **item_base,
                    "status": "skipped",
                    "reason": "source_rate_limited",
                    "message": source_rate_limited[source],
                    "retry_hint": _SOURCE_RATE_LIMIT_RETRY_HINT,
                }
            )
            continue

        if fetch_attempt_count and interval > 0:
            time.sleep(interval)
        fetch_attempt_count += 1

        try:
            fetched = fetch_mod.fetch_law(
                db_path,
                entry.get("query") or title,
                source=source,
                prefer_bbbs=entry.get("prefer_id") or entry.get("prefer_bbbs"),
                limit=limit,
                status=fetch_status,
            )
        except fetch_mod.FetchError as exc:
            failed_count += 1
            item = {
                **item_base,
                "status": "failed",
                "reason": reason,
                "error": exc.__class__.__name__,
                "message": str(exc),
            }
            candidates = getattr(exc, "candidates", None)
            if candidates:
                item["candidates"] = candidates
            if existing:
                item["law"] = _summarize_law(existing)
            if _is_source_rate_limited_error(exc):
                source_rate_limited[source] = str(exc)
                item["reason"] = "source_rate_limited"
                item["retry_hint"] = _SOURCE_RATE_LIMIT_RETRY_HINT
            items.append(item)
            continue

        article_count = int(fetched.get("article_count") or 0)
        if article_count <= 0:
            failed_count += 1
            items.append(
                {
                    **item_base,
                    "status": "failed",
                    "reason": "empty_articles",
                    "message": "fetch returned a law payload with zero articles",
                    "fetch": _summarize_fetch(fetched),
                }
            )
            continue

        if fetched.get("loaded"):
            fetched_count += 1
            status = "fetched"
        elif fetched.get("skipped"):
            skipped_count += 1
            status = "skipped"
        else:
            skipped_count += 1
            status = "resolved"

        items.append(
            {
                **item_base,
                "status": status,
                "reason": reason,
                "fetch": _summarize_fetch(fetched),
                "law": _summarize_law(fetched.get("law") or {}),
            }
        )

    return {
        "kind": "law_ensure_corpus",
        "ok": failed_count == 0 and rate_limited_count == 0,
        "source": "mixed",
        "db_path": str(db_path),
        "profile_names": resolved.get("requested_profiles") or [],
        "included_profiles": resolved.get("included_profiles") or [],
        "include_dependencies": include_dependencies,
        "requested_count": len(entries),
        "unique_count": len(entries),
        "skipped_duplicate_count": 0,
        "present_count": present_count,
        "fixture_loaded_count": fixture_loaded_count,
        "fetched_count": fetched_count,
        "skipped_count": skipped_count,
        "failed_count": failed_count,
        "rate_limited_count": rate_limited_count,
        "blocked_sources": sorted(source_rate_limited),
        "fetch_attempt_count": fetch_attempt_count,
        "items": items,
    }


def _append_name(target: list[str], raw: str | Path) -> None:
    normalized = normalize_law_name(raw)
    if normalized:
        target.append(normalized)


def _is_source_rate_limited_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return any(marker in message for marker in _SOURCE_RATE_LIMIT_MARKERS)


def _resolve_existing_law(db_path: str | Path, entry: dict) -> dict | None:
    identifiers = [
        entry.get("fixture_id"),
        entry.get("id"),
        entry.get("title"),
        entry.get("short_title"),
    ]
    for alias in entry.get("aliases") or []:
        identifiers.append(alias)
    for ident in identifiers:
        if not ident:
            continue
        law = service.get_law(db_path, str(ident))
        if law:
            return law
    return None


def _load_builtin_fixture_if_available(db_path: str | Path, entry: dict) -> dict | None:
    fixture_paths = _find_builtin_fixtures(entry)
    if not fixture_paths:
        return None
    loader.load_files(db_path, fixture_paths)
    return _resolve_existing_law(db_path, entry)


def _find_builtin_fixtures(entry: dict) -> list[Path]:
    fixture_id = str(entry.get("fixture_id") or "").strip()
    wanted_names = {
        str(value).strip()
        for value in (
            entry.get("title"),
            entry.get("short_title"),
            *(entry.get("aliases") or []),
        )
        if str(value).strip()
    }
    if not fixture_id and not wanted_names:
        return []

    candidates: list[tuple[Path, dict]] = []
    for path in sorted(loader.FIXTURES_DIR.glob("*.json")):
        try:
            payload = cleaning.canonicalize(
                json.loads(path.read_text(encoding="utf-8")),
                source_kind="external_json",
            )
        except FileNotFoundError:
            continue
        candidates.append((path, payload))

    if fixture_id:
        matches = [
            (path, payload)
            for path, payload in candidates
            if str(payload.get("id") or "").strip() == fixture_id
        ]
        return [
            path
            for path, _payload in sorted(matches, key=lambda item: _fixture_load_order(item[1]))
        ]

    matches = []
    for path, payload in candidates:
        if wanted_names & _fixture_identifiers(payload):
            matches.append((path, payload))
    if not matches:
        return []
    path, _payload = max(matches, key=lambda item: _fixture_load_order(item[1]))
    return [path]


def _fixture_identifiers(payload: dict) -> set[str]:
    return {
        str(value).strip()
        for value in (
            payload.get("title"),
            payload.get("short_title"),
            *(payload.get("aliases") or []),
        )
        if str(value).strip()
    }


def _fixture_load_order(payload: dict) -> tuple[str, int]:
    """Order same-id fixture snapshots from older to newer.

    Some built-in fixtures intentionally use the same stable law id for
    multiple historical snapshots. Loading them oldest-first keeps the final
    ``laws`` row at the latest/current version while preserving all revisions.
    """

    status_rank = {
        "repealed": 0,
        "amended": 1,
        "pending_effective": 2,
        "current": 3,
    }.get(str(payload.get("status") or ""), 1)
    return (str(payload.get("effective_at") or payload.get("released_at") or ""), status_rank)


def _dedupe(names: list[str]) -> tuple[list[str], int]:
    seen: set[str] = set()
    unique: list[str] = []
    duplicates = 0
    for name in names:
        if name in seen:
            duplicates += 1
            continue
        seen.add(name)
        unique.append(name)
    return unique, duplicates


def _is_populated(law: dict | None) -> bool:
    if not law:
        return False
    if law.get("status") == "seed" or law.get("articles_coverage") in {"seed", "stub"}:
        return False
    try:
        return int(law.get("article_count") or 0) > 0
    except (TypeError, ValueError):
        return False


def _summarize_law(law: dict) -> dict:
    return {
        "id": law.get("id"),
        "title": law.get("title"),
        "short_title": law.get("short_title"),
        "status": law.get("status"),
        "source_name": law.get("source_name"),
        "source_url": law.get("source_url"),
        "source_checked_at": law.get("source_checked_at"),
        "article_count": law.get("article_count"),
        "articles_coverage": law.get("articles_coverage"),
    }


def _summarize_fetch(result: dict) -> dict:
    return {
        "matched_id": result.get("matched_id") or result.get("matched_bbbs"),
        "matched_bbbs": result.get("matched_bbbs"),
        "matched_title": result.get("matched_title"),
        "article_count": result.get("article_count"),
        "loaded": bool(result.get("loaded")),
        "skipped": bool(result.get("skipped")),
    }
