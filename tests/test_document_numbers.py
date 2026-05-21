"""Tests for the shared document_number helper (PR refactor/unify-document-number-regex).

详见 ``docs/UNIFY_DOCUMENT_NUMBER_REGEX_SPEC.md``。
"""

from __future__ import annotations

import unittest

from chinalaw import cleaning
from chinalaw.adapters import court_gongbao, spp_gov_cn
from chinalaw.document_numbers import (
    DOCUMENT_NUMBER_FULLMATCH_RE,
    DOCUMENT_NUMBER_INLINE_RE,
    DOCUMENT_NUMBER_RE,
    extract_document_number,
)


class ExtractDocumentNumberTests(unittest.TestCase):
    """守门 :func:`chinalaw.document_numbers.extract_document_number`。"""

    def test_extract_document_number_recognizes_all_central_prefixes(self) -> None:
        """中央 / 法 / 检 / 党办 / 国发各家文号都应识别。

        修前 court_gongbao 强制 ``法`` 前缀漏召高检 / 中办 / 国发；本 helper
        无前缀约束，5 类样本都应原样抽出。
        """

        samples = [
            ("法释〔2023〕13号 正文", "法释〔2023〕13号"),
            ("法发〔2024〕5号", "法发〔2024〕5号"),
            ("高检发释字〔2017〕7号", "高检发释字〔2017〕7号"),
            ("中办发〔2022〕10号 通知", "中办发〔2022〕10号"),
            ("正文里出现 国发〔2021〕23号 的引用", "国发〔2021〕23号"),
        ]
        for text, expected in samples:
            with self.subTest(text=text):
                self.assertEqual(extract_document_number(text), expected)

    def test_extract_document_number_tolerates_whitespace(self) -> None:
        """文号内夹空白（``法释〔2023〕 13 号``）也能抽出 + normalize。"""

        self.assertEqual(
            extract_document_number("法释〔2023〕 13 号"),
            "法释〔2023〕13号",
        )
        self.assertEqual(
            extract_document_number("高检发〔2024〕  7  号"),
            "高检发〔2024〕7号",
        )

    def test_extract_document_number_returns_none_when_absent(self) -> None:
        """无文号文本（含空 / None）一律返回 None，不抛异常。"""

        self.assertIsNone(extract_document_number(None))
        self.assertIsNone(extract_document_number(""))
        self.assertIsNone(extract_document_number("没有任何文号的普通文本"))
        self.assertIsNone(extract_document_number("《中华人民共和国刑法》第三条"))

    def test_extract_document_number_from_preamble_ignores_repeal_clause(self) -> None:
        """metadata 文号只从题注区抽，不读取正文中被废止文件的文号。"""

        text = "\n".join(
            [
                "人民检察院刑事诉讼规则",
                "第一条 为保证人民检察院在刑事诉讼中严格依照法定程序办案，制定本规则。",
                "第六百八十四条 本规则自2019年12月30日起施行。《人民检察院刑事诉讼规则（试行）》（高检发释字〔2012〕2号）同时废止。",
            ]
        )
        self.assertIsNone(cleaning.extract_document_number_from_preamble(text))

    def test_extract_document_number_from_preamble_reads_heading_number(self) -> None:
        """真实文号位于标题/题注区时仍应抽出。"""

        text = "\n".join(
            [
                "最高人民法院关于审理示例案件适用法律若干问题的解释",
                "法释〔2026〕5号",
                "第一条 示例正文。",
                "第二条 引用旧文件法释〔2010〕1号。",
            ]
        )
        self.assertEqual(
            cleaning.extract_document_number_from_preamble(text),
            "法释〔2026〕5号",
        )

    def test_extract_document_number_from_preamble_keeps_article_reference_title(self) -> None:
        """标题片段引用条款款项时，不应被当成正文第一条边界。"""

        text = "\n".join(
            [
                "最高人民法院",
                "关于《中华人民共和国公司法》",
                "第八十八条第一款",
                "不溯及适用的批复",
                "法释〔2024〕7号",
                "你院请示收悉。经研究，批复如下：",
            ]
        )
        self.assertEqual(
            cleaning.extract_document_number_from_preamble(text),
            "法释〔2024〕7号",
        )

    def test_court_gongbao_extract_document_number_uses_shared_helper(self) -> None:
        """court_gongbao 现在能抽出非 ``法`` 前缀文号——修前漏召的关键场景。

        守门：court_gongbao adapter 不再保留本地 ``法`` 前缀约束，
        中办 / 高检 / 国发等前缀都应被抽出。
        """

        self.assertIs(court_gongbao._extract_document_number, extract_document_number)
        self.assertEqual(
            court_gongbao._extract_document_number(
                "正文中嵌入 高检发释字〔2017〕7号 引用"
            ),
            "高检发释字〔2017〕7号",
        )
        self.assertEqual(
            court_gongbao._extract_document_number("中办发〔2022〕10号"),
            "中办发〔2022〕10号",
        )

    def test_spp_gov_cn_extract_document_number_uses_shared_helper(self) -> None:
        """spp_gov_cn 也走 shared helper，抽取 ``法释`` / ``高检发`` 都正常。

        守门：spp_gov_cn adapter 删除本地正则后，与权威定义口径一致。
        """

        self.assertIs(spp_gov_cn._extract_document_number, extract_document_number)
        self.assertEqual(
            spp_gov_cn._extract_document_number(
                "本解释自2024年X月Y日起施行 高检发〔2024〕12号"
            ),
            "高检发〔2024〕12号",
        )
        # spp 修前下限 2 字，权威定义下限 1 字；用真实 2 字前缀守门。
        self.assertEqual(
            spp_gov_cn._extract_document_number("法释〔2024〕12号"),
            "法释〔2024〕12号",
        )

    def test_module_constants_exposed(self) -> None:
        """``document_numbers`` 暴露 fullmatch / inline 两个常量供下游显式选择。"""

        # FULLMATCH alias 必须等于既有 RE（向后兼容）。
        self.assertIs(DOCUMENT_NUMBER_FULLMATCH_RE, DOCUMENT_NUMBER_RE)
        # INLINE 不是 FULLMATCH（语义不同）。
        self.assertIsNot(DOCUMENT_NUMBER_INLINE_RE, DOCUMENT_NUMBER_FULLMATCH_RE)
        # INLINE 不锚定，能在中间命中。
        self.assertIsNotNone(
            DOCUMENT_NUMBER_INLINE_RE.search("正文 法释〔2023〕13号 后续")
        )
        # FULLMATCH 锚定，正文不命中。
        self.assertIsNone(
            DOCUMENT_NUMBER_FULLMATCH_RE.match("正文 法释〔2023〕13号 后续")
        )
        self.assertIsNotNone(
            DOCUMENT_NUMBER_FULLMATCH_RE.match("法释〔2023〕13号")
        )


if __name__ == "__main__":
    unittest.main()
