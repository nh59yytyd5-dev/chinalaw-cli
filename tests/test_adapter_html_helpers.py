"""PR5a 守门测试 — adapter HTML helper 收口与 module-alias 兼容。

详见 ``docs/ADAPTER_HTML_HELPERS_SPEC.md`` §3.6。
"""

from __future__ import annotations

import unittest

from chinalaw import cleaning
from chinalaw.adapters import _html, court_gongbao, gov_xzfgk, spp_gov_cn


class HtmlToTextTests(unittest.TestCase):
    """``_html.html_to_text`` 归一三种 Unicode 空格 + 段落换行守门。"""

    def test_html_to_text_normalizes_en_em_ideographic_space(self) -> None:
        """U+2002 / U+2003 / U+3000 都收敛为半角空格。

        spp 修前已归一三种空白；court 修前只归一全角空格。合并后超集统一
        在 ``_html.html_to_text``。

        注意：NBSP（U+00A0）目前不在归一范围内（spp 修前也不归一），保留
        给 cleaning 层处理；本测试不断言 NBSP 行为。
        """

        # 用 unicode 转义避免读代码者把不同空白字符看成同一符号
        en_space = " "
        em_space = " "
        ideographic_space = "　"
        text = f"<p>a{en_space}b{em_space}c{ideographic_space}d</p>"
        result = _html.html_to_text(text)
        self.assertEqual(result, "a b c d")

    def test_html_to_text_preserves_paragraph_breaks(self) -> None:
        """``</p>`` / ``<br>`` / ``</li>`` 等块级闭合 → 换行。"""

        html = "<p>第一条</p><p>第二条</p><br/>第三条<li>第四条</li>"
        result = _html.html_to_text(html)
        self.assertIn("第一条", result)
        self.assertIn("第二条", result)
        self.assertIn("第三条", result)
        self.assertIn("第四条", result)
        # 段落之间应有换行
        self.assertGreaterEqual(result.count("\n"), 3)

    def test_html_to_text_drops_non_content_elements(self) -> None:
        html = (
            "<p>正文</p>"
            "<script>第一条 伪造脚本正文</script>"
            "<style>.x::after{content:'第二条';}</style>"
            "<noscript>第三条</noscript>"
            "<template>第四条</template>"
        )
        self.assertEqual(_html.html_to_text(html), "正文")

    def test_html_to_text_handles_br_attributes_and_table_cells(self) -> None:
        html = (
            "<p>第一条<br class='page-break'>第一款</p>"
            "<table><tr><td>甲</td><td>乙</td></tr></table>"
        )
        result = _html.html_to_text(html)
        self.assertEqual(result.splitlines(), ["第一条", "第一款", "甲", "乙"])


class HtmlExtractTitleTests(unittest.TestCase):
    def test_html_extract_title_strips_embedded_tags(self) -> None:
        html = (
            "<html><head><title>最高人民法院 最高人民检察院<br>"
            "关于适用认罪认罚从宽制度的指导意见_最高人民检察院</title></head>"
            "</html>"
        )
        self.assertEqual(
            _html.html_extract_title(html),
            "最高人民法院 最高人民检察院 关于适用认罪认罚从宽制度的指导意见_最高人民检察院",
        )


class ModuleAliasPreservedTests(unittest.TestCase):
    """既有测试与 adapter 内部仍调 ``court_gongbao._html_to_text`` 等名字；
    搬家时必须保留 module-level helper（无论 alias 形式还是薄 wrapper），
    且功能与 ``_html.*`` 完全等价。
    """

    SAMPLE_HTML = "<p>第一条 内容　X</p><br/><p>第二条 Y</p>"

    def test_court_gongbao_html_to_text_delegates_to_html_helper(self) -> None:
        self.assertEqual(
            court_gongbao._html_to_text(self.SAMPLE_HTML),
            _html.html_to_text(self.SAMPLE_HTML),
        )

    def test_spp_gov_cn_html_to_text_delegates_to_html_helper(self) -> None:
        self.assertEqual(
            spp_gov_cn._html_to_text(self.SAMPLE_HTML),
            _html.html_to_text(self.SAMPLE_HTML),
        )

    def test_court_gongbao_extract_title_module_alias_preserved(self) -> None:
        # ``_extract_title`` 是直接 alias（赋值），可以 assertIs
        self.assertIs(court_gongbao._extract_title, _html.html_extract_title)

    def test_spp_gov_cn_extract_title_module_alias_preserved(self) -> None:
        self.assertIs(spp_gov_cn._extract_title, _html.html_extract_title)


class InferShortTitleAliasFirstTests(unittest.TestCase):
    """``_html.infer_short_title`` 必须先用 ``preferred_short_title`` 命中，
    避免 27+ 字超长 short_title 占满 agent 笔记。
    """

    def test_infer_short_title_alias_takes_precedence_over_prefix_strip(
        self,
    ) -> None:
        """``合通解释``-类标题：preferred_short_title 命中 → 不进入站点
        prefix 剥离分支。"""

        title = (
            "最高人民法院关于适用《中华人民共和国民法典》合同编通则若干问题"
            "的解释"
        )
        # court_gongbao prefix 剥离会得到 "关于适用《中华人民共和国民法典》..."
        # 共 20+ 字；preferred_short_title 优先返回 "合同编通则解释"。
        result = _html.infer_short_title(
            title,
            site_prefixes=("最高人民法院 ", "最高人民法院"),
        )
        self.assertEqual(result, "合同编通则解释")


class PublicDocumentFallbackTests(unittest.TestCase):
    def test_numbered_policy_items_are_searchable_articles(self) -> None:
        articles = cleaning.parse_public_document_articles(
            "会议说明。\n1. 第一项审理要求。\n第一项续行。\n2. 第二项审理要求。"
        )
        self.assertEqual([item["number"] for item in articles], ["1", "2"])
        self.assertIn("续行", articles[0]["text"])

    def test_court_gongbao_unnumbered_minutes_use_numbered_items(self) -> None:
        adapter = court_gongbao.CourtGongbaoAdapter()
        payload = adapter.build_law_payload(
            "a" * 30,
            search_row={"serial_no": "sfwj"},
            detail={
                "detail_id": "a" * 30,
                "title": "全国法院示例工作会议纪要",
                "content_html": (
                    "<p>会议说明。</p><p>1. 第一项审理要求。</p>"
                    "<p>2. 第二项审理要求。</p>"
                ),
                "url": "https://gongbao.court.gov.cn/Details/example.html",
                "checked_at": "2026-08-06T00:00:00+00:00",
            },
        )
        self.assertEqual(payload["level"], "judicial_meeting_minutes")
        self.assertEqual([item["number"] for item in payload["articles"]], ["1", "2"])

    def test_gov_unnumbered_document_uses_body_item(self) -> None:
        adapter = gov_xzfgk.GovXzfgkAdapter()
        payload = adapter.build_law_payload(
            "gov_cn:unnumbered",
            detail={
                "detail_id": "gov_cn:unnumbered",
                "title": "国务院关于示例事项的决定",
                "content_text": "国务院决定开展示例事项。\n本决定自公布之日起施行。",
                "url": "https://www.gov.cn/zhengce/example.htm",
                "source_name": "www.gov.cn",
                "checked_at": "2026-08-06T00:00:00+00:00",
                "related_versions": [],
            },
        )
        self.assertEqual(len(payload["articles"]), 1)
        self.assertEqual(payload["articles"][0]["number"], "正文")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
