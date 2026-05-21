"""Tests for local-only article commentary bundles."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from chinalaw import commentary, loader
from chinalaw.cli import app

FIXTURES = Path(__file__).resolve().parents[1] / "data" / "fixtures"


class CommentaryTests(unittest.TestCase):
    def _db(self, td: str) -> Path:
        db_path = Path(td) / "commentary.db"
        loader.load_fixtures(db_path, FIXTURES)
        return db_path

    def test_import_and_query_article_commentary(self) -> None:
        payload = {
            "book": {
                "id": "book-civil-code-study",
                "title": "民法典条文释义测试书",
                "author": "测试作者",
                "source_name": "local-law-data",
                "license_scope": "local_only",
            },
            "items": [
                {
                    "law_id": "flk-civil-code-2020",
                    "law_title": "中华人民共和国民法典",
                    "article_number": "第一百四十三条",
                    "page_start": 12,
                    "page_end": 13,
                    "summary": "民事法律行为有效要件。",
                    "excerpt": "主体适格、意思表示真实、不违反强制性规定。",
                    "ocr_confidence": 0.98,
                    "boundary_confidence": 0.95,
                    "qa_status": "checked",
                }
            ],
        }
        with tempfile.TemporaryDirectory() as td:
            db_path = self._db(td)
            bundle = Path(td) / "commentary.json"
            bundle.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

            imported = commentary.import_bundle_file(db_path, bundle)
            found = commentary.get_article_commentary(db_path, "民法典", "143")

        self.assertEqual(imported["items_loaded"], 1)
        self.assertTrue(found["found"])
        self.assertEqual(found["commentary_count"], 1)
        self.assertEqual(found["commentaries"][0]["qa_status"], "checked")
        self.assertEqual(found["commentaries"][0]["book"]["license_scope"], "local_only")

    def test_commentary_cli_article_returns_not_found_for_missing_article(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db_path = self._db(td)
            code = app(
                [
                    "--db",
                    str(db_path),
                    "commentary",
                    "article",
                    "不存在法",
                    "1",
                    "--format",
                    "json",
                ]
            )

        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
