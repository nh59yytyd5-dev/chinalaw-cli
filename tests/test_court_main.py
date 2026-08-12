"""Tests for the ``court_main`` adapter.

The adapter targets ``www.court.gov.cn`` (the Supreme People's Court main
site), not ``gongbao.court.gov.cn``. Fixtures here are intentionally small but
preserve the site's key shapes: ``/search.html`` rows, ``/xiangqing/`` detail
URLs, ``.txt_txt`` content, source/date metadata, and press-release titles that
embed a normative document title in book-title marks.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from chinalaw import sources
from chinalaw.adapters import court_main
from chinalaw.document_numbers import infer_source, infer_source_id


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


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
