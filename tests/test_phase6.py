"""Phase 6 regressions for revision replay, trace semantics, and service errors."""

from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from chinalaw import audit, service
from chinalaw.cli import app
from chinalaw.db import connect, migrate
from chinalaw.loader import load_law_from_dict


def _law_payload(
    *,
    law_id: str = "phase6-law",
    title: str = "中华人民共和国阶段六测试法",
    status: str = "current",
    released_at: str,
    effective_at: str,
    article_text: str,
    repealed_at: str | None = None,
) -> dict:
    return {
        "id": law_id,
        "title": title,
        "short_title": title.removeprefix("中华人民共和国"),
        "aliases": [],
        "level": "law",
        "status": status,
        "issuing_body": "测试机关",
        "released_at": released_at,
        "effective_at": effective_at,
        "repealed_at": repealed_at,
        "source_url": f"https://example.test/{law_id}/{released_at}",
        "source_name": "example.test",
        "source_checked_at": "2026-08-06T00:00:00+00:00",
        "articles": [
            {
                "number": "1",
                "number_display": "第一条",
                "text": article_text,
                "position": 1,
            }
        ],
    }


class RevisionReplayTests(unittest.TestCase):
    def test_current_hash_revision_without_snapshot_replays_current_articles(self) -> None:
        payload = _law_payload(
            released_at="2024-01-01",
            effective_at="2024-02-01",
            article_text="当前版本正文。",
        )
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "t.db"
            with connect(db_path) as conn:
                migrate(conn)
                load_law_from_dict(conn, payload)
                conn.execute("UPDATE revisions SET snapshot_json = NULL")

            law = service.get_law_as_of(db_path, "阶段六测试法", "2025-01-01")
            article = service.get_article_as_of(
                db_path, "阶段六测试法", "1", "2025-01-01"
            )
            articles = service.get_articles(
                db_path, "阶段六测试法", "1", as_of="2025-01-01"
            )

        self.assertEqual(law["article_count"], 1)
        self.assertEqual(law["articles"][0]["text"], "当前版本正文。")
        self.assertEqual(
            law["warnings"][0]["code"],
            "revision_snapshot_missing_fallback_current",
        )
        self.assertEqual(article["article"]["text"], "当前版本正文。")
        self.assertEqual(articles["found_count"], 1)

    def test_corrupt_historical_snapshot_returns_structured_diagnostics(self) -> None:
        old = _law_payload(
            released_at="2020-01-01",
            effective_at="2020-02-01",
            article_text="旧版本正文。",
            status="amended",
        )
        new = _law_payload(
            released_at="2024-01-01",
            effective_at="2024-02-01",
            article_text="新版本正文。",
        )
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "t.db"
            with connect(db_path) as conn:
                migrate(conn)
                load_law_from_dict(conn, old)
                load_law_from_dict(conn, new)
                conn.execute(
                    "UPDATE revisions SET snapshot_json = '{broken' WHERE released_at = ?",
                    ("2020-01-01",),
                )

            law = service.get_law_as_of(db_path, "阶段六测试法", "2021-01-01")
            article = service.get_article_as_of(
                db_path, "阶段六测试法", "1", "2021-01-01"
            )
            articles = service.get_articles(
                db_path, "阶段六测试法", "1", as_of="2021-01-01"
            )
            diff = service.diff_law_as_of(
                db_path,
                "阶段六测试法",
                "2021-01-01",
                "2025-01-01",
            )

        for result in (law, article, articles, diff):
            self.assertEqual(result["error"], "revision_snapshot_corrupt")
        self.assertEqual(diff["error_side"], "from")
        self.assertIn("revision_id", law["revision_diagnostic"])


class TraceSemanticsTests(unittest.TestCase):
    def test_low_confidence_same_number_is_amended_not_deleted(self) -> None:
        old = _law_payload(
            law_id="same-number-law",
            title="中华人民共和国同号修正法",
            released_at="2020-01-01",
            effective_at="2020-02-01",
            article_text="旧法规定甲方应当提交全部纸质材料并经三级审批。",
            status="amended",
        )
        new = _law_payload(
            law_id="same-number-law",
            title="中华人民共和国同号修正法",
            released_at="2024-01-01",
            effective_at="2024-02-01",
            article_text="新法规定监管机关可以直接采取数字化风险处置措施。",
        )
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "t.db"
            with connect(db_path) as conn:
                migrate(conn)
                load_law_from_dict(conn, old)
                load_law_from_dict(conn, new)

            traced = service.trace_article_as_of(
                db_path,
                "同号修正法",
                "1",
                from_as_of="2021-01-01",
                to_as_of="2025-01-01",
            )

        self.assertFalse(traced["ok"])
        self.assertEqual(traced["status"], "amended")
        self.assertEqual(traced["to"]["article"]["number"], "1")
        self.assertEqual(traced["warning"], "low_confidence")
        self.assertTrue(any("不能据此判定删除" in item for item in traced["evidence"]))


class AuditTimeEffectTests(unittest.TestCase):
    def test_audit_flags_as_of_on_or_after_repeal_date(self) -> None:
        payload = _law_payload(
            law_id="repealed-test-law",
            title="中华人民共和国测试合同法",
            status="repealed",
            released_at="1999-03-15",
            effective_at="1999-10-01",
            repealed_at="2021-01-01",
            article_text="依法成立的合同受法律保护。",
        )
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "t.db"
            with connect(db_path) as conn:
                migrate(conn)
                load_law_from_dict(conn, payload)

            before = audit.audit_text(
                db_path,
                "依据《测试合同法》第一条。",
                as_of="2020-12-31",
            )
            after = audit.audit_text(
                db_path,
                "依据《测试合同法》第一条。",
                as_of="2021-01-01",
            )

        before_codes = [item["code"] for item in before["citations"][0]["issues"]]
        after_codes = [item["code"] for item in after["citations"][0]["issues"]]
        self.assertNotIn("repealed_before_as_of", before_codes)
        self.assertIn("repealed_before_as_of", after_codes)
        self.assertFalse(after["ok"])


class ServiceErrorContractTests(unittest.TestCase):
    def test_articles_errors_are_distinct(self) -> None:
        payload = _law_payload(
            released_at="2024-01-01",
            effective_at="2024-02-01",
            article_text="正文。",
        )
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "t.db"
            with connect(db_path) as conn:
                migrate(conn)
                load_law_from_dict(conn, payload)

            empty = service.get_articles(db_path, "阶段六测试法", " , ")
            invalid_date = service.get_articles(
                db_path, "阶段六测试法", "1", as_of="2024/01/01"
            )
            missing_law = service.get_articles(db_path, "不存在的法规", "1")
            batch = service.get_articles_batch(
                db_path,
                "阶段六测试法:1;不存在的法规:1",
                as_of="2024/01/01",
            )

        self.assertEqual(empty["error"], "empty_numbers")
        self.assertEqual(invalid_date["error"], "invalid_as_of")
        self.assertEqual(missing_law["error"], "law_not_found")
        self.assertEqual(batch["error"], "invalid_as_of")
        self.assertEqual(
            [section["error"] for section in batch["sections"]],
            ["invalid_as_of", "invalid_as_of"],
        )

    def test_get_and_diff_invalid_dates_are_not_law_not_found(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "t.db"
            get_result = service.get_law_as_of(db_path, "任意法", "bad-date")
            diff_result = service.diff_law_as_of(
                db_path, "任意法", "bad-date", "2025-01-01"
            )

        self.assertEqual(get_result["error"], "invalid_as_of")
        self.assertEqual(diff_result["error"], "invalid_as_of")
        self.assertEqual(diff_result["error_side"], "from")

    def test_cli_get_and_articles_emit_structured_invalid_as_of(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "t.db"
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                get_code = app(
                    [
                        "get",
                        "任意法",
                        "--as-of",
                        "bad-date",
                        "--format",
                        "json",
                        "--db",
                        str(db_path),
                    ]
                )
            get_payload = json.loads(output.getvalue())

            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                articles_code = app(
                    [
                        "articles",
                        "任意法",
                        "1",
                        "--as-of",
                        "bad-date",
                        "--format",
                        "json",
                        "--db",
                        str(db_path),
                    ]
                )
            articles_payload = json.loads(output.getvalue())

        self.assertEqual(get_code, 1)
        self.assertEqual(articles_code, 1)
        self.assertEqual(get_payload["error"], "invalid_as_of")
        self.assertEqual(articles_payload["error"], "invalid_as_of")


class CitationGrammarTests(unittest.TestCase):
    def test_range_and_inserted_article_are_distinct(self) -> None:
        range_citations = audit.extract_citations("依据《公司法》第186-187条处理。")
        inserted = audit.extract_citations("依据《示例条例》第十四条之一处理。")

        self.assertEqual([item["number"] for item in range_citations], ["186", "187"])
        self.assertTrue(all(item["range_input"] == "第186-187条" for item in range_citations))
        self.assertEqual([item["number"] for item in inserted], ["14-1"])
        self.assertNotIn("range_input", inserted[0])

    def test_short_citation_requires_unicode_word_boundary(self) -> None:
        self.assertEqual(audit.extract_citations("全民§4不是短引用。"), [])
        citation = audit.extract_citations("民§4是短引用。")
        self.assertEqual(citation[0]["law_input"], "民法典")
        self.assertEqual(citation[0]["number"], "4")


class ImportBoundaryTests(unittest.TestCase):
    def test_trace_module_can_be_imported_before_service(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        env = os.environ.copy()
        env["PYTHONPATH"] = str(repo / "src")
        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                "from chinalaw.trace import trace_article_as_of; print(trace_article_as_of.__name__)",
            ],
            cwd=repo,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout.strip(), "trace_article_as_of")
