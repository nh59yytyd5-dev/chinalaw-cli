"""Tests for the gov.cn / MOJ National Administrative Regulations Database adapter."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from chinalaw import fetch, sources
from chinalaw.adapters import gov_xzfgk
from chinalaw.document_numbers import infer_source, infer_source_id


class GovXzfgkAdapterTests(unittest.TestCase):
    SEARCH_FIXTURE = """
<html><body>
<input type="hidden" id="law-total" value="611"/>
<ul class="searching-results-list">
  <li class="list-item">
    <div>1.</div>
    <div class="search-in-title">
      <div class="col-left">
        <div class="title">
          <a target="_blank" href="https://xzfg.moj.gov.cn/front/law/detail?LawID=1814&Query=">
            <font color=red>行政法规制定</font><font color=red>程序</font><font color=red>条例</font>
          </a>
          <span class="tip">历史沿革</span>
        </div>
        <ul class="info-list">
          <li class="publish-date">2026-05-15公布</li>
          <li class="implemented-date">2026-07-01施行</li>
        </ul>
      </div>
    </div>
    <div class="fold-con">
      <div class="incident-record" data-time="2001-11-16">
        <a class="" target="_blank" href="https://xzfg.moj.gov.cn/front/law/detail?LawID=889">行政法规制定程序条例</a>
      </div>
      <div class="incident-record" data-time="2017-12-22">
        <a class="" target="_blank" href="https://xzfg.moj.gov.cn/front/law/detail?LawID=569">行政法规制定程序条例</a>
      </div>
      <div class="incident-record" data-time="2026-05-15">
        <a class="on" target="_blank" href="https://xzfg.moj.gov.cn/front/law/detail?LawID=1814">行政法规制定程序条例</a>
      </div>
    </div>
  </li>
</ul>
<div id="pagination"></div>
<input type="hidden" id="page-count" value="1"/>
</body></html>
"""

    DETAIL_FIXTURE = """
<html>
<head><title>行政法规制定程序条例 - 国家行政法规库</title></head>
<body>
<div class="text-title">行政法规制定程序条例</div>
<div class="fold-con">
  <div class="incident-record" data-time="2001-11-16">
    <a class="" target="_blank" href="https://xzfg.moj.gov.cn/front/law/detail?LawID=889">行政法规制定程序条例</a>
  </div>
  <div class="incident-record" data-time="2026-05-15">
    <a class="on" target="_blank" href="https://xzfg.moj.gov.cn/front/law/detail?LawID=1814">行政法规制定程序条例</a>
  </div>
</div>
<div class="law-chapter">
  <div>
    <p>行政法规制定程序条例</p>
    <p>(2001年11月16日中华人民共和国国务院令第321号公布 2026年5月15日中华人民共和国国务院令第838号第二次修订)</p>
    <h2>第一章 总则</h2>
    <p><span>第一条 </span><span>为了规范行政法规制定程序，制定本条例。</span></p>
    <p><span>第二条 </span><span>行政法规的立项、起草、审查、决定、公布、解释，适用本条例。</span></p>
    <h2>附则</h2>
    <p><span>第三条 </span><span>本条例自2026年7月1日起施行。</span></p>
  </div>
</div>
</main>
</body>
</html>
"""

    def _search_result(self) -> gov_xzfgk.FetchResult:
        return gov_xzfgk.FetchResult(
            url="https://xzfg.moj.gov.cn/SearchAdvancedFront?title=x",
            status_code=200,
            headers={},
            text=self.SEARCH_FIXTURE,
        )

    def _detail_result(self) -> gov_xzfgk.FetchResult:
        return gov_xzfgk.FetchResult(
            url="https://xzfg.moj.gov.cn/front/law/detail?LawID=1814",
            status_code=200,
            headers={"Last-Modified": "Thu, 21 May 2026 00:00:00 GMT"},
            text=self.DETAIL_FIXTURE,
        )

    def test_search_list_parses_rows_and_version_chain(self) -> None:
        adapter = gov_xzfgk.GovXzfgkAdapter()
        with patch.object(gov_xzfgk, "_fetch_text", return_value=self._search_result()):
            result = adapter.search_list("行政法规制定程序条例", page_size=5)

        self.assertEqual(result["source"], "gov_xzfgk")
        self.assertEqual(result["total_count"], 611)
        self.assertEqual(result["total_pages"], 1)
        self.assertEqual(len(result["rows"]), 1)
        row = result["rows"][0]
        self.assertEqual(row["detail_id"], "1814")
        self.assertEqual(row["title"], "行政法规制定程序条例")
        self.assertEqual(row["released_at"], "2026-05-15")
        self.assertEqual(row["effective_at"], "2026-07-01")
        self.assertEqual(len(row["related_versions"]), 3)
        self.assertTrue(row["related_versions"][-1]["current"])

    def test_build_law_payload_cleans_admin_regulation_detail(self) -> None:
        adapter = gov_xzfgk.GovXzfgkAdapter()
        with patch.object(gov_xzfgk, "_fetch_text", return_value=self._detail_result()):
            payload = adapter.build_law_payload("1814")

        self.assertEqual(payload["id"], "gov_xzfgk:1814")
        self.assertEqual(payload["title"], "行政法规制定程序条例")
        self.assertEqual(payload["short_title"], "行政法规制定程序条例")
        self.assertEqual(payload["level"], "admin_regulation")
        self.assertEqual(payload["status"], "current")
        self.assertEqual(payload["issuing_body"], "国务院")
        self.assertEqual(payload["document_number"], "国务院令第838号")
        self.assertEqual(payload["released_at"], "2026-05-15")
        self.assertEqual(payload["effective_at"], "2026-07-01")
        self.assertEqual(payload["source_name"], "xzfg.moj.gov.cn")
        self.assertEqual(len(payload["articles"]), 3)
        self.assertEqual(payload["articles"][0]["number_display"], "第一条")
        self.assertEqual(len(payload["related_versions"]), 2)

    def test_source_registry_and_source_id_inference_include_gov_xzfgk(self) -> None:
        self.assertIn("gov_xzfgk", sources.ADAPTER_REGISTRY)
        self.assertIn("gov_xzfgk", sources.VERIFIABLE_SOURCES)
        self.assertIn("gov_xzfgk", fetch.FETCH_SOURCES)
        payload = {
            "id": "gov_xzfgk:1814",
            "source_url": "https://xzfg.moj.gov.cn/front/law/detail?LawID=1814",
            "source_name": "xzfg.moj.gov.cn",
        }
        self.assertEqual(infer_source(payload), "gov_xzfgk")
        self.assertEqual(infer_source_id(payload, "gov_xzfgk"), "1814")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
