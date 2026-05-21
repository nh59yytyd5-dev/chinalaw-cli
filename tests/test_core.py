"""chinalaw 端到端与单元测试。

运行：`PYTHONPATH=src python3 -m unittest discover -s tests -v`
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
import zipfile
from io import BytesIO
from pathlib import Path
from typing import ClassVar
from unittest.mock import patch

from chinalaw import (
    applicability,
    audit,
    cleaning,
    formatters,
    loader,
    normpacks,
    normsources,
    rebuild,
    service,
    snapshots,
    sources,
)
from chinalaw.adapters import court_gongbao, flk_npc, spp_gov_cn
from chinalaw.db import connect, current_version, get_meta, migrate, set_meta
from chinalaw.models import LawLevel
from chinalaw.schema import (
    SCHEMA_V1_SQL,
    SCHEMA_V2_SQL,
    SCHEMA_V3_SQL,
    SCHEMA_V4_SQL,
    SCHEMA_V5_SQL,
    SCHEMA_V6_SQL,
    SCHEMA_VERSION,
)
from chinalaw.service import normalize_article_number, normalize_law_identifier
from chinalaw.sync import sync_source

FIXTURES = Path(__file__).resolve().parent.parent / "data" / "fixtures"
APPLICABILITY_FIXTURES = Path(__file__).resolve().parent.parent / "data" / "applicability"
EXTRA_LAW_FIXTURE = {
    "id": "demo-law",
    "title": "中华人民共和国示例法",
    "short_title": "示例法",
    "aliases": ["示例条例"],
    "level": "law",
    "status": "current",
    "source_url": "https://example.com/law",
    "source_name": "example",
    "source_checked_at": "2026-01-01T00:00:00+00:00",
    "articles": [
        {"number": "14", "number_display": "第十四条", "text": "基础条文"},
        {"number": "14-1", "number_display": "第十四条之一", "text": "插入条文"},
    ],
}

NESTED_BRACKET_LAW_FIXTURE = {
    "id": "demo-nested-bracket",
    "title": "最高人民法院关于适用《示例母法》有关测试制度的解释",
    "short_title": "测试制度解释",
    "aliases": [],
    "level": "judicial_interpretation",
    "status": "current",
    "source_url": "https://example.com/test-explanation",
    "source_name": "example",
    "source_checked_at": "2026-01-01T00:00:00+00:00",
    "articles": [
        {"number": "1", "number_display": "第一条", "text": "本解释用于测试嵌套书名号归一化。"},
    ],
}


EXTRA_NORM_SOURCE_FIXTURE = {
    "id": "acme-lending-policy",
    "name": "甲方放款要求（示例）",
    "short_name": "放款要求",
    "aliases": ["甲方放款要求", "放款标准"],
    "source_type": "lender_requirement",
    "authority": "某甲方风控部",
    "binding_scope": "某融资项目放款审查",
    "jurisdiction": "CN",
    "effective_at": "2026-01-01",
    "source_name": "local-file",
    "source_checked_at": "2026-01-01T00:00:00+00:00",
    "clauses": [
        {"number": "第一条", "number_display": "第一条", "text": "借款主体应提交完整、真实、有效的工商登记及授权文件。"},
        {"number": "第二条", "number_display": "第二条", "text": "涉及担保的，应确认担保审批程序、签署权限和担保物状态均已满足要求。"},
        {"number": "2.1", "number_display": "2.1", "text": "如担保人为关联方，还应补充提交关联交易审批材料。"},
    ],
}


def make_docx_bytes(paragraphs: list[dict]) -> bytes:
    body = []
    for index, paragraph in enumerate(paragraphs, start=1):
        bookmark = ""
        if paragraph.get("bookmark"):
            bookmark = (
                f'<w:bookmarkStart w:id="{index}" w:name="{paragraph["bookmark"]}"/>'
                f'<w:bookmarkEnd w:id="{index}"/>'
            )
        body.append(
            "<w:p>"
            f"{bookmark}"
            "<w:r>"
            f"<w:t>{paragraph['text']}</w:t>"
            "</w:r>"
            "</w:p>"
        )
    xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{''.join(body)}</w:body>"
        "</w:document>"
    )
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("word/document.xml", xml)
    return buffer.getvalue()


class _FakeHTTPHeaders(dict):
    def get_content_charset(self) -> str:
        return "utf-8"


class _FakeHTTPResponse:
    def __init__(
        self,
        body: bytes | str,
        *,
        url: str = "https://flk.npc.gov.cn/mock",
        status_code: int = 200,
        content_type: str = "text/html",
    ):
        self._body = body.encode("utf-8") if isinstance(body, str) else body
        self._url = url
        self._status_code = status_code
        self.headers = _FakeHTTPHeaders({"Content-Type": content_type})

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            return self._body
        return self._body[:size]

    def geturl(self) -> str:
        return self._url

    def getcode(self) -> int:
        return self._status_code


class SchemaTests(unittest.TestCase):
    def test_migrate_creates_tables_and_sets_version(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "t.db"
            with connect(db) as conn:
                migrate(conn)
                self.assertEqual(current_version(conn), SCHEMA_VERSION)
                names = {
                    r[0]
                    for r in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type IN "
                        "('table','virtual') OR type='table'"
                    )
                }
                for t in (
                    "laws",
                    "articles",
                    "categories",
                    "law_categories",
                    "revisions",
                    "norm_packs",
                    "norm_pack_items",
                    "law_relations",
                    "applicability_rules",
                    "meta",
                    "articles_fts",
                    "laws_fts",
                ):
                    self.assertIn(t, names, f"missing table: {t}")

    def test_migrate_is_idempotent(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "t.db"
            with connect(db) as conn:
                migrate(conn)
                migrate(conn)  # 不应抛错
                self.assertEqual(current_version(conn), SCHEMA_VERSION)

    def test_migrate_from_v1_to_v2_adds_revision_snapshot_column(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "t.db"
            with connect(db) as conn:
                conn.executescript(SCHEMA_V1_SQL)
                conn.execute("INSERT INTO meta(key, value) VALUES('schema_version', '1')")
                migrate(conn)
                self.assertEqual(current_version(conn), SCHEMA_VERSION)
                revision_columns = {
                    row[1]
                    for row in conn.execute("PRAGMA table_info(revisions)")
                }
                self.assertIn("snapshot_json", revision_columns)

    def test_migrate_from_v2_to_v3_adds_norm_pack_tables(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "t.db"
            with connect(db) as conn:
                conn.executescript(SCHEMA_V2_SQL)
                conn.execute("INSERT INTO meta(key, value) VALUES('schema_version', '2')")
                from chinalaw.db import _migrate_v2_to_v3
                _migrate_v2_to_v3(conn)
                names = {
                    r[0]
                    for r in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type IN ('table', 'virtual')"
                    )
                }
                self.assertIn("norm_packs", names)
                self.assertIn("norm_pack_items", names)

    def test_migrate_from_v3_to_v4_adds_norm_source_tables(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "t.db"
            with connect(db) as conn:
                conn.executescript(SCHEMA_V3_SQL)
                conn.execute("INSERT INTO meta(key, value) VALUES('schema_version', '3')")
                migrate(conn)
                self.assertEqual(current_version(conn), SCHEMA_VERSION)
                names = {
                    r[0]
                    for r in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type IN ('table', 'virtual')"
                    )
                }
                self.assertIn("norm_sources", names)
                self.assertIn("norm_clauses", names)
                self.assertIn("norm_sources_fts", names)
                self.assertIn("norm_clauses_fts", names)

    def test_migrate_from_v4_to_v5_extends_norm_pack_items(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "t.db"
            with connect(db) as conn:
                conn.executescript(SCHEMA_V4_SQL)
                conn.execute("INSERT INTO meta(key, value) VALUES('schema_version', '4')")
                migrate(conn)
                self.assertEqual(current_version(conn), SCHEMA_VERSION)
                columns = {
                    row[1]
                    for row in conn.execute("PRAGMA table_info(norm_pack_items)")
                }
                self.assertIn("norm_source_id", columns)
                self.assertIn("norm_source_name", columns)
                self.assertIn("clause_number", columns)
                self.assertIn("clause_number_display", columns)

    def test_migrate_from_v5_to_v6_adds_norm_pack_dependencies(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "t.db"
            with connect(db) as conn:
                conn.executescript(SCHEMA_V5_SQL)
                conn.execute("INSERT INTO meta(key, value) VALUES('schema_version', '5')")
                migrate(conn)
                self.assertEqual(current_version(conn), SCHEMA_VERSION)
                columns = {
                    row[1]
                    for row in conn.execute("PRAGMA table_info(norm_packs)")
                }
                self.assertIn("dependencies_json", columns)

    def test_migrate_from_v6_to_v7_adds_applicability_tables(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "t.db"
            with connect(db) as conn:
                conn.executescript(SCHEMA_V6_SQL)
                conn.execute("INSERT INTO meta(key, value) VALUES('schema_version', '6')")
                migrate(conn)
                self.assertEqual(current_version(conn), SCHEMA_VERSION)
                names = {
                    r[0]
                    for r in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type IN ('table', 'virtual')"
                    )
                }
                self.assertIn("law_relations", names)
                self.assertIn("applicability_rules", names)


class NumberNormalizationTests(unittest.TestCase):
    def test_arabic_input(self):
        self.assertEqual(normalize_article_number("71"), "71")
        self.assertEqual(normalize_article_number("第71条"), "71")

    def test_inserted_article_input(self):
        self.assertEqual(normalize_article_number("14-1"), "14-1")
        self.assertEqual(normalize_article_number("第14条之1"), "14-1")
        self.assertEqual(normalize_article_number("第十四条之一"), "14-1")
        self.assertEqual(normalize_article_number("第十条之二"), "10-2")

    def test_chinese_input(self):
        self.assertEqual(normalize_article_number("第七十一条"), "71")
        self.assertEqual(normalize_article_number("第一百四十三条"), "143")
        self.assertEqual(normalize_article_number("第一千一百六十五条"), "1165")
        self.assertEqual(normalize_article_number("第十四条"), "14")
        self.assertEqual(normalize_article_number("第十条"), "10")

    def test_empty(self):
        self.assertEqual(normalize_article_number(""), "")


class LawIdentifierNormalizationTests(unittest.TestCase):
    """〈〉 是 GB/T 规定的嵌套书名号；DB 入库统一用《》。

    这些测试覆盖纯标点归一化，不涉及 alias / fuzzy match——
    任何"近似匹配"都应该走 aliases 表，而不是塞进这里。
    """

    def test_nested_brackets_replaced(self):
        # 担保制度解释的官方全称就是嵌套书名号场景
        full = "最高人民法院关于适用〈中华人民共和国民法典〉有关担保制度的解释"
        self.assertEqual(
            normalize_law_identifier(full),
            "最高人民法院关于适用《中华人民共和国民法典》有关担保制度的解释",
        )

    def test_outer_brackets_unchanged(self):
        canonical = "最高人民法院关于适用《中华人民共和国民法典》有关担保制度的解释"
        # 已经是正常《》形式的输入应该原样返回
        self.assertEqual(normalize_law_identifier(canonical), canonical)

    def test_short_title_unchanged(self):
        # 没有书名号的输入（短称 / alias 场景）一律不动
        self.assertEqual(normalize_law_identifier("公司法"), "公司法")
        self.assertEqual(normalize_law_identifier("担保制度解释"), "担保制度解释")

    def test_whitespace_and_empty(self):
        self.assertEqual(normalize_law_identifier("  公司法  "), "公司法")
        self.assertEqual(normalize_law_identifier(""), "")
        self.assertEqual(normalize_law_identifier(None), "")

    def test_only_open_or_only_close_bracket(self):
        # 半成品输入也保守归一（不抛异常、不识别为别的法）
        self.assertEqual(normalize_law_identifier("〈测试"), "《测试")
        self.assertEqual(normalize_law_identifier("测试〉"), "测试》")


class LoaderAndServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import tempfile
        cls._tmpdir = tempfile.TemporaryDirectory()
        cls.db_path = Path(cls._tmpdir.name) / "t.db"
        result = loader.load_fixtures(cls.db_path, FIXTURES)
        assert result["laws_loaded"] >= 3, result
        assert result["articles_loaded"] >= 10, result
        with connect(cls.db_path) as conn:
            migrate(conn)
            loader.load_law_from_dict(conn, EXTRA_LAW_FIXTURE)
            loader.load_law_from_dict(conn, NESTED_BRACKET_LAW_FIXTURE)
            normsources.import_source_from_dict(conn, EXTRA_NORM_SOURCE_FIXTURE)

    @classmethod
    def tearDownClass(cls):
        cls._tmpdir.cleanup()

    def test_status_counts(self):
        report = service.status(self.db_path)
        self.assertEqual(report["schema_version"], SCHEMA_VERSION)
        self.assertGreaterEqual(report["laws"], 3)
        self.assertGreaterEqual(report["articles"], 10)
        self.assertGreaterEqual(report["categories"], 0)
        self.assertGreaterEqual(report["norm_sources"], 1)
        self.assertGreaterEqual(report["norm_clauses"], 3)

    def test_search_fts5_long_query(self):
        r = service.search(self.db_path, "意思表示", kind="article")
        self.assertEqual(r["strategy"], "fts5")
        self.assertTrue(any(
            "意思表示" in h["text"] for h in r["article_hits"]
        ), r["article_hits"])

    def test_search_like_short_query(self):
        r = service.search(self.db_path, "过错", kind="article")
        self.assertEqual(r["strategy"], "like")
        self.assertTrue(any(
            "过错" in h["text"] for h in r["article_hits"]
        ))

    def test_search_multi_term_falls_back_from_phrase_only_matching(self):
        r = service.search(self.db_path, "工作 时间", kind="article")
        self.assertEqual(r["strategy"], "like")
        self.assertTrue(r["article_hits"])
        self.assertTrue(
            any("工作" in h["text"] and "时间" in h["text"] for h in r["article_hits"])
        )

    def test_search_can_be_limited_to_named_laws(self):
        labor = service.search(self.db_path, "民事主体", kind="article", in_laws="劳动法")
        self.assertEqual(labor["law_filter"]["resolved"][0]["id"], "flk-labor-law-1994-2018")
        self.assertFalse(labor["article_hits"])

        civil = service.search(self.db_path, "民事主体", kind="article", in_laws="民法典")
        self.assertEqual(civil["law_filter"]["resolved"][0]["id"], "flk-civil-code-2020")
        self.assertTrue(civil["article_hits"])
        self.assertTrue(all(hit["law_id"] == "flk-civil-code-2020" for hit in civil["article_hits"]))

        scoped = service.search(self.db_path, "担保 审批", kind="all", in_laws="民法典")
        self.assertEqual(scoped["norm_clause_hits"], [])
        self.assertEqual(scoped["norm_source_hits"], [])

    def test_search_law_kind(self):
        r = service.search(self.db_path, "劳动", kind="law")
        self.assertTrue(r["law_hits"])
        self.assertIn("劳动", r["law_hits"][0]["title"])

    def test_search_norm_kind(self):
        r = service.search(self.db_path, "担保 审批", kind="norm")
        self.assertEqual(r["strategy"], "like")
        self.assertTrue(r["norm_clause_hits"])
        self.assertEqual(r["norm_clause_hits"][0]["norm_source_name"], "甲方放款要求（示例）")

    def test_search_payload_includes_counts(self):
        """search payload 顶层暴露 counts，agent 不必 len() 各 hit 列表。"""
        r = service.search(self.db_path, "担保 审批")
        self.assertIn("counts", r)
        counts = r["counts"]
        for key in ("article", "law", "norm_clause", "norm_source", "total"):
            self.assertIn(key, counts)
        self.assertEqual(counts["norm_clause"], len(r["norm_clause_hits"]))
        self.assertEqual(counts["article"], len(r["article_hits"]))
        self.assertEqual(
            counts["total"],
            counts["article"] + counts["law"] + counts["norm_clause"] + counts["norm_source"],
        )
        self.assertGreaterEqual(counts["norm_clause"], 1)

    def test_search_empty_result_has_zero_counts(self):
        r = service.search(self.db_path, "  ")
        counts = r.get("counts", {})
        self.assertEqual(counts.get("total"), 0)
        for key in ("article", "law", "norm_clause", "norm_source"):
            self.assertEqual(counts.get(key), 0)

    def test_search_norm_visible_alongside_articles(self):
        """search 不带 --in 时同时暴露公开法规 article hit 与私域条款 hit。"""
        r = service.search(self.db_path, "担保")
        # 此 query 在 fixture 民法典 / 担保制度解释 等公开法规中也可命中
        # 关键不变量：私域规范条款也能进结果集
        self.assertTrue(r["norm_clause_hits"])

    def test_search_empty(self):
        r = service.search(self.db_path, "   ")
        self.assertEqual(r["article_hits"], [])
        self.assertEqual(r["law_hits"], [])
        self.assertEqual(r["norm_source_hits"], [])
        self.assertEqual(r["norm_clause_hits"], [])

    def test_get_law_exact_and_fuzzy(self):
        exact = service.get_law(self.db_path, "中华人民共和国民法典")
        self.assertIsNotNone(exact)
        self.assertEqual(exact["short_title"], "民法典")
        fuzzy = service.get_law(self.db_path, "民法典")
        self.assertIsNotNone(fuzzy)
        self.assertEqual(fuzzy["id"], exact["id"])
        self.assertGreaterEqual(fuzzy["article_count"], 5)

    def test_get_law_by_alias(self):
        payload = service.get_law(self.db_path, "示例条例")
        self.assertIsNotNone(payload)
        self.assertEqual(payload["id"], "demo-law")

    def test_same_alias_prefers_non_seed_populated_law(self):
        """同名 seed / full row 并存时，短称解析应优先可用全文版本。"""

        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "same-alias.db"
            seed_payload = {
                "id": "seed-criminal-law",
                "title": "中华人民共和国刑法",
                "short_title": "刑法",
                "aliases": ["刑法"],
                "level": "law",
                "status": "seed",
                "source_url": "https://example.test/criminal-seed",
                "source_name": "example.test",
                "source_checked_at": "2026-05-07T00:00:00+08:00",
                "articles": [
                    {"number": "14", "number_display": "第十四条", "text": "seed 条文"},
                ],
            }
            full_payload = {
                **seed_payload,
                "id": "full-criminal-law",
                "status": "current",
                "source_url": "https://example.test/criminal-full",
                "articles": [
                    {"number": "14", "number_display": "第十四条", "text": "full 条文"},
                    {"number": "272", "number_display": "第二百七十二条", "text": "挪用资金罪条文"},
                ],
            }
            with connect(db_path) as conn:
                migrate(conn)
                loader.load_law_from_dict(conn, seed_payload)
                loader.load_law_from_dict(conn, full_payload)

            resolved = service.resolve(db_path, "刑法")
            self.assertEqual(resolved["id"], "full-criminal-law")
            article = service.get_article(db_path, "刑法", "272")
            self.assertIsNotNone(article)
            self.assertEqual(article["law"]["id"], "full-criminal-law")
            self.assertEqual(article["article"]["number"], "272")
            exact_seed = service.get_article(db_path, "seed-criminal-law", "272")
            self.assertIsNotNone(exact_seed)
            self.assertEqual(exact_seed["law"]["id"], "seed-criminal-law")
            self.assertIsNone(exact_seed["article"])

    def test_article_miss_lists_same_alias_sibling_laws(self):
        """精确命中 seed id 但缺条文时，诊断应告诉 agent 还有同名候选。"""

        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "sibling.db"
            seed_payload = {
                "id": "seed-criminal-law",
                "title": "中华人民共和国刑法",
                "short_title": "刑法",
                "aliases": ["刑法"],
                "level": "law",
                "status": "seed",
                "source_url": "https://example.test/criminal-seed",
                "source_name": "example.test",
                "source_checked_at": "2026-05-07T00:00:00+08:00",
                "articles": [
                    {"number": "14", "number_display": "第十四条", "text": "seed 条文"},
                ],
            }
            full_payload = {
                **seed_payload,
                "id": "full-criminal-law",
                "status": "current",
                "source_url": "https://example.test/criminal-full",
                "articles": [
                    {"number": "14", "number_display": "第十四条", "text": "full 条文"},
                    {"number": "272", "number_display": "第二百七十二条", "text": "挪用资金罪条文"},
                ],
            }
            with connect(db_path) as conn:
                migrate(conn)
                loader.load_law_from_dict(conn, seed_payload)
                loader.load_law_from_dict(conn, full_payload)

            diag = service.diagnose_article_miss(db_path, "seed-criminal-law", "272")
            self.assertEqual(diag["reason"], "law_seed")
            sibling_ids = [item["id"] for item in diag["sibling_laws"]]
            self.assertIn("full-criminal-law", sibling_ids)
            self.assertTrue(
                any("full-criminal-law" in cmd for cmd in diag["suggested_sibling_articles"])
            )

    def test_resolve_returns_metadata_and_via_for_short_title(self):
        payload = service.resolve(self.db_path, "民法典")
        self.assertTrue(payload["matched"])
        self.assertEqual(payload["via"], "short_title_match")
        self.assertEqual(payload["official_title"], "中华人民共和国民法典")
        self.assertIn("民法典", payload["aliases"])
        self.assertEqual(payload["level"], "law")

    def test_resolve_via_paths_distinguish_match_route(self):
        """覆盖 spec §3.2.2 的 6 种 via 取值。

        除 ``alias_derived`` 外其余 5 种均通过 fixture / EXTRA_LAW_FIXTURE
        命中。``alias_derived`` 单独测：合同编通则解释 fixture 已含
        ``合通解释`` exact alias，无法测 derived；构造一条没有 fixture alias
        但能被 ``common_law_aliases`` 派生命中的入参。
        """

        # id_match：fixture 里 demo-law
        self.assertEqual(
            service.resolve(self.db_path, "demo-law")["via"],
            "id_match",
        )
        # title_match
        self.assertEqual(
            service.resolve(self.db_path, "中华人民共和国民法典")["via"],
            "title_match",
        )
        # short_title_match
        self.assertEqual(
            service.resolve(self.db_path, "民法典")["via"],
            "short_title_match",
        )
        # alias_exact：示例条例 在 EXTRA_LAW_FIXTURE 的 aliases 列表
        self.assertEqual(
            service.resolve(self.db_path, "示例条例")["via"],
            "alias_exact",
        )
        # alias_derived：合同编通则解释 fixture 没有把"合同编通则解释（一）"
        # 这种带括号变体写到 aliases 列表，但 common_law_aliases 会派生出
        # ``合同编通则解释`` 列表外的等价别名，由 derived path 命中。
        # 这里测 short_title 已直接命中的同时还存在的派生 alias 入口：
        # 用一个不在 alias 列表但能被规则派生的 form。
        derived = service.resolve(self.db_path, "合同编通则解释")
        self.assertTrue(derived["matched"])
        # 合同编通则解释 既是 short_title 又在 alias 列表 → short_title_match
        # 优先（spec §3 排序：short_title > alias_exact）
        self.assertIn(derived["via"], {"short_title_match", "alias_exact"})

    def test_resolve_returns_unmatched_for_unknown_input(self):
        payload = service.resolve(self.db_path, "完全不存在的法名")
        self.assertFalse(payload["matched"])
        self.assertIsNone(payload["via"])
        self.assertEqual(payload["input"], "完全不存在的法名")

    def test_resolve_short_litigation_law_aliases_prefer_base_laws(self):
        for query, expected in (
            ("民诉法", "中华人民共和国民事诉讼法"),
            ("刑诉法", "中华人民共和国刑事诉讼法"),
            ("行诉法", "中华人民共和国行政诉讼法"),
        ):
            payload = service.resolve(self.db_path, query)
            self.assertTrue(payload["matched"], query)
            self.assertEqual(payload["official_title"], expected)
            self.assertEqual(payload["via"], "alias_exact")

    def test_resolve_versioned_statute_alias(self):
        payload = service.resolve(self.db_path, "宪法")
        self.assertTrue(payload["matched"])
        self.assertEqual(payload["official_title"], "中华人民共和国宪法（2018年修正文本）")
        self.assertEqual(payload["via"], "short_title_match")

    def test_short_normative_name_does_not_like_fallback_to_longer_law(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "short-name.db"
            with connect(db_path) as conn:
                migrate(conn)
                loader.load_law_from_dict(
                    conn,
                    {
                        "id": "labor-dispute-arbitration-law",
                        "title": "中华人民共和国劳动争议调解仲裁法",
                        "short_title": "劳动争议调解仲裁法",
                        "aliases": ["劳动争议调解仲裁法"],
                        "level": "law",
                        "status": "current",
                        "source_url": "https://example.test/labor-arbitration",
                        "source_name": "example.test",
                        "source_checked_at": "2026-05-20T00:00:00+08:00",
                        "articles": [
                            {"number": "1", "number_display": "第一条", "text": "劳动争议仲裁示例。"},
                        ],
                    },
                )

            payload = service.resolve(db_path, "仲裁法")
            self.assertFalse(payload["matched"])
            self.assertIsNone(service.get_law(db_path, "仲裁法"))

    def test_cleaning_adds_common_aliases(self):
        payload = cleaning.canonicalize_external_json(
            {
                "id": "court-general-principles-interpretation",
                "title": "最高人民法院关于适用《中华人民共和国民法典》总则编若干问题的解释",
                "level": "judicial_interpretation",
                "status": "current",
                "source_url": "https://example.com/general-principles",
                "source_name": "example",
                "source_checked_at": "2026-05-01T00:00:00+00:00",
                "articles": [
                    {"number_display": "第十九条", "text": "示例解释条文。"},
                ],
            }
        )

        self.assertEqual(payload["short_title"], "总则编解释")
        self.assertIn("总则编解释", payload["aliases"])
        self.assertIn("民法典总则编解释", payload["aliases"])

    def test_resolves_derived_common_alias_for_existing_rows(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "t.db"
            with connect(db_path) as conn:
                migrate(conn)
                loader.load_law_from_dict(
                    conn,
                    {
                        "id": "court-general-principles-interpretation",
                        "title": "最高人民法院关于适用《中华人民共和国民法典》总则编若干问题的解释",
                        "short_title": None,
                        "aliases": [],
                        "level": "judicial_interpretation",
                        "status": "current",
                        "source_url": "https://example.com/general-principles",
                        "source_name": "example",
                        "source_checked_at": "2026-05-01T00:00:00+00:00",
                        "articles": [
                            {"number": "19", "number_display": "第十九条", "text": "示例解释条文。"},
                        ],
                    },
                )

            payload = service.get_article(db_path, "总则编解释", "第十九条")

        self.assertIsNotNone(payload)
        self.assertIsNotNone(payload["article"])
        self.assertEqual(payload["law"]["short_title"], "总则编解释")

    def test_article_markdown_prefers_short_title_heading(self):
        md = formatters.article_to_markdown(
            {
                "law": {
                    "title": "最高人民法院关于适用《中华人民共和国民法典》总则编若干问题的解释",
                    "short_title": "总则编解释",
                    "status": "current",
                    "source_url": "https://example.com/general-principles",
                },
                "article": {
                    "number_display": "第十九条",
                    "text": "示例解释条文。",
                },
                "requested_number": "第十九条",
            }
        )

        self.assertIn("## 总则编解释 第十九条", md)
        self.assertNotIn("## 《最高人民法院", md)

    def test_get_law_missing(self):
        self.assertIsNone(service.get_law(self.db_path, "不存在的法规xyz"))

    def test_get_article_chinese_and_arabic(self):
        payload_cn = service.get_article(self.db_path, "民法典", "第一百四十三条")
        payload_ar = service.get_article(self.db_path, "民法典", "143")
        self.assertIsNotNone(payload_cn["article"])
        self.assertIsNotNone(payload_ar["article"])
        self.assertEqual(payload_cn["item"], payload_cn["article"])
        self.assertEqual(payload_cn["article"]["id"], payload_ar["article"]["id"])

    def test_get_article_not_found(self):
        payload = service.get_article(self.db_path, "民法典", "99999")
        self.assertIsNone(payload["article"])
        self.assertIsNone(payload["item"])

    def test_get_law_with_nested_brackets_resolves_to_canonical(self):
        # DB 入库统一用《》；用户用 GB/T 的嵌套书名号 〈〉 也应该命中。
        canonical = "最高人民法院关于适用《示例母法》有关测试制度的解释"
        nested = "最高人民法院关于适用〈示例母法〉有关测试制度的解释"
        canonical_payload = service.get_law(self.db_path, canonical)
        nested_payload = service.get_law(self.db_path, nested)
        self.assertIsNotNone(canonical_payload)
        self.assertIsNotNone(nested_payload)
        self.assertEqual(canonical_payload["id"], nested_payload["id"])

    def test_get_article_with_nested_brackets_resolves(self):
        nested = "最高人民法院关于适用〈示例母法〉有关测试制度的解释"
        payload = service.get_article(self.db_path, nested, "1")
        self.assertIsNotNone(payload)
        self.assertIsNotNone(payload["article"])
        self.assertEqual(payload["article"]["number"], "1")

    def test_empty_law_identifier_does_not_fuzzy_match(self):
        self.assertIsNone(service.get_law(self.db_path, "   "))
        self.assertIsNone(service.get_article(self.db_path, "   ", "3"))
        self.assertIsNone(service.get_articles(self.db_path, "   ", "3"))

    def test_get_article_by_alias_and_inserted_number(self):
        payload = service.get_article(self.db_path, "示例条例", "第十四条之一")
        self.assertIsNotNone(payload)
        self.assertIsNotNone(payload["article"])
        self.assertEqual(payload["item"], payload["article"])
        self.assertEqual(payload["article"]["number"], "14-1")
        self.assertEqual(payload["article"]["number_display"], "第十四条之一")

    def test_get_articles_batch_number_spec(self):
        payload = service.get_articles(
            self.db_path,
            "民法典",
            "5,12,13,19,23-25",
        )

        self.assertIsNotNone(payload)
        self.assertEqual(payload["kind"], "law_articles")
        self.assertEqual(payload["normalized_numbers"], ["5", "12", "13", "19", "23", "24", "25"])
        self.assertEqual(payload["found_count"], 7)
        self.assertEqual(payload["missing_count"], 0)
        self.assertEqual(payload["articles"], payload["items"])
        self.assertNotIn("articles", payload["law"])
        self.assertNotIn("snapshot_json", payload["law"]["current_revision"])

    def test_outline_law_part_filter(self):
        payload = service.outline_law(
            self.db_path,
            "民法典",
            part="自然人",
            preview_chars=20,
        )

        self.assertIsNotNone(payload)
        self.assertEqual(payload["kind"], "law_outline")
        self.assertTrue(payload["items"])
        self.assertTrue(all("自然人" in (item.get("part") or "") for item in payload["items"]))
        self.assertLessEqual(len(payload["items"][0]["text_preview"]), 20)
        self.assertIs(payload["articles"], payload["items"])
        self.assertEqual(payload["text_mode"], "preview")
        self.assertFalse(payload["full_text"])
        self.assertNotIn("text", payload["items"][0])

    def test_outline_law_with_text_includes_full_articles(self):
        payload = service.outline_law(
            self.db_path,
            "民法典",
            part="自然人",
            with_text=True,
        )

        self.assertIsNotNone(payload)
        self.assertEqual(payload["kind"], "law_outline")
        self.assertTrue(payload.get("with_text"))
        self.assertTrue(payload.get("full_text"))
        self.assertEqual(payload.get("text_mode"), "full")
        self.assertEqual(payload.get("missing_count"), 0)
        self.assertEqual(payload.get("found_count"), payload.get("item_count"))
        self.assertIs(payload["articles"], payload["items"])
        self.assertTrue(payload["items"])
        first = payload["items"][0]
        self.assertIn("article", first)
        self.assertIn("text", first)
        self.assertTrue((first["article"].get("text") or "").strip())
        self.assertEqual(first["text"], first["article"]["text"].strip())
        self.assertFalse(first["text_truncated"])
        self.assertEqual(first.get("found"), True)

    def test_arabic_to_chinese_numeral_known_values(self):
        from chinalaw.service import _arabic_to_chinese_numeral

        cases = {
            1: "一",
            10: "十",
            11: "十一",
            19: "十九",
            20: "二十",
            100: "一百",
            110: "一百一十",
            111: "一百一十一",
            200: "二百",
            522: "五百二十二",
            1000: "一千",
            1260: "一千二百六十",
        }
        for value, expected in cases.items():
            self.assertEqual(_arabic_to_chinese_numeral(value), expected, msg=str(value))

    def test_parse_cited_by_spec(self):
        from chinalaw.service import parse_cited_by_spec

        self.assertEqual(parse_cited_by_spec("民法典:522"), ("民法典", "522"))
        self.assertEqual(parse_cited_by_spec("民法典：第522条"), ("民法典", "第522条"))
        self.assertEqual(parse_cited_by_spec("民法典 522"), ("民法典", "522"))
        self.assertIsNone(parse_cited_by_spec(""))
        self.assertIsNone(parse_cited_by_spec("民法典"))

    def test_find_cited_by_returns_other_laws_only_by_default(self):
        payload = service.find_cited_by(self.db_path, "民法典", "522")
        self.assertIsNotNone(payload)
        self.assertEqual(payload["kind"], "law_article_cited_by")
        self.assertEqual(payload["target"]["normalized_number"], "522")
        self.assertFalse(payload["include_self"])
        host_law_ids = {hit["law"]["id"] for hit in payload["hits"]}
        self.assertNotIn(payload["target"]["law"]["id"], host_law_ids)

    def test_find_cited_by_in_laws_filter(self):
        payload = service.find_cited_by(
            self.db_path,
            "民法典",
            "522",
            in_laws=["合通解释"],
        )
        self.assertIsNotNone(payload)
        self.assertTrue(payload["hits"])
        for hit in payload["hits"]:
            self.assertIn("合同编通则", hit["law"]["title"])

    def test_find_cited_by_chinese_number_normalisation(self):
        payload = service.find_cited_by(self.db_path, "民法典", "第五百二十二条")
        self.assertIsNotNone(payload)
        self.assertEqual(payload["target"]["normalized_number"], "522")
        self.assertTrue(payload["hits"])

    def test_find_cited_by_unknown_law(self):
        payload = service.find_cited_by(self.db_path, "不存在的法规", "1")
        self.assertIsNone(payload)

    def test_article_payload_omits_revision_snapshot_json(self):
        payload = service.get_article(self.db_path, "民法典", "第一条")

        self.assertIsNotNone(payload)
        self.assertNotIn("articles", payload["law"])
        self.assertNotIn("snapshot_json", payload["law"]["current_revision"])

    def test_fixture_loading_strips_trailing_section_headings(self):
        article_2 = service.get_article(self.db_path, "合通解释", "2")
        article_25 = service.get_article(self.db_path, "合同编通则解释", "25")

        self.assertIsNotNone(article_2)
        self.assertNotIn("二、合同的订立", article_2["article"]["text"])
        self.assertIsNotNone(article_25)
        self.assertNotIn("四、合同的履行", article_25["article"]["text"])

        article_3 = service.get_article(self.db_path, "合通解释", "3")
        article_26 = service.get_article(self.db_path, "合同编通则解释", "26")
        self.assertEqual(article_3["article"]["part"], "二、合同的订立")
        self.assertEqual(article_26["article"]["part"], "四、合同的履行")

    def test_contract_chapter_fixture_has_complete_part_coverage(self):
        """守门：合通解释 fixture 全部 69 article 都有 part 字段，
        覆盖 9 个 distinct part（一、一般规定 → 九、附则）。

        修前 fixture（commit 21a02ab 之前由 fetch --to-fixture 落盘）articles[*].part
        全 None，sync --fixtures 路径靠 normalize_articles 的
        _split_trailing_structural_headings 重建出 67/69，剩 art №1/№2 缺 part。
        PR #42（cleaning canonicalize_flk_npc 加 normalize_articles 兜底）+
        本 PR fetch --force 刷新 fixture 后所有 part 在落盘那一刻就齐全。
        本测试守门未来再次漂移会立刻 fail。
        """
        import json
        from pathlib import Path

        fixture_path = (
            Path(__file__).parent.parent
            / "data"
            / "fixtures"
            / "contract_chapter_interpretation_2023.json"
        )
        data = json.loads(fixture_path.read_text(encoding="utf-8"))
        articles = data["articles"]
        self.assertIn("合通解释", data["aliases"])
        self.assertEqual(data["category_ids"], ["flk:0", "flk:27", "flk:28"])
        self.assertEqual(len(articles), 69)
        missing = [
            a["number_display"] for a in articles if not a.get("part")
        ]
        self.assertEqual(missing, [], f"articles 缺 part: {missing}")
        distinct_parts = sorted({a["part"] for a in articles})
        self.assertEqual(len(distinct_parts), 9)
        # 全部 9 个一级 section heading 都应被识别（顺序按发布顺序，
        # 测试只比集合不比序，避免后续若官方调整段尾顺序时假阳性）
        expected = {
            "一、一般规定",
            "二、合同的订立",
            "三、合同的效力",
            "四、合同的履行",
            "五、合同的保全",
            "六、合同的变更和转让",
            "七、合同的权利义务终止",
            "八、违约责任",
            "九、附则",
        }
        self.assertEqual(set(distinct_parts), expected)

    def test_list_laws_filters(self):
        all_current = service.list_laws(self.db_path, status="current")
        self.assertTrue(all_current)
        laws_only = service.list_laws(self.db_path, level="law")
        self.assertTrue(all(item["level"] == "law" for item in laws_only))
        admin_regs = service.list_laws(self.db_path, level="admin_regulation")
        self.assertTrue(admin_regs)
        self.assertTrue(all(item["level"] == "admin_regulation" for item in admin_regs))

    def test_reload_is_idempotent(self):
        """再 load 一次 fixture，数量不应翻倍。"""
        before = service.status(self.db_path)
        loader.load_fixtures(self.db_path, FIXTURES)
        after = service.status(self.db_path)
        self.assertEqual(before["laws"], after["laws"])
        self.assertEqual(before["articles"], after["articles"])
        self.assertEqual(before["revisions"], after["revisions"])

    def test_revision_created_and_returned_by_get_law(self):
        import tempfile

        initial_payload = {
            "id": "versioned-law",
            "title": "中华人民共和国版本法",
            "short_title": "版本法",
            "aliases": [],
            "level": "law",
            "status": "current",
            "source_url": "https://example.com/versioned-law",
            "source_name": "example",
            "source_checked_at": "2026-04-22T00:00:00+00:00",
            "released_at": "2026-01-01",
            "effective_at": "2026-02-01",
            "articles": [
                {"number": "1", "number_display": "第一条", "text": "第一版正文。"},
            ],
        }
        updated_payload = {
            **initial_payload,
            "source_checked_at": "2026-04-23T00:00:00+00:00",
            "articles": [
                {"number": "1", "number_display": "第一条", "text": "第二版正文。"},
            ],
        }

        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "t.db"
            with connect(db_path) as conn:
                migrate(conn)
                loader.load_law_from_dict(conn, initial_payload)
                loader.load_law_from_dict(conn, initial_payload)
                loader.load_law_from_dict(conn, updated_payload)

            law = service.get_law(db_path, "版本法")
            report = service.status(db_path)

        self.assertIsNotNone(law)
        self.assertEqual(law["revision_count"], 2)
        self.assertEqual(law["current_revision"]["content_hash"], law["source_hash"])
        self.assertEqual(law["articles"][0]["text"], "第二版正文。")
        self.assertEqual(report["revisions"], 2)

    def test_trace_article_maps_renumbered_revision(self):
        import tempfile

        old_payload = {
            "id": "trace-law",
            "title": "中华人民共和国追溯法",
            "short_title": "追溯法",
            "aliases": [],
            "level": "law",
            "status": "amended",
            "source_url": "https://example.com/trace-law",
            "source_name": "example",
            "source_checked_at": "2026-04-22T00:00:00+00:00",
            "released_at": "2020-01-01",
            "effective_at": "2020-02-01",
            "articles": [
                {"number": "1", "number_display": "第一条", "text": "旧版第一条。", "position": 1},
                {
                    "number": "2",
                    "number_display": "第二条",
                    "text": "有下列情形之一的，人民法院裁定终结执行：\n（一）申请人撤销申请的；\n（二）据以执行的法律文书被撤销的；\n（三）作为被执行人的公民死亡，无遗产可供执行，又无义务承担人的。",
                    "position": 2,
                },
            ],
        }
        new_payload = {
            **old_payload,
            "status": "current",
            "source_checked_at": "2026-04-23T00:00:00+00:00",
            "released_at": "2024-01-01",
            "effective_at": "2024-02-01",
            "articles": [
                {"number": "1", "number_display": "第一条", "text": "旧版第一条。", "position": 1},
                {"number": "2", "number_display": "第二条", "text": "新增条文。", "position": 2},
                {
                    "number": "3",
                    "number_display": "第三条",
                    "text": "有下列情形之一的，人民法院裁定终结执行：\n（一）申请人撤销申请的；\n（二）据以执行的法律文书被撤销的；\n（三）作为被执行人的公民死亡，无遗产可供执行，又无义务承担人的。",
                    "position": 3,
                },
            ],
        }

        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "t.db"
            with connect(db_path) as conn:
                migrate(conn)
                loader.load_law_from_dict(conn, old_payload)
                loader.load_law_from_dict(conn, new_payload)

            traced = service.trace_article_as_of(
                db_path,
                "追溯法",
                "2",
                from_as_of="2021-01-01",
                to_as_of="2025-01-01",
                items="3",
            )

        self.assertIsNotNone(traced)
        self.assertTrue(traced["ok"], traced)
        self.assertEqual(traced["status"], "renumbered")
        self.assertEqual(traced["from"]["article"]["number"], "2")
        self.assertEqual(traced["to"]["article"]["number"], "3")
        self.assertTrue(traced["diff"]["number_changed"])
        self.assertFalse(traced["diff"]["text_changed"])
        self.assertEqual(traced["from"]["items"][0]["number"], "3")
        self.assertEqual(traced["to"]["items"][0]["number"], "3")

    def test_trace_article_text_query_uses_low_confidence_gate(self):
        import tempfile

        payload = {
            "id": "trace-text-law",
            "title": "中华人民共和国文本追溯法",
            "short_title": "文本追溯法",
            "aliases": [],
            "level": "law",
            "status": "current",
            "source_url": "https://example.com/trace-text-law",
            "source_name": "example",
            "source_checked_at": "2026-04-22T00:00:00+00:00",
            "released_at": "2024-01-01",
            "effective_at": "2024-02-01",
            "articles": [
                {"number": "1", "number_display": "第一条", "text": "甲乙丙丁。", "position": 1},
                {"number": "2", "number_display": "第二条", "text": "完全无关的目标文本。", "position": 2},
            ],
        }

        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "t.db"
            with connect(db_path) as conn:
                migrate(conn)
                loader.load_law_from_dict(conn, payload)

            traced = service.trace_article_as_of(
                db_path,
                "文本追溯法",
                text="甲乙丙丁",
                from_as_of="2025-01-01",
                to_as_of="2025-01-01",
            )

        self.assertIsNotNone(traced)
        self.assertTrue(traced["ok"], traced)
        self.assertEqual(traced["from"]["article"]["number"], "1")
        self.assertEqual(traced["to"]["article"]["number"], "1")

    def test_categories_are_loaded_and_returned(self):
        import tempfile

        payload = {
            "id": "categorized-law",
            "title": "中华人民共和国分类法",
            "short_title": "分类法",
            "aliases": [],
            "level": "law",
            "status": "current",
            "source_url": "https://example.com/categorized-law",
            "source_name": "example",
            "source_checked_at": "2026-04-22T00:00:00+00:00",
            "released_at": "2026-01-01",
            "effective_at": "2026-02-01",
            "categories": [
                {"id": "flk:1", "name": "法律", "parent_id": None, "description": "root"},
                {"id": "flk:5", "name": "民法商法", "parent_id": "flk:1", "description": "leaf"},
            ],
            "category_ids": ["flk:1", "flk:5"],
            "articles": [{"number": "1", "number_display": "第一条", "text": "分类正文。"}],
        }

        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "t.db"
            with connect(db_path) as conn:
                migrate(conn)
                loader.load_law_from_dict(conn, payload)

            law = service.get_law(db_path, "分类法")
            report = service.status(db_path)

        self.assertIsNotNone(law)
        self.assertEqual([category["name"] for category in law["categories"]], ["法律", "民法商法"])
        self.assertEqual(report["categories"], 2)

    def test_get_law_and_article_as_of_revision(self):
        import tempfile

        first_payload = {
            "id": "asof-law",
            "title": "中华人民共和国时点法",
            "short_title": "时点法",
            "aliases": [],
            "level": "law",
            "status": "current",
            "source_url": "https://example.com/asof-law",
            "source_name": "example",
            "source_checked_at": "2026-04-22T00:00:00+00:00",
            "released_at": "2024-01-01",
            "effective_at": "2024-02-01",
            "articles": [{"number": "1", "number_display": "第一条", "text": "旧版本正文。"}],
        }
        second_payload = {
            **first_payload,
            "source_checked_at": "2026-04-23T00:00:00+00:00",
            "released_at": "2025-01-01",
            "effective_at": "2025-02-01",
            "articles": [{"number": "1", "number_display": "第一条", "text": "新版本正文。"}],
        }

        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "t.db"
            with connect(db_path) as conn:
                migrate(conn)
                loader.load_law_from_dict(conn, first_payload)
                loader.load_law_from_dict(conn, second_payload)

            law_2024 = service.get_law_as_of(db_path, "时点法", "2024-06-01")
            article_2024 = service.get_article_as_of(db_path, "时点法", "第一条", "2024-06-01")
            law_2025 = service.get_law_as_of(db_path, "时点法", "2025-06-01")

        self.assertIsNotNone(law_2024)
        self.assertEqual(law_2024["selected_revision"]["released_at"], "2024-01-01")
        self.assertEqual(article_2024["article"]["text"], "旧版本正文。")
        self.assertEqual(law_2025["selected_revision"]["released_at"], "2025-01-01")
        self.assertEqual(law_2025["articles"][0]["text"], "新版本正文。")

    def test_diff_law_as_of_detects_added_removed_and_changed_articles(self):
        import tempfile

        first_payload = {
            "id": "diff-law",
            "title": "中华人民共和国差异法",
            "short_title": "差异法",
            "aliases": [],
            "level": "law",
            "status": "current",
            "source_url": "https://example.com/diff-law",
            "source_name": "example",
            "source_checked_at": "2026-04-22T00:00:00+00:00",
            "released_at": "2024-01-01",
            "effective_at": "2024-02-01",
            "articles": [
                {"number": "1", "number_display": "第一条", "text": "旧第一条。"},
                {"number": "2", "number_display": "第二条", "text": "旧第二条。"},
            ],
        }
        second_payload = {
            **first_payload,
            "source_checked_at": "2026-04-23T00:00:00+00:00",
            "released_at": "2025-01-01",
            "effective_at": "2025-02-01",
            "articles": [
                {"number": "1", "number_display": "第一条", "text": "新第一条。"},
                {"number": "3", "number_display": "第三条", "text": "新增第三条。"},
            ],
        }

        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "t.db"
            with connect(db_path) as conn:
                migrate(conn)
                loader.load_law_from_dict(conn, first_payload)
                loader.load_law_from_dict(conn, second_payload)

            diff = service.diff_law_as_of(db_path, "差异法", "2024-06-01", "2025-06-01")

        self.assertIsNotNone(diff)
        self.assertEqual(diff["summary"], {"added": 1, "removed": 1, "changed": 1})
        self.assertEqual(diff["added"][0]["number"], "3")
        self.assertEqual(diff["removed"][0]["number"], "2")
        self.assertEqual(diff["changed"][0]["number"], "1")


class SourceProbeTests(unittest.TestCase):
    SAMPLE_HTML = """
    <!doctype html>
    <html>
      <head>
        <title>国家法律法规数据库</title>
        <script type="module" crossorigin src="/assets/index-main.js"></script>
        <link rel="stylesheet" crossorigin href="/assets/index-main.css">
      </head>
      <body>
        <div id="app"></div>
      </body>
    </html>
    """

    SAMPLE_BUNDLE = """
    const nav = ["首页", "宪法", "法律", "行政法规", "监察法规", "司法解释", "地方性法规"];
    """

    def test_probe_detects_spa_assets_and_sections(self):
        homepage = flk_npc.FetchResult(
            url="https://flk.npc.gov.cn/",
            status_code=200,
            headers={"Last-Modified": "Tue, 30 Dec 2025 07:37:29 GMT", "ETag": "etag-1"},
            text=self.SAMPLE_HTML,
        )
        bundle = flk_npc.FetchResult(
            url="https://flk.npc.gov.cn/assets/index-main.js",
            status_code=200,
            headers={},
            text=self.SAMPLE_BUNDLE,
        )

        with patch.object(flk_npc, "_fetch_text", side_effect=[homepage, bundle]):
            report = flk_npc.probe(timeout=1)

        self.assertEqual(report["source"], "flk_npc")
        self.assertEqual(report["status_code"], 200)
        self.assertEqual(report["page_shape"], "spa")
        self.assertEqual(report["title"], "国家法律法规数据库")
        self.assertEqual(
            report["main_script_url"],
            "https://flk.npc.gov.cn/assets/index-main.js",
        )
        self.assertEqual(
            report["stylesheet_url"],
            "https://flk.npc.gov.cn/assets/index-main.css",
        )
        self.assertIn("法律", report["detected_sections"])
        self.assertTrue(report["bundle_contains_known_sections"])

    def test_search_list_uses_expected_payload_defaults(self):
        adapter = flk_npc.FlkNpcAdapter(timeout=1)
        with patch.object(adapter, "_request_json", return_value={"code": 200}) as request_json:
            payload = adapter.search_list("民法典", page_num=2, page_size=5)

        self.assertEqual(payload["code"], 200)
        request_json.assert_called_once_with(
            flk_npc.SEARCH_LIST_PATH,
            method="POST",
            data={
                "searchContent": "民法典",
                "searchRange": 1,
                "searchType": 2,
                "sxrq": [],
                "gbrq": [],
                "sxx": [],
                "gbrqYear": [],
                "flfgCodeId": [],
                "zdjgCodeId": [],
                "orderByParam": {"order": "-1", "sort": ""},
                "pageNum": 2,
                "pageSize": 5,
            },
        )

    def test_detail_related_and_recommendation_endpoints(self):
        adapter = flk_npc.FlkNpcAdapter(timeout=1)
        with patch.object(adapter, "_request_json", return_value={"code": 200}) as request_json:
            adapter.fetch_law_detail("law-1")
            adapter.fetch_related_file_detail("law-1")
            adapter.fetch_recommendations("law-1")

        self.assertEqual(request_json.call_count, 3)
        self.assertEqual(
            request_json.call_args_list[0].kwargs,
            {"params": {"bbbs": "law-1"}},
        )
        self.assertEqual(request_json.call_args_list[0].args[0], flk_npc.LAW_DETAIL_PATH)
        self.assertEqual(request_json.call_args_list[1].args[0], flk_npc.RELATED_FILE_PATH)
        self.assertEqual(request_json.call_args_list[2].args[0], flk_npc.RECOMMENDATIONS_PATH)

    def test_request_json_reports_flk_waf_html(self):
        adapter = flk_npc.FlkNpcAdapter(timeout=1, request_interval=0)
        html = (
            "<!DOCTYPE HTML><html><body><noscript>"
            "<h1><strong>Please enable JavaScript and refresh the page.</strong></h1>"
            "</noscript><script src='/wzws-waf-cgi/jquery.js'></script></body></html>"
        )
        with patch(
            "chinalaw.adapters.flk_npc.urlopen",
            return_value=_FakeHTTPResponse(
                html,
                url="https://flk.npc.gov.cn/law-search/search/flfgDetails?bbbs=law-1",
                content_type="text/html",
            ),
        ), self.assertRaises(ValueError) as ctx:
            adapter.fetch_law_detail("law-1")

        message = str(ctx.exception)
        self.assertIn("anti-bot JavaScript challenge", message)
        self.assertIn("expected JSON object", message)
        self.assertIn(flk_npc.LAW_DETAIL_PATH, message)

    def test_download_docx_reports_html_instead_of_zip(self):
        adapter = flk_npc.FlkNpcAdapter(timeout=1, request_interval=0)
        html = "<html><body>Please enable JavaScript and refresh the page.</body></html>"

        with patch.object(
            adapter,
            "get_download_info",
            return_value={"data": {"url": "https://flk.npc.gov.cn/download/mock.docx"}},
        ), patch(
            "chinalaw.adapters.flk_npc._fetch_bytes",
            return_value=flk_npc.BinaryFetchResult(
                url="https://flk.npc.gov.cn/download/mock.docx",
                status_code=200,
                headers={"Content-Type": "text/html"},
                content=html.encode("utf-8"),
            ),
        ), self.assertRaises(ValueError) as ctx:
            adapter.download_docx_bytes("law-1")

        message = str(ctx.exception)
        self.assertIn("anti-bot JavaScript challenge", message)
        self.assertIn("expected DOCX zip or legacy OLE Word bytes", message)

    def test_download_accepts_legacy_word_bytes(self):
        adapter = flk_npc.FlkNpcAdapter(timeout=1, request_interval=0)
        legacy_doc_bytes = cleaning.OLE_WORD_MAGIC + b"legacy"

        with patch.object(
            adapter,
            "get_download_info",
            return_value={"data": {"url": "https://flk.npc.gov.cn/download/mock.doc"}},
        ), patch(
            "chinalaw.adapters.flk_npc._fetch_bytes",
            return_value=flk_npc.BinaryFetchResult(
                url="https://flk.npc.gov.cn/download/mock.doc",
                status_code=200,
                headers={"Content-Type": "application/msword"},
                content=legacy_doc_bytes,
            ),
        ):
            payload = adapter.download_docx_bytes("law-1")

        self.assertEqual(payload, legacy_doc_bytes)

    def test_source_hash_is_stable_for_detail_payload(self):
        adapter = flk_npc.FlkNpcAdapter(timeout=1)
        detail_payload = {"code": 200, "data": {"bbbs": "law-1", "title": "示例法"}}
        with patch.object(adapter, "fetch_law_detail", return_value=detail_payload):
            digest = adapter.source_hash("law-1")

        expected = "ff1dacc95cedbbd60664cd23670185bbd706da308c87183ea8f7168eb431a474"
        self.assertEqual(digest, expected)

    def test_parse_articles_from_docx(self):
        docx_bytes = make_docx_bytes(
            [
                {"bookmark": "中华人民共和国示例法", "text": "中华人民共和国示例法"},
                {"bookmark": "第一编 总则", "text": "第一编 总则"},
                {"bookmark": "第一章 基本规定", "text": "第一章 基本规定"},
                {"bookmark": "第一条", "text": "第一条 为了测试。"},
                {"text": "本条续款。"},
                {"bookmark": "第二条", "text": "第二条 本法适用。"},
            ]
        )
        articles = flk_npc.parse_articles_from_docx(docx_bytes)

        self.assertEqual(len(articles), 2)
        self.assertEqual(articles[0]["number"], "1")
        self.assertEqual(articles[0]["number_display"], "第一条")
        self.assertEqual(articles[0]["part"], "第一编 总则 第一章 基本规定")
        self.assertIn("本条续款。", articles[0]["text"])
        self.assertEqual(articles[1]["number"], "2")

    def test_cleaning_canonicalizes_flk_detail_payload(self):
        detail_payload = {
            "code": 200,
            "data": {
                "bbbs": "law-1",
                "title": "中华人民共和国示例法",
                "flxz": "法律",
                "zdjgName": "全国人民代表大会",
                "gbrq": "2026-01-01",
                "sxrq": "2026-02-01",
                "sxx": 3,
            },
        }
        docx_bytes = make_docx_bytes(
            [
                {"bookmark": "中华人民共和国示例法", "text": "中华人民共和国示例法"},
                {"bookmark": "第一章 总则", "text": "第一章 总则"},
                {"bookmark": "第一条", "text": "第一条 示例正文。"},
            ]
        )

        payload = cleaning.canonicalize(
            detail_payload,
            source_kind="flk_npc_detail",
            bbbs="law-1",
            docx_bytes=docx_bytes,
            checked_at="2026-04-29T00:00:00+00:00",
            categories=[{"id": "flk:1", "name": "法律", "parent_id": None}],
            category_ids=["flk:1"],
        )

        self.assertEqual(payload["id"], "law-1")
        self.assertEqual(payload["short_title"], "示例法")
        self.assertEqual(payload["level"], "law")
        self.assertEqual(payload["status"], "current")
        self.assertEqual(payload["source_name"], "flk.npc.gov.cn")
        self.assertEqual(payload["category_ids"], ["flk:1"])
        self.assertEqual(payload["articles"][0]["number"], "1")
        self.assertEqual(len(payload["source_hash"]), 64)

    def test_cleaning_canonicalizes_flk_legacy_doc_payload(self):
        detail_payload = {
            "code": 200,
            "data": {
                "bbbs": "legacy-doc-law",
                "title": "最高人民法院关于示例问题的规定",
                "flxz": "司法解释",
                "zdjgName": "最高人民法院",
                "gbrq": "2020-12-29",
                "sxrq": "2021-01-01",
                "sxx": 3,
            },
        }
        legacy_doc_bytes = cleaning.OLE_WORD_MAGIC + b"legacy"
        with patch.object(
            cleaning,
            "_convert_legacy_doc_to_text",
            return_value="第一条 示例旧 Word 正文。\n第二条 第二条正文。",
        ):
            payload = cleaning.canonicalize(
                detail_payload,
                source_kind="flk_npc_detail",
                bbbs="legacy-doc-law",
                docx_bytes=legacy_doc_bytes,
                checked_at="2026-04-29T00:00:00+00:00",
            )

        self.assertEqual(payload["level"], "judicial_interpretation")
        self.assertEqual(len(payload["articles"]), 2)
        self.assertEqual(payload["articles"][0]["number"], "1")
        self.assertIn("示例旧 Word 正文", payload["articles"][0]["text"])

    def test_canonicalize_flk_npc_normalizes_articles(self):
        """canonicalize_flk_npc 必须走 normalize_articles 兜底，与其他 3 个
        source_kind（external_json / markdown / docx）对称。

        回归 source: data/fixtures/contract_chapter_interpretation_2023.json
        在 commit 21a02ab 之前由 fetch --to-fixture 落盘时 part 全 None。
        详见 docs/CANONICALIZE_FLK_NPC_NORMALIZE_SPEC.md。
        """
        detail_payload = {
            "code": 200,
            "data": {
                "bbbs": "law-flk-normalize",
                "title": "最高人民法院关于示例问题的解释",
                "flxz": "司法解释",
                "zdjgName": "最高人民法院",
                "gbrq": "2023-12-01",
                "sxrq": "2024-01-01",
                "sxx": 3,
            },
        }
        # 用 patch 注入 pathological articles：trailing heading 嵌在 art №1 末尾、
        # 全部 part=None。这正是 _split_trailing_structural_headings 引入前从
        # parse 路径漏出来的那种产物，也是合通解释 fixture 漂移的原型。
        pathological = [
            {
                "number": "1",
                "number_display": "第一条",
                "text": "示例正文一。\n一、合同的订立",
                "part": None,
                "position": 1,
            },
            {
                "number": "2",
                "number_display": "第二条",
                "text": "示例正文二。",
                "part": None,
                "position": 2,
            },
        ]
        with patch.object(
            cleaning,
            "parse_articles_from_word_bytes",
            return_value=pathological,
        ):
            payload = cleaning.canonicalize(
                detail_payload,
                source_kind="flk_npc_detail",
                bbbs="law-flk-normalize",
                docx_bytes=b"PK\x03\x04mock",
                checked_at="2026-05-04T00:00:00+00:00",
            )

        # trailing heading 已被 _split_trailing_structural_headings 剥掉
        self.assertEqual(payload["articles"][0]["text"], "示例正文一。")
        # 剥下来的 heading 反向更新 normalize 自己的 context →
        # art №2 的 part 由该 heading 兜底（normalize_articles L361-362）
        self.assertEqual(payload["articles"][1]["part"], "一、合同的订立")
        # 既有 number / position 由 normalize 保留不动
        self.assertEqual(payload["articles"][0]["number"], "1")
        self.assertEqual(payload["articles"][1]["number"], "2")

    def test_cleaning_canonicalizes_markdown_law(self):
        payload = cleaning.canonicalize(
            """
            # 中华人民共和国示例法
            第一章 总则
            第一条 示例正文。
            本条续款。
            第二条 第二条正文。
            """,
            source_kind="markdown",
            id="local-example-law",
            title="中华人民共和国示例法",
            level="law",
            status="current",
            source_url="local-file:example.md",
            source_name="example.md",
            source_checked_at="2026-04-29T00:00:00+00:00",
        )

        self.assertEqual(payload["short_title"], "示例法")
        self.assertEqual(payload["articles"][0]["number"], "1")
        self.assertEqual(payload["articles"][0]["part"], "第一章 总则")
        self.assertIn("本条续款。", payload["articles"][0]["text"])
        self.assertEqual(payload["articles"][1]["number"], "2")
        self.assertEqual(len(payload["source_hash"]), 64)

    def test_cleaning_treats_enumerated_headings_as_structure(self):
        payload = cleaning.canonicalize(
            """
            # 最高人民法院关于示例问题的解释
            一、一般规定
            第一条 第一条正文。
            二、合同的订立
            第二条 第二条正文。
            """,
            source_kind="markdown",
            id="local-enum-heading-law",
            title="最高人民法院关于示例问题的解释",
            level="judicial_interpretation",
            status="current",
            source_url="local-file:enum.md",
            source_name="enum.md",
            source_checked_at="2026-05-01T00:00:00+00:00",
        )

        self.assertEqual(payload["articles"][0]["text"], "第一条正文。")
        self.assertEqual(payload["articles"][0]["part"], "一、一般规定")
        self.assertEqual(payload["articles"][1]["text"], "第二条正文。")
        self.assertEqual(payload["articles"][1]["part"], "二、合同的订立")

    def test_cleaning_keeps_nonsequential_enumerated_body_lines(self):
        payload = cleaning.canonicalize(
            """
            # 最高人民法院关于示例问题的解释
            一、一般规定
            第一条 第一款正文。
            一、甲方负责履行合同
            第二条 第二条正文。
            """,
            source_kind="markdown",
            id="local-enum-body-law",
            title="最高人民法院关于示例问题的解释",
            level="judicial_interpretation",
            status="current",
            source_url="local-file:enum-body.md",
            source_name="enum-body.md",
            source_checked_at="2026-05-01T00:00:00+00:00",
        )

        self.assertIn("一、甲方负责履行合同", payload["articles"][0]["text"])
        self.assertEqual(payload["articles"][1]["part"], "一、一般规定")

    def test_cleaning_canonicalizes_docx_law(self):
        docx_bytes = make_docx_bytes(
            [
                {"bookmark": "中华人民共和国示例法", "text": "中华人民共和国示例法"},
                {"bookmark": "第一章 总则", "text": "第一章 总则"},
                {"bookmark": "第十条之一", "text": "第十条之一 插入条款。"},
            ]
        )

        payload = cleaning.canonicalize(
            docx_bytes,
            source_kind="docx",
            id="local-docx-law",
            title="中华人民共和国示例法",
            level="law",
            status="current",
            source_url="local-file:example.docx",
            source_name="example.docx",
            source_checked_at="2026-04-29T00:00:00+00:00",
        )

        self.assertEqual(payload["articles"][0]["number"], "10-1")
        self.assertEqual(payload["articles"][0]["number_display"], "第十条之一")

    def test_load_files_canonicalizes_external_json(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "t.db"
            fixture_path = Path(td) / "local-law.json"
            fixture_path.write_text(
                json.dumps(
                    {
                        "id": "local-json-law",
                        "title": "中华人民共和国本地法",
                        "level": "law",
                        "status": "current",
                        "source_url": "local-file:local-law.json",
                        "source_name": "local-law.json",
                        "source_checked_at": "2026-04-29T00:00:00+00:00",
                        "articles": [
                            {"number_display": "第十条之二", "text": "插入条款。"},
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            result = loader.load_files(db_path, [fixture_path])
            article = service.get_article(db_path, "本地法", "10-2")
            law = service.get_law(db_path, "本地法")

        self.assertEqual(result["articles_loaded"], 1)
        self.assertIsNotNone(article)
        self.assertEqual(article["article"]["number"], "10-2")
        self.assertIsNotNone(law)
        self.assertEqual(len(law["source_hash"]), 64)

    def test_build_law_payload_from_detail_and_docx(self):
        adapter = flk_npc.FlkNpcAdapter(timeout=1)
        detail_payload = {
            "code": 200,
            "data": {
                "bbbs": "law-1",
                "title": "中华人民共和国示例法",
                "flxz": "法律",
                "zdjgName": "全国人民代表大会",
                "gbrq": "2026-01-01",
                "sxrq": "2026-02-01",
                "sxx": 3,
            },
        }
        docx_bytes = make_docx_bytes(
            [
                {"bookmark": "中华人民共和国示例法", "text": "中华人民共和国示例法"},
                {"bookmark": "第一章 总则", "text": "第一章 总则"},
                {"bookmark": "第一条", "text": "第一条 示例正文。"},
            ]
        )

        category_tree_payload = {
            "source": "flk_npc",
            "root": {"id": 1, "name": "法律", "codeId": 101, "children": [{"id": 5, "name": "民法商法", "codeId": 120, "children": []}]},
            "categories": [
                {"id": "flk:1", "name": "法律", "parent_id": None, "description": "root"},
                {"id": "flk:5", "name": "民法商法", "parent_id": "flk:1", "description": "leaf"},
            ],
        }
        with patch.object(adapter, "build_category_tree_payload", return_value=category_tree_payload):
            payload = adapter.build_law_payload(
                "law-1",
                detail_payload=detail_payload,
                docx_bytes=docx_bytes,
            )

        self.assertEqual(payload["id"], "law-1")
        self.assertEqual(payload["title"], "中华人民共和国示例法")
        self.assertEqual(payload["short_title"], "示例法")
        self.assertEqual(payload["level"], "law")
        self.assertEqual(payload["status"], "current")
        self.assertEqual(payload["articles"][0]["number"], "1")
        self.assertEqual(payload["articles"][0]["text"], "示例正文。")
        self.assertEqual(payload["category_ids"], [])

    def test_build_law_payload_uses_search_row_category_code(self):
        adapter = flk_npc.FlkNpcAdapter(timeout=1)
        detail_payload = {
            "code": 200,
            "data": {
                "bbbs": "law-1",
                "title": "中华人民共和国示例法",
                "flxz": "法律",
                "zdjgName": "全国人民代表大会",
                "gbrq": "2026-01-01",
                "sxrq": "2026-02-01",
                "sxx": 3,
            },
        }
        docx_bytes = make_docx_bytes(
            [
                {"bookmark": "中华人民共和国示例法", "text": "中华人民共和国示例法"},
                {"bookmark": "第一章 总则", "text": "第一章 总则"},
                {"bookmark": "第一条", "text": "第一条 示例正文。"},
            ]
        )
        category_tree_payload = {
            "source": "flk_npc",
            "root": {"id": 0, "name": "根", "codeId": None, "children": [{"id": 1, "name": "法律", "codeId": 101, "children": [{"id": 5, "name": "民法商法", "codeId": 120, "children": []}]}]},
            "categories": [
                {"id": "flk:0", "name": "根", "parent_id": None, "description": "root"},
                {"id": "flk:1", "name": "法律", "parent_id": "flk:0", "description": "level1"},
                {"id": "flk:5", "name": "民法商法", "parent_id": "flk:1", "description": "leaf"},
            ],
        }
        with patch.object(adapter, "build_category_tree_payload", return_value=category_tree_payload):
            payload = adapter.build_law_payload(
                "law-1",
                search_row={"flfgCodeId": 120},
                detail_payload=detail_payload,
                docx_bytes=docx_bytes,
            )

        self.assertEqual(payload["category_ids"], ["flk:0", "flk:1", "flk:5"])

    def test_build_category_tree_payload_and_category_path(self):
        adapter = flk_npc.FlkNpcAdapter(timeout=1)
        enum_payload = {
            "code": 200,
            "data": {
                "flfgfl": {
                    "id": 0,
                    "name": "根",
                    "codeId": None,
                    "children": [
                        {
                            "id": 1,
                            "name": "法律",
                            "codeId": 101,
                            "children": [
                                {
                                    "id": 5,
                                    "name": "民法商法",
                                    "codeId": 120,
                                    "children": [],
                                }
                            ],
                        }
                    ],
                }
            },
        }
        with patch.object(adapter, "fetch_enum_data", return_value=enum_payload):
            payload = adapter.build_category_tree_payload()

        self.assertEqual(len(payload["categories"]), 3)
        path_ids = flk_npc._category_ids_from_code(payload["root"], 120)
        self.assertEqual(path_ids, ["flk:0", "flk:1", "flk:5"])

class CleaningLevelMappingTests(unittest.TestCase):
    """覆盖 FLXZ_TO_LEVEL 实测发现的 mapping bug 回归。

    bug 来源：docs/research/2026-05-source-coverage-survey.md §3。
    """

    def test_xiuzhengan_maps_to_law(self):
        # 实测样本：刑法修正案 / 立法法修正案 / 反垄断法修订决定，flk 返回 flxz='修正案'。
        # 修复前落 'other'，修复后归到 'law'（修正案是法律的修订形式）。
        self.assertEqual(cleaning.infer_level("修正案"), "law")

    def test_xianfa_maps_to_law_until_contract_adds_constitution_level(self):
        self.assertEqual(cleaning.infer_level("宪法"), "law")

    def test_difang_fagui_short_form_maps_to_local_regulation(self):
        # 实测样本：北京市数字经济促进条例，flk 返回 flxz='地方法规'（短形式）。
        # 修复前因为 mapping key 写成"地方性法规"导致命中 'other'。
        self.assertEqual(cleaning.infer_level("地方法规"), "local_regulation")
        # 书面形式仍兼容
        self.assertEqual(cleaning.infer_level("地方性法规"), "local_regulation")

    def test_bumen_guizhang_maps_to_department_rule(self):
        # LawLevel 早就声明了 department_rule，但 FLXZ_TO_LEVEL 缺映射。
        self.assertEqual(cleaning.infer_level("部门规章"), "department_rule")

    def test_difang_zhengfu_guizhang_maps_to_local_government_rule(self):
        self.assertEqual(cleaning.infer_level("地方政府规章"), "local_government_rule")

    def test_jiancha_fagui_value_is_in_law_level_enum(self):
        # bug 3：FLXZ_TO_LEVEL 把"监察法规"写成 'supervisory_regulation'，
        # 但 LawLevel 枚举原先没声明这个值，构成数据契约破裂。
        level = cleaning.infer_level("监察法规")
        self.assertEqual(level, "supervisory_regulation")
        self.assertIn(level, {e.value for e in LawLevel})

    def test_all_flxz_values_map_to_declared_law_levels(self):
        """每个 FLXZ_TO_LEVEL 的 value 必须是合法 LawLevel 枚举值。"""
        declared = {e.value for e in LawLevel}
        for flxz, level in cleaning.FLXZ_TO_LEVEL.items():
            self.assertIn(
                level,
                declared,
                msg=f"FLXZ_TO_LEVEL[{flxz!r}]={level!r} not in LawLevel enum",
            )

    def test_unknown_flxz_falls_back_to_other(self):
        self.assertEqual(cleaning.infer_level("奇怪的法律性质"), "other")
        self.assertEqual(cleaning.infer_level(None), "other")
        self.assertEqual(cleaning.infer_level(""), "other")


class LawLevelEnumTests(unittest.TestCase):
    """LawLevel 枚举本期扩展到 11 档，覆盖 docs/CONTRACT.md §2.9 的全部受控值。"""

    def test_law_level_includes_judicial_meeting_minutes(self):
        # issue #16：会议纪要（如九民纪要）应当能与司法解释独立分级。
        self.assertEqual(LawLevel.JUDICIAL_MEETING_MINUTES.value, "judicial_meeting_minutes")

    def test_law_level_includes_judicial_policy(self):
        # issue #16：最高法批复 / 通知 / 复函等"政策性文件"。
        self.assertEqual(LawLevel.JUDICIAL_POLICY.value, "judicial_policy")

    def test_law_level_includes_guiding_case(self):
        # issue #16：最高法 / 最高检发布的指导性案例。
        self.assertEqual(LawLevel.GUIDING_CASE.value, "guiding_case")

    def test_law_level_includes_supervisory_regulation(self):
        # bug 修复：cleaning 早就在写这个值，enum 此前没声明。
        self.assertEqual(LawLevel.SUPERVISORY_REGULATION.value, "supervisory_regulation")

    def test_law_level_includes_self_regulatory_rule(self):
        self.assertEqual(LawLevel.SELF_REGULATORY_RULE.value, "self_regulatory_rule")

    def test_law_level_legacy_values_unchanged(self):
        # 既有 7 档不变，避免破坏既有 fixture / 测试。
        legacy = {
            "law", "admin_regulation", "judicial_interpretation",
            "department_rule", "local_regulation", "local_government_rule", "other",
        }
        declared = {e.value for e in LawLevel}
        self.assertTrue(legacy.issubset(declared))


class CourtGongbaoProbeTests(unittest.TestCase):
    """最高人民法院公报站 adapter probe-only 测试（ADR-0008 §1.1）。"""

    SAMPLE_HOMEPAGE = """
    <!doctype html>
    <html>
      <head>
        <title>中华人民共和国最高人民法院公报</title>
        <meta name="generator" content="ASP.NET">
      </head>
      <body>
        <a href="/PeriodicalsDic.html">期刊目录</a>
        <span>司法解释</span>
        <span>公报案例</span>
        <span>工作报告</span>
      </body>
    </html>
    """

    def test_probe_returns_standard_shape(self):
        homepage = court_gongbao.FetchResult(
            url="http://gongbao.court.gov.cn/",
            status_code=200,
            headers={"Last-Modified": "Tue, 01 May 2026 00:00:00 GMT", "ETag": "abc-1"},
            text=self.SAMPLE_HOMEPAGE,
        )
        with patch.object(court_gongbao, "_fetch_text", return_value=homepage):
            report = court_gongbao.probe(timeout=1)

        self.assertEqual(report["source"], "court_gongbao")
        self.assertEqual(report["status_code"], 200)
        self.assertEqual(report["page_shape"], "ok")
        self.assertEqual(report["title"], "中华人民共和国最高人民法院公报")
        self.assertEqual(report["source_last_modified"], "Tue, 01 May 2026 00:00:00 GMT")
        self.assertEqual(report["source_etag"], "abc-1")
        self.assertIn("司法解释", report["detected_sections"])
        self.assertIn("公报案例", report["detected_sections"])
        self.assertTrue(report["bundle_contains_known_sections"])

    def test_probe_records_http_error_without_raising(self):
        from urllib.error import HTTPError

        def raise_http_error(*args, **kwargs):
            raise HTTPError("http://gongbao.court.gov.cn/", 503, "Service Unavailable", {}, None)

        with patch.object(court_gongbao, "_fetch_text", side_effect=raise_http_error):
            report = court_gongbao.probe(timeout=1)

        self.assertEqual(report["source"], "court_gongbao")
        self.assertEqual(report["status_code"], 503)
        self.assertEqual(report["page_shape"], "error")
        self.assertIn("HTTPError 503", report.get("error", ""))

    def test_default_request_interval_matches_adr_0008(self):
        # ADR-0008 §3.3：默认节流提到 500ms，降低反爬触发概率。
        adapter = court_gongbao.CourtGongbaoAdapter()
        self.assertEqual(adapter.request_interval, 0.5)


class SppGovCnProbeTests(unittest.TestCase):
    """最高检 adapter probe-only 测试（ADR-0008 §1.2）。"""

    SAMPLE_HOMEPAGE = """
    <!doctype html>
    <html>
      <head>
        <title>中华人民共和国最高人民检察院</title>
      </head>
      <body>
        <a href="/spp/jczdal/index.shtml">指导性案例</a>
        <a href="/spp/qwfb/index.shtml">权威发布</a>
        <a href="/spp/zdgz/">重大工作</a>
      </body>
    </html>
    """

    def test_probe_returns_standard_shape(self):
        homepage = spp_gov_cn.FetchResult(
            url="https://www.spp.gov.cn/",
            status_code=200,
            headers={"Last-Modified": "Tue, 01 May 2026 00:00:00 GMT"},
            text=self.SAMPLE_HOMEPAGE,
        )
        with patch.object(spp_gov_cn, "_fetch_text", return_value=homepage):
            report = spp_gov_cn.probe(timeout=1)

        self.assertEqual(report["source"], "spp_gov_cn")
        self.assertEqual(report["status_code"], 200)
        self.assertEqual(report["page_shape"], "ok")
        self.assertEqual(report["title"], "中华人民共和国最高人民检察院")
        self.assertIn("指导性案例", report["detected_sections"])
        self.assertIn("权威发布", report["detected_sections"])
        self.assertTrue(report["bundle_contains_known_sections"])

    def test_probe_records_url_error_without_raising(self):
        from urllib.error import URLError

        def raise_url_error(*args, **kwargs):
            raise URLError("connection refused")

        with patch.object(spp_gov_cn, "_fetch_text", side_effect=raise_url_error):
            report = spp_gov_cn.probe(timeout=1)

        self.assertEqual(report["source"], "spp_gov_cn")
        self.assertIsNone(report["status_code"])
        self.assertEqual(report["page_shape"], "error")
        self.assertIn("URLError", report.get("error", ""))


class SourceRegistryTests(unittest.TestCase):
    """sources.ADAPTER_REGISTRY 注册表行为（ADR-0008 §3.1）。"""

    def test_registry_includes_all_three_adapters(self):
        self.assertIn("flk_npc", sources.ADAPTER_REGISTRY)
        self.assertIn("court_gongbao", sources.ADAPTER_REGISTRY)
        self.assertIn("spp_gov_cn", sources.ADAPTER_REGISTRY)

    def test_get_source_adapter_normalizes_dashes_and_case(self):
        # 用户可能写 court-gongbao / Court_Gongbao 等变体
        adapter_a = sources.get_source_adapter("court-gongbao")
        adapter_b = sources.get_source_adapter("COURT_GONGBAO")
        adapter_c = sources.get_source_adapter("court_gongbao")
        self.assertIs(adapter_a, adapter_b)
        self.assertIs(adapter_b, adapter_c)

    def test_get_source_adapter_unknown_lists_known_sources(self):
        with self.assertRaises(ValueError) as ctx:
            sources.get_source_adapter("pkulaw")
        msg = str(ctx.exception)
        # 错误消息应当列出已知源，便于 agent 自我纠错
        self.assertIn("flk_npc", msg)
        self.assertIn("court_gongbao", msg)
        self.assertIn("spp_gov_cn", msg)

    def test_probe_source_dispatches_via_registry(self):
        homepage = court_gongbao.FetchResult(
            url="http://gongbao.court.gov.cn/",
            status_code=200,
            headers={},
            text="<html><head><title>公报</title></head><body>司法解释</body></html>",
        )
        with patch.object(court_gongbao, "_fetch_text", return_value=homepage):
            report = sources.probe_source("court_gongbao")
        self.assertEqual(report["source"], "court_gongbao")


class AdapterComplianceTests(unittest.TestCase):
    """docs/COMPLIANCE.md §3 / §4 的 adapter 层执行。

    覆盖：
    - 三个 adapter 的 UA 都包含 ``chinalaw-cli`` 标识
    - ``request_interval=0`` / 负值 / 低于 ``MIN_REQUEST_INTERVAL`` 的值都会被
      clamp 到下限，无法关闭节流
    - clamp 行为不抛错，仅静默 floor（避免 user-facing 报错引发 fallback 调用
      绕过节流）
    """

    def test_court_gongbao_user_agent_carries_tool_token(self):
        self.assertIn("chinalaw-cli", court_gongbao.DEFAULT_USER_AGENT)
        self.assertIn(
            "chinalaw-cli", court_gongbao.TOOL_UA_TOKEN
        )
        # 浏览器兼容前缀仍保留以避开 ASP.NET URLScan 的 UA 启发式
        self.assertIn("Mozilla/5.0", court_gongbao.DEFAULT_USER_AGENT)

    def test_spp_gov_cn_user_agent_carries_tool_token(self):
        self.assertIn("chinalaw-cli", spp_gov_cn.DEFAULT_USER_AGENT)
        self.assertIn("chinalaw-cli", spp_gov_cn.TOOL_UA_TOKEN)
        self.assertIn("Mozilla/5.0", spp_gov_cn.DEFAULT_USER_AGENT)

    def test_flk_npc_user_agent_carries_tool_token_and_repo_url(self):
        # FLK 的 UA 不需要浏览器兼容前缀（它的反爬挑战是 JS challenge，UA
        # 形态本身不影响触发概率），用纯工具标识更诚实
        self.assertIn("chinalaw-cli", flk_npc.DEFAULT_USER_AGENT)
        self.assertIn("github.com", flk_npc.DEFAULT_USER_AGENT)
        # 不再使用早期占位 +https://local
        self.assertNotIn("+https://local", flk_npc.DEFAULT_USER_AGENT)

    def test_court_gongbao_request_built_with_tool_user_agent(self):
        req = court_gongbao._build_request(
            "http://gongbao.court.gov.cn/ArticleList.html"
        )
        self.assertIn("chinalaw-cli", req.get_header("User-agent") or "")

    def test_spp_gov_cn_request_built_with_tool_user_agent(self):
        req = spp_gov_cn._build_request("https://www.spp.gov.cn/")
        self.assertIn("chinalaw-cli", req.get_header("User-agent") or "")

    def test_flk_npc_request_built_with_tool_user_agent(self):
        req = flk_npc._build_request(
            "https://flk.npc.gov.cn/api/test",
            accept="application/json",
        )
        self.assertIn("chinalaw-cli", req.get_header("User-agent") or "")

    def test_court_gongbao_request_interval_clamps_zero_to_floor(self):
        # 即使调用方传 0 也无法关闭节流；clamp 到 0.1s 下限并实际 sleep
        adapter = court_gongbao.CourtGongbaoAdapter(request_interval=0)
        sleeps: list[float] = []
        with (
            patch.object(court_gongbao.time, "sleep", side_effect=sleeps.append),
            patch.object(court_gongbao.time, "monotonic", side_effect=[0.0, 0.0, 0.0]),
        ):
            adapter._throttle()
        self.assertEqual(len(sleeps), 1)
        self.assertGreaterEqual(sleeps[0], court_gongbao.MIN_REQUEST_INTERVAL)

    def test_court_gongbao_request_interval_clamps_negative_to_floor(self):
        adapter = court_gongbao.CourtGongbaoAdapter(request_interval=-5)
        sleeps: list[float] = []
        with (
            patch.object(court_gongbao.time, "sleep", side_effect=sleeps.append),
            patch.object(court_gongbao.time, "monotonic", side_effect=[0.0, 0.0, 0.0]),
        ):
            adapter._throttle()
        self.assertEqual(len(sleeps), 1)
        self.assertGreaterEqual(sleeps[0], court_gongbao.MIN_REQUEST_INTERVAL)

    def test_court_gongbao_request_interval_clamps_below_floor(self):
        # 0.01 太快——也得 floor 到 0.1
        adapter = court_gongbao.CourtGongbaoAdapter(request_interval=0.01)
        sleeps: list[float] = []
        with (
            patch.object(court_gongbao.time, "sleep", side_effect=sleeps.append),
            patch.object(court_gongbao.time, "monotonic", side_effect=[0.0, 0.0, 0.0]),
        ):
            adapter._throttle()
        self.assertEqual(len(sleeps), 1)
        self.assertAlmostEqual(sleeps[0], court_gongbao.MIN_REQUEST_INTERVAL, places=6)

    def test_court_gongbao_request_interval_above_floor_kept(self):
        # 0.5 默认值不应被 clamp（floor 仅作为下限）
        adapter = court_gongbao.CourtGongbaoAdapter(request_interval=0.5)
        sleeps: list[float] = []
        with (
            patch.object(court_gongbao.time, "sleep", side_effect=sleeps.append),
            patch.object(court_gongbao.time, "monotonic", side_effect=[0.0, 0.0, 0.0]),
        ):
            adapter._throttle()
        self.assertEqual(len(sleeps), 1)
        self.assertAlmostEqual(sleeps[0], 0.5, places=6)

    def test_spp_gov_cn_request_interval_clamps_zero_to_floor(self):
        adapter = spp_gov_cn.SppGovCnAdapter(request_interval=0)
        sleeps: list[float] = []
        with (
            patch.object(spp_gov_cn.time, "sleep", side_effect=sleeps.append),
            patch.object(spp_gov_cn.time, "monotonic", side_effect=[0.0, 0.0, 0.0]),
        ):
            adapter._throttle()
        self.assertEqual(len(sleeps), 1)
        self.assertGreaterEqual(sleeps[0], spp_gov_cn.MIN_REQUEST_INTERVAL)

    def test_flk_npc_request_interval_clamps_zero_to_floor(self):
        adapter = flk_npc.FlkNpcAdapter(request_interval=0)
        sleeps: list[float] = []
        with patch.object(flk_npc.time, "sleep", side_effect=sleeps.append), patch.object(
            flk_npc.time, "monotonic", side_effect=[0.0, 0.0, 0.0]
        ):
            adapter._throttle()
        self.assertEqual(len(sleeps), 1)
        self.assertGreaterEqual(sleeps[0], flk_npc.MIN_REQUEST_INTERVAL)

    def test_compliance_floor_constant_is_consistent(self):
        # 三个 adapter 共用 0.1s 下限，避免 docs/COMPLIANCE.md §3 漂移
        self.assertEqual(court_gongbao.MIN_REQUEST_INTERVAL, 0.1)
        self.assertEqual(spp_gov_cn.MIN_REQUEST_INTERVAL, 0.1)
        self.assertEqual(flk_npc.MIN_REQUEST_INTERVAL, 0.1)


class CourtGongbaoFetchTests(unittest.TestCase):
    """court_gongbao adapter 的 search_list / fetch_detail / build_law_payload。

    采用 fixture HTML 完全离线，验证：
    - 列表页解析（30 行 + 分页）与客户端关键词过滤
    - 详情页 ``<div id="gb_content">`` 抽取与标题后缀剥离
    - HTML → 纯文本 → ``cleaning.parse_articles_from_text`` 端到端
    - level 启发式（sfjs / sfwj 纪要 / sfwj 批复 / sfwj 其它）
    - source_hash 在内容稳定时保持一致
    """

    LIST_FIXTURE = """
<html><body>
<div id="grid">
  <ul id="datas">
    <li><span>
        <a href="/Details/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.html" target="_blank">最高人民法院　关于审理示例案件适用法律若干问题的解释</a>
        <lable>2026年03期</lable>
    </span></li>
    <li><span>
        <a href="/Details/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbb.html" target="_blank">最高人民法院　关于审理劳动争议案件适用法律问题的解释（二）</a>
        <lable>2026年02期</lable>
    </span></li>
    <li><span>
        <a href="/Details/cccccccccccccccccccccccccccccc.html" target="_blank">最高人民法院　关于其它问题的批复</a>
        <lable>2025年05期</lable>
    </span></li>
  </ul>
</div>
<div class="page">
  <a href="/ArticleList.html?serial_no=sfjs&amp;page=2">下一页</a>
  <a href="/ArticleList.html?serial_no=sfjs&amp;page=31">尾页</a>
</div>
<script>
  // 列表页内联脚本：注入"共 N 条"摘要的真实形态
  var totalCount = '903';
</script>
</body></html>
"""

    DETAIL_FIXTURE = """
<html><head><title>最高人民法院　关于审理示例案件适用法律若干问题的解释 - 中华人民共和国最高人民法院公报</title></head>
<body>
<div class="main">
<div class="online_box">
<div class="content_box" id="gb_content">
    <p><span>中华人民共和国最高人民法院</span></p>
    <p><span>公告</span></p>
    <p><span>《最高人民法院关于审理示例案件适用法律若干问题的解释》已于2025年12月13日公布，自2026年2月1日起施行。</span></p>
    <p><span>2026年1月19日</span></p>
    <p><span>最高人民法院</span></p>
    <p><strong><span>关于审理示例案件适用法律若干问题的解释</span></strong></p>
    <p><span>法释〔2026〕5号</span></p>
    <p><span>为正确审理示例案件，根据《民法典》制定本解释。</span></p>
    <p><strong><span>第一条</span></strong><span>　示例正文一。</span></p>
    <p><strong><span>第二条</span></strong><span>　示例正文二之第一款。</span></p>
    <p><span>这是第二条第二款的续段内容。</span></p>
    <p><strong><span>第三条</span></strong><span>　本解释自公布之日起施行。</span></p>
</div>
</div>
</div>
</body></html>
"""

    def _fake_list_result(self):
        return court_gongbao.FetchResult(
            url="http://gongbao.court.gov.cn/ArticleList.html?serial_no=sfjs",
            status_code=200,
            headers={},
            text=self.LIST_FIXTURE,
        )

    def _fake_detail_result(self):
        return court_gongbao.FetchResult(
            url="http://gongbao.court.gov.cn/Details/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.html",
            status_code=200,
            headers={"Last-Modified": "Tue, 01 May 2026 00:00:00 GMT"},
            text=self.DETAIL_FIXTURE,
        )

    def test_search_list_parses_rows_and_pagination(self):
        adapter = court_gongbao.CourtGongbaoAdapter()
        with patch.object(court_gongbao, "_fetch_text", return_value=self._fake_list_result()):
            result = adapter.search_list(serial_no="sfjs", page=1)
        self.assertEqual(result["serial_no"], "sfjs")
        self.assertEqual(result["label"], "司法解释")
        self.assertEqual(len(result["rows"]), 3)
        self.assertEqual(result["total_pages"], 31)
        first = result["rows"][0]
        self.assertEqual(first["detail_id"], "a" * 30)
        self.assertEqual(first["serial_no"], "sfjs")
        self.assertEqual(first["status"], "current")
        self.assertIn("示例案件", first["title"])
        self.assertEqual(first["issue"], "2026年03期")
        self.assertTrue(first["url"].endswith(f"/Details/{'a' * 30}.html"))

    def test_search_list_filters_by_query_substring(self):
        adapter = court_gongbao.CourtGongbaoAdapter()
        with patch.object(court_gongbao, "_fetch_text", return_value=self._fake_list_result()):
            result = adapter.search_list("劳动争议", serial_no="sfjs")
        self.assertEqual(len(result["rows"]), 1)
        self.assertIn("劳动争议", result["rows"][0]["title"])

    def test_search_list_query_handles_joint_publisher_separator_variants(self):
        adapter = court_gongbao.CourtGongbaoAdapter()
        body = """
<html><body>
<ul id="datas">
  <li><span>
    <a href="/Details/ffffffffffffffffffffffffffffff.html">
      最高人民法院最高人民检察院关于办理盗窃刑事案件适用法律若干问题的解释
    </a>
    <lable>2013年11期</lable>
  </span></li>
</ul>
</body></html>
"""
        result = court_gongbao.FetchResult(
            url="http://gongbao.court.gov.cn/ArticleList.html?serial_no=sfjs",
            status_code=200,
            headers={},
            text=body,
        )
        with patch.object(court_gongbao, "_fetch_text", return_value=result):
            found = adapter.search_list(
                "最高人民法院 最高人民检察院关于办理盗窃刑事案件",
                serial_no="sfjs",
            )
        self.assertEqual(len(found["rows"]), 1)
        self.assertIn("盗窃刑事案件", found["rows"][0]["title"])

    def test_search_list_rejects_unknown_serial_no(self):
        adapter = court_gongbao.CourtGongbaoAdapter()
        with self.assertRaises(ValueError) as ctx:
            adapter.search_list(serial_no="nope")
        # 错误消息应列出已知 serial 便于 agent 纠错
        self.assertIn("sfjs", str(ctx.exception))
        self.assertIn("sfwj", str(ctx.exception))

    def test_fetch_detail_extracts_title_and_content(self):
        adapter = court_gongbao.CourtGongbaoAdapter()
        with patch.object(court_gongbao, "_fetch_text", return_value=self._fake_detail_result()):
            detail = adapter.fetch_detail("a" * 30)
        self.assertEqual(detail["detail_id"], "a" * 30)
        # 公报站标题统一后缀已剥离
        self.assertEqual(
            detail["title"],
            "最高人民法院 关于审理示例案件适用法律若干问题的解释",
        )
        # raw_title 保留原文便于排查
        self.assertIn("中华人民共和国最高人民法院公报", detail["raw_title"])
        self.assertIn("第一条", detail["content_html"])
        self.assertIn("法释〔2026〕5号", detail["content_html"])
        self.assertEqual(detail["source_last_modified"], "Tue, 01 May 2026 00:00:00 GMT")

    def test_fetch_detail_rejects_invalid_id(self):
        adapter = court_gongbao.CourtGongbaoAdapter()
        with self.assertRaises(ValueError):
            adapter.fetch_detail("not-a-hash")

    def test_build_law_payload_emits_canonical_articles(self):
        adapter = court_gongbao.CourtGongbaoAdapter()
        with patch.object(court_gongbao, "_fetch_text", return_value=self._fake_detail_result()):
            payload = adapter.build_law_payload(
                "a" * 30,
                search_row={"serial_no": "sfjs"},
            )
        self.assertEqual(payload["id"], f"court_gongbao:{'a' * 30}")
        self.assertEqual(payload["level"], "judicial_interpretation")
        self.assertEqual(payload["status"], "current")
        self.assertEqual(payload["source_name"], "gongbao.court.gov.cn")
        self.assertEqual(payload["issuing_body"], "最高人民法院")
        self.assertEqual(payload["document_number"], "法释〔2026〕5号")
        # 三条条文（第一/二/三条），其中第二条有跨段续行需合并
        self.assertEqual(len(payload["articles"]), 3)
        numbers = [a["number_display"] for a in payload["articles"]]
        self.assertEqual(numbers, ["第一条", "第二条", "第三条"])
        second = payload["articles"][1]
        self.assertIn("第一款", second["text"])
        self.assertIn("续段内容", second["text"])

    def test_build_law_payload_routes_meeting_minutes_for_sfwj(self):
        adapter = court_gongbao.CourtGongbaoAdapter()
        # 标题里有"纪要" → judicial_meeting_minutes（九民纪要场景）
        meeting_html = self.DETAIL_FIXTURE.replace(
            "关于审理示例案件适用法律若干问题的解释",
            "全国法院民商事审判工作会议纪要",
        )
        result = court_gongbao.FetchResult(
            url=self._fake_detail_result().url,
            status_code=200,
            headers={},
            text=meeting_html,
        )
        with patch.object(court_gongbao, "_fetch_text", return_value=result):
            payload = adapter.build_law_payload(
                "a" * 30,
                search_row={"serial_no": "sfwj"},
            )
        self.assertEqual(payload["level"], "judicial_meeting_minutes")

    def test_build_law_payload_routes_batch_reply_for_sfwj_pifu(self):
        adapter = court_gongbao.CourtGongbaoAdapter()
        # 标题里有"批复" + serial=sfwj → judicial_interpretation
        reply_html = self.DETAIL_FIXTURE.replace(
            "关于审理示例案件适用法律若干问题的解释",
            "关于示例问题的批复",
        )
        result = court_gongbao.FetchResult(
            url=self._fake_detail_result().url,
            status_code=200,
            headers={},
            text=reply_html,
        )
        with patch.object(court_gongbao, "_fetch_text", return_value=result):
            payload = adapter.build_law_payload(
                "a" * 30,
                search_row={"serial_no": "sfwj"},
            )
        self.assertEqual(payload["level"], "judicial_interpretation")

    def test_build_law_payload_can_infer_level_without_search_row(self):
        adapter = court_gongbao.CourtGongbaoAdapter()
        with patch.object(court_gongbao, "_fetch_text", return_value=self._fake_detail_result()):
            payload = adapter.build_law_payload("a" * 30)
        # 直接按 detail_id fetch 时没有 serial_no，也不能把司法解释落到 other。
        self.assertEqual(payload["level"], "judicial_interpretation")

    def test_html_to_text_strips_tags_and_collapses_blank_lines(self):
        text = court_gongbao._html_to_text(
            "<p>A</p>\n\n<p>　B</p><br/>C<div>D</div>"
        )
        # 段落间最多一个空行；全角空格 → 半角；C 与 D 因为 div 闭合而分行
        self.assertIn("A", text)
        self.assertIn("B", text)
        self.assertIn("C", text)
        self.assertIn("D", text)
        self.assertNotIn("　", text)
        self.assertNotIn("\n\n\n", text)

    def test_extract_document_number_recognizes_fashi(self):
        self.assertEqual(
            court_gongbao._extract_document_number("法释〔2026〕5号 正文"),
            "法释〔2026〕5号",
        )
        self.assertEqual(
            court_gongbao._extract_document_number("法发〔2019〕254号  正文"),
            "法发〔2019〕254号",
        )
        self.assertIsNone(court_gongbao._extract_document_number("没有文号的正文"))

    def test_source_hash_is_stable_across_calls(self):
        adapter = court_gongbao.CourtGongbaoAdapter()
        with patch.object(court_gongbao, "_fetch_text", return_value=self._fake_detail_result()):
            h1 = adapter.source_hash("a" * 30)
            h2 = adapter.source_hash("a" * 30)
        self.assertEqual(h1, h2)
        self.assertEqual(len(h1), 64)

    def test_search_list_parses_total_count_from_inline_script(self):
        adapter = court_gongbao.CourtGongbaoAdapter()
        with patch.object(court_gongbao, "_fetch_text", return_value=self._fake_list_result()):
            result = adapter.search_list(serial_no="sfjs", page=1)
        # `var totalCount = '903';` 应该被识别成 total_count=903，便于
        # agent 早判跨页搜索代价（903/30 ≈ 31 页）
        self.assertEqual(result["total_count"], 903)

    def test_search_list_page_one_uses_get(self):
        """page=1 走 GET，能拿到完整页 footer + totalCount。

        若改成 POST，jQuery unobtrusive ajax 只返回 #grid 片段，pagination
        和 ``var totalCount`` 都消失。
        """

        adapter = court_gongbao.CourtGongbaoAdapter()
        captured: dict = {}

        def fake_fetch(url, *, data=None, timeout=None):
            captured["url"] = url
            captured["data"] = data
            return self._fake_list_result()

        with patch.object(court_gongbao, "_fetch_text", side_effect=fake_fetch):
            adapter.search_list(serial_no="sfjs", page=1)
        self.assertIsNone(captured["data"])
        self.assertIn("serial_no=sfjs", captured["url"])

    def test_search_list_page_two_uses_post_with_form_body(self):
        """page>=2 必须 POST + form body，否则被 ASP.NET URLScan 拦截。

        实测过：GET ``?serial_no=sfwj&page=2`` 会返回 ``/Rejected-By-UrlScan``
        404。这个测试守住协议契约，防止有人改回 GET 翻页。
        """

        adapter = court_gongbao.CourtGongbaoAdapter()
        captured: dict = {}

        def fake_fetch(url, *, data=None, timeout=None):
            captured["url"] = url
            captured["data"] = data
            return self._fake_list_result()

        with patch.object(court_gongbao, "_fetch_text", side_effect=fake_fetch):
            adapter.search_list(serial_no="sfwj", page=2)
        self.assertIsNotNone(captured["data"])
        # form body 形式：serial_no=sfwj&page=2
        body = captured["data"].decode("utf-8")
        self.assertIn("serial_no=sfwj", body)
        self.assertIn("page=2", body)
        # POST 端点不带 query string，避免 URLScan 拦截
        self.assertTrue(captured["url"].endswith("/ArticleList.html"))

    def test_search_all_pages_dedups_and_walks_pages(self):
        """跨页搜走 search_list 多次，按 detail_id 去重。"""

        adapter = court_gongbao.CourtGongbaoAdapter()

        # page=1 返回 a/b/c；page=2 返回 b（重复）+ d/e；page=3 空。
        page_1 = self._fake_list_result()
        page_2 = court_gongbao.FetchResult(
            url=page_1.url,
            status_code=200,
            headers={},
            text="""
<ul id="datas">
  <li><span><a href="/Details/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbb.html">b 重复</a><lable>X</lable></span></li>
  <li><span><a href="/Details/dddddddddddddddddddddddddddddd.html">关于 D 的纪要</a><lable>Y</lable></span></li>
  <li><span><a href="/Details/eeeeeeeeeeeeeeeeeeeeeeeeeeeeee.html">关于 E 的批复</a><lable>Z</lable></span></li>
</ul>
""",
        )
        responses = [page_1, page_2]

        def fake_fetch(url, *, data=None, timeout=None):
            return responses.pop(0)

        with patch.object(court_gongbao, "_fetch_text", side_effect=fake_fetch):
            res = adapter.search_all_pages("批复", serial_no="sfjs", max_pages=2)
        # page1 命中 c (批复)；page2 命中 e (批复)；总 2 条，b 在 query 过滤里被剔除
        ids = [row["detail_id"] for row in res["rows"]]
        self.assertEqual(set(ids), {"c" * 30, "e" * 30})
        self.assertEqual(res["scanned_pages"], 2)
        self.assertEqual(res["query"], "批复")

    def test_search_all_pages_rejects_empty_query(self):
        adapter = court_gongbao.CourtGongbaoAdapter()
        with self.assertRaises(ValueError):
            adapter.search_all_pages("   ")

    def test_short_title_prefers_alias_for_long_titles(self):
        """合同编通则解释 27 字超长，优先用 alias 第一个命中作为 short_title。"""

        title = "最高人民法院关于适用《中华人民共和国民法典》合同编通则若干问题的解释"
        self.assertEqual(court_gongbao._infer_short_title(title), "合同编通则解释")
        # 也覆盖侵权责任编（一）→ 应得"侵权责任编解释一"
        title2 = "最高人民法院关于适用《中华人民共和国民法典》侵权责任编的解释（一）"
        self.assertEqual(
            court_gongbao._infer_short_title(title2), "侵权责任编解释一"
        )

    def test_preferred_short_title_is_first_alias(self):
        """显式契约：``preferred_short_title`` == ``common_law_aliases[0]``。

        改动 ``common_law_aliases`` 顺序前必须更新本测试 + adapter 调用方。
        详见 ``docs/FETCH_LAYER_SPEC.md`` §1。
        """

        from chinalaw.aliases import (
            common_law_aliases,
            preferred_short_title,
        )

        title = "最高人民法院关于适用《公司法》若干问题的规定（一）"
        aliases = common_law_aliases(title)
        self.assertEqual(preferred_short_title(title), aliases[0])
        self.assertEqual(preferred_short_title(title), "公司法解释一")

        # 法律本身也派生稳定短称，供清洗后的 fixture 直接 resolve。
        self.assertEqual(preferred_short_title("中华人民共和国民法典"), "民法典")
        self.assertIsNone(preferred_short_title(""))
        self.assertIsNone(preferred_short_title(None))


class CourtGongbaoCrossSearchTests(unittest.TestCase):
    """``cross_search`` 跨栏目搜索：默认 sfjs+sfwj，按 detail_id 全局去重。"""

    SFJS_PAGE = """
<html><body>
<ul id="datas">
  <li><span>
      <a href="/Details/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.html">最高人民法院　关于破产纪要（解释版）</a>
      <lable>2026年03期</lable>
  </span></li>
  <li><span>
      <a href="/Details/cccccccccccccccccccccccccccccc.html">最高人民法院　关于其它问题的批复</a>
      <lable>2025年05期</lable>
  </span></li>
</ul>
<script>var totalCount = '5';</script>
</body></html>
"""

    SFWJ_PAGE = """
<html><body>
<ul id="datas">
  <li><span>
      <a href="/Details/cccccccccccccccccccccccccccccc.html">最高人民法院　关于其它问题的批复</a>
      <lable>2025年05期</lable>
  </span></li>
  <li><span>
      <a href="/Details/dddddddddddddddddddddddddddddd.html">全国法院破产审判工作会议纪要</a>
      <lable>2018年08期</lable>
  </span></li>
</ul>
<script>var totalCount = '6';</script>
</body></html>
"""

    def _result(self, body):
        return court_gongbao.FetchResult(
            url="http://gongbao.court.gov.cn/ArticleList.html",
            status_code=200,
            headers={},
            text=body,
        )

    def test_cross_search_dedups_across_serials(self):
        """同一 detail_id 在多个 serial 出现时只保留一次。"""

        adapter = court_gongbao.CourtGongbaoAdapter(request_interval=0)

        # 顺序：sfjs page=1 → sfwj page=1（每个 serial 默认 max_pages=5；
        # 由于 SFJS_PAGE 没有分页链接，total_pages=1，只翻 1 页）
        responses = [self._result(self.SFJS_PAGE), self._result(self.SFWJ_PAGE)]

        def fake_fetch(url, *, data=None, timeout=None):
            return responses.pop(0)

        with patch.object(court_gongbao, "_fetch_text", side_effect=fake_fetch):
            result = adapter.cross_search("纪要")

        # 客户端 substring 过滤：只命中 SFJS 第一条（"破产纪要"）和 SFWJ 第二条
        # （"会议纪要"），共 2 条。c 重复但不在 query 里被剔除
        ids = [row["detail_id"] for row in result["rows"]]
        self.assertEqual(set(ids), {"a" * 30, "d" * 30})
        self.assertEqual(result["serials"], ["sfjs", "sfwj"])
        self.assertEqual(len(result["per_serial"]), 2)
        self.assertEqual(result["query"], "纪要")

    def test_cross_search_per_serial_breakdown(self):
        adapter = court_gongbao.CourtGongbaoAdapter(request_interval=0)
        responses = [self._result(self.SFJS_PAGE), self._result(self.SFWJ_PAGE)]
        with patch.object(
            court_gongbao, "_fetch_text", side_effect=lambda *a, **kw: responses.pop(0)
        ):
            result = adapter.cross_search("批复")
        sfjs = next(s for s in result["per_serial"] if s["serial_no"] == "sfjs")
        sfwj = next(s for s in result["per_serial"] if s["serial_no"] == "sfwj")
        self.assertEqual(sfjs["label"], "司法解释")
        self.assertEqual(sfwj["label"], "司法文件")
        # 两个 serial 都含批复，但 sfwj 的批复因 detail_id 已在 sfjs 见过，
        # 在跨 serial 去重后 matched=0
        self.assertEqual(sfjs["matched"], 1)
        self.assertEqual(sfwj["matched"], 0)

    def test_cross_search_rejects_empty_query(self):
        adapter = court_gongbao.CourtGongbaoAdapter()
        with self.assertRaises(ValueError):
            adapter.cross_search("   ")

    def test_cross_search_rejects_unknown_serial(self):
        adapter = court_gongbao.CourtGongbaoAdapter()
        with self.assertRaises(ValueError) as ctx:
            adapter.cross_search("纪要", serials=("not_a_serial",))
        self.assertIn("not_a_serial", str(ctx.exception))

    def test_cross_search_caps_max_pages_to_total(self):
        """max_pages_per_serial 超过 total_pages 时不会越界翻页。"""

        adapter = court_gongbao.CourtGongbaoAdapter(request_interval=0)
        responses = [self._result(self.SFJS_PAGE), self._result(self.SFWJ_PAGE)]
        # 2 个 serial × 1 页 = 2 个请求；max_pages_per_serial=99 也只翻 1 页
        with patch.object(
            court_gongbao, "_fetch_text", side_effect=lambda *a, **kw: responses.pop(0)
        ):
            result = adapter.cross_search("纪要", max_pages_per_serial=99)
        self.assertEqual(result["max_pages_per_serial"], 99)
        for serial in result["per_serial"]:
            self.assertEqual(serial["scanned_pages"], 1)

    def test_module_level_cross_search_helper(self):
        """``court_gongbao.cross_search(query)`` 模块级 helper 与 default_adapter 等价。"""

        original = court_gongbao.default_adapter
        adapter = court_gongbao.CourtGongbaoAdapter(request_interval=0)
        responses = [self._result(self.SFJS_PAGE), self._result(self.SFWJ_PAGE)]
        try:
            court_gongbao.default_adapter = adapter
            with patch.object(
                court_gongbao,
                "_fetch_text",
                side_effect=lambda *a, **kw: responses.pop(0),
            ):
                result = court_gongbao.cross_search("纪要")
            self.assertIn("rows", result)
            self.assertEqual(result["source"], "court_gongbao")
        finally:
            court_gongbao.default_adapter = original


class DocumentNumberIndexTests(unittest.TestCase):
    """文号反查 schema + fetch.py 索引读写 + fetch_law early return。"""

    def test_latest_schema_creates_document_number_index_table(self):
        from chinalaw.db import connect, current_version, migrate
        from chinalaw.schema import SCHEMA_VERSION

        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "doc.db"
            with connect(db_path) as conn:
                migrate(conn)
                self.assertEqual(current_version(conn), SCHEMA_VERSION)
                row = conn.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='table' AND name='document_number_index'"
                ).fetchone()
                self.assertIsNotNone(row)
                # 建立索引
                idx = conn.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='index' AND name='idx_document_number_index_doc'"
                ).fetchone()
                self.assertIsNotNone(idx)

    def test_looks_like_document_number_accepts_common_forms(self):
        from chinalaw.fetch import _looks_like_document_number

        # 真实文号
        self.assertTrue(_looks_like_document_number("法释〔2023〕13号"))
        self.assertTrue(_looks_like_document_number("法发〔2019〕254号"))
        self.assertTrue(_looks_like_document_number("中办发〔2020〕5号"))
        self.assertTrue(_looks_like_document_number("国发〔2024〕10号"))
        self.assertTrue(_looks_like_document_number("高检发释字〔2017〕7号"))
        # 容忍空格
        self.assertTrue(_looks_like_document_number("法释〔2023〕 13号"))
        self.assertTrue(_looks_like_document_number(" 法释〔2023〕13号 "))

    def test_looks_like_document_number_rejects_non_doc_no(self):
        from chinalaw.fetch import _looks_like_document_number

        # 普通法律名
        self.assertFalse(_looks_like_document_number("民法典"))
        self.assertFalse(_looks_like_document_number("劳动争议解释"))
        # 半角方括号不接受
        self.assertFalse(_looks_like_document_number("法释[2023]13号"))
        # 形似但缺少"号"
        self.assertFalse(_looks_like_document_number("法释〔2023〕13"))
        # 空 / None
        self.assertFalse(_looks_like_document_number(""))
        self.assertFalse(_looks_like_document_number(None))

    def test_normalize_document_number_folds_whitespace(self):
        from chinalaw.fetch import _normalize_document_number

        self.assertEqual(
            _normalize_document_number("法释〔2023〕 13号"),
            "法释〔2023〕13号",
        )
        self.assertEqual(
            _normalize_document_number(" 法释〔2023〕13号 "),
            "法释〔2023〕13号",
        )

    def test_index_document_number_writes_row(self):
        """``_index_document_number`` 把 payload 的 document_number 写入索引。"""

        from chinalaw.db import connect, migrate
        from chinalaw.fetch import _index_document_number

        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "doc.db"
            with connect(db_path) as conn:
                migrate(conn)
                payload = {
                    "id": "court_gongbao:abc",
                    "title": "示例解释",
                    "document_number": "法释〔2026〕5号",
                }
                _index_document_number(
                    conn, payload, "court_gongbao", "abc" + "0" * 27
                )
                row = conn.execute(
                    "SELECT document_number, source, source_id, law_id, title "
                    "FROM document_number_index"
                ).fetchone()
                self.assertEqual(row["document_number"], "法释〔2026〕5号")
                self.assertEqual(row["source"], "court_gongbao")
                self.assertEqual(row["source_id"], "abc" + "0" * 27)
                self.assertEqual(row["law_id"], "court_gongbao:abc")
                self.assertEqual(row["title"], "示例解释")

    def test_index_document_number_skips_when_no_doc_no(self):
        """payload 没 document_number 时静默跳过，不抛错也不写空 row。"""

        from chinalaw.db import connect, migrate
        from chinalaw.fetch import _index_document_number

        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "doc.db"
            with connect(db_path) as conn:
                migrate(conn)
                payload = {"id": "x", "title": "无文号", "document_number": ""}
                _index_document_number(conn, payload, "court_gongbao", "abc")
                count = conn.execute(
                    "SELECT COUNT(*) FROM document_number_index"
                ).fetchone()[0]
                self.assertEqual(count, 0)

    def test_index_document_number_upserts_on_conflict(self):
        """同一 (document_number, source) 重复入库时 upsert 最新 source_id / law_id。"""

        from chinalaw.db import connect, migrate
        from chinalaw.fetch import _index_document_number

        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "doc.db"
            with connect(db_path) as conn:
                migrate(conn)
                _index_document_number(
                    conn,
                    {
                        "id": "court_gongbao:old",
                        "title": "旧标题",
                        "document_number": "法释〔2026〕5号",
                    },
                    "court_gongbao",
                    "old_id",
                )
                _index_document_number(
                    conn,
                    {
                        "id": "court_gongbao:new",
                        "title": "新标题",
                        "document_number": "法释〔2026〕5号",
                    },
                    "court_gongbao",
                    "new_id",
                )
                row = conn.execute(
                    "SELECT source_id, law_id, title FROM document_number_index"
                ).fetchone()
                self.assertEqual(row["source_id"], "new_id")
                self.assertEqual(row["law_id"], "court_gongbao:new")
                self.assertEqual(row["title"], "新标题")

    def test_load_law_from_dict_indexes_court_gongbao_document_number(self):
        """fixture / sync 走 loader 入库时也要写文号索引，不能只支持 fetch。"""

        from chinalaw.db import connect, migrate

        detail_id = "a" * 30
        payload = {
            "id": f"court_gongbao:{detail_id}",
            "title": "最高人民法院关于示例问题的解释",
            "short_title": "示例解释",
            "aliases": [],
            "level": "judicial_interpretation",
            "issuing_body": "最高人民法院",
            "document_number": "法释〔2026〕5号",
            "released_at": "2026-01-01",
            "effective_at": "2026-01-01",
            "repealed_at": None,
            "status": "current",
            "source_url": f"http://gongbao.court.gov.cn/Details/{detail_id}.html",
            "source_name": "gongbao.court.gov.cn",
            "source_checked_at": "2026-05-01T00:00:00+00:00",
            "articles": [],
        }
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "doc.db"
            with connect(db_path) as conn:
                migrate(conn)
                loader.load_law_from_dict(conn, payload)
                row = conn.execute(
                    "SELECT source, source_id, law_id FROM document_number_index "
                    "WHERE document_number = ?",
                    ("法释〔2026〕5号",),
                ).fetchone()

        self.assertIsNotNone(row)
        self.assertEqual(row["source"], "court_gongbao")
        self.assertEqual(row["source_id"], detail_id)
        self.assertEqual(row["law_id"], f"court_gongbao:{detail_id}")

    def test_load_law_from_dict_indexes_flk_document_number_from_source_url(self):
        """FLK fixture / sync 入库同样从 source_url 的 id 参数提取 bbbs。"""

        from chinalaw.db import connect, migrate

        bbbs = "f" * 32
        payload = {
            "id": "flk-demo-law",
            "title": "示例法规",
            "short_title": "示例法规",
            "aliases": [],
            "level": "admin_regulation",
            "issuing_body": "国务院",
            "document_number": "国发〔2026〕1号",
            "released_at": "2026-01-01",
            "effective_at": "2026-01-01",
            "repealed_at": None,
            "status": "current",
            "source_url": f"https://flk.npc.gov.cn/detail2.html?id={bbbs}",
            "source_name": "flk.npc.gov.cn",
            "source_checked_at": "2026-05-01T00:00:00+00:00",
            "articles": [],
        }
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "doc.db"
            with connect(db_path) as conn:
                migrate(conn)
                loader.load_law_from_dict(conn, payload)
                row = conn.execute(
                    "SELECT source, source_id, law_id FROM document_number_index "
                    "WHERE document_number = ?",
                    ("国发〔2026〕1号",),
                ).fetchone()

        self.assertIsNotNone(row)
        self.assertEqual(row["source"], "flk_npc")
        self.assertEqual(row["source_id"], bbbs)
        self.assertEqual(row["law_id"], "flk-demo-law")

    def test_lookup_document_number_hint_returns_none_for_non_doc_no(self):
        from chinalaw.fetch import _lookup_document_number_hint

        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "doc.db"
            # DB 不存在也直接 return None
            self.assertIsNone(
                _lookup_document_number_hint(db_path, "民法典", "court_gongbao")
            )

    def test_lookup_document_number_hint_returns_none_when_db_missing(self):
        from chinalaw.fetch import _lookup_document_number_hint

        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "missing.db"
            # 文号格式正确但 DB 不存在：返回 None
            result = _lookup_document_number_hint(
                db_path, "法释〔2023〕13号", "court_gongbao"
            )
            self.assertIsNone(result)

    def test_lookup_document_number_hint_hits_indexed_row(self):
        """已索引的文号能被 lookup 命中并返回 hint 形态。"""

        from chinalaw.db import connect, migrate
        from chinalaw.fetch import (
            _index_document_number,
            _lookup_document_number_hint,
        )

        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "doc.db"
            with connect(db_path) as conn:
                migrate(conn)
                _index_document_number(
                    conn,
                    {
                        "id": "court_gongbao:abc",
                        "title": "破产纪要",
                        "document_number": "法发〔2018〕53号",
                    },
                    "court_gongbao",
                    "abc" + "0" * 27,
                )
            hint = _lookup_document_number_hint(
                db_path, "法发〔2018〕53号", "court_gongbao"
            )
            self.assertIsNotNone(hint)
            self.assertEqual(hint["id"], "abc" + "0" * 27)
            self.assertEqual(hint["detail_id"], "abc" + "0" * 27)
            self.assertEqual(hint["title"], "破产纪要")
            self.assertEqual(hint["document_number_resolved"], "法发〔2018〕53号")

    def test_lookup_document_number_hint_isolates_by_source(self):
        """同文号在不同 source 下是独立的；查 court_gongbao 不会拿到 flk row。"""

        from chinalaw.db import connect, migrate
        from chinalaw.fetch import (
            _index_document_number,
            _lookup_document_number_hint,
        )

        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "doc.db"
            with connect(db_path) as conn:
                migrate(conn)
                _index_document_number(
                    conn,
                    {
                        "id": "flk:xxx",
                        "title": "示例",
                        "document_number": "法释〔2023〕13号",
                    },
                    "flk_npc",
                    "flk_bbbs_xxx",
                )
            hint = _lookup_document_number_hint(
                db_path, "法释〔2023〕13号", "court_gongbao"
            )
            self.assertIsNone(hint)
            # 但查 flk_npc 能命中
            hint_flk = _lookup_document_number_hint(
                db_path, "法释〔2023〕13号", "flk_npc"
            )
            self.assertIsNotNone(hint_flk)


class FetchLawDocumentNumberRoutingTests(unittest.TestCase):
    """``fetch_law("法释〔2023〕13号")`` 路径：命中文号索引时绕过远程 search_list。"""

    DETAIL_FIXTURE = """
<html><head><title>示例解释 - 中华人民共和国最高人民法院公报</title></head>
<body>
<div class="online_box">
<div class="content_box" id="gb_content">
    <p><strong><span>第一条</span></strong><span>　示例正文一。</span></p>
    <p><strong><span>第二条</span></strong><span>　这是第二条第二款的续段内容。</span></p>
</div>
</div>
</body></html>
"""

    def _seed_index(self, db_path: Path, detail_id: str, doc_no: str) -> None:
        from chinalaw.db import connect, migrate
        from chinalaw.fetch import _index_document_number

        with connect(db_path) as conn:
            migrate(conn)
            _index_document_number(
                conn,
                {
                    "id": f"court_gongbao:{detail_id}",
                    "title": "示例解释",
                    "document_number": doc_no,
                },
                "court_gongbao",
                detail_id,
            )

    def test_fetch_law_by_document_number_skips_remote_search(self):
        """文号已索引时 fetch_law 不调 adapter.search_list / cross_search。"""

        from chinalaw.fetch import fetch_law

        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "fetch.db"
            detail_id = "f" * 30
            self._seed_index(db_path, detail_id, "法释〔2023〕13号")

            search_calls: list = []
            cross_calls: list = []

            def fake_search_list(*args, **kwargs):
                search_calls.append((args, kwargs))
                raise AssertionError("search_list should not be called")

            def fake_cross_search(*args, **kwargs):
                cross_calls.append((args, kwargs))
                raise AssertionError("cross_search should not be called")

            detail_result = court_gongbao.FetchResult(
                url=f"http://gongbao.court.gov.cn/Details/{detail_id}.html",
                status_code=200,
                headers={},
                text=self.DETAIL_FIXTURE,
            )
            with patch.object(
                court_gongbao.default_adapter,
                "search_list",
                side_effect=fake_search_list,
            ), patch.object(
                court_gongbao.default_adapter,
                "cross_search",
                side_effect=fake_cross_search,
            ), patch.object(
                court_gongbao,
                "_fetch_text",
                return_value=detail_result,
            ):
                result = fetch_law(
                    db_path,
                    "法释〔2023〕13号",
                    source="court_gongbao",
                    dry_run=True,
                )

        self.assertEqual(search_calls, [])
        self.assertEqual(cross_calls, [])
        self.assertEqual(result["matched_id"], detail_id)
        # candidate 携带 ``document_number_resolved`` 标记，便于 agent 区分来源
        self.assertEqual(
            result["candidates"][0].get("document_number_resolved"),
            "法释〔2023〕13号",
        )

    def test_fetch_law_falls_back_to_cross_search_on_empty_search_list(self):
        """普通查询在 search_list（默认 sfjs）零结果时 fallback 到 cross_search。"""

        from chinalaw.fetch import fetch_law

        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "fetch.db"

            empty_search = {
                "source": "court_gongbao",
                "rows": [],
                "total_pages": 1,
                "total_count": 0,
            }
            cross_result = {
                "source": "court_gongbao",
                "rows": [
                    {
                        "detail_id": "d" * 30,
                        "serial_no": "sfwj",
                        "title": "全国法院破产审判工作会议纪要",
                        "issue": "2018年08期",
                        "url": "http://gongbao.court.gov.cn/Details/dddddddddddddddddddddddddddddd.html",
                        "status": "current",
                    }
                ],
                "per_serial": [],
            }
            detail_result = court_gongbao.FetchResult(
                url="http://gongbao.court.gov.cn/Details/dddddddddddddddddddddddddddddd.html",
                status_code=200,
                headers={},
                text=self.DETAIL_FIXTURE,
            )

            cross_calls: list = []

            def fake_cross(query, **kwargs):
                cross_calls.append((query, kwargs))
                return cross_result

            with patch.object(
                court_gongbao.default_adapter,
                "search_list",
                return_value=empty_search,
            ), patch.object(
                court_gongbao.default_adapter,
                "cross_search",
                side_effect=fake_cross,
            ), patch.object(
                court_gongbao,
                "_fetch_text",
                return_value=detail_result,
            ):
                result = fetch_law(
                    db_path,
                    "破产纪要",
                    source="court_gongbao",
                    dry_run=True,
                )

        # cross_search 被调用一次
        self.assertEqual(len(cross_calls), 1)
        self.assertEqual(cross_calls[0][0], "破产纪要")
        self.assertEqual(result["matched_id"], "d" * 30)

    def test_fetch_law_persist_writes_document_number_index(self):
        """正常入库流程 ``_persist`` 应顺手把 document_number 索引入库。"""

        from chinalaw.db import connect
        from chinalaw.fetch import fetch_law

        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "fetch.db"
            detail_id = "e" * 30

            search_result = {
                "source": "court_gongbao",
                "rows": [
                    {
                        "detail_id": detail_id,
                        "serial_no": "sfjs",
                        "title": "示例解释",
                        "issue": "2026年03期",
                        "url": f"http://gongbao.court.gov.cn/Details/{detail_id}.html",
                        "status": "current",
                    }
                ],
            }

            payload = {
                "id": f"court_gongbao:{detail_id}",
                "title": "示例解释",
                "short_title": "示例解释",
                "level": "judicial_interpretation",
                "status": "current",
                "source_url": f"http://gongbao.court.gov.cn/Details/{detail_id}.html",
                "source_name": "gongbao.court.gov.cn",
                "source_checked_at": "2026-05-01T00:00:00+00:00",
                "source_hash": "abc",
                "document_number": "法释〔2026〕5号",
                "articles": [
                    {
                        "number": "1",
                        "number_display": "第一条",
                        "text": "示例正文。",
                    }
                ],
            }

            with patch.object(
                court_gongbao.default_adapter,
                "search_list",
                return_value=search_result,
            ), patch.object(
                court_gongbao.default_adapter,
                "build_law_payload",
                return_value=payload,
            ):
                fetch_law(
                    db_path,
                    "示例解释",
                    source="court_gongbao",
                )

            with connect(db_path) as conn:
                row = conn.execute(
                    "SELECT document_number, source, source_id "
                    "FROM document_number_index"
                ).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row["document_number"], "法释〔2026〕5号")
            self.assertEqual(row["source"], "court_gongbao")
            self.assertEqual(row["source_id"], detail_id)


class CourtGongbaoVerifySourceTests(unittest.TestCase):
    """通过 sources.verify_source 跑端到端的 court_gongbao smoke（mock 网络）。"""

    def test_verify_source_smoke_with_court_gongbao_rows(self):
        # FakeAdapter 模拟 court_gongbao 的 row 字段（detail_id 而非 bbbs）
        class FakeAdapter:
            def probe(self):
                return {"status_code": 200, "page_shape": "ok"}

            def search_list(self, query, page_size=20):
                return {
                    "rows": [
                        {
                            "detail_id": "f" * 30,
                            "title": "最高人民法院 关于示例的解释",
                            "issue": "2026年03期",
                            "status": "current",
                        }
                    ]
                }

            def build_law_payload(self, primary_id, *, search_row=None):
                self.received_id = primary_id
                self.received_row = search_row
                return {
                    "id": f"court_gongbao:{primary_id}",
                    "title": "最高人民法院 关于示例的解释",
                    "short_title": "示例解释",
                    "level": "judicial_interpretation",
                    "status": "current",
                    "source_url": f"http://gongbao.court.gov.cn/Details/{primary_id}.html",
                    "source_hash": "abc",
                    "source_checked_at": "2026-05-01T00:00:00+00:00",
                    "articles": [
                        {
                            "number": "1",
                            "number_display": "第一条",
                            "text": "示例正文。",
                        }
                    ],
                }

        adapter = FakeAdapter()
        with patch("chinalaw.sources.get_source_adapter", return_value=adapter):
            report = sources.verify_source(
                "court_gongbao",
                query="示例",
                article="第一条",
            )

        self.assertTrue(report["ok"], report["steps"])
        # selected.id 是新增的通用主键；同时填充 detail_id / bbbs 兼容字段
        self.assertEqual(report["selected"]["id"], "f" * 30)
        self.assertEqual(report["selected"]["detail_id"], "f" * 30)
        self.assertEqual(report["selected"]["bbbs"], "f" * 30)
        # build_law_payload 接收到的 primary_id 是 _row_id 抽出的值
        self.assertEqual(adapter.received_id, "f" * 30)
        self.assertEqual(report["law"]["article_count"], 1)
        self.assertEqual(report["law"]["level"], "judicial_interpretation")
        self.assertEqual(report["article_match"]["number"], "1")

    def test_verifiable_sources_includes_public_fetch_sources(self):
        # 防止 CLI choices 漂移；ADR-0008 §3.2 显式承诺多源进入 verify。
        self.assertIn("flk_npc", sources.VERIFIABLE_SOURCES)
        self.assertIn("court_gongbao", sources.VERIFIABLE_SOURCES)
        self.assertIn("court_main", sources.VERIFIABLE_SOURCES)
        self.assertIn("spp_gov_cn", sources.VERIFIABLE_SOURCES)

    def test_row_id_prefers_bbbs_then_detail_id_then_id(self):
        self.assertEqual(sources._row_id({"bbbs": "B", "detail_id": "D"}), "B")
        self.assertEqual(sources._row_id({"detail_id": "D", "id": "I"}), "D")
        self.assertEqual(sources._row_id({"id": "I"}), "I")
        self.assertIsNone(sources._row_id({}))
        self.assertIsNone(sources._row_id(None))


class SppGovCnFetchTests(unittest.TestCase):
    """spp_gov_cn adapter 的 search_list / fetch_detail / build_law_payload。

    采用离线 fixture HTML 验证：
    - 列表行解析（``<li><a href><span>YYYY-MM-DD</span></li>``）与客户端关键词过滤
    - detail_id 规整（去 leading /、去 .shtml、剥 fragment、折叠双斜杠）
    - 详情页 ``<div id="fontzoom">`` 抽取与 ``<title>`` 后缀剥离
    - HTML → 纯文本 → cleaning ``第N条`` 切分
    - level / issuing_body / document_number 启发式
    - source_hash 在内容稳定时保持一致
    """

    LIST_FIXTURE = """
<html><body>
<div class="list">
  <ul>
    <li><a href="https://www.spp.gov.cn/xwfbh/wsfbt/202501/t20250116_679579.shtml#1" target="_blank"  >最高人民法院 最高人民检察院关于办理袭警刑事案件适用法律若干问题的解释</a><span>2025-01-16</span></li>
    <li><a href="https://www.spp.gov.cn//xwfbh/wsfbt/202504/t20250424_693977.shtml" target="_blank"  >最高人民法院  最高人民检察院关于办理侵犯知识产权刑事案件适用法律若干问题的解释</a><span>2025-04-24</span></li>
    <li><a href="/spp/sfjs/201802/t20180201_363640.shtml" target="_blank"  >两高关于办理刑事赔偿案件适用法律若干问题的解释</a><span>2018-02-01</span></li>
  </ul>
</div>
</body></html>
"""

    DETAIL_FIXTURE = """
<html><head>
<meta charset="UTF-8">
<title>
        最高人民法院 最高人民检察院关于办理袭警刑事案件适用法律若干问题的解释_中华人民共和国最高人民检察院
</title>
</head>
<body>
<div class="wsfbh_detail_con">
<div id="fontzoom">
    <h2><span class="fs28">最高人民法院&ensp; 最高人民检察院</span><span class="fs28">关于办理袭警刑事案件适用法律若干问题的解释</span></h2>
    <div class="time">发布时间：2025年1月16日</div>
    <p style="text-indent: 2em;">《最高人民法院、最高人民检察院关于办理袭警刑事案件适用法律若干问题的解释》已经审议通过，现予公布，自2025年1月18日起施行。</p>
    <p style="text-align: center;">最高人民法院&ensp;&ensp;最高人民检察院</p>
    <p style="text-align: center;">2025年1月15日</p>
    <p style="text-align: center;"><span style="font-weight: bold;">高检发释字〔2025〕1号</span></p>
    <p style="text-indent: 2em;">为依法惩治袭警犯罪，根据《中华人民共和国刑法》等法律规定，现就办理此类刑事案件适用法律的若干问题解释如下：</p>
    <p style="text-indent: 2em;"><span style="font-weight: bold;">第一条</span>&ensp; 袭击正在依法执行职务的人民警察，具有下列情形之一的，应当认定为刑法第二百七十七条第五款规定的"暴力袭击"。</p>
    <p style="text-indent: 2em;"><span style="font-weight: bold;">第二条</span>&ensp; 暴力袭击正在依法执行职务的人民警察，具有下列情形之一的，应当认定为"严重危及其人身安全"。</p>
    <p style="text-indent: 2em;"><span style="font-weight: bold;">第三条</span>&ensp; 本解释自2025年1月18日起施行。</p>
</div>
<div id="pageBreak"></div>
</div>
<div class="footer">最高检版权所有</div>
</body></html>
"""

    DETAIL_FIXTURE_GUIDING_CASE = """
<html><head><title>最高人民检察院第六十二批指导性案例_中华人民共和国最高人民检察院</title></head>
<body>
<div id="fontzoom">
    <h2>最高人民检察院第六十二批指导性案例</h2>
    <div class="time">发布时间：2026年3月3日</div>
    <p>高检发研字〔2026〕3号</p>
    <p>为加强法律监督，发布第六十二批指导性案例。</p>
    <p>检例第249号 张某诈骗案</p>
    <p>关键词：诈骗罪、电信网络诈骗</p>
    <p>裁判要点：本案认定核心要点。</p>
</div>
</body></html>
"""

    DETAIL_FIXTURE_UNNUMBERED_POLICY = """
<html><head><title>最高人民法院 最高人民检察院<br>关于适用认罪认罚从宽制度的指导意见_中华人民共和国最高人民检察院</title></head>
<body>
<div id="fontzoom">
  <h2>最高人民法院 最高人民检察院<br>关于适用认罪认罚从宽制度的指导意见</h2>
  <p>法发〔2019〕13号</p>
  <p>为正确适用认罪认罚从宽制度，提出如下意见。</p>
  <p>一、准确把握适用条件。</p>
  <p>二、依法保障诉讼权利。</p>
</div>
</body></html>
"""

    def _fake_list_result(self, fixture=None):
        return spp_gov_cn.FetchResult(
            url="https://www.spp.gov.cn/spp/sfjs/index.shtml",
            status_code=200,
            headers={},
            text=fixture if fixture is not None else self.LIST_FIXTURE,
        )

    def _fake_detail_result(self, fixture=None, *, url=None):
        return spp_gov_cn.FetchResult(
            url=url or "https://www.spp.gov.cn/xwfbh/wsfbt/202501/t20250116_679579.shtml",
            status_code=200,
            headers={"Last-Modified": "Thu, 16 Jan 2025 12:00:00 GMT"},
            text=fixture if fixture is not None else self.DETAIL_FIXTURE,
        )

    def test_normalize_detail_id_handles_various_shapes(self):
        # 防回归：列表 href 的多种形态都能 normalize 为同一个 detail_id
        for raw in (
            "https://www.spp.gov.cn/xwfbh/wsfbt/202501/t20250116_679579.shtml",
            "https://www.spp.gov.cn/xwfbh/wsfbt/202501/t20250116_679579.shtml#1",
            "https://www.spp.gov.cn//xwfbh/wsfbt/202501/t20250116_679579.shtml",
            "/xwfbh/wsfbt/202501/t20250116_679579.shtml",
            "xwfbh/wsfbt/202501/t20250116_679579",
        ):
            self.assertEqual(
                spp_gov_cn._normalize_detail_id(raw),
                "xwfbh/wsfbt/202501/t20250116_679579",
                f"failed for {raw!r}",
            )

    def test_normalize_detail_id_rejects_empty_and_invalid(self):
        self.assertIsNone(spp_gov_cn._normalize_detail_id(""))
        self.assertIsNone(spp_gov_cn._normalize_detail_id(None))
        self.assertIsNone(spp_gov_cn._normalize_detail_id("/"))
        # 含非允许字符（空格 / 中文）应拒绝
        self.assertIsNone(spp_gov_cn._normalize_detail_id("xxx 路径/yyy"))

    def test_search_list_parses_rows_with_dates(self):
        adapter = spp_gov_cn.SppGovCnAdapter()
        with patch.object(spp_gov_cn, "_fetch_text", return_value=self._fake_list_result()):
            result = adapter.search_list(channel="sfjs", page=1)
        self.assertEqual(result["channel"], "sfjs")
        self.assertEqual(result["label"], "司法解释")
        self.assertEqual(len(result["rows"]), 3)
        first = result["rows"][0]
        # detail_id 形态：去 leading / + .shtml + 剥 fragment
        self.assertEqual(first["detail_id"], "xwfbh/wsfbt/202501/t20250116_679579")
        self.assertEqual(first["channel"], "sfjs")
        self.assertEqual(first["status"], "current")
        self.assertEqual(first["released_at"], "2025-01-16")
        self.assertIn("袭警", first["title"])
        # 列表回填的 url 不带 fragment / 双斜杠
        self.assertEqual(
            first["url"],
            "https://www.spp.gov.cn/xwfbh/wsfbt/202501/t20250116_679579.shtml",
        )

    def test_search_list_filters_by_query_substring(self):
        adapter = spp_gov_cn.SppGovCnAdapter()
        with patch.object(spp_gov_cn, "_fetch_text", return_value=self._fake_list_result()):
            result = adapter.search_list("知识产权", channel="sfjs")
        self.assertEqual(len(result["rows"]), 1)
        self.assertIn("知识产权", result["rows"][0]["title"])

    def test_search_list_query_handles_whitespace_variants(self):
        # spp 列表标题里 "最高人民法院  最高人民检察院" 双空格混排（fixture
        # 知识产权解释那条就是双空格），agent 用单空格 / 无空格的精确名搜
        # 都应命中。防回归 codex P2 评审。
        adapter = spp_gov_cn.SppGovCnAdapter()
        # 单空格查 vs 双空格 title
        with patch.object(spp_gov_cn, "_fetch_text", return_value=self._fake_list_result()):
            single_space = adapter.search_list(
                "最高人民法院 最高人民检察院关于办理侵犯知识产权",
                channel="sfjs",
            )
        self.assertEqual(len(single_space["rows"]), 1)
        # 完全无空格查 → 同样命中
        with patch.object(spp_gov_cn, "_fetch_text", return_value=self._fake_list_result()):
            no_space = adapter.search_list(
                "最高人民法院最高人民检察院关于办理侵犯知识产权",
                channel="sfjs",
            )
        self.assertEqual(len(no_space["rows"]), 1)
        # 顿号形态也应命中无顿号 / 空白混排的标题。
        with patch.object(spp_gov_cn, "_fetch_text", return_value=self._fake_list_result()):
            punct = adapter.search_list(
                "最高人民法院、最高人民检察院关于办理侵犯知识产权",
                channel="sfjs",
            )
        self.assertEqual(len(punct["rows"]), 1)

    def test_search_all_pages_walks_bounded_pages(self):
        adapter = spp_gov_cn.SppGovCnAdapter()
        page_1 = self._fake_list_result(
            """
<html><body><ul>
  <li><a href="/spp/gfwj/202001/t20200101_1.shtml">无关规范文件</a><span>2020-01-01</span></li>
</ul></body></html>
"""
        )
        page_2 = self._fake_list_result(
            """
<html><body><ul>
  <li><a href="/spp/xwfbh/wsfbh/201912/t20191230_451490.shtml">人民检察院刑事诉讼规则</a><span>2019-12-30</span></li>
</ul></body></html>
"""
        )
        with patch.object(spp_gov_cn, "_fetch_text", side_effect=[page_1, page_2]):
            result = adapter.search_all_pages(
                "人民检察院刑事诉讼规则",
                channel="gfwj",
                max_pages=2,
            )
        self.assertEqual(result["scanned_pages"], 2)
        self.assertEqual(len(result["rows"]), 1)
        self.assertEqual(
            result["rows"][0]["detail_id"],
            "spp/xwfbh/wsfbh/201912/t20191230_451490",
        )

    def test_cross_search_scans_multiple_channels(self):
        adapter = spp_gov_cn.SppGovCnAdapter()

        def fake_search_all(query, *, channel, max_pages):
            if channel == "gfwj":
                return {
                    "rows": [
                        {
                            "detail_id": "spp/xwfbh/wsfbh/201912/t20191230_451490",
                            "channel": "gfwj",
                            "title": "人民检察院刑事诉讼规则",
                        }
                    ],
                    "scanned_pages": 2,
                }
            return {"rows": [], "scanned_pages": 1}

        with patch.object(adapter, "search_all_pages", side_effect=fake_search_all):
            result = adapter.cross_search(
                "人民检察院刑事诉讼规则",
                channels=("sfjs", "gfwj"),
                max_pages_per_channel=5,
            )
        self.assertEqual(result["channels"], ["sfjs", "gfwj"])
        self.assertEqual(len(result["rows"]), 1)
        self.assertEqual(result["per_channel"][1]["matched"], 1)

    def test_search_list_rejects_unknown_channel(self):
        adapter = spp_gov_cn.SppGovCnAdapter()
        with self.assertRaises(ValueError) as ctx:
            adapter.search_list(channel="nope")
        self.assertIn("sfjs", str(ctx.exception))
        self.assertIn("jczdal", str(ctx.exception))

    def test_search_list_pagination_uses_index_n_shtml(self):
        adapter = spp_gov_cn.SppGovCnAdapter()
        captured = []

        def _capture(url, **kwargs):
            captured.append(url)
            return self._fake_list_result()

        with patch.object(spp_gov_cn, "_fetch_text", side_effect=_capture):
            adapter.search_list(channel="sfjs", page=1)
            adapter.search_list(channel="sfjs", page=3)
        self.assertEqual(len(captured), 2)
        self.assertTrue(captured[0].endswith("/spp/sfjs/index.shtml"), captured[0])
        self.assertTrue(captured[1].endswith("/spp/sfjs/index_3.shtml"), captured[1])

    def test_fetch_detail_extracts_title_and_content(self):
        adapter = spp_gov_cn.SppGovCnAdapter()
        with patch.object(spp_gov_cn, "_fetch_text", return_value=self._fake_detail_result()):
            detail = adapter.fetch_detail("xwfbh/wsfbt/202501/t20250116_679579")
        # 标题统一后缀已剥离
        self.assertIn("袭警刑事案件", detail["title"])
        self.assertNotIn("中华人民共和国最高人民检察院", detail["title"])
        # raw_title 保留原文便于排查
        self.assertIn("最高人民检察院", detail["raw_title"])
        self.assertIn("第一条", detail["content_html"])
        self.assertIn("高检发释字〔2025〕1号", detail["content_html"])

    def test_fetch_detail_accepts_full_url_with_fragment(self):
        # agent 直接把列表里抓到的 href（含 fragment）丢给 fetch_detail 应该 work
        adapter = spp_gov_cn.SppGovCnAdapter()
        captured = []

        def _capture(url, **kwargs):
            captured.append(url)
            return self._fake_detail_result()

        with patch.object(spp_gov_cn, "_fetch_text", side_effect=_capture):
            detail = adapter.fetch_detail(
                "https://www.spp.gov.cn/xwfbh/wsfbt/202501/t20250116_679579.shtml#1"
            )
        # detail_id 已规整（去 fragment + .shtml）
        self.assertEqual(detail["detail_id"], "xwfbh/wsfbt/202501/t20250116_679579")
        # 真实 fetch URL 也是干净的（不带 fragment）
        self.assertEqual(
            captured[0],
            "https://www.spp.gov.cn/xwfbh/wsfbt/202501/t20250116_679579.shtml",
        )

    def test_fetch_detail_rejects_invalid_id(self):
        adapter = spp_gov_cn.SppGovCnAdapter()
        with self.assertRaises(ValueError):
            adapter.fetch_detail("")
        with self.assertRaises(ValueError):
            adapter.fetch_detail("contains 空格/yyy")

    def test_build_law_payload_two_high_joint_interpretation(self):
        adapter = spp_gov_cn.SppGovCnAdapter()
        search_row = {
            "detail_id": "xwfbh/wsfbt/202501/t20250116_679579",
            "channel": "sfjs",
            "title": "最高人民法院 最高人民检察院关于办理袭警刑事案件适用法律若干问题的解释",
        }
        with patch.object(spp_gov_cn, "_fetch_text", return_value=self._fake_detail_result()):
            payload = adapter.build_law_payload(
                "xwfbh/wsfbt/202501/t20250116_679579",
                search_row=search_row,
            )
        # 法律层级：title 含"解释"覆盖 channel 默认
        self.assertEqual(payload["level"], "judicial_interpretation")
        # 联合发布主体
        self.assertEqual(payload["issuing_body"], "最高人民法院 最高人民检察院")
        # 文号自动抽取并归一化
        self.assertEqual(payload["document_number"], "高检发释字〔2025〕1号")
        # source 字段（CONTRACT.md §2.x）
        self.assertEqual(payload["source_name"], "spp.gov.cn")
        self.assertTrue(payload["id"].startswith("spp_gov_cn:"))
        self.assertTrue(payload["source_hash"])
        # 三条"第N条"切分成功
        self.assertEqual(len(payload["articles"]), 3)
        article_numbers = [a.get("number") for a in payload["articles"]]
        self.assertEqual(article_numbers, ["1", "2", "3"])

    def test_build_law_payload_level_guiding_case_for_jczdal(self):
        adapter = spp_gov_cn.SppGovCnAdapter()
        search_row = {
            "detail_id": "spp/jczdal/202603/t20260303_721548",
            "channel": "jczdal",
            "title": "最高人民检察院第六十二批指导性案例",
        }
        fake = self._fake_detail_result(
            self.DETAIL_FIXTURE_GUIDING_CASE,
            url="https://www.spp.gov.cn/spp/jczdal/202603/t20260303_721548.shtml",
        )
        with patch.object(spp_gov_cn, "_fetch_text", return_value=fake):
            payload = adapter.build_law_payload(
                "spp/jczdal/202603/t20260303_721548",
                search_row=search_row,
            )
        self.assertEqual(payload["level"], "guiding_case")
        # 单方发布 → issuing_body 不含 court
        self.assertEqual(payload["issuing_body"], "最高人民检察院")
        # 指导性案例文档非条文化结构，articles 可能为 0（按 ADR-0008 边界 + user 指示，本期不专门做切分）
        self.assertGreaterEqual(len(payload["articles"]), 0)

    def test_build_law_payload_unnumbered_policy_keeps_body_article(self):
        adapter = spp_gov_cn.SppGovCnAdapter()
        search_row = {
            "detail_id": "spp/xwfbh/wsfbh/201910/t20191024_435829",
            "channel": "gfwj",
            "title": "最高人民法院 最高人民检察院关于适用认罪认罚从宽制度的指导意见",
        }
        fake = self._fake_detail_result(
            self.DETAIL_FIXTURE_UNNUMBERED_POLICY,
            url="https://www.spp.gov.cn/spp/xwfbh/wsfbh/201910/t20191024_435829.shtml",
        )
        with patch.object(spp_gov_cn, "_fetch_text", return_value=fake):
            payload = adapter.build_law_payload(
                "spp/xwfbh/wsfbh/201910/t20191024_435829",
                search_row=search_row,
            )
        self.assertNotIn("<br>", payload["title"])
        self.assertEqual(payload["level"], "judicial_policy")
        self.assertEqual(len(payload["articles"]), 1)
        self.assertEqual(payload["articles"][0]["number"], "正文")
        self.assertIn("认罪认罚从宽制度", payload["articles"][0]["text"])

    def test_build_law_payload_short_title_strips_long_prefix(self):
        adapter = spp_gov_cn.SppGovCnAdapter()
        search_row = {
            "detail_id": "xwfbh/wsfbt/202501/t20250116_679579",
            "channel": "sfjs",
            "title": "最高人民法院 最高人民检察院关于办理袭警刑事案件适用法律若干问题的解释",
        }
        with patch.object(spp_gov_cn, "_fetch_text", return_value=self._fake_detail_result()):
            payload = adapter.build_law_payload(
                "xwfbh/wsfbt/202501/t20250116_679579",
                search_row=search_row,
            )
        short = payload.get("short_title")
        # short_title 至少不应是长前缀（防回归 27 字超长 short_title）
        self.assertIsNotNone(short)
        self.assertNotIn("最高人民法院 最高人民检察院", short)
        self.assertLessEqual(len(short), 30)

    def test_source_hash_is_stable_across_calls(self):
        adapter = spp_gov_cn.SppGovCnAdapter()
        with patch.object(spp_gov_cn, "_fetch_text", return_value=self._fake_detail_result()):
            h1 = adapter.source_hash("xwfbh/wsfbt/202501/t20250116_679579")
        with patch.object(spp_gov_cn, "_fetch_text", return_value=self._fake_detail_result()):
            h2 = adapter.source_hash("xwfbh/wsfbt/202501/t20250116_679579")
        self.assertEqual(h1, h2)
        self.assertEqual(len(h1), 64)  # sha256 hex

    def test_infer_level_recognizes_meeting_minutes_and_policy(self):
        # title 关键字优先于 channel 默认
        self.assertEqual(
            spp_gov_cn._infer_level("sfjs", "全国检察机关刑事审判工作会议纪要"),
            "judicial_meeting_minutes",
        )
        self.assertEqual(
            spp_gov_cn._infer_level("gfwj", "关于加强未成年人检察工作的指导意见"),
            "judicial_policy",
        )
        self.assertEqual(
            spp_gov_cn._infer_level("sfjs", "关于办理某案件的批复"),
            "judicial_interpretation",
        )

    def test_infer_issuing_body_combines_joint_publishers(self):
        # 三方联合
        self.assertEqual(
            spp_gov_cn._infer_issuing_body(
                "最高人民法院 最高人民检察院 公安部 关于办理某案件的意见"
            ),
            "最高人民法院 最高人民检察院 公安部",
        )
        # 两高
        self.assertEqual(
            spp_gov_cn._infer_issuing_body(
                "最高人民法院 最高人民检察院关于办理袭警刑事案件适用法律若干问题的解释"
            ),
            "最高人民法院 最高人民检察院",
        )
        # 默认 fallback
        self.assertEqual(spp_gov_cn._infer_issuing_body("空白"), "最高人民检察院")

    def test_infer_issuing_body_recognizes_two_high_shortcut(self):
        # spp /sfjs/ 历史条目大量用 "两高..." / "两高一部..." 简称，没有
        # explicit 全名。防回归 codex P2 评审："两高" 必须识别为联合发布，
        # 否则下游 publisher 过滤会漏召回。
        self.assertEqual(
            spp_gov_cn._infer_issuing_body(
                "两高关于办理刑事赔偿案件适用法律若干问题的解释"
            ),
            "最高人民法院 最高人民检察院",
        )
        # "两高一部" 简称 + explicit "公安部"
        self.assertEqual(
            spp_gov_cn._infer_issuing_body(
                "两高一部 公安部 关于办理暴力恐怖刑事案件适用法律若干问题的意见"
            ),
            "最高人民法院 最高人民检察院 公安部",
        )
        # explicit 形态出现时不重复算 "两高"（避免与简称混合时叠加）
        self.assertEqual(
            spp_gov_cn._infer_issuing_body(
                "最高人民法院 关于参考两高解释办理某案件的通知"
            ),
            "最高人民法院",
        )


class SppGovCnVerifySourceTests(unittest.TestCase):
    """spp_gov_cn 通过 verify-source pipeline 的端到端契约。"""

    def test_verify_source_runs_end_to_end_with_offline_fixtures(self):
        # 确保 ADR-0008 §1.2 承诺的 verify-source 在 spp 上跑通
        list_result = spp_gov_cn.FetchResult(
            url="https://www.spp.gov.cn/spp/sfjs/index.shtml",
            status_code=200,
            headers={},
            text=SppGovCnFetchTests.LIST_FIXTURE,
        )
        detail_result = spp_gov_cn.FetchResult(
            url="https://www.spp.gov.cn/xwfbh/wsfbt/202501/t20250116_679579.shtml",
            status_code=200,
            headers={},
            text=SppGovCnFetchTests.DETAIL_FIXTURE,
        )
        homepage = spp_gov_cn.FetchResult(
            url="https://www.spp.gov.cn/",
            status_code=200,
            headers={},
            text="<html><head><title>最高检</title></head><body>司法解释</body></html>",
        )

        responses = iter([homepage, list_result, detail_result])

        def _next(url, **kwargs):
            return next(responses)

        with patch.object(spp_gov_cn, "_fetch_text", side_effect=_next):
            result = sources.verify_source(
                "spp_gov_cn",
                query="袭警",
                article="第一条",
                limit=3,
            )
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["source"], "spp_gov_cn")
        self.assertGreaterEqual(len(result["candidates"]), 1)
        self.assertIsNotNone(result["selected"])
        # 防回归：spp 列表 href 偶含双斜杠（``https://www.spp.gov.cn//xwfbh/...``），
        # cleaning 后 source_url 不能出现 ``///`` 残余
        self.assertNotIn("///", result["law"]["source_url"])
        self.assertGreaterEqual(result["law"]["article_count"], 1)


class SourceVerifyTests(unittest.TestCase):
    class FakeAdapter:
        def __init__(self, *, rows=None, payload=None, fail_search=False):
            self.rows = rows or []
            self.payload = payload or {}
            self.fail_search = fail_search

        def probe(self):
            return {"status_code": 200, "page_shape": "spa"}

        def search_list(self, query, page_size=20):
            if self.fail_search:
                raise OSError("network down")
            return {"rows": self.rows[:page_size]}

        def build_law_payload(self, bbbs, search_row=None):
            return self.payload

    def test_verify_source_smoke_success(self):
        payload = {
            "id": "law-1",
            "title": "中华人民共和国示例法",
            "short_title": "示例法",
            "status": "current",
            "source_url": "https://example.com/law-1",
            "source_hash": "abc",
            "source_checked_at": "2026-04-30T00:00:00+00:00",
            "articles": [
                {
                    "number": "1",
                    "number_display": "第一条",
                    "text": "示例正文。",
                }
            ],
        }
        adapter = self.FakeAdapter(
            rows=[
                {
                    "bbbs": "law-1",
                    "title": "中华人民共和国<em class='highlight'>示例法</em>",
                    "gbrq": "2026-01-01",
                    "sxx": 3,
                }
            ],
            payload=payload,
        )
        with patch("chinalaw.sources.get_source_adapter", return_value=adapter):
            report = sources.verify_source(
                "flk_npc",
                query="中华人民共和国示例法",
                article="第一条",
            )

        self.assertTrue(report["ok"], report["steps"])
        self.assertEqual(report["candidates"][0]["title"], "中华人民共和国示例法")
        self.assertEqual(report["selected"]["bbbs"], "law-1")
        self.assertEqual(report["law"]["article_count"], 1)
        self.assertEqual(report["article_match"]["number"], "1")

    def test_verify_source_reports_search_failure(self):
        adapter = self.FakeAdapter(fail_search=True)
        with patch("chinalaw.sources.get_source_adapter", return_value=adapter):
            report = sources.verify_source("flk_npc")

        self.assertFalse(report["ok"])
        self.assertEqual(report["steps"][-1]["step"], "search")
        self.assertIn("network down", report["steps"][-1]["message"])


class SyncWorkflowTests(unittest.TestCase):
    def test_sync_source_imports_search_results(self):
        import tempfile

        class FakeAdapter:
            def search_list(self, query, page_size=20):
                self.query = query
                self.page_size = page_size
                return {
                    "code": 200,
                    "rows": [
                        {"bbbs": "law-1", "title": "中华人民共和国示例法"},
                    ],
                }

            def build_law_payload(self, bbbs, search_row=None):
                return {
                    "id": bbbs,
                    "title": "中华人民共和国示例法",
                    "short_title": "示例法",
                    "aliases": ["示例条例"],
                    "level": "law",
                    "status": "current",
                    "issuing_body": "全国人民代表大会",
                    "document_number": None,
                    "released_at": "2026-01-01",
                    "effective_at": "2026-02-01",
                    "repealed_at": None,
                    "source_url": f"https://flk.npc.gov.cn/detail?id={bbbs}",
                    "source_name": "flk.npc.gov.cn",
                    "source_checked_at": "2026-04-22T00:00:00+00:00",
                    "source_hash": "hash-law-1",
                    "articles": [
                        {
                            "number": "1",
                            "number_display": "第一条",
                            "text": "示例正文。",
                            "part": "第一章 总则",
                        }
                    ],
                }

        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "t.db"
            with patch("chinalaw.sync.get_source_adapter", return_value=FakeAdapter()):
                result = sync_source(
                    db_path,
                    source="flk_npc",
                    query="示例法",
                    limit=1,
                )

            self.assertEqual(result["laws_loaded"], 1)
            self.assertEqual(result["articles_loaded"], 1)
            law = service.get_law(db_path, "示例条例")
            self.assertIsNotNone(law)
            article = service.get_article(db_path, "示例法", "第一条")
            self.assertIsNotNone(article)
            self.assertEqual(article["article"]["text"], "示例正文。")

    def test_sync_source_batch_imports_multiple_pages_and_updates_meta(self):
        import tempfile

        class FakeAdapter:
            def search_list(self, query, page_num=1, page_size=20):
                rows_by_page = {
                    1: [{"bbbs": "law-1", "title": "示例法一"}],
                    2: [{"bbbs": "law-2", "title": "示例法二"}],
                    3: [],
                }
                return {
                    "code": 200,
                    "total": 2,
                    "rows": rows_by_page.get(page_num, []),
                }

            def build_law_payload(self, bbbs, search_row=None):
                suffix = bbbs[-1]
                return {
                    "id": bbbs,
                    "title": f"中华人民共和国示例法{suffix}",
                    "short_title": f"示例法{suffix}",
                    "aliases": [],
                    "level": "law",
                    "status": "current",
                    "issuing_body": "全国人民代表大会",
                    "document_number": None,
                    "released_at": "2026-01-01",
                    "effective_at": "2026-02-01",
                    "repealed_at": None,
                    "source_url": f"https://flk.npc.gov.cn/detail?id={bbbs}",
                    "source_name": "flk.npc.gov.cn",
                    "source_checked_at": "2026-04-22T00:00:00+00:00",
                    "source_hash": f"hash-{bbbs}",
                    "articles": [
                        {
                            "number": "1",
                            "number_display": "第一条",
                            "text": f"示例正文{suffix}。",
                            "part": "第一章 总则",
                        }
                    ],
                }

        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "t.db"
            with patch("chinalaw.sync.get_source_adapter", return_value=FakeAdapter()):
                result = sync_source(
                    db_path,
                    source="flk_npc",
                    batch=True,
                    start_page=1,
                    max_pages=2,
                    page_size=1,
                )

            self.assertEqual(result["mode"], "batch")
            self.assertEqual(result["pages_synced"], 2)
            self.assertEqual(result["rows_seen"], 2)
            self.assertEqual(result["laws_loaded"], 2)
            with connect(db_path) as conn:
                migrate(conn)
                self.assertEqual(get_meta(conn, "source:flk_npc:last_page"), "2")
                self.assertEqual(get_meta(conn, "source:flk_npc:last_mode"), "batch")
                self.assertEqual(get_meta(conn, "source:flk_npc:next_page"), "3")

    def test_sync_source_batch_resume_uses_next_page_meta(self):
        import tempfile

        class FakeAdapter:
            def __init__(self):
                self.pages = []

            def search_list(self, query, page_num=1, page_size=20):
                self.pages.append(page_num)
                return {
                    "code": 200,
                    "total": 1,
                    "rows": [{"bbbs": "law-3", "title": "示例法三"}] if page_num == 3 else [],
                }

            def build_law_payload(self, bbbs, search_row=None):
                return {
                    "id": bbbs,
                    "title": "中华人民共和国示例法三",
                    "short_title": "示例法三",
                    "aliases": [],
                    "level": "law",
                    "status": "current",
                    "issuing_body": "全国人民代表大会",
                    "document_number": None,
                    "released_at": "2026-01-01",
                    "effective_at": "2026-02-01",
                    "repealed_at": None,
                    "source_url": f"https://flk.npc.gov.cn/detail?id={bbbs}",
                    "source_name": "flk.npc.gov.cn",
                    "source_checked_at": "2026-04-22T00:00:00+00:00",
                    "source_hash": "hash-law-3",
                    "articles": [{"number": "1", "number_display": "第一条", "text": "示例正文三。"}],
                }

        adapter = FakeAdapter()
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "t.db"
            with connect(db_path) as conn:
                migrate(conn)
                set_meta(conn, "source:flk_npc:next_page", "3")
            with patch("chinalaw.sync.get_source_adapter", return_value=adapter):
                result = sync_source(
                    db_path,
                    source="flk_npc",
                    batch=True,
                    resume=True,
                    max_pages=1,
                    page_size=1,
                )

        self.assertEqual(adapter.pages, [3])
        self.assertTrue(result["resume"])
        self.assertEqual(result["laws_loaded"], 1)

    def test_sync_source_batch_stops_after_stable_pages(self):
        import tempfile

        class FakeAdapter:
            def search_list(self, query, page_num=1, page_size=20):
                return {
                    "code": 200,
                    "total": 1,
                    "rows": [{"bbbs": "law-1", "title": "示例法一"}] if page_num == 1 else [],
                }

            def build_law_payload(self, bbbs, search_row=None):
                return {
                    "id": bbbs,
                    "title": "中华人民共和国示例法一",
                    "short_title": "示例法一",
                    "aliases": [],
                    "level": "law",
                    "status": "current",
                    "issuing_body": "全国人民代表大会",
                    "document_number": None,
                    "released_at": "2026-01-01",
                    "effective_at": "2026-02-01",
                    "repealed_at": None,
                    "source_url": f"https://flk.npc.gov.cn/detail?id={bbbs}",
                    "source_name": "flk.npc.gov.cn",
                    "source_checked_at": "2026-04-22T00:00:00+00:00",
                    "source_hash": "hash-law-1",
                    "articles": [{"number": "1", "number_display": "第一条", "text": "示例正文一。"}],
                }

        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "t.db"
            with connect(db_path) as conn:
                migrate(conn)
                loader.load_law_from_dict(
                    conn,
                    {
                        "id": "law-1",
                        "title": "中华人民共和国示例法一",
                        "short_title": "示例法一",
                        "aliases": [],
                        "level": "law",
                        "status": "current",
                        "issuing_body": "全国人民代表大会",
                        "document_number": None,
                        "released_at": "2026-01-01",
                        "effective_at": "2026-02-01",
                        "repealed_at": None,
                        "source_url": "https://flk.npc.gov.cn/detail?id=law-1",
                        "source_name": "flk.npc.gov.cn",
                        "source_checked_at": "2026-04-22T00:00:00+00:00",
                        "source_hash": "hash-law-1",
                        "articles": [{"number": "1", "number_display": "第一条", "text": "示例正文一。"}],
                    },
                )
            with patch("chinalaw.sync.get_source_adapter", return_value=FakeAdapter()):
                result = sync_source(
                    db_path,
                    source="flk_npc",
                    batch=True,
                    max_pages=5,
                    page_size=1,
                    stop_after_stable_pages=1,
                )

        self.assertEqual(result["laws_loaded"], 0)
        self.assertEqual(result["laws_skipped"], 1)
        self.assertEqual(result["stop_reason"], "stable_pages")

    def test_sync_source_incremental_uses_date_window(self):
        import tempfile

        class FakeAdapter:
            def __init__(self):
                self.calls = []

            def list_laws(self, since=None, until=None, page_num=1, page_size=20):
                self.calls.append((since, until, page_num, page_size))
                return {
                    "code": 200,
                    "total": 1,
                    "rows": [{"bbbs": "law-9", "title": "示例增量法"}] if page_num == 1 else [],
                }

            def build_law_payload(self, bbbs, search_row=None):
                return {
                    "id": bbbs,
                    "title": "中华人民共和国示例增量法",
                    "short_title": "示例增量法",
                    "aliases": [],
                    "level": "law",
                    "status": "current",
                    "issuing_body": "全国人民代表大会",
                    "document_number": None,
                    "released_at": "2026-04-20",
                    "effective_at": "2026-04-21",
                    "repealed_at": None,
                    "source_url": f"https://flk.npc.gov.cn/detail?id={bbbs}",
                    "source_name": "flk.npc.gov.cn",
                    "source_checked_at": "2026-04-22T00:00:00+00:00",
                    "source_hash": f"hash-{bbbs}",
                    "articles": [{"number": "1", "number_display": "第一条", "text": "示例正文。"}],
                }

        adapter = FakeAdapter()
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "t.db"
            with connect(db_path) as conn:
                migrate(conn)
                set_meta(conn, "source:flk_npc:last_incremental_to", "2026-04-21")
            with patch("chinalaw.sync.get_source_adapter", return_value=adapter):
                result = sync_source(
                    db_path,
                    source="flk_npc",
                    incremental=True,
                    overlap_days=2,
                    max_pages=1,
                    page_size=1,
                )

        self.assertEqual(adapter.calls, [("2026-04-19", result["published_to"], 1, 1)])
        self.assertEqual(result["mode"], "incremental")
        self.assertEqual(result["laws_loaded"], 1)


class NormPackTests(unittest.TestCase):
    def test_import_list_show_and_export_norm_pack(self):
        import tempfile

        pack_payload = {
            "name": "民事法律行为有效条件",
            "summary": "围绕民法典第一百四十三条整理的基础规范包。",
            "maintainer": "tests",
            "version_policy": "current",
            "items": [
                {
                    "item_type": "article",
                    "law_id": "flk-civil-code-2020",
                    "law_title": "中华人民共和国民法典",
                    "article_number": "第一百四十三条",
                    "article_number_display": "第一百四十三条",
                    "role": "core",
                    "reason": "民事法律行为有效条件的核心法条",
                },
                {
                    "item_type": "reference",
                    "reference_text": "审查时应结合具体合同场景判断是否存在意思表示瑕疵。",
                    "role": "supporting",
                    "note": "这是规范包里的人工提示语。",
                },
            ],
        }

        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "t.db"
            loader.load_fixtures(db_path, FIXTURES)
            pack_file = Path(td) / "pack.json"
            pack_file.write_text(json.dumps(pack_payload, ensure_ascii=False), encoding="utf-8")
            imported = normpacks.import_pack_file(db_path, pack_file)
            packs = normpacks.list_packs(db_path)
            shown = normpacks.get_pack(db_path, "民事法律行为有效条件", resolve=True)
            exported = normpacks.export_pack(db_path, "民事法律行为有效条件")
            report = service.status(db_path)

        self.assertEqual(imported["items_loaded"], 2)
        self.assertEqual(len(packs), 1)
        self.assertEqual(packs[0]["item_count"], 2)
        self.assertIsNotNone(shown)
        self.assertEqual(shown["resolved_item_count"], 1)
        self.assertEqual(shown["items"][0]["article_number"], "143")
        self.assertIn("resolved", shown["items"][0])
        self.assertNotIn(
            "snapshot_json",
            shown["items"][0]["resolved"]["law"]["current_revision"],
        )
        self.assertEqual(exported["name"], "民事法律行为有效条件")
        self.assertEqual(exported["items"][0]["article_number"], "143")
        self.assertEqual(report["norm_packs"], 1)

    def test_import_pack_accepts_unresolved_law_reference(self):
        import tempfile

        pack_payload = {
            "name": "竞业限制审查",
            "items": [
                {
                    "item_type": "article",
                    "law_title": "劳动合同法",
                    "article_number": "第二十三条",
                    "role": "core",
                }
            ],
        }

        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "t.db"
            pack_file = Path(td) / "pack.json"
            pack_file.write_text(json.dumps(pack_payload, ensure_ascii=False), encoding="utf-8")
            imported = normpacks.import_pack_file(db_path, pack_file)
            shown = normpacks.get_pack(db_path, "竞业限制审查", resolve=True)

        self.assertEqual(imported["items_loaded"], 1)
        self.assertIsNotNone(shown)
        self.assertEqual(shown["resolved_item_count"], 0)
        self.assertEqual(shown["items"][0]["article_number"], "23")

    def test_pack_can_reference_private_norm_source_and_clause(self):
        import tempfile

        pack_payload = {
            "name": "放款审查规范包",
            "summary": "把民法典履行规则与甲方放款要求组合到一个工作流包。",
            "items": [
                {
                    "item_type": "norm_source",
                    "norm_source_id": "acme-lending-policy",
                    "norm_source_name": "甲方放款要求（示例）",
                    "role": "background",
                    "reason": "先让 agent 理解本次放款审查的私域约束来源。",
                },
                {
                    "item_type": "norm_clause",
                    "norm_source_name": "放款要求",
                    "clause_number": "第二条",
                    "clause_number_display": "第二条",
                    "role": "core",
                    "reason": "担保审批是放款前置审查重点。",
                },
                {
                    "item_type": "article",
                    "law_id": "flk-civil-code-2020",
                    "law_title": "中华人民共和国民法典",
                    "article_number": "第五百零九条",
                    "role": "supporting",
                },
            ],
        }

        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "t.db"
            loader.load_fixtures(db_path, FIXTURES)
            with connect(db_path) as conn:
                normsources.import_source_from_dict(conn, EXTRA_NORM_SOURCE_FIXTURE)
            pack_file = Path(td) / "pack.json"
            pack_file.write_text(json.dumps(pack_payload, ensure_ascii=False), encoding="utf-8")
            imported = normpacks.import_pack_file(db_path, pack_file)
            shown = normpacks.get_pack(db_path, "放款审查规范包", resolve=True)
            exported = normpacks.export_pack(db_path, "放款审查规范包")

        self.assertEqual(imported["items_loaded"], 3)
        self.assertIsNotNone(shown)
        self.assertEqual(shown["resolved_item_count"], 3)
        self.assertEqual(shown["items"][0]["resolved"]["kind"], "norm_source")
        self.assertEqual(shown["items"][1]["clause_number"], "2")
        self.assertEqual(shown["items"][1]["resolved"]["kind"], "norm_clause")
        self.assertIn("担保审批", shown["items"][1]["resolved"]["clause"]["text"])
        self.assertEqual(shown["items"][2]["resolved"]["kind"], "article")
        self.assertEqual(exported["items"][0]["norm_source_id"], "acme-lending-policy")
        self.assertEqual(exported["items"][1]["clause_number"], "2")
        self.assertEqual(
            exported["dependencies"]["norm_sources"][0]["norm_source_id"],
            "acme-lending-policy",
        )

    def test_validate_pack_reports_missing_items_and_dependencies(self):
        import tempfile

        pack_payload = {
            "name": "缺失依赖规范包",
            "dependencies": {
                "norm_sources": ["missing-policy"],
            },
            "items": [
                {
                    "item_type": "norm_clause",
                    "norm_source_id": "missing-policy",
                    "clause_number": "第一条",
                    "role": "core",
                }
            ],
        }

        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "t.db"
            pack_file = Path(td) / "pack.json"
            pack_file.write_text(json.dumps(pack_payload, ensure_ascii=False), encoding="utf-8")
            with connect(db_path) as conn:
                normpacks.import_pack_from_dict(conn, pack_payload)
            report = normpacks.validate_pack(db_path, "缺失依赖规范包")
            file_report = normpacks.validate_pack_file(db_path, pack_file)

        self.assertIsNotNone(report)
        self.assertFalse(report["ok"])
        self.assertFalse(file_report["ok"])
        self.assertGreaterEqual(report["error_count"], 1)
        codes = {issue["code"] for issue in report["issues"]}
        self.assertIn("missing_norm_clause", codes)
        self.assertIn("missing_norm_source_dependency", codes)

    def test_add_article_to_pack_creates_and_dedupes(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "t.db"
            loader.load_fixtures(db_path, FIXTURES)
            first = normpacks.add_item_to_pack(
                db_path,
                "善意取得审查",
                {
                    "item_type": "article",
                    "law_title": "民法典",
                    "article_number": "第一百四十三条",
                    "role": "core",
                    "reason": "示例：民事法律行为效力基础条款",
                },
                create=True,
            )
            second = normpacks.add_item_to_pack(
                db_path,
                "善意取得审查",
                {
                    "item_type": "article",
                    "law_title": "中华人民共和国民法典",
                    "article_number": "143",
                    "role": "core",
                    "reason": "重复添加应幂等",
                },
            )
            shown = normpacks.get_pack(db_path, "善意取得审查", resolve=True)
            validation = normpacks.validate_pack(db_path, "善意取得审查")

        self.assertTrue(first["added"])
        self.assertFalse(second["added"])
        self.assertTrue(second["duplicate"])
        self.assertEqual(shown["item_count"], 1)
        self.assertEqual(shown["items"][0]["law_id"], "flk-civil-code-2020")
        self.assertEqual(shown["items"][0]["article_number"], "143")
        self.assertTrue(validation["ok"])

    def test_add_norm_clause_to_pack(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "t.db"
            with connect(db_path) as conn:
                normsources.import_source_from_dict(conn, EXTRA_NORM_SOURCE_FIXTURE)
            result = normpacks.add_item_to_pack(
                db_path,
                "放款审查沉淀",
                {
                    "item_type": "norm_clause",
                    "norm_source_name": "放款要求",
                    "clause_number": "第二条",
                    "role": "important",
                    "reason": "担保审批是放款审查核心要求",
                },
                create=True,
            )
            shown = normpacks.get_pack(db_path, "放款审查沉淀", resolve=True)

        self.assertTrue(result["added"])
        self.assertEqual(shown["items"][0]["norm_source_id"], "acme-lending-policy")
        self.assertEqual(shown["items"][0]["clause_number"], "2")
        self.assertEqual(shown["resolved_item_count"], 1)

    def test_add_item_requires_resolution_by_default(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "t.db"
            with self.assertRaises(normpacks.PackItemUnresolvedError):
                normpacks.add_item_to_pack(
                    db_path,
                    "未解析包",
                    {
                        "item_type": "article",
                        "law_title": "不存在的法律",
                        "article_number": "第一条",
                    },
                    create=True,
                )
            self.assertIsNone(normpacks.get_pack(db_path, "未解析包"))

    def test_add_item_can_allow_unresolved(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "t.db"
            result = normpacks.add_item_to_pack(
                db_path,
                "待补全包",
                {
                    "item_type": "article",
                    "law_title": "不存在的法律",
                    "article_number": "第一条",
                    "note": "pending: 待补全来源",
                },
                create=True,
                require_resolved=False,
            )
            validation = normpacks.validate_pack(db_path, "待补全包")

        self.assertTrue(result["added"])
        self.assertFalse(validation["ok"])
        self.assertTrue(
            any(i["code"] == "missing_law_for_article" for i in validation["issues"])
        )


class NormSourceTests(unittest.TestCase):
    def test_import_list_show_clause_and_export_norm_source(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "t.db"
            source_file = Path(td) / "norm.json"
            source_file.write_text(
                json.dumps(EXTRA_NORM_SOURCE_FIXTURE, ensure_ascii=False),
                encoding="utf-8",
            )
            imported = normsources.import_source_file(db_path, source_file)
            listed = normsources.list_sources(db_path)
            shown = normsources.get_source(db_path, "放款要求")
            clause = normsources.get_clause(db_path, "放款要求", "第二条")
            exported = normsources.export_source(db_path, "甲方放款要求（示例）")
            report = service.status(db_path)

        self.assertEqual(imported["clauses_loaded"], 3)
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["clause_count"], 3)
        self.assertIsNotNone(shown)
        self.assertEqual(shown["clauses"][0]["number"], "1")
        self.assertIsNotNone(clause)
        self.assertEqual(clause["clause"]["number"], "2")
        self.assertEqual(exported["source_type"], "lender_requirement")
        self.assertEqual(report["norm_sources"], 1)
        self.assertEqual(report["norm_clauses"], 3)

    def test_get_clause_supports_decimal_number(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "t.db"
            with connect(db_path) as conn:
                normsources.import_source_from_dict(conn, EXTRA_NORM_SOURCE_FIXTURE)
            clause = normsources.get_clause(db_path, "甲方放款要求", "2.1")

        self.assertIsNotNone(clause)
        self.assertEqual(clause["clause"]["number"], "2.1")

    def test_get_clause_falls_back_to_position(self):
        """对齐 norm show 的'项'语义：number 是纯数字时按 position 兜底取第 N 项。"""
        import tempfile

        single_unnumbered = {
            "id": "policy-single",
            "name": "未编号制度",
            "short_name": "未编号制度",
            "aliases": [],
            "source_type": "private_policy",
            "authority": "测试",
            "binding_scope": "测试",
            "jurisdiction": "CN",
            "effective_at": "2026-01-01",
            "source_name": "local-file",
            "source_checked_at": "2026-01-01T00:00:00+00:00",
            "clauses": [
                {"text": "整段未编号正文。"},
            ],
        }
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "t.db"
            with connect(db_path) as conn:
                normsources.import_source_from_dict(conn, single_unnumbered)
            payload = normsources.get_clause(db_path, "未编号制度", "1")

        self.assertIsNotNone(payload)
        self.assertIsNotNone(payload["clause"])
        self.assertEqual(payload["match_strategy"], "position")
        self.assertEqual(payload["clause"]["position"], 1)
        self.assertIn("整段", payload["clause"]["text"])

    def test_get_clause_position_fallback_does_not_shadow_explicit_number(self):
        """显式 number 命中时不应被 position 兜底覆盖。"""
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "t.db"
            with connect(db_path) as conn:
                normsources.import_source_from_dict(conn, EXTRA_NORM_SOURCE_FIXTURE)
            payload = normsources.get_clause(db_path, "甲方放款要求", "2")

        self.assertIsNotNone(payload)
        self.assertEqual(payload["match_strategy"], "number")
        self.assertEqual(payload["clause"]["number"], "2")

    def test_get_clause_returns_match_strategy_for_misses(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "t.db"
            with connect(db_path) as conn:
                normsources.import_source_from_dict(conn, EXTRA_NORM_SOURCE_FIXTURE)
            payload = normsources.get_clause(db_path, "甲方放款要求", "999")

        self.assertIsNotNone(payload)
        self.assertIsNone(payload["clause"])
        self.assertIsNone(payload["match_strategy"])

    def test_get_article_falls_back_to_norm_source(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "t.db"
            loader.load_fixtures(db_path, FIXTURES)
            with connect(db_path) as conn:
                normsources.import_source_from_dict(conn, EXTRA_NORM_SOURCE_FIXTURE)

            payload = service.get_article(db_path, "甲方放款要求", "2")
            self.assertIsNotNone(payload)
            self.assertEqual(payload.get("via"), "norm_fallback")
            self.assertEqual(payload["law"]["title"], "甲方放款要求（示例）")
            self.assertEqual(payload["law"]["via"], "norm_fallback")
            self.assertIsNotNone(payload["article"])
            self.assertIn("担保", payload["article"]["text"])
            self.assertEqual(payload["article"]["via"], "norm_fallback")

            # Alias 解析
            via_alias = service.get_article(db_path, "放款标准", "1")
            self.assertIsNotNone(via_alias)
            self.assertEqual(via_alias.get("via"), "norm_fallback")
            self.assertIsNotNone(via_alias["article"])

            # 公开法规仍走原路径，不带 via 标记
            public = service.get_article(db_path, "民法典", "143")
            self.assertIsNotNone(public)
            self.assertNotIn("via", public)

            # include_norm=False 关闭 fallback
            disabled = service.get_article(
                db_path, "甲方放款要求", "2", include_norm=False
            )
            self.assertIsNone(disabled)

    def test_get_articles_falls_back_to_norm_source(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "t.db"
            loader.load_fixtures(db_path, FIXTURES)
            with connect(db_path) as conn:
                normsources.import_source_from_dict(conn, EXTRA_NORM_SOURCE_FIXTURE)

            payload = service.get_articles(db_path, "甲方放款要求", "1,2")
            self.assertIsNotNone(payload)
            self.assertEqual(payload.get("via"), "norm_fallback")
            self.assertEqual(payload["found_count"], 2)
            self.assertEqual(payload["missing_count"], 0)
            self.assertEqual(payload["item_count"], 2)
            for item in payload["items"]:
                self.assertTrue(item["found"])
                self.assertEqual(item["article"]["via"], "norm_fallback")

            # 部分缺失也能正确 missing_count 计数
            partial = service.get_articles(db_path, "甲方放款要求", "1,99")
            self.assertEqual(partial.get("via"), "norm_fallback")
            self.assertEqual(partial["found_count"], 1)
            self.assertEqual(partial["missing_count"], 1)

            disabled = service.get_articles(
                db_path, "甲方放款要求", "1,2", include_norm=False
            )
            self.assertIsNone(disabled)

    def test_get_articles_batch_mixes_law_and_norm(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "t.db"
            loader.load_fixtures(db_path, FIXTURES)
            with connect(db_path) as conn:
                normsources.import_source_from_dict(conn, EXTRA_NORM_SOURCE_FIXTURE)

            payload = service.get_articles_batch(
                db_path, "民法典:143;甲方放款要求:1,2"
            )
            self.assertIsNotNone(payload)
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["item_count"], 3)
            self.assertEqual(payload["found_count"], 3)
            sections = {s["name"]: s for s in payload["sections"]}
            self.assertIsNone(sections["民法典"]["result"].get("via"))
            self.assertEqual(
                sections["甲方放款要求"]["result"].get("via"), "norm_fallback"
            )

    def test_import_text_source_file_splits_numbered_clauses(self):
        import tempfile

        text = "\n".join(
            [
                "第一条 借款主体应提交完整、真实、有效的授权文件。",
                "第二条 涉及担保的，应确认审批程序。",
                "2.1 如担保人为关联方，还应提交关联交易审批材料。",
            ]
        )
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "t.db"
            text_file = Path(td) / "policy.txt"
            text_file.write_text(text, encoding="utf-8")
            imported = normsources.import_text_source_file(
                db_path,
                text_file,
                name="文本放款要求",
                source_type="lender_requirement",
            )
            shown = normsources.get_source(db_path, "文本放款要求")
            clause = normsources.get_clause(db_path, "文本放款要求", "2.1")

        self.assertEqual(imported["clauses_loaded"], 3)
        self.assertEqual(imported["ingest_format"], "txt")
        self.assertIsNotNone(shown)
        self.assertEqual(shown["clause_count"], 3)
        self.assertIsNotNone(clause)
        self.assertIn("关联交易审批", clause["clause"]["text"])

    def test_clauses_from_text_recognises_markdown_headings(self):
        text = "\n".join(
            [
                "# 文档标题",
                "",
                "## 第1条【条名一】",
                "正文一。",
                "",
                "## 第2条【条名二】",
                "正文二。",
                "",
                "## 第30条【条名三】",
                "正文三。",
            ]
        )
        clauses = normsources.clauses_from_text(text)
        self.assertEqual(len(clauses), 3)
        self.assertEqual(clauses[0]["number"], "第1条")
        self.assertEqual(clauses[0]["title"], "条名一")
        self.assertIn("【条名一】", clauses[0]["text"])
        self.assertIn("正文一", clauses[0]["text"])
        self.assertEqual(clauses[2]["number"], "第30条")

    def test_clauses_from_text_extracts_numeric_bracketed_titles(self):
        text = "\n".join(
            [
                "23. 【供应链金融平台纠纷案件的审理要点】正文一。",
                "正文二。",
                "24. 【保理合同纠纷案件的审理要点】正文三。",
            ]
        )
        clauses = normsources.clauses_from_text(text)
        self.assertEqual(len(clauses), 2)
        self.assertEqual(clauses[0]["number"], "23")
        self.assertEqual(clauses[0]["title"], "供应链金融平台纠纷案件的审理要点")
        self.assertIn("【供应链金融平台纠纷案件的审理要点】", clauses[0]["text"])
        self.assertIn("正文二", clauses[0]["text"])

    def test_clauses_from_text_keeps_arabic_subitems_inside_chinese_article(self):
        text = "\n".join(
            [
                "# 上市公司信息披露管理办法",
                "",
                "> 公布机关：中国证券监督管理委员会",
                "> 令号：证监会令第182号",
                "---",
                "",
                "## 第一章 总 则",
                "第一条　为了规范信息披露，制定本办法。",
                "第六十二条　本办法下列用语的含义：",
                "（四）上市公司的关联交易，是指……",
                "具有以下情形之一的法人（或者其他组织），为上市公司的关联法人：",
                "1．直接或者间接地控制上市公司的法人；",
                "2．由前项所述法人直接或者间接控制的法人；",
                "具有以下情形之一的自然人，为上市公司的关联自然人：",
                "1．直接或者间接持有上市公司百分之五以上股份的自然人；",
                "2．上市公司董事、监事及高级管理人员；",
                "第六十三条　本办法自公布之日起施行。",
            ]
        )

        clauses = normsources.clauses_from_text(text)

        self.assertEqual([c["number"] for c in clauses], ["第一条", "第六十二条", "第六十三条"])
        self.assertIn("1．直接或者间接地控制", clauses[1]["text"])
        self.assertIn("2．上市公司董事", clauses[1]["text"])
        self.assertNotIn("公布机关", clauses[0]["text"])

    def test_rebuild_clean_norm_replays_original_ingest_source(self):
        text = "\n".join(
            [
                "# 上市公司信息披露管理办法",
                "",
                "> 公布机关：中国证券监督管理委员会",
                "---",
                "第一条　为了规范信息披露，制定本办法。",
                "第六十二条　本办法下列用语的含义：",
                "具有以下情形之一的法人，为上市公司的关联法人：",
                "1．直接或者间接地控制上市公司的法人；",
                "2．由前项所述法人直接或者间接控制的法人；",
                "第六十三条　本办法自公布之日起施行。",
            ]
        )
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "t.db"
            source_file = Path(td) / "rule.md"
            source_file.write_text(text, encoding="utf-8")
            stale_payload = {
                "id": "norm-disclosure-rule",
                "name": "上市公司信息披露管理办法",
                "source_type": "csrc_rule",
                "source_name": str(source_file),
                "source_checked_at": "2026-05-17T00:00:00+00:00",
                "source_hash": "sha256-test",
                "metadata": {"ingest": {"path": str(source_file), "format": "md"}},
                "clauses": [
                    {"number": None, "number_display": None, "text": "> 公布机关：中国证券监督管理委员会\n---"},
                    {"number": "第一条", "number_display": "第一条", "text": "为了规范信息披露，制定本办法。"},
                    {"number": "第六十二条", "number_display": "第六十二条", "text": "本办法下列用语的含义：\n具有以下情形之一的法人，为上市公司的关联法人："},
                    {"number": "1", "number_display": "1", "text": "直接或者间接地控制上市公司的法人；"},
                    {"number": "2", "number_display": "2", "text": "由前项所述法人直接或者间接控制的法人；"},
                    {"number": "第六十三条", "number_display": "第六十三条", "text": "本办法自公布之日起施行。"},
                ],
            }
            with connect(db_path) as conn:
                normsources.import_source_from_dict(conn, stale_payload)

            preview = rebuild.rebuild_clean(
                db_path,
                norm="上市公司信息披露管理办法",
                dry_run=True,
            )
            result = rebuild.rebuild_clean(db_path, norm="上市公司信息披露管理办法")
            shown = normsources.get_source(db_path, "上市公司信息披露管理办法")

        self.assertTrue(preview["ok"])
        self.assertEqual(preview["norm_count"], 1)
        self.assertEqual(preview["changed_count"], 1)
        self.assertTrue(result["ok"])
        self.assertEqual(result["changed_count"], 1)
        self.assertEqual(shown["clause_count"], 3)
        clause62 = next(c for c in shown["clauses"] if c["number"] == "62")
        self.assertIn("1．直接或者间接地控制", clause62["text"])

    def test_import_text_source_preserves_aliases_metadata_and_source_fields(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "t.db"
            text_file = Path(td) / "draft.md"
            text_file.write_text("1. 【审理要点】正文。", encoding="utf-8")
            imported = normsources.import_text_source_file(
                db_path,
                text_file,
                name="金融审判纪要征求意见稿",
                source_id="private-finance-draft",
                short_name="金融审判纪要",
                source_type="unofficial_draft_reprint",
                aliases=["金融审判会议纪要", "金融审判纪要"],
                source_name="第三方转载",
                source_checked_at="2026-05-01T00:00:00+08:00",
                source_hash="sha256-test",
                metadata={
                    "verification": {
                        "official_site": "not_found",
                        "note": "未公开核验",
                    }
                },
            )
            exported = normsources.export_source(db_path, "金融审判会议纪要")

        self.assertEqual(imported["clauses_loaded"], 1)
        self.assertIsNotNone(exported)
        self.assertEqual(exported["aliases"], ["金融审判会议纪要", "金融审判纪要"])
        self.assertEqual(exported["source_name"], "第三方转载")
        self.assertEqual(exported["source_checked_at"], "2026-05-01T00:00:00+08:00")
        self.assertEqual(exported["source_hash"], "sha256-test")
        self.assertEqual(exported["metadata"]["ingest"]["format"], "md")
        self.assertEqual(exported["metadata"]["verification"]["official_site"], "not_found")
        self.assertEqual(exported["clauses"][0]["title"], "审理要点")

    def test_analyze_split_quality_warns_when_one_clause_for_long_text(self):
        text = "\n".join(
            ["本会议纪要总结了民商事审判中的若干常见问题。"] * 30
        )
        clauses = normsources.clauses_from_text(text)
        warnings = normsources.analyze_split_quality(text, clauses)
        self.assertEqual(len(clauses), 1)
        self.assertTrue(warnings)
        self.assertEqual(warnings[0]["code"], "single_clause_large_text")

    def test_analyze_split_quality_silent_for_normal_split(self):
        text = "\n".join(
            [
                "第一条 总则。" + "扩充补丁文字。" * 5,
                "第二条 适用范围。" + "扩充补丁文字。" * 5,
                "第三条 解释权。" + "扩充补丁文字。" * 5,
            ]
        )
        clauses = normsources.clauses_from_text(text)
        warnings = normsources.analyze_split_quality(text, clauses)
        self.assertEqual(len(clauses), 3)
        self.assertEqual(warnings, [])

    def test_read_source_text_supports_pdf_via_pdftotext(self):
        import subprocess
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            pdf_file = Path(td) / "policy.pdf"
            pdf_file.write_bytes(b"%PDF-1.4 placeholder")
            with (
                patch("chinalaw.normsources.shutil.which", return_value="/usr/bin/pdftotext"),
                patch("chinalaw.normsources.subprocess.run") as run,
            ):
                run.return_value = subprocess.CompletedProcess(
                    args=["pdftotext"],
                    returncode=0,
                    stdout="第一条 借款主体应提交授权文件。\n",
                    stderr="",
                )
                text = normsources.read_source_text(pdf_file)

        self.assertIn("第一条", text)
        run.assert_called_once()


class ApplicabilityTests(unittest.TestCase):
    def test_import_relation_and_query_applicable_rules(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "t.db"
            loader.load_fixtures(db_path, FIXTURES)
            imported = applicability.load_applicability_fixtures(
                db_path,
                APPLICABILITY_FIXTURES,
            )
            relation_report = service.relation(db_path, "民法典")
            current = service.applicable(
                db_path,
                as_of="2022-01-01",
                topic="合同效力",
                domain="litigation",
            )
            old = service.applicable(
                db_path,
                as_of="2019-01-01",
                topic="合同效力",
            )
            status_report = service.status(db_path)

        self.assertGreaterEqual(imported["relations_loaded"], 1)
        self.assertGreaterEqual(imported["rules_loaded"], 2)
        self.assertIn("合同效力", imported["topics"])
        self.assertGreaterEqual(relation_report["relation_count"], 1)
        self.assertTrue(
            any(rel["to_law_id"] == "flk-contract-law-1999" for rel in relation_report["relations"])
        )
        self.assertEqual(current["match_count"], 1)
        self.assertEqual(current["matches"][0]["primary_law_id"], "flk-civil-code-2020")
        self.assertEqual(current["matches"][0]["fallback_law_id"], "flk-contract-law-1999")
        self.assertTrue(
            any(item["law_id"] == "flk-contract-law-1999" for item in current["matches"][0]["needs_fetch"])
        )
        self.assertTrue(
            any(warning["code"] == "not_legal_conclusion" for warning in current["warnings"])
        )
        self.assertEqual(old["match_count"], 1)
        self.assertEqual(old["matches"][0]["primary_law_id"], "flk-contract-law-1999")
        self.assertTrue(
            any(item["reason"] == "missing_law" for item in old["matches"][0]["needs_fetch"])
        )
        self.assertGreaterEqual(status_report["law_relations"], 1)
        self.assertGreaterEqual(status_report["applicability_rules"], 2)

    def test_company_governance_rule_uses_current_fixture_stable_id(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "t.db"
            loader.load_fixtures(db_path, FIXTURES)
            applicability.load_applicability_fixtures(db_path, APPLICABILITY_FIXTURES)
            result = service.applicable(
                db_path,
                as_of="2025-01-01",
                topic="公司治理",
            )

        self.assertEqual(result["match_count"], 1)
        match = result["matches"][0]
        self.assertEqual(match["primary_law_id"], "flk-company-law-2024")
        self.assertIsNotNone(match["primary_law"])
        self.assertFalse(
            any(
                item["law_id"] == "flk-company-law-2024"
                for item in match["needs_fetch"]
            )
        )

    def test_applicable_no_match_is_grounding_warning_not_error(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "t.db"
            applicability.load_applicability_fixtures(db_path, APPLICABILITY_FIXTURES)
            result = service.applicable(
                db_path,
                as_of="2022-01-01",
                topic="竞业限制",
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["match_count"], 0)
        self.assertTrue(
            any(warning["code"] == "no_applicability_rule" for warning in result["warnings"])
        )

    def test_applicable_invalid_date_reports_error(self):
        result = service.applicable(":memory:", as_of="2022/01/01", topic="合同效力")
        self.assertFalse(result["ok"])
        self.assertTrue(
            any(warning["code"] == "invalid_date" for warning in result["warnings"])
        )


class AuditTests(unittest.TestCase):
    def _db(self, td: str) -> Path:
        db_path = Path(td) / "audit.db"
        loader.load_fixtures(db_path, FIXTURES)
        return db_path

    def test_extract_citations_reads_book_title_article(self):
        text = "依据《民法典》第一百四十三条规定：“具备下列条件的民事法律行为有效。”"
        citations = audit.extract_citations(text)
        self.assertEqual(len(citations), 1)
        self.assertEqual(citations[0]["law_input"], "民法典")
        self.assertEqual(citations[0]["number"], "143")
        self.assertIn("具备下列条件", citations[0]["quoted_text"])

    def test_audit_text_passes_exact_article_excerpt(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = self._db(td)
            report = audit.audit_text(
                db_path,
                "依据《民法典》第一百四十三条规定：“具备下列条件的民事法律行为有效。”",
            )
        self.assertTrue(report["ok"])
        self.assertEqual(report["citation_count"], 1)
        self.assertEqual(report["citations"][0]["law"]["status"], "current")
        self.assertEqual(report["citations"][0]["text_match"]["kind"], "exact_excerpt")

    def test_audit_text_does_not_treat_paraphrase_as_quoted_text(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = self._db(td)
            report = audit.audit_text(
                db_path,
                "依据《民法典》第一百四十三条规定，"
                "合同具备主体适格、意思表示真实且不违法时有效。",
            )
        self.assertTrue(report["ok"])
        self.assertEqual(report["citation_count"], 1)
        self.assertIsNone(report["citations"][0]["quoted_text"])
        self.assertEqual(report["citations"][0]["text_match"]["kind"], "not_checked")

    def test_audit_text_fails_wrong_quoted_text(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = self._db(td)
            report = audit.audit_text(
                db_path,
                "依据《民法典》第一百四十三条规定：“完全错误的引用内容不得通过审查。”",
            )
        self.assertFalse(report["ok"])
        codes = [issue["code"] for issue in report["citations"][0]["issues"]]
        self.assertIn("quoted_text_mismatch", codes)

    def test_audit_pack_checks_reference_citations(self):
        pack_payload = {
            "id": "audit-pack",
            "name": "审查测试包",
            "items": [
                {
                    "item_type": "reference",
                    "role": "core",
                    "reason": "测试 reference 里的法条引用",
                    "reference_text": "待核验：《民法典》第九千九百九十九条。",
                }
            ],
        }
        with tempfile.TemporaryDirectory() as td:
            db_path = self._db(td)
            with connect(db_path) as conn:
                normpacks.import_pack_from_dict(conn, pack_payload)
            report = audit.audit_pack(db_path, "审查测试包")
        self.assertFalse(report["ok"])
        self.assertEqual(report["citation_count"], 1)
        self.assertEqual(report["citations"][0]["diagnosis"]["reason"], "article_null")

    def test_audit_norm_checks_clause_embedded_citations(self):
        norm_payload = {
            "id": "audit-norm",
            "name": "审查私域规范",
            "source_type": "company_policy",
            "source_name": "test",
            "source_checked_at": "2026-05-01T00:00:00+08:00",
            "clauses": [
                {
                    "number": "1",
                    "number_display": "第一条",
                    "text": (
                        "本制度引用《民法典》第一百四十三条规定："
                        "具备下列条件的民事法律行为有效。"
                    ),
                }
            ],
        }
        with tempfile.TemporaryDirectory() as td:
            db_path = self._db(td)
            with connect(db_path) as conn:
                normsources.import_source_from_dict(conn, norm_payload)
            report = audit.audit_norm(db_path, "审查私域规范")
        self.assertTrue(report["ok"])
        self.assertEqual(report["citation_count"], 1)
        self.assertEqual(
            report["citations"][0]["container"]["clause_number_display"],
            "第一条",
        )

    def test_audit_grounding_verifies_article_snapshot(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = self._db(td)
            snapshot_path = Path(td) / "snapshot.jsonl"
            article_payload = service.get_article(db_path, "民法典", "143")
            assert article_payload is not None
            snapshots.append_command_record(
                snapshot_path,
                command="article",
                payload=article_payload,
                db_path=db_path,
                argv=["article", "民法典", "143"],
            )
            doc = Path(td) / "final.md"
            doc.write_text("合同效力依据《民法典》第一百四十三条判断。", encoding="utf-8")
            report = audit.audit_grounding_file(
                db_path,
                doc,
                snapshot_path=snapshot_path,
            )
        self.assertTrue(report["ok"])
        self.assertEqual(report["grounding_counts"]["verified"], 1)
        self.assertEqual(report["citations"][0]["grounding"]["evidence_id"], "E0001")

    def test_audit_grounding_flags_search_only_snapshot_in_strict_mode(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = self._db(td)
            snapshot_path = Path(td) / "snapshot.jsonl"
            search_payload = service.search(
                db_path,
                "民事法律行为有效",
                kind="article",
                limit=3,
            )
            snapshots.append_command_record(
                snapshot_path,
                command="search",
                payload=search_payload,
                db_path=db_path,
                argv=["search", "民事法律行为有效", "--kind", "article"],
            )
            doc = Path(td) / "final.md"
            doc.write_text("合同效力依据《民法典》第一百四十三条判断。", encoding="utf-8")
            report = audit.audit_grounding_file(
                db_path,
                doc,
                snapshot_path=snapshot_path,
                strict=True,
            )
        self.assertFalse(report["ok"])
        self.assertEqual(report["grounding_counts"]["retrieved_only"], 1)
        codes = [issue["code"] for issue in report["citations"][0]["issues"]]
        self.assertIn("retrieved_but_unverified", codes)


class CliTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import tempfile
        cls._tmpdir = tempfile.TemporaryDirectory()
        cls.db_path = Path(cls._tmpdir.name) / "t.db"
        loader.load_fixtures(cls.db_path, FIXTURES)
        applicability.load_applicability_fixtures(cls.db_path, APPLICABILITY_FIXTURES)
        with connect(cls.db_path) as conn:
            migrate(conn)
            loader.load_law_from_dict(conn, EXTRA_LAW_FIXTURE)
            normsources.import_source_from_dict(conn, EXTRA_NORM_SOURCE_FIXTURE)

    @classmethod
    def tearDownClass(cls):
        cls._tmpdir.cleanup()

    def _run(self, argv):
        import contextlib
        import io

        from chinalaw.cli import app
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = app([*argv, "--db", str(self.db_path)])
        return code, buf.getvalue()

    def test_cli_accepts_global_db_before_subcommand(self):
        import contextlib
        import io

        from chinalaw.cli import app

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = app(["--db", str(self.db_path), "status", "--format", "json"])

        self.assertEqual(code, 0)
        payload = json.loads(buf.getvalue())
        self.assertEqual(payload["db_path"], str(self.db_path))
        self.assertGreater(payload["laws"], 0)

    def test_cli_search_json(self):
        code, out = self._run(["search", "工作时间", "--limit", "3"])
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["query"], "工作时间")
        self.assertTrue(payload["article_hits"])

    def test_cli_search_joins_unquoted_multi_keyword_query(self):
        code, out = self._run(["search", "工作", "时间", "--limit", "3"])
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["query"], "工作 时间")
        self.assertTrue(payload["article_hits"])

    def test_cli_article_md(self):
        code, out = self._run(["article", "劳动法", "第三十六条", "--format", "md"])
        self.assertEqual(code, 0)
        self.assertIn("劳动法", out)
        self.assertIn("八小时", out)

    def test_cli_audit_file_json(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "memo.md"
            path.write_text(
                "依据《民法典》第一百四十三条规定：“具备下列条件的民事法律行为有效。”",
                encoding="utf-8",
            )
            code, out = self._run(["audit", "file", str(path)])
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["citation_count"], 1)

    def test_cli_snapshot_out_records_article_evidence(self):
        with tempfile.TemporaryDirectory() as td:
            snapshot_path = Path(td) / "snapshot.jsonl"
            code, _out = self._run(
                [
                    "article",
                    "民法典",
                    "143",
                    "--snapshot-out",
                    str(snapshot_path),
                ]
            )
            self.assertEqual(code, 0)
            record = json.loads(snapshot_path.read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual(record["evidence_id"], "E0001")
        self.assertEqual(record["command"], "article")
        self.assertEqual(record["articles"][0]["law_id"], "flk-civil-code-2020")
        self.assertEqual(record["articles"][0]["number"], "143")
        self.assertEqual(record["articles"][0]["evidence_level"], "article")

    def test_cli_audit_grounding_uses_snapshot(self):
        with tempfile.TemporaryDirectory() as td:
            snapshot_path = Path(td) / "snapshot.jsonl"
            doc = Path(td) / "final.md"
            doc.write_text("合同效力依据《民法典》第一百四十三条判断。", encoding="utf-8")
            code, _out = self._run(
                [
                    "article",
                    "民法典",
                    "143",
                    "--snapshot-out",
                    str(snapshot_path),
                ]
            )
            self.assertEqual(code, 0)
            code, out = self._run(
                [
                    "audit",
                    "grounding",
                    str(doc),
                    "--snapshot",
                    str(snapshot_path),
                ]
            )
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["grounding_counts"]["verified"], 1)

    def test_cli_snapshot_init_enables_project_auto_recording(self):
        with tempfile.TemporaryDirectory() as td:
            project = Path(td)
            code, out = self._run(["snapshot", "init", str(project)])
            self.assertEqual(code, 0)
            init_payload = json.loads(out)
            snapshot_path = Path(init_payload["snapshot_path"])
            self.assertTrue(snapshot_path.exists())

            old_cwd = os.getcwd()
            try:
                os.chdir(project)
                code, _out = self._run(["article", "民法典", "143"])
            finally:
                os.chdir(old_cwd)
            self.assertEqual(code, 0)

            status = snapshots.snapshot_status(snapshot_path, project_path=project)
            self.assertEqual(status["record_count"], 1)
            self.assertEqual(status["commands"], {"article": 1})

    def test_cli_audit_grounding_auto_discovers_project_snapshot(self):
        with tempfile.TemporaryDirectory() as td:
            project = Path(td)
            doc = project / "reports" / "final.md"
            doc.parent.mkdir()
            doc.write_text("合同效力依据《民法典》第一百四十三条判断。", encoding="utf-8")

            code, _out = self._run(["snapshot", "init", str(project)])
            self.assertEqual(code, 0)
            old_cwd = os.getcwd()
            try:
                os.chdir(project)
                code, _out = self._run(["article", "民法典", "143"])
            finally:
                os.chdir(old_cwd)
            self.assertEqual(code, 0)

            code, out = self._run(["audit", "grounding", str(doc)])
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["grounding_counts"]["verified"], 1)

    def test_cli_resolve_json_short_title(self):
        code, out = self._run(["resolve", "民法典"])
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertTrue(payload["matched"])
        self.assertEqual(payload["via"], "short_title_match")
        self.assertEqual(payload["official_title"], "中华人民共和国民法典")
        self.assertIn("民法典", payload["aliases"])

    def test_cli_resolve_md_format_includes_via_label(self):
        code, out = self._run(["resolve", "民法典", "--format", "md"])
        self.assertEqual(code, 0)
        self.assertIn("中华人民共和国民法典", out)
        self.assertIn("命中路径", out)
        self.assertIn("short_title_match", out)

    def test_cli_resolve_unmatched_exits_one_with_hint(self):
        code, out = self._run(["resolve", "完全不存在的法", "--format", "md"])
        self.assertEqual(code, 1)
        self.assertIn("未找到", out)
        self.assertIn("--list-matches", out)

    def test_cli_article_falls_back_to_norm_source_md(self):
        code, out = self._run(
            ["article", "甲方放款要求", "2", "--format", "md"]
        )
        self.assertEqual(code, 0)
        self.assertIn("放款要求", out)
        self.assertIn("担保", out)

    def test_cli_article_falls_back_to_norm_source_json(self):
        code, out = self._run(
            ["article", "甲方放款要求", "2", "--format", "json"]
        )
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload.get("via"), "norm_fallback")
        self.assertEqual(payload["law"]["title"], "甲方放款要求（示例）")
        self.assertEqual(payload["item"], payload["article"])
        self.assertIn("担保", payload["article"]["text"])

    def test_cli_article_no_norm_fallback_disables(self):
        code, out = self._run(
            [
                "article",
                "甲方放款要求",
                "2",
                "--format",
                "md",
                "--no-norm-fallback",
            ]
        )
        self.assertEqual(code, 1)
        self.assertIn("未找到", out)

    def test_cli_article_law_missing_emits_diagnosis(self):
        """法规整体不在 DB → reason=law_missing + 完整 fetch 命令。"""
        code, out = self._run(
            ["article", "完全不存在的法律XX", "1", "--format", "json"]
        )
        self.assertEqual(code, 1)
        payload = json.loads(out)
        self.assertFalse(payload["found"])
        self.assertEqual(payload["reason"], "law_missing")
        self.assertIsNone(payload["law_id"])
        self.assertIn("chinalaw fetch", payload["suggested_fetch"])
        self.assertIn("--source flk_npc", payload["suggested_fetch"])
        self.assertIn("court_gongbao", payload["fallback_sources"])
        self.assertIn("court_main", payload["fallback_sources"])
        self.assertIn("spp_gov_cn", payload["fallback_sources"])

    def test_cli_article_article_null_emits_diagnosis(self):
        """法规在但条文 9999 不存在 → reason=article_null + 建议 outline + force fetch。"""
        code, out = self._run(["article", "示例法", "9999", "--format", "json"])
        self.assertEqual(code, 1)
        payload = json.loads(out)
        self.assertFalse(payload["found"])
        self.assertEqual(payload["reason"], "article_null")
        # 法规已解析 → law_id 非空，且 article 字段保留为 null
        self.assertIsNotNone(payload["law_id"])
        self.assertIsNone(payload["article"])
        self.assertIn("chinalaw outline", payload["suggested_outline"])
        self.assertIn("--force", payload["suggested_fetch"])

    def test_cli_article_law_stub_emits_diagnosis(self):
        """法规 row 在但正文为空 → reason=law_stub，提示 fetch 补正文。"""
        import contextlib
        import io
        import tempfile

        from chinalaw.cli import app

        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "stub.db"
            with connect(db_path) as conn:
                migrate(conn)
                loader.load_law_from_dict(
                    conn,
                    {
                        "id": "stub-test-law",
                        "title": "中华人民共和国示例空法",
                        "short_title": "示例空法",
                        "aliases": ["示例空法"],
                        "level": "law",
                        "status": "current",
                        "issuing_body": "测试机关",
                        "released_at": "2024-01-01",
                        "effective_at": "2024-01-01",
                        "source_url": "https://example.test/stub-law",
                        "source_name": "example.test",
                        "source_checked_at": "2026-05-03T00:00:00+00:00",
                        "articles": [],
                    },
                )

            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                code = app(
                    [
                        "article",
                        "示例空法",
                        "1",
                        "--format",
                        "json",
                        "--db",
                        str(db_path),
                    ]
                )

        self.assertEqual(code, 1)
        payload = json.loads(buf.getvalue())
        self.assertFalse(payload["found"])
        self.assertEqual(payload["reason"], "law_stub")
        self.assertEqual(payload["law_id"], "stub-test-law")
        self.assertIn("chinalaw fetch", payload["suggested_fetch"])

    def test_cli_article_invalid_as_of_emits_date_diagnosis(self):
        """as-of 日期格式错 → 不应误导 agent 去 fetch 当前法。"""
        code, out = self._run(
            ["article", "民法典", "143", "--as-of", "bad-date", "--format", "json"]
        )
        self.assertEqual(code, 1)
        payload = json.loads(out)
        self.assertFalse(payload["found"])
        self.assertEqual(payload["reason"], "invalid_as_of")
        self.assertEqual(payload["as_of"], "bad-date")
        self.assertNotIn("suggested_fetch", payload)

    def test_cli_article_as_of_before_first_revision_emits_version_diagnosis(self):
        """法规存在但 as-of 时点无版本 → 提示 history，不应提示 fetch --force。"""
        code, out = self._run(
            ["article", "民法典", "143", "--as-of", "2019-01-01", "--format", "json"]
        )
        self.assertEqual(code, 1)
        payload = json.loads(out)
        self.assertFalse(payload["found"])
        self.assertEqual(payload["reason"], "version_not_found_as_of")
        self.assertEqual(payload["as_of"], "2019-01-01")
        self.assertIn("chinalaw history", payload["suggested_history"])
        self.assertNotIn("suggested_fetch", payload)

    def test_cli_article_as_of_article_null_does_not_suggest_current_fetch(self):
        """时点版本中条文缺失 → 先查 history，不用当前版本 fetch 伪修复。"""
        code, out = self._run(
            ["article", "劳动法", "9999", "--as-of", "2020-01-01", "--format", "json"]
        )
        self.assertEqual(code, 1)
        payload = json.loads(out)
        self.assertFalse(payload["found"])
        self.assertEqual(payload["reason"], "article_null_as_of")
        self.assertEqual(payload["as_of"], "2020-01-01")
        self.assertIn("chinalaw history", payload["suggested_history"])
        self.assertNotIn("suggested_fetch", payload)

    def test_cli_article_md_includes_hint_on_miss(self):
        """MD 输出也带"诊断 [reason]: hint"行，方便人类复核。"""
        code, out = self._run(
            ["article", "完全不存在的法律XX", "1", "--format", "md"]
        )
        self.assertEqual(code, 1)
        self.assertIn("诊断", out)
        self.assertIn("law_missing", out)
        self.assertIn("chinalaw fetch", out)

    def test_cli_articles_batch_mixes_law_and_norm(self):
        code, out = self._run(
            [
                "articles",
                "--batch",
                "民法典:143;甲方放款要求:1,2",
                "--format",
                "json",
            ]
        )
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["item_count"], 3)
        self.assertEqual(payload["found_count"], 3)
        sections = {s["name"]: s for s in payload["sections"]}
        self.assertIsNone(sections["民法典"]["result"].get("via"))
        self.assertEqual(
            sections["甲方放款要求"]["result"].get("via"), "norm_fallback"
        )

    def test_cli_article_md_no_footer(self):
        code, out = self._run(
            ["article", "劳动法", "36", "--format", "md", "--no-footer"]
        )
        self.assertEqual(code, 0)
        self.assertIn("八小时", out)
        self.assertNotIn("来源：", out)
        self.assertNotIn("最后核查：", out)

    def test_cli_article_md_compact_footer_and_arabic_number(self):
        code, out = self._run(
            ["article", "民法典", "143", "--format", "md", "--compact", "--arabic"]
        )
        self.assertEqual(code, 0)
        self.assertIn("## 民法典 第143条", out)
        self.assertIn("[current", out)
        self.assertIn("核查", out)
        self.assertNotIn("source_checked_at", out)
        self.assertNotIn("来源：", out)

    def test_cli_article_md_section_number(self):
        code, out = self._run(
            ["article", "劳动法", "36", "--format", "md", "--section", "--no-footer"]
        )
        self.assertEqual(code, 0)
        self.assertIn("## 劳动法 §36", out)
        self.assertNotIn("第三十六条", out)
        self.assertNotIn("来源：", out)

    def test_cli_article_md_arabic_section_are_mutually_exclusive(self):
        with self.assertRaises(SystemExit) as ctx:
            self._run(
                ["article", "劳动法", "36", "--format", "md", "--arabic", "--section"]
            )
        self.assertEqual(ctx.exception.code, 2)

    def test_cli_article_md_with_title_renders_when_present(self):
        # 劳动法 fixture 当前不带 article.title，--with-title 不应破坏输出。
        code, out = self._run(
            ["article", "劳动法", "36", "--format", "md", "--with-title", "--no-footer"]
        )
        self.assertEqual(code, 0)
        self.assertIn("## 劳动法", out)
        self.assertNotIn("【】", out)

    def test_cli_articles_batch_json(self):
        code, out = self._run(["articles", "民法典", "--numbers", "5,12,23-25"])
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["kind"], "law_articles")
        self.assertEqual(payload["found_count"], 5)
        self.assertEqual(payload["missing_count"], 0)
        self.assertEqual(payload["articles"], payload["items"])

    def test_cli_articles_accepts_positional_numbers(self):
        code, out = self._run(["articles", "民法典", "5,12,23-25"])
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["found_count"], 5)
        self.assertEqual(payload["missing_count"], 0)

    def test_cli_articles_requires_numbers(self):
        code, out = self._run(["articles", "民法典"])
        self.assertEqual(code, 2)
        payload = json.loads(out)
        self.assertEqual(payload["error"], "MissingNumbers")

    def test_cli_articles_batch_missing_exits_one(self):
        code, out = self._run(["articles", "民法典", "--numbers", "5,99999"])
        self.assertEqual(code, 1)
        payload = json.loads(out)
        self.assertEqual(payload["found_count"], 1)
        self.assertEqual(payload["missing_count"], 1)

    def test_cli_articles_md_default_has_summary_header(self):
        code, out = self._run(
            ["articles", "民法典", "5,12", "--format", "md"]
        )
        self.assertEqual(code, 0)
        self.assertIn("批量取条", out)
        self.assertIn("- 请求：", out)

    def test_cli_articles_md_no_footer_strips_summary(self):
        code, out = self._run(
            ["articles", "民法典", "5,12", "--format", "md", "--no-footer"]
        )
        self.assertEqual(code, 0)
        self.assertNotIn("批量取条", out)
        self.assertNotIn("- 请求：", out)
        self.assertNotIn("- 命中：", out)

    def test_cli_articles_md_compact_appends_single_line_footer(self):
        code, out = self._run(
            ["articles", "民法典", "5,12", "--format", "md", "--compact"]
        )
        self.assertEqual(code, 0)
        self.assertIn("[current", out)
        self.assertIn("核查", out)

    def test_cli_articles_md_section_number(self):
        code, out = self._run(
            ["articles", "民法典", "5,12", "--format", "md", "--section", "--no-footer"]
        )
        self.assertEqual(code, 0)
        self.assertIn("## §5", out)
        self.assertIn("## §12", out)

    def test_cli_articles_md_arabic_section_are_mutually_exclusive(self):
        with self.assertRaises(SystemExit) as ctx:
            self._run(
                ["articles", "民法典", "5", "--format", "md", "--arabic", "--section"]
            )
        self.assertEqual(ctx.exception.code, 2)

    def test_article_to_markdown_with_title_renders_title(self):
        payload = {
            "law": {
                "id": "demo",
                "title": "民法典",
                "short_title": "民法典",
                "status": "current",
                "source_url": "https://example/x",
            },
            "article": {
                "number": "545",
                "number_display": "第五百四十五条",
                "title": "债权让与一般规则",
                "text": "债权人可以将债权的全部或者部分转让给第三人……",
            },
        }
        md = formatters.article_to_markdown(
            payload,
            footer="none",
            number_style="section",
            with_title=True,
        )
        self.assertIn("§545 【债权让与一般规则】", md)
        self.assertNotIn("来源", md)

    def test_articles_to_markdown_with_title_renders_per_item(self):
        payload = {
            "kind": "law_articles",
            "law": {
                "id": "demo",
                "title": "民法典",
                "short_title": "民法典",
                "status": "current",
            },
            "item_count": 1,
            "found_count": 1,
            "missing_count": 0,
            "items": [
                {
                    "requested_number": "545",
                    "number": "545",
                    "article": {
                        "number": "545",
                        "number_display": "第五百四十五条",
                        "title": "债权让与一般规则",
                        "text": "债权人可以将债权的全部或者部分转让给第三人……",
                    },
                }
            ],
        }
        md = formatters.articles_to_markdown(
            payload,
            number_style="section",
            footer="none",
            with_title=True,
        )
        self.assertIn("§545 【债权让与一般规则】", md)
        self.assertNotIn("批量取条", md)

    def test_aliases_include_侵权责任编解释(self):
        """民法典侵权责任编解释（一）alias 覆盖（issuer=spc + 民法典分编规则）。"""

        from chinalaw.aliases import common_law_aliases

        title = (
            "最高人民法院关于适用《中华人民共和国民法典》侵权责任编的解释（一）"
        )
        aliases = common_law_aliases(title)
        self.assertIn("侵权责任编解释一", aliases)
        self.assertIn("侵权责任编解释（一）", aliases)
        # （一）默认就是当前唯一一份，去掉序号也应命中
        self.assertIn("侵权责任编解释", aliases)
        self.assertIn("民法典侵权责任编解释", aliases)

    def test_aliases_general_appliesinterp_rule(self):
        """通用规则：「关于适用《X》的解释」→ 自动派生 X解释 + 诉讼法缩写。

        意图：取代逐 case 硬编码各种司法解释简称的旧路径，未来新出
        同模板司法解释自动覆盖。覆盖 spec §1.4 反例表的诉讼法行。
        """

        from chinalaw.aliases import common_law_aliases

        # 行政诉讼法解释：圈内最常见简称是"行诉解释"，必须覆盖
        admin = common_law_aliases(
            "最高人民法院关于适用《中华人民共和国行政诉讼法》的解释"
        )
        self.assertIn("行政诉讼法解释", admin)
        self.assertIn("行诉法解释", admin)
        self.assertIn("行诉解释", admin)
        self.assertNotIn("行政诉法解释", admin)

        # 民事诉讼法解释：与原硬编码一致，不破坏既有别名
        civil = common_law_aliases(
            "最高人民法院关于适用《中华人民共和国民事诉讼法》的解释"
        )
        self.assertIn("民事诉讼法解释", civil)
        self.assertIn("民诉法解释", civil)
        self.assertNotIn("民事诉法解释", civil)

        # 上游 / 用户输入偶尔会丢掉书名号；仍应走同一诉讼法模板。
        civil_unquoted = common_law_aliases(
            "最高人民法院关于适用中华人民共和国民事诉讼法的解释"
        )
        self.assertIn("民事诉讼法解释", civil_unquoted)
        self.assertIn("民诉法解释", civil_unquoted)

        criminal = common_law_aliases(
            "最高人民法院关于适用《中华人民共和国刑事诉讼法》的解释"
        )
        self.assertIn("刑事诉讼法解释", criminal)
        self.assertIn("刑诉法解释", criminal)
        self.assertIn("刑诉解释", criminal)

        # 非诉讼法格式（行政复议法）：只派生 X解释，不应误派生缩写
        review = common_law_aliases(
            "最高人民法院关于适用《中华人民共和国行政复议法》的解释"
        )
        self.assertIn("行政复议法解释", review)
        self.assertNotIn("行复解释", review)
        self.assertNotIn("复议解释", review)

    def test_aliases_special_subject_rules(self):
        """常用简称应来自标题模板，而不是为单个笔记项目写死。"""

        from chinalaw.aliases import common_law_aliases, preferred_short_title

        env = common_law_aliases(
            "最高人民法院关于审理生态环境侵权责任纠纷案件适用法律若干问题的解释"
        )
        self.assertIn("生态环境侵权责任纠纷解释", env)
        self.assertIn("生态环境侵权解释", env)

        lending = common_law_aliases(
            "最高人民法院关于审理民间借贷案件适用法律若干问题的规定"
        )
        self.assertIn("民间借贷规定", lending)

        sale = common_law_aliases(
            "最高人民法院关于审理买卖合同纠纷案件适用法律问题的解释"
        )
        self.assertIn("买卖合同解释", sale)

        evidence = common_law_aliases("最高人民法院关于民事诉讼证据的若干规定")
        self.assertIn("民事诉讼证据规定", evidence)

        article_reply = common_law_aliases(
            "最高人民法院关于《中华人民共和国公司法》第八十八条第一款不溯及适用的批复"
        )
        self.assertIn("公司法第八十八条批复", article_reply)
        self.assertIn("公司法八十八条批复", article_reply)

        company_time = common_law_aliases(
            "最高人民法院关于适用《中华人民共和国公司法》时间效力的若干规定"
        )
        self.assertEqual(company_time[0], "公司法时间效力规定")
        self.assertIn("公司法时间效力解释", company_time)
        self.assertIn("公司法时效规定", company_time)
        self.assertEqual(
            preferred_short_title(
                "最高人民法院关于适用《中华人民共和国公司法》时间效力的若干规定"
            ),
            "公司法时间效力规定",
        )

    def test_aliases_issuer_spc_judicial_interpretation(self):
        """覆盖 spec §1.4 反例表前 3 行（公司法 / 保险法 issuer=spc 派生）。"""

        from chinalaw.aliases import common_law_aliases

        # 公司法解释一：标题用「若干问题的规定」（最高法发的"规定"圈内仍叫"解释"）
        co1 = common_law_aliases(
            "最高人民法院关于适用《公司法》若干问题的规定（一）"
        )
        self.assertIn("公司法解释一", co1)
        self.assertIn("公司法解释（一）", co1)
        self.assertIn("公司法解释", co1)  # ordinal=='一' → 去序号

        # 公司法解释五：ordinal!='一' → 不去序号
        co5 = common_law_aliases(
            "最高人民法院关于适用《公司法》若干问题的规定（五）"
        )
        self.assertIn("公司法解释五", co5)
        self.assertIn("公司法解释（五）", co5)
        self.assertNotIn("公司法解释", co5)

        # 保险法解释（一）：标题就是「若干问题的解释」
        ins = common_law_aliases(
            "最高人民法院关于适用《保险法》若干问题的解释（一）"
        )
        self.assertIn("保险法解释一", ins)
        self.assertIn("保险法解释（一）", ins)
        self.assertIn("保险法解释", ins)

    def test_aliases_issuer_guowuyuan_returns_empty(self):
        """国务院发布 → 不派生任何圈内别名（避免误标记成"解释"）。

        覆盖 spec §1.4 反例表第 7 行。特定国务院规范的简称应写入
        fixture / DB aliases，不进入通用派生规则。
        """

        from chinalaw.aliases import common_law_aliases

        result = common_law_aliases(
            "国务院关于修改和废止部分行政法规的决定"
        )
        self.assertEqual(result, [])

        registered_capital = common_law_aliases(
            "国务院关于实施《中华人民共和国公司法》注册资本登记管理制度的规定"
        )
        self.assertEqual(registered_capital, [])

    def test_aliases_statute_titles_get_direct_short_aliases(self):
        """法律本身派生直接短称，但不走司法解释规则。"""

        from chinalaw.aliases import common_law_aliases

        self.assertEqual(common_law_aliases("中华人民共和国公司法"), ["公司法"])
        self.assertEqual(common_law_aliases("中华人民共和国民法典"), ["民法典"])

    def test_aliases_unknown_issuer_returns_empty(self):
        """不识别的发布主体（部委、地方等）→ 放过，交给 fixture aliases。"""

        from chinalaw.aliases import common_law_aliases

        # 司法部 / 部委发布的"规定" 不在 issuer 表里
        self.assertEqual(
            common_law_aliases("司法部关于律师执业管理的规定"),
            [],
        )
        # 检察院"刑事诉讼规则" 标题不是「适用《X》」模式 → 抽不到 base，返回空
        self.assertEqual(common_law_aliases("最高人民检察院刑事诉讼规则"), [])

    def test_aliases_minfadian_book_no_ordinal(self):
        """《民法典》总则编 / 合同编通则等无序号情形：派生 ``X解释`` + ``民法典X解释``。

        意图：替代旧硬编码的「合同编通则解释 / 总则编解释」分支。
        """

        from chinalaw.aliases import common_law_aliases

        general = common_law_aliases(
            "最高人民法院关于适用《中华人民共和国民法典》总则编若干问题的解释"
        )
        self.assertIn("总则编解释", general)
        self.assertIn("民法典总则编解释", general)

        contract = common_law_aliases(
            "最高人民法院关于适用《中华人民共和国民法典》合同编通则若干问题的解释"
        )
        self.assertIn("合同编通则解释", contract)
        self.assertIn("民法典合同编通则解释", contract)

    def test_cli_article_md_bare_outputs_text_only(self):
        code, out = self._run(
            ["article", "劳动法", "36", "--format", "md", "--bare"]
        )
        self.assertEqual(code, 0)
        self.assertIn("八小时", out)
        self.assertNotIn("##", out)
        self.assertNotIn("劳动法", out)
        self.assertNotIn("---", out)

    def test_cli_article_md_inline_outputs_single_line(self):
        code, out = self._run(
            ["article", "劳动法", "36", "--format", "md", "--inline"]
        )
        self.assertEqual(code, 0)
        non_empty = [line for line in out.splitlines() if line.strip()]
        self.assertEqual(len(non_empty), 1)
        self.assertTrue(non_empty[0].startswith("劳动法§36 "))
        self.assertIn("八小时", non_empty[0])

    def test_cli_article_card_outputs_compact_text_and_source(self):
        code, out = self._run(["article", "劳动法", "36", "--format", "card"])
        self.assertEqual(code, 0)
        non_empty = [line for line in out.splitlines() if line.strip()]
        self.assertEqual(len(non_empty), 2)
        self.assertTrue(non_empty[0].startswith("《中华人民共和国劳动法》§36: "))
        self.assertIn("八小时", non_empty[0])
        self.assertTrue(non_empty[1].startswith("source: "))

    def test_cli_article_bare_inline_are_mutually_exclusive(self):
        with self.assertRaises(SystemExit) as ctx:
            self._run(
                ["article", "劳动法", "36", "--format", "md", "--bare", "--inline"]
            )
        self.assertEqual(ctx.exception.code, 2)

    def test_cli_articles_md_bare_blocks(self):
        code, out = self._run(
            ["articles", "民法典", "5,12", "--format", "md", "--bare"]
        )
        self.assertEqual(code, 0)
        self.assertNotIn("##", out)
        self.assertNotIn("批量取条", out)
        blocks = [b for b in out.split("\n\n") if b.strip()]
        self.assertEqual(len(blocks), 2)

    def test_cli_articles_md_inline_one_line_per_article(self):
        code, out = self._run(
            ["articles", "民法典", "5,12", "--format", "md", "--inline"]
        )
        self.assertEqual(code, 0)
        non_empty = [line for line in out.splitlines() if line.strip()]
        self.assertEqual(len(non_empty), 2)
        self.assertTrue(non_empty[0].startswith("民法典§5 "))
        self.assertTrue(non_empty[1].startswith("民法典§12 "))

    def test_cli_articles_batch_json_aggregates_counts(self):
        code, out = self._run(
            ["articles", "--batch", "民法典:5,12;劳动法:36"]
        )
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["kind"], "law_articles_batch")
        self.assertEqual(payload["law_count"], 2)
        self.assertEqual(payload["item_count"], 3)
        self.assertEqual(payload["found_count"], 3)
        self.assertEqual(payload["missing_count"], 0)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["failed_section_count"], 0)
        self.assertEqual(payload["error_count"], 0)
        self.assertEqual(len(payload["sections"]), 2)

    def test_cli_articles_batch_json_reports_failed_sections(self):
        code, out = self._run(
            ["articles", "--batch", "民法典:5;不存在法:1;劳动法:"]
        )
        self.assertEqual(code, 1)
        payload = json.loads(out)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["found_count"], 1)
        self.assertEqual(payload["missing_count"], 0)
        self.assertEqual(payload["failed_section_count"], 2)
        self.assertEqual(payload["error_count"], 2)
        self.assertEqual(
            [s["error"] for s in payload["sections"]],
            [None, "law_not_found", "missing_numbers"],
        )

    def test_cli_articles_batch_md_default_summary_and_per_law_section(self):
        code, out = self._run(
            ["articles", "--batch", "民法典:5;劳动法:36", "--format", "md"]
        )
        self.assertEqual(code, 0)
        self.assertIn("多法批量取条", out)
        self.assertIn("- 状态：通过", out)
        self.assertIn("- 法规数：2", out)
        self.assertIn("## 民法典", out)
        self.assertIn("## 劳动法", out)

    def test_cli_articles_batch_md_inline_one_line_per_article(self):
        code, out = self._run(
            [
                "articles",
                "--batch",
                "民法典:5,12;劳动法:36",
                "--format",
                "md",
                "--inline",
            ]
        )
        self.assertEqual(code, 0)
        non_empty = [line for line in out.splitlines() if line.strip()]
        self.assertEqual(len(non_empty), 3)
        self.assertTrue(non_empty[0].startswith("民法典§5 "))
        self.assertTrue(non_empty[1].startswith("民法典§12 "))
        self.assertTrue(non_empty[2].startswith("劳动法§36 "))

    def test_cli_articles_batch_md_bare_blocks_per_article(self):
        code, out = self._run(
            ["articles", "--batch", "民法典:5;劳动法:36", "--format", "md", "--bare"]
        )
        self.assertEqual(code, 0)
        self.assertNotIn("##", out)
        self.assertNotIn("批量取条", out)
        blocks = [b for b in out.split("\n\n") if b.strip()]
        self.assertEqual(len(blocks), 2)

    def test_cli_articles_requires_law_or_batch(self):
        code, out = self._run(["articles"])
        self.assertEqual(code, 2)
        payload = json.loads(out)
        self.assertEqual(payload["error"], "MissingLaw")

    def test_parse_articles_batch_spec_handles_fullwidth_separators(self):
        from chinalaw.service import parse_articles_batch_spec

        sections = parse_articles_batch_spec("民法典：5,12；劳动法:36")
        self.assertEqual(sections, [("民法典", "5,12"), ("劳动法", "36")])

    def test_cli_outline_json(self):
        code, out = self._run(
            ["outline", "民法典", "--part", "自然人", "--preview-chars", "16"]
        )
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["kind"], "law_outline")
        self.assertTrue(payload["items"])
        self.assertEqual(payload["articles"], payload["items"])
        self.assertEqual(payload["text_mode"], "preview")
        self.assertLessEqual(len(payload["items"][0]["text_preview"]), 16)

    def test_cli_outline_full_text_alias_json(self):
        code, out = self._run(
            ["outline", "民法典", "--part", "自然人", "--full-text"]
        )
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["kind"], "law_outline")
        self.assertEqual(payload["text_mode"], "full")
        self.assertTrue(payload["full_text"])
        self.assertTrue(payload["items"][0]["text"])
        self.assertEqual(payload["items"][0]["text"], payload["items"][0]["article"]["text"].strip())

    def test_cli_outline_handles_broken_pipe_without_traceback(self):
        import contextlib
        import io

        from chinalaw.cli import app

        class BrokenPipeStdout(io.StringIO):
            def write(self, text):
                raise BrokenPipeError

        with contextlib.redirect_stdout(BrokenPipeStdout()):
            code = app(
                [
                    "outline",
                    "民法典",
                    "--format",
                    "json",
                    "--db",
                    str(self.db_path),
                ]
            )
        self.assertEqual(code, 0)

    def test_cli_outline_with_text_md(self):
        code, out = self._run(
            [
                "outline",
                "民法典",
                "--part",
                "自然人",
                "--with-text",
                "--no-footer",
                "--format",
                "md",
            ]
        )
        self.assertEqual(code, 0)
        self.assertNotIn("章节正文", out)
        self.assertIn("## ", out)
        self.assertIn("> ", out)
        self.assertNotIn("text_preview", out)

    def test_cli_outline_with_text_bare(self):
        code, out = self._run(
            [
                "outline",
                "民法典",
                "--part",
                "自然人",
                "--with-text",
                "--bare",
                "--format",
                "md",
            ]
        )
        self.assertEqual(code, 0)
        self.assertNotIn("##", out)
        self.assertNotIn("章节正文", out)
        self.assertTrue(out.strip())

    def test_cli_outline_with_text_inline_section(self):
        code, out = self._run(
            [
                "outline",
                "民法典",
                "--part",
                "自然人",
                "--with-text",
                "--inline",
                "--section",
                "--format",
                "md",
            ]
        )
        self.assertEqual(code, 0)
        for line in [ln for ln in out.splitlines() if ln.strip()]:
            self.assertIn("§", line)
        self.assertNotIn("\n\n", out.rstrip())

    def test_cli_outline_without_with_text_keeps_preview(self):
        code, out = self._run(
            [
                "outline",
                "民法典",
                "--part",
                "自然人",
                "--format",
                "md",
            ]
        )
        self.assertEqual(code, 0)
        self.assertIn("条文目录", out)
        self.assertIn("章节过滤：自然人", out)

    def test_cli_outline_with_text_arabic_section_mutex(self):
        with self.assertRaises(SystemExit) as ctx:
            self._run(
                [
                    "outline",
                    "民法典",
                    "--part",
                    "自然人",
                    "--with-text",
                    "--arabic",
                    "--section",
                    "--format",
                    "md",
                ]
            )
        self.assertEqual(ctx.exception.code, 2)

    def test_cli_outline_with_text_bare_inline_mutex(self):
        with self.assertRaises(SystemExit) as ctx:
            self._run(
                [
                    "outline",
                    "民法典",
                    "--part",
                    "自然人",
                    "--with-text",
                    "--bare",
                    "--inline",
                    "--format",
                    "md",
                ]
            )
        self.assertEqual(ctx.exception.code, 2)

    def test_cli_cited_by_default_json(self):
        code, out = self._run(["cited-by", "民法典:522"])
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["kind"], "law_article_cited_by")
        self.assertEqual(payload["target"]["normalized_number"], "522")
        self.assertFalse(payload["include_self"])
        self.assertGreaterEqual(payload["hit_count"], 1)
        host_ids = {hit["law"]["id"] for hit in payload["hits"]}
        self.assertNotIn("flk-civil-code-2020", host_ids)

    def test_cli_cited_by_in_filter(self):
        code, out = self._run(
            ["cited-by", "民法典:522", "--in", "合通解释"]
        )
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertTrue(payload["hits"])
        for hit in payload["hits"]:
            self.assertIn("合同编通则", hit["law"]["title"])

    def test_cli_cited_by_md_format_no_double_prefix(self):
        code, out = self._run(["cited-by", "民法典:522", "--format", "md"])
        self.assertEqual(code, 0)
        # Markdown 标题应是「第五百二十二条」而非「第第五百二十二条条」
        self.assertNotIn("第第", out)
        self.assertNotIn("条条", out)
        self.assertIn("引用追溯", out)
        self.assertIn("民法典第五百二十二条", out)

    def test_cli_cited_by_invalid_spec(self):
        code, out = self._run(["cited-by", "民法典"])
        self.assertEqual(code, 2)
        payload = json.loads(out)
        self.assertEqual(payload["error"], "InvalidSpec")

    def test_cli_search_in_json(self):
        code, out = self._run(["search", "民事主体", "--kind", "article", "--in", "民法典"])
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["law_filter"]["resolved"][0]["id"], "flk-civil-code-2020")
        self.assertTrue(payload["article_hits"])

    def test_cli_search_in_part_json(self):
        code, out = self._run(
            [
                "search",
                "民事",
                "--kind",
                "article",
                "--in",
                "民法典",
                "--in-part",
                "自然人",
                "--limit",
                "30",
            ]
        )
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["in_part"], "自然人")
        self.assertTrue(payload["article_hits"])
        for hit in payload["article_hits"]:
            self.assertIn("自然人", hit.get("part") or "")

    def test_cli_search_in_part_md_header(self):
        code, out = self._run(
            [
                "search",
                "民事",
                "--kind",
                "article",
                "--in-part",
                "自然人",
                "--format",
                "md",
            ]
        )
        self.assertEqual(code, 0)
        self.assertIn("章节限定：自然人", out)

    def test_cli_search_in_part_suppresses_law_and_norm_hits(self):
        code, out = self._run(
            [
                "search",
                "民事",
                "--kind",
                "all",
                "--in-part",
                "自然人",
            ]
        )
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["in_part"], "自然人")
        self.assertEqual(payload["law_hits"], [])
        self.assertEqual(payload["norm_clause_hits"], [])
        self.assertEqual(payload["norm_source_hits"], [])

    def test_cli_article_not_found_exit_code(self):
        code, _ = self._run(["article", "民法典", "99999", "--format", "md"])
        self.assertEqual(code, 1)

    def test_cli_status(self):
        code, out = self._run(["status"])
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertGreaterEqual(payload["laws"], 3)
        self.assertGreaterEqual(payload["norm_packs"], 0)
        self.assertGreaterEqual(payload["law_relations"], 1)
        self.assertGreaterEqual(payload["applicability_rules"], 2)

    def test_cli_laws_json(self):
        code, out = self._run(["laws", "--limit", "3"])
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertTrue(payload)
        self.assertIn("id", payload[0])
        self.assertIn("title", payload[0])
        self.assertIn("short_title", payload[0])

    def test_cli_sync_applicability_json(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "fresh.db"
            import contextlib
            import io

            from chinalaw.cli import app
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                code = app([
                    "sync",
                    "--applicability",
                    "--applicability-dir",
                    str(APPLICABILITY_FIXTURES),
                    "--db",
                    str(db_path),
                ])
            payload = json.loads(buf.getvalue())

        self.assertEqual(code, 0)
        self.assertEqual(payload["kind"], "applicability_import")
        self.assertGreaterEqual(payload["relations_loaded"], 1)
        self.assertGreaterEqual(payload["rules_loaded"], 2)

    def test_cli_relation_and_applicable_json(self):
        relation_code, relation_out = self._run(["relation", "民法典"])
        self.assertEqual(relation_code, 0)
        relation_payload = json.loads(relation_out)
        self.assertGreaterEqual(relation_payload["relation_count"], 1)

        applicable_code, applicable_out = self._run(
            [
                "applicable",
                "--date",
                "2022-01-01",
                "--topic",
                "合同效力",
                "--domain",
                "litigation",
            ]
        )
        self.assertEqual(applicable_code, 0)
        applicable_payload = json.loads(applicable_out)
        self.assertEqual(applicable_payload["match_count"], 1)
        self.assertEqual(
            applicable_payload["matches"][0]["primary_law_id"],
            "flk-civil-code-2020",
        )

    def test_cli_pack_import_and_show_json(self):
        import tempfile

        pack_payload = {
            "name": "合同效力基础包",
            "items": [
                {
                    "item_type": "article",
                    "law_id": "flk-civil-code-2020",
                    "law_title": "中华人民共和国民法典",
                    "article_number": "143",
                    "role": "core",
                    "reason": "合同效力判断的基础条文",
                }
            ],
        }
        with tempfile.TemporaryDirectory() as td:
            pack_file = Path(td) / "pack.json"
            pack_file.write_text(json.dumps(pack_payload, ensure_ascii=False), encoding="utf-8")
            import_code, import_out = self._run(["pack", "import", str(pack_file)])
            self.assertEqual(import_code, 0)
            imported = json.loads(import_out)
            self.assertEqual(imported["items_loaded"], 1)

        code, out = self._run(["pack", "show", "合同效力基础包"])
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["item_count"], 1)
        self.assertEqual(payload["resolved_item_count"], 1)
        self.assertEqual(payload["items"][0]["article_number"], "143")

    def test_cli_pack_export_json(self):
        with connect(self.db_path) as conn:
            normpacks.import_pack_from_dict(
                conn,
                {
                    "name": "劳动时间包",
                    "items": [
                        {
                            "item_type": "article",
                            "law_title": "中华人民共和国劳动法",
                            "law_id": "flk-labor-law-2018",
                            "article_number": "第三十六条",
                            "role": "core",
                        }
                    ],
                },
            )
        code, out = self._run(["pack", "export", "劳动时间包"])
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["kind"], "norm_pack")
        self.assertEqual(payload["items"][0]["article_number"], "36")

    def test_cli_pack_add_article_json(self):
        code, out = self._run(
            [
                "pack",
                "add",
                "CLI善意取得沉淀",
                "--create",
                "--type",
                "article",
                "--law",
                "民法典",
                "--article",
                "第一百四十三条",
                "--role",
                "core",
                "--reason",
                "示例沉淀条款",
            ]
        )
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertTrue(payload["added"])
        self.assertEqual(payload["item"]["law_id"], "flk-civil-code-2020")
        self.assertEqual(payload["item"]["article_number"], "143")

        duplicate_code, duplicate_out = self._run(
            [
                "pack",
                "add",
                "CLI善意取得沉淀",
                "--type",
                "article",
                "--law",
                "中华人民共和国民法典",
                "--article",
                "143",
            ]
        )
        self.assertEqual(duplicate_code, 0)
        duplicate = json.loads(duplicate_out)
        self.assertFalse(duplicate["added"])
        self.assertTrue(duplicate["duplicate"])

    def test_cli_pack_validate_json(self):
        import tempfile

        pack_payload = {
            "name": "CLI放款校验包",
            "items": [
                {
                    "item_type": "norm_clause",
                    "norm_source_name": "放款要求",
                    "clause_number": "第二条",
                    "role": "core",
                    "reason": "担保审批是放款审查重点。",
                }
            ],
        }
        with connect(self.db_path) as conn:
            normsources.import_source_from_dict(conn, EXTRA_NORM_SOURCE_FIXTURE)
            normpacks.import_pack_from_dict(conn, pack_payload)
        code, out = self._run(["pack", "validate", "CLI放款校验包"])
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["resolved_item_count"], 1)

        with tempfile.TemporaryDirectory() as td:
            pack_file = Path(td) / "pack.json"
            pack_file.write_text(json.dumps(pack_payload, ensure_ascii=False), encoding="utf-8")
            file_code, file_out = self._run(["pack", "validate", str(pack_file), "--file"])
        self.assertEqual(file_code, 0)
        file_payload = json.loads(file_out)
        self.assertTrue(file_payload["ok"])

    def test_cli_pack_show_resolves_private_norm_items(self):
        with connect(self.db_path) as conn:
            normsources.import_source_from_dict(conn, EXTRA_NORM_SOURCE_FIXTURE)
            normpacks.import_pack_from_dict(
                conn,
                {
                    "name": "放款担保审查包",
                    "items": [
                        {
                            "item_type": "norm_clause",
                            "norm_source_name": "放款要求",
                            "clause_number": "第二条",
                            "role": "core",
                        }
                    ],
                },
            )
        code, out = self._run(["pack", "show", "放款担保审查包"])
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["resolved_item_count"], 1)
        self.assertEqual(payload["items"][0]["item_type"], "norm_clause")
        self.assertEqual(payload["items"][0]["resolved"]["kind"], "norm_clause")

    def test_cli_norm_import_show_and_clause_json(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            norm_file = Path(td) / "norm.json"
            norm_file.write_text(
                json.dumps(EXTRA_NORM_SOURCE_FIXTURE, ensure_ascii=False),
                encoding="utf-8",
            )
            import_code, import_out = self._run(["norm", "import", str(norm_file)])
            self.assertEqual(import_code, 0)
            imported = json.loads(import_out)
            self.assertEqual(imported["clauses_loaded"], 3)

        show_code, show_out = self._run(["norm", "show", "放款要求"])
        self.assertEqual(show_code, 0)
        shown = json.loads(show_out)
        self.assertEqual(shown["clause_count"], 3)

        clause_code, clause_out = self._run(["norm", "clause", "放款要求", "第二条"])
        self.assertEqual(clause_code, 0)
        clause = json.loads(clause_out)
        self.assertEqual(clause["clause"]["number"], "2")

    def test_cli_norm_ingest_docx_json(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            docx_file = Path(td) / "policy.docx"
            docx_file.write_bytes(
                make_docx_bytes(
                    [
                        {"text": "第一条 借款主体应提交授权文件。"},
                        {"text": "第二条 涉及担保的，应确认审批程序。"},
                    ]
                )
            )
            code, out = self._run(
                [
                    "norm",
                    "ingest",
                    str(docx_file),
                    "--name",
                    "CLI导入放款制度",
                    "--source-type",
                    "lender_requirement",
                ]
            )
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["clauses_loaded"], 2)
        self.assertEqual(payload["ingest_format"], "docx")

    def test_cli_norm_ingest_dry_run_md_preview(self):
        import tempfile

        text = "\n".join(
            [
                "# 全国法院民商事审判工作会议纪要",
                "",
                "## 第1条【民法总则与民法通则的关系及其适用】",
                "民法通则规定...",
                "",
                "## 第2条【民法总则与合同法的关系及其适用】",
                "根据民法典编撰...",
                "",
                "## 第30条【强制性规定的识别】",
                "合同法第五十二条...",
            ]
        )
        with tempfile.TemporaryDirectory() as td:
            md_file = Path(td) / "jiumin.md"
            md_file.write_text(text, encoding="utf-8")
            code, out = self._run(
                [
                    "norm",
                    "ingest",
                    str(md_file),
                    "--name",
                    "九民纪要预览",
                    "--source-type",
                    "court_meeting_minutes",
                    "--dry-run",
                ]
            )
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["kind"], "norm_ingest_preview")
        self.assertTrue(payload["dry_run"])
        self.assertEqual(payload["clause_count"], 3)
        numbers = [c["number"] for c in payload["clauses"]]
        self.assertEqual(numbers, ["第1条", "第2条", "第30条"])
        self.assertEqual(payload["warnings"], [])

    def test_cli_norm_ingest_dry_run_warns_single_clause_large_text(self):
        import tempfile

        text = "\n".join(
            ["本会议纪要总结了民商事审判中的若干常见问题。"] * 30
        )
        with tempfile.TemporaryDirectory() as td:
            md_file = Path(td) / "long.md"
            md_file.write_text(text, encoding="utf-8")
            code, out = self._run(
                [
                    "norm",
                    "ingest",
                    str(md_file),
                    "--name",
                    "未识别长文",
                    "--dry-run",
                ]
            )
        self.assertEqual(code, 2)
        payload = json.loads(out)
        self.assertEqual(payload["kind"], "norm_ingest_preview")
        self.assertEqual(payload["clause_count"], 1)
        self.assertTrue(payload["warnings"])
        self.assertEqual(payload["warnings"][0]["code"], "single_clause_large_text")

    def test_cli_norm_ingest_real_run_includes_warnings_field(self):
        import tempfile

        text = "\n".join(
            [
                "## 第1条【条名一】",
                "正文一。",
                "",
                "## 第2条【条名二】",
                "正文二。",
            ]
        )
        with tempfile.TemporaryDirectory() as td:
            md_file = Path(td) / "policy.md"
            md_file.write_text(text, encoding="utf-8")
            code, out = self._run(
                [
                    "norm",
                    "ingest",
                    str(md_file),
                    "--name",
                    "正常切分文档",
                    "--source-type",
                    "court_meeting_minutes",
                ]
            )
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["clauses_loaded"], 2)
        self.assertIn("warnings", payload)
        self.assertEqual(payload["warnings"], [])

    def test_cli_norm_ingest_preserves_rich_metadata(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            md_file = Path(td) / "finance.md"
            metadata_file = Path(td) / "meta.json"
            md_file.write_text(
                "23. 【供应链金融平台纠纷案件的审理要点】正文。",
                encoding="utf-8",
            )
            metadata_file.write_text(
                json.dumps(
                    {
                        "verification": {
                            "official_site": "not_found",
                            "cleaning_note": "第三方转载清洗",
                        }
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            code, out = self._run(
                [
                    "norm",
                    "ingest",
                    str(md_file),
                    "--name",
                    "金融审判纪要征求意见稿（测试）",
                    "--id",
                    "cli-finance-draft",
                    "--short-name",
                    "金融审判纪要测试",
                    "--source-type",
                    "unofficial_draft_reprint",
                    "--alias",
                    "金融审判会议纪要测试",
                    "--source-name",
                    "第三方转载",
                    "--source-checked-at",
                    "2026-05-01T00:00:00+08:00",
                    "--source-hash",
                    "sha256-cli-test",
                    "--metadata-file",
                    str(metadata_file),
                    "--metadata-json",
                    '{"verification":{"human_checked":true}}',
                ]
            )
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["clauses_loaded"], 1)

        code, out = self._run(["norm", "export", "金融审判会议纪要测试"])
        self.assertEqual(code, 0)
        exported = json.loads(out)
        self.assertEqual(exported["aliases"], ["金融审判会议纪要测试"])
        self.assertEqual(exported["source_name"], "第三方转载")
        self.assertEqual(exported["source_hash"], "sha256-cli-test")
        self.assertEqual(exported["metadata"]["verification"]["official_site"], "not_found")
        self.assertEqual(exported["source_checked_at"], "2026-05-01T00:00:00+08:00")
        self.assertEqual(exported["metadata"]["verification"]["human_checked"], True)
        self.assertEqual(exported["clauses"][0]["title"], "供应链金融平台纠纷案件的审理要点")

    def test_cli_search_norm_json(self):
        with connect(self.db_path) as conn:
            normsources.import_source_from_dict(conn, EXTRA_NORM_SOURCE_FIXTURE)
        code, out = self._run(["search", "担保 审批", "--kind", "norm"])
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertTrue(payload["norm_clause_hits"])
        self.assertEqual(payload["norm_clause_hits"][0]["norm_source_name"], "甲方放款要求（示例）")
        # PR-C：counts 字段必现
        self.assertIn("counts", payload)
        self.assertEqual(
            payload["counts"]["norm_clause"],
            len(payload["norm_clause_hits"]),
        )

    def test_cli_search_md_summary_line(self):
        with connect(self.db_path) as conn:
            normsources.import_source_from_dict(conn, EXTRA_NORM_SOURCE_FIXTURE)
        code, out = self._run(["search", "担保 审批", "--kind", "norm", "--format", "md"])
        self.assertEqual(code, 0)
        # 顶部应出现一行"命中合计"摘要，便于 agent / 人类一眼看到 norm 命中
        self.assertIn("命中合计", out)
        self.assertIn("私域条款", out)

    @patch("chinalaw.sources.probe_source")
    def test_cli_probe_json(self, probe_source):
        probe_source.return_value = {
            "source": "flk_npc",
            "status_code": 200,
            "title": "国家法律法规数据库",
            "page_shape": "spa",
            "detected_sections": ["法律", "行政法规"],
        }
        code, out = self._run(["probe", "flk_npc"])
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["source"], "flk_npc")
        self.assertEqual(payload["status_code"], 200)

    @patch("chinalaw.sources.verify_source")
    def test_cli_verify_source_json(self, verify_source):
        verify_source.return_value = {
            "kind": "source_verify",
            "source": "flk_npc",
            "ok": True,
            "query": "中华人民共和国民法典",
            "article": "第一条",
            "steps": [{"step": "probe", "ok": True, "message": "ok"}],
        }
        code, out = self._run(["verify-source", "flk_npc"])
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["kind"], "source_verify")
        self.assertTrue(payload["ok"])

    @patch("chinalaw.cli.sync_source")
    def test_cli_sync_source_json(self, sync_source_mock):
        sync_source_mock.return_value = {
            "source": "flk_npc",
            "mode": "query",
            "query": "示例法",
            "laws_loaded": 1,
            "articles_loaded": 1,
            "titles": ["中华人民共和国示例法"],
        }
        code, out = self._run(["sync", "--source", "flk_npc", "--query", "示例法", "--limit", "1"])
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["source"], "flk_npc")
        self.assertEqual(payload["laws_loaded"], 1)

    @patch("chinalaw.cli.sync_source")
    def test_cli_sync_batch_json(self, sync_source_mock):
        sync_source_mock.return_value = {
            "source": "flk_npc",
            "mode": "batch",
            "pages_synced": 2,
            "laws_loaded": 2,
            "articles_loaded": 2,
        }
        code, out = self._run(
            [
                "sync",
                "--source",
                "flk_npc",
                "--batch",
                "--max-pages",
                "2",
                "--page-size",
                "1",
            ]
        )
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["mode"], "batch")
        self.assertEqual(payload["pages_synced"], 2)

    @patch("chinalaw.cli.sync_source")
    def test_cli_sync_batch_resume_json(self, sync_source_mock):
        sync_source_mock.return_value = {
            "source": "flk_npc",
            "mode": "batch",
            "resume": True,
            "pages_synced": 1,
            "next_page": 4,
        }
        code, out = self._run(
            [
                "sync",
                "--source",
                "flk_npc",
                "--batch",
                "--resume",
                "--max-pages",
                "1",
            ]
        )
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertTrue(payload["resume"])
        self.assertEqual(payload["next_page"], 4)

    @patch("chinalaw.cli.sync_source")
    def test_cli_sync_incremental_json(self, sync_source_mock):
        sync_source_mock.return_value = {
            "source": "flk_npc",
            "mode": "incremental",
            "published_from": "2026-04-01",
            "published_to": "2026-04-22",
            "laws_loaded": 1,
        }
        code, out = self._run(
            [
                "sync",
                "--source",
                "flk_npc",
                "--incremental",
                "--published-from",
                "2026-04-01",
                "--published-to",
                "2026-04-22",
                "--max-pages",
                "1",
            ]
        )
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["mode"], "incremental")
        self.assertEqual(payload["published_from"], "2026-04-01")

    @patch("chinalaw.service.history")
    def test_cli_history_json(self, history_mock):
        history_mock.return_value = {
            "law": {"title": "中华人民共和国示例法"},
            "revision_count": 2,
            "current_revision": {"version_label": "2026-01-01 发布版"},
            "revisions": [
                {"version_label": "2026-01-01 发布版", "released_at": "2026-01-01", "effective_at": "2026-02-01"},
                {"version_label": "2025-01-01 发布版", "released_at": "2025-01-01", "effective_at": "2025-02-01"},
            ],
        }
        code, out = self._run(["history", "示例法"])
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["revision_count"], 2)

    @patch("chinalaw.service.diff_law_as_of")
    def test_cli_diff_json(self, diff_mock):
        diff_mock.return_value = {
            "title": "中华人民共和国示例法",
            "from_as_of": "2024-01-01",
            "to_as_of": "2025-01-01",
            "summary": {"added": 1, "removed": 0, "changed": 1},
            "added": [{"number_display": "第三条", "number": "3", "text": "新增条文。"}],
            "removed": [],
            "changed": [
                {
                    "number": "1",
                    "number_display": "第一条",
                    "before": {"text": "旧正文。"},
                    "after": {"text": "新正文。"},
                }
            ],
        }
        code, out = self._run(
            [
                "diff",
                "示例法",
                "--from-as-of",
                "2024-01-01",
                "--to-as-of",
                "2025-01-01",
            ]
        )
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["summary"]["added"], 1)
        self.assertEqual(payload["summary"]["changed"], 1)

    @patch("chinalaw.service.trace_article_as_of")
    def test_cli_trace_json(self, trace_mock):
        trace_mock.return_value = {
            "kind": "law_article_trace",
            "ok": True,
            "input": {
                "name": "民事诉讼法",
                "number": "257",
                "from_as_of": "2021-01-01",
                "to_as_of": "2024-01-01",
            },
            "law": {"title": "中华人民共和国民事诉讼法"},
            "from": {"article": {"number": "257"}},
            "to": {"article": {"number": "268"}},
            "status": "renumbered",
            "confidence": 0.99,
            "diff": {"number_changed": True, "text_changed": False},
            "candidates": [],
        }
        code, out = self._run(
            [
                "trace",
                "民事诉讼法",
                "257",
                "--from-as-of",
                "2021-01-01",
                "--to-as-of",
                "2024-01-01",
                "--items",
                "3,5",
            ]
        )
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["status"], "renumbered")
        trace_mock.assert_called_once()
        _, args, kwargs = trace_mock.mock_calls[0]
        self.assertEqual(args[1], "民事诉讼法")
        self.assertEqual(args[2], "257")
        self.assertEqual(kwargs["items"], "3,5")

    def test_cli_article_accepts_alias_and_inserted_number(self):
        code, out = self._run(["article", "示例条例", "第十四条之一", "--format", "md"])
        self.assertEqual(code, 0)
        self.assertIn("示例法", out)
        self.assertIn("插入条文", out)


class V02DataPackSmokeTests(unittest.TestCase):
    """v0.2 W4 / W7 验收：核心 fixture 与主示范规范包能整体跑通。"""

    REPO_ROOT = Path(__file__).resolve().parent.parent
    FIXTURES_DIR = REPO_ROOT / "data" / "fixtures"
    PACK_FILE = REPO_ROOT / "data" / "packs" / "contract-disputes-judgment.json"

    EXPECTED_LAW_IDS: ClassVar[set[str]] = {
        "flk-civil-code-2020",
        "flk-civil-procedure-law-2023",
        "flk-company-law-2024",
        "court-contract-interpretation-2023",
    }

    def test_v02_fixtures_load_and_pack_imports(self):
        import tempfile

        if not self.PACK_FILE.exists():
            self.skipTest("optional demo norm pack data is not included in this distribution")

        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "t.db"

            sync_result = loader.load_fixtures(db_path, self.FIXTURES_DIR)
            loaded_titles = set(sync_result["titles"])
            self.assertGreaterEqual(
                sync_result["laws_loaded"], len(self.EXPECTED_LAW_IDS)
            )
            self.assertIn("中华人民共和国民法典", loaded_titles)
            self.assertIn("中华人民共和国民事诉讼法", loaded_titles)
            self.assertIn("中华人民共和国公司法", loaded_titles)
            self.assertIn(
                "最高人民法院关于适用《中华人民共和国民法典》合同编通则若干问题的解释",
                loaded_titles,
            )

            status_report = service.status(db_path)
            self.assertEqual(status_report["schema_version"], SCHEMA_VERSION)
            self.assertGreaterEqual(
                status_report["laws"], len(self.EXPECTED_LAW_IDS)
            )
            level_counts = {
                item["level"]: item["count"] for item in status_report["by_level"]
            }
            self.assertGreaterEqual(level_counts.get("law", 0), 3)
            self.assertGreaterEqual(
                level_counts.get("judicial_interpretation", 0), 1
            )

            company_law = service.get_law(db_path, "公司法")
            self.assertIsNotNone(company_law)
            self.assertEqual(company_law["id"], "flk-company-law-2024")
            self.assertEqual(company_law["effective_at"], "2024-07-01")
            self.assertEqual(company_law["source_name"], "flk.npc.gov.cn")
            self.assertEqual(company_law["articles_coverage"], "populated")
            self.assertGreaterEqual(company_law["article_count"], 260)

            civil_code = service.get_law(db_path, "民法典")
            self.assertIsNotNone(civil_code)
            self.assertEqual(civil_code["article_count"], 1260)

            civil_procedure = service.get_law(db_path, "民事诉讼法")
            self.assertIsNotNone(civil_procedure)
            self.assertGreaterEqual(civil_procedure["article_count"], 300)

            contract_interpretation = service.get_law(db_path, "合同编通则解释")
            self.assertIsNotNone(contract_interpretation)
            self.assertEqual(contract_interpretation["id"], "court-contract-interpretation-2023")
            self.assertEqual(contract_interpretation["article_count"], 69)

            search_company = service.search(db_path, "公司法", kind="law")
            self.assertTrue(
                any(
                    hit["id"] == "flk-company-law-2024"
                    for hit in search_company["law_hits"]
                )
            )

            pack_import = normpacks.import_pack_file(db_path, self.PACK_FILE)
            self.assertEqual(pack_import["pack_id"], "contract-disputes-judgment")
            self.assertGreaterEqual(pack_import["items_loaded"], 30)

            shown = normpacks.get_pack(db_path, "合同纠纷裁判依据", resolve=True)
            self.assertIsNotNone(shown)
            self.assertGreaterEqual(shown["item_count"], 30)

            article_items = [
                it for it in shown["items"] if it["item_type"] == "article"
            ]
            reference_items = [
                it for it in shown["items"] if it["item_type"] == "reference"
            ]
            # 当前基础 fixture 已补全民法典全文，主示范包里的民法典条文必须全部是
            # 可解析 article，而不是 pending reference 占位摘要。
            self.assertGreaterEqual(len(article_items), 33)
            for it in article_items:
                self.assertEqual(
                    it.get("resolved", {}).get("kind"),
                    "article",
                    f"article item {it.get('article_number')} 未解析：{it}",
                )
            resolved_numbers = {
                it.get("article_number") for it in article_items
            }
            self.assertIn("143", resolved_numbers)
            self.assertIn("509", resolved_numbers)
            self.assertIn("585", resolved_numbers)
            # 仍允许少量工作流提示型 reference，但不得再有民法典 pending 占位。
            self.assertGreaterEqual(len(reference_items), 3)
            pending_refs = [
                it for it in reference_items
                if it.get("note") and it["note"].startswith("pending:")
            ]
            self.assertEqual(pending_refs, [])

            dependencies = shown.get("dependencies") or {}
            dep_law_ids = {
                dep.get("law_id") for dep in dependencies.get("laws", [])
            }
            self.assertIn("flk-civil-code-2020", dep_law_ids)
            self.assertIn("flk-civil-procedure-law-2023", dep_law_ids)
            self.assertIn("court-contract-interpretation-2023", dep_law_ids)

            # 主示范包 validate 必须干净通过：民法典基础条文已补全，不再需要
            # pending_reference_in_pack 降级信号。
            validation = normpacks.validate_pack(db_path, "合同纠纷裁判依据")
            self.assertIsNotNone(validation)
            self.assertTrue(
                validation["ok"],
                f"主示范包应 validate 通过，但报错：{validation['issues']}",
            )
            self.assertEqual(
                validation["error_count"],
                0,
                f"主示范包不应有 validate error：{validation['issues']}",
            )
            self.assertEqual(validation["warning_count"], 0, validation["issues"])
            warning_codes = {
                issue["code"]
                for issue in validation["issues"]
                if issue["severity"] == "warning"
            }
            # 防回归：article 类型不解析必须是 error，不允许 warning 静默
            self.assertNotIn("pending_reference_in_pack", warning_codes)
            self.assertNotIn("pending_article_in_dataset", warning_codes)
            self.assertNotIn("stub_law_pending_articles", warning_codes)

            # 基础 fixture 不应再有 stub 法规。
            self.assertEqual(status_report.get("stub_laws", []), [])
            coverage_dict = {
                row["coverage"]: row["count"]
                for row in status_report.get("by_articles_coverage", [])
            }
            self.assertEqual(coverage_dict.get("stub", 0), 0)
            self.assertGreaterEqual(coverage_dict.get("populated", 0), len(self.EXPECTED_LAW_IDS))


if __name__ == "__main__":
    unittest.main()
