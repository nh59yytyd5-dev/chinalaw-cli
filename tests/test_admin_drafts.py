"""A human confirmation must apply once, to exactly the reviewed content."""

from __future__ import annotations

import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from chinalaw import loader, normsources, service
from chinalaw.admin import catalog, drafts, payloads
from chinalaw.admin.errors import LibraryError
from chinalaw.db import connect, connect_readonly, migrate


def law(text: str = "原有完整正文。") -> dict:
    return {
        "id": "draft-law",
        "title": "公开资料测试样例",
        "aliases": [],
        "level": "other",
        "status": "unknown",
        "source_url": "https://example.com/synthetic",
        "source_name": "synthetic-test",
        "source_checked_at": "2026-09-13T00:00:00+00:00",
        "articles": [{"number": "1", "text": text, "part": "第一章 测试"}],
    }


class DraftTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / "library.db"
        with connect(self.db) as conn:
            migrate(conn)
            loader.load_law_from_dict(conn, law())

    def commit(self, draft: dict) -> dict:
        return drafts.commit_draft(self.db, draft["id"], expected_fingerprint=draft["fingerprint"])

    def test_full_preview_then_commit_exact_content(self) -> None:
        text = "应能完整预览的测试内容。" * 60
        before = catalog.get_document(self.db, "law", "draft-law")
        draft = drafts.create_draft(self.db, "law", law(text))
        self.assertEqual(draft["document"]["articles"][0]["text"], text)
        self.assertEqual(draft["diff"]["modified_count"], 1)
        self.assertEqual(catalog.get_document(self.db, "law", "draft-law"), before)
        result = self.commit(draft)
        after = catalog.get_document(self.db, "law", "draft-law")
        self.assertEqual(after["fingerprint"], draft["fingerprint"])
        self.assertEqual(after["document"]["articles"][0]["text"], text)
        self.assertEqual(after["latest_operation"]["id"], result["operation_id"])
        self.assertEqual(len(drafts.list_operations(self.db, "law", "draft-law")), 1)

    def test_concurrent_confirmations_are_idempotent(self) -> None:
        draft = drafts.create_draft(self.db, "law", law("更新内容。"))
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.commit(draft), range(2)))
        self.assertEqual(results[0]["operation_id"], results[1]["operation_id"])
        self.assertEqual(sorted(x["repeated"] for x in results), [False, True])
        self.assertEqual(len(drafts.list_operations(self.db, "law", "draft-law")), 1)

    def test_external_writer_causes_conflict_and_is_not_overwritten(self) -> None:
        draft = drafts.create_draft(self.db, "law", law("拟入库的内容。"))
        with connect(self.db) as conn:
            loader.load_law_from_dict(conn, law("另一个 CLI 写入的内容。"))
        self.assertTrue(drafts.get_draft(self.db, draft["id"])["conflict"])
        with self.assertRaises(LibraryError) as caught:
            self.commit(draft)
        self.assertEqual(caught.exception.code, "content_conflict")
        self.assertEqual(
            catalog.get_document(self.db, "law", "draft-law")["document"]["articles"][0]["text"],
            "另一个 CLI 写入的内容。",
        )

    def test_failure_after_loader_writes_rolls_back_content_and_indexes(self) -> None:
        draft = drafts.create_draft(self.db, "law", law("不应入库的独有词。"))
        original = loader.load_law_from_dict

        def failing(conn, payload):
            original(conn, payload)
            raise OSError("synthetic disk failure")

        with (
            patch.object(loader, "load_law_from_dict", side_effect=failing),
            self.assertRaises(OSError),
        ):
            self.commit(draft)
        self.assertEqual(
            catalog.get_document(self.db, "law", "draft-law")["document"]["articles"][0]["text"],
            "原有完整正文。",
        )
        self.assertFalse(service.search(self.db, "不应入库的独有词")["article_hits"])
        self.assertEqual(drafts.list_operations(self.db, "law", "draft-law"), [])
        self.assertEqual(drafts.get_draft(self.db, draft["id"])["status"], "ready")

    def test_normalization_mismatch_rolls_back(self) -> None:
        draft = drafts.create_draft(self.db, "law", law("确认的正文。"))
        original = loader.load_law_from_dict

        def different(conn, payload):
            payload["articles"][0]["text"] = "意外变化的正文。"
            return original(conn, payload)

        with (
            patch.object(loader, "load_law_from_dict", side_effect=different),
            self.assertRaises(LibraryError) as caught,
        ):
            self.commit(draft)
        self.assertEqual(caught.exception.code, "commit_mismatch")
        self.assertEqual(
            catalog.get_document(self.db, "law", "draft-law")["document"]["articles"][0]["text"],
            "原有完整正文。",
        )

    def test_expired_cancelled_or_wrong_confirmation_cannot_commit(self) -> None:
        draft = drafts.create_draft(self.db, "law", law("替换内容。"))
        with self.assertRaises(LibraryError):
            drafts.commit_draft(self.db, draft["id"], expected_fingerprint="wrong")
        with connect(self.db) as conn:
            conn.execute("UPDATE library_drafts SET expires_at = '2000-01-01T00:00:00+00:00'")
        with self.assertRaises(LibraryError) as expired:
            self.commit(draft)
        self.assertEqual(expired.exception.code, "draft_expired")
        drafts.cancel_draft(self.db, draft["id"])
        self.assertEqual(drafts.get_draft(self.db, draft["id"])["status"], "cancelled")

    def test_review_is_bound_to_exact_content(self) -> None:
        before = catalog.get_document(self.db, "law", "draft-law")
        drafts.mark_reviewed(
            self.db,
            "law",
            "draft-law",
            expected_fingerprint=before["fingerprint"],
            note="已对照来源",
        )
        self.assertIsNotNone(catalog.get_document(self.db, "law", "draft-law")["review"])
        draft = drafts.create_draft(self.db, "law", law("版本已更新。"))
        self.commit(draft)
        self.assertIsNone(catalog.get_document(self.db, "law", "draft-law")["review"])
        with self.assertRaises(LibraryError):
            drafts.mark_reviewed(
                self.db, "law", "draft-law", expected_fingerprint=before["fingerprint"]
            )

    def test_restore_is_a_new_reviewable_draft(self) -> None:
        change = drafts.create_draft(self.db, "law", law("新的正文。"))
        operation = self.commit(change)
        restore = drafts.restore_operation(self.db, operation["operation_id"], side="before")
        self.assertEqual(restore["document"]["articles"][0]["text"], "原有完整正文。")
        self.assertEqual(
            catalog.get_document(self.db, "law", "draft-law")["document"]["articles"][0]["text"],
            "新的正文。",
        )
        self.commit(restore)
        self.assertEqual(
            drafts.list_operations(self.db, "law", "draft-law")[0]["action"], "restore"
        )

    def test_private_import_preserves_structure_and_history(self) -> None:
        payload = {
            "id": "private-example",
            "name": "私域导入测试",
            "source_type": "other",
            "authority": "测试制定者",
            "binding_scope": "虚构范围",
            "clauses": [{"number": "1", "part": "第一章 样例", "text": "私域长文本。" * 50}],
        }
        draft = drafts.create_draft(self.db, "norm", payload)
        self.commit(draft)
        after = catalog.get_document(self.db, "norm", "private-example")
        self.assertEqual(after["fingerprint"], draft["fingerprint"])
        self.assertEqual(after["document"]["clauses"][0]["part"], "第一章 样例")
        self.assertEqual(
            normsources.list_revisions(self.db, "private-example")["revision_count"], 1
        )
        self.commit(draft)
        self.assertEqual(
            normsources.list_revisions(self.db, "private-example")["revision_count"], 1
        )

    def test_repeated_private_numbering_is_not_lost_in_diff(self) -> None:
        payload = {
            "id": "repeated",
            "name": "重复编号测试",
            "source_type": "other",
            "clauses": [{"number": "1", "text": "第一段。"}, {"number": "1", "text": "第二段。"}],
        }
        draft = drafts.create_draft(self.db, "norm", payload)
        self.assertEqual(draft["diff"]["added_count"], 2)
        self.commit(draft)
        with connect_readonly(self.db) as conn:
            stored = payloads.current_payload(conn, "norm", "repeated")
        self.assertEqual(len(stored["clauses"]), 2)


if __name__ == "__main__":
    unittest.main()
