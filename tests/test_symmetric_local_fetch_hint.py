"""PR5b 守门测试 — `_resolve_local_fetch_hint` 多源对称 + flk-only helper 收口。

详见 ``docs/SYMMETRIC_LOCAL_FETCH_HINT_SPEC.md`` §6.1。

三组守门：

1. ``ResolveLocalFetchHintTests``：每源一个正例 + unknown source / 跨源
   marker 不匹配 / row 缺失 / DB 缺失 / law_id fallback 等退化路径。
2. ``FlkOnlyHelpersDeprecatedTests``：三个 flk-only helper 函数体已删除，
   ``hasattr`` 不命中。
3. ``SourceNameMarkersTests``：``SOURCE_NAME_MARKERS`` 常量包含多源 + 值与
   ``adapters/*.py`` 写入侧字面量一致。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from chinalaw import fetch
from chinalaw.db import connect, migrate
from chinalaw.loader import load_law_from_dict


def _build_payload(
    *,
    law_id: str,
    title: str,
    source_name: str,
    source_url: str,
    short_title: str | None = None,
    aliases: list[str] | None = None,
    status: str = "current",
) -> dict:
    """构造一个最小可入库的 payload。articles 至少 1 条（loader 约束）。"""

    return {
        "id": law_id,
        "title": title,
        "short_title": short_title or title,
        "aliases": aliases or [title],
        "level": "law",
        "status": status,
        "source_url": source_url,
        "source_name": source_name,
        "source_checked_at": "2026-05-05T00:00:00+00:00",
        "source_hash": f"stub-{law_id}",
        "released_at": "2024-01-01",
        "articles": [
            {"number": "1", "number_display": "第一条", "text": "总则。"},
        ],
    }


def _seed(db_path: Path, payload: dict) -> None:
    with connect(db_path) as conn:
        migrate(conn)
        load_law_from_dict(conn, payload)


class ResolveLocalFetchHintTests(unittest.TestCase):
    """多源对称：每源一个正例 + 退化路径。"""

    # 1. 每源一个正例 ------------------------------------------------------

    def test_flk_npc_returns_hint_with_bbbs_compat_field(self) -> None:
        """FLK 路径必须仍然写 ``hint["bbbs"]`` 兼容字段（fetch 主流程历史
        消费方式）。"""

        flk_bbbs = "abc1234567890abcdef12345"
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "t.db"
            _seed(
                db,
                _build_payload(
                    law_id="flk-test-civil-code",
                    title="中华人民共和国民法典",
                    short_title="民法典",
                    aliases=["民法典"],
                    source_name="flk.npc.gov.cn",
                    source_url=(
                        f"https://flk.npc.gov.cn/detail2.html?id={flk_bbbs}"
                    ),
                ),
            )
            hint = fetch._resolve_local_fetch_hint(db, "民法典", "flk_npc")
            self.assertIsNotNone(hint)
            self.assertEqual(hint["id"], flk_bbbs)
            self.assertEqual(hint["detail_id"], flk_bbbs)
            self.assertEqual(hint["bbbs"], flk_bbbs)  # FLK 兼容字段
            self.assertEqual(hint["title"], "中华人民共和国民法典")
            self.assertTrue(hint["local_alias_resolved"])

    def test_court_gongbao_returns_hint_without_bbbs_field(self) -> None:
        """court_gongbao 路径**不写** ``hint["bbbs"]``（court 没有 bbbs 概念）。"""

        court_id = "abcdef1234567890abcdef1234567890"
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "t.db"
            _seed(
                db,
                _build_payload(
                    law_id=f"court_gongbao:{court_id}",
                    title="某某指导案例",
                    short_title="某案",
                    aliases=["某案"],
                    source_name="gongbao.court.gov.cn",
                    source_url=(
                        f"https://gongbao.court.gov.cn/Details/{court_id}.html"
                    ),
                ),
            )
            hint = fetch._resolve_local_fetch_hint(db, "某案", "court_gongbao")
            self.assertIsNotNone(hint)
            self.assertEqual(hint["id"], court_id)
            self.assertEqual(hint["detail_id"], court_id)
            self.assertNotIn("bbbs", hint)

    def test_court_main_returns_hint_without_bbbs_field(self) -> None:
        """court_main detail_id 是 ``channel/xiangqing/id``，同样不写 bbbs。"""

        court_id = "zixun/xiangqing/499051"
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "t.db"
            _seed(
                db,
                _build_payload(
                    law_id=f"court_main:{court_id}",
                    title="最高人民法院关于审理示例案件适用法律若干问题的解释（二）",
                    short_title="审理示例案件解释二",
                    aliases=["示例案件解释二"],
                    source_name="www.court.gov.cn",
                    source_url=f"https://www.court.gov.cn/{court_id}.html",
                ),
            )
            hint = fetch._resolve_local_fetch_hint(
                db, "示例案件解释二", "court_main"
            )
            self.assertIsNotNone(hint)
            self.assertEqual(hint["id"], court_id)
            self.assertEqual(hint["detail_id"], court_id)
            self.assertNotIn("bbbs", hint)

    def test_spp_gov_cn_returns_hint_without_bbbs_field(self) -> None:
        """spp_gov_cn 路径同样不写 bbbs，detail_id 是 path fragment。"""

        spp_path = "xwfbh/wsfbt/202501/t20250116_679579"
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "t.db"
            _seed(
                db,
                _build_payload(
                    law_id=f"spp_gov_cn:{spp_path}",
                    title="某某检察意见",
                    short_title="某检察意见",
                    aliases=["某检察意见"],
                    source_name="spp.gov.cn",
                    source_url=f"https://www.spp.gov.cn/{spp_path}.shtml",
                ),
            )
            hint = fetch._resolve_local_fetch_hint(
                db, "某检察意见", "spp_gov_cn"
            )
            self.assertIsNotNone(hint)
            self.assertEqual(hint["id"], spp_path)
            self.assertEqual(hint["detail_id"], spp_path)
            self.assertNotIn("bbbs", hint)

    # 2. 退化路径 ----------------------------------------------------------

    def test_unknown_source_returns_none(self) -> None:
        """source 不在 ``SOURCE_NAME_MARKERS`` → 直接 None，不查 DB。"""

        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "t.db"
            _seed(
                db,
                _build_payload(
                    law_id="flk-x",
                    title="X",
                    source_name="flk.npc.gov.cn",
                    source_url="https://flk.npc.gov.cn/detail2.html?id=abc1234567890abcdef12345",
                ),
            )
            self.assertIsNone(
                fetch._resolve_local_fetch_hint(db, "X", "unknown_source")
            )

    def test_source_name_marker_mismatch_returns_none(self) -> None:
        """row 是 court_gongbao 但请求 spp_gov_cn → marker 校验失败返回 None。

        防止"俗称模糊命中跨源 row"时把 court 的 detail_id 喂给 spp 主流程。
        """

        court_id = "abcdef1234567890abcdef1234567890"
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "t.db"
            _seed(
                db,
                _build_payload(
                    law_id=f"court_gongbao:{court_id}",
                    title="某指导案例",
                    short_title="某案",
                    aliases=["某案"],
                    source_name="gongbao.court.gov.cn",
                    source_url=(
                        f"https://gongbao.court.gov.cn/Details/{court_id}.html"
                    ),
                ),
            )
            # row 实际是 court，但请求 spp → marker 不匹配
            self.assertIsNone(
                fetch._resolve_local_fetch_hint(db, "某案", "spp_gov_cn")
            )

    def test_returns_none_when_db_missing(self) -> None:
        """DB 文件不存在 → 返回 None，不创建新 DB。"""

        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "missing.db"
            self.assertIsNone(
                fetch._resolve_local_fetch_hint(db, "X", "flk_npc")
            )
            # 不应顺带创建 DB
            self.assertFalse(db.exists())

    def test_returns_none_when_row_missing(self) -> None:
        """row 不在 DB → 返回 None。"""

        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "t.db"
            with connect(db) as conn:
                migrate(conn)
            # 空 DB（仅 schema，无 laws row）
            self.assertIsNone(
                fetch._resolve_local_fetch_hint(db, "不存在的法", "flk_npc")
            )


class FlkOnlyHelpersDeprecatedTests(unittest.TestCase):
    """三个 flk-only helper 函数体已删除守门。"""

    def test_extract_flk_bbbs_attribute_absent(self) -> None:
        self.assertFalse(hasattr(fetch, "_extract_flk_bbbs"))

    def test_raw_flk_bbbs_from_id_attribute_absent(self) -> None:
        self.assertFalse(hasattr(fetch, "_raw_flk_bbbs_from_id"))

    def test_looks_like_flk_bbbs_attribute_absent(self) -> None:
        self.assertFalse(hasattr(fetch, "_looks_like_flk_bbbs"))


class SourceNameMarkersTests(unittest.TestCase):
    """``SOURCE_NAME_MARKERS`` 常量约束守门。"""

    def test_fetch_sources_present(self) -> None:
        self.assertEqual(
            set(fetch.SOURCE_NAME_MARKERS.keys()),
            {
                "flk_npc",
                "court_gongbao",
                "court_main",
                "gov_xzfgk",
                "spp_gov_cn",
                "csrc_gov_cn",
                "bse_cn",
                "sse_com_cn",
                "szse_cn",
                "chinaclear_cn",
                "sac_net_cn",
            },
        )

    def test_marker_values_match_adapter_writes(self) -> None:
        """marker 值与 ``adapters/*.py``
        写入侧 ``source_name=...`` 字面量逐字一致；任何一侧改动须双向同步。
        """

        self.assertEqual(
            fetch.SOURCE_NAME_MARKERS["flk_npc"], "flk.npc.gov.cn"
        )
        self.assertEqual(
            fetch.SOURCE_NAME_MARKERS["court_gongbao"], "gongbao.court.gov.cn"
        )
        self.assertEqual(
            fetch.SOURCE_NAME_MARKERS["court_main"], "www.court.gov.cn"
        )
        self.assertEqual(
            fetch.SOURCE_NAME_MARKERS["gov_xzfgk"], "xzfg.moj.gov.cn"
        )
        self.assertEqual(
            fetch.SOURCE_NAME_MARKERS["spp_gov_cn"], "spp.gov.cn"
        )
        self.assertEqual(
            fetch.SOURCE_NAME_MARKERS["csrc_gov_cn"], "www.csrc.gov.cn"
        )
        self.assertEqual(
            fetch.SOURCE_NAME_MARKERS["bse_cn"], "www.bse.cn"
        )
        self.assertEqual(
            fetch.SOURCE_NAME_MARKERS["sse_com_cn"], "www.sse.com.cn"
        )
        self.assertEqual(
            fetch.SOURCE_NAME_MARKERS["szse_cn"], "www.szse.cn"
        )
        self.assertEqual(
            fetch.SOURCE_NAME_MARKERS["chinaclear_cn"], "www.chinaclear.cn"
        )
        self.assertEqual(
            fetch.SOURCE_NAME_MARKERS["sac_net_cn"], "www.sac.net.cn"
        )

    def test_keys_match_fetch_sources_tuple(self) -> None:
        """``SOURCE_NAME_MARKERS`` key 必须与 ``FETCH_SOURCES`` 值
        完全相等：marker dict 是 fetch sources 的语义超集（每源一个标签），
        漏一不可。"""

        self.assertEqual(
            set(fetch.SOURCE_NAME_MARKERS.keys()),
            set(fetch.FETCH_SOURCES),
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
