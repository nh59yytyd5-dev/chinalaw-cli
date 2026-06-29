"""Tests for the ``nfra_gov_cn`` adapter."""

from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from chinalaw import discover, fetch, sources
from chinalaw.adapters import nfra_gov_cn
from chinalaw.document_numbers import infer_source, infer_source_id


class NfraGovCnAdapterTests(unittest.TestCase):
    DETAIL_HTML = """
<div>
  <p>《银行保险机构公司治理准则》已于2021年1月6日经中国银保监会通过。</p>
  <p>中国银保监会</p>
  <p>2021年6月2日</p>
  <p>银行保险机构公司治理准则</p>
  <p>第一章 总则</p>
  <p>第一条 为推动银行保险机构提高公司治理质效，制定本准则。</p>
  <p>第十六条 银行保险机构股东应当具备履行股东职责的能力。</p>
</div>
"""

    def _detail_result(self) -> nfra_gov_cn.FetchResult:
        payload = {
            "rptCode": 200,
            "data": {
                "docTitle": "中国银保监会关于印发\n银行保险机构公司治理准则的通知",
                "docSubtitle": "银行保险机构公司治理准则",
                "docClob": self.DETAIL_HTML,
                "documentNo": "银保监发〔2021〕14号",
                "builddate": "2021-06-02",
            },
        }
        return nfra_gov_cn.FetchResult(
            url="https://www.nfra.gov.cn/cbircweb/DocInfo/SelectByDocId?docId=989061",
            status_code=200,
            headers={},
            text=json.dumps(payload, ensure_ascii=False),
        )

    def test_normalize_detail_id_accepts_item_detail_url(self) -> None:
        self.assertEqual(
            nfra_gov_cn._normalize_detail_id(
                "https://www.nfra.gov.cn/cn/view/pages/ItemDetail.html?docId=989061&itemId=928"
            ),
            "989061",
        )
        self.assertIsNone(nfra_gov_cn._normalize_detail_id("not-a-detail-url"))

    def test_search_list_returns_bounded_known_rows(self) -> None:
        adapter = nfra_gov_cn.NfraGovCnAdapter()
        result = adapter.search_list("银行保险机构公司治理准则", page_size=5)

        self.assertEqual(result["source"], "nfra_gov_cn")
        self.assertEqual(len(result["rows"]), 1)
        row = result["rows"][0]
        self.assertEqual(row["detail_id"], "989061")
        self.assertEqual(row["title"], "银行保险机构公司治理准则")
        self.assertEqual(row["document_number"], "银保监发〔2021〕14号")

    def test_build_law_payload_cleans_json_detail_api(self) -> None:
        adapter = nfra_gov_cn.NfraGovCnAdapter()
        with patch.object(nfra_gov_cn, "_fetch_text", return_value=self._detail_result()):
            payload = adapter.build_law_payload("989061")

        self.assertEqual(payload["id"], "nfra_gov_cn:989061")
        self.assertEqual(payload["title"], "银行保险机构公司治理准则")
        self.assertEqual(payload["short_title"], "银行保险机构公司治理准则")
        self.assertEqual(payload["level"], "departmental_rule")
        self.assertEqual(payload["status"], "current")
        self.assertEqual(payload["issuing_body"], "中国银行保险监督管理委员会")
        self.assertEqual(payload["document_number"], "银保监发〔2021〕14号")
        self.assertEqual(payload["released_at"], "2021-06-02")
        self.assertEqual(payload["source_name"], "www.nfra.gov.cn")
        self.assertEqual(len(payload["articles"]), 2)
        self.assertEqual(payload["articles"][1]["number"], "16")

    def test_source_registry_and_source_id_inference_include_nfra(self) -> None:
        self.assertIn("nfra_gov_cn", sources.ADAPTER_REGISTRY)
        self.assertIn("nfra_gov_cn", sources.VERIFIABLE_SOURCES)
        self.assertIn("nfra_gov_cn", fetch.FETCH_SOURCES)
        self.assertIn("nfra_gov_cn", discover.DISCOVER_SOURCES)
        payload = {
            "id": "nfra_gov_cn:989061",
            "source_url": "https://www.nfra.gov.cn/cn/view/pages/ItemDetail.html?docId=989061&itemId=928",
            "source_name": "www.nfra.gov.cn",
        }
        self.assertEqual(infer_source(payload), "nfra_gov_cn")
        self.assertEqual(infer_source_id(payload, "nfra_gov_cn"), "989061")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
