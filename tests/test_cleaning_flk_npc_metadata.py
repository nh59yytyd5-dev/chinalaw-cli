"""Regression tests for ``canonicalize_flk_npc`` document_number / repealed_at.

Pre-PR：``src/chinalaw/cleaning.py:171,174`` 把这两个字段硬编码为 ``None``。
PR-A 修复同层不变量违反：

* ``document_number`` —— 优先 ``detail_data`` 候选键（``wenhao`` / ``wh`` /
  ``documentNumber``），退到 ``docx_bytes`` 题注调
  :func:`chinalaw.document_numbers.extract_document_number` 抽首匹配。
* ``repealed_at`` —— 防御性读 ``detail_data.get("fzrq")``。

详见 ``docs/CLEANING_FLK_NPC_RESTORE_METADATA_SPEC.md``。
"""

from __future__ import annotations

import json
import unittest
import zipfile
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from chinalaw import cleaning


def _make_docx_bytes(paragraphs: list[dict]) -> bytes:
    """与 ``tests/test_core.py::make_docx_bytes`` 同型，避免跨文件依赖。"""

    body = []
    for index, paragraph in enumerate(paragraphs, start=1):
        bookmark = ""
        if paragraph.get("bookmark"):
            bookmark = (
                f'<w:bookmarkStart w:id="{index}" w:name="{paragraph["bookmark"]}"/>'
                f'<w:bookmarkEnd w:id="{index}"/>'
            )
        body.append(
            "<w:p>"
            f"{bookmark}"
            "<w:r>"
            f"<w:t>{paragraph['text']}</w:t>"
            "</w:r>"
            "</w:p>"
        )
    xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{''.join(body)}</w:body>"
        "</w:document>"
    )
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("word/document.xml", xml)
    return buffer.getvalue()


def _flk_detail_payload(**overrides) -> dict:
    """合理默认值的 flk 详情 payload，方便测试覆盖单字段差异。

    与实测 flk 详情字段集对齐（合同法 1999 / 公司法 2018 实抓字段：
    ``bbbs / title / flxz / zdjgName / gbrq / sxrq / sxx``）。
    """

    data = {
        "bbbs": "law-flk-meta",
        "title": "中华人民共和国示例法",
        "flxz": "法律",
        "zdjgName": "全国人民代表大会",
        "gbrq": "1999-03-15",
        "sxrq": "1999-10-01",
        "sxx": 1,
    }
    data.update(overrides)
    return {"code": 200, "data": data}


def _minimal_docx() -> bytes:
    """没有题注 / 文号的最小可解析 docx，仅含一条 article。"""

    return _make_docx_bytes(
        [
            {"bookmark": "中华人民共和国示例法", "text": "中华人民共和国示例法"},
            {"bookmark": "第一章 总则", "text": "第一章 总则"},
            {"bookmark": "第一条", "text": "第一条 示例正文。"},
        ]
    )


class FlkNpcDocumentNumberTests(unittest.TestCase):
    """document_number 提取路径覆盖。"""

    def test_document_number_from_detail_data_wenhao(self):
        """detail_data['wenhao'] 命中时直接采用，不调 docx 抽取。"""

        payload = cleaning.canonicalize(
            _flk_detail_payload(wenhao="主席令第15号"),
            source_kind="flk_npc_detail",
            bbbs="law-flk-meta",
            docx_bytes=_minimal_docx(),
            checked_at="2026-05-05T00:00:00+00:00",
        )
        self.assertEqual(payload["document_number"], "主席令第15号")

    def test_document_number_from_detail_data_wh_alias(self):
        """``wh`` 候选键是 flk 拼音首字母约定的备选命名（``zdjgName``、``gbrq``
        等同款），即便当前 flk 未返回，本 fix 也保留对它的支持，避免后续
        schema 改名时再次硬编码失效。"""

        payload = cleaning.canonicalize(
            _flk_detail_payload(wh="法释〔2023〕13号"),
            source_kind="flk_npc_detail",
            bbbs="law-flk-meta",
            docx_bytes=_minimal_docx(),
            checked_at="2026-05-05T00:00:00+00:00",
        )
        self.assertEqual(payload["document_number"], "法释〔2023〕13号")

    def test_document_number_from_docx_preamble(self):
        """detail_data 无任何候选键，但 docx 题注含 ``法释〔2023〕13号`` 这种
        ``DOCUMENT_NUMBER_INLINE_RE`` 覆盖范围内的文号时，由
        ``extract_document_number`` 抽出（与 HTML adapter 同型）。

        覆盖边界：``DOCUMENT_NUMBER_INLINE_RE`` 只匹配 ``XX〔YYYY〕NN号``
        形态，覆盖 ``法释 / 法发 / 中办发 / 高检发`` 等中央 / 司法 / 党政
        发文体；不覆盖 ``主席令第N号`` / ``国务院令第N号``（无 ``〔YYYY〕``）
        ——这两种 flk 法律 / 行政法规 docx 题注典型形态需要单独扩 regex，
        本 PR 不在范围（详见 spec §4.2 / §5）。
        """

        docx_bytes = _make_docx_bytes(
            [
                {"bookmark": "题注", "text": "法释〔2023〕13号"},
                {"bookmark": "最高人民法院关于示例问题的解释", "text": "最高人民法院关于示例问题的解释"},
                {"bookmark": "第一章 总则", "text": "第一章 总则"},
                {"bookmark": "第一条", "text": "第一条 示例正文。"},
            ]
        )
        payload = cleaning.canonicalize(
            _flk_detail_payload(),
            source_kind="flk_npc_detail",
            bbbs="law-flk-meta",
            docx_bytes=docx_bytes,
            checked_at="2026-05-05T00:00:00+00:00",
        )
        self.assertEqual(payload["document_number"], "法释〔2023〕13号")

    def test_document_number_absent_falls_back_to_none(self):
        """detail JSON 没有候选键、docx 也没文号字符串 → None（不抛错）。"""

        payload = cleaning.canonicalize(
            _flk_detail_payload(),
            source_kind="flk_npc_detail",
            bbbs="law-flk-meta",
            docx_bytes=_minimal_docx(),
            checked_at="2026-05-05T00:00:00+00:00",
        )
        self.assertIsNone(payload["document_number"])

    def test_document_number_ignores_article_body_citations(self):
        """正文条款里引用其它文件文号时，不得误当成本法规自己的文号。

        ``document_number_index`` 会按该字段建立反查索引；如果扫描整份 docx，
        一部没有自身文号的法规只要正文引用 ``法释〔2023〕13号``，后续文号
        fetch hint 就会命中错误法规。
        """

        docx_bytes = _make_docx_bytes(
            [
                {"bookmark": "中华人民共和国示例法", "text": "中华人民共和国示例法"},
                {
                    "bookmark": "第一条",
                    "text": "第一条 本条引用法释〔2023〕13号作为示例。",
                },
            ]
        )
        payload = cleaning.canonicalize(
            _flk_detail_payload(),
            source_kind="flk_npc_detail",
            bbbs="law-flk-meta",
            docx_bytes=docx_bytes,
            checked_at="2026-05-05T00:00:00+00:00",
        )
        self.assertIsNone(payload["document_number"])

    def test_corrupt_docx_falls_back_to_none(self):
        """``docx_bytes`` 不是合法 ZIP 也不是 OLE 时，document_number 抽取
        helper swallow 解析错误回退 None，不阻断 canonicalize 调用。"""

        # build_law_payload 调到 normalize_articles 之前会先打 articles，
        # 所以无法用纯坏字节。这里 patch parse_articles_from_word_bytes 让
        # canonicalize 走完，单独验证 _flk_document_number 的容错路径。
        bad_bytes = b"\x00not-a-zip-or-ole"
        with patch.object(
            cleaning,
            "parse_articles_from_word_bytes",
            return_value=[
                {
                    "number": "1",
                    "number_display": "第一条",
                    "text": "示例正文。",
                    "part": None,
                    "position": 1,
                }
            ],
        ):
            payload = cleaning.canonicalize(
                _flk_detail_payload(),
                source_kind="flk_npc_detail",
                bbbs="law-flk-meta",
                docx_bytes=bad_bytes,
                checked_at="2026-05-05T00:00:00+00:00",
            )
        self.assertIsNone(payload["document_number"])


class FlkNpcRepealedAtTests(unittest.TestCase):
    """repealed_at 提取路径覆盖。"""

    def test_repealed_at_from_detail_data_fzrq(self):
        """detail_data 含 ``fzrq``（"废止日期"拼音候选）时直接采用。

        实测当前 flk 详情接口不返回该字段（见
        ``docs/CLEANING_FLK_NPC_RESTORE_METADATA_SPEC.md`` §1.1），但移除硬编码
        后 caller 在合成 detail_payload / 未来 flk schema 升级时即可使用。
        """

        payload = cleaning.canonicalize(
            _flk_detail_payload(fzrq="2020-12-31"),
            source_kind="flk_npc_detail",
            bbbs="law-flk-meta",
            docx_bytes=_minimal_docx(),
            checked_at="2026-05-05T00:00:00+00:00",
        )
        self.assertEqual(payload["repealed_at"], "2020-12-31")

    def test_repealed_at_absent_falls_back_to_none(self):
        """``fzrq`` 缺失时仍是 None——与原硬编码语义对外等价（payload 字段
        存在但值为 None），但代码不再硬编码。"""

        payload = cleaning.canonicalize(
            _flk_detail_payload(),
            source_kind="flk_npc_detail",
            bbbs="law-flk-meta",
            docx_bytes=_minimal_docx(),
            checked_at="2026-05-05T00:00:00+00:00",
        )
        self.assertIsNone(payload["repealed_at"])


class FlkNpcMetadataExistingFixtureRegressionTests(unittest.TestCase):
    """既有 fixture 回归保护，确保本 PR 不漂移现有 fixture 的字段语义。"""

    def test_existing_contract_chapter_interpretation_fixture_metadata_unchanged(self):
        """``data/fixtures/contract_chapter_interpretation_2023.json`` 是当前唯一
        从 flk_npc 落盘的 fixture，document_number / repealed_at 都是 null。

        本 PR 改 cleaning 层后再用同样的 detail_data + 一份模拟 docx
        重跑 canonicalize，metadata 应仍为 None（fixture 当前 detail 数据
        不带 wenhao / fzrq，docx 题注也无文号）—— 即对现有 fixture 输出
        语义零回归。
        """

        fixture_path = (
            Path(__file__).resolve().parent.parent
            / "data"
            / "fixtures"
            / "contract_chapter_interpretation_2023.json"
        )
        fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
        # fixture 落盘时已经是 canonicalize 后的 payload；本回归只确认其
        # document_number / repealed_at 都为 None，与本 PR canonicalize_flk_npc
        # 无法从 detail_data 找到这两个字段时的输出一致。
        self.assertIsNone(fixture["document_number"])
        self.assertIsNone(fixture["repealed_at"])

        # 再用同 fixture 的核心 metadata 合成 detail_payload + 模拟 docx，
        # 跑 canonicalize_flk_npc，验证输出仍为 None / 未漂移。
        synthetic_detail = _flk_detail_payload(
            bbbs="ff8081818c24e05b018c814e6de45ab5",
            title=fixture["title"],
            flxz="司法解释",
            zdjgName=fixture["issuing_body"],
            gbrq=fixture["released_at"],
            sxrq=fixture["effective_at"],
            sxx=3,
        )
        with patch.object(
            cleaning,
            "parse_articles_from_word_bytes",
            return_value=[
                {
                    "number": "1",
                    "number_display": "第一条",
                    "text": "回归占位正文。",
                    "part": None,
                    "position": 1,
                }
            ],
        ):
            payload = cleaning.canonicalize(
                synthetic_detail,
                source_kind="flk_npc_detail",
                bbbs="ff8081818c24e05b018c814e6de45ab5",
                docx_bytes=_minimal_docx(),
                checked_at=fixture["source_checked_at"],
            )
        self.assertIsNone(payload["document_number"])
        self.assertIsNone(payload["repealed_at"])
        # released_at / effective_at 字段同步未漂移
        self.assertEqual(payload["released_at"], fixture["released_at"])
        self.assertEqual(payload["effective_at"], fixture["effective_at"])


class FlkNpcMetadataPriorityTests(unittest.TestCase):
    """detail_data 候选键 vs docx 抽取的优先级，确保 detail JSON 优先。"""

    def test_detail_data_wenhao_takes_priority_over_docx(self):
        """detail_data['wenhao'] 与 docx 题注同时含 ``DOCUMENT_NUMBER_INLINE_RE``
        覆盖范围内的文号时，以 detail_data 为权威——这与 court_gongbao /
        spp_gov_cn adapter 把 metadata 视作最终 truth（cleaning
        ``_canonicalize_local_text_payload`` 经
        ``metadata.get('document_number')`` 读）的路径对称。
        """

        docx_bytes = _make_docx_bytes(
            [
                {"bookmark": "题注", "text": "法释〔2023〕99号"},
                {"bookmark": "中华人民共和国示例法", "text": "中华人民共和国示例法"},
                {"bookmark": "第一条", "text": "第一条 示例正文。"},
            ]
        )
        payload = cleaning.canonicalize(
            _flk_detail_payload(wenhao="法释〔2023〕13号"),
            source_kind="flk_npc_detail",
            bbbs="law-flk-meta",
            docx_bytes=docx_bytes,
            checked_at="2026-05-05T00:00:00+00:00",
        )
        # 以 detail_data['wenhao'] 为准，docx 中的 99 号被忽略。
        self.assertEqual(payload["document_number"], "法释〔2023〕13号")


class FlkNpcDocumentNumberPreambleBoundaryTests(unittest.TestCase):
    """PR-A.1：锁定 ``_extract_document_number_from_docx_bytes`` 仅扫 preamble。

    PR-A 在 ``b779ab0`` squash merge 时已带入第二个 commit
    ``fix(cleaning): limit flk document number extraction to preamble``，
    实测 master HEAD 的 ``_metadata_preamble_text`` /
    ``_metadata_preamble_text_from_lines`` 已在第一个 ``ARTICLE_RE`` /
    ``_is_structural_heading`` / ``目录`` marker 处截断。

    PR-A 主测试类 ``FlkNpcDocumentNumberTests`` 已经覆盖 DOCX zip 路径下的
    "正文引用文号不得误抽"（``test_document_number_ignores_article_body_citations``）。
    本类追加 PR-A 主测试类未触及的 4 个边界 case，把 preamble-only
    invariant 在 OLE legacy ``.doc`` 路径与边界形态上钉住。

    详见 ``docs/CLEANING_FLK_PREAMBLE_ONLY_SPEC.md``（codex P2 fixup）。
    """

    def test_document_number_from_legacy_doc_preamble(self):
        """OLE legacy ``.doc`` 路径正面：题注含 ``法释〔2023〕13号`` → 抽出。

        ``_extract_document_number_from_docx_bytes`` 进入 ``OLE_WORD_MAGIC``
        分支，调 ``_convert_legacy_doc_to_text`` 把 OLE bytes 转成文本，
        再 ``splitlines()`` 喂给 ``_metadata_preamble_text_from_lines``。
        与 DOCX zip 路径走同一个 preamble 切片函数，invariant 同步生效。

        测试用 ``patch`` 注入 textutil / antiword 的转出文本，避免对 host
        工具依赖（与 ``test_corrupt_docx_falls_back_to_none`` patch
        ``parse_articles_from_word_bytes`` 同型）。
        """

        legacy_bytes = cleaning.OLE_WORD_MAGIC + b"\x00" * 64
        converted_text = (
            "最高人民法院关于示例问题的解释\n"
            "法释〔2023〕13号\n"
            "第一条 示例正文。\n"
        )
        with patch.object(
            cleaning, "_convert_legacy_doc_to_text", return_value=converted_text
        ), patch.object(
            cleaning,
            "parse_articles_from_word_bytes",
            return_value=[
                {
                    "number": "1",
                    "number_display": "第一条",
                    "text": "示例正文。",
                    "part": None,
                    "position": 1,
                }
            ],
        ):
            payload = cleaning.canonicalize(
                _flk_detail_payload(),
                source_kind="flk_npc_detail",
                bbbs="law-flk-meta",
                docx_bytes=legacy_bytes,
                checked_at="2026-05-05T00:00:00+00:00",
            )
        self.assertEqual(payload["document_number"], "法释〔2023〕13号")

    def test_document_number_legacy_doc_ignores_article_body_citations(self):
        """OLE legacy ``.doc`` 路径负面：题注无文号、正文引用其它文件文号 → None。

        镜像 DOCX zip 路径的 ``test_document_number_ignores_article_body_citations``。
        ``_metadata_preamble_text_from_lines`` 在 ``第一条`` 处 ``break``，
        preamble 切片不含正文 → ``extract_document_number`` 返回 None。
        防止把其它文件文号误索引成本法规文号、污染
        ``document_number_index`` / ``fetch "<文号>"`` 路径。
        """

        legacy_bytes = cleaning.OLE_WORD_MAGIC + b"\x00" * 64
        converted_text = (
            "中华人民共和国示例法\n"
            "第一条 本条引用法释〔2023〕13号作为示例。\n"
        )
        with patch.object(
            cleaning, "_convert_legacy_doc_to_text", return_value=converted_text
        ), patch.object(
            cleaning,
            "parse_articles_from_word_bytes",
            return_value=[
                {
                    "number": "1",
                    "number_display": "第一条",
                    "text": "本条引用法释〔2023〕13号作为示例。",
                    "part": None,
                    "position": 1,
                }
            ],
        ):
            payload = cleaning.canonicalize(
                _flk_detail_payload(),
                source_kind="flk_npc_detail",
                bbbs="law-flk-meta",
                docx_bytes=legacy_bytes,
                checked_at="2026-05-05T00:00:00+00:00",
            )
        self.assertIsNone(payload["document_number"])

    def test_document_number_empty_preamble_when_first_paragraph_is_article(self):
        """边界：题注 / 文号段落完全缺席，第一段就是 ``第一条 ...``。

        ``_metadata_preamble_text_from_lines`` 第一轮就在 ``ARTICLE_RE.match``
        命中、立即 ``break``，``preamble`` list 为空 → join 后是 ``""`` →
        ``extract_document_number("")`` 走 ``not text`` 分支返回 None；
        不抛错，与硬编码 None 退化兼容。

        本测试还故意把 ``法释〔2023〕13号`` 字符串放到第一条的正文里，
        验证即使正文存在合规文号子串也不会被错抽（与
        ``test_document_number_ignores_article_body_citations`` 形成"完全
        无前导段落"边界变体）。
        """

        docx_bytes = _make_docx_bytes(
            [
                {
                    "bookmark": "第一条",
                    "text": "第一条 这条引用文号格式法释〔2023〕13号。",
                },
            ]
        )
        payload = cleaning.canonicalize(
            _flk_detail_payload(),
            source_kind="flk_npc_detail",
            bbbs="law-flk-meta",
            docx_bytes=docx_bytes,
            checked_at="2026-05-05T00:00:00+00:00",
        )
        self.assertIsNone(payload["document_number"])

    def test_document_number_directory_marker_breaks_preamble(self):
        """边界：题注后接 ``目录`` paragraph，目录条目含合规文号字符串 → None。

        ``目录`` 是 ``_metadata_preamble_text_from_lines`` 显式声明的
        三个截断点之一（另两个是 ``ARTICLE_RE`` / ``_is_structural_heading``）。
        flk 司法解释 docx 实际下载形态：有时题注 / 标题之后会出现独立的
        ``目录`` paragraph，再列举各章节作为目录条目。

        本测试构造的 docx：标题（"最高人民法院关于示例问题的解释"，不含合规文号）
        → ``目录`` paragraph → ``法释〔2023〕13号`` 作为一条目录条目 →
        ``第一章 总则`` → ``第一条 示例正文``。

        期望：preamble 在 ``目录`` 截断 → 切片只含 "最高人民法院关于
        示例问题的解释"（不含合规文号） → ``extract_document_number``
        返回 None；目录条目里的 ``法释〔2023〕13号`` 不被吃进 preamble。
        """

        docx_bytes = _make_docx_bytes(
            [
                {
                    "bookmark": "题注",
                    "text": "最高人民法院关于示例问题的解释",
                },
                {"bookmark": "目录", "text": "目录"},
                # 目录之后的"目录条目"含合规文号字符串：
                # 若 preamble 切片**未**在"目录"处截断，会被错抽成本法规文号。
                {"bookmark": "目录条目", "text": "法释〔2023〕13号"},
                {"bookmark": "第一章 总则", "text": "第一章 总则"},
                {"bookmark": "第一条", "text": "第一条 示例正文。"},
            ]
        )
        payload = cleaning.canonicalize(
            _flk_detail_payload(),
            source_kind="flk_npc_detail",
            bbbs="law-flk-meta",
            docx_bytes=docx_bytes,
            checked_at="2026-05-05T00:00:00+00:00",
        )
        self.assertIsNone(payload["document_number"])


if __name__ == "__main__":
    unittest.main()
