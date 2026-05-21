from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from chinalaw import corpus, service
from chinalaw import ensure as ensure_mod
from chinalaw.cli import app, build_parser
from chinalaw.loader import FIXTURES_DIR


class RecommendedCorpusTests(unittest.TestCase):
    def test_builtin_corpus_lists_expected_profiles(self) -> None:
        payload = corpus.list_profiles()
        names = {item["name"] for item in payload["profiles"]}

        self.assertIn("baseline", names)
        self.assertIn("general", names)
        self.assertIn("criminal", names)
        self.assertIn("securities", names)

        labor = next(item for item in payload["profiles"] if item["name"] == "labor")
        self.assertGreater(labor["entry_count"], labor["installable_count"])
        self.assertGreaterEqual(labor["unsupported_count"], 1)

    def test_profile_alias_resolves_with_dependencies(self) -> None:
        payload = corpus.resolve_profiles(["real_estate"])

        self.assertEqual(payload["requested_profiles"], ["real_estate"])
        self.assertEqual(payload["included_profiles"], ["baseline", "general", "real-estate"])
        self.assertGreater(payload["entry_count"], 20)

    def test_unknown_profile_is_a_corpus_error(self) -> None:
        with self.assertRaises(corpus.CorpusError):
            corpus.resolve_profiles(["missing-profile"])

    def test_invalid_fetch_status_is_a_corpus_error(self) -> None:
        payload = {
            "profiles": {
                "demo": {
                    "entries": [
                        {
                            "id": "demo-law",
                            "title": "示例法",
                            "primary_source": "flk_npc",
                            "fetch_status": "historical",
                        }
                    ]
                }
            }
        }

        with self.assertRaises(corpus.CorpusError) as ctx:
            corpus._validate_corpus(payload)
        self.assertIn("invalid fetch_status", str(ctx.exception))

    def test_non_supported_entries_are_explicitly_non_installable(self) -> None:
        payload = corpus.load_corpus()
        for profile_name, profile in payload["profiles"].items():
            for entry in profile["entries"]:
                if entry.get("source_status", "supported") != "supported":
                    self.assertFalse(
                        entry.get("installable", True),
                        f"{profile_name}:{entry.get('id')} must not be installable",
                    )
                    self.assertTrue(
                        entry.get("skip_reason") or entry.get("notes"),
                        f"{profile_name}:{entry.get('id')} needs a skip reason or notes",
                    )

    def test_baseline_completed_fixture_ids_exist_and_are_populated(self) -> None:
        payload = corpus.load_corpus()
        fixture_payloads = {}
        for path in FIXTURES_DIR.glob("*.json"):
            data = json.loads(path.read_text(encoding="utf-8"))
            fixture_payloads[data.get("id")] = data

        for entry in payload["profiles"]["baseline"]["entries"]:
            if entry.get("installable", True) is False:
                continue
            fixture_id = entry.get("fixture_id")
            self.assertIsNotNone(fixture_id, entry["id"])
            fixture = fixture_payloads.get(fixture_id)
            self.assertIsNotNone(fixture, entry["id"])
            self.assertNotEqual(fixture.get("status"), "seed", entry["id"])
            self.assertGreater(len(fixture.get("articles") or []), 0, entry["id"])

    def test_cli_corpus_show_outputs_json(self) -> None:
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = app(["--no-notice", "corpus", "show", "baseline", "--format", "json"])

        self.assertEqual(code, 0)
        payload = json.loads(buffer.getvalue())
        self.assertEqual(payload["kind"], "recommended_corpus_profile")
        self.assertEqual(payload["included_profiles"], ["baseline"])

    def test_cli_parser_accepts_profile_ensure_options(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["ensure", "--profile", "criminal", "--no-profile-deps"])

        self.assertEqual(args.command, "ensure")
        self.assertEqual(args.profile, ["criminal"])
        self.assertTrue(args.no_profile_deps)

    def test_profile_ensure_fetches_by_entry_source_and_skips_unsupported(self) -> None:
        resolved = {
            "requested_profiles": ["demo"],
            "included_profiles": ["demo"],
            "entries": [
                {
                    "id": "demo-law",
                    "title": "示例法",
                    "short_title": "示例法",
                    "primary_source": "flk_npc",
                    "source_status": "supported",
                    "profile": "demo",
                    "priority": "P2",
                },
                {
                    "id": "demo-rule",
                    "title": "示例规章",
                    "short_title": "示例规章",
                    "primary_source": "gjgzk",
                    "source_status": "unsupported",
                    "profile": "demo",
                    "priority": "P2",
                },
            ],
        }

        def fake_fetch(db_path, name, *, source, prefer_bbbs=None, limit=5, status=None):
            self.assertEqual(name, "示例法")
            self.assertEqual(source, "flk_npc")
            self.assertIsNone(status)
            return {
                "article_count": 1,
                "loaded": True,
                "matched_id": "law-demo",
                "matched_title": "示例法",
                "law": {
                    "id": "law-demo",
                    "title": "示例法",
                    "short_title": "示例法",
                    "status": "current",
                    "source_name": "flk.npc.gov.cn",
                    "source_url": "https://example.test/law",
                    "source_checked_at": "2026-05-20T00:00:00+00:00",
                    "article_count": 1,
                    "articles_coverage": "full",
                },
            }

        with (
            tempfile.TemporaryDirectory() as td,
            patch("chinalaw.ensure.corpus.resolve_profiles", return_value=resolved),
            patch("chinalaw.ensure.service.get_law", return_value=None),
            patch("chinalaw.ensure.fetch_mod.fetch_law", side_effect=fake_fetch) as mocked,
        ):
            report = ensure_mod.ensure_corpus_profiles(Path(td) / "t.db", ["demo"], interval=0)

        self.assertTrue(report["ok"])
        self.assertEqual(mocked.call_count, 1)
        self.assertEqual(report["fetched_count"], 1)
        self.assertEqual(report["skipped_count"], 1)
        self.assertEqual(report["failed_count"], 0)
        self.assertEqual(report["items"][1]["reason"], "unsupported_source")

    def test_profile_ensure_forwards_entry_fetch_status(self) -> None:
        resolved = {
            "requested_profiles": ["demo"],
            "included_profiles": ["demo"],
            "entries": [
                {
                    "id": "demo-repealed-law",
                    "title": "中华人民共和国示例旧法",
                    "primary_source": "flk_npc",
                    "source_status": "supported",
                    "fetch_status": "repealed",
                    "profile": "demo",
                    "priority": "P2",
                },
            ],
        }

        def fake_fetch(db_path, name, *, source, prefer_bbbs=None, limit=5, status=None):
            self.assertEqual(name, "中华人民共和国示例旧法")
            self.assertEqual(source, "flk_npc")
            self.assertEqual(status, "repealed")
            return {
                "article_count": 1,
                "loaded": True,
                "matched_id": "law-old",
                "matched_title": name,
                "law": {
                    "id": "law-old",
                    "title": name,
                    "status": "repealed",
                    "source_name": "flk.npc.gov.cn",
                    "article_count": 1,
                    "articles_coverage": "populated",
                },
            }

        with (
            tempfile.TemporaryDirectory() as td,
            patch("chinalaw.ensure.corpus.resolve_profiles", return_value=resolved),
            patch("chinalaw.ensure.service.get_law", return_value=None),
            patch("chinalaw.ensure.fetch_mod.fetch_law", side_effect=fake_fetch) as mocked,
        ):
            report = ensure_mod.ensure_corpus_profiles(Path(td) / "t.db", ["demo"], interval=0)

        self.assertTrue(report["ok"])
        self.assertEqual(mocked.call_count, 1)
        self.assertEqual(report["items"][0]["fetch_status"], "repealed")

    def test_profile_ensure_stops_same_source_after_rate_limit(self) -> None:
        resolved = {
            "requested_profiles": ["demo"],
            "included_profiles": ["demo"],
            "entries": [
                {
                    "id": "demo-law-1",
                    "title": "示例甲法",
                    "primary_source": "flk_npc",
                    "source_status": "supported",
                    "profile": "demo",
                    "priority": "P2",
                },
                {
                    "id": "demo-law-2",
                    "title": "示例乙法",
                    "primary_source": "flk_npc",
                    "source_status": "supported",
                    "profile": "demo",
                    "priority": "P2",
                },
            ],
        }

        with (
            tempfile.TemporaryDirectory() as td,
            patch("chinalaw.ensure.corpus.resolve_profiles", return_value=resolved),
            patch("chinalaw.ensure.service.get_law", return_value=None),
            patch(
                "chinalaw.ensure.fetch_mod.fetch_law",
                side_effect=ensure_mod.fetch_mod.FetchSourceError(
                    "FLK returned anti-bot JavaScript challenge; status=200"
                ),
            ) as mocked,
        ):
            report = ensure_mod.ensure_corpus_profiles(Path(td) / "t.db", ["demo"], interval=0)

        self.assertFalse(report["ok"])
        self.assertEqual(mocked.call_count, 1)
        self.assertEqual(report["failed_count"], 1)
        self.assertEqual(report["skipped_count"], 1)
        self.assertEqual(report["rate_limited_count"], 1)
        self.assertEqual(report["blocked_sources"], ["flk_npc"])
        self.assertEqual(report["items"][0]["reason"], "source_rate_limited")
        self.assertEqual(report["items"][1]["status"], "skipped")
        self.assertEqual(report["items"][1]["reason"], "source_rate_limited")

    def test_profile_ensure_loads_builtin_fixture_before_fetching(self) -> None:
        with tempfile.TemporaryDirectory() as td, patch(
            "chinalaw.ensure.fetch_mod.fetch_law"
        ) as mocked_fetch:
            db_path = Path(td) / "profile.db"
            report = ensure_mod.ensure_corpus_profiles(
                db_path,
                ["baseline"],
                interval=0,
            )

            self.assertTrue(report["ok"])
            self.assertEqual(report["fetch_attempt_count"], 0)
            self.assertEqual(report["fixture_loaded_count"], 8)
            self.assertEqual(report["skipped_count"], 0)
            mocked_fetch.assert_not_called()

            constitution = service.get_law(db_path, "宪法")
            self.assertIsNotNone(constitution)
            self.assertEqual(constitution["status"], "current")
            self.assertEqual(constitution["article_count"], 144)
            constitution_history = service.history(db_path, "宪法")
            self.assertIsNotNone(constitution_history)
            self.assertGreaterEqual(constitution_history["revision_count"], 11)
            current_preamble = service.get_article(db_path, "宪法", "序言")
            historical_preamble = service.get_article_as_of(
                db_path, "宪法", "序言", "1954-09-20"
            )
            self.assertIsNotNone(current_preamble)
            self.assertIsNotNone(historical_preamble)
            self.assertIn("中国特色社会主义", current_preamble["article"]["text"])
            self.assertIn("中國人民", historical_preamble["article"]["text"])
            self.assertEqual(
                historical_preamble["law"]["selected_revision"]["effective_at"],
                "1954-09-20",
            )

            criminal_law = service.get_law(db_path, "刑法")
            self.assertIsNotNone(criminal_law)
            self.assertEqual(criminal_law["status"], "current")
            self.assertEqual(criminal_law["article_count"], 505)
            history = service.history(db_path, "刑法")
            self.assertIsNotNone(history)
            self.assertGreaterEqual(history["revision_count"], 16)
            current_article = service.get_article_as_of(db_path, "刑法", "12", "2024-03-01")
            prior_article = service.get_article_as_of(db_path, "刑法", "12", "2024-02-29")
            self.assertIsNotNone(current_article)
            self.assertIsNotNone(prior_article)
            self.assertEqual(
                current_article["law"]["selected_revision"]["effective_at"],
                "2024-03-01",
            )
            self.assertEqual(
                prior_article["law"]["selected_revision"]["effective_at"],
                "2021-03-01",
            )
            current_165 = service.get_article_as_of(
                db_path, "刑法", "165", "2024-03-01"
            )
            prior_165 = service.get_article_as_of(
                db_path, "刑法", "165", "2024-02-29"
            )
            self.assertIsNotNone(current_165)
            self.assertIsNotNone(prior_165)
            self.assertIn("董事、监事、高级管理人员", current_165["article"]["text"])
            self.assertIn("其他公司、企业", current_165["article"]["text"])
            self.assertIn("董事、经理", prior_165["article"]["text"])
            self.assertNotIn("其他公司、企业", prior_165["article"]["text"])

            criminal_procedure = service.get_law(db_path, "刑诉法")
            self.assertIsNotNone(criminal_procedure)
            self.assertEqual(criminal_procedure["articles_coverage"], "populated")

    def test_contracts_profile_core_entries_are_fixture_backed(self) -> None:
        """民商合同基础包应离线加载，不应在 smoke 路径批量打官方源。"""

        with tempfile.TemporaryDirectory() as td, patch(
            "chinalaw.ensure.fetch_mod.fetch_law"
        ) as mocked_fetch:
            db_path = Path(td) / "contracts.db"
            report = ensure_mod.ensure_corpus_profiles(
                db_path,
                ["contracts"],
                include_dependencies=False,
                interval=0,
            )

            self.assertTrue(report["ok"], report["items"])
            self.assertEqual(report["fetch_attempt_count"], 0)
            self.assertEqual(report["fixture_loaded_count"], 7)
            mocked_fetch.assert_not_called()

            lending = service.get_law(db_path, "民间借贷规定")
            sale = service.get_law(db_path, "买卖合同解释")
            insurance = service.get_law(db_path, "保险法")
            self.assertIsNotNone(lending)
            self.assertIsNotNone(sale)
            self.assertIsNotNone(insurance)
            self.assertGreaterEqual(lending["article_count"], 30)
            self.assertGreaterEqual(sale["article_count"], 30)
            self.assertGreaterEqual(insurance["article_count"], 180)

    def test_ensure_profile_rejects_mixed_name_mode(self) -> None:
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = app(
                [
                    "--no-notice",
                    "ensure",
                    "--profile",
                    "baseline",
                    "民法典",
                    "--format",
                    "json",
                ]
            )

        self.assertEqual(code, 2)
        payload = json.loads(buffer.getvalue())
        self.assertEqual(payload["kind"], "law_ensure_error")


if __name__ == "__main__":
    unittest.main()
