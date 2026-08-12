"""Tests for the registry-based DB migrator (PR refactor/db-migrator-registry).

详见 ``docs/DB_MIGRATOR_REGISTRY_SPEC.md``。
"""

from __future__ import annotations

import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from chinalaw.db import (
    _MIGRATORS,
    SQLITE_BUSY_TIMEOUT_MS,
    _migrate_v0_to_v1,
    current_version,
    migrate,
    open_connection,
    set_meta,
)
from chinalaw.schema import (
    SCHEMA_V1_SQL,
    SCHEMA_V2_SQL,
    SCHEMA_V3_SQL,
    SCHEMA_V4_SQL,
    SCHEMA_V5_SQL,
    SCHEMA_V6_SQL,
    SCHEMA_V7_SQL,
    SCHEMA_V8_SQL,
    SCHEMA_V9_SQL,
    SCHEMA_V10_SQL,
    SCHEMA_VERSION,
)

SCHEMA_SQL_BY_VERSION = {
    1: SCHEMA_V1_SQL,
    2: SCHEMA_V2_SQL,
    3: SCHEMA_V3_SQL,
    4: SCHEMA_V4_SQL,
    5: SCHEMA_V5_SQL,
    6: SCHEMA_V6_SQL,
    7: SCHEMA_V7_SQL,
    8: SCHEMA_V8_SQL,
    9: SCHEMA_V9_SQL,
    10: SCHEMA_V10_SQL,
}


class MigratorRegistryTests(unittest.TestCase):
    """守门 :data:`chinalaw.db._MIGRATORS` 的完整性与 ``migrate`` 主控流。"""

    REQUIRED_TABLES = (
        "meta",
        "laws",
        "articles",
        "document_number_index",
        "commentary_books",
        "article_commentaries",
        "law_alias_index",
        "laws_fts_rows",
        "articles_fts_rows",
        "norm_sources_fts_rows",
        "norm_clauses_fts_rows",
    )

    def test_migrators_registry_complete(self) -> None:
        """注册表必须覆盖 ``[0, SCHEMA_VERSION)`` 全部起点。

        加 v9 时若忘了在 ``_MIGRATORS`` 加 ``8: _migrate_v8_to_v9``，
        module-level assert 在 import 时已经失败；本测试是显式守门，避免
        ``python -O`` 关掉 assert 时仍能漂移。
        """

        self.assertEqual(set(_MIGRATORS), set(range(0, SCHEMA_VERSION)))

    def test_migrate_idempotent_at_latest(self) -> None:
        """已是最新 schema 时再 ``migrate`` 不应报错或重复升级。"""

        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "t.db"
            conn = open_connection(db)
            try:
                first = migrate(conn)
                self.assertEqual(first, SCHEMA_VERSION)
                second = migrate(conn)
                self.assertEqual(second, SCHEMA_VERSION)
                self.assertEqual(current_version(conn), SCHEMA_VERSION)
            finally:
                conn.close()

    def test_connection_configures_busy_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            conn = open_connection(Path(tmp) / "t.db")
            try:
                timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
            finally:
                conn.close()
        self.assertEqual(timeout, SQLITE_BUSY_TIMEOUT_MS)

    def test_concurrent_empty_database_migrations_serialize(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "t.db"

            def run_migration() -> int:
                conn = open_connection(db)
                try:
                    return migrate(conn)
                finally:
                    conn.close()

            with ThreadPoolExecutor(max_workers=2) as pool:
                versions = list(pool.map(lambda _item: run_migration(), range(2)))

        self.assertEqual(versions, [SCHEMA_VERSION, SCHEMA_VERSION])

    def test_migrate_from_each_version(self) -> None:
        """从 0..SCHEMA_VERSION-1 任一起点都能升到最新且关键表齐全。

        覆盖 reviewer C3 触发场景：阶梯式 if/elif 漏一档时本测试会立刻 fail，
        因为新档 ``schema_version=N`` 起点的实际表状态不会被升一级。
        """

        for start in range(0, SCHEMA_VERSION):
            with (
                self.subTest(start=start),
                tempfile.TemporaryDirectory() as tmp,
            ):
                db = Path(tmp) / "t.db"
                conn = open_connection(db)
                try:
                    if start == 0:
                        # 真正的空 DB，让 migrate 走 _migrate_v0_to_v1。
                        self.assertEqual(current_version(conn), 0)
                    else:
                        # 使用真实的累计 vN DDL，而不是先建最新 schema 再倒填
                        # 版本号；否则漏写某档 migrator 也会被最新表结构掩盖。
                        conn.executescript(SCHEMA_SQL_BY_VERSION[start])
                        set_meta(conn, "schema_version", str(start))
                        conn.commit()
                        self.assertEqual(current_version(conn), start)

                    final = migrate(conn)
                    self.assertEqual(final, SCHEMA_VERSION)
                    self.assertEqual(current_version(conn), SCHEMA_VERSION)

                    existing_tables = {
                        row[0]
                        for row in conn.execute(
                            "SELECT name FROM sqlite_master WHERE type='table'"
                        ).fetchall()
                    }
                    for required in self.REQUIRED_TABLES:
                        self.assertIn(
                            required,
                            existing_tables,
                            f"start={start}: missing required table {required}",
                        )
                finally:
                    conn.close()

    def test_v10_normalizes_legacy_department_rule_level(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "t.db"
            conn = open_connection(db)
            try:
                _migrate_v0_to_v1(conn)
                conn.execute(
                    """
                    INSERT INTO laws (
                        id, title, aliases, level, status, source_url,
                        source_name, source_checked_at, source_hash
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "legacy-department-rule",
                        "历史部门规章",
                        "[]",
                        "departmental_rule",
                        "current",
                        "https://example.test/legacy",
                        "example.test",
                        "2026-08-06T00:00:00+00:00",
                        "legacy-hash",
                    ),
                )
                set_meta(conn, "schema_version", "9")
                conn.commit()

                self.assertEqual(migrate(conn), SCHEMA_VERSION)
                row = conn.execute(
                    "SELECT level FROM laws WHERE id = ?",
                    ("legacy-department-rule",),
                ).fetchone()
                self.assertEqual(row["level"], "department_rule")
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()
