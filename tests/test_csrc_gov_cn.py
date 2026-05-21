"""Tests for the ``csrc_gov_cn`` adapter."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from chinalaw import fetch, sources
from chinalaw.adapters import csrc_gov_cn
from chinalaw.document_numbers import infer_source, infer_source_id


class CsrcGovCnAdapterTests(unittest.TestCase):
    SEARCH_FIXTURE = """
<html><body>
<div class="wordGuide Residence-permit" id="0">
  <div class="bigTit clearfix">
    <span class="fl columnLabel styleColor">证监会要闻</span>
    <a href="//www.csrc.gov.cn/csrc/c100028/c1002922/content.shtml"
       title="上市公司信息披露管理办法">上市公司信息披露管理办法</a>
  </div>
  <p class="summaryFont">中国证券监督管理委员会令 第40号 第一条 示例。</p>
  <p class="time">
    <a class="sourceDateFont permitU" href="//www.csrc.gov.cn/csrc/c100028/c1002922/content.shtml">中国证券监督管理委员会</a>
    <span class="sourceDateFont">2007-02-01</span>
  </p>
  <ul>
    <li class="clearfix">
      <span class="fl columnLabel styleColor">规章 </span>
      <a href="http://www.csrc.gov.cn/csrc/c106256/c1653948/content.shtml"
         target="_blank" class="fl" data="规章">上市公司信息披露管理办法</a>
      <span class="fr">2021-12-16</span>
    </li>
  </ul>
</div>
</body></html>
"""

    DETAIL_FIXTURE = """
<html>
<head>
<meta name="ArticleTitle" content="上市公司信息披露管理办法"/>
<meta name="PubDate" content="2025-10-25 11:32:34"/>
<meta name="ContentSource" content="中国证监会"/>
<title>上市公司信息披露管理办法_中国证券监督管理委员会</title>
</head>
<body>
<div class="content-body">
  <h3>上市公司信息披露管理办法</h3>
  <p class="sub-title">(2007年1月30日证监会令第40号公布 2021年3月18日证监会令第182号修订)</p>
  <p><span>第一章 总则</span></p>
  <p><span>第一条</span><span>为了规范上市公司及其他信息披露义务人的信息披露行为，制定本办法。</span></p>
  <p><span>第二条</span><span>信息披露义务人履行信息披露义务应当遵守本办法的规定。</span></p>
  <p><span>第六十五条</span><span>本办法自2021年5月1日起施行。</span></p>
</div>
<div class="fg-foot"><span>中国证券监督管理委员会发布</span></div>
</body>
</html>
"""

    PDF_DETAIL_FIXTURE = """
<html>
<head>
<meta name="ArticleTitle" content="【第226号令】《上市公司信息披露管理办法》"/>
<meta name="PubDate" content="2025-03-28 17:46:39"/>
<meta name="ColumnName" content="证监会令"/>
<title>【第226号令】《上市公司信息披露管理办法》_中国证券监督管理委员会</title>
</head>
<body>
<div class="detail-news">
  <p>中国证券监督管理委员会令</p>
  <p>第226号</p>
  <p>《上市公司信息披露管理办法》已经2025年2月28日中国证券监督管理委员会2025年第2次委务会议审议通过，现予公布，自2025年7月1日起施行。</p>
  <p>2025年3月26日</p>
  <div id="files">
    <a href="7547359/files/附件1：上市公司信息披露管理办法.pdf">附件1：上市公司信息披露管理办法.pdf</a>
    <a href="7547359/files/附件2：《上市公司信息披露管理办法》修订说明.pdf">附件2：《上市公司信息披露管理办法》修订说明.pdf</a>
  </div>
</div>
</body>
</html>
"""

    def _search_result(self) -> csrc_gov_cn.FetchResult:
        return csrc_gov_cn.FetchResult(
            url="https://www.csrc.gov.cn/guestweb4/s",
            status_code=200,
            headers={},
            text=self.SEARCH_FIXTURE,
        )

    def _detail_result(self) -> csrc_gov_cn.FetchResult:
        return csrc_gov_cn.FetchResult(
            url="https://www.csrc.gov.cn/csrc/c106256/c1653948/content.shtml",
            status_code=200,
            headers={"Last-Modified": "Sat, 25 Oct 2025 00:00:00 GMT"},
            text=self.DETAIL_FIXTURE,
        )

    def _pdf_detail_result(self) -> csrc_gov_cn.FetchResult:
        return csrc_gov_cn.FetchResult(
            url="https://www.csrc.gov.cn/csrc/c101953/c7547359/content.shtml",
            status_code=200,
            headers={},
            text=self.PDF_DETAIL_FIXTURE,
        )

    def _pdf_binary_result(self) -> csrc_gov_cn.FetchResult:
        return csrc_gov_cn.FetchResult(
            url=(
                "https://www.csrc.gov.cn/csrc/c101953/c7547359/7547359/files/"
                "附件1：上市公司信息披露管理办法.pdf"
            ),
            status_code=200,
            headers={"Content-Type": "application/pdf"},
            text=b"%PDF placeholder".decode("latin1"),
        )

    def test_request_uses_tool_user_agent(self) -> None:
        req = csrc_gov_cn._build_request("https://www.csrc.gov.cn/")
        user_agent = req.headers["User-agent"]
        self.assertIn("chinalaw-cli", user_agent)
        self.assertIn("github.com", user_agent)

    def test_normalize_detail_id_accepts_url_and_path(self) -> None:
        self.assertEqual(
            csrc_gov_cn._normalize_detail_id(
                "https://www.csrc.gov.cn/csrc/c106256/c1653948/content.shtml"
            ),
            "csrc/c106256/c1653948",
        )
        self.assertEqual(
            csrc_gov_cn._normalize_detail_id("/csrc/c106256/c1653948/content.shtml"),
            "csrc/c106256/c1653948",
        )
        self.assertIsNone(csrc_gov_cn._normalize_detail_id("not-a-detail-url"))

    def test_search_list_parses_main_and_similar_rows(self) -> None:
        adapter = csrc_gov_cn.CsrcGovCnAdapter()
        with patch.object(csrc_gov_cn, "_fetch_text", return_value=self._search_result()):
            result = adapter.search_list("上市公司信息披露管理办法", page_size=10)

        self.assertEqual(result["source"], "csrc_gov_cn")
        self.assertEqual(len(result["rows"]), 2)
        first = result["rows"][0]
        self.assertEqual(first["detail_id"], "csrc/c106256/c1653948")
        self.assertEqual(first["title"], "上市公司信息披露管理办法")
        self.assertEqual(first["column"], "规章")
        self.assertEqual(first["released_at"], "2021-12-16")
        self.assertEqual(first["status"], "current")

    def test_search_list_prefers_latest_csrc_order_over_old_rule_archive(self) -> None:
        html = """
<html><body>
<div>
  <span class="fl columnLabel styleColor">规章</span>
  <a href="http://www.csrc.gov.cn/csrc/c106256/c1653948/content.shtml">上市公司信息披露管理办法</a>
  <span class="fr">2021-12-16</span>
</div>
<div>
  <span class="fl columnLabel styleColor">证监会令</span>
  <a href="http://www.csrc.gov.cn/csrc/c101953/c7547359/content.shtml">上市公司信息披露管理办法</a>
  <span class="fr">2025-03-28</span>
</div>
</body></html>
"""
        rows = csrc_gov_cn._parse_search_rows(
            html,
            base_url="https://www.csrc.gov.cn",
            page_size=10,
        )

        self.assertEqual(rows[0]["detail_id"], "csrc/c101953/c7547359")
        self.assertEqual(rows[0]["column"], "证监会令")

    def test_build_law_payload_extracts_csrc_rule_articles(self) -> None:
        adapter = csrc_gov_cn.CsrcGovCnAdapter()
        with patch.object(csrc_gov_cn, "_fetch_text", return_value=self._detail_result()):
            payload = adapter.build_law_payload("csrc/c106256/c1653948")

        self.assertEqual(payload["id"], "csrc_gov_cn:csrc/c106256/c1653948")
        self.assertEqual(payload["title"], "上市公司信息披露管理办法")
        self.assertEqual(payload["level"], "departmental_rule")
        self.assertEqual(payload["status"], "current")
        self.assertEqual(payload["issuing_body"], "中国证券监督管理委员会")
        self.assertEqual(payload["source_name"], "www.csrc.gov.cn")
        self.assertEqual(payload["document_number"], "证监会令第182号")
        self.assertEqual(payload["released_at"], "2021-03-18")
        self.assertEqual(payload["effective_at"], "2021-05-01")
        self.assertEqual(len(payload["articles"]), 3)
        self.assertEqual(payload["articles"][0]["number_display"], "第一条")

    def test_build_law_payload_uses_matching_pdf_attachment_when_html_is_order_summary(self) -> None:
        adapter = csrc_gov_cn.CsrcGovCnAdapter()
        pdf_text = "\n".join(
            [
                "上市公司信息披露管理办法",
                "第一条 为了规范上市公司及其他信息披露义务人的信息披露行为，制定本办法。",
                "第二条 信息披露义务人履行信息披露义务应当遵守本办法的规定。",
                "第六十五条 本办法自2025年7月1日起施行。",
            ]
        )
        with (
            patch.object(csrc_gov_cn, "_fetch_text", return_value=self._pdf_detail_result()),
            patch.object(csrc_gov_cn, "_fetch_bytes", return_value=self._pdf_binary_result()) as fetch_bytes,
            patch.object(csrc_gov_cn, "_pdf_bytes_to_text", return_value=pdf_text) as pdf_to_text,
        ):
            payload = adapter.build_law_payload("csrc/c101953/c7547359")

        self.assertEqual(payload["id"], "csrc_gov_cn:csrc/c101953/c7547359")
        self.assertEqual(payload["title"], "上市公司信息披露管理办法")
        self.assertEqual(payload["document_number"], "证监会令第226号")
        self.assertEqual(payload["released_at"], "2025-03-26")
        self.assertEqual(payload["effective_at"], "2025-07-01")
        self.assertEqual(len(payload["articles"]), 3)
        self.assertEqual(payload["articles"][0]["number_display"], "第一条")
        self.assertIn("/7547359/files/", fetch_bytes.call_args.args[0])
        self.assertNotIn("修订说明", fetch_bytes.call_args.args[0])
        pdf_to_text.assert_called_once()

    def test_pdf_text_cleanup_removes_page_numbers_and_preserves_article_heads(self) -> None:
        raw = "\n".join(
            [
                "  第一条   为了规范上市公司及其他信息",
                "披露行为，制定本办法。",
                "1",
                "\f  第二条 信息披露义务人应当遵守本办法。",
                "第六十七条 本办法自 2025 年 7 月 1 日起施行。",
            ]
        )

        text = csrc_gov_cn._clean_pdf_text(raw)

        self.assertIn("第一条 为了规范上市公司及其他信息披露行为", text)
        self.assertIn("\n第二条", text)
        self.assertNotIn("\n1\n", f"\n{text}\n")
        self.assertEqual(csrc_gov_cn._infer_effective_at(text), "2025-07-01")

    def test_pdf_text_cleanup_does_not_promote_cross_reference_to_article_heading(self) -> None:
        raw = "\n".join(
            [
                "第七十四条 相关人员可以采取本办法",
                "第六十九条规定的相关监管措施；情节严重的，处以警告、罚款。",
                "第七十五条 后续条文。",
            ]
        )

        text = csrc_gov_cn._clean_pdf_text(raw)

        self.assertIn("本办法第六十九条规定的相关监管措施", text)
        self.assertNotIn("\n第六十九条规定", text)
        self.assertIn("\n第七十五条", text)

    def test_pdf_text_cleanup_keeps_article_range_references_inline(self) -> None:
        raw = "\n".join(
            [
                "第二条 大股东减持股份，仅适用本办法",
                "第四条至第八条、第十条、第十一条、第二十八条至",
                "第三十条的规定。",
                "第三条 后续条文。",
            ]
        )

        text = csrc_gov_cn._clean_pdf_text(raw)

        self.assertIn(
            "适用本办法第四条至第八条、第十条、第十一条、第二十八条至第三十条的规定。",
            text,
        )
        self.assertNotIn("\n第四条至", text)
        self.assertNotIn("\n第三十条的规定", text)
        self.assertIn("\n第三条 后续条文。", text)

    def test_source_registry_fetch_and_source_id_include_csrc(self) -> None:
        self.assertIn("csrc_gov_cn", sources.ADAPTER_REGISTRY)
        self.assertIn("csrc_gov_cn", sources.VERIFIABLE_SOURCES)
        self.assertIn("csrc_gov_cn", fetch.FETCH_SOURCES)
        payload = {
            "id": "csrc_gov_cn:csrc/c106256/c1653948",
            "source_url": "https://www.csrc.gov.cn/csrc/c106256/c1653948/content.shtml",
            "source_name": "www.csrc.gov.cn",
        }
        self.assertEqual(infer_source(payload), "csrc_gov_cn")
        self.assertEqual(infer_source_id(payload, "csrc_gov_cn"), "csrc/c106256/c1653948")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
