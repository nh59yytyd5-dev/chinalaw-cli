"""identity.law_row_matches_payload strict / loose 守门测试。

详见 ``docs/FETCH_LAYER_SPEC.md`` §2。
"""

from __future__ import annotations

import json
import unittest

from chinalaw.identity import law_row_matches_payload


class LawRowMatchesPayloadTests(unittest.TestCase):
    def test_strict_rejects_cross_source(self):
        """同名但 source_name 不同 → strict=True 拒；strict=False 收。"""

        row = {
            "title": "X",
            "source_name": "flk.npc.gov.cn",
            "short_title": None,
            "aliases": "[]",
            "released_at": "2020-01-01",
            "effective_at": "2020-01-02",
        }
        payload = {
            "title": "X",
            "source_name": "court.gov.cn",
            "released_at": "2020-01-01",
            "effective_at": "2020-01-02",
        }
        self.assertFalse(law_row_matches_payload(row, payload, strict=True))
        self.assertTrue(law_row_matches_payload(row, payload, strict=False))

    def test_strict_rejects_different_revision_dates(self):
        """同名同源但 released_at 不同 → strict 拒（修订版）。"""

        row = {
            "title": "X",
            "source_name": "flk.npc.gov.cn",
            "short_title": None,
            "aliases": "[]",
            "released_at": "2018-08-01",
            "effective_at": "2018-08-01",
        }
        payload = {
            "title": "X",
            "source_name": "flk.npc.gov.cn",
            "released_at": "2023-12-29",
            "effective_at": "2024-07-01",
        }
        self.assertFalse(law_row_matches_payload(row, payload, strict=True))
        # 宽松路径：用户搜"X"不应因日期对不上被拒
        self.assertTrue(law_row_matches_payload(row, payload, strict=False))

    def test_disjoint_names_always_false(self):
        """名称完全无交集 → 两路都拒。"""

        row = {
            "title": "公司法",
            "short_title": None,
            "aliases": "[]",
            "source_name": None,
            "released_at": None,
            "effective_at": None,
        }
        payload = {
            "title": "证券法",
            "source_name": None,
            "released_at": None,
            "effective_at": None,
        }
        self.assertFalse(law_row_matches_payload(row, payload, strict=True))
        self.assertFalse(law_row_matches_payload(row, payload, strict=False))

    def test_alias_intersection(self):
        """名称通过 aliases 列表交集命中（不仅 title）。"""

        row = {
            "title": "中华人民共和国公司法",
            "short_title": "公司法",
            "aliases": json.dumps(["公司法", "Company Law"]),
            "source_name": "flk.npc.gov.cn",
            "released_at": "2023-12-29",
            "effective_at": "2024-07-01",
        }
        payload = {
            "title": "Company Law",
            "source_name": "flk.npc.gov.cn",
            "released_at": "2023-12-29",
            "effective_at": "2024-07-01",
        }
        self.assertTrue(law_row_matches_payload(row, payload, strict=True))


if __name__ == "__main__":
    unittest.main()
