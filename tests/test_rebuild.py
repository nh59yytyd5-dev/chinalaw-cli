"""Tests for the public cleaning rebuild workflow."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from chinalaw import rebuild, service
from chinalaw.cleaning import CLEANING_SCHEMA_VERSION
from chinalaw.db import connect, migrate
from chinalaw.loader import load_law_from_dict


def _dirty_interpretation_payload() -> dict:
    return {
        "id": "court-general-interpretation-2022",
        "title": "最高人民法院关于适用《中华人民共和国民法典》总则编若干问题的解释",
        "short_title": None,
        "aliases": [],
        "level": "judicial_interpretation",
        "status": "current",
        "issuing_body": "最高人民法院",
        "document_number": None,
        "released_at": "2022-02-24",
        "effective_at": "2022-03-01",
        "repealed_at": None,
        "source_url": "https://flk.npc.gov.cn/detail?id=ff8081817f2d0cde017f3577f95f0377",
        "source_name": "flk.npc.gov.cn",
        "source_checked_at": "2026-04-30T00:00:00+00:00",
        "source_hash": "same-upstream-hash",
        "articles": [
            {
                "number": "1",
                "number_display": "第一条",
                "text": "第一条正文。\n二、民事权利",
                "part": None,
            },
            {
                "number": "2",
                "number_display": "第二条",
                "text": "第二条正文。",
                "part": None,
            },
        ],
    }


class RebuildCleanTests(unittest.TestCase):
    def _load_dirty_payload(self, db_path: Path) -> None:
        with connect(db_path) as conn:
            migrate(conn)
            load_law_from_dict(conn, _dirty_interpretation_payload())
            # The loader now enforces current cleaning invariants. Recreate a
            # legacy pre-contract row directly so rebuild-clean still tests its
            # migration purpose rather than bypassing the ingest choke point.
            conn.execute(
                "UPDATE articles SET text = ? WHERE law_id = ? AND number = ?",
                (
                    "第一条正文。\n二、民事权利",
                    "court-general-interpretation-2022",
                    "1",
                ),
            )
            conn.execute(
                "UPDATE articles SET part = NULL WHERE law_id = ? AND number = ?",
                ("court-general-interpretation-2022", "2"),
            )

    def test_rebuild_clean_reapplies_alias_and_heading_rules(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "t.db"
            self._load_dirty_payload(db_path)

            dry = rebuild.rebuild_clean(db_path, law="总则编解释", dry_run=True)
            self.assertTrue(dry["ok"])
            self.assertTrue(dry["dry_run"])
            self.assertEqual(dry["changed_count"], 1)
            self.assertEqual(dry["cleaning_schema_version"], CLEANING_SCHEMA_VERSION)
            self.assertIn("总则编解释", dry["items"][0]["aliases_after"])
            article_before = service.get_article(db_path, "总则编解释", "第一条")
            self.assertIn("二、民事权利", article_before["article"]["text"])

            written = rebuild.rebuild_clean(db_path, law="总则编解释")
            self.assertTrue(written["ok"])
            self.assertEqual(written["rebuilt_count"], 1)
            article_after = service.get_article(db_path, "总则编解释", "第一条")
            second = service.get_article(db_path, "总则编解释", "第二条")

        self.assertEqual(article_after["article"]["text"], "第一条正文。")
        self.assertEqual(second["article"]["part"], "二、民事权利")
        self.assertIn("总则编解释", article_after["law"]["aliases"])

    def test_rebuild_clean_reports_missing_law(self):
        with tempfile.TemporaryDirectory() as td:
            report = rebuild.rebuild_clean(Path(td) / "t.db", law="不存在的法")

        self.assertFalse(report["ok"])
        self.assertFalse(report["found"])
        self.assertEqual(report["law_count"], 0)

    def test_cli_parser_accepts_rebuild_clean(self):
        from chinalaw.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["rebuild-clean", "--law", "民法典", "--dry-run"])

        self.assertEqual(args.command, "rebuild-clean")
        self.assertEqual(args.law, "民法典")
        self.assertTrue(args.dry_run)


if __name__ == "__main__":
    unittest.main()
