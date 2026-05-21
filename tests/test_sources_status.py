"""PR5a 守门测试 — sources._status_from_row 优先级 invariant。

``fetch._normalize_row_status`` 与 ``sources._status_from_row`` 修前优先级
相反；本 PR 选 sources 版（``sxx`` 优先）作权威，删 fetch 副本。本测试守
门 sxx 优先 + status 兜底 + 双空兜底三种路径。

详见 ``docs/ADAPTER_HTML_HELPERS_SPEC.md`` §2.3 / §3.6。
"""

from __future__ import annotations

import unittest

from chinalaw import sources


class SourcesStatusFromRowTests(unittest.TestCase):
    def test_sources_status_from_row_handles_flk_sxx(self) -> None:
        """FLK row 仅有 ``sxx`` int → 通过 SXX_TO_STATUS 映射。"""

        self.assertEqual(sources._status_from_row({"sxx": 1}), "repealed")
        self.assertEqual(sources._status_from_row({"sxx": 3}), "current")
        self.assertEqual(sources._status_from_row({"sxx": 4}), "pending_effective")

    def test_sources_status_from_row_handles_court_status(self) -> None:
        """court / spp row 仅有 ``status`` 字符串 → 直接透传。"""

        self.assertEqual(
            sources._status_from_row({"status": "current"}), "current"
        )
        self.assertEqual(
            sources._status_from_row({"status": "amended"}), "amended"
        )

    def test_sources_status_from_row_unknown_returns_unknown(self) -> None:
        """缺字段 / sxx 非整数 / status 空 → ``"unknown"``。"""

        self.assertEqual(sources._status_from_row({}), "unknown")
        self.assertEqual(sources._status_from_row({"status": ""}), "unknown")
        self.assertEqual(
            sources._status_from_row({"sxx": "garbage"}), "unknown"
        )
        # sxx 优先：即使 status 字段非空，sxx 存在就走 sxx 路径
        self.assertEqual(
            sources._status_from_row({"sxx": 3, "status": "amended"}),
            "current",
        )


class FetchHelpersConsolidatedTests(unittest.TestCase):
    """grep 自检：fetch.py 不应再 def 4 个收口 helper；attribute 应等同
    sources.* 同名 helper（identity 比较守门 import 路径漂移）。"""

    def test_fetch_imports_helpers_from_sources(self) -> None:
        from chinalaw import fetch

        # 1. 静态 attribute identity：fetch.* 与 sources.* 必须是同一对象
        self.assertIs(fetch._row_id, sources._row_id)
        self.assertIs(fetch._clean_title, sources._clean_title)
        self.assertIs(fetch._candidate_from_row, sources._candidate_from_row)

        # 2. 删除项：fetch._normalize_row_status 不再存在
        self.assertFalse(
            hasattr(fetch, "_normalize_row_status"),
            "fetch._normalize_row_status should be removed; "
            "sources._status_from_row is the single authority",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
