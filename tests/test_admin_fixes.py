"""Regression tests for reviewed defects in the library admin core."""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import time
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from chinalaw import fetch, loader, service
from chinalaw.admin import artifacts, backups, catalog, drafts, ingest, jobs
from chinalaw.admin.errors import LibraryError
from chinalaw.db import connect, connect_readonly, migrate


def law_payload(text: str = "原有完整正文。", **extra) -> dict:
    return {
        "id": "fix-law",
        "title": "修复测试法",
        "aliases": [],
        "level": "other",
        "status": "unknown",
        "source_url": "https://example.com/fix",
        "source_name": "synthetic-test",
        "source_checked_at": "2026-09-13T00:00:00+00:00",
        "articles": [{"number": "1", "text": text}],
        **extra,
    }


class AdminFixtureCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.db = self.base / "library.db"
        self.root = self.db.with_name(self.db.name + ".assets")
        with connect(self.db) as conn:
            migrate(conn)

    def commit(self, draft: dict) -> dict:
        return drafts.commit_draft(self.db, draft["id"], expected_fingerprint=draft["fingerprint"])


class BackupFixTests(AdminFixtureCase):
    def setUp(self) -> None:
        super().setUp()
        self.target = self.base / "target.db"
        self.target_root = self.base / "target.db.assets"
        self.stage = self.base / "restores"
        with connect(self.target) as conn:
            migrate(conn)
        self.backup = self.base / "backup.zip"

    def prepare(self):
        if not self.backup.exists():
            backups.create_backup(self.db, self.root, self.backup)
        with self.backup.open("rb") as stream:
            return backups.prepare_restore(self.target, self.stage, stream)

    def restore(self, preview):
        return backups.commit_restore(
            self.target,
            self.target_root,
            self.stage,
            preview["id"],
            expected_fingerprint=preview["fingerprint"],
        )

    def test_analyzed_library_can_be_backed_up_and_prepared(self) -> None:
        # Item 1: sqlite_stat* tables written by ANALYZE are SQLite internals.
        loader.load_fixtures(self.db)
        with connect(self.db) as conn:
            conn.execute("ANALYZE")
        with connect_readonly(self.db) as conn:
            self.assertTrue(
                conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE name LIKE 'sqlite_stat%'"
                ).fetchone()
            )
        manifest = backups.create_backup(self.db, self.root, self.backup)
        self.assertGreater(manifest["counts"]["laws"], 0)
        preview = self.prepare()
        self.assertEqual(preview["incoming"]["laws"], manifest["counts"]["laws"])

    def test_commit_removes_staging_and_stays_idempotent_without_it(self) -> None:
        # Item 2: staging folders are cleaned up; repeat confirmation still works.
        preview = self.prepare()
        folder = self.stage / preview["id"]
        self.assertTrue(folder.is_dir())
        self.assertFalse(self.restore(preview)["repeated"])
        self.assertFalse(folder.exists())
        self.assertTrue(self.restore(preview)["repeated"])

    def test_sweep_removes_expired_and_abandoned_previews_only(self) -> None:
        preview = self.prepare()
        kept = self.stage / preview["id"]
        expired = self.stage / ("e" * 32)
        expired.mkdir()
        (expired / "preview.json").write_text(
            json.dumps({**preview, "expires_at": "2000-01-01T00:00:00+00:00"}),
            encoding="utf-8",
        )
        abandoned = self.stage / ("a" * 32)
        abandoned.mkdir()
        (abandoned / "backup.zip").write_bytes(b"partial")
        old = time.time() - 3 * 3600
        for path in (abandoned, abandoned / "backup.zip"):
            os.utime(path, (old, old))
        fresh = self.stage / ("f" * 32)
        fresh.mkdir()
        (fresh / "backup.zip").write_bytes(b"uploading")
        self.assertEqual(backups.sweep_restores(self.stage), 2)
        self.assertTrue(kept.is_dir())
        self.assertTrue(fresh.is_dir())
        self.assertFalse(expired.exists())
        self.assertFalse(abandoned.exists())
        self.assertEqual(backups.sweep_restores(self.base / "missing"), 0)

    def test_prepare_sweeps_and_get_restore_reports_expiry(self) -> None:
        preview = self.prepare()
        path = self.stage / preview["id"] / "preview.json"
        stale = {**preview, "expires_at": "2000-01-01T00:00:00+00:00"}
        path.write_text(json.dumps(stale), encoding="utf-8")
        with self.assertRaises(LibraryError) as caught:
            backups.get_restore(self.stage, preview["id"])
        self.assertEqual(caught.exception.code, "restore_expired")
        self.assertEqual(caught.exception.status, 410)
        with self.assertRaises(LibraryError) as blocked:
            self.restore(preview)
        self.assertEqual(blocked.exception.code, "restore_expired")
        # The next upload sweeps the expired folder.
        self.prepare()
        self.assertFalse(path.parent.exists())

    def test_prepare_wraps_unexpected_errors_and_cleans_up(self) -> None:
        backups.create_backup(self.db, self.root, self.backup)
        with (
            patch("chinalaw.admin.backups._extract_checked", side_effect=RuntimeError("boom")),
            self.backup.open("rb") as stream,
            self.assertRaises(LibraryError) as caught,
        ):
            backups.prepare_restore(self.target, self.stage, stream)
        self.assertEqual(caught.exception.code, "backup_invalid")
        self.assertEqual([p for p in self.stage.iterdir() if p.is_dir()], [])

    def test_job_progress_between_preview_and_commit_is_not_a_conflict(self) -> None:
        # A JSON upload is parsed without writing an extracted-text artifact, so
        # the only rows that change while the job runs are job/draft bookkeeping.
        document = {
            "id": "queued-norm",
            "name": "排队中的制度",
            "source_type": "other",
            "clauses": [{"number": "1", "text": "内容。" * 20}],
        }
        upload = artifacts.save_text(
            self.target, self.target_root, "a.json", json.dumps(document, ensure_ascii=False)
        )
        job = jobs.create_job(self.target, "import", {"artifact_id": upload["id"], "kind": "norm"})
        preview = self.prepare()
        worker = jobs.JobWorker(self.target, self.target_root)
        self.assertTrue(worker.run_once())
        self.assertEqual(jobs.get_job(self.target, job["id"])["state"], "awaiting_confirmation")
        self.assertFalse(self.restore(preview)["repeated"])
        # Real content changes are still detected.
        second = self.prepare()
        with connect(self.target) as conn:
            loader.load_law_from_dict(conn, law_payload("预览后写入。"))
        with self.assertRaises(LibraryError) as caught:
            self.restore(second)
        self.assertEqual(caught.exception.code, "content_conflict")


class JobFixTests(AdminFixtureCase):
    def setUp(self) -> None:
        super().setUp()
        self.upload = artifacts.save_text(
            self.db, self.root, "synthetic.txt", "第一条 " + "核对完整正文。" * 30
        )
        self.arguments = {
            "artifact_id": self.upload["id"],
            "kind": "norm",
            "metadata": {"id": "synthetic", "name": "虚构制度", "source_type": "other"},
        }

    def test_worker_survives_transient_database_lock(self) -> None:
        # Item 3: a locked database during claim must not kill the worker thread.
        worker = jobs.JobWorker(self.db, self.root)
        original = worker._claim
        calls = {"count": 0}

        def flaky():
            calls["count"] += 1
            if calls["count"] == 1:
                raise sqlite3.OperationalError("database is locked")
            return original()

        worker._claim = flaky
        worker.start()
        try:
            time.sleep(0.2)
            job = jobs.create_job(self.db, "import", self.arguments)
            worker.notify()
            deadline = time.time() + 10
            while time.time() < deadline:
                if jobs.get_job(self.db, job["id"])["state"] == "awaiting_confirmation":
                    break
                worker.notify()
                time.sleep(0.05)
            self.assertTrue(worker.thread.is_alive())
            self.assertEqual(jobs.get_job(self.db, job["id"])["state"], "awaiting_confirmation")
            self.assertGreaterEqual(calls["count"], 2)
        finally:
            worker.stop()

    def test_failure_recording_error_is_logged_not_raised(self) -> None:
        worker = jobs.JobWorker(self.db, self.root)
        jobs.create_job(self.db, "import", self.arguments)
        with (
            patch("chinalaw.admin.ingest.import_artifact", side_effect=ValueError("bad")),
            patch.object(worker, "_fail", side_effect=sqlite3.OperationalError("locked")),
            self.assertLogs("chinalaw.admin.jobs", level="ERROR"),
        ):
            self.assertTrue(worker.run_once())

    def test_internal_failure_hides_python_details_from_message(self) -> None:
        # Item 4: raw exception text goes to detail, not to the user-facing message.
        worker = jobs.JobWorker(self.db, self.root)
        job = jobs.create_job(self.db, "import", self.arguments)
        with patch("chinalaw.admin.ingest.import_artifact", side_effect=KeyError("secret path")):
            worker.run_once()
        error = jobs.get_job(self.db, job["id"])["error"]
        self.assertEqual(error["code"], "internal_error")
        self.assertEqual(error["message"], "处理时发生内部错误，请重试或检查文件。")
        self.assertTrue(error["detail"].startswith("KeyError: "))
        self.assertIn("secret path", error["detail"])
        with patch(
            "chinalaw.admin.ingest.import_artifact",
            side_effect=LibraryError("custom", "可读原因"),
        ):
            failed = jobs.create_job(self.db, "import", self.arguments)
            worker.run_once()
        self.assertEqual(jobs.get_job(self.db, failed["id"])["error"]["code"], "custom")

    def test_source_failures_are_reported_as_source_errors(self) -> None:
        worker = jobs.JobWorker(self.db, self.root)
        cases = (
            (fetch.FetchNotFoundError("no results"), "source_not_found"),
            (fetch.FetchAmbiguousError("many", [{"id": "x"}]), "source_ambiguous"),
            (fetch.FetchSourceError("HTTP 502"), "source_unavailable"),
        )
        for exc, code in cases:
            with self.subTest(code=code):
                job = jobs.create_job(self.db, "fetch", {"query": "虚构法规"})
                with patch("chinalaw.admin.ingest.fetch_draft", side_effect=exc):
                    self.assertTrue(worker.run_once())
                result = jobs.get_job(self.db, job["id"])
                self.assertEqual(result["state"], "failed")
                self.assertEqual(result["error"]["code"], code)
                self.assertNotIn("内部错误", result["error"]["message"])
                self.assertTrue(result["error"]["detail"].startswith(type(exc).__name__))

    def test_cancel_refuses_every_finished_state(self) -> None:
        # Item 5: terminal states are never overwritten by cancel.
        for state in ("failed", "interrupted", "completed", "cancelled"):
            with self.subTest(state=state):
                job = jobs.create_job(self.db, "import", self.arguments)
                with connect(self.db) as conn:
                    conn.execute(
                        "UPDATE library_jobs SET state = ?, finished_at = ? WHERE id = ?",
                        (state, "2026-01-01T00:00:00+00:00", job["id"]),
                    )
                with self.assertRaises(LibraryError) as caught:
                    jobs.cancel_job(self.db, job["id"])
                self.assertEqual(caught.exception.code, "job_finished")
                self.assertEqual(caught.exception.status, 409)
                self.assertEqual(jobs.get_job(self.db, job["id"])["state"], state)
        queued = jobs.create_job(self.db, "import", self.arguments)
        self.assertEqual(jobs.cancel_job(self.db, queued["id"])["state"], "cancelled")
        worker = jobs.JobWorker(self.db, self.root)
        prepared = jobs.create_job(self.db, "import", self.arguments)
        worker.run_once()
        self.assertEqual(jobs.get_job(self.db, prepared["id"])["state"], "awaiting_confirmation")
        self.assertEqual(jobs.cancel_job(self.db, prepared["id"])["state"], "cancelled")
        running = jobs.create_job(self.db, "import", self.arguments)
        with connect(self.db) as conn:
            conn.execute("UPDATE library_jobs SET state = 'running' WHERE id = ?", (running["id"],))
        result = jobs.cancel_job(self.db, running["id"])
        self.assertEqual(result["state"], "running")
        self.assertTrue(result["cancel_requested"])

    def test_create_job_validates_arguments(self) -> None:
        # Item 6: missing or malformed arguments are rejected before queueing.
        cases = [
            ("import", {"kind": "norm"}),
            ("import", {"artifact_id": self.upload["id"]}),
            ("import", {"artifact_id": "", "kind": "norm"}),
            ("import", {"artifact_id": self.upload["id"], "kind": "norm", "metadata": "x"}),
            ("import", {"artifact_id": self.upload["id"], "kind": "norm", "metadata": []}),
            ("fetch", {}),
            ("fetch", {"query": 3}),
            ("fetch", {"query": "民法典", "prefer_id": 5}),
        ]
        for action, arguments in cases:
            with self.subTest(action=action, arguments=arguments):
                with self.assertRaises(LibraryError) as caught:
                    jobs.create_job(self.db, action, arguments)
                self.assertEqual(caught.exception.code, "invalid_arguments")
                self.assertEqual(caught.exception.status, 400)
        with self.assertRaises(LibraryError) as bad_kind:
            jobs.create_job(self.db, "import", {"artifact_id": self.upload["id"], "kind": "x"})
        self.assertEqual(bad_kind.exception.code, "invalid_kind")
        with self.assertRaises(LibraryError) as not_dict:
            jobs.create_job(self.db, "import", "nope")
        self.assertEqual(not_dict.exception.code, "invalid_job")
        self.assertEqual(jobs.list_jobs(self.db), [])
        self.assertEqual(jobs.create_job(self.db, "fetch", {"query": "民法典"})["state"], "queued")


class IngestFixTests(AdminFixtureCase):
    def test_same_text_file_targets_same_law_and_shows_diff(self) -> None:
        # Item 7: deterministic ids let a second import compare against the first.
        upload = artifacts.save_text(self.db, self.root, "虚构条例.txt", "第一条 初版正文。" * 10)
        metadata = {"level": "other", "status": "unknown"}
        first = ingest.import_artifact(
            self.db, self.root, upload["id"], kind="law", metadata=metadata
        )
        self.assertTrue(first["target_id"].startswith("local-law-"))
        self.assertEqual(first["diff"]["before_count"], 0)
        self.commit(first)
        again = artifacts.save_text(self.db, self.root, "虚构条例.txt", "第一条 修订后正文。" * 10)
        second = ingest.import_artifact(
            self.db, self.root, again["id"], kind="law", metadata=metadata
        )
        self.assertEqual(second["target_id"], first["target_id"])
        self.assertGreater(second["diff"]["before_count"], 0)
        self.assertEqual(second["diff"]["modified_count"], 1)
        titled = ingest.import_artifact(
            self.db,
            self.root,
            again["id"],
            kind="law",
            metadata={**metadata, "title": "另一个标题"},
        )
        self.assertNotEqual(titled["target_id"], first["target_id"])

    def test_same_norm_file_targets_same_source(self) -> None:
        upload = artifacts.save_text(self.db, self.root, "Internal Rules.txt", "第一条 初版。" * 10)
        metadata = {"source_type": "other"}
        first = ingest.import_artifact(
            self.db, self.root, upload["id"], kind="norm", metadata=metadata
        )
        self.assertEqual(first["target_id"], "internal-rules")
        self.commit(first)
        again = artifacts.save_text(self.db, self.root, "Internal Rules.txt", "第一条 修订。" * 10)
        second = ingest.import_artifact(
            self.db, self.root, again["id"], kind="norm", metadata=metadata
        )
        self.assertEqual(second["target_id"], first["target_id"])
        self.assertGreater(second["diff"]["before_count"], 0)

    def test_non_utf8_text_is_a_user_error(self) -> None:
        # Item 8: GBK uploads produce a stable, actionable error.
        body = ("第一条 " + "国标编码正文。" * 20).encode("gbk")
        upload = artifacts.save_upload(self.db, self.root, "gbk.txt", BytesIO(body))
        with self.assertRaises(LibraryError) as caught:
            ingest.import_artifact(
                self.db, self.root, upload["id"], kind="norm", metadata={"source_type": "other"}
            )
        self.assertEqual(caught.exception.code, "source_encoding")
        as_json = artifacts.save_upload(self.db, self.root, "gbk.json", BytesIO(body))
        with self.assertRaises(LibraryError) as json_caught:
            ingest.import_artifact(self.db, self.root, as_json["id"], kind="law")
        self.assertEqual(json_caught.exception.code, "source_encoding")

    def test_json_law_is_canonicalized_like_the_cli(self) -> None:
        # Item 9: uploads and `load_files` produce the same normalized document.
        payload = {
            "id": "json-canon",
            "title": "中华人民共和国虚构测试法",
            "level": "law",
            "status": "current",
            "source_url": "https://example.org/canon",
            "source_name": "synthetic",
            "source_checked_at": "2026-09-13T00:00:00+00:00",
            "articles": [{"number": "第一条", "text": "正文。"}],
        }
        upload = artifacts.save_text(self.db, self.root, "canon.json", json.dumps(payload))
        draft = ingest.import_artifact(self.db, self.root, upload["id"], kind="law")
        self.assertEqual(draft["document"]["short_title"], "虚构测试法")
        self.assertIn("虚构测试法", draft["document"]["aliases"])
        self.commit(draft)
        via_cli = self.base / "cli.db"
        path = self.base / "canon.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        loader.load_files(via_cli, [path])
        self.assertEqual(
            catalog.get_document(self.db, "law", "json-canon")["document"]["aliases"],
            catalog.get_document(via_cli, "law", "json-canon")["document"]["aliases"],
        )
        broken = artifacts.save_text(
            self.db, self.root, "broken.json", json.dumps({"id": "x", "title": "y"})
        )
        with self.assertRaises(LibraryError) as caught:
            ingest.import_artifact(self.db, self.root, broken["id"], kind="law")
        self.assertEqual(caught.exception.code, "invalid_payload")


class RevisionRestoreTests(AdminFixtureCase):
    def test_restoring_before_content_reuses_existing_revision(self) -> None:
        # Item 10: the before-snapshot carries the revision identity it came from.
        original = law_payload(
            "初版正文。",
            revision_id="explicit-v1",
            version_label="初版标签",
            revision_released_at="2020-01-01",
            revision_notes="初版备注",
        )
        self.commit(drafts.create_draft(self.db, "law", original))
        replaced = law_payload(
            "第二版正文。",
            revision_id="explicit-v2",
            version_label="第二版标签",
            revision_released_at="2021-01-01",
        )
        operation = self.commit(drafts.create_draft(self.db, "law", replaced))
        self.assertEqual(catalog.revisions(self.db, "law", "fix-law")["revision_count"], 2)
        restore = drafts.restore_operation(self.db, operation["operation_id"], side="before")
        self.assertEqual(restore["document"]["revision_id"], "explicit-v1")
        self.assertEqual(restore["document"]["version_label"], "初版标签")
        self.assertEqual(restore["document"]["revision_notes"], "初版备注")
        self.commit(restore)
        revisions = catalog.revisions(self.db, "law", "fix-law")
        self.assertEqual(revisions["revision_count"], 2)
        self.assertEqual(
            catalog.get_document(self.db, "law", "fix-law")["document"]["articles"][0]["text"],
            "初版正文。",
        )
        self.assertEqual(service.status(self.db)["revisions"], 2)

    def test_before_snapshot_without_matching_revision_is_unchanged(self) -> None:
        with connect(self.db) as conn:
            loader.load_law_from_dict(conn, law_payload("无修订记录。"))
            conn.execute("DELETE FROM revisions")
        operation = self.commit(drafts.create_draft(self.db, "law", law_payload("替换。")))
        restore = drafts.restore_operation(self.db, operation["operation_id"], side="before")
        self.assertNotIn("revision_id", restore["document"])
        self.assertEqual(restore["document"]["articles"][0]["text"], "无修订记录。")


if __name__ == "__main__":
    unittest.main()
