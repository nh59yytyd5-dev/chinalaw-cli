"""Recovery suggestions must preserve scope and never masquerade as search hits."""

import tempfile
import unittest
from pathlib import Path

from chinalaw import loader, service
from chinalaw.db import connect, migrate


def numbered_document(db, law_id, title, number, **extra):
    with connect(db) as conn:
        loader.load_law_from_dict(conn, {
            "id": law_id, "title": title, "level": "other", "status": "unknown",
            "document_number": number,
            "source_url": "https://example.com/test", "source_name": "synthetic-test",
            "articles": [{"number": "1", "text": "仅测试元数据匹配，不在正文重复文号。"}],
            **extra,
        })


class SearchGuidanceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.library = Path(temporary.name) / "library.db"
        with connect(self.library) as conn:
            migrate(conn)
            loader.load_law_from_dict(conn, {
                "id": "test-law", "title": "测试公开文件", "level": "other", "status": "unknown",
                "source_url": "https://example.com/test", "source_name": "synthetic-test",
                "articles": [{"number": "1", "text": "证人出庭作证。"}],
            })

    def test_zero_hit_suggestions_keep_all_filters_and_do_not_add_hits(self):
        result = service.search(
            self.library, "证人出庭 不存在的词", kind="article", in_laws=["test-law"],
            as_of="2020-01-01", level="other", status="unknown", region="上海市", versions="all",
        )
        self.assertEqual(result["counts"]["total"], 0)
        steps = result["guidance"]["next_steps"]
        self.assertTrue(steps)
        for step in steps:
            args = step["arguments"]
            self.assertEqual(args["in_laws"], ["test-law"])
            self.assertEqual(args["as_of"], "2020-01-01")
            self.assertEqual((args["level"], args["status"]), ("other", "unknown"))
            self.assertEqual((args["region"], args["versions"]), ("上海市", "all"))
            self.assertEqual(args["kind"], "article")
        self.assertEqual(result["article_hits"], [])

    def test_missing_scope_suggests_resolution_only(self):
        result = service.search(self.library, "证人出庭", in_laws="不存在的法")
        self.assertEqual(result["counts"]["total"], 0)
        self.assertEqual(result["guidance"]["code"], "unresolved_law_scope")
        self.assertEqual(result["guidance"]["next_steps"], [
            {"tool": "resolve", "arguments": {"name": "不存在的法"}},
        ])

    def test_title_plus_document_number_has_literal_recovery(self):
        result = service.search(self.library, "测试公开文件 法释〔2024〕10号", kind="law")
        self.assertEqual(result["counts"]["total"], 0)
        step = result["guidance"]["next_steps"][0]
        self.assertEqual(step["arguments"]["query"], "测试公开文件")
        self.assertEqual(service.search(self.library, **step["arguments"])["counts"]["law"], 1)

    def test_success_does_not_suggest_rewriting(self):
        result = service.search(self.library, "证人出庭")
        self.assertEqual(result["counts"]["total"], 1)
        self.assertNotIn("guidance", result)

    def test_substring_resolution_warns_about_title_identity(self):
        result = service.resolve(self.library, "公开文件")
        self.assertTrue(result["matched"])
        self.assertEqual(result["via"], "like_fallback")
        self.assertIn("official_title", result["hint"])
        self.assertNotIn("hint", service.resolve(self.library, "测试公开文件"))

    def test_document_number_search_resolve_and_article(self):
        numbered_document(self.library, "numbered", "文号检索测试", "法释〔2024〕10号")
        result = service.search(self.library, "法释〔2024〕10号", kind="law")
        self.assertEqual([hit["id"] for hit in result["law_hits"]], ["numbered"])
        self.assertEqual(result["law_hits"][0]["match_mode"], "document_number")
        resolved = service.resolve(self.library, "法释〔2024〕 10 号")
        self.assertEqual((resolved["id"], resolved["via"]), ("numbered", "document_number_match"))
        self.assertEqual(
            service.get_article(self.library, "法释〔2024〕10号", "1")["law"]["id"], "numbered"
        )

    def test_document_number_keeps_title_and_scope_constraints(self):
        numbered_document(self.library, "numbered", "文号检索测试", "法释〔2024〕10号")
        good = service.search(self.library, "文号检索测试 法释〔2024〕10号", kind="law")
        self.assertEqual(good["counts"]["law"], 1)
        for arguments in [
            {"query": "无关名称 法释〔2024〕10号"},
            {"query": "法释〔2024〕10号", "in_laws": ["test-law"]},
            {"query": "法释〔2024〕10号", "level": "law"},
        ]:
            with self.subTest(arguments=arguments):
                result = service.search(self.library, kind="law", **arguments)
                self.assertEqual(result["counts"]["total"], 0)

    def test_csrc_number_matches_in_full_and_short_form(self):
        """库存全称时，全称、“中国证监会”“证监会”三种写法都能查到（#69）。"""

        numbered_document(self.library, "csrc-decision", "关于修改部分证券期货规范性文件的决定",
                          "中国证券监督管理委员会公告〔2025〕5号")
        for number in ["中国证券监督管理委员会公告〔2025〕5号",
                       "中国证监会公告〔2025〕5号", "证监会公告〔2025〕 5号"]:
            with self.subTest(number=number):
                resolved = service.resolve(self.library, number)
                self.assertEqual((resolved["id"], resolved["via"]),
                                 ("csrc-decision", "document_number_match"))
                result = service.search(self.library, number, kind="law")
                self.assertEqual([hit["id"] for hit in result["law_hits"]], ["csrc-decision"])
                self.assertEqual(result["law_hits"][0]["match_mode"], "document_number")
                self.assertEqual(
                    service.get_article(self.library, number, "1")["law"]["id"], "csrc-decision"
                )
        titled = service.search(
            self.library, "关于修改部分证券期货规范性文件的决定 中国证券监督管理委员会公告〔2025〕5号",
            kind="law",
        )
        self.assertEqual(titled["counts"]["law"], 1)
        other_title = service.search(
            self.library, "无关名称 中国证券监督管理委员会公告〔2025〕5号", kind="law"
        )
        self.assertEqual(other_title["counts"]["total"], 0)

    def test_order_number_without_year_matches(self):
        """“证监会令第N号”可按全称或简称写法解析、检索（#69）。"""

        numbered_document(self.library, "csrc-order", "示例管理办法", "证监会令第227号",
                          level="department_rule")
        for number in ["证监会令第227号", "中国证券监督管理委员会令第227号"]:
            with self.subTest(number=number):
                resolved = service.resolve(self.library, number)
                self.assertEqual((resolved["id"], resolved["via"]),
                                 ("csrc-order", "document_number_match"))
                result = service.search(self.library, number, kind="law")
                self.assertEqual([hit["id"] for hit in result["law_hits"]], ["csrc-order"])
        self.assertFalse(service.resolve(self.library, "证监会令第228号")["matched"])

    def test_shared_document_number_requires_selection(self):
        for suffix in ["甲", "乙"]:
            numbered_document(self.library, suffix, f"同文号文件{suffix}", "法发〔2024〕12号")
        result = service.resolve(self.library, "法发〔2024〕12号")
        self.assertFalse(result["matched"])
        self.assertEqual({item["id"] for item in result["candidates"]}, {"甲", "乙"})
        search = service.search(self.library, "法发〔2024〕12号", kind="law")
        self.assertEqual({item["id"] for item in search["law_hits"]}, {"甲", "乙"})
        self.assertIsNone(service.get_article(self.library, "法发〔2024〕12号", "1"))

    def test_explicit_old_document_number_does_not_switch_to_current_version(self):
        numbered_document(self.library, "old", "相同标题测试", "法发〔2010〕1号",
                          work_id="synthetic:versioned", status="repealed",
                          effective_at="2010-01-01", repealed_at="2020-01-01")
        numbered_document(self.library, "new", "相同标题测试", "法发〔2020〕1号",
                          work_id="synthetic:versioned", status="current",
                          effective_at="2020-01-01")
        self.assertEqual(service.resolve(self.library, "法发〔2010〕1号")["id"], "old")
        self.assertEqual(
            service.get_article(self.library, "法发〔2010〕1号", "1")["law"]["id"], "old"
        )

    def test_confident_citation_does_not_add_split_digit_fuzzy_hits(self):
        numbered_document(self.library, "citation", "编号测试法", "法发〔2024〕1号",
                          articles=[{"number": "191", "text": "目标条文"}])
        numbered_document(self.library, "noise", "无关引用", "法发〔2024〕2号",
                          articles=[{"number": "1", "text": "编号测试法第19项，另见第91条"}])
        result = service.search(self.library, "编号测试法第191条", kind="article")
        self.assertTrue(result["retrieval"]["citation"])
        self.assertEqual(result["article_hits"][0]["law_id"], "citation")
        self.assertFalse(result["fuzzy"]["applied"])
        self.assertTrue(all(hit.get("match_mode") != "fuzzy" for hit in result["article_hits"]))

    def test_citation_honors_explicit_filters(self):
        for filters in [{"level": "law"}, {"status": "repealed"}]:
            with self.subTest(filters=filters):
                result = service.search(self.library, "测试公开文件第一条", kind="article", **filters)
                self.assertEqual(result["counts"]["total"], 0)
                self.assertFalse(result["retrieval"]["citation"])

    def test_citation_inside_scope_preserves_scope_including_inserted_articles(self):
        numbered_document(self.library, "inserted", "增设条文测试法", "法发〔2024〕2号",
                          articles=[{"number": "120-1", "text": "目标增设条文。"}])
        query = "增设条文测试法第一百二十条之一"
        found = service.search(self.library, query, in_laws=["inserted"], kind="article")
        self.assertTrue(found["retrieval"]["citation"])
        self.assertEqual([(hit["law_id"], hit["number"]) for hit in found["article_hits"]], [
            ("inserted", "120-1"),
        ])
        for scope in ["test-law", "不存在的法"]:
            with self.subTest(scope=scope):
                blocked = service.search(self.library, query, in_laws=[scope], kind="article")
                self.assertEqual(blocked["counts"]["total"], 0)
                self.assertFalse(blocked["retrieval"]["citation"])

    def test_unknown_applicability_domain_explains_wildcard_results(self):
        with connect(self.library) as conn:
            conn.execute(
                "INSERT INTO applicability_rules "
                "(id,topic,domain,primary_law_id,rule_text,source_name,source_checked_at) "
                "VALUES ('test-rule','测试主题','all','test-law','测试规则','synthetic-test',"
                "'2020-01-01T00:00:00+00:00')"
            )
        result = service.applicable(self.library, as_of="2020-01-01", domain="不存在的领域",
                                    law="test-law")
        self.assertEqual(result["match_count"], 1)  # Existing wildcard semantics are retained.
        self.assertEqual(result["coverage"]["domains"], ["all"])
        codes = {warning["code"] for warning in result["warnings"]}
        self.assertLessEqual({"domain_not_in_library", "law_metadata_is_reference"}, codes)


class FuzzyCitationTests(unittest.TestCase):
    def test_fuzzy_cannot_piece_together_a_different_article_number(self):
        from chinalaw.fuzzy import matching_fragments

        for query, text in [
            ("第500条", "依据第50项，另见10条"),
            ("刑法第191条", "刑法第19章，另见第291条"),
            ("刑法第三百零六条", "刑法第二百零六条，第三百条另有规定"),
        ]:
            with self.subTest(query=query):
                self.assertIsNone(matching_fragments([query], text))


if __name__ == "__main__":
    unittest.main()
