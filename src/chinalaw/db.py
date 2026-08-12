"""本地 SQLite 存储层。

职责：
- 管理数据库连接（WAL、foreign_keys）
- 维护 schema 版本与 migration
- 提供 connect() / migrate() / current_version() 等基础设施

不包含业务查询，查询逻辑见 service.py。
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

from chinalaw.schema import SCHEMA_V11_DELTA_SQL, SCHEMA_V11_SQL, SCHEMA_VERSION

DEFAULT_DB_PATH = Path.home() / ".chinalaw" / "chinalaw.db"
SQLITE_BUSY_TIMEOUT_MS = 30_000
_SQLITE_LOCK_RETRY_INITIAL_SECONDS = 0.005
_SQLITE_LOCK_RETRY_MAX_SECONDS = 0.1


def open_connection(db_path: Path | str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=SQLITE_BUSY_TIMEOUT_MS / 1000)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute(f"PRAGMA busy_timeout = {SQLITE_BUSY_TIMEOUT_MS}")
        conn.execute("PRAGMA foreign_keys = ON")
        _enable_wal_with_lock_retry(conn)
    except Exception:
        conn.close()
        raise
    return conn


def _enable_wal_with_lock_retry(conn: sqlite3.Connection) -> None:
    """Enable WAL despite the transient lock race on a brand-new database.

    Two processes or threads can open the same empty file concurrently before
    :func:`migrate` acquires its ``BEGIN IMMEDIATE`` lock. SQLite may reject one
    of their simultaneous ``journal_mode`` changes immediately, even when the
    connection has a busy timeout. Retry only SQLITE_BUSY / SQLITE_LOCKED; all
    other initialization errors still fail loudly.
    """

    deadline = time.monotonic() + (SQLITE_BUSY_TIMEOUT_MS / 1000)
    delay = _SQLITE_LOCK_RETRY_INITIAL_SECONDS
    while True:
        try:
            conn.execute("PRAGMA journal_mode = WAL").fetchone()
            return
        except sqlite3.OperationalError as exc:
            if not _is_sqlite_lock_error(exc) or time.monotonic() >= deadline:
                raise
            time.sleep(delay)
            delay = min(delay * 2, _SQLITE_LOCK_RETRY_MAX_SECONDS)


def _is_sqlite_lock_error(exc: sqlite3.OperationalError) -> bool:
    """Recognize base and extended BUSY / LOCKED codes across Python 3.10+."""

    error_code = getattr(exc, "sqlite_errorcode", None)
    if error_code is not None:
        return error_code & 0xFF in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}
    message = str(exc).lower()
    return "database is locked" in message or "database table is locked" in message


def open_readonly_connection(db_path: Path | str) -> sqlite3.Connection:
    """Open an existing SQLite database without creating or migrating it."""

    path = Path(db_path)
    if not path.exists():
        raise FileNotFoundError(path)
    uri = f"{path.resolve().as_uri()}?mode=ro"
    conn = sqlite3.connect(
        uri,
        uri=True,
        timeout=SQLITE_BUSY_TIMEOUT_MS / 1000,
    )
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA query_only = ON")
        conn.execute(f"PRAGMA busy_timeout = {SQLITE_BUSY_TIMEOUT_MS}")
    except Exception:
        conn.close()
        raise
    return conn


@contextmanager
def connect(db_path: Path | str = DEFAULT_DB_PATH) -> Iterator[sqlite3.Connection]:
    conn = open_connection(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@contextmanager
def connect_readonly(db_path: Path | str) -> Iterator[sqlite3.Connection]:
    conn = open_readonly_connection(db_path)
    try:
        yield conn
    finally:
        conn.close()


def current_version(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT value FROM meta WHERE key = 'schema_version'"
    ).fetchone() if _meta_exists(conn) else None
    if row is None:
        return 0
    try:
        return int(row[0])
    except (TypeError, ValueError):
        return 0


def _meta_exists(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='meta'"
    ).fetchone()
    return row is not None


def migrate(conn: sqlite3.Connection) -> int:
    """确保 schema 为最新版本，返回迁移后的版本号。

    按 :data:`_MIGRATORS` 注册表逐档应用 migration。每个 migrator 把 schema
    从 ``v`` 升到 ``v+1``；空 DB（``current=0``）走 ``_migrate_v0_to_v1`` 中
    一次性 ``executescript(SCHEMA_V8_SQL)`` 的快路径，后续 ALTER migrator 都
    依赖 ``IF NOT EXISTS`` / ``PRAGMA table_info`` 的 idempotent 守护，重复跑
    无副作用。维护纪律见 ``docs/DEVELOPMENT_GUIDE.md`` §5。
    """
    current = current_version(conn)
    if current >= SCHEMA_VERSION:
        return current

    owns_transaction = not conn.in_transaction
    if owns_transaction:
        conn.execute("BEGIN IMMEDIATE")
    try:
        # Another process may have completed the migration while this
        # connection waited for the write lock.
        current = current_version(conn)
        while current < SCHEMA_VERSION:
            migrator = _MIGRATORS.get(current)
            if migrator is None:
                raise RuntimeError(
                    f"missing migrator for schema_version={current}; "
                    "_MIGRATORS registry is incomplete"
                )
            migrator(conn)
            current += 1

        set_meta(conn, "schema_version", str(SCHEMA_VERSION))
        if owns_transaction:
            conn.commit()
        return SCHEMA_VERSION
    except Exception:
        if owns_transaction:
            conn.rollback()
        raise


def ensure_schema(conn: sqlite3.Connection) -> None:
    """向下兼容的入口，等同于 migrate()。"""
    migrate(conn)


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
        "updated_at=CURRENT_TIMESTAMP",
        (key, value),
    )


def get_meta(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else default


def _execute_script(conn: sqlite3.Connection, script: str) -> None:
    """Execute a DDL script without ``executescript``'s implicit commit."""

    buffer: list[str] = []
    for line in script.splitlines(keepends=True):
        buffer.append(line)
        statement = "".join(buffer)
        if not sqlite3.complete_statement(statement):
            continue
        if statement.strip():
            conn.execute(statement)
        buffer = []
    if "".join(buffer).strip():
        raise sqlite3.OperationalError("incomplete SQL migration statement")


def _migrate_v1_to_v2(conn: sqlite3.Connection) -> None:
    columns = {
        row[1]
        for row in conn.execute("PRAGMA table_info(revisions)")
    }
    if "snapshot_json" not in columns:
        conn.execute("ALTER TABLE revisions ADD COLUMN snapshot_json TEXT")


def _migrate_v2_to_v3(conn: sqlite3.Connection) -> None:
    _execute_script(
        conn,
        """
        CREATE TABLE IF NOT EXISTS norm_packs (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            summary TEXT,
            scope TEXT,
            maintainer TEXT,
            version_policy TEXT NOT NULL DEFAULT 'current',
            source_kind TEXT NOT NULL DEFAULT 'manual',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS norm_pack_items (
            id TEXT PRIMARY KEY,
            pack_id TEXT NOT NULL REFERENCES norm_packs(id) ON DELETE CASCADE,
            item_type TEXT NOT NULL,
            law_id TEXT,
            law_title TEXT,
            article_number TEXT,
            article_number_display TEXT,
            role TEXT NOT NULL,
            reason TEXT,
            note TEXT,
            reference_text TEXT,
            position INTEGER NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_norm_packs_name ON norm_packs(name);
        CREATE INDEX IF NOT EXISTS idx_norm_pack_items_pack_position
            ON norm_pack_items(pack_id, position);
        """
    )


def _migrate_v3_to_v4(conn: sqlite3.Connection) -> None:
    _execute_script(
        conn,
        """
        CREATE TABLE IF NOT EXISTS norm_sources (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            short_name TEXT,
            aliases TEXT NOT NULL DEFAULT '[]',
            source_type TEXT NOT NULL,
            authority TEXT,
            binding_scope TEXT,
            jurisdiction TEXT,
            effective_at TEXT,
            repealed_at TEXT,
            source_url TEXT,
            source_name TEXT NOT NULL,
            source_checked_at TEXT NOT NULL,
            source_hash TEXT NOT NULL,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS norm_clauses (
            id TEXT PRIMARY KEY,
            norm_source_id TEXT NOT NULL REFERENCES norm_sources(id) ON DELETE CASCADE,
            number TEXT,
            number_display TEXT,
            title TEXT,
            text TEXT NOT NULL,
            position INTEGER NOT NULL,
            UNIQUE(norm_source_id, position)
        );

        CREATE INDEX IF NOT EXISTS idx_norm_sources_type ON norm_sources(source_type);
        CREATE INDEX IF NOT EXISTS idx_norm_clauses_source_position
            ON norm_clauses(norm_source_id, position);

        CREATE VIRTUAL TABLE IF NOT EXISTS norm_sources_fts USING fts5(
            norm_source_id UNINDEXED,
            name,
            short_name,
            aliases,
            tokenize = 'trigram'
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS norm_clauses_fts USING fts5(
            clause_id UNINDEXED,
            norm_source_id UNINDEXED,
            norm_source_name,
            number_display UNINDEXED,
            text,
            tokenize = 'trigram'
        );
        """
    )


def _migrate_v4_to_v5(conn: sqlite3.Connection) -> None:
    columns = {
        row[1]
        for row in conn.execute("PRAGMA table_info(norm_pack_items)")
    }
    additions = {
        "norm_source_id": "TEXT",
        "norm_source_name": "TEXT",
        "clause_number": "TEXT",
        "clause_number_display": "TEXT",
    }
    for column, column_type in additions.items():
        if column not in columns:
            conn.execute(
                f"ALTER TABLE norm_pack_items ADD COLUMN {column} {column_type}"
            )


def _migrate_v5_to_v6(conn: sqlite3.Connection) -> None:
    columns = {
        row[1]
        for row in conn.execute("PRAGMA table_info(norm_packs)")
    }
    if "dependencies_json" not in columns:
        conn.execute(
            "ALTER TABLE norm_packs ADD COLUMN "
            "dependencies_json TEXT NOT NULL DEFAULT '{}'"
        )


def _migrate_v6_to_v7(conn: sqlite3.Connection) -> None:
    _execute_script(
        conn,
        """
        CREATE TABLE IF NOT EXISTS law_relations (
            id TEXT PRIMARY KEY,
            relation_type TEXT NOT NULL,
            from_law_id TEXT NOT NULL,
            from_law_title TEXT,
            to_law_id TEXT NOT NULL,
            to_law_title TEXT,
            effective_at TEXT,
            source_name TEXT NOT NULL,
            source_url TEXT,
            source_checked_at TEXT NOT NULL,
            notes TEXT,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_law_relations_from
            ON law_relations(from_law_id, relation_type);
        CREATE INDEX IF NOT EXISTS idx_law_relations_to
            ON law_relations(to_law_id, relation_type);
        CREATE INDEX IF NOT EXISTS idx_law_relations_effective
            ON law_relations(effective_at);

        CREATE TABLE IF NOT EXISTS applicability_rules (
            id TEXT PRIMARY KEY,
            topic TEXT NOT NULL,
            domain TEXT NOT NULL DEFAULT 'all',
            primary_law_id TEXT NOT NULL,
            primary_law_title TEXT,
            fallback_law_id TEXT,
            fallback_law_title TEXT,
            effective_from TEXT,
            effective_to TEXT,
            rule_text TEXT NOT NULL,
            transition_text TEXT,
            source_name TEXT NOT NULL,
            source_url TEXT,
            source_checked_at TEXT NOT NULL,
            confidence TEXT NOT NULL DEFAULT 'seed',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_applicability_rules_topic
            ON applicability_rules(topic);
        CREATE INDEX IF NOT EXISTS idx_applicability_rules_domain
            ON applicability_rules(domain);
        CREATE INDEX IF NOT EXISTS idx_applicability_rules_window
            ON applicability_rules(effective_from, effective_to);
        CREATE INDEX IF NOT EXISTS idx_applicability_rules_primary
            ON applicability_rules(primary_law_id);
        CREATE INDEX IF NOT EXISTS idx_applicability_rules_fallback
            ON applicability_rules(fallback_law_id);
        """
    )


def _migrate_v7_to_v8(conn: sqlite3.Connection) -> None:
    """新增 ``document_number_index`` 反查表。详见 schema.py SCHEMA_V8_SQL 注释。"""
    _execute_script(
        conn,
        """
        CREATE TABLE IF NOT EXISTS document_number_index (
            document_number TEXT NOT NULL,
            source TEXT NOT NULL,
            source_id TEXT NOT NULL,
            law_id TEXT,
            title TEXT,
            indexed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (document_number, source)
        );

        CREATE INDEX IF NOT EXISTS idx_document_number_index_doc
            ON document_number_index(document_number);
        """
    )


def _migrate_v8_to_v9(conn: sqlite3.Connection) -> None:
    _execute_script(
        conn,
        """
        CREATE TABLE IF NOT EXISTS commentary_books (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            author TEXT,
            publisher TEXT,
            edition TEXT,
            published_at TEXT,
            isbn TEXT,
            source_name TEXT NOT NULL,
            source_url TEXT,
            license_scope TEXT NOT NULL DEFAULT 'local_only',
            notes TEXT,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS article_commentaries (
            id TEXT PRIMARY KEY,
            book_id TEXT NOT NULL REFERENCES commentary_books(id) ON DELETE CASCADE,
            law_id TEXT NOT NULL,
            law_title TEXT,
            article_number TEXT NOT NULL,
            article_number_display TEXT,
            page_start INTEGER,
            page_end INTEGER,
            excerpt TEXT,
            summary TEXT,
            ocr_confidence REAL,
            boundary_confidence REAL,
            qa_status TEXT NOT NULL DEFAULT 'unchecked',
            license_scope TEXT NOT NULL DEFAULT 'local_only',
            source_hash TEXT,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            position INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_commentary_books_title
            ON commentary_books(title);
        CREATE INDEX IF NOT EXISTS idx_article_commentaries_article
            ON article_commentaries(law_id, article_number);
        CREATE INDEX IF NOT EXISTS idx_article_commentaries_book
            ON article_commentaries(book_id, position);
        """
    )


def _migrate_v9_to_v10(conn: sqlite3.Connection) -> None:
    """Normalize the legacy non-contract department level value."""

    conn.execute(
        "UPDATE laws SET level = 'department_rule' "
        "WHERE level = 'departmental_rule'"
    )


def _migrate_v10_to_v11(conn: sqlite3.Connection) -> None:
    """Add exact-alias and FTS row mappings, then remove legacy duplicates."""

    from chinalaw.search_indexes import rebuild_search_indexes

    _execute_script(conn, SCHEMA_V11_DELTA_SQL)
    rebuild_search_indexes(conn)


def _migrate_v0_to_v1(conn: sqlite3.Connection) -> None:
    """空 DB → 一次性落最新累积 DDL。

    历史决定（沿用 :data:`SCHEMA_V8_SQL` 一次性 ``executescript``）：
    ``schema.py`` 把 v1..v9 的 DDL 拼成单一字符串，依赖 SQLite
    ``CREATE TABLE IF NOT EXISTS`` 幂等性避免对全新 DB 跑 7 次 ALTER。
    本 wrapper 把这一行为收进 :data:`_MIGRATORS` 注册表，保留单步升一档的
    contract（v0 → v1）；后续 ``_migrate_v1_to_v2`` ... ``_migrate_v7_to_v8``
    在自身的 ``IF NOT EXISTS`` / ``PRAGMA table_info`` 守护下重复跑全部
    ALTER 无副作用。维护纪律见 docs/DEVELOPMENT_GUIDE.md §5。
    """
    _execute_script(conn, SCHEMA_V11_SQL)


# Migrator 注册表：``current_version`` → 把 schema 升一档的回调。
#
# 每次新增 schema 版本时必须同步追加键值对（详见
# ``docs/DEVELOPMENT_GUIDE.md`` §5）。下面的 module-level
# assert 在 import 时立刻校验注册表覆盖 ``[0, SCHEMA_VERSION)``，把"忘加
# elif 分支但 schema_version 仍被升号"这一类 silent corruption 提前到模块
# 加载阶段失败。
_MIGRATORS: dict[int, Callable[[sqlite3.Connection], None]] = {
    0: _migrate_v0_to_v1,
    1: _migrate_v1_to_v2,
    2: _migrate_v2_to_v3,
    3: _migrate_v3_to_v4,
    4: _migrate_v4_to_v5,
    5: _migrate_v5_to_v6,
    6: _migrate_v6_to_v7,
    7: _migrate_v7_to_v8,
    8: _migrate_v8_to_v9,
    9: _migrate_v9_to_v10,
    10: _migrate_v10_to_v11,
}

assert set(_MIGRATORS) == set(range(0, SCHEMA_VERSION)), (
    f"_MIGRATORS keys {sorted(_MIGRATORS)} 不等于 [0, {SCHEMA_VERSION})；"
    "新增 schema 版本时必须同步更新 _MIGRATORS（详见 "
    "docs/DEVELOPMENT_GUIDE.md §5）"
)
