"""Public artifacts are freshly built and must not contain runtime/private data."""
from __future__ import annotations

import json
import runpy
import tempfile
import unittest
from pathlib import Path

from chinalaw.db import connect, migrate

BUILDER = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts/build-public-data"))


def law():
    return {
        "id": "synthetic", "title": "公开发布测试法", "level": "law", "status": "current", "aliases": [],
        "source_url": "https://flk.npc.gov.cn/detail?id=synthetic", "source_name": "test",
        "source_checked_at": "2026-09-29T00:00:00+08:00", "source_hash": "synthetic-hash",
        "articles": [{"number": "1", "position": 1, "text": "这是一条虚构的公开发布测试正文。"}],
    }


class PublicReleaseTests(unittest.TestCase):
    def test_rejects_unknown_fields_and_nonofficial_input(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "flk-synthetic.json"
            data = law()
            data["private_notes"] = "must not ship"
            path.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "non-public fields"):
                BUILDER["reviewed_payload"](path)
            data = law()
            data["source_url"] = "https://unapproved.example/private"
            path.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "official reviewed"):
                BUILDER["reviewed_payload"](path)

    def test_build_is_public_only_and_does_not_overwrite(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "input"
            source.mkdir()
            (source / "flk-synthetic.json").write_text(json.dumps(law()), encoding="utf-8")
            output = root / "output"
            result = BUILDER["build"](source, output, "2026-09-29")
            self.assertEqual(result["counts"]["articles"], 1)
            self.assertEqual(len(result["archives"]), 2)
            with connect(output / "chinalaw-public-2026-09-29-sqlite/library.db") as conn:
                sql = conn.execute("SELECT sql FROM sqlite_master WHERE name='articles_fts'").fetchone()[0]
                self.assertNotIn("contentless_delete", sql)
                self.assertEqual(BUILDER["public_database_check"](conn)["laws"], 1)
            with self.assertRaisesRegex(ValueError, "never overwritten"):
                BUILDER["build"](source, output, "2026-09-29")

    def test_build_includes_reviewed_rules_and_rejects_missing_ids(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, rules = root / "input", root / "rules"
            source.mkdir()
            rules.mkdir()
            (source / "flk-synthetic.json").write_text(json.dumps(law()), encoding="utf-8")
            payload = {"rules": [{"id": "rule", "topic": "测试", "primary_law_id": "legacy",
                                   "rule_text": "仅为线索", "confidence": "source_reviewed_guidance"}],
                       "law_id_map": {"legacy": "synthetic"}}
            (rules / "rules.json").write_text(json.dumps(payload), encoding="utf-8")
            output = root / "output"
            result = BUILDER["build"](source, output, "2026-09-30", applicability_dir=rules)
            self.assertEqual(result["counts"]["applicability_rules"], 1)
            self.assertTrue((output / "chinalaw-public-2026-09-30-json/applicability/rules.json").is_file())
            payload["law_id_map"] = {}
            (rules / "rules.json").write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Missing public applicability reference"):
                BUILDER["build"](source, root / "missing", "2026-09-30", applicability_dir=rules)

    def test_check_rejects_runtime_rows(self):
        with tempfile.TemporaryDirectory() as temporary, connect(Path(temporary) / "db") as conn:
            migrate(conn)
            conn.execute("INSERT INTO norm_packs (id, name) VALUES ('private', 'Synthetic private')")
            with self.assertRaisesRegex(ValueError, "Private/runtime"):
                BUILDER["public_database_check"](conn)
