"""Tests for the registry-based DB migrator (PR refactor/db-migrator-registry).

详见 ``docs/DB_MIGRATOR_REGISTRY_SPEC.md``。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from chinalaw.db import (
    _MIGRATORS,
    _migrate_v0_to_v1,
    current_version,
    migrate,
    open_connection,
    set_meta,
)
from chinalaw.schema import SCHEMA_VERSION


class MigratorRegistryTests(unittest.TestCase):
    """守门 :data:`chinalaw.db._MIGRATORS` 的完整性与 ``migrate`` 主控流。"""

    REQUIRED_TABLES = (
        "meta",
        "laws",
        "articles",
        "document_number_index",
        "commentary_books",
        "article_commentaries",
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
                        # 先把所有表建出来再把 schema_version 强制设回 N，
                        # 模拟"DB 处于 vN 状态"。后续 migrator 必须 idempotent
                        # 跳过已存在 column / table。
                        _migrate_v0_to_v1(conn)
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


if __name__ == "__main__":
    unittest.main()
