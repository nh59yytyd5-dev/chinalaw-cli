"""Derived names must identify a document rather than a title quoted inside it."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from chinalaw import loader, service
from chinalaw.aliases import ALIAS_RULES_VERSION, common_law_aliases
from chinalaw.db import connect, get_meta, migrate, read_only_operation, set_meta

INTERPRETATION_TITLE = "最高人民法院关于审理非法集资刑事案件具体应用法律若干问题的解释"
DECISION_TITLE = f"最高人民法院关于修改《{INTERPRETATION_TITLE}》的决定"


class AliasBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / "library.db"

    def load_decision(self, *, aliases: list[str] | None = None) -> None:
        with connect(self.db) as conn:
            migrate(conn)
            loader.load_law_from_dict(
                conn,
                {
                    "id": "test-amending-decision",
                    "title": DECISION_TITLE,
                    "short_title": None,
                    "aliases": aliases or [],
                    "level": "judicial_interpretation",
                    "status": "current",
                    "source_url": "https://example.com/decision",
                    "source_name": "synthetic-test",
                    "source_checked_at": "2026-10-25T00:00:00+00:00",
                    "articles": [{"number": "1", "text": "示例修改内容。"}],
                },
            )

    def test_decisions_do_not_inherit_names_from_quoted_titles(self) -> None:
        titles = (
            DECISION_TITLE,
            "最高人民法院关于修改《最高人民法院关于审理劳动争议案件适用法律问题的解释》的决定",
            "最高人民法院关于修改《最高人民法院关于审理商品房买卖合同纠纷案件适用法律若干问题的解释》等二十七件民事类司法解释的决定",
            "最高人民法院关于修改〈最高人民法院关于适用《中华人民共和国公司法》若干问题的规定（三）〉的决定",
            DECISION_TITLE.replace("《", "〈").replace("》", "〉"),
            DECISION_TITLE + "（2026年修正）",
        )
        for title in titles:
            with self.subTest(title=title):
                expected = [DECISION_TITLE] if title.endswith("（2026年修正）") else []
                self.assertEqual(common_law_aliases(title), expected)
        self.assertIn("非法集资刑事解释", common_law_aliases(INTERPRETATION_TITLE))

    def test_resolve_does_not_return_a_decision_as_the_interpretation(self) -> None:
        self.load_decision()

        result = service.resolve(self.db, "非法集资刑事解释")

        self.assertFalse(result["matched"])
        self.assertEqual(service.resolve(self.db, DECISION_TITLE)["id"], "test-amending-decision")

    def test_rule_upgrade_removes_stale_derived_names_and_keeps_explicit_aliases(self) -> None:
        self.load_decision(aliases=["测试修改决定"])
        with connect(self.db) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO law_alias_index(alias, law_id, kind, position) VALUES (?, ?, ?, ?)",
                ("非法集资刑事解释", "test-amending-decision", "derived", 1),
            )
            set_meta(conn, "alias_rules_version", str(ALIAS_RULES_VERSION - 1))

        result = service.resolve(self.db, "非法集资刑事解释")

        self.assertFalse(result["matched"])
        self.assertEqual(service.resolve(self.db, "测试修改决定")["id"], "test-amending-decision")
        with connect(self.db) as conn:
            self.assertEqual(get_meta(conn, "alias_rules_version"), str(ALIAS_RULES_VERSION))
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM law_alias_index WHERE kind = 'derived'").fetchone()[0],
                0,
            )

    def test_readonly_lookup_does_not_refresh_stale_alias_rules(self) -> None:
        self.load_decision()
        with connect(self.db) as conn:
            set_meta(conn, "alias_rules_version", str(ALIAS_RULES_VERSION - 1))
        before = self.db.read_bytes()

        with read_only_operation():
            self.assertTrue(service.resolve(self.db, DECISION_TITLE)["matched"])

        self.assertEqual(before, self.db.read_bytes())
        with connect(self.db) as conn:
            self.assertEqual(get_meta(conn, "alias_rules_version"), str(ALIAS_RULES_VERSION - 1))
