"""Management must add human access without weakening read/write boundaries."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from chinalaw import loader, normsources, service
from chinalaw.admin import catalog, payloads
from chinalaw.admin.errors import LibraryError
from chinalaw.db import connect, connect_readonly, migrate, read_only_operation, set_meta
from chinalaw.schema import SCHEMA_V13_SQL


def example_law(identifier: str = "test-law", text: str = "公开测试正文。") -> dict:
    return {
        "id": identifier,
        "title": "测试资料 " + identifier,
        "short_title": None,
        "aliases": [],
        "level": "other",
        "status": "unknown",
        "source_url": "https://example.com/source",
        "source_name": "synthetic-test",
        "source_checked_at": "2026-09-13T00:00:00+00:00",
        "articles": [{"number": "1", "text": text, "part": "第一章 核对样例"}],
    }


class AdminFoundationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / "library.db"
        with connect(self.db) as conn:
            migrate(conn)
            loader.load_law_from_dict(conn, example_law())
            normsources.import_source_from_dict(
                conn,
                {
                    "id": "private-example",
                    "name": "私域测试资料",
                    "source_type": "other",
                    "authority": "测试制定者",
                    "binding_scope": "虚构测试",
                    "clauses": [{"number": "1", "text": "私域独有检索词。"}],
                },
            )

    def test_read_context_rejects_writes_and_restores_context(self) -> None:
        with read_only_operation():
            self.assertIsNotNone(service.get_law(self.db, "test-law"))
            with self.assertRaises(Exception) as caught, connect(self.db) as conn:
                set_meta(conn, "should_not_exist", "x")
            self.assertIn("readonly", str(caught.exception).lower())
        with connect(self.db) as conn:
            set_meta(conn, "normal_writer", "allowed")

    def test_read_context_never_creates_missing_database(self) -> None:
        missing = self.db.parent / "missing.db"
        with read_only_operation(), self.assertRaises(FileNotFoundError):
            service.list_laws(missing)
        self.assertFalse(missing.exists())

    def test_read_context_never_migrates_old_database(self) -> None:
        old = self.db.parent / "old.db"
        with connect(old) as conn:
            conn.executescript(SCHEMA_V13_SQL)
            set_meta(conn, "schema_version", "13")
        before = old.read_bytes()
        with read_only_operation(), self.assertRaises(ValueError):
            service.list_laws(old)
        self.assertEqual(before, old.read_bytes())
        self.assertEqual(service.status(old)["schema_version"], 13)

    def test_inventory_pages_do_not_mix_public_and_private(self) -> None:
        with connect(self.db) as conn:
            for number in range(3):
                loader.load_law_from_dict(conn, example_law(f"example-{number}"))
        first = catalog.list_documents(self.db, page=1, page_size=2)
        second = catalog.list_documents(self.db, page=2, page_size=2)
        self.assertEqual(first["total"], 4)
        self.assertEqual(second["total"], 4)
        self.assertTrue(
            {x["id"] for x in first["items"]}.isdisjoint({x["id"] for x in second["items"]})
        )
        self.assertEqual(catalog.list_documents(self.db, kind="norm")["total"], 1)
        self.assertNotIn("clauses", first["items"][0])

    def test_search_scope_covers_mixed_results(self) -> None:
        public = catalog.search_library(self.db, "私域独有检索词")
        self.assertNotIn("私域独有检索词。", json.dumps(public, ensure_ascii=False))
        private = catalog.search_library(self.db, "私域独有检索词", include_private=True)
        self.assertIn("私域独有检索词。", json.dumps(private, ensure_ascii=False))
        with self.assertRaises(LibraryError):
            catalog.search_library(self.db, "独有", kind="norm")

    def test_full_document_and_fingerprint_match_normalized_input(self) -> None:
        doc = catalog.get_document(self.db, "law", "test-law")
        prepared = payloads.prepare_payload("law", example_law())
        self.assertEqual(doc["document"]["articles"][0]["text"], "公开测试正文。")
        self.assertEqual(doc["fingerprint"], payloads.content_fingerprint(prepared, "law"))

    def test_private_preparation_preserves_full_text_and_never_writes_library(self) -> None:
        long_text = "完整核对文本。" * 50
        before = service.status(self.db)
        prepared = payloads.prepare_payload(
            "norm",
            {
                "id": "new-private",
                "name": "虚构导入测试",
                "source_type": "other",
                "clauses": [{"number": "1", "text": long_text}],
            },
        )
        self.assertEqual(prepared["clauses"][0]["text"], long_text)
        self.assertEqual(service.status(self.db), before)
        with connect_readonly(self.db) as conn:
            self.assertIsNone(payloads.current_payload(conn, "norm", "new-private"))

    def test_literal_wildcards_do_not_match_every_document(self) -> None:
        self.assertEqual(catalog.list_documents(self.db, query="%")["total"], 0)
        with self.assertRaises(LibraryError):
            catalog.list_documents(self.db, page_size=100_000)


if __name__ == "__main__":
    unittest.main()
