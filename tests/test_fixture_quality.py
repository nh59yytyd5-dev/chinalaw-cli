from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHECK_SCRIPT = ROOT / "scripts" / "check-public-fixtures"


class PublicFixtureGateTests(unittest.TestCase):
    def _run_gate(self, fixtures_dir: Path, manifest_path: Path) -> subprocess.CompletedProcess:
        return subprocess.run(
            [
                sys.executable,
                str(CHECK_SCRIPT),
                "--fixtures-dir",
                str(fixtures_dir),
                "--manifest",
                str(manifest_path),
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_gate_fails_when_a_declared_complete_fixture_loses_an_article(self) -> None:
        payload = {
            "id": "fixture-gate-law",
            "title": "公开质量门禁测试法",
            "short_title": "门禁测试法",
            "aliases": ["门禁测试法"],
            "level": "law",
            "status": "current",
            "issuing_body": "测试机关",
            "document_number": None,
            "released_at": "2026-01-01",
            "effective_at": "2026-01-01",
            "repealed_at": None,
            "source_url": "https://example.test/fixture-gate",
            "source_name": "example.test",
            "source_checked_at": "2026-08-06T00:00:00+00:00",
            "source_hash": "fixture-gate-hash",
            "articles": [
                {
                    "number": str(number),
                    "number_display": f"第{number}条",
                    "text": f"第{number}条正文。",
                    "part": None,
                    "position": number,
                }
                for number in range(1, 4)
            ],
        }
        manifest = {
            "schema_version": 1,
            "fixtures": {
                "gate.json": {
                    "expected_article_count": 3,
                    "integer_sequence": {"first": 1, "last": 3},
                }
            },
        }

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixtures_dir = root / "fixtures"
            fixtures_dir.mkdir()
            fixture_path = fixtures_dir / "gate.json"
            manifest_path = root / "manifest.json"
            fixture_path.write_text(
                json.dumps(payload, ensure_ascii=False),
                encoding="utf-8",
            )
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False),
                encoding="utf-8",
            )

            passed = self._run_gate(fixtures_dir, manifest_path)
            self.assertEqual(passed.returncode, 0, passed.stderr)

            payload["articles"].pop(1)
            payload["articles"][1]["position"] = 2
            fixture_path.write_text(
                json.dumps(payload, ensure_ascii=False),
                encoding="utf-8",
            )
            failed = self._run_gate(fixtures_dir, manifest_path)

        self.assertEqual(failed.returncode, 1)
        self.assertIn("expected 3 articles, got 2", failed.stderr)
        self.assertIn("missing integer articles [2]", failed.stderr)

    def test_gate_rejects_judicial_interpretation_without_document_number(self) -> None:
        payload = {
            "id": "fixture-gate-interpretation",
            "title": "最高人民法院关于公开质量门禁的解释",
            "short_title": "质量门禁解释",
            "aliases": ["质量门禁解释"],
            "level": "judicial_interpretation",
            "status": "current",
            "issuing_body": "最高人民法院",
            "document_number": None,
            "released_at": "2026-01-01",
            "effective_at": "2026-01-01",
            "repealed_at": None,
            "source_url": "https://example.test/fixture-gate-interpretation",
            "source_name": "example.test",
            "source_checked_at": "2026-08-06T00:00:00+00:00",
            "source_hash": "fixture-gate-interpretation-hash",
            "articles": [
                {
                    "number": "1",
                    "number_display": "第一条",
                    "text": "第一条正文。",
                    "part": None,
                    "position": 1,
                }
            ],
        }
        manifest = {
            "schema_version": 1,
            "fixtures": {
                "interpretation.json": {
                    "expected_article_count": 1,
                    "integer_sequence": {"first": 1, "last": 1},
                }
            },
        }

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixtures_dir = root / "fixtures"
            fixtures_dir.mkdir()
            fixture_path = fixtures_dir / "interpretation.json"
            manifest_path = root / "manifest.json"
            fixture_path.write_text(
                json.dumps(payload, ensure_ascii=False),
                encoding="utf-8",
            )
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False),
                encoding="utf-8",
            )
            failed = self._run_gate(fixtures_dir, manifest_path)

        self.assertEqual(failed.returncode, 1)
        self.assertIn("missing document_number", failed.stderr)


class BundledFixtureMetadataTests(unittest.TestCase):
    def test_criminal_law_general_provisions_keep_book_and_chapter_hierarchy(self) -> None:
        paths = sorted((ROOT / "data" / "fixtures").glob("criminal_law*.json"))
        self.assertEqual(16, len(paths))

        for path in paths:
            payload = json.loads(path.read_text(encoding="utf-8"))
            general_last = 89 if path.name == "criminal_law_1979.json" else 101
            for article in payload["articles"]:
                main_number = str(article["number"]).split("-", 1)[0]
                if main_number.isdigit() and int(main_number) <= general_last:
                    with self.subTest(path=path.name, article=article["number"]):
                        self.assertTrue(
                            article["part"].startswith("第一编 总则 "),
                            article["part"],
                        )

    def test_arbitration_fixture_id_matches_2025_revision_and_keeps_legacy_alias(self) -> None:
        fixture = json.loads(
            (ROOT / "data" / "fixtures" / "arbitration_law.json").read_text(
                encoding="utf-8"
            )
        )
        corpus = json.loads(
            (ROOT / "data" / "recommended_corpus.json").read_text(encoding="utf-8")
        )

        self.assertEqual("flk-arbitration-law-2025", fixture["id"])
        self.assertEqual("2025-09-12", fixture["released_at"])
        self.assertEqual("2026-03-01", fixture["effective_at"])
        self.assertIn("flk-arbitration-law-1994-2017", fixture["aliases"])
        serialized_corpus = json.dumps(corpus, ensure_ascii=False)
        self.assertIn('"fixture_id": "flk-arbitration-law-2025"', serialized_corpus)
        self.assertNotIn('"fixture_id": "flk-arbitration-law-1994-2017"', serialized_corpus)
        self.assertNotIn("现有 fixture 已含全文（1 条）", serialized_corpus)


if __name__ == "__main__":
    unittest.main()
