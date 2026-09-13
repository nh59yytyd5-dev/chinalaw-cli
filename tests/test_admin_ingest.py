"""Canonical/public uploads preserve text, structure and explicit associations."""

from __future__ import annotations

import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch
from xml.sax.saxutils import escape

from chinalaw.admin import artifacts, catalog, drafts, ingest
from chinalaw.admin.errors import LibraryError
from chinalaw.db import connect, migrate


class IngestTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.db = Path(self.temporary.name) / "library.db"
        self.root = self.db.with_name(self.db.name + ".assets")
        with connect(self.db) as conn:
            migrate(conn)

    def commit(self, draft):
        drafts.commit_draft(self.db, draft["id"], expected_fingerprint=draft["fingerprint"])
        return catalog.get_document(self.db, draft["document_kind"], draft["target_id"])["document"]

    def test_public_text_and_docx_preserve_chapters_and_full_text(self):
        long_text = "公开法规的虚构完整测试内容。" * 30
        paragraphs = ["第一章 测试章节", "第一条 " + long_text, "第二条 结尾正文。"]
        docx = io.BytesIO()
        with zipfile.ZipFile(docx, "w") as archive:
            archive.writestr(
                "word/document.xml",
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
                + "".join(
                    "<w:p><w:r><w:t>" + escape(line) + "</w:t></w:r></w:p>" for line in paragraphs
                )
                + "</w:body></w:document>",
            )
        for filename, content in (
            ("test.md", "\n".join(paragraphs).encode()),
            ("test.docx", docx.getvalue()),
        ):
            with self.subTest(filename=filename):
                upload = artifacts.save_upload(self.db, self.root, filename, io.BytesIO(content))
                draft = ingest.import_artifact(
                    self.db,
                    self.root,
                    upload["id"],
                    kind="law",
                    metadata={
                        "id": filename,
                        "title": "虚构公开规范",
                        "level": "other",
                        "status": "unknown",
                    },
                )
                self.assertEqual(draft["document"]["articles"][0]["text"], long_text)
                actual = self.commit(draft)
                self.assertEqual(actual["articles"][0]["part"], "第一章 测试章节")
                self.assertEqual(actual["articles"][0]["text"], long_text)

    def test_canonical_json_preserves_provided_categories_and_retains_existing_on_reimport(self):
        payload = {
            "id": "json-law",
            "title": "虚构 JSON 规范",
            "level": "other",
            "status": "unknown",
            "source_url": "https://example.org/test",
            "articles": [{"number": "1", "text": "完整正文。"}],
            "categories": [
                {"id": "child", "name": "虚构分类子项", "parent_id": "parent"},
                {"id": "parent", "name": "虚构分类父项"},
            ],
            "category_ids": ["child"],
        }
        upload = artifacts.save_text(self.db, self.root, "law.json", json.dumps(payload))
        draft = ingest.import_artifact(self.db, self.root, upload["id"], kind="law")
        self.assertEqual(draft["document"]["category_ids"], ["child"])
        actual = self.commit(draft)
        self.assertEqual(actual["category_ids"], ["child"])
        self.assertEqual([item["id"] for item in actual["categories"]], ["parent", "child"])
        del payload["categories"], payload["category_ids"]
        payload["articles"][0]["text"] = "重新导入正文。"
        actual = self.commit(drafts.create_draft(self.db, "law", payload))
        self.assertEqual(actual["category_ids"], ["child"])

    def test_shared_category_change_is_explicit_conflict(self):
        payload = {
            "id": "first",
            "title": "虚构规范",
            "level": "other",
            "status": "unknown",
            "source_url": "https://example.org/test",
            "articles": [{"number": "1", "text": "正文。"}],
            "categories": [{"id": "existing", "name": "原有分类"}],
            "category_ids": ["existing"],
        }
        self.commit(drafts.create_draft(self.db, "law", payload))
        payload["categories"][0]["name"] = "不同的分类定义"
        with self.assertRaisesRegex(LibraryError, "现有目录不同"):
            drafts.create_draft(self.db, "law", payload)
        self.assertEqual(
            catalog.get_document(self.db, "law", "first")["document"]["categories"][0]["name"],
            "原有分类",
        )

    def test_source_fetch_freezes_once_and_disables_model_alias_enrichment(self):
        payload = {
            "id": "fetch-test",
            "title": "来源测试",
            "level": "other",
            "status": "unknown",
            "source_url": "https://example.org/source",
            "articles": [{"number": "1", "text": "取得时的完整正文。"}],
        }
        with patch(
            "chinalaw.admin.ingest.fetch.fetch_law",
            return_value={"law": payload, "matched_id": "source-id"},
        ) as fetch:
            draft = ingest.fetch_draft(self.db, "来源测试", prefer_id="source-id")
            payload["articles"][0]["text"] = "后来改变的来源。"
            self.assertEqual(self.commit(draft)["articles"][0]["text"], "取得时的完整正文。")
        self.assertEqual(fetch.call_count, 1)
        self.assertFalse(fetch.call_args.kwargs["enrich_aliases"])
        self.assertTrue(fetch.call_args.kwargs["dry_run"])

    def test_canonical_revision_identity_and_label_are_reviewed_and_saved(self):
        payload = {
            "id": "versioned-law",
            "title": "虚构版本测试",
            "level": "other",
            "status": "unknown",
            "source_url": "https://example.org/test",
            "revision_id": "explicit-version",
            "version_label": "明确提供的版本标签",
            "revision_released_at": "2020-01-01",
            "revision_notes": "虚构测试版本备注",
            "articles": [{"number": "1", "text": "版本正文。"}],
        }
        draft = drafts.create_draft(self.db, "law", payload)
        self.assertIn("version_label", [item["field"] for item in draft["diff"]["metadata"]])
        self.commit(draft)
        revision = catalog.revisions(self.db, "law", payload["id"])["revisions"][0]
        self.assertEqual(revision["id"], "explicit-version")
        self.assertEqual(revision["version_label"], "明确提供的版本标签")
        self.assertEqual(revision["notes"], "虚构测试版本备注")
        restored = drafts.restore_revision(self.db, "law", payload["id"], revision["id"])
        self.assertEqual(restored["document"]["revision_id"], "explicit-version")
        self.assertEqual(restored["document"]["version_label"], "明确提供的版本标签")
        self.commit(restored)
        self.assertEqual(catalog.revisions(self.db, "law", payload["id"])["revision_count"], 1)
        self.assertIsNotNone(
            catalog.get_document(self.db, "law", payload["id"])["latest_operation"]
        )
