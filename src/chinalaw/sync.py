"""真实数据源同步编排。"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from chinalaw.db import connect, get_meta, migrate, set_meta
from chinalaw.loader import load_law_from_dict
from chinalaw.sources import get_source_adapter


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
    if normalized != "flk_npc":
        raise ValueError(f"unsupported source for sync: {source}")

    adapter = get_source_adapter(normalized)

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

    total_laws = 0
    total_articles = 0
    laws_skipped = 0
    loaded_titles: list[str] = []

    with connect(db_path) as conn:
        migrate(conn)
        rows_by_id = {row["bbbs"]: row for row in rows if row.get("bbbs")} if not bbbs else {}
        for law_id in ids:
            payload = adapter.build_law_payload(law_id, search_row=rows_by_id.get(law_id))
            changed, article_count = _load_if_changed(conn, payload)
            if changed:
                total_articles += article_count
                total_laws += 1
                loaded_titles.append(payload["title"])
            else:
                laws_skipped += 1

        checked_at = datetime.now(timezone.utc).isoformat()
        set_meta(conn, "last_sync_at", checked_at)
        set_meta(conn, f"source:{normalized}:last_sync_at", checked_at)
        set_meta(conn, f"source:{normalized}:last_mode", mode)
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
        "articles_loaded": total_articles,
        "titles": loaded_titles,
    }


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

    end_date = _resolve_incremental_end(published_to)
    with connect(db_path) as conn:
        migrate(conn)
        start_date = _resolve_incremental_start(
            conn,
            source=source,
            explicit_from=published_from,
            end_date=end_date,
            days_back=days_back,
            overlap_days=overlap_days,
        )

    return _sync_batch(
        db_path,
        source=source,
        adapter=adapter,
        start_page=start_page,
        max_pages=max_pages,
        page_size=page_size,
        resume=False,
        stop_after_stable_pages=stop_after_stable_pages,
        search_query="",
        search_since=start_date.isoformat(),
        search_until=end_date.isoformat(),
        mode="incremental",
        extra_meta={
            "source:flk_npc:last_incremental_from": start_date.isoformat(),
            "source:flk_npc:last_incremental_to": end_date.isoformat(),
        },
    )


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

    total_laws = 0
    total_articles = 0
    laws_skipped = 0
    loaded_titles: list[str] = []
    pages_synced = 0
    rows_seen = 0
    total_available: int | None = None
    current_page = start_page
    stable_pages_seen = 0
    stop_reason = "exhausted"

    with connect(db_path) as conn:
        migrate(conn)
        if resume:
            current_page = _resolve_resume_page(conn, source, fallback=start_page)
        resolved_start_page = current_page

        while True:
            if max_pages is not None and pages_synced >= max_pages:
                stop_reason = "max_pages"
                break

            if search_since is not None or search_until is not None:
                search_result = adapter.list_laws(
                    since=search_since,
                    until=search_until,
                    page_num=current_page,
                    page_size=page_size,
                )
            else:
                search_result = adapter.search_list(
                    search_query, page_num=current_page, page_size=page_size
                )
            if total_available is None:
                total_available = search_result.get("total")

            rows = search_result.get("rows") or []
            if not rows:
                stop_reason = "no_rows"
                break

            page_changed = False
            for row in rows:
                law_id = row.get("bbbs")
                if not law_id:
                    continue
                payload = adapter.build_law_payload(law_id, search_row=row)
                changed, article_count = _load_if_changed(conn, payload)
                if changed:
                    total_articles += article_count
                    total_laws += 1
                    loaded_titles.append(payload["title"])
                    page_changed = True
                else:
                    laws_skipped += 1
                rows_seen += 1

            pages_synced += 1
            checked_at = datetime.now(timezone.utc).isoformat()
            set_meta(conn, "last_sync_at", checked_at)
            set_meta(conn, f"source:{source}:last_sync_at", checked_at)
            set_meta(conn, f"source:{source}:last_mode", mode)
            set_meta(conn, f"source:{source}:last_page", str(current_page))
            set_meta(conn, f"source:{source}:next_page", str(current_page + 1))
            set_meta(conn, f"source:{source}:last_page_size", str(page_size))
            if total_available is not None:
                set_meta(conn, f"source:{source}:last_total", str(total_available))
            if extra_meta:
                for key, value in extra_meta.items():
                    set_meta(conn, key, value)
            if page_changed:
                set_meta(conn, f"source:{source}:last_changed_page", str(current_page))
                stable_pages_seen = 0
                set_meta(conn, f"source:{source}:stable_pages_seen", "0")
            else:
                stable_pages_seen += 1
                set_meta(conn, f"source:{source}:stable_pages_seen", str(stable_pages_seen))
                if (
                    stop_after_stable_pages is not None
                    and stable_pages_seen >= stop_after_stable_pages
                ):
                    stop_reason = "stable_pages"
                    current_page += 1
                    break

            current_page += 1

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


def _resolve_resume_page(conn, source: str, *, fallback: int) -> int:
    stored = get_meta(conn, f"source:{source}:next_page")
    if stored is None:
        stored = get_meta(conn, f"source:{source}:last_page")
        if stored is not None:
            try:
                return int(stored) + 1
            except ValueError:
                return fallback
    if stored is None:
        return fallback
    try:
        return max(int(stored), 1)
    except ValueError:
        return fallback


def _get_existing_source_hash(conn, law_id: str) -> str | None:
    row = conn.execute("SELECT source_hash FROM laws WHERE id = ?", (law_id,)).fetchone()
    return row[0] if row else None


def _load_if_changed(conn, payload: dict) -> tuple[bool, int]:
    existing_hash = _get_existing_source_hash(conn, payload["id"])
    if existing_hash == payload["source_hash"]:
        return False, 0
    return True, load_law_from_dict(conn, payload)


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
