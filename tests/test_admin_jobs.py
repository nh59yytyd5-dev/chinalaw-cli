from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from chinalaw import normsources
from chinalaw.admin import artifacts, catalog, drafts, jobs
from chinalaw.admin.errors import LibraryError
from chinalaw.db import connect, migrate


class JobTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.db = Path(self.temporary.name) / "library.db"
        self.root = self.db.with_name(self.db.name + ".assets")
        with connect(self.db) as conn:
            migrate(conn)
        self.upload = artifacts.save_text(
            self.db, self.root, "synthetic.txt", "第一章 总则\n第一条 " + "核对完整正文。" * 60
        )
        self.arguments = {
            "artifact_id": self.upload["id"],
            "kind": "norm",
            "metadata": {"id": "synthetic", "name": "虚构测试制度", "source_type": "other"},
        }
        self.worker = jobs.JobWorker(self.db, self.root)

    def test_import_only_prepares_full_preview_then_confirmation_completes_job(self):
        job = jobs.create_job(self.db, "import", self.arguments)
        self.assertTrue(self.worker.run_once())
        result = jobs.get_job(self.db, job["id"])
        self.assertEqual(result["state"], "awaiting_confirmation")
        draft = drafts.get_draft(self.db, result["draft_id"])
        self.assertGreater(len(draft["document"]["clauses"][0]["text"]), 120)
        self.assertEqual(catalog.list_documents(self.db, kind="norm")["total"], 0)
        drafts.commit_draft(self.db, draft["id"], expected_fingerprint=draft["fingerprint"])
        self.assertEqual(jobs.get_job(self.db, job["id"])["state"], "completed")
        self.assertEqual(
            catalog.get_document(self.db, "norm", "synthetic")["document"]["clauses"][0]["part"],
            "第一章 总则",
        )

    def test_cancelled_prepared_job_cannot_commit_and_retry_is_separate(self):
        job = jobs.create_job(self.db, "import", self.arguments)
        self.worker.run_once()
        result = jobs.cancel_job(self.db, job["id"])
        draft = drafts.get_draft(self.db, result["draft_id"])
        with self.assertRaises(LibraryError):
            drafts.commit_draft(self.db, draft["id"], expected_fingerprint=draft["fingerprint"])
        retried = jobs.retry_job(self.db, job["id"])
        self.assertNotEqual(retried["id"], job["id"])
        self.assertEqual(retried["parent_id"], job["id"])

    def test_worker_failure_keeps_library_empty_and_records_reason(self):
        job = jobs.create_job(self.db, "import", self.arguments)
        with patch("chinalaw.admin.ingest.import_artifact", side_effect=ValueError("bad document")):
            self.worker.run_once()
        result = jobs.get_job(self.db, job["id"])
        self.assertEqual(result["state"], "failed")
        self.assertIn("bad document", result["error"]["message"])
        self.assertEqual(catalog.list_documents(self.db, kind="norm")["total"], 0)

    def test_restart_interrupts_unfinished_jobs_and_duplicate_worker_rejected(self):
        job = jobs.create_job(self.db, "import", self.arguments)
        self.worker.start()
        try:
            self.assertEqual(jobs.get_job(self.db, job["id"])["state"], "interrupted")
            with self.assertRaisesRegex(LibraryError, "已有维护服务"):
                jobs.JobWorker(self.db, self.root).start()
        finally:
            self.worker.stop()

    def test_restore_gate_defers_worker_without_losing_queued_job(self):
        job = jobs.create_job(self.db, "import", self.arguments)
        with self.worker.gate.exclusive():
            self.assertFalse(self.worker.run_once())
        self.assertEqual(jobs.get_job(self.db, job["id"])["state"], "queued")
        self.assertTrue(self.worker.run_once())

    def test_changed_original_is_detected_at_confirmation(self):
        job = jobs.create_job(self.db, "import", self.arguments)
        self.worker.run_once()
        draft = drafts.get_draft(self.db, jobs.get_job(self.db, job["id"])["draft_id"])
        path = artifacts.get_artifact(self.db, self.root, self.upload["id"])[1]
        path.write_bytes(b"changed")
        with self.assertRaises(LibraryError):
            drafts.commit_draft(self.db, draft["id"], expected_fingerprint=draft["fingerprint"])
        self.assertEqual(catalog.list_documents(self.db, kind="norm")["total"], 0)

    def test_cli_update_does_not_reuse_uploaded_source_for_different_content(self):
        job = jobs.create_job(self.db, "import", self.arguments)
        self.worker.run_once()
        draft = drafts.get_draft(self.db, jobs.get_job(self.db, job["id"])["draft_id"])
        drafts.commit_draft(self.db, draft["id"], expected_fingerprint=draft["fingerprint"])
        document = catalog.get_document(self.db, "norm", "synthetic")
        self.assertIsNotNone(document["latest_operation"])
        payload = document["document"]
        payload["clauses"][0]["text"] += "模拟 CLI 更新后的内容。"
        with connect(self.db) as conn:
            normsources.import_source_from_dict(conn, payload)
        updated = catalog.get_document(self.db, "norm", "synthetic")
        self.assertIsNone(updated["latest_operation"])
