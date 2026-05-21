"""Tests for securities self-regulatory source adapters."""

from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from chinalaw import cleaning, fetch, sources
from chinalaw.adapters import bse_cn, sac_net_cn, securities_rules, sse_com_cn, szse_cn
from chinalaw.document_numbers import infer_source, infer_source_id
from chinalaw.service import normalize_article_number


class SecuritiesRuleCleaningTests(unittest.TestCase):
    def test_decimal_article_numbers_are_preserved(self) -> None:
        self.assertEqual(normalize_article_number("2.2.7"), "2.2.7")
        self.assertEqual(normalize_article_number("第2.2.7条"), "2.2.7")

    def test_parse_decimal_rule_clauses_from_text(self) -> None:
        articles = cleaning.parse_articles_from_text(
            "\n".join(
                [
                    "第一章 总则",
                    "1.1 为了规范证券交易所业务，制定本规则。",
                    "1.2 本规则适用于相关市场主体。",
                ]
            )
        )

        self.assertEqual([item["number"] for item in articles], ["1.1", "1.2"])
        self.assertEqual(articles[0]["number_display"], "1.1")
        self.assertEqual(articles[0]["part"], "第一章 总则")

    def test_parse_decimal_rule_clauses_ignores_cross_references(self) -> None:
        articles = cleaning.parse_articles_from_text(
            "\n".join(
                [
                    "1.1 为了规范证券交易所业务，制定本规则。",
                    "1.6 条规定的监管对象应当配合。",
                    "4.5.3 条、 第 4.5.8 条的规定另有适用。",
                    "1.2 本规则适用于相关市场主体。",
                ]
            )
        )

        self.assertEqual([item["number"] for item in articles], ["1.1", "1.2"])
        self.assertIn("1.6 条规定", articles[0]["text"])
        self.assertIn("4.5.3 条", articles[0]["text"])

    def test_generic_pdf_cleanup_keeps_cross_law_article_reference_inline(self) -> None:
        raw = "\n".join(
            [
                "12.1 本规则下列用语的含义如下：",
                "特别表决权股份，是指上市公司依照《公司法》",
                "第一百四十四条的规定发行的股份。",
                "- 152 -第十三章 附则",
                "13.1 本规则由本所负责解释。",
            ]
        )

        text = securities_rules._clean_pdf_text(raw)
        articles = cleaning.parse_articles_from_text(text)

        self.assertNotIn("\n第一百四十四条", text)
        self.assertEqual([item["number"] for item in articles], ["12.1", "13.1"])
        self.assertIn("《公司法》第一百四十四条的规定", articles[0]["text"])
        self.assertEqual(articles[1]["part"], "第十三章 附则")

    def test_generic_pdf_cleanup_drops_table_of_contents_lines(self) -> None:
        raw = "\n".join(
            [
                "北京证券交易所股票上市规则 目 录",
                "第一章 总则 .........................................- 4 -",
                "第一节 定期报告编制和披露要求 ...................- 77 -",
                "第一章 总 则",
                "1.1 为了规范股票上市和持续监管事宜，制定本规则。",
            ]
        )

        text = securities_rules._clean_pdf_text(raw)
        articles = cleaning.parse_articles_from_text(text)

        self.assertNotIn(".........", text)
        self.assertEqual(articles[0]["number"], "1.1")
        self.assertEqual(articles[0]["part"], "第一章 总 则")


class SzseCnAdapterTests(unittest.TestCase):
    def test_search_list_uses_szse_content_api(self) -> None:
        payload = {
            "totalSize": 1,
            "data": [
                {
                    "id": "620193",
                    "doctitle": "关于发布《深圳证券交易所<span class=\"keyword\">股票上市规则</span>（2026年修订）》的通知",
                    "docpuburl": "http://www.szse.cn/lawrules/rule/stock/supervision/mb/t20260424_620193.html",
                    "docpubtime": 1777029505000,
                    "doctype": "html",
                }
            ],
        }
        result = securities_rules.FetchResult(
            url="https://www.szse.cn/api/search/content",
            status_code=200,
            headers={},
            text=json.dumps(payload, ensure_ascii=False),
        )
        adapter = szse_cn.SecuritiesRulesAdapter(szse_cn.CONFIG)
        with patch.object(securities_rules, "_fetch_text", return_value=result):
            found = adapter.search_list("股票上市规则", page_size=5)

        self.assertEqual(found["source"], "szse_cn")
        self.assertEqual(found["rows"][0]["detail_id"], "lawrules/rule/stock/supervision/mb/t20260424_620193.html")
        self.assertEqual(found["rows"][0]["status"], "current")


class SseComCnAdapterTests(unittest.TestCase):
    DETAIL_HTML = """
<html>
<head><title>关于发布《上海证券交易所股票上市规则（2025年4月修订）》的通知 | 上海证券交易所</title></head>
<body>
<div class="allZoom">
  <p style="text-align:center">上证发〔2025〕59号</p>
  <p>本规则自发布之日起施行。</p>
  <p>附件：<a title="1.上海证券交易所股票上市规则（2025年4月修订）" href="10777756/files/rule.pdf">1.上海证券交易所股票上市规则（2025年4月修订）</a></p>
  <p><a title="2.《上海证券交易所股票上市规则》修订说明" href="10777756/files/note.pdf">修订说明</a></p>
</div>
</body>
</html>
"""

    def test_build_law_payload_uses_rule_attachment_not_revision_note(self) -> None:
        detail = securities_rules.FetchResult(
            url="https://www.sse.com.cn/services/listingwithsse/home/policy/supervise/c/c_20250425_10777756.shtml",
            status_code=200,
            headers={},
            text=self.DETAIL_HTML,
        )
        pdf = (detail.url + "/10777756/files/rule.pdf", {"Content-Type": "application/pdf"}, b"%PDF")
        adapter = sse_com_cn.SecuritiesRulesAdapter(sse_com_cn.CONFIG)
        with (
            patch.object(securities_rules, "_fetch_text", return_value=detail),
            patch.object(securities_rules, "_fetch_bytes", return_value=pdf) as fetch_bytes,
            patch.object(
                securities_rules,
                "_pdf_bytes_to_text",
                return_value="1.1 为了规范股票上市行为，制定本规则。\n1.2 本规则适用于主板股票上市。",
            ),
        ):
            payload = adapter.build_law_payload(
                "services/listingwithsse/home/policy/supervise/c/c_20250425_10777756.shtml"
            )

        self.assertEqual(payload["id"], "sse_com_cn:services/listingwithsse/home/policy/supervise/c/c_20250425_10777756.shtml")
        self.assertEqual(payload["title"], "上海证券交易所股票上市规则（2025年4月修订）")
        self.assertEqual(payload["level"], "self_regulatory_rule")
        self.assertEqual(payload["issuing_body"], "上海证券交易所")
        self.assertEqual(payload["document_number"], "上证发〔2025〕59号")
        self.assertEqual(payload["articles"][0]["number"], "1.1")
        self.assertIn("rule.pdf", fetch_bytes.call_args.args[0])
        self.assertNotIn("note.pdf", fetch_bytes.call_args.args[0])


class BseCnAdapterTests(unittest.TestCase):
    DETAIL_HTML = """
<html>
<head><title>关于发布《北京证券交易所股票上市规则》的公告 </title></head>
<body>
<span class="date_span">2026-04-24</span>
<div class="text_box" id="fileDownload" file="/uploads/6/file/public/202604/rule.pdf">
  <a href="" class="text_download" download="北京证券交易所股票上市规则.pdf">下载</a>
</div>
<div class="in_main clearfix">
  <p style="text-align:center">北证公告〔2026〕18号</p>
  <p>北京证券交易所股票上市规则</p>
  <p>第一章 总则</p>
  <p>1.1 为了规范股票上市和持续监管事宜，制定本规则。</p>
  <p>1.2 股票在本所的上市和持续监管事宜，适用本规则。</p>
</div>
</body>
</html>
"""

    def test_bse_jsonp_search_rows_are_parsed_and_filtered(self) -> None:
        payload = (
            'null([{"result":true,"data":{"content":[{"infoId":200028220,'
            '"title":"关于发布《北京证券交易所股票上市规则》的公告",'
            '"htmlUrl":"/cxjg_list/200028220.html",'
            '"fileUrl":"/uploads/6/file/public/202604/rule.pdf",'
            '"publishDate":"2026-04-24 21:35:09"}]}}])'
        )
        result = securities_rules.FetchResult(
            url="https://www.bse.cn/info/listseSub.do",
            status_code=200,
            headers={},
            text=payload,
        )
        adapter = bse_cn.SecuritiesRulesAdapter(bse_cn.CONFIG)
        with patch.object(adapter, "_fetch_bse_jsonp", return_value=result):
            found = adapter.search_list("股票上市规则", page_size=5)

        self.assertEqual(found["source"], "bse_cn")
        self.assertEqual(found["rows"][0]["detail_id"], "cxjg_list/200028220.html")
        self.assertEqual(found["rows"][0]["released_at"], "2026-04-24")
        self.assertEqual(found["rows"][0]["status"], "current")

    def test_build_law_payload_parses_inline_bse_rule_text(self) -> None:
        detail = securities_rules.FetchResult(
            url="https://www.bse.cn/cxjg_list/200028220.html",
            status_code=200,
            headers={},
            text=self.DETAIL_HTML,
        )
        adapter = bse_cn.SecuritiesRulesAdapter(bse_cn.CONFIG)
        with (
            patch.object(securities_rules, "_fetch_text", return_value=detail),
            patch.object(
                securities_rules,
                "_fetch_bytes",
                side_effect=ValueError("attachment unavailable in unit test"),
            ),
        ):
            payload = adapter.build_law_payload("cxjg_list/200028220.html")

        self.assertEqual(payload["id"], "bse_cn:cxjg_list/200028220.html")
        self.assertEqual(payload["title"], "北京证券交易所股票上市规则")
        self.assertEqual(payload["issuing_body"], "北京证券交易所")
        self.assertEqual(payload["document_number"], "北证公告〔2026〕18号")
        self.assertEqual(payload["articles"][0]["number"], "1.1")


class SacNetCnAdapterTests(unittest.TestCase):
    def test_specific_search_skips_timed_out_list_page(self) -> None:
        page = securities_rules.FetchResult(
            url="https://www.sac.net.cn/flfg/zlgz/index_1.html",
            status_code=200,
            headers={},
            text="""
            <a href="./202406/t20240612_12345.html">
              证券公司债券业务执业质量评价办法 2024-06-12
            </a>
            """,
        )
        adapter = sac_net_cn.SecuritiesRulesAdapter(sac_net_cn.CONFIG)
        with patch.object(
            securities_rules,
            "_fetch_text",
            side_effect=[TimeoutError("slow page"), page],
        ):
            found = adapter.search_list("证券公司债券业务执业质量评价办法", page_size=1)

        self.assertEqual(found["source"], "sac_net_cn")
        self.assertEqual(found["rows"][0]["detail_id"], "flfg/zlgz/202406/t20240612_12345.html")
        self.assertEqual(found["rows"][0]["released_at"], "2024-06-12")
        self.assertEqual(found["warnings"][0]["code"], "search_page_unavailable")

    def test_precise_search_scans_paginated_roots_when_front_pages_miss(self) -> None:
        config = securities_rules.SiteConfig(
            source="test_rules",
            source_name="example.test",
            base_url="https://example.test",
            homepage_path="/rules/index.html",
            issuing_body="测试主体",
            title_suffixes=(),
            content_markers=(),
            search_pages=("/rules/index.html",),
            paginated_search_roots=("/rules",),
            paginated_search_max_pages=3,
        )
        empty = securities_rules.FetchResult(
            url="https://example.test/rules/index.html",
            status_code=200,
            headers={},
            text="var countPage = 3;",
        )
        hit = securities_rules.FetchResult(
            url="https://example.test/rules/index_1.html",
            status_code=200,
            headers={},
            text="""
            <a href="./202401/rule.html">证券公司投资银行类业务内部控制指引 2024-01-01</a>
            """,
        )
        adapter = securities_rules.SecuritiesRulesAdapter(config)
        with patch.object(securities_rules, "_fetch_text", side_effect=[empty, hit]):
            found = adapter.search_list("证券公司投资银行类业务内部控制指引", page_size=5)

        self.assertEqual(found["total_count"], 1)
        self.assertEqual(found["rows"][0]["detail_id"], "rules/202401/rule.html")


class GenericAttachmentTitleTests(unittest.TestCase):
    def test_direct_pdf_uses_search_row_title_when_file_title_is_generic(self) -> None:
        detail = {
            "detail_id": "zdjs/editor_file/20220620141248275.pdf",
            "url": "https://www.chinaclear.cn/zdjs/editor_file/20220620141248275.pdf",
            "title": "pdf",
            "content_text": "",
            "selected_attachment": {
                "url": "https://www.chinaclear.cn/zdjs/editor_file/20220620141248275.pdf",
                "title": "pdf",
                "bytes": b"%PDF",
                "headers": {},
            },
            "checked_at": "2026-05-17T00:00:00+00:00",
        }
        adapter = bse_cn.SecuritiesRulesAdapter(bse_cn.CONFIG)
        with patch.object(
            securities_rules,
            "_pdf_bytes_to_text",
            return_value="第一条 为了规范结算业务，制定本规则。",
        ):
            payload = adapter.build_law_payload(
                "zdjs/editor_file/20220620141248275.pdf",
                search_row={"title": "中国证券登记结算有限责任公司结算规则"},
                detail=detail,
            )

        self.assertEqual(payload["title"], "中国证券登记结算有限责任公司结算规则")


class SecuritiesSourceRegistryTests(unittest.TestCase):
    def test_new_sources_are_registered_for_fetch_and_verify(self) -> None:
        for name in ("bse_cn", "sse_com_cn", "szse_cn", "chinaclear_cn", "sac_net_cn"):
            self.assertIn(name, sources.ADAPTER_REGISTRY)
            self.assertIn(name, sources.VERIFIABLE_SOURCES)
            self.assertIn(name, fetch.FETCH_SOURCES)

    def test_source_id_inference_for_generic_rule_sources(self) -> None:
        payload = {
            "id": "sse_com_cn:services/listingwithsse/home/policy/supervise/c/c_20250425_10777756.shtml",
            "source_url": "https://www.sse.com.cn/services/listingwithsse/home/policy/supervise/c/c_20250425_10777756.shtml",
            "source_name": "www.sse.com.cn",
        }

        self.assertEqual(infer_source(payload), "sse_com_cn")
        self.assertEqual(
            infer_source_id(payload, "sse_com_cn"),
            "services/listingwithsse/home/policy/supervise/c/c_20250425_10777756.shtml",
        )

        bse_payload = {
            "id": "bse_cn:cxjg_list/200028220.html",
            "source_url": "https://www.bse.cn/cxjg_list/200028220.html",
            "source_name": "www.bse.cn",
        }
        self.assertEqual(infer_source(bse_payload), "bse_cn")
        self.assertEqual(
            infer_source_id(bse_payload, "bse_cn"),
            "cxjg_list/200028220.html",
        )


class SecuritiesProbeTests(unittest.TestCase):
    def test_probe_returns_error_payload_on_timeout(self) -> None:
        adapter = bse_cn.SecuritiesRulesAdapter(bse_cn.CONFIG)
        with patch.object(securities_rules, "_fetch_text", side_effect=TimeoutError("slow")):
            report = adapter.probe()

        self.assertEqual(report["source"], "bse_cn")
        self.assertEqual(report["page_shape"], "error")
        self.assertIn("slow", report["error"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
