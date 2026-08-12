"""Maintain exact-alias and FTS row indexes without scanning FTS payload columns."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable

from chinalaw.aliases import common_law_aliases, display_short_title


def _clean_aliases(values: Iterable[object]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            continue
        alias = value.strip()
        if alias and alias not in seen:
            result.append(alias)
            seen.add(alias)
    return result


def _decode_aliases(value: object) -> list[str]:
    if isinstance(value, list):
        return _clean_aliases(value)
    if not isinstance(value, str) or not value:
        return []
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        return []
    return _clean_aliases(decoded) if isinstance(decoded, list) else []


def law_alias_entries(
    title: str,
    short_title: str | None,
    aliases: Iterable[object],
) -> list[tuple[str, str, int]]:
    """Return ``(alias, kind, position)`` rows for exact and derived aliases."""

    exact = _clean_aliases(aliases)
    entries = [(alias, "exact", position) for position, alias in enumerate(exact)]
    seen = set(exact)
    stored_short = (short_title or "").strip()
    if stored_short:
        seen.add(stored_short)

    derived_candidates: list[str] = []
    displayed_short = display_short_title(title, short_title)
    if not stored_short and displayed_short:
        derived_candidates.append(displayed_short)
    derived_candidates.extend(common_law_aliases(title))
    for alias in _clean_aliases(derived_candidates):
        if alias in seen:
            continue
        entries.append((alias, "derived", len(entries)))
        seen.add(alias)
    return entries


def replace_law_alias_index(
    conn: sqlite3.Connection,
    *,
    law_id: str,
    title: str,
    short_title: str | None,
    aliases: Iterable[object],
) -> None:
    conn.execute("DELETE FROM law_alias_index WHERE law_id = ?", (law_id,))
    conn.executemany(
        "INSERT INTO law_alias_index(alias, law_id, kind, position) "
        "VALUES (?, ?, ?, ?)",
        (
            (alias, law_id, kind, position)
            for alias, kind, position in law_alias_entries(
                title,
                short_title,
                aliases,
            )
        ),
    )


def _delete_legacy_fts_rows(
    conn: sqlite3.Connection,
    *,
    fts_table: str,
    legacy_column: str,
    value: str,
) -> None:
    rows = conn.execute(
        f"SELECT rowid FROM {fts_table} WHERE {legacy_column} = ?",
        (value,),
    ).fetchall()
    if rows:
        placeholders = ", ".join("?" for _ in rows)
        conn.execute(
            f"DELETE FROM {fts_table} WHERE rowid IN ({placeholders})",
            [int(row[0]) for row in rows],
        )


def _delete_mapped_fts_rows(
    conn: sqlite3.Connection,
    *,
    fts_table: str,
    map_table: str,
    map_key: str,
    value: str,
    legacy_column: str,
    expected_count: int,
) -> None:
    rows = conn.execute(
        f"SELECT fts_rowid FROM {map_table} WHERE {map_key} = ?",
        (value,),
    ).fetchall()
    if rows:
        placeholders = ", ".join("?" for _ in rows)
        conn.execute(
            f"DELETE FROM {fts_table} WHERE rowid IN ({placeholders})",
            [int(row[0]) for row in rows],
        )
        conn.execute(f"DELETE FROM {map_table} WHERE {map_key} = ?", (value,))
    if len(rows) < expected_count:
        # Legacy/corrupt databases may have FTS rows without a mapping.  The
        # normal v11 path never reaches this scan; it is only a correctness
        # fallback while repairing incomplete indexes.
        _delete_legacy_fts_rows(
            conn,
            fts_table=fts_table,
            legacy_column=legacy_column,
            value=value,
        )


def _insert_fts_row(
    conn: sqlite3.Connection,
    *,
    fts_table: str,
    columns: tuple[str, ...],
    values: tuple[object, ...],
) -> int:
    placeholders = ", ".join("?" for _ in values)
    cursor = conn.execute(
        f"INSERT INTO {fts_table}({', '.join(columns)}) VALUES ({placeholders})",
        values,
    )
    return int(cursor.lastrowid)


def replace_law_search_indexes(
    conn: sqlite3.Connection,
    *,
    law_id: str,
    title: str,
    short_title: str | None,
    aliases: Iterable[object],
) -> None:
    alias_list = _clean_aliases(aliases)
    replace_law_alias_index(
        conn,
        law_id=law_id,
        title=title,
        short_title=short_title,
        aliases=alias_list,
    )
    _delete_mapped_fts_rows(
        conn,
        fts_table="laws_fts",
        map_table="laws_fts_rows",
        map_key="law_id",
        value=law_id,
        legacy_column="law_id",
        expected_count=1,
    )
    rowid = _insert_fts_row(
        conn,
        fts_table="laws_fts",
        columns=("law_id", "title", "short_title", "aliases"),
        values=(law_id, title, short_title or "", " ".join(alias_list)),
    )
    conn.execute(
        "INSERT INTO laws_fts_rows(law_id, fts_rowid) VALUES (?, ?)",
        (law_id, rowid),
    )


def delete_article_search_indexes(conn: sqlite3.Connection, law_id: str) -> None:
    expected = int(
        conn.execute(
            "SELECT COUNT(*) FROM articles WHERE law_id = ?",
            (law_id,),
        ).fetchone()[0]
    )
    _delete_mapped_fts_rows(
        conn,
        fts_table="articles_fts",
        map_table="articles_fts_rows",
        map_key="law_id",
        value=law_id,
        legacy_column="law_id",
        expected_count=expected,
    )


def insert_article_search_index(
    conn: sqlite3.Connection,
    *,
    article_id: str,
    law_id: str,
    law_title: str,
    number_display: str,
    text: str,
) -> None:
    rowid = _insert_fts_row(
        conn,
        fts_table="articles_fts",
        columns=("article_id", "law_id", "law_title", "number_display", "text"),
        values=(article_id, law_id, law_title, number_display, text),
    )
    conn.execute(
        "INSERT INTO articles_fts_rows(article_id, law_id, fts_rowid) "
        "VALUES (?, ?, ?)",
        (article_id, law_id, rowid),
    )


def replace_norm_source_search_index(
    conn: sqlite3.Connection,
    *,
    source_id: str,
    name: str,
    short_name: str | None,
    aliases: Iterable[object],
) -> None:
    alias_list = _clean_aliases(aliases)
    _delete_mapped_fts_rows(
        conn,
        fts_table="norm_sources_fts",
        map_table="norm_sources_fts_rows",
        map_key="norm_source_id",
        value=source_id,
        legacy_column="norm_source_id",
        expected_count=1,
    )
    rowid = _insert_fts_row(
        conn,
        fts_table="norm_sources_fts",
        columns=("norm_source_id", "name", "short_name", "aliases"),
        values=(source_id, name, short_name or "", " ".join(alias_list)),
    )
    conn.execute(
        "INSERT INTO norm_sources_fts_rows(norm_source_id, fts_rowid) VALUES (?, ?)",
        (source_id, rowid),
    )


def delete_norm_clause_search_indexes(
    conn: sqlite3.Connection,
    source_id: str,
) -> None:
    expected = int(
        conn.execute(
            "SELECT COUNT(*) FROM norm_clauses WHERE norm_source_id = ?",
            (source_id,),
        ).fetchone()[0]
    )
    _delete_mapped_fts_rows(
        conn,
        fts_table="norm_clauses_fts",
        map_table="norm_clauses_fts_rows",
        map_key="norm_source_id",
        value=source_id,
        legacy_column="norm_source_id",
        expected_count=expected,
    )


def insert_norm_clause_search_index(
    conn: sqlite3.Connection,
    *,
    clause_id: str,
    source_id: str,
    source_name: str,
    number_display: str,
    text: str,
) -> None:
    rowid = _insert_fts_row(
        conn,
        fts_table="norm_clauses_fts",
        columns=(
            "clause_id",
            "norm_source_id",
            "norm_source_name",
            "number_display",
            "text",
        ),
        values=(clause_id, source_id, source_name, number_display, text),
    )
    conn.execute(
        "INSERT INTO norm_clauses_fts_rows(clause_id, norm_source_id, fts_rowid) "
        "VALUES (?, ?, ?)",
        (clause_id, source_id, rowid),
    )


def rebuild_search_indexes(conn: sqlite3.Connection) -> None:
    """Rebuild alias and FTS mappings from canonical base tables."""

    for table in (
        "law_alias_index",
        "laws_fts_rows",
        "articles_fts_rows",
        "norm_sources_fts_rows",
        "norm_clauses_fts_rows",
    ):
        conn.execute(f"DELETE FROM {table}")
    for table in (
        "laws_fts",
        "articles_fts",
        "norm_sources_fts",
        "norm_clauses_fts",
    ):
        conn.execute(f"DELETE FROM {table}")

    for law in conn.execute(
        "SELECT id, title, short_title, aliases FROM laws ORDER BY rowid"
    ).fetchall():
        aliases = _decode_aliases(law["aliases"])
        replace_law_alias_index(
            conn,
            law_id=law["id"],
            title=law["title"],
            short_title=law["short_title"],
            aliases=aliases,
        )
        rowid = _insert_fts_row(
            conn,
            fts_table="laws_fts",
            columns=("law_id", "title", "short_title", "aliases"),
            values=(
                law["id"],
                law["title"],
                law["short_title"] or "",
                " ".join(aliases),
            ),
        )
        conn.execute(
            "INSERT INTO laws_fts_rows(law_id, fts_rowid) VALUES (?, ?)",
            (law["id"], rowid),
        )

    for article in conn.execute(
        """
        SELECT a.id, a.law_id, l.title AS law_title, a.number_display, a.text
        FROM articles a
        JOIN laws l ON l.id = a.law_id
        ORDER BY a.rowid
        """
    ).fetchall():
        insert_article_search_index(
            conn,
            article_id=article["id"],
            law_id=article["law_id"],
            law_title=article["law_title"],
            number_display=article["number_display"],
            text=article["text"],
        )

    for source in conn.execute(
        "SELECT id, name, short_name, aliases FROM norm_sources ORDER BY rowid"
    ).fetchall():
        aliases = _decode_aliases(source["aliases"])
        rowid = _insert_fts_row(
            conn,
            fts_table="norm_sources_fts",
            columns=("norm_source_id", "name", "short_name", "aliases"),
            values=(
                source["id"],
                source["name"],
                source["short_name"] or "",
                " ".join(aliases),
            ),
        )
        conn.execute(
            "INSERT INTO norm_sources_fts_rows(norm_source_id, fts_rowid) "
            "VALUES (?, ?)",
            (source["id"], rowid),
        )

    for clause in conn.execute(
        """
        SELECT c.id, c.norm_source_id, n.name AS source_name,
               COALESCE(c.number_display, c.number, '') AS number_display,
               c.text
        FROM norm_clauses c
        JOIN norm_sources n ON n.id = c.norm_source_id
        ORDER BY c.rowid
        """
    ).fetchall():
        insert_norm_clause_search_index(
            conn,
            clause_id=clause["id"],
            source_id=clause["norm_source_id"],
            source_name=clause["source_name"],
            number_display=clause["number_display"],
            text=clause["text"],
        )
