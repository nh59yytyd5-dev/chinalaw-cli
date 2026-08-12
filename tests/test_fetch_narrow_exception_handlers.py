"""PR5c 守门测试 — `fetch.py` 5 处 `except Exception` 收窄到具体异常类型。

异常边界遵循 ``docs/CONTRACT.md`` 中的只读查询与结构化错误契约。

立场：``except Exception: return None`` 把真正的编程 bug（``AttributeError`` /
``KeyError`` / 配置错误）静默吞成"找不到"。本 PR 把 5 处分两类：

- DB 路径（``_resolve_local_fetch_hint`` / ``_lookup_document_number_hint``
  / ``_try_resolve_canonical_id``）→ ``(sqlite3.OperationalError,
  sqlite3.DatabaseError, OSError)``
- 解析路径（``_locate_article`` 两处 ``normalize_article_number`` 调用）
  → ``(TypeError, ValueError)``

本测试三组守门：

1. ``DbHandlersStillSwallowDbErrors``：DB 函数遇 sqlite3.OperationalError
   仍正常 return None（不退化为抛错）。
2. ``DbHandlersPropagateProgrammingErrors``：DB 函数遇 AttributeError /
   RuntimeError 直接抛（不再被吞）。
3. ``LocateArticleHandlers``：``_locate_article`` 解析异常仍 None / fallback；
   非解析异常透传。
"""

from __future__ import annotations

import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import ClassVar
from unittest.mock import patch

from chinalaw import fetch


class DbHandlersStillSwallowDbErrors(unittest.TestCase):
    """DB 异常被新窄 except 接住，行为与修前等价（return None）。"""

    def test_resolve_local_fetch_hint_swallows_operational_error(self) -> None:
        with TemporaryDirectory() as td:
            db = Path(td) / "stub.db"
            db.write_bytes(b"not a sqlite db")  # 损坏文件 → DatabaseError
            # 只读连接会抛 sqlite3.DatabaseError；新窄 except 接住
            self.assertIsNone(
                fetch._resolve_local_fetch_hint(db, "X", "flk_npc")
            )

    def test_lookup_document_number_hint_swallows_operational_error(
        self,
    ) -> None:
        with TemporaryDirectory() as td:
            db = Path(td) / "stub.db"
            db.write_bytes(b"not a sqlite db")
            # name 是文号样式，否则前置 _looks_like_document_number 已经 None
            self.assertIsNone(
                fetch._lookup_document_number_hint(
                    db, "法释〔2023〕13号", "flk_npc"
                )
            )

    def test_try_resolve_canonical_id_swallows_operational_error(self) -> None:
        with TemporaryDirectory() as td:
            db = Path(td) / "stub.db"
            db.write_bytes(b"not a sqlite db")
            self.assertIsNone(
                fetch._try_resolve_canonical_id(
                    db,
                    {
                        "id": "x",
                        "title": "X",
                        "source_url": "https://flk.npc.gov.cn/?id=x",
                        "source_name": "flk.npc.gov.cn",
                    },
                )
            )


class DbHandlersPropagateProgrammingErrors(unittest.TestCase):
    """编程错误（AttributeError / RuntimeError）现在透传，不再被吞。

    用 mock 注入异常验证：修前 ``except Exception`` 会把这些静默吞掉，本 PR
    后必须冒到调用方。
    """

    def test_resolve_local_fetch_hint_propagates_attribute_error(self) -> None:
        # mock _resolve_law_row 抛 AttributeError；窄 except 不再吞
        with TemporaryDirectory() as td:
            db = Path(td) / "t.db"
            from chinalaw.db import connect, migrate

            with connect(db) as conn:
                migrate(conn)
            with (
                patch.object(
                    fetch,
                    "_resolve_law_row",
                    side_effect=AttributeError("simulated bug"),
                ),
                self.assertRaises(AttributeError),
            ):
                fetch._resolve_local_fetch_hint(db, "X", "flk_npc")

    def test_resolve_local_fetch_hint_propagates_runtime_error(self) -> None:
        # 模拟只读连接层抛 RuntimeError；本 PR 后透传
        with TemporaryDirectory() as td:
            db = Path(td) / "t.db"
            db.touch()  # 让 db.exists() 通过；空文件 sqlite3 视为 new DB
            with (
                patch.object(
                    fetch,
                    "connect_readonly",
                    side_effect=RuntimeError("simulated readonly failure"),
                ),
                self.assertRaises(RuntimeError),
            ):
                fetch._resolve_local_fetch_hint(db, "X", "flk_npc")

    def test_try_resolve_canonical_id_propagates_runtime_error(self) -> None:
        with TemporaryDirectory() as td:
            db = Path(td) / "t.db"
            db.touch()
            with (
                patch.object(
                    fetch,
                    "connect_readonly",
                    side_effect=RuntimeError("simulated readonly failure"),
                ),
                self.assertRaises(RuntimeError),
            ):
                fetch._try_resolve_canonical_id(
                    db,
                    {
                        "id": "x",
                        "title": "X",
                        "source_url": "https://flk.npc.gov.cn/?id=x",
                        "source_name": "flk.npc.gov.cn",
                    },
                )

    def test_resolve_local_fetch_hint_propagates_sqlite_programming_error(
        self,
    ) -> None:
        # sqlite3.ProgrammingError 是 sqlite3.DatabaseError 的子类；必须显式
        # 透传，否则 SQL 绑定错误 / 连接误用仍会被误诊为"hint 不命中"。
        with TemporaryDirectory() as td:
            db = Path(td) / "t.db"
            db.touch()
            with (
                patch.object(
                    fetch,
                    "connect_readonly",
                    side_effect=sqlite3.ProgrammingError("simulated SQL misuse"),
                ),
                self.assertRaises(sqlite3.ProgrammingError),
            ):
                fetch._resolve_local_fetch_hint(db, "X", "flk_npc")


class LocateArticleHandlers(unittest.TestCase):
    """``_locate_article`` 解析异常仍 None / fallback；非解析异常透传。"""

    PAYLOAD: ClassVar[dict] = {
        "articles": [
            {
                "number": "1",
                "number_display": "第一条",
                "text": "总则。",
                "part": None,
            },
            {
                "number": "2",
                "number_display": "第二条",
                "text": "应用。",
                "part": None,
            },
        ],
    }

    def test_locate_article_returns_none_for_typeerror(self) -> None:
        """``requested=None`` 让 normalize_article_number 走 ``raw is None ->
        ""``，target 为空字符串后 for 循环找不到 → None；属于业务正常。"""

        # 实际不抛 TypeError（normalize 已防御 None），仅作健壮性回归
        self.assertIsNone(fetch._locate_article(self.PAYLOAD, None))

    def test_locate_article_returns_none_for_invalid_number(self) -> None:
        """非常规字符（如 ``"第〤条"``）让 ``_chinese_to_arabic`` 抛
        ValueError；新窄 except 接住返回 None。"""

        # 模拟 normalize_article_number 抛 ValueError 走 except 路径
        with patch.object(
            fetch,
            "normalize_article_number",
            side_effect=ValueError("unparseable"),
        ):
            self.assertIsNone(fetch._locate_article(self.PAYLOAD, "第〤条"))

    def test_locate_article_propagates_attribute_error(self) -> None:
        """非解析异常（AttributeError）现在透传，不再被吞。"""

        with (
            patch.object(
                fetch,
                "normalize_article_number",
                side_effect=AttributeError("simulated bug"),
            ),
            self.assertRaises(AttributeError),
        ):
            fetch._locate_article(self.PAYLOAD, "第一条")

    def test_locate_article_candidate_fallback_on_value_error(self) -> None:
        """payload 内的 candidate 归一抛 ValueError 时 fallback 到原值（不
        return None）；行为与修前等价。"""

        # 第一次（target）成功，第二次（candidate）抛 ValueError；fallback
        # 后 candidate_number 与 target("1") 不相等 → 跳过这一条，最终命中
        # 第二条返回 None（"1" != "2"）。
        call_count = {"n": 0}

        def fake_normalize(raw):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return "999"  # target = 999
            raise ValueError("simulated parse error")

        with patch.object(
            fetch, "normalize_article_number", side_effect=fake_normalize
        ):
            # target=999 找不到匹配（candidate fallback 到原值 "1" / "2"）→ None
            result = fetch._locate_article(self.PAYLOAD, "999")
            self.assertIsNone(result)

    def test_locate_article_normal_path_unchanged(self) -> None:
        """正常输入回归：行为与修前完全等价。"""

        result = fetch._locate_article(self.PAYLOAD, "第一条")
        self.assertIsNotNone(result)
        self.assertEqual(result["number"], "1")


class ImportHasSqlite3(unittest.TestCase):
    """守门：fetch.py 顶部加了 ``import sqlite3``（窄 except 必需）。"""

    def test_fetch_module_imports_sqlite3(self) -> None:
        self.assertIs(fetch.sqlite3, sqlite3)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
