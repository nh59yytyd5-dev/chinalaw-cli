"""Lines that look like article headings but are not."""

from __future__ import annotations

import unittest

from chinalaw import cleaning


def numbers(text: str) -> list[str]:
    return [article["number"] for article in cleaning.parse_articles_from_text(text)]


class ArticleHeadingTests(unittest.TestCase):
    def test_paragraph_citing_its_own_article_continues_it(self) -> None:
        text = (
            "第四十四条　高等学校应当开展军事训练。\n"
            "第四十五条　普通高等学校的学生在就学期间，必须接受基本军事训练。\n"
            "第四十五条第二款规定的培养预备役军官的短期集中训练，由军事部门派出现役军官组织实施。\n"
            "第四十六条　本法自公布之日起施行。"
        )
        articles = cleaning.parse_articles_from_text(text)
        self.assertEqual([a["number"] for a in articles], ["44", "45", "46"])
        self.assertIn("第四十五条第二款规定的", articles[1]["text"])

    def test_citation_of_another_article_without_separator_is_text(self) -> None:
        text = "第一条　甲。\n第二条　乙。\n第一条第二款的规定不适用于前款情形。\n第三条　丙。"
        self.assertEqual(numbers(text), ["1", "2", "3"])

    def test_heading_whose_body_starts_with_guiding_word_is_kept(self) -> None:
        text = "第七十五条　甲。\n第七十六条　规定本行政区域特别重大事项的地方性法规，应当由人民代表大会通过。"
        self.assertEqual(numbers(text), ["75", "76"])

    def test_decimal_table_cells_after_statute_numbering_are_text(self) -> None:
        text = "第十三条　本法自2012年1月1日起施行。\n乘用车\n1.0升（含）以下的\n60元至360元\n1.6升以上至2.0升（含）的"
        articles = cleaning.parse_articles_from_text(text)
        self.assertEqual([a["number"] for a in articles], ["13"])
        self.assertIn("1.0升（含）以下的", articles[0]["text"])

    def test_decimal_numbering_still_works_on_its_own(self) -> None:
        self.assertEqual(numbers("1.1 会员应当遵守本规则。\n1.2 本所负责解释。"), ["1.1", "1.2"])


if __name__ == "__main__":
    unittest.main()
