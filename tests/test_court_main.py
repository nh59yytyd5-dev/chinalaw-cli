"""Tests for the ``court_main`` adapter.

The adapter targets ``www.court.gov.cn`` (the Supreme People's Court main
site), not ``gongbao.court.gov.cn``. Fixtures here are intentionally small but
preserve the site's key shapes: ``/search.html`` rows, ``/xiangqing/`` detail
URLs, ``.txt_txt`` content, source/date metadata, and press-release titles that
embed a normative document title in book-title marks.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from chinalaw import fetch as fetch_mod
from chinalaw import service, sources
from chinalaw.adapters import court_main
from chinalaw.db import connect, migrate
from chinalaw.document_numbers import infer_source, infer_source_id
from chinalaw.loader import load_law_from_dict


class CourtMainAdapterTests(unittest.TestCase):
    SEARCH_FIXTURE = """
<html><body>
<div class="results">为您找到相关结果约2个</div>
<ul>
  <li>
    <a href="/zixun/xiangqing/499051.html" target="_blank">
      最高法发布《最高人民法院关于审理示例案件适用法律若干问题的解释（二）》
    </a>
    <i class="date">2026-05-06</i>
  </li>
  <li>
    <a href="/fabu/xiangqing/499421.html" title="两高发布《关于办理示例刑事案件适用法律若干问题的解释》">
      两高发布《关于办理示例刑事案件适用法律若干问题的解释》
    </a>
    <i class="date">2026-04-18 10:00</i>
  </li>
</ul>
<a href="/zixun/gengduo/16_3.html">3</a>
</body></html>
"""

    DETAIL_FIXTURE = """
<html>
<head>
  <title>最高法发布《最高人民法院关于审理示例案件适用法律若干问题的解释（二）》 - 中华人民共和国最高人民法院</title>
</head>
<body>
<div class="title">最高法发布《最高人民法院关于审理示例案件适用法律若干问题的解释（二）》</div>
<ul class="info">
  <li>来源：最高人民法院</li>
  <li>发布时间：2026-05-06</li>
</ul>
<div class="txt_txt" id="zoom">
  <p>最高人民法院介绍了相关典型案例，但本页正文载明司法解释全文。</p>
  <p>《最高人民法院关于审理示例案件适用法律若干问题的解释（二）》已公布。</p>
  <p>法释〔2026〕5号</p>
  <p>本解释自2026年6月1日起施行。</p>
  <p><strong>第一条</strong> 示例正文一。</p>
  <p><strong>第二条</strong> 示例正文二。</p>
  <p><strong>　相关链接：</strong> 这段不应进入 canonical text。</p>
</div>
<div class="txt_etr">分享区</div>
</body>
</html>
"""

    REPLY_DETAIL_FIXTURE = """
<html>
<head>
  <title>最高人民法院关于《中华人民共和国公司法》第八十八条第一款不溯及适用的批复 - 中华人民共和国最高人民法院</title>
</head>
<body>
<div class="title">最高人民法院关于《中华人民共和国公司法》第八十八条第一款不溯及适用的批复</div>
<ul class="info">
  <li>来源：最高人民法院</li>
  <li>发布时间：2024-06-29</li>
</ul>
<div class="txt_txt" id="zoom">
  <p>最高人民法院</p>
  <p>关于《中华人民共和国公司法》</p>
  <p>第八十八条第一款</p>
  <p>不溯及适用的批复</p>
  <p>法释〔2024〕7号</p>
  <p>你院请示收悉。经研究，批复如下：</p>
  <p>《中华人民共和国公司法》第八十八条第一款的规定，不溯及适用于本法施行前已经转让股权的情形。</p>
</div>
<div class="txt_etr">分享区</div>
</body>
</html>
"""

    POLICY_ITEM_DETAIL_FIXTURE = """
<html>
<head>
  <title>最高人民法院关于依法妥善审理涉新冠肺炎疫情民事案件若干问题的指导意见（二） - 中华人民共和国最高人民法院</title>
</head>
<body>
<div class="title">最高人民法院关于依法妥善审理涉新冠肺炎疫情民事案件若干问题的指导意见（二）</div>
<ul class="info">
  <li>来源：最高人民法院</li>
  <li>发布时间：2020-05-19</li>
</ul>
<div class="txt_txt" id="zoom">
  <p>法发〔2020〕17号</p>
  <p>最高人民法院关于依法妥善审理涉新冠肺炎疫情民事案件若干问题的指导意见（二）</p>
  <p>一、关于合同案件的审理</p>
  <p>1.疫情或者疫情防控措施导致买卖合同履行成本增加，继续履行不影响合同目的实现，当事人请求解除合同的，人民法院不予支持。</p>
  <p>2.买卖合同能够继续履行，但履约成本显著增加的，人民法院应当结合案件实际情况调整价款。</p>
  <p>二、关于破产案件的审理</p>
  <p>18.人民法院在审查企业是否符合破产受理条件时，要注意审查企业陷入困境是否因疫情或者疫情防控措施所致。</p>
</div>
<div class="txt_etr">分享区</div>
</body>
</html>
"""

    AMENDMENT_DECISION_DETAIL_FIXTURE = """
<html>
<head>
  <title>“两高”发布《关于修改〈最高人民法院、最高人民检察院关于办理示例刑事案件适用法律若干问题的解释〉的决定》 - 中华人民共和国最高人民法院</title>
</head>
<body>
<div class="title">“两高”发布《关于修改〈最高人民法院、最高人民检察院关于办理示例刑事案件适用法律若干问题的解释〉的决定》</div>
<ul class="info">
  <li>来源：最高人民法院新闻局</li>
  <li>发布时间：2026-07-24 15:03:50</li>
</ul>
<div class="txt_txt" id="zoom">
  <p>依法惩治示例犯罪 维护投资者合法权益</p>
  <p>2026年7月24日，最高人民法院、最高人民检察院联合发布《修改决定》，自2026年7月27日起施行。</p>
  <p>《最高人民法院、最高人民检察院关于修改〈最高人民法院、最高人民检察院关于办理示例刑事案件适用法律若干问题的解释〉的决定》已于2025年12月13日通过，现予公布，自2026年7月27日起施行。</p>
  <p>最高人民法院 最高人民检察院</p>
  <p>2026年7月24日</p>
  <p>法释〔2026〕13号</p>
  <p>关于修改《最高人民法院、最高人民检察院关于办理示例刑事案件适用法律若干问题的解释》的决定</p>
  <p>根据司法实践情况，现决定对《最高人民法院、最高人民检察院关于办理示例刑事案件适用法律若干问题的解释》（法释〔2012〕6号）作如下修改：</p>
  <p>一、将第六条修改为：“在敏感期内从事示例交易，具有下列情形之一的，应当认定为情节严重：</p>
  <p>“（一）成交额在二百万元以上的；</p>
  <p>“（二）具有其他严重情节的。”</p>
  <p>二、将第四条调整至第九条之后，相应调整相关条文序号。</p>
  <p>本决定自2026年7月27日起施行。</p>
  <p>根据本决定，《最高人民法院、最高人民检察院关于办理示例刑事案件适用法律若干问题的解释》作相应修改后，重新公布。</p>
  <p>最高人民法院 最高人民检察院</p>
  <p>关于办理示例刑事案件适用法律若干问题的解释</p>
  <p>第一条 下列人员应当认定为示例知情人员。</p>
  <p>第六条 本解释所称“敏感期”是指信息自形成至公开的期间。</p>
</div>
<div class="txt_etr">分享区</div>
</body>
</html>
"""

    BATCH_AMENDMENT_DECISION_DETAIL_FIXTURE = """
<html>
<head>
  <title>最高人民法院关于修改《示例甲规定》等二件执行类司法解释的决定 - 中华人民共和国最高人民法院</title>
</head>
<body>
<div class="title">最高人民法院关于修改 《示例甲 规定》等二件执行类司法解释的决定</div>
<ul class="info">
  <li>发布时间：2020-12-31 10:00:00</li>
</ul>
<div class="txt_txt" id="zoom">
  <p>《最高人民法院关于修改〈示例甲规定〉等二件执行类司法解释的决定》已于2020年12月23日由最高人民法院审判委员会第1823次会议通过，现予公布，自2021年1月1日起施行。</p>
  <p>最高人民法院</p>
  <p>2020年12月29日</p>
  <p>法释〔2020〕21号</p>
  <p>根据审判、执行实践需要，对《示例甲规定》等二件司法解释作如下修改：</p>
  <p>一、修改《示例甲规定》</p>
  <p>将第三条修改为：“甲新第三条。”</p>
  <p>二、修改《示例乙规定》</p>
  <p>将第四条修改为：“乙新第四条。”</p>
  <p>本决定自2021年1月1日起施行。</p>
  <p>根据本决定，《示例甲规定》等二件司法解释作相应修改后重新公布。</p>
  <p>二件执行类司法解释修改后全文</p>
  <p>示例甲规定</p>
  <p>第一条 甲第一条。</p>
  <p>示例乙规定</p>
  <p>第一条 乙第一条。</p>
</div>
</body>
</html>
"""

    UNANNOUNCED_REPRINT_DETAIL_FIXTURE = """
<html>
<head>
  <title>最高人民法院关于修改《关于审理示例借贷案件适用法律若干问题的规定》的决定 - 中华人民共和国最高人民法院</title>
</head>
<body>
<div class="title">最高人民法院关于修改《关于审理示例借贷案件适用法律若干问题的规定》的决定</div>
<ul class="info">
  <li>发布时间：2020-08-20 10:00:00</li>
</ul>
<div class="txt_txt" id="zoom">
  <p>《最高人民法院关于修改〈关于审理示例借贷案件适用法律若干问题的规定〉的决定》已于2020年8月18日由最高人民法院审判委员会第1809次会议通过，现予公布，自2020年8月20日起施行。</p>
  <p>最高人民法院</p>
  <p>2020年8月19日</p>
  <p>法释〔2020〕6号</p>
  <p>最高人民法院</p>
  <p>关于修改《关于审理示例借贷案件</p>
  <p>适用法律若干问题的规定》的决定</p>
  <p>（2020年8月18日最高人民法院审判委员会</p>
  <p>第1809次会议通过，自2020年8月20日起施行）</p>
  <p>根据审判实践需要，对《关于审理示例借贷案件适用法律若干问题的规定》作如下修改：</p>
  <p>一、将第一条修改为：</p>
  <p>“本规定所称的示例借贷，是指示例行为。”</p>
  <p>（一）示例项。</p>
  <p>本决定自2020年8月20日起施行。</p>
  <p>最高人民法院</p>
  <p>关于审理示例借贷案件适用法律若干问题的规定</p>
  <p>（2015年6月23日最高人民法院审判委员会第1655次会议通过，</p>
  <p>根据2020年8月18日最高人民法院审判委员会第1809次会议《关于修改〈关于审理示例借贷案件适用法律若干问题的规定〉的决定》修正，该修正自2020年8月20日起施行）</p>
  <p>为正确审理示例借贷纠纷案件，制定本规定。</p>
  <p>第一条 本规定所称的示例借贷，是指示例行为。</p>
  <p>第二条 示例第二条。</p>
</div>
</body>
</html>
"""

    def _search_result(self) -> court_main.FetchResult:
        return court_main.FetchResult(
            url="https://www.court.gov.cn/search.html?content=%E7%A4%BA%E4%BE%8B",
            status_code=200,
            headers={},
            text=self.SEARCH_FIXTURE,
        )

    def _detail_result(self, url: str = "https://www.court.gov.cn/zixun/xiangqing/499051.html") -> court_main.FetchResult:
        return court_main.FetchResult(
            url=url,
            status_code=200,
            headers={"Last-Modified": "Wed, 06 May 2026 00:00:00 GMT"},
            text=self.DETAIL_FIXTURE,
        )

    def _reply_detail_result(self) -> court_main.FetchResult:
        return court_main.FetchResult(
            url="https://www.court.gov.cn/fabu/xiangqing/499421.html",
            status_code=200,
            headers={},
            text=self.REPLY_DETAIL_FIXTURE,
        )

    def _policy_item_detail_result(self) -> court_main.FetchResult:
        return court_main.FetchResult(
            url="https://www.court.gov.cn/fabu/xiangqing/230181.html",
            status_code=200,
            headers={},
            text=self.POLICY_ITEM_DETAIL_FIXTURE,
        )

    def test_request_uses_tool_user_agent(self) -> None:
        req = court_main._build_request("https://www.court.gov.cn/")
        user_agent = req.headers["User-agent"]
        self.assertIn("chinalaw-cli", user_agent)
        self.assertIn("github.com", user_agent)

    def test_request_interval_clamps_zero_to_floor(self) -> None:
        adapter = court_main.CourtMainAdapter(request_interval=0)
        sleeps: list[float] = []
        with patch.object(court_main.time, "sleep", side_effect=sleeps.append), patch.object(
            court_main.time,
            "monotonic",
            side_effect=[0.0, 0.0, 0.0],
        ):
            adapter._throttle()
        self.assertGreaterEqual(sleeps[0], court_main.MIN_REQUEST_INTERVAL)

    def test_normalize_detail_id_accepts_url_path_and_numeric_id(self) -> None:
        self.assertEqual(
            court_main._normalize_detail_id(
                "https://www.court.gov.cn/fabu/xiangqing/499421.html?x=1"
            ),
            "fabu/xiangqing/499421",
        )
        self.assertEqual(
            court_main._normalize_detail_id("/zixun/xiangqing/499051.html"),
            "zixun/xiangqing/499051",
        )
        self.assertEqual(
            court_main._normalize_detail_id("499051"),
            "zixun/xiangqing/499051",
        )
        self.assertIsNone(court_main._normalize_detail_id("not-a-detail-url"))

    def test_search_list_parses_rows_totals_and_dates(self) -> None:
        adapter = court_main.CourtMainAdapter()
        with patch.object(court_main, "_fetch_text", return_value=self._search_result()):
            result = adapter.search_list("示例", page_size=10)

        self.assertEqual(result["source"], "court_main")
        self.assertEqual(result["query"], "示例")
        self.assertEqual(result["total_count"], 2)
        self.assertEqual(result["total_pages"], 3)
        self.assertEqual(len(result["rows"]), 2)
        first = result["rows"][0]
        self.assertEqual(first["detail_id"], "zixun/xiangqing/499051")
        self.assertEqual(first["released_at"], "2026-05-06")
        self.assertEqual(first["status"], "unknown")
        self.assertEqual(
            first["url"],
            "https://www.court.gov.cn/zixun/xiangqing/499051.html",
        )

    def test_fetch_detail_extracts_metadata_and_strips_related_links(self) -> None:
        adapter = court_main.CourtMainAdapter()
        with patch.object(court_main, "_fetch_text", return_value=self._detail_result()):
            detail = adapter.fetch_detail("zixun/xiangqing/499051")

        self.assertEqual(detail["detail_id"], "zixun/xiangqing/499051")
        self.assertEqual(detail["source_name_text"], "最高人民法院")
        self.assertEqual(detail["published_at"], "2026-05-06")
        self.assertIn("第一条", detail["content_html"])
        self.assertNotIn("相关链接", detail["content_html"])
        self.assertEqual(
            detail["source_last_modified"],
            "Wed, 06 May 2026 00:00:00 GMT",
        )

    def test_fetch_detail_numeric_id_falls_back_across_channels(self) -> None:
        adapter = court_main.CourtMainAdapter()
        not_found = court_main.FetchResult(
            url="https://www.court.gov.cn/zixun/xiangqing/499051.html",
            status_code=200,
            headers={},
            text="<html><body>抱歉，找不到您要的页面</body></html>",
        )
        success = self._detail_result(
            "https://www.court.gov.cn/fabu/xiangqing/499051.html"
        )
        with patch.object(
            court_main,
            "_fetch_text",
            side_effect=[not_found, success],
        ):
            detail = adapter.fetch_detail("499051")

        self.assertEqual(detail["detail_id"], "fabu/xiangqing/499051")
        self.assertEqual(detail["url"], success.url)

    def test_build_law_payload_uses_embedded_normative_title(self) -> None:
        adapter = court_main.CourtMainAdapter()
        with patch.object(court_main, "_fetch_text", return_value=self._detail_result()):
            payload = adapter.build_law_payload("zixun/xiangqing/499051")

        self.assertEqual(payload["id"], "court_main:zixun/xiangqing/499051")
        self.assertEqual(
            payload["title"],
            "最高人民法院关于审理示例案件适用法律若干问题的解释（二）",
        )
        # The intro mentions "典型案例"; title-level signals must still win.
        self.assertEqual(payload["level"], "judicial_interpretation")
        self.assertEqual(payload["source_name"], "www.court.gov.cn")
        self.assertEqual(payload["issuing_body"], "最高人民法院")
        self.assertEqual(payload["document_number"], "法释〔2026〕5号")
        self.assertEqual(payload["released_at"], "2026-05-06")
        self.assertEqual(payload["effective_at"], "2026-06-01")
        self.assertEqual(len(payload["articles"]), 2)
        self.assertEqual(payload["articles"][0]["number_display"], "第一条")
        joined = "\n".join(article["text"] for article in payload["articles"])
        self.assertNotIn("相关链接", joined)

    def test_build_law_payload_does_not_fabricate_article_from_reply_title(self) -> None:
        adapter = court_main.CourtMainAdapter()
        with patch.object(court_main, "_fetch_text", return_value=self._reply_detail_result()):
            payload = adapter.build_law_payload("fabu/xiangqing/499421")

        self.assertEqual(
            payload["title"],
            "最高人民法院关于《中华人民共和国公司法》第八十八条第一款不溯及适用的批复",
        )
        self.assertEqual(payload["level"], "judicial_interpretation")
        self.assertEqual(payload["document_number"], "法释〔2024〕7号")
        self.assertEqual(len(payload["articles"]), 1)
        self.assertEqual(payload["articles"][0]["number"], "正文")
        self.assertEqual(payload["articles"][0]["number_display"], "正文")
        self.assertIn("不溯及适用的批复", payload["articles"][0]["text"])

    def test_build_law_payload_splits_policy_guidance_numbered_items(self) -> None:
        adapter = court_main.CourtMainAdapter()
        with patch.object(
            court_main,
            "_fetch_text",
            return_value=self._policy_item_detail_result(),
        ):
            payload = adapter.build_law_payload("fabu/xiangqing/230181")

        self.assertEqual(payload["level"], "judicial_policy")
        self.assertEqual(payload["document_number"], "法发〔2020〕17号")
        self.assertEqual([item["number"] for item in payload["articles"]], ["1", "2", "18"])
        self.assertEqual(payload["articles"][2]["number_display"], "第18项")
        self.assertEqual(payload["articles"][2]["part"], "二、关于破产案件的审理")
        self.assertIn("破产受理条件", payload["articles"][2]["text"])

    def test_amendment_decision_splits_off_the_republished_document(self) -> None:
        adapter = court_main.CourtMainAdapter()
        page = court_main.FetchResult(
            url="https://www.court.gov.cn/zixun/xiangqing/506981.html",
            status_code=200,
            headers={},
            text=self.AMENDMENT_DECISION_DETAIL_FIXTURE,
        )
        with patch.object(court_main, "_fetch_text", return_value=page):
            payload = adapter.build_law_payload("zixun/xiangqing/506981")

        # Title, number, dates and body must all belong to the decision itself.
        self.assertEqual(
            payload["title"],
            "最高人民法院、最高人民检察院关于修改〈最高人民法院、最高人民检察院"
            "关于办理示例刑事案件适用法律若干问题的解释〉的决定",
        )
        self.assertEqual(payload["document_number"], "法释〔2026〕13号")
        self.assertEqual(payload["released_at"], "2026-07-24")
        self.assertEqual(payload["effective_at"], "2026-07-27")
        self.assertEqual([item["number"] for item in payload["articles"]], ["正文"])
        body = payload["articles"][0]["text"]
        self.assertTrue(body.startswith("《最高人民法院、最高人民检察院关于修改"))
        self.assertIn("一、将第六条修改为", body)
        self.assertTrue(body.endswith("作相应修改后，重新公布。"))
        self.assertNotIn("依法惩治示例犯罪", body)
        self.assertNotIn("本解释所称“敏感期”", body)
        self.assertNotIn("warnings", payload)

        # #39: the reprinted text is the amended document's 修正本 — its 文号,
        # the decision's dates, and the decision as 修正依据.
        republished = payload["republished"]
        self.assertEqual(republished["id"], "court_main:zixun/xiangqing/506981/republished")
        self.assertEqual(
            republished["title"],
            "最高人民法院、最高人民检察院关于办理示例刑事案件适用法律若干问题的解释",
        )
        self.assertEqual(republished["level"], "judicial_interpretation")
        self.assertEqual(republished["document_number"], "法释〔2012〕6号")
        self.assertEqual(republished["status"], "current")
        self.assertEqual(
            (republished["released_at"], republished["effective_at"]), ("2026-07-24", "2026-07-27")
        )
        self.assertEqual(republished["version_label"], "2026年修正")
        self.assertEqual([item["number"] for item in republished["articles"]], ["1", "6"])
        self.assertEqual(
            republished["relations"],
            [{
                "relation_type": "amendment_basis",
                "to_law_id": "court_main:zixun/xiangqing/506981",
                "to_law_title": payload["title"],
                "effective_at": "2026-07-27",
                "notes": "法释〔2026〕13号",
            }],
        )

    def test_decision_amending_several_documents_keeps_only_the_decision(self) -> None:
        adapter = court_main.CourtMainAdapter()
        page = court_main.FetchResult(
            url="https://www.court.gov.cn/fabu/xiangqing/282661.html",
            status_code=200,
            headers={},
            text=self.BATCH_AMENDMENT_DECISION_DETAIL_FIXTURE,
        )
        with patch.object(court_main, "_fetch_text", return_value=page):
            payload = adapter.build_law_payload("fabu/xiangqing/282661")

        # The page reprints every amended document; none of them is one 修正本.
        self.assertNotIn("republished", payload)
        # #63: the page title wraps; the line breaks leave no spaces behind.
        self.assertEqual(payload["title"], "最高人民法院关于修改《示例甲规定》等二件执行类司法解释的决定")
        self.assertEqual(payload["document_number"], "法释〔2020〕21号")
        self.assertEqual(payload["effective_at"], "2021-01-01")
        self.assertEqual([item["number"] for item in payload["articles"]], ["正文"])
        body = payload["articles"][0]["text"]
        self.assertTrue(body.endswith("作相应修改后重新公布。"))
        self.assertNotIn("甲第一条", body)
        self.assertEqual(
            [(item["code"], item["republished_title"]) for item in payload["warnings"]],
            [("republished_texts_not_imported", "示例甲规定")],
        )

    def test_amended_text_printed_without_the_reprint_sentence_is_split_at_its_heading(self) -> None:
        adapter = court_main.CourtMainAdapter()
        page = court_main.FetchResult(
            url="https://www.court.gov.cn/fabu/xiangqing/249031.html",
            status_code=200,
            headers={},
            text=self.UNANNOUNCED_REPRINT_DETAIL_FIXTURE,
        )
        with patch.object(court_main, "_fetch_text", return_value=page):
            payload = adapter.build_law_payload("fabu/xiangqing/249031")

        # #62: no "……重新公布。" sentence; the amended text's 题注 marks where it starts.
        self.assertEqual(payload["document_number"], "法释〔2020〕6号")
        self.assertEqual(payload["effective_at"], "2020-08-20")
        self.assertEqual([item["number"] for item in payload["articles"]], ["正文"])
        body = payload["articles"][0]["text"]
        self.assertTrue(body.startswith("《最高人民法院关于修改〈关于审理示例借贷案件"))
        self.assertTrue(body.endswith("本决定自2020年8月20日起施行。"))
        self.assertNotIn("示例第二条", body)
        republished = payload["republished"]
        self.assertEqual(
            republished["title"], "最高人民法院关于审理示例借贷案件适用法律若干问题的规定"
        )
        self.assertIsNone(republished["document_number"])
        self.assertEqual(republished["level"], "judicial_interpretation")
        self.assertEqual(
            (republished["released_at"], republished["effective_at"]), ("2020-08-20", "2020-08-20")
        )
        self.assertEqual(republished["version_label"], "2020年修正")
        self.assertEqual([item["number"] for item in republished["articles"]], ["1", "2"])
        self.assertEqual(
            [(item["relation_type"], item["to_law_id"], item["notes"]) for item in republished["relations"]],
            [("amendment_basis", "court_main:fabu/xiangqing/249031", "法释〔2020〕6号")],
        )

    def test_wrapped_titles_lose_their_line_break_spaces(self) -> None:
        for raw, tidy in (
            (
                "最高人民法院关于修改 《最高人民法院关于人民法院扣押铁路运输货物 若干问题的规定》"
                "等十八件执行类司法解释的决定",
                "最高人民法院关于修改《最高人民法院关于人民法院扣押铁路运输货物若干问题的规定》"
                "等十八件执行类司法解释的决定",
            ),
            (
                "最高人民法院 最高人民检察院 关于办理示例刑事案件适用法律若干问题的解释",
                "最高人民法院、最高人民检察院关于办理示例刑事案件适用法律若干问题的解释",
            ),
            ("关于 Example Rules 的通知", "关于Example Rules的通知"),
        ):
            with self.subTest(raw=raw):
                self.assertEqual(court_main._tidy_title(raw), tidy)

    def test_reprint_sentence_naming_several_documents_marks_a_batch(self) -> None:
        title = "关于修改《示例甲规定》《示例乙规定》的决定"
        for sentence, batch in (
            ("根据本决定，《示例甲规定》等二件司法解释作相应修改后重新公布。", True),
            ("根据本决定，《示例甲规定》《示例乙规定》作相应修改后重新公布。", True),
            ("根据本决定，《示例甲规定》作相应修改后重新公布。", False),
        ):
            with self.subTest(sentence=sentence):
                split = court_main._split_amendment_decision(
                    title, f"{title}\n一、删去第三条。\n{sentence}\n示例甲规定\n第一条 示例。"
                )
                self.assertEqual(split.batch, batch)

    def test_amendment_decision_without_appended_text_is_not_split(self) -> None:
        text = "关于修改《示例规定》的决定\n一、删去第三条。\n根据本决定，《示例规定》作相应修改，重新公布。"
        self.assertIsNone(court_main._split_amendment_decision("关于修改《示例规定》的决定", text))
        self.assertIsNone(court_main._split_amendment_decision("示例规定", text + "\n第一条 示例。"))
        # Without that sentence, only a 题注 naming an amending decision starts a reprint.
        unannounced = (
            "关于修改《示例规定》的决定\n（2020年8月18日通过，自2020年8月20日起施行）\n"
            "一、将第三条修改为：\n（一）示例项。\n本决定自2020年8月20日起施行。\n"
            "示例规定\n（2015年6月23日通过）\n第一条 示例。"
        )
        self.assertIsNone(court_main._split_amendment_decision("关于修改《示例规定》的决定", unannounced))

    def test_infer_document_title_preserves_direct_normative_title(self) -> None:
        title = "最高人民法院关于适用《中华人民共和国刑事诉讼法》的解释"

        self.assertEqual(
            court_main._infer_document_title(title, "第一条 示例正文。"),
            title,
        )

    def test_infer_document_title_still_unwraps_press_title(self) -> None:
        self.assertEqual(
            court_main._infer_document_title(
                "最高法发布《最高人民法院关于审理示例案件适用法律若干问题的解释（二）》",
                "正文。",
            ),
            "最高人民法院关于审理示例案件适用法律若干问题的解释（二）",
        )

    def test_source_registry_and_source_id_inference_include_court_main(self) -> None:
        self.assertIn("court_main", sources.ADAPTER_REGISTRY)
        self.assertIn("court_main", sources.VERIFIABLE_SOURCES)
        payload = {
            "id": "court_main:zixun/xiangqing/499051",
            "source_url": "https://www.court.gov.cn/zixun/xiangqing/499051.html",
            "source_name": "www.court.gov.cn",
        }
        self.assertEqual(infer_source(payload), "court_main")
        self.assertEqual(infer_source_id(payload, "court_main"), "zixun/xiangqing/499051")


class CourtMainRepublishedFetchTests(unittest.TestCase):
    """#39: fetching a decision page stores its reprinted text as a 修正本."""

    TITLE = "最高人民法院、最高人民检察院关于办理示例刑事案件适用法律若干问题的解释"
    DECISION_ID = "court_main:zixun/xiangqing/506981"

    def _fetch(self, db_path, **kwargs) -> dict:
        page = court_main.FetchResult(
            url="https://www.court.gov.cn/zixun/xiangqing/506981.html",
            status_code=200,
            headers={},
            text=CourtMainAdapterTests.AMENDMENT_DECISION_DETAIL_FIXTURE,
        )
        with patch.object(court_main, "_fetch_text", return_value=page), patch(
            "chinalaw.fetch.get_source_adapter", return_value=court_main.CourtMainAdapter()
        ):
            return fetch_mod.fetch_law(
                db_path,
                "关于修改示例解释的决定",
                source="court_main",
                prefer_bbbs="zixun/xiangqing/506981",
                **kwargs,
            )

    def _seed_version(self, db_path, **fields) -> None:
        """Store a version of the amended document; the 2012 original unless overridden."""
        with connect(db_path) as conn:
            migrate(conn)
            load_law_from_dict(conn, {
                "id": "flk-example-2012",
                "title": self.TITLE,
                "level": "judicial_interpretation",
                "status": "current",
                "issuing_body": "最高人民法院;最高人民检察院",
                "document_number": "法释〔2012〕6号",
                "released_at": "2012-03-29",
                "effective_at": "2012-06-01",
                "source_url": "https://flk.npc.gov.cn/detail?id=example",
                "source_name": "flk.npc.gov.cn",
                "source_checked_at": "2026-10-01T00:00:00+08:00",
                "source_hash": "example-2012",
                "articles": [{"number": "1", "number_display": "第一条", "text": "原第一条。"}],
                **fields,
            })

    def test_republished_text_joins_the_amended_documents_work(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "t.db"
            self._seed_version(db_path)

            result = self._fetch(db_path)

            republished = result["republished"]
            self.assertNotIn("republished", result["law"])
            self.assertEqual(republished["id"], f"{self.DECISION_ID}/republished")
            self.assertEqual(republished["aligned_with"], "flk-example-2012")
            self.assertTrue(republished["loaded"])
            self.assertEqual(republished["article_count"], 2)

            # The 文号 names the document as amended: the 修正本 in force today.
            current = service.get_law(db_path, "法释〔2012〕6号")
            self.assertEqual(current["id"], f"{self.DECISION_ID}/republished")
            self.assertEqual(current["issuing_body"], "最高人民法院;最高人民检察院")
            self.assertEqual(current["effective_status_as_of"], "current")
            self.assertEqual(
                current["amendment_basis"],
                [{
                    "law_id": self.DECISION_ID,
                    "title": result["law"]["title"],
                    "document_number": "法释〔2026〕13号",
                    "effective_at": "2026-07-27",
                }],
            )
            original = service.get_law(db_path, "flk-example-2012")
            self.assertEqual(original["effective_status_as_of"], "amended")
            self.assertNotIn("amendment_basis", original)
            self.assertEqual(
                [version["id"] for version in original["work_versions"]],
                ["flk-example-2012", f"{self.DECISION_ID}/republished"],
            )
            with connect(db_path) as conn:
                sources_indexed = {
                    row["source"] for row in conn.execute(
                        "SELECT source FROM document_number_index WHERE document_number = ?",
                        ("法释〔2012〕6号",),
                    )
                }
            self.assertNotIn("court_main", sources_indexed)

    def test_republished_text_already_in_the_work_is_not_stored_again(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "t.db"
            self._seed_version(db_path)
            # flk keeps the amended text under its own id, without the 文号.
            self._seed_version(
                db_path,
                id="flk-example-2026",
                document_number=None,
                released_at="2026-07-24",
                effective_at="2026-07-27",
                source_hash="example-2026",
            )

            result = self._fetch(db_path)

            self.assertTrue(result["loaded"])
            republished = result["republished"]
            self.assertEqual(republished["aligned_with"], "flk-example-2012")
            self.assertEqual(republished["existing_version"], "flk-example-2026")
            self.assertFalse(republished["loaded"])
            self.assertIsNone(republished["wrote_fixture"])
            original = service.get_law(db_path, "flk-example-2012")
            self.assertEqual(
                [version["id"] for version in original["work_versions"]],
                ["flk-example-2012", "flk-example-2026"],
            )

    def test_republished_text_is_written_beside_the_decision_fixture(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            fixture = Path(td) / "decision.json"

            result = self._fetch(Path(td) / "absent.db", to_fixture=fixture)

            stored = json.loads((Path(td) / "decision.republished.json").read_text(encoding="utf-8"))
            self.assertEqual(result["republished"]["wrote_fixture"], str(Path(td) / "decision.republished.json"))
            self.assertIsNone(result["republished"]["aligned_with"])
            self.assertEqual(stored["document_number"], "法释〔2012〕6号")
            self.assertEqual(stored["relations"][0]["to_law_id"], json.loads(fixture.read_text(encoding="utf-8"))["id"])
            self.assertNotIn("republished", json.loads(fixture.read_text(encoding="utf-8")))
            self.assertFalse((Path(td) / "absent.db").exists())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
