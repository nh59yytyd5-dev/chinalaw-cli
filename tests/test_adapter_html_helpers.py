"""PR5a 守门测试 — adapter HTML helper 收口与 module-alias 兼容。

详见 ``docs/ADAPTER_HTML_HELPERS_SPEC.md`` §3.6。
"""

from __future__ import annotations

import unittest

from chinalaw import cleaning
from chinalaw.adapters import _html, court_gongbao, court_main, gov_xzfgk, spp_gov_cn


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

    def test_numbered_item_body_may_start_with_fullwidth_quote(self) -> None:
        """认罪认罚指导意见（高检发〔2026〕5号）回归：``7．“认罪”的把握。``
        这类引号开头的条目不得被静默并入上一条。"""

        articles = cleaning.parse_public_document_articles(
            "通知说明。\n1．单位犯罪案件的适用。单位犯罪案件内容。\n"
            "2．“认罪”的把握。认罪认定内容。\n3．“认罚”的把握。认罚认定内容。"
        )
        self.assertEqual([item["number"] for item in articles], ["1", "2", "3"])
        self.assertNotIn("2．", articles[0]["text"])
        self.assertIn("认罪认定内容", articles[1]["text"])

    def test_enum_section_heading_may_contain_fullwidth_quotes(self) -> None:
        """同文档回归：节标题``三、认罪认罚后“从宽”的把握``须推进 part 上下文，
        不得让后续条目全部挂在上一节。"""

        articles = cleaning.parse_public_document_articles(
            "一、基本原则\n1．第一条内容。\n二、适用范围和适用条件\n2．第二条内容。\n"
            "三、认罪认罚后“从宽”的把握\n3．第三条内容。"
        )
        self.assertEqual(articles[2]["part"], "三、认罪认罚后“从宽”的把握")

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

    def test_outline_policy_items_reachable_via_public_chain(self) -> None:
        """电诈意见 / 软暴力意见形态回归：``一、`` 节 + ``（一）`` 条目层级的
        意见类文档应经 parse_public_document_articles 回退链切出条目，
        不再退化为 single_body 全文一条。"""

        articles = cleaning.parse_public_document_articles(
            "意见引言，为依法惩治示例犯罪提出如下意见。\n"
            "一、总体要求\n"
            "（一）准确把握示例犯罪的构成要件。\n"
            "（二）严格区分罪与非罪的界限。\n"
            "二、适用范围\n"
            "（一）本意见适用于示例案件办理。\n"
        )
        self.assertEqual(
            [item["number"] for item in articles],
            ["序言", "1", "2", "3"],
        )
        self.assertEqual(articles[1]["title"], "（一）")
        self.assertEqual(articles[1]["part"], "一、总体要求")
        self.assertEqual(articles[3]["part"], "二、适用范围")

    def test_outline_items_pass_full_canonicalize(self) -> None:
        """outline 解析产物须过 normalize_articles 全量校验：
        number 全库唯一、number_display 与 number 一致、序言 symbolic 合法。"""

        articles = cleaning.parse_public_document_articles(
            "意见引言。\n"
            "一、总体要求\n"
            "（一）第一条目正文。\n"
            "（二）第二条目正文。\n"
            "二、适用范围\n"
            "（一）第三条目正文。\n"
        )
        payload = cleaning.canonicalize(
            {
                "id": "test:outline-opinion",
                "title": "最高人民法院、最高人民检察院关于办理示例案件若干问题的意见",
                "level": "judicial_policy",
                "status": "unknown",
                "source_url": "https://www.spp.gov.cn/spp/gfwj/example.shtml",
                "source_name": "spp.gov.cn",
                "source_checked_at": "2026-10-04T00:00:00+00:00",
                "articles": articles,
            },
            source_kind="external_json",
        )
        self.assertEqual(len(payload["articles"]), 4)
        self.assertEqual(payload["articles"][1]["number_display"], "第1项")
        self.assertEqual(payload["articles"][1]["title"], "（一）")


class OutlineNumberedItemsParseTests(unittest.TestCase):
    """``一、`` 节 + ``（一）`` 条目（+ ``1．`` 子项）层级意见文档的解析器测试。

    实测样本形态：《关于办理电信网络诈骗等刑事案件适用法律若干问题的意见》
    （2016）及其（二）（法发〔2021〕22号）、《关于办理非法集资刑事案件若干
    问题的意见》（2019）、《关于办理实施"软暴力"的刑事案件若干问题的意见》
    （2019）等"两高"联合意见；纯节级退化样本：《关于推进以审判为中心的
    刑事诉讼制度改革的意见》（2016）。
    """

    # 三层形态：节 + （一）条目 + 1．子项，跨节（N）序号重启。
    OUTLINE_SAMPLE = (
        "最高人民法院 最高人民检察院\n"
        "关于办理示例案件若干问题的意见\n"
        "为依法惩治示例犯罪，现提出如下意见：\n"
        "一、总体要求\n"
        "（一）准确把握示例犯罪的构成要件，依法惩治相关犯罪。\n"
        "（二）严格区分罪与非罪的界限，防止扩大打击面。\n"
        "本条续段内容。\n"
        "1．子项一是正文普通段落。\n"
        "2．子项二也是正文普通段落。\n"
        "二、证据审查\n"
        "（一）全面收集、固定示例证据。\n"
        "（二）依法排除非法证据，保障诉讼权利。\n"
    )

    def test_parses_three_layer_outline(self) -> None:
        items = cleaning.parse_outline_numbered_items_from_text(self.OUTLINE_SAMPLE)
        self.assertEqual(len(items), 5)  # 序言 + 4 条
        self.assertEqual(items[0]["number"], "序言")
        self.assertIn("为依法惩治示例犯罪", items[0]["text"])

        first = items[1]
        # number 用全文流水号、number_display 用"第N项"，
        # 原文（一）标记存 title 以还原"一、（一）"原始地址。
        self.assertEqual(first["number"], "1")
        self.assertEqual(first["number_display"], "第1项")
        self.assertEqual(first["title"], "（一）")
        self.assertEqual(first["part"], "一、总体要求")

        second = items[2]
        self.assertEqual(second["title"], "（二）")
        # 续段与 1．子项都并入条目正文，子项不提升为条目。
        self.assertIn("本条续段内容。", second["text"])
        self.assertIn("1．子项一是正文普通段落。", second["text"])
        self.assertIn("2．子项二也是正文普通段落。", second["text"])

        third = items[3]
        # 新节内（N）从（一）重启，流水号继续递增。
        self.assertEqual(third["number"], "3")
        self.assertEqual(third["title"], "（一）")
        self.assertEqual(third["part"], "二、证据审查")
        self.assertTrue(all(item["text"] for item in items))

    def test_section_only_document_falls_back_to_section_items(self) -> None:
        """以审判为中心意见形态：只有 ``一、二、`` 节、没有（一）条目层，
        回退到节级切分——每节一个条目，title 与 part 同记节名。"""

        items = cleaning.parse_outline_numbered_items_from_text(
            "关于推进以审判为中心的刑事诉讼制度改革的意见\n"
            "为贯彻党中央决策部署，现提出如下意见。\n"
            "一、推进以审判为中心的诉讼制度改革\n"
            "改革正文第一段。\n"
            "改革正文第二段。\n"
            "二、完善庭前会议和法庭审理程序\n"
            "审判环节正文。\n"
            "三、完善证据裁判和非法证据排除规则\n"
            "证据环节正文。\n"
        )
        self.assertEqual([item["number"] for item in items], ["序言", "1", "2", "3"])
        self.assertEqual(items[1]["title"], "一、推进以审判为中心的诉讼制度改革")
        self.assertEqual(items[1]["part"], "一、推进以审判为中心的诉讼制度改革")
        self.assertEqual(items[1]["number_display"], "第1项")
        self.assertIn("改革正文第二段。", items[1]["text"])
        self.assertEqual(items[3]["title"], "三、完善证据裁判和非法证据排除规则")

    def test_broken_item_sequence_fails_loud(self) -> None:
        text = "一、节标题\n（一）正文一。\n（三）正文三。\n"
        with self.assertRaises(ValueError):
            cleaning.parse_outline_numbered_items_from_text(text)

    def test_loose_item_before_first_item_fails_loud(self) -> None:
        """首个条目前的散装（N）行不得静默吞掉：首条目必须是（一）。"""
        text = "引言段落。\n（二）散装行。\n（三）又一散装行。\n"
        with self.assertRaises(ValueError):
            cleaning.parse_outline_numbered_items_from_text(text)

    def test_item_ordinal_must_restart_at_new_section(self) -> None:
        """新节内（N）必须从（一）重启；同时守门枚举节标题含"节"字时
        严格 +1 序号约定不被 编/章/节 关键字分支打断（_update_context 回归）。"""
        text = "一、第一节标题\n（一）正文。\n二、第二节标题\n（二）未重启。\n"
        with self.assertRaises(ValueError):
            cleaning.parse_outline_numbered_items_from_text(text)

    def test_enum_section_heading_containing_jie_keeps_sequence(self) -> None:
        """与上一测试同源的正面用例：含"节"字的枚举标题正常推进 part。"""
        items = cleaning.parse_outline_numbered_items_from_text(
            "一、第一节标题\n（一）正文一。\n二、第二节标题\n（一）正文二。\n"
        )
        self.assertEqual(
            [item["part"] for item in items],
            ["一、第一节标题", "二、第二节标题"],
        )

    def test_empty_item_body_fails_loud(self) -> None:
        text = "（一）\n（二）正文二。\n"
        with self.assertRaises(ValueError):
            cleaning.parse_outline_numbered_items_from_text(text)

    def test_statute_style_text_returns_empty(self) -> None:
        text = "第一条 为了测试制定本法。\n第二条 本法适用于测试事项。\n"
        self.assertEqual(cleaning.parse_outline_numbered_items_from_text(text), [])

    def test_fewer_than_min_items_returns_empty(self) -> None:
        self.assertEqual(
            cleaning.parse_outline_numbered_items_from_text("（一）唯一条目。"),
            [],
        )
        self.assertEqual(
            cleaning.parse_outline_numbered_items_from_text("一、仅有节标题\n节正文。\n"),
            [],
        )

    def test_inline_item_reference_is_not_an_item(self) -> None:
        """"（一）项规定的……" 这类行首交叉引用按续段处理，不当条目切分。"""
        items = cleaning.parse_outline_numbered_items_from_text(
            "一、节标题\n"
            "（一）第一条目正文。\n"
            "（一）项规定的引用行属于上一条。\n"
            "（二）第二条目正文。\n"
        )
        self.assertEqual([item["number"] for item in items], ["1", "2"])
        self.assertIn("（一）项规定的引用行属于上一条。", items[0]["text"])

    def test_policy_item_articles_falls_back_to_outline_items(self) -> None:
        # 与公开回退链同序：numbered 形态优先；本样本 1．子项粘合在条目行内
        # （spp / court 详情页 HTML 转文本的常见线性化结果），numbered 不命中，
        # 落到 outline 解析。
        items = court_main._policy_item_articles(
            "judicial_policy",
            "意见引言。\n"
            "一、总体要求\n"
            "（一）第一条目正文，1．子项粘合在同一段。\n"
            "（二）第二条目正文。\n"
            "二、证据审查\n"
            "（一）第三条目正文。\n",
        )
        self.assertEqual(len(items), 4)
        self.assertEqual(items[1]["title"], "（一）")
        self.assertEqual(items[3]["part"], "二、证据审查")
        # 非纪要/政策层级不启用该解析路径。
        self.assertEqual(court_main._policy_item_articles("law", self.OUTLINE_SAMPLE), [])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
