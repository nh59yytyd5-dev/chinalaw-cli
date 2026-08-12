"""真实数据源同步编排。"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from chinalaw.db import connect, get_meta, migrate, set_meta
from chinalaw.loader import load_law_from_dict, refresh_law_metadata
from chinalaw.sources import get_source_adapter

SYNC_SOURCES: tuple[str, ...] = ("flk_npc",)


def sync_source(
    db_path: Path | str,
    *,
    source: str,
    query: str | None = None,
    bbbs: str | None = None,
    limit: int = 5,
    batch: bool = False,
    start_page: int = 1,
    max_pages: int | None = None,
    page_size: int = 20,
    resume: bool = False,
    stop_after_stable_pages: int | None = None,
    incremental: bool = False,
    published_from: str | None = None,
    published_to: str | None = None,
    days_back: int = 30,
    overlap_days: int = 1,
) -> dict:
    normalized = source.strip().lower().replace("-", "_")
    if normalized not in SYNC_SOURCES:
        raise ValueError(f"unsupported source for sync: {source}")
    if limit < 1:
        raise ValueError("limit must be >= 1")

    adapter = get_source_adapter(normalized)
    _initialize_database(db_path)

    if incremental:
        return _sync_incremental(
            db_path,
            source=normalized,
            adapter=adapter,
            start_page=start_page,
            max_pages=max_pages,
            page_size=page_size,
            published_from=published_from,
            published_to=published_to,
            days_back=days_back,
            overlap_days=overlap_days,
            stop_after_stable_pages=stop_after_stable_pages,
        )

    if batch:
        return _sync_batch(
            db_path,
            source=normalized,
            adapter=adapter,
            start_page=start_page,
            max_pages=max_pages,
            page_size=page_size,
            resume=resume,
            stop_after_stable_pages=stop_after_stable_pages,
        )

    rows: list[dict] = []
    if bbbs:
        ids = [bbbs]
        mode = "bbbs"
        titles: list[str] = []
    else:
        search_query = (query or "").strip()
        if not search_query:
            raise ValueError("query is required when bbbs is not provided")
        search_result = adapter.search_list(search_query, page_size=limit)
        rows = (search_result.get("rows") or [])[:limit]
        ids = [row["bbbs"] for row in rows if row.get("bbbs")]
        titles = [row.get("title", "") for row in rows]
        mode = "query"

    rows_by_id = {row["bbbs"]: row for row in rows if row.get("bbbs")}
    total_laws = 0
    total_articles = 0
    laws_skipped = 0
    metadata_refreshed = 0
    loaded_titles: list[str] = []

    for law_id in ids:
        # Network and document parsing happen before the write transaction.
        payload = adapter.build_law_payload(law_id, search_row=rows_by_id.get(law_id))
        with connect(db_path) as conn:
            migrate(conn)
            changed, article_count, metadata_changed = _load_if_changed(conn, payload)
        if changed:
            total_articles += article_count
            total_laws += 1
            loaded_titles.append(payload["title"])
        else:
            laws_skipped += 1
            metadata_refreshed += int(metadata_changed)

    checked_at = datetime.now(timezone.utc).isoformat()
    with connect(db_path) as conn:
        migrate(conn)
        _set_common_sync_meta(conn, normalized, mode, checked_at)
        if query:
            set_meta(conn, f"source:{normalized}:last_query", query)
        if bbbs:
            set_meta(conn, f"source:{normalized}:last_bbbs", bbbs)

    return {
        "source": normalized,
        "mode": mode,
        "query": query,
        "bbbs": bbbs,
        "candidate_titles": titles,
        "laws_loaded": total_laws,
        "laws_skipped": laws_skipped,
        "metadata_refreshed": metadata_refreshed,
        "articles_loaded": total_articles,
        "titles": loaded_titles,
    }


def _initialize_database(db_path: Path | str) -> None:
    with connect(db_path) as conn:
        migrate(conn)


def _sync_incremental(
    db_path: Path | str,
    *,
    source: str,
    adapter,
    start_page: int,
    max_pages: int | None,
    page_size: int,
    published_from: str | None,
    published_to: str | None,
    days_back: int,
    overlap_days: int,
    stop_after_stable_pages: int | None,
) -> dict:
    if days_back < 0:
        raise ValueError("days_back must be >= 0")
    if overlap_days < 0:
        raise ValueError("overlap_days must be >= 0")

    explicit_window = published_from is not None or published_to is not None
    prefix = _checkpoint_prefix(source, "incremental")
    with connect(db_path) as conn:
        migrate(conn)
        active_from = get_meta(conn, f"{prefix}:window_from")
        active_to = get_meta(conn, f"{prefix}:window_to")
        active_exhausted = get_meta(conn, f"{prefix}:window_exhausted")
        resume_window = bool(
            not explicit_window
            and active_from
            and active_to
            and active_exhausted == "false"
        )
        if resume_window:
            start_date = date.fromisoformat(active_from)
            end_date = date.fromisoformat(active_to)
        else:
            end_date = _resolve_incremental_end(published_to)
            start_date = _resolve_incremental_start(
                conn,
                source=source,
                explicit_from=published_from,
                end_date=end_date,
                days_back=days_back,
                overlap_days=overlap_days,
            )
            set_meta(conn, f"{prefix}:next_page", str(start_page))
            set_meta(conn, f"{prefix}:stable_pages_seen", "0")
        set_meta(conn, f"{prefix}:window_from", start_date.isoformat())
        set_meta(conn, f"{prefix}:window_to", end_date.isoformat())
        set_meta(conn, f"{prefix}:window_exhausted", "false")

    result = _sync_batch(
        db_path,
        source=source,
        adapter=adapter,
        start_page=start_page,
        max_pages=max_pages,
        page_size=page_size,
        resume=resume_window,
        stop_after_stable_pages=stop_after_stable_pages,
        search_query="",
        search_since=start_date.isoformat(),
        search_until=end_date.isoformat(),
        mode="incremental",
        extra_meta={
            f"{prefix}:window_from": start_date.isoformat(),
            f"{prefix}:window_to": end_date.isoformat(),
            f"{prefix}:window_exhausted": "false",
        },
    )

    exhausted = result["stop_reason"] == "no_rows"
    completed_at = datetime.now(timezone.utc).isoformat()
    with connect(db_path) as conn:
        migrate(conn)
        set_meta(conn, f"{prefix}:window_exhausted", "true" if exhausted else "false")
        if exhausted:
            set_meta(conn, f"source:{source}:last_incremental_from", start_date.isoformat())
            set_meta(conn, f"source:{source}:last_incremental_to", end_date.isoformat())
            set_meta(conn, f"{prefix}:completed_at", completed_at)
            set_meta(conn, f"{prefix}:next_page", "1")

    result["window_exhausted"] = exhausted
    result["resume_token"] = None if exhausted else {
        "source": source,
        "mode": "incremental",
        "published_from": start_date.isoformat(),
        "published_to": end_date.isoformat(),
        "next_page": result["next_page"],
    }
    return result


def _sync_batch(
    db_path: Path | str,
    *,
    source: str,
    adapter,
    start_page: int,
    max_pages: int | None,
    page_size: int,
    resume: bool,
    stop_after_stable_pages: int | None,
    search_query: str = "",
    search_since: str | None = None,
    search_until: str | None = None,
    mode: str = "batch",
    extra_meta: dict[str, str] | None = None,
) -> dict:
    if start_page < 1:
        raise ValueError("start_page must be >= 1")
    if page_size < 1:
        raise ValueError("page_size must be >= 1")
    if max_pages is not None and max_pages < 1:
        raise ValueError("max_pages must be >= 1 when provided")
    if stop_after_stable_pages is not None and stop_after_stable_pages < 1:
        raise ValueError("stop_after_stable_pages must be >= 1 when provided")

    prefix = _checkpoint_prefix(source, mode)
    with connect(db_path) as conn:
        migrate(conn)
        current_page = (
            _resolve_resume_page(conn, source, mode=mode, fallback=start_page)
            if resume
            else start_page
        )
        stable_pages_seen = (
            _meta_int(conn, f"{prefix}:stable_pages_seen", default=0)
            if resume
            else 0
        )
    resolved_start_page = current_page

    total_laws = 0
    total_articles = 0
    laws_skipped = 0
    metadata_refreshed = 0
    loaded_titles: list[str] = []
    pages_synced = 0
    rows_seen = 0
    total_available: int | None = None
    stop_reason = "exhausted"

    while True:
        if max_pages is not None and pages_synced >= max_pages:
            stop_reason = "max_pages"
            break

        # Fetch the complete page outside any SQLite write transaction.
        if search_since is not None or search_until is not None:
            search_result = adapter.list_laws(
                since=search_since,
                until=search_until,
                page_num=current_page,
                page_size=page_size,
            )
        else:
            search_result = adapter.search_list(
                search_query,
                page_num=current_page,
                page_size=page_size,
            )
        if total_available is None:
            total_available = search_result.get("total")

        rows = search_result.get("rows") or []
        if not rows:
            stop_reason = "no_rows"
            break

        prepared: list[tuple[dict, dict]] = []
        for row in rows:
            law_id = row.get("bbbs")
            if not law_id:
                continue
            payload = adapter.build_law_payload(law_id, search_row=row)
            prepared.append((row, payload))

        page_laws = 0
        page_articles = 0
        page_skipped = 0
        page_metadata = 0
        page_titles: list[str] = []
        page_changed = False
        checked_at = datetime.now(timezone.utc).isoformat()

        # Laws, stable counter and checkpoint commit atomically for this page.
        with connect(db_path) as conn:
            migrate(conn)
            for _row, payload in prepared:
                changed, article_count, metadata_changed = _load_if_changed(conn, payload)
                if changed:
                    page_articles += article_count
                    page_laws += 1
                    page_titles.append(payload["title"])
                    page_changed = True
                else:
                    page_skipped += 1
                    page_metadata += int(metadata_changed)

            if page_changed:
                stable_pages_seen = 0
                set_meta(conn, f"{prefix}:last_changed_page", str(current_page))
            else:
                stable_pages_seen += 1

            next_page = current_page + 1
            _set_common_sync_meta(conn, source, mode, checked_at)
            _set_page_checkpoint(
                conn,
                source=source,
                mode=mode,
                current_page=current_page,
                next_page=next_page,
                page_size=page_size,
                total_available=total_available,
                stable_pages_seen=stable_pages_seen,
            )
            if extra_meta:
                for key, value in extra_meta.items():
                    set_meta(conn, key, value)

        pages_synced += 1
        rows_seen += len(prepared)
        total_laws += page_laws
        total_articles += page_articles
        laws_skipped += page_skipped
        metadata_refreshed += page_metadata
        loaded_titles.extend(page_titles)
        current_page += 1

        if (
            not page_changed
            and stop_after_stable_pages is not None
            and stable_pages_seen >= stop_after_stable_pages
        ):
            stop_reason = "stable_pages"
            break

    return {
        "source": source,
        "mode": mode,
        "start_page": resolved_start_page,
        "resume": resume,
        "next_page": current_page,
        "max_pages": max_pages,
        "page_size": page_size,
        "pages_synced": pages_synced,
        "rows_seen": rows_seen,
        "laws_loaded": total_laws,
        "laws_skipped": laws_skipped,
        "metadata_refreshed": metadata_refreshed,
        "articles_loaded": total_articles,
        "total_available": total_available,
        "stable_pages_seen": stable_pages_seen,
        "stop_after_stable_pages": stop_after_stable_pages,
        "stop_reason": stop_reason,
        "search_query": search_query,
        "published_from": search_since,
        "published_to": search_until,
        "titles": loaded_titles,
    }


def _set_common_sync_meta(conn, source: str, mode: str, checked_at: str) -> None:
    set_meta(conn, "last_sync_at", checked_at)
    set_meta(conn, f"source:{source}:last_sync_at", checked_at)
    set_meta(conn, f"source:{source}:last_mode", mode)


def _set_page_checkpoint(
    conn,
    *,
    source: str,
    mode: str,
    current_page: int,
    next_page: int,
    page_size: int,
    total_available: int | None,
    stable_pages_seen: int,
) -> None:
    prefix = _checkpoint_prefix(source, mode)
    set_meta(conn, f"{prefix}:last_page", str(current_page))
    set_meta(conn, f"{prefix}:next_page", str(next_page))
    set_meta(conn, f"{prefix}:last_page_size", str(page_size))
    set_meta(conn, f"{prefix}:stable_pages_seen", str(stable_pages_seen))
    if total_available is not None:
        set_meta(conn, f"{prefix}:last_total", str(total_available))

    # Preserve the pre-v10 batch status keys for existing callers, but never
    # let incremental windows overwrite them.
    if mode == "batch":
        set_meta(conn, f"source:{source}:last_page", str(current_page))
        set_meta(conn, f"source:{source}:next_page", str(next_page))
        set_meta(conn, f"source:{source}:last_page_size", str(page_size))
        set_meta(conn, f"source:{source}:stable_pages_seen", str(stable_pages_seen))
        if total_available is not None:
            set_meta(conn, f"source:{source}:last_total", str(total_available))


def _checkpoint_prefix(source: str, mode: str) -> str:
    return f"source:{source}:{mode}"


def _resolve_resume_page(conn, source: str, *, mode: str, fallback: int) -> int:
    prefix = _checkpoint_prefix(source, mode)
    stored = get_meta(conn, f"{prefix}:next_page")
    if stored is None:
        last_page = get_meta(conn, f"{prefix}:last_page")
        if last_page is not None:
            try:
                return max(int(last_page) + 1, 1)
            except ValueError:
                return fallback

    # One-time compatibility path for databases created before checkpoint
    # namespaces existed.  Incremental mode deliberately never reads it.
    if stored is None and mode == "batch":
        stored = get_meta(conn, f"source:{source}:next_page")
        if stored is None:
            last_page = get_meta(conn, f"source:{source}:last_page")
            if last_page is not None:
                try:
                    return max(int(last_page) + 1, 1)
                except ValueError:
                    return fallback
    if stored is None:
        return fallback
    try:
        return max(int(stored), 1)
    except ValueError:
        return fallback


def _meta_int(conn, key: str, *, default: int) -> int:
    raw = get_meta(conn, key)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _get_existing_source_hash(conn, law_id: str) -> str | None:
    row = conn.execute("SELECT source_hash FROM laws WHERE id = ?", (law_id,)).fetchone()
    return row[0] if row else None


def _load_if_changed(conn, payload: dict) -> tuple[bool, int, bool]:
    existing_hash = _get_existing_source_hash(conn, payload["id"])
    if existing_hash == payload["source_hash"]:
        refresh_law_metadata(conn, payload)
        return False, 0, True
    return True, load_law_from_dict(conn, payload), False


def _resolve_incremental_end(explicit_to: str | None) -> date:
    if explicit_to:
        return date.fromisoformat(explicit_to)
    return datetime.now(timezone.utc).date()


def _resolve_incremental_start(
    conn,
    *,
    source: str,
    explicit_from: str | None,
    end_date: date,
    days_back: int,
    overlap_days: int,
) -> date:
    if explicit_from:
        return date.fromisoformat(explicit_from)

    stored = get_meta(conn, f"source:{source}:last_incremental_to")
    if stored:
        return max(date.fromisoformat(stored) - timedelta(days=overlap_days), date.min)

    return end_date - timedelta(days=days_back)
