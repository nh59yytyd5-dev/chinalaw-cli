"""单元测试：chinalaw.fetch (按需爬取 + 清洗 + 入库 high-level API)。

mock 数据源 adapter.search_list / build_law_payload，覆盖：
- list-matches / dry-run / to-fixture / 默认入库 四种动作
- --article 定位（命中 / 未命中）
- 选最佳匹配（单结果 / 完全匹配 / 包含匹配 / 多义）
- prefer-bbbs（命中 / 不在候选）
- court_gongbao / court_main detail_id 风格候选与 --prefer-id 直取
- source 异常 / 入库幂等
不打 flk 真实接口。
"""

from __future__ import annotations

import contextlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from chinalaw import ensure as ensure_mod
from chinalaw import fetch as fetch_mod
from chinalaw import service
from chinalaw.aliases import common_law_aliases


def _make_payload(*, bbbs: str, title: str = "中华人民共和国示例法",
                  source_hash: str = "hash-1",
                  articles: list[dict] | None = None) -> dict:
    return {
        "id": bbbs,
        "title": title,
        "short_title": title.replace("中华人民共和国", "")[:6] or None,
        "aliases": ["示例法"],
        "level": "law",
        "status": "current",
        "issuing_body": "全国人民代表大会",
        "document_number": None,
        "released_at": "2026-01-01",
        "effective_at": "2026-02-01",
        "repealed_at": None,
        "source_url": f"https://flk.npc.gov.cn/detail?id={bbbs}",
        "source_name": "flk.npc.gov.cn",
        "source_checked_at": "2026-04-27T00:00:00+00:00",
        "source_hash": source_hash,
        "articles": articles
        if articles is not None
        else [
            {
                "number": "1",
                "number_display": "第一条",
                "text": "示例正文。",
                "part": "第一章 总则",
            },
            {
                "number": "568",
                "number_display": "第五百六十八条",
                "text": "当事人互负债务……",
                "part": "第三编 合同/第八章 合同的权利义务终止",
            },
        ],
    }


def _make_court_payload(
    *,
    detail_id: str,
    title: str = "最高人民法院 关于审理示例案件适用法律若干问题的解释",
    source_hash: str = "court-hash-1",
) -> dict:
    return {
        "id": f"court_gongbao:{detail_id}",
        "title": title,
        "short_title": "审理示例案件适用法律若干问题的解释",
        "aliases": [],
        "level": "judicial_interpretation",
        "status": "current",
        "issuing_body": "最高人民法院",
        "document_number": "法释〔2026〕5号",
        "released_at": None,
        "effective_at": None,
        "repealed_at": None,
        "source_url": f"http://gongbao.court.gov.cn/Details/{detail_id}.html",
        "source_name": "gongbao.court.gov.cn",
        "source_checked_at": "2026-05-02T00:00:00+00:00",
        "source_hash": source_hash,
        "articles": [
            {
                "number": "1",
                "number_display": "第一条",
                "text": "公报示例正文。",
                "part": None,
            }
        ],
    }


def _make_court_main_payload(
    *,
    detail_id: str,
    title: str = "最高人民法院关于审理示例案件适用法律若干问题的解释（二）",
    source_hash: str = "court-main-hash-1",
) -> dict:
    return {
        "id": f"court_main:{detail_id}",
        "title": title,
        "short_title": "审理示例案件适用法律若干问题的解释（二）",
        "aliases": ["示例案件解释二"],
        "level": "judicial_interpretation",
        "status": "current",
        "issuing_body": "最高人民法院",
        "document_number": "法释〔2026〕5号",
        "released_at": "2026-05-06",
        "effective_at": "2026-06-01",
        "repealed_at": None,
        "source_url": f"https://www.court.gov.cn/{detail_id}.html",
        "source_name": "www.court.gov.cn",
        "source_checked_at": "2026-05-06T00:00:00+00:00",
        "source_hash": source_hash,
        "articles": [
            {
                "number": "1",
                "number_display": "第一条",
                "text": "最高法主站示例正文。",
                "part": None,
            }
        ],
    }


def _make_spp_payload(
    *,
    detail_id: str,
    title: str = "最高人民检察院关于办理示例案件若干问题的意见",
    source_hash: str = "spp-hash-1",
) -> dict:
    return {
        "id": f"spp_gov_cn:{detail_id}",
        "title": title,
        "short_title": "办理示例案件若干问题的意见",
        "aliases": [],
        "level": "judicial_policy",
        "status": "current",
        "issuing_body": "最高人民检察院",
        "document_number": "高检发〔2026〕5号",
        "released_at": None,
        "effective_at": None,
        "repealed_at": None,
        "source_url": f"https://www.spp.gov.cn/{detail_id}.shtml",
        "source_name": "spp.gov.cn",
        "source_checked_at": "2026-05-02T00:00:00+00:00",
        "source_hash": source_hash,
        "articles": [
            {
                "number": "1",
                "number_display": "第一条",
                "text": "最高检示例正文。",
                "part": None,
            }
        ],
    }


class _FakeAdapter:
    def __init__(self, rows: list[dict], payloads: dict[str, dict] | None = None,
                 search_error: Exception | None = None):
        self._rows = rows
        self._payloads = payloads or {}
        self._search_error = search_error
        self.search_calls: list[tuple[str, int]] = []
        self.payload_calls: list[tuple[str, dict | None]] = []

    def search_list(self, query, page_size=20):
        if self._search_error:
            raise self._search_error
        self.search_calls.append((query, page_size))
        return {"code": 200, "rows": self._rows}

    def build_law_payload(self, bbbs, search_row=None):
        self.payload_calls.append((bbbs, search_row))
        if bbbs not in self._payloads:
            return _make_payload(bbbs=bbbs)
        return self._payloads[bbbs]


class _QueryFallbackAdapter(_FakeAdapter):
    def __init__(self, rows_by_query: dict[str, list[dict]], payloads: dict[str, dict]):
        super().__init__(rows=[], payloads=payloads)
        self._rows_by_query = rows_by_query

    def search_list(self, query, page_size=20):
        self.search_calls.append((query, page_size))
        return {"code": 200, "rows": self._rows_by_query.get(query, [])}


class _CrossSearchAdapter(_FakeAdapter):
    def __init__(self, cross_rows: list[dict], payloads: dict[str, dict]):
        super().__init__(rows=[], payloads=payloads)
        self._cross_rows = cross_rows
        self.cross_calls: list[tuple[str, dict]] = []

    def cross_search(self, query, **kwargs):
        self.cross_calls.append((query, kwargs))
        return {"rows": self._cross_rows}


class FetchLawTests(unittest.TestCase):
    def _patch_adapter(self, adapter: _FakeAdapter):
        return patch("chinalaw.fetch.get_source_adapter", return_value=adapter)

    # ---- 默认入库 + 单结果 ----------------------------------------------------

    def test_loads_when_single_match(self):
        adapter = _FakeAdapter(rows=[{"bbbs": "law-1", "title": "中华人民共和国示例法"}])
        with tempfile.TemporaryDirectory() as td, self._patch_adapter(adapter):
            db_path = Path(td) / "t.db"
            result = fetch_mod.fetch_law(db_path, "示例法")
            self.assertEqual(result["kind"], "law_fetch")
            self.assertEqual(result["matched_bbbs"], "law-1")
            self.assertTrue(result["loaded"])
            self.assertFalse(result["skipped"])
            self.assertFalse(result["dry_run"])
            self.assertIsNone(result["wrote_fixture"])
            self.assertEqual(result["article_count"], 2)
            # 真的入库了
            law = service.get_law(db_path, "示例法")
            self.assertIsNotNone(law)

    def test_agent_aliases_are_added_to_response_and_db_when_enabled(self):
        adapter = _FakeAdapter(
            rows=[{"bbbs": "law-1", "title": "中华人民共和国示例法"}],
            payloads={"law-1": {**_make_payload(bbbs="law-1"), "aliases": []}},
        )
        with tempfile.TemporaryDirectory() as td, self._patch_adapter(adapter), patch(
            "chinalaw.fetch.derive_aliases",
            return_value=["示例社区简称"],
        ), patch.dict(
            "os.environ", {"CHINALAW_USE_ALIAS_AGENT": "1"}
        ):
            db_path = Path(td) / "t.db"
            result = fetch_mod.fetch_law(db_path, "示例法")

            self.assertIn("示例社区简称", result["law"]["aliases"])
            law = service.get_law(db_path, "示例社区简称")
            self.assertIsNotNone(law)
            self.assertEqual(law["id"], result["law"]["id"])

    def test_agent_alias_recoverable_error_writes_warning(self):
        """opt-in 时 alias_agent 抛 recoverable 错误 → 写 warnings，不挂主流程。"""

        from chinalaw.alias_agent import AliasAgentRecoverableError

        adapter = _FakeAdapter(rows=[{"bbbs": "law-1", "title": "中华人民共和国示例法"}])
        with tempfile.TemporaryDirectory() as td, self._patch_adapter(adapter), patch(
            "chinalaw.fetch.derive_aliases",
            side_effect=AliasAgentRecoverableError("network", "timeout after 10s"),
        ), patch.dict(
            "os.environ", {"CHINALAW_USE_ALIAS_AGENT": "1"}
        ):
            result = fetch_mod.fetch_law(Path(td) / "t.db", "示例法")

        self.assertTrue(result["loaded"])
        self.assertIn("示例法", result["law"]["aliases"])
        warnings = result["law"].get("warnings") or []
        self.assertTrue(
            any(w.get("code") == "alias_agent_skipped" for w in warnings),
            warnings,
        )

    def test_agent_alias_skipped_by_default(self):
        """默认无 ``CHINALAW_USE_ALIAS_AGENT`` → fetch 不调 derive_aliases。"""

        called: list[str] = []

        def fake_derive(title, **_kwargs):
            called.append(title)
            return ["不该出现"]

        adapter = _FakeAdapter(
            rows=[{"bbbs": "law-1", "title": "中华人民共和国示例法"}],
            payloads={"law-1": {**_make_payload(bbbs="law-1"), "aliases": []}},
        )
        with tempfile.TemporaryDirectory() as td, self._patch_adapter(adapter), patch(
            "chinalaw.fetch.derive_aliases",
            side_effect=fake_derive,
        ), patch.dict("os.environ", {}, clear=False):
            import os as _os

            _os.environ.pop("CHINALAW_USE_ALIAS_AGENT", None)
            result = fetch_mod.fetch_law(Path(td) / "t.db", "示例法")

        self.assertEqual(called, [])
        self.assertNotIn("不该出现", result["law"]["aliases"])

    def test_agent_alias_unknown_exception_propagates(self):
        """opt-in 时 alias_agent 抛未知异常 → 不被吞，让 fetch 主流程上抛。"""

        adapter = _FakeAdapter(rows=[{"bbbs": "law-1", "title": "中华人民共和国示例法"}])
        with (
            tempfile.TemporaryDirectory() as td,
            self._patch_adapter(adapter),
            patch(
                "chinalaw.fetch.derive_aliases",
                side_effect=ValueError("alias_agent 内部 bug"),
            ),
            patch.dict("os.environ", {"CHINALAW_USE_ALIAS_AGENT": "1"}),
            self.assertRaises(ValueError),
        ):
            fetch_mod.fetch_law(Path(td) / "t.db", "示例法")

    # ---- dry-run / to-fixture / list-matches 三种非入库 -----------------------

    def test_dry_run_does_not_persist(self):
        adapter = _FakeAdapter(rows=[{"bbbs": "law-1", "title": "中华人民共和国示例法"}])
        with tempfile.TemporaryDirectory() as td, self._patch_adapter(adapter):
            db_path = Path(td) / "t.db"
            result = fetch_mod.fetch_law(db_path, "示例法", dry_run=True)
            self.assertTrue(result["dry_run"])
            self.assertFalse(result["loaded"])
            self.assertFalse(result["skipped"])
            # 没入库
            self.assertIsNone(service.get_law(db_path, "示例法"))

    def test_to_fixture_writes_file_and_skips_persist(self):
        adapter = _FakeAdapter(rows=[{"bbbs": "law-1", "title": "中华人民共和国示例法"}])
        with tempfile.TemporaryDirectory() as td, self._patch_adapter(adapter):
            db_path = Path(td) / "t.db"
            fixture_path = Path(td) / "out" / "civil_code.json"
            result = fetch_mod.fetch_law(db_path, "示例法", to_fixture=str(fixture_path))
            self.assertEqual(result["wrote_fixture"], str(fixture_path))
            self.assertFalse(result["loaded"])
            self.assertFalse(result["skipped"])
            self.assertTrue(fixture_path.exists())
            written = json.loads(fixture_path.read_text(encoding="utf-8"))
            self.assertEqual(written["id"], "law-1")
            self.assertEqual(len(written["articles"]), 2)
            # 没入库
            self.assertIsNone(service.get_law(db_path, "示例法"))

    def test_to_fixture_omits_alias_agent_runtime_warnings(self):
        """response.law 暴露运行时告警，但 fixture 文件必须保持 canonical。"""

        from chinalaw.alias_agent import AliasAgentRecoverableError

        adapter = _FakeAdapter(rows=[{"bbbs": "law-1", "title": "中华人民共和国示例法"}])
        with tempfile.TemporaryDirectory() as td, self._patch_adapter(adapter), patch(
            "chinalaw.fetch.derive_aliases",
            side_effect=AliasAgentRecoverableError("network", "timeout after 10s"),
        ), patch.dict(
            "os.environ", {"CHINALAW_USE_ALIAS_AGENT": "1"}
        ):
            fixture_path = Path(td) / "out" / "law.json"
            result = fetch_mod.fetch_law(
                Path(td) / "t.db",
                "示例法",
                to_fixture=str(fixture_path),
            )
            written = json.loads(fixture_path.read_text(encoding="utf-8"))

        self.assertTrue(result["law"].get("warnings"))
        self.assertNotIn("warnings", written)

    def test_atomic_fixture_replace_failure_preserves_existing_file(self):
        adapter = _FakeAdapter(rows=[{"bbbs": "law-1", "title": "中华人民共和国示例法"}])
        with tempfile.TemporaryDirectory() as td, self._patch_adapter(adapter):
            fixture_path = Path(td) / "law.json"
            original = b'{"sentinel":"keep"}\n'
            fixture_path.write_bytes(original)
            with (
                patch("chinalaw.fetch.os.replace", side_effect=OSError("replace failed")),
                self.assertRaises(fetch_mod.FetchError),
            ):
                fetch_mod.fetch_law(
                    Path(td) / "t.db",
                    "示例法",
                    to_fixture=fixture_path,
                )

            self.assertEqual(fixture_path.read_bytes(), original)
            self.assertEqual(list(fixture_path.parent.glob(f".{fixture_path.name}.*.tmp")), [])

    def test_list_matches_returns_candidates_only(self):
        adapter = _FakeAdapter(rows=[
            {"bbbs": "law-1", "title": "中华人民共和国公司法", "gbrq": "2023-12-29", "sxx": 3},
            {"bbbs": "law-2", "title": "中华人民共和国公司法（旧）", "gbrq": "2018-10-26", "sxx": 1},
        ])
        with tempfile.TemporaryDirectory() as td, self._patch_adapter(adapter):
            db_path = Path(td) / "t.db"
            result = fetch_mod.fetch_law(db_path, "公司法", list_matches=True)
            self.assertEqual(result["kind"], "law_fetch_candidates")
            self.assertEqual(len(result["candidates"]), 2)
            self.assertEqual(result["candidates"][0]["status"], "current")
            self.assertEqual(result["candidates"][1]["status"], "repealed")

    def test_court_gongbao_list_matches_uses_detail_id_as_primary_id(self):
        detail_id = "a" * 30
        adapter = _FakeAdapter(rows=[
            {
                "detail_id": detail_id,
                "serial_no": "sfjs",
                "title": "最高人民法院 关于审理示例案件适用法律若干问题的解释",
                "issue": "2026年03期",
                "status": "current",
            }
        ])
        with tempfile.TemporaryDirectory() as td, self._patch_adapter(adapter):
            result = fetch_mod.fetch_law(
                Path(td) / "t.db",
                "示例案件",
                source="court_gongbao",
                list_matches=True,
            )

        self.assertEqual(result["source"], "court_gongbao")
        self.assertEqual(result["candidates"][0]["id"], detail_id)
        self.assertEqual(result["candidates"][0]["detail_id"], detail_id)
        self.assertEqual(result["candidates"][0]["bbbs"], detail_id)
        self.assertEqual(result["candidates"][0]["released_at"], "2026年03期")
        self.assertEqual(result["candidates"][0]["status"], "current")

    def test_court_gongbao_fetch_persists_by_detail_id(self):
        detail_id = "b" * 30
        row = {
            "detail_id": detail_id,
            "serial_no": "sfjs",
            "title": "最高人民法院 关于审理示例案件适用法律若干问题的解释",
            "issue": "2026年03期",
            "status": "current",
        }
        adapter = _FakeAdapter(
            rows=[row],
            payloads={detail_id: _make_court_payload(detail_id=detail_id)},
        )
        with tempfile.TemporaryDirectory() as td, self._patch_adapter(adapter):
            db_path = Path(td) / "t.db"
            result = fetch_mod.fetch_law(
                db_path,
                "示例案件",
                source="court_gongbao",
                article="第一条",
            )
            article = service.get_article(db_path, "示例案件", "第一条")

        self.assertEqual(result["matched_id"], detail_id)
        self.assertEqual(result["matched_detail_id"], detail_id)
        self.assertEqual(result["matched_bbbs"], detail_id)
        self.assertTrue(result["loaded"])
        self.assertEqual(result["article"]["text"], "公报示例正文。")
        self.assertIsNotNone(article)
        self.assertEqual(article["law"]["source_name"], "gongbao.court.gov.cn")
        self.assertEqual(adapter.payload_calls[0], (detail_id, row))

    def test_court_gongbao_prefer_id_picks_detail_id(self):
        detail_a = "a" * 30
        detail_b = "b" * 30
        adapter = _FakeAdapter(
            rows=[
                {"detail_id": detail_a, "title": "示例案件解释（一）", "status": "current"},
                {"detail_id": detail_b, "title": "示例案件解释（二）", "status": "current"},
            ],
            payloads={detail_b: _make_court_payload(detail_id=detail_b, title="示例案件解释（二）")},
        )
        with tempfile.TemporaryDirectory() as td, self._patch_adapter(adapter):
            result = fetch_mod.fetch_law(
                Path(td) / "t.db",
                "示例案件解释",
                source="court_gongbao",
                prefer_bbbs=detail_b,
            )

        self.assertEqual(result["matched_id"], detail_b)
        self.assertEqual(result["matched_title"], "示例案件解释（二）")

    def test_court_gongbao_prefer_id_can_fetch_detail_without_search(self):
        detail_id = "c" * 30
        adapter = _FakeAdapter(
            rows=[],
            payloads={detail_id: _make_court_payload(detail_id=detail_id)},
            search_error=AssertionError("search should not run when direct detail_id is supplied"),
        )
        with tempfile.TemporaryDirectory() as td, self._patch_adapter(adapter):
            result = fetch_mod.fetch_law(
                Path(td) / "t.db",
                "示例案件",
                source="court_gongbao",
                prefer_bbbs=detail_id,
            )

        self.assertEqual(adapter.search_calls, [])
        self.assertEqual(result["matched_id"], detail_id)
        self.assertEqual(result["law"]["id"], f"court_gongbao:{detail_id}")

    def test_court_main_prefer_id_can_fetch_detail_without_search(self):
        detail_id = "zixun/xiangqing/499051"
        adapter = _FakeAdapter(
            rows=[],
            payloads={detail_id: _make_court_main_payload(detail_id=detail_id)},
            search_error=AssertionError(
                "search should not run when direct detail_id is supplied"
            ),
        )
        with tempfile.TemporaryDirectory() as td, self._patch_adapter(adapter):
            result = fetch_mod.fetch_law(
                Path(td) / "t.db",
                "示例案件解释二",
                source="court_main",
                prefer_bbbs=detail_id,
            )

        self.assertEqual(adapter.search_calls, [])
        self.assertEqual(result["source"], "court_main")
        self.assertEqual(result["matched_id"], detail_id)
        self.assertEqual(result["matched_detail_id"], detail_id)
        self.assertEqual(result["law"]["id"], f"court_main:{detail_id}")

    def test_court_main_retries_normative_query_variant(self):
        detail_id = "shenpan/xiangqing/6622"
        adapter = _QueryFallbackAdapter(
            rows_by_query={
                "最高人民法院关于常见犯罪的量刑指导意见": [
                    {
                        "detail_id": detail_id,
                        "title": "最高人民法院关于常见犯罪的量刑指导意见",
                        "released_at": "2014-07-31",
                        "status": "current",
                    }
                ]
            },
            payloads={
                detail_id: _make_court_main_payload(
                    detail_id=detail_id,
                    title="最高人民法院关于常见犯罪的量刑指导意见",
                )
            },
        )
        with tempfile.TemporaryDirectory() as td, self._patch_adapter(adapter):
            result = fetch_mod.fetch_law(
                Path(td) / "t.db",
                "最高人民法院关于常见犯罪的量刑指导意见（试行）",
                source="court_main",
                dry_run=True,
            )

        searched_queries = [query for query, _ in adapter.search_calls]
        self.assertIn("最高人民法院关于常见犯罪的量刑指导意见", searched_queries)
        self.assertEqual(result["matched_id"], detail_id)

    def test_court_main_query_variants_do_not_drop_joint_issuer(self):
        detail_id = "shenpan/xiangqing/6622"
        adapter = _QueryFallbackAdapter(
            rows_by_query={
                "关于常见犯罪的量刑指导意见": [
                    {
                        "detail_id": detail_id,
                        "title": "最高人民法院关于常见犯罪的量刑指导意见",
                        "released_at": "2014-07-31",
                        "status": "current",
                    }
                ]
            },
            payloads={detail_id: _make_court_main_payload(detail_id=detail_id)},
        )
        with tempfile.TemporaryDirectory() as td, self._patch_adapter(
            adapter
        ), self.assertRaises(fetch_mod.FetchNotFoundError):
            fetch_mod.fetch_law(
                Path(td) / "t.db",
                "最高人民法院 最高人民检察院关于常见犯罪的量刑指导意见（试行）",
                source="court_main",
                dry_run=True,
            )

        searched_queries = [query for query, _ in adapter.search_calls]
        self.assertNotIn("关于常见犯罪的量刑指导意见", searched_queries)

    def test_court_gongbao_empty_first_page_uses_bounded_deep_search(self):
        detail_id = "d" * 30
        adapter = _CrossSearchAdapter(
            cross_rows=[
                {
                    "detail_id": detail_id,
                    "serial_no": "sfjs",
                    "title": (
                        "最高人民法院最高人民检察院关于办理盗窃刑事案件"
                        "适用法律若干问题的解释"
                    ),
                    "status": "current",
                }
            ],
            payloads={detail_id: _make_court_payload(detail_id=detail_id)},
        )
        with tempfile.TemporaryDirectory() as td, self._patch_adapter(adapter):
            result = fetch_mod.fetch_law(
                Path(td) / "t.db",
                "最高人民法院 最高人民检察院关于办理盗窃刑事案件适用法律若干问题的解释",
                source="court_gongbao",
                dry_run=True,
            )

        self.assertEqual(len(adapter.cross_calls), 1)
        self.assertEqual(adapter.cross_calls[0][1]["max_pages_per_serial"], 31)
        self.assertEqual(result["matched_id"], detail_id)

    def test_spp_empty_default_channel_uses_bounded_cross_search(self):
        detail_id = "spp/xwfbh/wsfbh/201912/t20191230_451490"
        adapter = _CrossSearchAdapter(
            cross_rows=[
                {
                    "detail_id": detail_id,
                    "channel": "gfwj",
                    "title": "人民检察院刑事诉讼规则",
                    "released_at": "2019-12-30",
                    "status": "current",
                }
            ],
            payloads={
                detail_id: _make_spp_payload(
                    detail_id=detail_id,
                    title="人民检察院刑事诉讼规则",
                )
            },
        )
        with tempfile.TemporaryDirectory() as td, self._patch_adapter(adapter):
            result = fetch_mod.fetch_law(
                Path(td) / "t.db",
                "人民检察院刑事诉讼规则",
                source="spp_gov_cn",
                dry_run=True,
            )

        self.assertEqual(len(adapter.cross_calls), 1)
        self.assertEqual(adapter.cross_calls[0][1]["max_pages_per_channel"], 5)
        self.assertEqual(result["matched_id"], detail_id)

    def test_status_mapping_matches_adapter_sxx_table(self):
        # 真实 FLK 的 sxx 取值：1=repealed / 2=amended / 3=current / 4=pending_effective。
        # fetch 候选 status 必须复用 adapter SXX_TO_STATUS，否则 list-matches 会把
        # 现行法误判成 unknown / repealed。
        adapter = _FakeAdapter(rows=[
            {"bbbs": "b1", "title": "现行法", "sxx": 3},
            {"bbbs": "b2", "title": "已修改法", "sxx": 2},
            {"bbbs": "b3", "title": "未生效法", "sxx": 4},
            {"bbbs": "b4", "title": "已废止法", "sxx": 1},
            {"bbbs": "b5", "title": "未知状态法", "sxx": 99},
        ])
        with tempfile.TemporaryDirectory() as td, self._patch_adapter(adapter):
            db_path = Path(td) / "t.db"
            result = fetch_mod.fetch_law(db_path, "法", list_matches=True, limit=10)
        statuses = [c["status"] for c in result["candidates"]]
        self.assertEqual(
            statuses,
            ["current", "amended", "pending_effective", "repealed", "unknown"],
        )

    # ---- --article 定位 -----------------------------------------------------

    def test_locates_article_by_chinese_number(self):
        adapter = _FakeAdapter(rows=[{"bbbs": "law-1", "title": "中华人民共和国示例法"}])
        with tempfile.TemporaryDirectory() as td, self._patch_adapter(adapter):
            db_path = Path(td) / "t.db"
            result = fetch_mod.fetch_law(
                db_path, "示例法", article="第五百六十八条"
            )
            self.assertIsNotNone(result["article"])
            self.assertEqual(result["article"]["number"], "568")
            self.assertEqual(result["article"]["text"], "当事人互负债务……")

    def test_locates_article_by_arabic_number(self):
        adapter = _FakeAdapter(rows=[{"bbbs": "law-1", "title": "中华人民共和国示例法"}])
        with tempfile.TemporaryDirectory() as td, self._patch_adapter(adapter):
            db_path = Path(td) / "t.db"
            result = fetch_mod.fetch_law(db_path, "示例法", article="568")
            self.assertEqual(result["article"]["number"], "568")

    def test_article_not_in_law_raises_not_found(self):
        adapter = _FakeAdapter(rows=[{"bbbs": "law-1", "title": "中华人民共和国示例法"}])
        with tempfile.TemporaryDirectory() as td, self._patch_adapter(adapter):
            db_path = Path(td) / "t.db"
            with self.assertRaises(fetch_mod.FetchNotFoundError):
                fetch_mod.fetch_law(db_path, "示例法", article="第一万条")

            self.assertFalse(db_path.exists())

    def test_missing_article_preserves_existing_fixture_bytes(self):
        adapter = _FakeAdapter(rows=[{"bbbs": "law-1", "title": "中华人民共和国示例法"}])
        with tempfile.TemporaryDirectory() as td, self._patch_adapter(adapter):
            fixture_path = Path(td) / "law.json"
            original = b'{"sentinel":"keep"}\n'
            fixture_path.write_bytes(original)
            with self.assertRaises(fetch_mod.FetchNotFoundError):
                fetch_mod.fetch_law(
                    Path(td) / "missing.db",
                    "示例法",
                    article="9999",
                    to_fixture=fixture_path,
                )

            self.assertEqual(fixture_path.read_bytes(), original)
            self.assertFalse((Path(td) / "missing.db").exists())

    # ---- 错误路径 -----------------------------------------------------------

    def test_no_results_raises_not_found(self):
        adapter = _FakeAdapter(rows=[])
        with tempfile.TemporaryDirectory() as td, self._patch_adapter(adapter):
            db_path = Path(td) / "t.db"
            with self.assertRaises(fetch_mod.FetchNotFoundError):
                fetch_mod.fetch_law(db_path, "不存在的法律")

    def test_search_error_wrapped_as_source_error(self):
        adapter = _FakeAdapter(rows=[], search_error=ConnectionError("flk timeout"))
        with tempfile.TemporaryDirectory() as td, self._patch_adapter(adapter):
            db_path = Path(td) / "t.db"
            with self.assertRaises(fetch_mod.FetchSourceError):
                fetch_mod.fetch_law(db_path, "示例法")

    def test_ambiguous_when_multiple_no_best(self):
        adapter = _FakeAdapter(rows=[
            {"bbbs": "law-1", "title": "公司法实施条例"},
            {"bbbs": "law-2", "title": "公司法司法解释"},
        ])
        with tempfile.TemporaryDirectory() as td, self._patch_adapter(adapter):
            db_path = Path(td) / "t.db"
            with self.assertRaises(fetch_mod.FetchAmbiguousError) as ctx:
                fetch_mod.fetch_law(db_path, "公司")  # 两个都包含"公司"
        self.assertEqual([c["bbbs"] for c in ctx.exception.candidates], ["law-1", "law-2"])

    def test_unique_unrelated_candidate_is_rejected(self):
        adapter = _FakeAdapter(rows=[{"bbbs": "law-1", "title": "中华人民共和国证券法"}])
        with (
            tempfile.TemporaryDirectory() as td,
            self._patch_adapter(adapter),
            self.assertRaises(fetch_mod.FetchAmbiguousError) as ctx,
        ):
            fetch_mod.fetch_law(Path(td) / "t.db", "目标法律")

        self.assertEqual(ctx.exception.candidates[0]["id"], "law-1")
        self.assertEqual(adapter.payload_calls, [])

    # ---- 选最佳匹配 ----------------------------------------------------------

    def test_prefer_bbbs_picks_specified(self):
        adapter = _FakeAdapter(
            rows=[
                {"bbbs": "law-1", "title": "中华人民共和国公司法"},
                {"bbbs": "law-2", "title": "公司法（旧）"},
            ],
            payloads={
                "law-2": _make_payload(bbbs="law-2", title="公司法（旧）", source_hash="hash-2"),
            },
        )
        with tempfile.TemporaryDirectory() as td, self._patch_adapter(adapter):
            db_path = Path(td) / "t.db"
            result = fetch_mod.fetch_law(db_path, "公司法", prefer_bbbs="law-2")
            self.assertEqual(result["matched_bbbs"], "law-2")

    def test_prefer_bbbs_unknown_is_ambiguous(self):
        adapter = _FakeAdapter(rows=[
            {"bbbs": "law-1", "title": "公司法"},
            {"bbbs": "law-2", "title": "公司法（旧）"},
        ])
        with tempfile.TemporaryDirectory() as td, self._patch_adapter(adapter):
            db_path = Path(td) / "t.db"
            with self.assertRaises(fetch_mod.FetchAmbiguousError):
                fetch_mod.fetch_law(db_path, "公司法", prefer_bbbs="not-in-candidates")

    def test_exact_title_match_wins_over_contains(self):
        adapter = _FakeAdapter(rows=[
            {"bbbs": "law-1", "title": "民法典实施细则"},
            {"bbbs": "law-2", "title": "民法典"},  # 完全匹配
        ])
        with tempfile.TemporaryDirectory() as td, self._patch_adapter(adapter):
            db_path = Path(td) / "t.db"
            result = fetch_mod.fetch_law(db_path, "民法典")
            self.assertEqual(result["matched_bbbs"], "law-2")

    # ---- HTML 高亮清洗 -------------------------------------------------------

    def test_choose_best_strips_html_highlight(self):
        # FLK 真实搜索行：标题里嵌 <em class='highlight'>关键字</em>。
        # 不清洗的话 _choose_best 找不到 contains 候选，会抛 ambiguous。
        adapter = _FakeAdapter(rows=[
            {
                "bbbs": "law-1",
                "title": "中华人民共和国<em class='highlight'>民法典</em>",
                "sxx": 3,
                "gbrq": "2020-05-28",
            },
        ])
        with tempfile.TemporaryDirectory() as td, self._patch_adapter(adapter):
            db_path = Path(td) / "t.db"
            result = fetch_mod.fetch_law(db_path, "民法典")
        self.assertEqual(result["matched_bbbs"], "law-1")
        self.assertEqual(result["matched_title"], "中华人民共和国民法典")
        # candidates 输出给 agent，也必须是清洗后的纯文本
        self.assertEqual(result["candidates"][0]["title"], "中华人民共和国民法典")

    def test_list_matches_output_is_html_free(self):
        adapter = _FakeAdapter(rows=[
            {"bbbs": "b1", "title": "中华人民共和国<em class='highlight'>公司法</em>",
             "sxx": 3, "gbrq": "2023-12-29"},
            {"bbbs": "b2", "title": "中华人民共和国<em class='highlight'>公司法</em>",
             "sxx": 2, "gbrq": "2018-10-26"},
            {"bbbs": "b3", "title": "中华人民共和国<em class='highlight'>公司法</em>",
             "sxx": 2, "gbrq": "2013-12-28"},
        ])
        with tempfile.TemporaryDirectory() as td, self._patch_adapter(adapter):
            db_path = Path(td) / "t.db"
            result = fetch_mod.fetch_law(db_path, "公司法", list_matches=True, limit=5)
        for cand in result["candidates"]:
            self.assertNotIn("<", cand["title"])
            self.assertNotIn(">", cand["title"])
            self.assertEqual(cand["title"], "中华人民共和国公司法")
        # 多个 sxx=2（amended）+ 一个 sxx=3（current），保证 status 也被正确识别
        self.assertEqual(result["candidates"][0]["status"], "current")
        self.assertEqual(result["candidates"][1]["status"], "amended")

    def test_highlighted_title_chooses_exact_match_when_present(self):
        # 多条候选 + 高亮：清洗后存在完全匹配 "中华人民共和国民法典"
        # 不应回退到第一个 contains 候选。
        adapter = _FakeAdapter(rows=[
            {"bbbs": "law-1", "title": "中华人民共和国<em class='highlight'>民法典</em>实施细则",
             "sxx": 3, "gbrq": "2021-01-01"},
            {"bbbs": "law-2", "title": "中华人民共和国<em class='highlight'>民法典</em>",
             "sxx": 3, "gbrq": "2020-05-28"},
        ])
        with tempfile.TemporaryDirectory() as td, self._patch_adapter(adapter):
            db_path = Path(td) / "t.db"
            result = fetch_mod.fetch_law(db_path, "中华人民共和国民法典")
        self.assertEqual(result["matched_bbbs"], "law-2")

    # ---- short_title 推断 + current 优先级 -----------------------------------

    def test_short_title_input_prefers_short_title_match_over_contains(self):
        # 真实场景再现：fetch 民法典 时，FLK 同时返回《中华人民共和国民法典》
        # 和《最高人民法院关于适用《中华人民共和国民法典》... 解释》。
        # 仅靠 contains 会判 ambiguous；short_title 推断层应只命中前者。
        adapter = _FakeAdapter(rows=[
            {"bbbs": "law-1", "title": "中华人民共和国民法典", "sxx": 3,
             "gbrq": "2020-05-28"},
            {"bbbs": "law-2",
             "title": "最高人民法院关于适用《中华人民共和国民法典》合同编通则若干问题的解释",
             "sxx": 3, "gbrq": "2023-12-04"},
            {"bbbs": "law-3",
             "title": "最高人民法院关于适用《中华人民共和国民法典》总则编若干问题的解释",
             "sxx": 3, "gbrq": "2022-02-25"},
        ])
        with tempfile.TemporaryDirectory() as td, self._patch_adapter(adapter):
            result = fetch_mod.fetch_law(Path(td) / "t.db", "民法典")
        self.assertEqual(result["matched_bbbs"], "law-1")
        self.assertEqual(result["matched_title"], "中华人民共和国民法典")

    def test_current_status_wins_among_same_short_title_candidates(self):
        # fetch 公司法 时，FLK 同时返回 2023 现行 + 2018/2013 已修改三个版本。
        # 三个 title 完全相同，short_title 层有 3 个候选；
        # current 优先 + 最新 released_at 选 2023。
        adapter = _FakeAdapter(rows=[
            {"bbbs": "b-2023", "title": "中华人民共和国公司法",
             "sxx": 3, "gbrq": "2023-12-29"},
            {"bbbs": "b-2018", "title": "中华人民共和国公司法",
             "sxx": 2, "gbrq": "2018-10-26"},
            {"bbbs": "b-2013", "title": "中华人民共和国公司法",
             "sxx": 2, "gbrq": "2013-12-28"},
        ])
        with tempfile.TemporaryDirectory() as td, self._patch_adapter(adapter):
            result = fetch_mod.fetch_law(Path(td) / "t.db", "公司法")
        self.assertEqual(result["matched_bbbs"], "b-2023")

    def test_no_current_in_layer_is_ambiguous(self):
        # 同层全是非 current（amended/repealed），不武断推一个，让 agent
        # 用 --prefer-bbbs 显式选；保留 v0.2.x 的"宁可拒绝也别错引"原则。
        adapter = _FakeAdapter(rows=[
            {"bbbs": "b1", "title": "中华人民共和国某法",
             "sxx": 2, "gbrq": "2020-01-01"},
            {"bbbs": "b2", "title": "中华人民共和国某法",
             "sxx": 1, "gbrq": "2010-01-01"},
        ])
        with (
            tempfile.TemporaryDirectory() as td,
            self._patch_adapter(adapter),
            self.assertRaises(fetch_mod.FetchAmbiguousError),
        ):
            fetch_mod.fetch_law(Path(td) / "t.db", "某法")

    def test_multiple_currents_pick_newest_released(self):
        # 极端情况：FLK 把多个版本都标成 current（数据问题或修订法）。
        # 选 released_at 最新的一个，不抛 ambiguous。
        adapter = _FakeAdapter(rows=[
            {"bbbs": "b-old", "title": "中华人民共和国某法",
             "sxx": 3, "gbrq": "2010-01-01"},
            {"bbbs": "b-new", "title": "中华人民共和国某法",
             "sxx": 3, "gbrq": "2024-12-31"},
        ])
        with tempfile.TemporaryDirectory() as td, self._patch_adapter(adapter):
            result = fetch_mod.fetch_law(Path(td) / "t.db", "某法")
        self.assertEqual(result["matched_bbbs"], "b-new")

    # ---- 入库幂等 -----------------------------------------------------------

    def test_skips_when_same_source_hash(self):
        adapter = _FakeAdapter(rows=[{"bbbs": "law-1", "title": "中华人民共和国示例法"}])
        with tempfile.TemporaryDirectory() as td, self._patch_adapter(adapter):
            db_path = Path(td) / "t.db"
            first = fetch_mod.fetch_law(db_path, "示例法")
            self.assertTrue(first["loaded"])
            second = fetch_mod.fetch_law(db_path, "示例法")
            self.assertFalse(second["loaded"])
            self.assertTrue(second["skipped"])
            self.assertEqual(second["article_count"], 2)

    def test_same_hash_refreshes_metadata_without_rebuilding_body(self):
        first_payload = _make_payload(bbbs="law-1", source_hash="same-hash")
        adapter = _FakeAdapter(
            rows=[{"bbbs": "law-1", "title": "中华人民共和国示例法"}],
            payloads={"law-1": first_payload},
        )
        with tempfile.TemporaryDirectory() as td, self._patch_adapter(adapter):
            db_path = Path(td) / "t.db"
            fetch_mod.fetch_law(db_path, "示例法")
            # contextlib.closing 显式关闭连接：Windows 下未关闭的 SQLite 连接会
            # 让 TemporaryDirectory 清理报 WinError 32。
            with contextlib.closing(sqlite3.connect(db_path)) as conn:
                article_ids_before = conn.execute(
                    "SELECT id FROM articles WHERE law_id = ? ORDER BY position", ("law-1",)
                ).fetchall()
                revision_rows_before = conn.execute(
                    "SELECT id, snapshot_json FROM revisions WHERE law_id = ? ORDER BY id",
                    ("law-1",),
                ).fetchall()

            refreshed = {**first_payload}
            refreshed.update(
                {
                    "status": "repealed",
                    "repealed_at": "2026-06-01",
                    "source_checked_at": "2026-08-06T12:00:00+00:00",
                }
            )
            adapter._payloads["law-1"] = refreshed
            result = fetch_mod.fetch_law(db_path, "示例法")

            with contextlib.closing(sqlite3.connect(db_path)) as conn:
                law = conn.execute(
                    "SELECT status, repealed_at, source_checked_at FROM laws WHERE id = ?",
                    ("law-1",),
                ).fetchone()
                article_ids_after = conn.execute(
                    "SELECT id FROM articles WHERE law_id = ? ORDER BY position", ("law-1",)
                ).fetchall()
                revision_rows_after = conn.execute(
                    "SELECT id, snapshot_json FROM revisions WHERE law_id = ? ORDER BY id",
                    ("law-1",),
                ).fetchall()
                last_mode = conn.execute(
                    "SELECT value FROM meta WHERE key = ?",
                    ("source:flk_npc:last_mode",),
                ).fetchone()[0]

        self.assertTrue(result["skipped"])
        self.assertEqual(tuple(law), ("repealed", "2026-06-01", "2026-08-06T12:00:00+00:00"))
        self.assertEqual(article_ids_after, article_ids_before)
        self.assertEqual(revision_rows_after, revision_rows_before)
        self.assertEqual(last_mode, "fetch")

    def test_force_reloads_when_same_source_hash(self):
        adapter = _FakeAdapter(
            rows=[{"bbbs": "law-1", "title": "中华人民共和国示例法"}],
            payloads={"law-1": _make_payload(bbbs="law-1", source_hash="same-hash")},
        )
        with tempfile.TemporaryDirectory() as td, self._patch_adapter(adapter):
            db_path = Path(td) / "t.db"
            first = fetch_mod.fetch_law(db_path, "示例法")
            self.assertTrue(first["loaded"])

            adapter._payloads["law-1"] = _make_payload(
                bbbs="law-1",
                source_hash="same-hash",
                articles=[
                    {
                        "number": "1",
                        "number_display": "第一条",
                        "text": "清洗规则升级后的正文。",
                    }
                ],
            )
            second = fetch_mod.fetch_law(db_path, "示例法", force=True)

            self.assertTrue(second["force"])
            self.assertTrue(second["loaded"])
            self.assertFalse(second["skipped"])
            article = service.get_article(db_path, "示例法", "第一条")
            self.assertEqual(article["article"]["text"], "清洗规则升级后的正文。")

    def test_fetch_uses_local_alias_bbbs_before_remote_search(self):
        from chinalaw.db import connect, migrate
        from chinalaw.loader import load_law_from_dict

        bbbs = "ff8081818c24e05b018c814e6de45ab5"
        stored_payload = {
            "id": "court-contract-interpretation-2023",
            "title": "最高人民法院关于适用《中华人民共和国民法典》合同编通则若干问题的解释",
            "short_title": "合同编通则解释",
            "aliases": ["合通解释"],
            "level": "judicial_interpretation",
            "status": "current",
            "source_url": f"https://flk.npc.gov.cn/detail?id={bbbs}",
            "source_name": "flk.npc.gov.cn",
            "source_checked_at": "2026-04-30T00:00:00+00:00",
            "source_hash": "stub-hash",
            "articles": [
                {"number": "1", "number_display": "第一条", "text": "旧正文。"},
            ],
        }
        fresh_payload = _make_payload(
            bbbs=bbbs,
            title=stored_payload["title"],
            source_hash="fresh-hash",
            articles=[
                {"number": "1", "number_display": "第一条", "text": "新正文。"},
            ],
        )
        fresh_payload["short_title"] = "合同编通则解释"
        fresh_payload["source_name"] = "flk.npc.gov.cn"

        adapter = _FakeAdapter(
            rows=[],
            payloads={bbbs: fresh_payload},
            search_error=AssertionError("remote search should not run"),
        )
        with tempfile.TemporaryDirectory() as td, self._patch_adapter(adapter):
            db_path = Path(td) / "t.db"
            with connect(db_path) as conn:
                migrate(conn)
                load_law_from_dict(conn, stored_payload)

            result = fetch_mod.fetch_law(db_path, "合通解释")

        self.assertEqual(adapter.search_calls, [])
        self.assertEqual(result["matched_bbbs"], bbbs)
        self.assertTrue(result["candidates"][0]["local_alias_resolved"])
        self.assertEqual(result["law"]["id"], "court-contract-interpretation-2023")

    def test_court_gongbao_local_alias_hint_fetches_without_bbbs_key(self):
        from chinalaw.db import connect, migrate
        from chinalaw.loader import load_law_from_dict

        detail_id = "abcdef1234567890abcdef1234567890"
        stored_payload = _make_court_payload(
            detail_id=detail_id,
            title="某某指导案例",
        )
        stored_payload["short_title"] = "某案"
        stored_payload["aliases"] = ["某案"]
        fresh_payload = {
            **stored_payload,
            "source_hash": "court-fresh-hash",
            "articles": [
                {"number": "1", "number_display": "第一条", "text": "新正文。"},
            ],
        }
        adapter = _FakeAdapter(
            rows=[],
            payloads={detail_id: fresh_payload},
            search_error=AssertionError("remote search should not run"),
        )

        with tempfile.TemporaryDirectory() as td, self._patch_adapter(adapter):
            db_path = Path(td) / "t.db"
            with connect(db_path) as conn:
                migrate(conn)
                load_law_from_dict(conn, stored_payload)

            result = fetch_mod.fetch_law(
                db_path,
                "某案",
                source="court_gongbao",
                force=True,
            )

        self.assertEqual(adapter.search_calls, [])
        self.assertEqual(adapter.payload_calls[0], (detail_id, None))
        self.assertEqual(result["matched_id"], detail_id)
        self.assertEqual(result["matched_bbbs"], detail_id)
        self.assertEqual(result["matched_detail_id"], detail_id)
        self.assertTrue(result["candidates"][0]["local_alias_resolved"])
        self.assertNotIn("bbbs", result["candidates"][0])

    def test_spp_gov_cn_local_alias_hint_fetches_without_bbbs_key(self):
        from chinalaw.db import connect, migrate
        from chinalaw.loader import load_law_from_dict

        detail_id = "xwfbh/wsfbt/202501/t20250116_679579"
        stored_payload = _make_spp_payload(detail_id=detail_id, title="某某检察意见")
        stored_payload["short_title"] = "某检察意见"
        stored_payload["aliases"] = ["某检察意见"]
        fresh_payload = {
            **stored_payload,
            "source_hash": "spp-fresh-hash",
            "articles": [
                {"number": "1", "number_display": "第一条", "text": "新检察正文。"},
            ],
        }
        adapter = _FakeAdapter(
            rows=[],
            payloads={detail_id: fresh_payload},
            search_error=AssertionError("remote search should not run"),
        )

        with tempfile.TemporaryDirectory() as td, self._patch_adapter(adapter):
            db_path = Path(td) / "t.db"
            with connect(db_path) as conn:
                migrate(conn)
                load_law_from_dict(conn, stored_payload)

            result = fetch_mod.fetch_law(
                db_path,
                "某检察意见",
                source="spp_gov_cn",
                force=True,
            )

        self.assertEqual(adapter.search_calls, [])
        self.assertEqual(adapter.payload_calls[0], (detail_id, None))
        self.assertEqual(result["matched_id"], detail_id)
        self.assertEqual(result["matched_bbbs"], detail_id)
        self.assertEqual(result["matched_detail_id"], detail_id)
        self.assertTrue(result["candidates"][0]["local_alias_resolved"])
        self.assertNotIn("bbbs", result["candidates"][0])

    def test_court_main_local_alias_hint_fetches_without_bbbs_key(self):
        from chinalaw.db import connect, migrate
        from chinalaw.loader import load_law_from_dict

        detail_id = "zixun/xiangqing/499051"
        stored_payload = _make_court_main_payload(
            detail_id=detail_id,
            title="最高人民法院关于审理示例案件适用法律若干问题的解释（二）",
        )
        fresh_payload = {
            **stored_payload,
            "source_hash": "court-main-fresh-hash",
            "articles": [
                {"number": "1", "number_display": "第一条", "text": "新主站正文。"},
            ],
        }
        adapter = _FakeAdapter(
            rows=[],
            payloads={detail_id: fresh_payload},
            search_error=AssertionError("remote search should not run"),
        )

        with tempfile.TemporaryDirectory() as td, self._patch_adapter(adapter):
            db_path = Path(td) / "t.db"
            with connect(db_path) as conn:
                migrate(conn)
                load_law_from_dict(conn, stored_payload)

            result = fetch_mod.fetch_law(
                db_path,
                "示例案件解释二",
                source="court_main",
                force=True,
            )

        self.assertEqual(adapter.search_calls, [])
        self.assertEqual(adapter.payload_calls[0], (detail_id, None))
        self.assertEqual(result["matched_id"], detail_id)
        self.assertEqual(result["matched_bbbs"], detail_id)
        self.assertEqual(result["matched_detail_id"], detail_id)
        self.assertTrue(result["candidates"][0]["local_alias_resolved"])
        self.assertNotIn("bbbs", result["candidates"][0])

    # ---- canonical id 补全 ---------------------------------------------------

    def test_refills_existing_stable_id_instead_of_creating_duplicate(self):
        # 模拟真实场景：DB 已有 flk-civil-code-2020（fixture 入的 stub），
        # 一段时间后用户跑 chinalaw fetch 民法典，flk 返回 raw bbbs 是
        # "ff808081729d1efe01729d50b5c500bf"。
        # fetch 必须把 payload 写到既有的 flk-civil-code-2020 上，否则
        # 后续 chinalaw article 民法典 ... 仍解析到旧 stub，永远命不中。
        from chinalaw.db import connect, migrate
        from chinalaw.loader import load_law_from_dict

        stub_payload = {
            "id": "flk-civil-code-2020",
            "title": "中华人民共和国民法典",
            "short_title": "民法典",
            "aliases": ["民法典"],
            "level": "law",
            "status": "current",
            "source_url": "https://flk.npc.gov.cn/detail2.html?xxx",
            "source_name": "flk.npc.gov.cn",
            "source_checked_at": "2026-04-19T00:00:00+00:00",
            "source_hash": "stub-hash",
            "articles": [
                {"number": "1", "number_display": "第一条", "text": "总则。"},
            ],
        }

        flk_bbbs = "ff808081729d1efe01729d50b5c500bf"
        flk_payload = _make_payload(
            bbbs=flk_bbbs,
            title="中华人民共和国民法典",
            source_hash="flk-fresh-hash",
            articles=[
                {"number": "1", "number_display": "第一条", "text": "总则。", "part": None},
                {
                    "number": "585",
                    "number_display": "第五百八十五条",
                    "text": "当事人可以约定一方违约时应当根据违约情况向对方支付一定数额的违约金……",
                    "part": "第三编 合同/第八章 违约责任",
                },
            ],
        )
        flk_payload["short_title"] = "民法典"
        flk_payload["aliases"] = ["民法典"]
        flk_payload["source_name"] = "flk.npc.gov.cn"

        adapter = _FakeAdapter(
            rows=[{"bbbs": flk_bbbs, "title": "中华人民共和国民法典", "sxx": 3}],
            payloads={flk_bbbs: flk_payload},
        )

        with tempfile.TemporaryDirectory() as td, self._patch_adapter(adapter):
            db_path = Path(td) / "t.db"
            with connect(db_path) as conn:
                migrate(conn)
                load_law_from_dict(conn, stub_payload)

            result = fetch_mod.fetch_law(db_path, "民法典")
            self.assertTrue(result["loaded"])
            self.assertEqual(result["matched_bbbs"], flk_bbbs)

            article = service.get_article(db_path, "民法典", "第五百八十五条")
            self.assertIsNotNone(article)
            self.assertEqual(article["law"]["id"], "flk-civil-code-2020")
            self.assertIn("当事人可以约定一方违约时", article["article"]["text"])

            # DB 里仍然只有一部《民法典》，没有出现 raw bbbs 的重复行
            with connect(db_path) as conn:
                row_count = conn.execute(
                    "SELECT COUNT(*) FROM laws WHERE title = ?",
                    ("中华人民共和国民法典",),
                ).fetchone()[0]
            self.assertEqual(row_count, 1)

    def test_does_not_refill_when_source_name_differs(self):
        # 防御性场景：DB 里偶然存在一条同名但来自不同来源的记录（例如未来用户
        # 用 norm import 手工导入了《民法典》节选）。fetch 此时不应把 FLK 的
        # 全文覆盖到那条上，应回退成新建以 raw bbbs 为 id 的记录。
        from chinalaw.db import connect, migrate
        from chinalaw.loader import load_law_from_dict

        manual_payload = {
            "id": "manual-civil-code-extract",
            "title": "中华人民共和国民法典",
            "short_title": "民法典",
            "aliases": ["民法典"],
            "level": "law",
            "status": "current",
            "source_url": "file:///local/manual.docx",
            "source_name": "manual",
            "source_checked_at": "2026-01-01T00:00:00+00:00",
            "source_hash": "manual-hash",
            "articles": [
                {"number": "1", "number_display": "第一条", "text": "节选。"},
            ],
        }

        flk_bbbs = "ff808081729d1efe01729d50b5c500bf"
        flk_payload = _make_payload(bbbs=flk_bbbs, title="中华人民共和国民法典")
        flk_payload["short_title"] = "民法典"
        flk_payload["aliases"] = ["民法典"]
        flk_payload["source_name"] = "flk.npc.gov.cn"

        adapter = _FakeAdapter(
            rows=[{"bbbs": flk_bbbs, "title": "中华人民共和国民法典", "sxx": 3}],
            payloads={flk_bbbs: flk_payload},
        )

        with tempfile.TemporaryDirectory() as td, self._patch_adapter(adapter):
            db_path = Path(td) / "t.db"
            with connect(db_path) as conn:
                migrate(conn)
                load_law_from_dict(conn, manual_payload)

            fetch_mod.fetch_law(db_path, "民法典")

            with connect(db_path) as conn:
                rows = conn.execute(
                    "SELECT id, source_name FROM laws WHERE title = ? ORDER BY id",
                    ("中华人民共和国民法典",),
                ).fetchall()
            ids = sorted(r["id"] for r in rows)
            # 同名但 source_name 不同：fetch 不复用 manual id，新建 FLK 行
            self.assertEqual(ids, sorted(["manual-civil-code-extract", flk_bbbs]))

    def test_canonical_lookup_checks_all_same_title_revisions(self):
        from chinalaw.db import connect, migrate
        from chinalaw.loader import load_law_from_dict

        old = _make_payload(
            bbbs="old-stable-id",
            title="中华人民共和国公司法",
            source_hash="old-hash",
        )
        old.update(
            short_title="公司法",
            aliases=["公司法"],
            released_at="2020-01-01",
            effective_at="2020-01-01",
        )
        matching = _make_payload(
            bbbs="matching-stable-id",
            title="中华人民共和国公司法",
            source_hash="matching-hash",
        )
        matching.update(
            short_title="公司法",
            aliases=["公司法"],
            released_at="2024-01-01",
            effective_at="2024-07-01",
        )
        incoming = {**matching, "id": "raw-upstream-id"}

        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "t.db"
            with connect(db_path) as conn:
                migrate(conn)
                load_law_from_dict(conn, old)
                load_law_from_dict(conn, matching)

            resolved = fetch_mod._try_resolve_canonical_id(db_path, incoming)

        self.assertEqual(resolved, "matching-stable-id")

    def test_canonical_lookup_fails_loudly_when_multiple_rows_strict_match(self):
        from chinalaw.db import connect, migrate
        from chinalaw.loader import load_law_from_dict

        first = _make_payload(bbbs="stable-a", source_hash="hash-a")
        second = _make_payload(bbbs="stable-b", source_hash="hash-b")
        incoming = _make_payload(bbbs="raw-upstream", source_hash="hash-c")

        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "t.db"
            with connect(db_path) as conn:
                migrate(conn)
                load_law_from_dict(conn, first)
                load_law_from_dict(conn, second)

            with self.assertRaises(fetch_mod.FetchAmbiguousError) as ctx:
                fetch_mod._try_resolve_canonical_id(db_path, incoming)

        self.assertEqual(
            [candidate["id"] for candidate in ctx.exception.candidates],
            ["stable-a", "stable-b"],
        )

    def test_applicability_references_supply_old_law_stable_ids(self):
        from chinalaw import applicability

        cases = [
            ("flk-contract-law-1999", "中华人民共和国合同法", "1999-03-15", "1999-10-01"),
            ("flk-property-law-2007", "中华人民共和国物权法", "2007-03-16", "2007-10-01"),
            ("flk-security-law-1995", "中华人民共和国担保法", "1995-06-30", "1995-10-01"),
            (
                "flk-tort-liability-law-2009",
                "中华人民共和国侵权责任法",
                "2009-12-26",
                "2010-07-01",
            ),
            ("flk-company-law-2018", "中华人民共和国公司法", "2018-10-26", "2018-10-26"),
            ("flk-company-law-2024", "中华人民共和国公司法", "2023-12-29", "2024-07-01"),
        ]
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "t.db"
            applicability.load_applicability_fixtures(db_path)
            resolved = []
            for expected_id, title, released_at, effective_at in cases:
                payload = _make_payload(
                    bbbs=f"raw-{expected_id}",
                    title=title,
                    source_hash=f"hash-{expected_id}",
                )
                payload.update(
                    short_title=title.removeprefix("中华人民共和国"),
                    aliases=[title.removeprefix("中华人民共和国")],
                    released_at=released_at,
                    effective_at=effective_at,
                )
                resolved.append(fetch_mod._try_resolve_canonical_id(db_path, payload))

        self.assertEqual(resolved, [case[0] for case in cases])

    def test_fetch_closes_applicability_needs_fetch_loop(self):
        from chinalaw import applicability

        raw_id = "raw-contract-law-1999"
        payload = _make_payload(
            bbbs=raw_id,
            title="中华人民共和国合同法",
            source_hash="contract-law-fulltext",
        )
        payload.update(
            short_title="合同法",
            aliases=["合同法"],
            status="repealed",
            released_at="1999-03-15",
            effective_at="1999-10-01",
            repealed_at="2021-01-01",
        )
        adapter = _FakeAdapter(
            rows=[{"bbbs": raw_id, "title": "中华人民共和国合同法", "sxx": 1}],
            payloads={raw_id: payload},
        )

        with tempfile.TemporaryDirectory() as td, self._patch_adapter(adapter):
            db_path = Path(td) / "t.db"
            applicability.load_applicability_fixtures(db_path)
            before = service.applicable(db_path, as_of="2019-01-01", topic="合同效力")
            result = fetch_mod.fetch_law(db_path, "合同法")
            after = service.applicable(db_path, as_of="2019-01-01", topic="合同效力")

        self.assertTrue(
            any(
                item["law_id"] == "flk-contract-law-1999"
                for item in before["matches"][0]["needs_fetch"]
            )
        )
        self.assertEqual(result["law"]["id"], "flk-contract-law-1999")
        self.assertIsNotNone(after["matches"][0]["primary_law"])
        self.assertFalse(
            any(
                item["law_id"] == "flk-contract-law-1999"
                for item in after["matches"][0]["needs_fetch"]
            )
        )


class FetchCanonicalIdAcrossOutputsTests(unittest.TestCase):
    """canonical id 应该应用到所有出口：response.law / dry_run / to_fixture / 入库。

    背景：``_resolve_canonical_id`` 早期版本只在 ``_persist`` 内部生效，
    导致 ``fetch_law`` 返回的 ``result['law']['id']`` 仍然是 raw bbbs，
    ``--to-fixture`` 写出的 JSON 也带 raw bbbs。agent 直接消费响应或
    把 fixture 提交进 ``data/fixtures/`` 都会破坏既有 stable id 的引用链。
    """

    def _make_db_with_stub(self, db_path: Path) -> None:
        from chinalaw.db import connect, migrate
        from chinalaw.loader import load_law_from_dict

        stub_payload = {
            "id": "flk-civil-code-2020",
            "title": "中华人民共和国民法典",
            "short_title": "民法典",
            "aliases": ["民法典"],
            "level": "law",
            "status": "current",
            "source_url": "https://flk.npc.gov.cn/detail2.html?xxx",
            "source_name": "flk.npc.gov.cn",
            "source_checked_at": "2026-04-19T00:00:00+00:00",
            "source_hash": "stub-hash",
            "articles": [
                {"number": "1", "number_display": "第一条", "text": "总则。"},
            ],
        }
        with connect(db_path) as conn:
            migrate(conn)
            load_law_from_dict(conn, stub_payload)

    def _flk_adapter(self, raw_bbbs: str) -> _FakeAdapter:
        flk_payload = _make_payload(
            bbbs=raw_bbbs,
            title="中华人民共和国民法典",
            source_hash="flk-fresh-hash",
        )
        flk_payload["short_title"] = "民法典"
        flk_payload["aliases"] = ["民法典"]
        flk_payload["source_name"] = "flk.npc.gov.cn"
        return _FakeAdapter(
            rows=[{"bbbs": raw_bbbs, "title": "中华人民共和国民法典", "sxx": 3}],
            payloads={raw_bbbs: flk_payload},
        )

    def _patch(self, adapter: _FakeAdapter):
        return patch("chinalaw.fetch.get_source_adapter", return_value=adapter)

    def test_canonical_id_applied_to_fetch_response_law(self):
        raw_bbbs = "ff808081729d1efe01729d50b5c500bf"
        adapter = self._flk_adapter(raw_bbbs)
        with tempfile.TemporaryDirectory() as td, self._patch(adapter):
            db_path = Path(td) / "t.db"
            self._make_db_with_stub(db_path)
            result = fetch_mod.fetch_law(db_path, "民法典")
        # response.law.id 必须是 stable id，否则 agent 据此生成的引用 / 规范包
        # 条目和入库后的 service.get_law 不一致。
        self.assertEqual(result["law"]["id"], "flk-civil-code-2020")
        # matched_bbbs 仍保留 raw bbbs，方便溯源 / --prefer-bbbs 后续调用
        self.assertEqual(result["matched_bbbs"], raw_bbbs)

    def test_canonical_id_applied_to_to_fixture_output(self):
        raw_bbbs = "ff808081729d1efe01729d50b5c500bf"
        adapter = self._flk_adapter(raw_bbbs)
        with tempfile.TemporaryDirectory() as td, self._patch(adapter):
            db_path = Path(td) / "t.db"
            self._make_db_with_stub(db_path)
            fixture_path = Path(td) / "civil_code.json"
            fetch_mod.fetch_law(db_path, "民法典", to_fixture=str(fixture_path))
            written = json.loads(fixture_path.read_text(encoding="utf-8"))
        # 写入 PR 的 fixture 必须带 stable id；否则 chinalaw sync --from-dir
        # 加载后会出现 raw bbbs vs stable id 的双胞胎 row，pack 引用断裂。
        self.assertEqual(written["id"], "flk-civil-code-2020")

    def test_to_fixture_existing_file_preserves_id_without_db(self):
        raw_bbbs = "ff808081729d1efe01729d50b5c500bf"
        adapter = self._flk_adapter(raw_bbbs)
        with tempfile.TemporaryDirectory() as td, self._patch(adapter):
            db_path = Path(td) / "missing.db"
            fixture_path = Path(td) / "civil_code.json"
            fixture_path.write_text(
                json.dumps(
                    {
                        "id": "flk-civil-code-2020",
                        "title": "中华人民共和国民法典",
                        "short_title": "民法典",
                        "aliases": ["民法典"],
                        "source_name": "flk.npc.gov.cn",
                        "articles": [],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            result = fetch_mod.fetch_law(
                db_path,
                "民法典",
                to_fixture=str(fixture_path),
            )
            written = json.loads(fixture_path.read_text(encoding="utf-8"))
            self.assertFalse(db_path.exists())

        self.assertEqual(result["law"]["id"], "flk-civil-code-2020")
        self.assertEqual(written["id"], "flk-civil-code-2020")

    def test_to_fixture_existing_file_preserves_id_for_same_title_even_source_changed(self):
        raw_bbbs = "ff8081818c24e05b018c814e6de45ab5"
        payload = _make_payload(
            bbbs=raw_bbbs,
            title="最高人民法院关于适用《中华人民共和国民法典》合同编通则若干问题的解释",
            source_hash="fresh-contract-interpretation",
        )
        payload["short_title"] = "合同编通则解释"
        payload["source_name"] = "flk.npc.gov.cn"
        payload["released_at"] = "2023-12-04"
        payload["effective_at"] = "2023-12-05"
        adapter = _FakeAdapter(
            rows=[
                {
                    "bbbs": raw_bbbs,
                    "title": payload["title"],
                    "sxx": 3,
                }
            ],
            payloads={raw_bbbs: payload},
        )
        with tempfile.TemporaryDirectory() as td, self._patch(adapter):
            db_path = Path(td) / "missing.db"
            fixture_path = Path(td) / "contract_interpretation.json"
            fixture_path.write_text(
                json.dumps(
                    {
                        "id": "court-contract-interpretation-2023",
                        "title": payload["title"],
                        "short_title": "合同编通则解释",
                        "source_name": "court.gov.cn",
                        "released_at": "2023-12-04",
                        "effective_at": "2023-12-05",
                        "articles": [],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            result = fetch_mod.fetch_law(
                db_path,
                "合同编通则解释",
                to_fixture=str(fixture_path),
            )
            written = json.loads(fixture_path.read_text(encoding="utf-8"))

        self.assertFalse(db_path.exists())
        self.assertEqual(result["law"]["id"], "court-contract-interpretation-2023")
        self.assertEqual(written["id"], "court-contract-interpretation-2023")

    def test_to_fixture_existing_file_does_not_preserve_id_for_same_title_different_version(self):
        raw_bbbs = "company-law-2018-raw"
        payload = _make_payload(
            bbbs=raw_bbbs,
            title="中华人民共和国公司法",
            source_hash="company-law-2018",
        )
        payload["short_title"] = "公司法"
        payload["source_name"] = "flk.npc.gov.cn"
        payload["released_at"] = "2018-10-26"
        payload["effective_at"] = "2018-10-26"
        adapter = _FakeAdapter(
            rows=[
                {
                    "bbbs": raw_bbbs,
                    "title": "中华人民共和国公司法",
                    "gbrq": "2018-10-26",
                    "sxx": 2,
                }
            ],
            payloads={raw_bbbs: payload},
        )
        with tempfile.TemporaryDirectory() as td, self._patch(adapter):
            db_path = Path(td) / "missing.db"
            fixture_path = Path(td) / "company_law_2024.json"
            fixture_path.write_text(
                json.dumps(
                    {
                        "id": "flk-company-law-2024",
                        "title": "中华人民共和国公司法",
                        "short_title": "公司法",
                        "source_name": "flk.npc.gov.cn",
                        "released_at": "2023-12-29",
                        "effective_at": "2024-07-01",
                        "articles": [],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            result = fetch_mod.fetch_law(
                db_path,
                "公司法",
                to_fixture=str(fixture_path),
            )
            written = json.loads(fixture_path.read_text(encoding="utf-8"))

        self.assertEqual(result["law"]["id"], raw_bbbs)
        self.assertEqual(written["id"], raw_bbbs)

    def test_canonical_id_applied_to_dry_run_response(self):
        raw_bbbs = "ff808081729d1efe01729d50b5c500bf"
        adapter = self._flk_adapter(raw_bbbs)
        with tempfile.TemporaryDirectory() as td, self._patch(adapter):
            db_path = Path(td) / "t.db"
            self._make_db_with_stub(db_path)
            result = fetch_mod.fetch_law(db_path, "民法典", dry_run=True)
        self.assertTrue(result["dry_run"])
        self.assertFalse(result["loaded"])
        self.assertEqual(result["law"]["id"], "flk-civil-code-2020")

    def test_dry_run_does_not_create_db_for_canonical_lookup(self):
        raw_bbbs = "ff808081729d1efe01729d50b5c500bf"
        adapter = self._flk_adapter(raw_bbbs)
        with tempfile.TemporaryDirectory() as td, self._patch(adapter):
            db_path = Path(td) / "missing.db"
            result = fetch_mod.fetch_law(db_path, "民法典", dry_run=True)
            self.assertFalse(db_path.exists())

        self.assertEqual(result["law"]["id"], raw_bbbs)

    def test_dry_run_does_not_change_existing_db_bytes_or_schema(self):
        raw_bbbs = "ff808081729d1efe01729d50b5c500bf"
        adapter = self._flk_adapter(raw_bbbs)
        with tempfile.TemporaryDirectory() as td, self._patch(adapter):
            db_path = Path(td) / "legacy.db"
            # contextlib.closing 显式关闭连接：Windows 下未关闭的 SQLite 连接会
            # 让 TemporaryDirectory 清理报 WinError 32。
            with contextlib.closing(sqlite3.connect(db_path)) as conn:
                conn.execute("CREATE TABLE sentinel(value TEXT NOT NULL)")
                conn.execute("INSERT INTO sentinel(value) VALUES ('keep')")
                conn.commit()
            before = db_path.read_bytes()

            result = fetch_mod.fetch_law(db_path, "民法典", dry_run=True)

            after = db_path.read_bytes()
            with contextlib.closing(sqlite3.connect(db_path)) as conn:
                tables = {
                    row[0]
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
                    )
                }

        self.assertTrue(result["dry_run"])
        self.assertEqual(after, before)
        self.assertEqual(tables, {"sentinel"})

    def test_canonical_lookup_failure_falls_back_to_raw_bbbs(self):
        # 防御性场景：DB 路径不可达 / migrate 失败时，canonical 解析应安全降级，
        # 用 raw bbbs 入库，不阻塞 fetch 主流程。
        raw_bbbs = "raw-bbbs-99"
        adapter = self._flk_adapter(raw_bbbs)
        with tempfile.TemporaryDirectory() as td, self._patch(adapter):
            db_path = Path(td) / "t.db"
            # DB 没有任何既有同名记录 → canonical 返回 None → 用 raw bbbs
            result = fetch_mod.fetch_law(db_path, "民法典")
        self.assertEqual(result["law"]["id"], raw_bbbs)
        self.assertEqual(result["matched_bbbs"], raw_bbbs)


class FetchActionMutexTests(unittest.TestCase):
    """三种"非默认入库"动作互斥：dry-run / to-fixture / list-matches。

    CLI 层 argparse mutually-exclusive group 兜底命令行用法；
    library 层 FetchActionConflictError 兜底 SDK 直接调用。
    见 ADR-0006 §3 / CONTRACT.md §4.11。
    """

    def test_library_rejects_dry_run_with_to_fixture(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(fetch_mod.FetchActionConflictError) as ctx:
                fetch_mod.fetch_law(
                    Path(td) / "t.db",
                    "民法典",
                    dry_run=True,
                    to_fixture=str(Path(td) / "out.json"),
                )
            self.assertEqual(ctx.exception.exit_code, 2)

    def test_library_rejects_list_matches_with_to_fixture(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(fetch_mod.FetchActionConflictError) as ctx:
                fetch_mod.fetch_law(
                    Path(td) / "t.db",
                    "民法典",
                    list_matches=True,
                    to_fixture=str(Path(td) / "out.json"),
                )
            self.assertEqual(ctx.exception.exit_code, 2)

    def test_library_rejects_list_matches_with_dry_run(self):
        with tempfile.TemporaryDirectory() as td, self.assertRaises(
            fetch_mod.FetchActionConflictError
        ):
            fetch_mod.fetch_law(
                Path(td) / "t.db",
                "民法典",
                list_matches=True,
                dry_run=True,
            )

    def test_library_rejects_all_three_actions(self):
        with tempfile.TemporaryDirectory() as td, self.assertRaises(
            fetch_mod.FetchActionConflictError
        ):
            fetch_mod.fetch_law(
                Path(td) / "t.db",
                "民法典",
                dry_run=True,
                list_matches=True,
                to_fixture=str(Path(td) / "out.json"),
            )

    def test_cli_argparse_rejects_conflicting_actions(self):
        # build_parser 直接构造，不打 flk 真实接口；argparse 冲突 sys.exit(2)
        from chinalaw.cli import build_parser

        parser = build_parser()
        for argv in (
            ["fetch", "民法典", "--dry-run", "--to-fixture", "/tmp/x.json"],
            ["fetch", "民法典", "--list-matches", "--to-fixture", "/tmp/x.json"],
            ["fetch", "民法典", "--list-matches", "--dry-run"],
        ):
            with self.subTest(argv=argv):
                with self.assertRaises(SystemExit) as ctx:
                    parser.parse_args(argv)
                self.assertEqual(ctx.exception.code, 2)

    def test_cli_fetch_accepts_court_source_and_prefer_id(self):
        from chinalaw.cli import build_parser

        parser = build_parser()
        cases = (
            ("court_gongbao", "a" * 30),
            ("court_main", "zixun/xiangqing/499051"),
        )
        for source, prefer_id in cases:
            with self.subTest(source=source):
                args = parser.parse_args(
                    [
                        "fetch",
                        "示例案件",
                        "--source",
                        source,
                        "--prefer-id",
                        prefer_id,
                    ]
                )
                self.assertEqual(args.source, source)
                self.assertEqual(args.prefer_bbbs, prefer_id)


class AliasRuleTests(unittest.TestCase):
    def test_feedback_alias_rules_are_derived_during_cleaning(self):
        self.assertIn(
            "民诉法解释",
            common_law_aliases("最高人民法院关于适用《中华人民共和国民事诉讼法》的解释"),
        )
        self.assertIn(
            "物权编解释",
            common_law_aliases("最高人民法院关于适用《中华人民共和国民法典》物权编的解释（一）"),
        )
        # 担保制度解释：通用 民法典》<topic> 规则可派生 ``担保制度解释`` 与
        # ``民法典担保制度解释``。「担保解释」是律师社区两字简称，是领域
        # 黑话，已迁去 fixture aliases；规则层不再生成。
        guarantee = common_law_aliases(
            "最高人民法院关于适用《中华人民共和国民法典》有关担保制度的解释"
        )
        self.assertIn("担保制度解释", guarantee)
        self.assertIn("民法典担保制度解释", guarantee)


class EnsureLawTests(unittest.TestCase):
    """ensure 是本地优先补库入口：已有不联网，缺失才走 fetch。"""

    def _load_payload(self, db_path: Path, payload: dict) -> None:
        from chinalaw.db import connect, migrate
        from chinalaw.loader import load_law_from_dict

        with connect(db_path) as conn:
            migrate(conn)
            load_law_from_dict(conn, payload)

    def test_normalize_law_name_keeps_semantic_suffix_and_strips_year(self):
        raw = "最高人民法院关于适用《中华人民共和国民法典》合同编通则若干问题的解释（一）（2020年）.pdf"
        self.assertEqual(
            ensure_mod.normalize_law_name(raw),
            "最高人民法院关于适用《中华人民共和国民法典》合同编通则若干问题的解释（一）",
        )

    def test_collect_names_from_dir_reads_filenames_only(self):
        with tempfile.TemporaryDirectory() as td:
            directory = Path(td)
            (directory / "中华人民共和国民法典（2020年）.pdf").write_text(
                "body must not be parsed",
                encoding="utf-8",
            )
            (directory / ".DS_Store").write_text("ignored", encoding="utf-8")
            (directory / "中华人民共和国公司法.docx").write_text("ignored", encoding="utf-8")

            names = ensure_mod.collect_names(from_dir=directory)

        self.assertEqual(names, ["中华人民共和国公司法", "中华人民共和国民法典"])

    def test_ensure_skips_populated_local_law(self):
        payload = _make_payload(bbbs="law-1", title="中华人民共和国民法典")
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "t.db"
            self._load_payload(db_path, payload)

            with patch("chinalaw.ensure.fetch_mod.fetch_law") as mocked_fetch:
                report = ensure_mod.ensure_laws(db_path, ["民法典"], interval=0)

        mocked_fetch.assert_not_called()
        self.assertTrue(report["ok"])
        self.assertEqual(report["present_count"], 1)
        self.assertEqual(report["fetch_attempt_count"], 0)
        self.assertEqual(report["items"][0]["status"], "present")

    def test_ensure_fetches_missing_law_and_persists(self):
        payload = _make_payload(bbbs="law-1", title="中华人民共和国示例法")

        def fake_fetch(db_path, name, *, source, limit):
            self._load_payload(Path(db_path), payload)
            return {
                "matched_bbbs": "law-1",
                "matched_title": payload["title"],
                "article_count": len(payload["articles"]),
                "loaded": True,
                "skipped": False,
                "law": {
                    **payload,
                    "article_count": len(payload["articles"]),
                    "articles_coverage": "populated",
                },
            }

        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "t.db"
            with patch("chinalaw.ensure.fetch_mod.fetch_law", side_effect=fake_fetch):
                report = ensure_mod.ensure_laws(db_path, ["示例法"], interval=0)
            law = service.get_law(db_path, "示例法")

        self.assertTrue(report["ok"])
        self.assertEqual(report["fetched_count"], 1)
        self.assertEqual(report["items"][0]["status"], "fetched")
        self.assertIsNotNone(law)
        self.assertEqual(law["article_count"], 2)

    def test_ensure_marks_zero_article_fetch_as_failed(self):
        def fake_fetch(db_path, name, *, source, limit):
            return {
                "matched_bbbs": "empty-law",
                "matched_title": "空法规",
                "article_count": 0,
                "loaded": True,
                "skipped": False,
                "law": {"id": "empty-law", "title": "空法规", "articles": []},
            }

        with tempfile.TemporaryDirectory() as td, patch(
            "chinalaw.ensure.fetch_mod.fetch_law", side_effect=fake_fetch
        ):
            report = ensure_mod.ensure_laws(Path(td) / "t.db", ["空法规"], interval=0)

        self.assertFalse(report["ok"])
        self.assertEqual(report["failed_count"], 1)
        self.assertEqual(report["items"][0]["status"], "failed")
        self.assertEqual(report["items"][0]["reason"], "empty_articles")

    def test_ensure_includes_ambiguous_candidates(self):
        candidates = [
            {"bbbs": "law-1", "title": "候选一", "status": "current", "released_at": ""},
            {"bbbs": "law-2", "title": "候选二", "status": "current", "released_at": ""},
        ]

        def fake_fetch(db_path, name, *, source, limit):
            raise fetch_mod.FetchAmbiguousError("ambiguous", candidates=candidates)

        with tempfile.TemporaryDirectory() as td, patch(
            "chinalaw.ensure.fetch_mod.fetch_law", side_effect=fake_fetch
        ):
            report = ensure_mod.ensure_laws(Path(td) / "t.db", ["候选"], interval=0)

        self.assertFalse(report["ok"])
        self.assertEqual(report["items"][0]["error"], "FetchAmbiguousError")
        self.assertEqual(report["items"][0]["candidates"], candidates)

    def test_cli_parser_accepts_ensure_batch_options(self):
        from chinalaw.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(
            [
                "ensure",
                "民法典",
                "--from-dir",
                "/tmp/laws",
                "--filenames-only",
                "--interval",
                "0",
            ]
        )

        self.assertEqual(args.command, "ensure")
        self.assertEqual(args.names, ["民法典"])
        self.assertEqual(args.from_dir, "/tmp/laws")
        self.assertTrue(args.filenames_only)
        self.assertEqual(args.interval, 0.0)


if __name__ == "__main__":
    unittest.main()
