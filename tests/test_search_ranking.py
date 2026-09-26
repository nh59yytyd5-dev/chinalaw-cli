"""Exact search on the bigram index: same hits as a substring scan, better order."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from chinalaw import loader, service
from chinalaw.db import connect, migrate
from chinalaw.search_tokens import index_tokens, is_exact_phrase, match_expression


def _law(law_id: str, texts: list[str], **extra) -> dict:
    effective_at = extra.pop("effective_at", "2020-01-01")
    payload = {
        "id": law_id,
        "title": extra.pop("title", f"测试法{law_id}"),
        "level": extra.pop("level", "law"),
        "status": extra.pop("status", "current"),
        "issuing_body": extra.pop("issuing_body", "全国人民代表大会"),
        "released_at": effective_at,
        "effective_at": effective_at,
        "source_url": f"https://example.com/{law_id}",
        "source_name": "synthetic-test",
        "articles": [{"number": str(index), "text": text} for index, text in enumerate(texts, 1)],
    }
    payload.update(extra)
    return payload


class TokenTests(unittest.TestCase):
    def test_bigrams_digits_and_single_characters(self) -> None:
        self.assertEqual(index_tokens("超越权限，处30日"), "超越 越权 权限 处 30 日")
        self.assertEqual(index_tokens("ＷＴＯ规则"), "wto 规则")

    def test_query_phrases(self) -> None:
        self.assertEqual(match_expression(["超越权限"]), '"超越 越权 权限"')
        # A lone Han character next to a digit is left to the substring check.
        self.assertEqual(match_expression(["第30条"]), '"30" *')
        # A leading number may sit inside a longer one: 5000元 in 15000元.
        self.assertIsNone(match_expression(["5000元"]))
        self.assertEqual(match_expression(["5000元以下"]), '"元以 以下"')
        self.assertIsNone(match_expression(["罚"]))
        self.assertTrue(is_exact_phrase("担保"))
        self.assertFalse(is_exact_phrase("30日"))
        self.assertFalse(is_exact_phrase("罚"))


class SearchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "library.db"
        with connect(self.db) as conn:
            migrate(conn)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def load(self, *payloads: dict) -> None:
        with connect(self.db) as conn:
            for payload in payloads:
                loader.load_law_from_dict(conn, payload)

    def hits(self, query: str, **options) -> list[tuple[str, str]]:
        result = service.search(self.db, query, kind="article", limit=50, **options)
        return [(hit["law_id"], hit["number"]) for hit in result["article_hits"]]

    def test_hits_equal_a_substring_scan(self) -> None:
        self.load(
            _law(
                "a",
                [
                    "行为人超越权限订立合同。",
                    "超越，权限不得混同。",
                    "处第30条规定的罚款。",
                    "并处15000元以下罚款。",
                    "并处5000元以下罚款。",
                    "当事人有过错的，承担责任。",
                ],
            )
        )
        self.assertEqual(self.hits("超越权限"), [("a", "1")])
        self.assertEqual(self.hits("第30条"), [("a", "3")])
        self.assertEqual(self.hits("过错"), [("a", "6")])
        self.assertEqual(sorted(self.hits("罚款")), [("a", "3"), ("a", "4"), ("a", "5")])
        # Same as a substring scan: 5000元 is also found inside 15000元.
        self.assertEqual(sorted(self.hits("5000元")), [("a", "4"), ("a", "5")])
        # Single characters still work, through a scan.
        self.assertEqual(sorted(self.hits("处")), [("a", "3"), ("a", "4"), ("a", "5")])

    def test_segments_may_match_the_law_title(self) -> None:
        self.load(_law("c", ["正当防卫，不负刑事责任。"], title="中华人民共和国刑法"))
        self.assertEqual(self.hits("刑法 正当防卫"), [("c", "1")])

    def test_versions_fold_to_the_one_in_force(self) -> None:
        common = {"title": "测试公司法", "work_id": "work-company"}
        self.load(
            _law(
                "old",
                ["公司可以为他人提供担保。"],
                effective_at="2014-03-01",
                status="amended",
                **common,
            ),
            _law("new", ["公司为他人提供担保，应当决议。"], effective_at="2024-07-01", **common),
        )
        result = service.search(self.db, "担保", kind="article")
        (hit,) = result["article_hits"]
        self.assertEqual(hit["law_id"], "new")
        self.assertEqual(hit["other_versions"], 1)
        self.assertEqual(hit["effective_status_as_of"], "current")
        self.assertEqual(hit["match_mode"], "exact")

        self.assertEqual(self.hits("担保", as_of="2018-01-01"), [("old", "1")])
        self.assertEqual(sorted(self.hits("担保", versions="all")), [("new", "1"), ("old", "1")])
        self.assertEqual(self.hits("担保", status="amended"), [("old", "1")])

    def test_order_puts_in_force_and_primary_levels_first(self) -> None:
        self.load(
            _law(
                "local",
                ["经济补偿按月计算。"],
                level="local_regulation",
                issuing_body="上海市人民代表大会常务委员会",
                title="上海市劳动条例",
            ),
            _law("gone", ["经济补偿另行规定。"], status="repealed", repealed_at="2021-01-01"),
            _law("main", ["用人单位应当支付经济补偿。"]),
        )
        self.assertEqual(self.hits("经济补偿"), [("main", "1"), ("local", "1"), ("gone", "1")])
        self.assertEqual(self.hits("经济补偿", level="local_regulation"), [("local", "1")])
        self.assertEqual(self.hits("经济补偿", status="current")[:1], [("main", "1")])
        self.assertNotIn(("local", "1"), self.hits("经济补偿", region="北京市"))
        self.assertIn(("local", "1"), self.hits("经济补偿", region="上海市"))

    def test_citation_query_returns_the_article_first(self) -> None:
        self.load(
            _law(
                "civil",
                ["第一条正文。", "第二条正文。", "越权订立的合同，对法人发生效力。"],
                title="中华人民共和国民法典",
                short_title="民法典",
            )
        )
        for query in ("民法典第三条", "民法典 3 条", "民法典第3条第一款"):
            with self.subTest(query=query):
                result = service.search(self.db, query)
                first = result["article_hits"][0]
                self.assertEqual((first["law_id"], first["number"]), ("civil", "3"))
                self.assertEqual(first["match_mode"], "citation")
                self.assertTrue(result["retrieval"]["citation"])

    def test_invalid_options_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            service.search(self.db, "担保", status="valid")
        with self.assertRaises(ValueError):
            service.search(self.db, "担保", as_of="2020/01/01")
        with self.assertRaises(ValueError):
            service.search(self.db, "担保", versions="some")

    def test_title_or_level_change_reindexes_articles(self) -> None:
        payload = _law("x", ["条文内容。"], title="旧名称法")
        self.load(payload)
        self.assertEqual(self.hits("旧名称 内容"), [("x", "1")])
        with connect(self.db) as conn:
            loader.refresh_law_metadata(conn, {**payload, "title": "新名称法"})
        self.assertEqual(self.hits("旧名称 内容"), [])
        self.assertEqual(self.hits("新名称 内容"), [("x", "1")])


class OldSqliteTests(unittest.TestCase):
    def test_index_without_contentless_delete_behaves_the_same(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.object(sqlite3, "sqlite_version_info", (3, 37, 2)),
        ):
            db = Path(tmp) / "old.db"
            with connect(db) as conn:
                migrate(conn)
                ddl = conn.execute(
                    "SELECT sql FROM sqlite_master WHERE name = 'articles_fts'"
                ).fetchone()[0]
                self.assertNotIn("contentless_delete", ddl)
                payload = _law("x", ["公司提供担保。"])
                loader.load_law_from_dict(conn, payload)
                loader.load_law_from_dict(
                    conn, {**payload, "articles": [{"number": "1", "text": "公司解散。"}]}
                )
            self.assertEqual(service.search(db, "担保", kind="article")["article_hits"], [])
            self.assertEqual(len(service.search(db, "解散", kind="article")["article_hits"]), 1)


class ArticleNumberTests(unittest.TestCase):
    def test_inserted_article_short_forms(self) -> None:
        for raw in ("133之一", "第133条之一", "第一百三十三条之一", "133-1"):
            with self.subTest(raw=raw):
                self.assertEqual(service.normalize_article_number(raw), "133-1")


if __name__ == "__main__":
    unittest.main()
