"""Portable round trips, archive attacks and failure/concurrency guarantees."""

from __future__ import annotations

import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from chinalaw import loader, service
from chinalaw.admin import artifacts, backups, catalog, drafts, ingest, jobs
from chinalaw.admin.errors import LibraryError
from chinalaw.db import connect, connect_readonly, migrate


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.db = self.base / "source.db"
        self.root = self.base / "source.db.assets"
        self.target = self.base / "target.db"
        self.target_root = self.base / "target.db.assets"
        self.stage = self.base / "restores"
        for path in (self.db, self.target):
            with connect(path) as conn:
                migrate(conn)
        upload = artifacts.save_text(
            self.db, self.root, "测试制度.txt", "第一条 " + "完整内容。" * 50
        )
        self.artifact = upload
        draft = ingest.import_artifact(
            self.db,
            self.root,
            upload["id"],
            kind="norm",
            metadata={
                "id": "synthetic-norm",
                "name": "测试规范",
                "source_type": "other",
            },
        )
        drafts.commit_draft(self.db, draft["id"], expected_fingerprint=draft["fingerprint"])
        self.backup = self.base / "backup.zip"

    def prepare(self):
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

    def test_round_trip_preserves_full_text_versions_artifacts_and_inode(self):
        before = self.target.stat().st_ino
        preview = self.prepare()
        self.assertEqual(service.status(self.target)["norm_sources"], 0)
        result = self.restore(preview)
        self.assertFalse(result["repeated"])
        self.assertEqual(self.target.stat().st_ino, before)
        self.assertEqual(
            catalog.get_document(self.db, "norm", "synthetic-norm")["document"],
            catalog.get_document(self.target, "norm", "synthetic-norm")["document"],
        )
        self.assertEqual(
            catalog.revisions(self.target, "norm", "synthetic-norm")["revision_count"], 1
        )
        self.assertEqual(
            artifacts.get_artifact(self.target, self.target_root, self.artifact["id"])[
                1
            ].read_bytes(),
            artifacts.get_artifact(self.db, self.root, self.artifact["id"])[1].read_bytes(),
        )
        self.assertTrue(
            service.search(self.target, "完整内容", include_norm=True)["norm_clause_hits"]
        )
        self.assertTrue(self.restore(preview)["repeated"])
        with zipfile.ZipFile(self.backup) as archive:
            self.assertNotIn("auth.db", " ".join(archive.namelist()))
            manifest = json.loads(archive.read("manifest.json"))
            self.assertEqual(manifest["format_version"], 1)

    def test_changed_destination_rejects_stale_preview(self):
        preview = self.prepare()
        with connect(self.target) as conn:
            loader.load_law_from_dict(
                conn,
                {
                    "id": "changed",
                    "title": "新资料",
                    "level": "other",
                    "status": "unknown",
                    "source_url": "https://example.org/test",
                    "articles": [{"number": "1", "text": "预览后新入库。"}],
                },
            )
        with self.assertRaisesRegex(LibraryError, "预览后已变化"):
            self.restore(preview)
        self.assertIsNotNone(service.get_law(self.target, "changed"))
        self.assertEqual(service.status(self.target)["norm_sources"], 0)

    def test_failed_index_rebuild_rolls_back_all_rows(self):
        preview = self.prepare()
        before = backups.library_fingerprint(self.target)
        with (
            patch(
                "chinalaw.admin.backups.rebuild_search_indexes", side_effect=OSError("disk full")
            ),
            self.assertRaises(OSError),
        ):
            self.restore(preview)
        self.assertEqual(backups.library_fingerprint(self.target), before)
        self.assertEqual(service.status(self.target)["norm_sources"], 0)

    def test_queued_work_is_interrupted_in_restored_library(self):
        job = jobs.create_job(
            self.db, "import", {"artifact_id": self.artifact["id"], "kind": "norm"}
        )
        preview = self.prepare()
        self.restore(preview)
        self.assertEqual(jobs.get_job(self.target, job["id"])["state"], "interrupted")

    def test_missing_source_artifact_aborts_backup(self):
        artifacts.get_artifact(self.db, self.root, self.artifact["id"])[1].unlink()
        with self.assertRaisesRegex(LibraryError, "原件缺失"):
            backups.create_backup(self.db, self.root, self.backup)
        self.assertFalse(self.backup.exists())

    def test_archive_corruption_path_traversal_and_extra_files_rejected(self):
        backups.create_backup(self.db, self.root, self.backup)
        with zipfile.ZipFile(self.backup) as archive:
            entries = {name: archive.read(name) for name in archive.namelist()}
        variations = [
            {**entries, "../escape": b"no"},
            {**entries, "auth.db": b"no"},
            {**entries, "library.sqlite3": b"corrupt"},
            {**entries, "manifest.json": json.dumps({"format_version": 99}).encode()},
        ]
        for entry_map in variations:
            with self.subTest(files=list(entry_map)):
                data = io.BytesIO()
                with zipfile.ZipFile(data, "w") as archive:
                    for name, body in entry_map.items():
                        archive.writestr(name, body)
                data.seek(0)
                with self.assertRaises(LibraryError):
                    backups.prepare_restore(self.target, self.stage, data)
        self.assertEqual(service.status(self.target)["norm_sources"], 0)
        self.assertFalse((self.base / "escape").exists())

    def test_old_reader_sees_consistent_snapshot_during_restore(self):
        preview = self.prepare()
        with connect_readonly(self.target) as reader:
            reader.execute("BEGIN")
            self.assertEqual(reader.execute("SELECT COUNT(*) FROM norm_sources").fetchone()[0], 0)
            self.restore(preview)
            self.assertEqual(reader.execute("SELECT COUNT(*) FROM norm_sources").fetchone()[0], 0)
        self.assertEqual(service.status(self.target)["norm_sources"], 1)
