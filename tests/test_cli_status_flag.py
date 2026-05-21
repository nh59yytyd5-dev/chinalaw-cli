"""PR-B 守门测试 — CLI ``--status`` flag for fetch / discover。

详见 ``docs/CLI_STATUS_FLAG_SPEC.md`` §6.1。

四组守门：

1. ``CliFetchStatusParseTests`` —— argparse 层：``--status repealed`` 解析
   正确 / default None / 非法值被 argparse choices 拦截到 SystemExit(2)。
2. ``FetchLawStatusFilterTests`` —— ``fetch_law`` 业务层：status → sxx →
   adapter 透传 / current-only 源边界 / 非 flk 源 fail loud / 默认行为不传 sxx。
3. ``DiscoverLawsTests`` —— ``discover_laws`` 模块：核心路径 / status fail
   loud / 默认空 query。
4. ``StatusToSxxTests`` —— ``sources.status_to_sxx`` 单元 + 与
   ``cleaning.SXX_TO_STATUS`` 反向对称守门。
"""

from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from chinalaw import cleaning, sources
from chinalaw import discover as discover_mod
from chinalaw import fetch as fetch_mod
from chinalaw.cli import app, build_parser


def _make_payload(*, bbbs: str = "law-1", title: str = "中华人民共和国示例法") -> dict:
    return {
        "id": bbbs,
        "title": title,
        "short_title": title.replace("中华人民共和国", "")[:6] or None,
        "aliases": ["示例法"],
        "level": "law",
        "status": "current",
        "issuing_body": "全国人民代表大会",
        "document_number": None,
        "released_at": "2026-01-01",
        "effective_at": "2026-02-01",
        "repealed_at": None,
        "source_url": f"https://flk.npc.gov.cn/detail?id={bbbs}",
        "source_name": "flk.npc.gov.cn",
        "source_checked_at": "2026-04-27T00:00:00+00:00",
        "source_hash": f"hash-{bbbs}",
        "articles": [
            {"number": "1", "number_display": "第一条", "text": "示例正文。"},
        ],
    }


class _StatusAwareFakeAdapter:
    """记录 search_list 收到的 kwargs，用于断言 sxx 是否被透传。"""

    def __init__(self, rows: list[dict] | None = None, payloads: dict[str, dict] | None = None):
        self._rows = rows if rows is not None else [
            {"bbbs": "law-1", "title": "中华人民共和国示例法"}
        ]
        self._payloads = payloads or {}
        self.search_calls: list[tuple[str, dict]] = []

    def search_list(self, query, **kwargs):
        # 与 _FakeAdapter（test_fetch.py）签名兼容：page_size 必需 + sxx 可选。
        self.search_calls.append((query, dict(kwargs)))
        return {"code": 200, "rows": self._rows}

    def build_law_payload(self, bbbs, search_row=None):
        if bbbs not in self._payloads:
            return _make_payload(bbbs=bbbs)
        return self._payloads[bbbs]


# ---------------------------------------------------------------------------
# 1. CLI argparse 层
# ---------------------------------------------------------------------------


class CliFetchStatusParseTests(unittest.TestCase):
    """argparse 解析 ``--status``：合法值 / default / 非法值。"""

    def test_fetch_status_repealed_parsed(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            ["fetch", "示例法", "--status", "repealed", "--list-matches"]
        )
        self.assertEqual(args.status, "repealed")

    def test_fetch_status_default_is_none(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["fetch", "示例法", "--list-matches"])
        self.assertIsNone(args.status)

    def test_fetch_invalid_status_rejected_by_argparse(self) -> None:
        """argparse choices 拦截 → SystemExit(2)；不会进入 fetch_law。"""

        parser = build_parser()
        with self.assertRaises(SystemExit) as ctx, redirect_stderr(io.StringIO()):
            parser.parse_args(
                ["fetch", "示例法", "--status", "invalid", "--list-matches"]
            )
        self.assertEqual(ctx.exception.code, 2)

    def test_fetch_all_four_status_keywords_accepted(self) -> None:
        parser = build_parser()
        for status in ("repealed", "amended", "current", "pending_effective"):
            args = parser.parse_args(
                ["fetch", "示例法", "--status", status, "--list-matches"]
            )
            self.assertEqual(args.status, status)

    def test_discover_status_repealed_parsed(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["discover", "--status", "repealed"])
        self.assertEqual(args.status, "repealed")
        self.assertEqual(args.source, "flk_npc")
        self.assertIsNone(args.query)
        self.assertEqual(args.limit, 20)


# ---------------------------------------------------------------------------
# 2. fetch_law 业务层
# ---------------------------------------------------------------------------


class FetchLawStatusFilterTests(unittest.TestCase):
    """fetch_law 透传 sxx / 非 flk fail loud / 默认不传 sxx。"""

    def _patch_adapter(self, adapter: _StatusAwareFakeAdapter):
        return patch("chinalaw.fetch.get_source_adapter", return_value=adapter)

    def test_flk_status_repealed_passes_sxx_one_to_adapter(self) -> None:
        """``--status repealed`` 经 sources.STATUS_TO_SXX 翻译为 sxx=[1]
        透传到 adapter.search_list。"""

        adapter = _StatusAwareFakeAdapter()
        with tempfile.TemporaryDirectory() as td, self._patch_adapter(adapter):
            db_path = Path(td) / "t.db"
            fetch_mod.fetch_law(
                db_path,
                "示例法",
                source="flk_npc",
                status="repealed",
                list_matches=True,
            )
        self.assertEqual(len(adapter.search_calls), 1)
        _, kwargs = adapter.search_calls[0]
        self.assertEqual(kwargs.get("sxx"), [1])

    def test_flk_no_status_does_not_pass_sxx(self) -> None:
        """default 行为：不传 ``status`` 时 search_kwargs 不含 sxx 键。"""

        adapter = _StatusAwareFakeAdapter()
        with tempfile.TemporaryDirectory() as td, self._patch_adapter(adapter):
            db_path = Path(td) / "t.db"
            fetch_mod.fetch_law(
                db_path,
                "示例法",
                source="flk_npc",
                list_matches=True,
            )
        self.assertEqual(len(adapter.search_calls), 1)
        _, kwargs = adapter.search_calls[0]
        self.assertNotIn("sxx", kwargs)

    def test_court_gongbao_with_status_raises_value_error(self) -> None:
        """方向 X：court_gongbao + status 必须 fail loud。"""

        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "t.db"
            with self.assertRaises(ValueError) as ctx:
                fetch_mod.fetch_law(
                    db_path,
                    "某解释",
                    source="court_gongbao",
                    status="repealed",
                    list_matches=True,
                )
            message = str(ctx.exception)
            self.assertIn("--status filter is not supported", message)
            self.assertIn("court_gongbao", message)
            # error message 必须列出 supported sources，让 agent 自学边界
            self.assertIn("flk_npc", message)

    def test_spp_gov_cn_with_status_raises_value_error(self) -> None:
        """方向 X：spp_gov_cn + status 必须 fail loud。"""

        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "t.db"
            with self.assertRaises(ValueError) as ctx:
                fetch_mod.fetch_law(
                    db_path,
                    "某检察意见",
                    source="spp_gov_cn",
                    status="repealed",
                    list_matches=True,
                )
            self.assertIn("spp_gov_cn", str(ctx.exception))

    def test_current_only_status_source_accepts_current_without_sxx(self) -> None:
        """current-only 源允许 --status current，但不向 adapter 传 sxx。"""

        for source_name in ("gov_xzfgk", "csrc_gov_cn"):
            adapter = _StatusAwareFakeAdapter()
            with tempfile.TemporaryDirectory() as td, self._patch_adapter(adapter):
                db_path = Path(td) / "t.db"
                fetch_mod.fetch_law(
                    db_path,
                    "示例法规",
                    source=source_name,
                    status="current",
                    list_matches=True,
                )
            _, kwargs = adapter.search_calls[0]
            self.assertNotIn("sxx", kwargs)

    def test_current_only_status_source_rejects_non_current(self) -> None:
        for source_name in ("gov_xzfgk", "csrc_gov_cn"):
            with tempfile.TemporaryDirectory() as td:
                db_path = Path(td) / "t.db"
                with self.assertRaises(ValueError) as ctx:
                    fetch_mod.fetch_law(
                        db_path,
                        "示例法规",
                        source=source_name,
                        status="repealed",
                        list_matches=True,
                    )
                self.assertIn(source_name, str(ctx.exception))

    def test_flk_amended_passes_sxx_two(self) -> None:
        """status=amended → sxx=[2]，验证完整映射不只 repealed=1。"""

        adapter = _StatusAwareFakeAdapter()
        with tempfile.TemporaryDirectory() as td, self._patch_adapter(adapter):
            db_path = Path(td) / "t.db"
            fetch_mod.fetch_law(
                db_path,
                "示例法",
                source="flk_npc",
                status="amended",
                list_matches=True,
            )
        _, kwargs = adapter.search_calls[0]
        self.assertEqual(kwargs.get("sxx"), [2])

    def test_status_bypasses_implicit_local_alias_hint(self) -> None:
        """``--status`` 是远程搜索过滤，不能被本地同名现行版本短路。

        场景：本地已有现行《公司法》，agent 要拉 ``--status amended`` 的历史
        修订版。如果复用本地 alias hint，会直接按当前 bbbs 取数，完全绕过
        sxx=[2]。
        """

        from chinalaw.db import connect, migrate
        from chinalaw.loader import load_law_from_dict

        current_payload = {
            **_make_payload(
                bbbs="current-bbbs",
                title="中华人民共和国公司法",
            ),
            "id": "flk-company-law-2023",
            "short_title": "公司法",
            "aliases": ["公司法"],
            "released_at": "2023-12-29",
            "effective_at": "2024-07-01",
            "source_hash": "current-hash",
        }
        old_payload = {
            **_make_payload(
                bbbs="old-bbbs",
                title="中华人民共和国公司法",
            ),
            "short_title": "公司法",
            "aliases": ["公司法"],
            "status": "amended",
            "released_at": "2018-10-26",
            "effective_at": "2018-10-26",
            "source_hash": "old-hash",
        }
        adapter = _StatusAwareFakeAdapter(
            rows=[
                {
                    "bbbs": "old-bbbs",
                    "title": "中华人民共和国公司法",
                    "sxx": 2,
                    "gbrq": "2018-10-26",
                }
            ],
            payloads={"old-bbbs": old_payload},
        )

        with tempfile.TemporaryDirectory() as td, self._patch_adapter(adapter):
            db_path = Path(td) / "t.db"
            with connect(db_path) as conn:
                migrate(conn)
                load_law_from_dict(conn, current_payload)
            result = fetch_mod.fetch_law(
                db_path,
                "公司法",
                source="flk_npc",
                status="amended",
            )

        self.assertEqual(len(adapter.search_calls), 1)
        _, kwargs = adapter.search_calls[0]
        self.assertEqual(kwargs.get("sxx"), [2])
        self.assertEqual(result["matched_bbbs"], "old-bbbs")
        self.assertEqual(result["law"]["status"], "amended")


class CliHandleFetchStatusErrorTests(unittest.TestCase):
    """CLI 层 ``_handle_fetch`` 捕获 ValueError → exit 2 + 结构化错误 payload。"""

    def test_court_gongbao_status_emits_law_fetch_error_exit_two(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "t.db"
            buf = io.StringIO()
            with redirect_stdout(buf):
                exit_code = app(
                    [
                        "--db",
                        str(db_path),
                        "fetch",
                        "x",
                        "--source",
                        "court_gongbao",
                        "--status",
                        "repealed",
                        "--list-matches",
                        "--format",
                        "json",
                    ]
                )
            self.assertEqual(exit_code, 2)
            output = buf.getvalue()
            self.assertIn("law_fetch_error", output)
            self.assertIn("ValueError", output)
            self.assertIn("--status filter is not supported", output)


# ---------------------------------------------------------------------------
# 3. discover_laws
# ---------------------------------------------------------------------------


class DiscoverLawsTests(unittest.TestCase):
    """discover_laws 透传 sxx / 非 flk 源拒绝 / 默认不传 sxx。"""

    def _patch_adapter(self, adapter):
        return patch("chinalaw.discover.get_source_adapter", return_value=adapter)

    def test_discover_status_repealed_passes_sxx_one(self) -> None:
        adapter = _StatusAwareFakeAdapter(
            rows=[
                {
                    "bbbs": "old-contract",
                    "title": "中华人民共和国合同法",
                    "sxx": 1,
                    "gbrq": "1999-03-15",
                }
            ]
        )
        with self._patch_adapter(adapter):
            result = discover_mod.discover_laws(
                source="flk_npc", status="repealed", limit=5
            )
        self.assertEqual(result["kind"], "law_discover_candidates")
        self.assertEqual(result["status"], "repealed")
        self.assertEqual(result["query"], "")
        self.assertEqual(len(result["candidates"]), 1)
        self.assertEqual(result["candidates"][0]["title"], "中华人民共和国合同法")
        # sxx 必须被透传
        _, kwargs = adapter.search_calls[0]
        self.assertEqual(kwargs.get("sxx"), [1])

    def test_discover_default_no_status_no_sxx(self) -> None:
        adapter = _StatusAwareFakeAdapter()
        with self._patch_adapter(adapter):
            discover_mod.discover_laws(source="flk_npc")
        _, kwargs = adapter.search_calls[0]
        self.assertNotIn("sxx", kwargs)

    def test_discover_orders_by_released_at_desc(self) -> None:
        """discover 是候选池入口，空 query 必须按公布日倒序稳定输出。"""

        adapter = _StatusAwareFakeAdapter()
        with self._patch_adapter(adapter):
            discover_mod.discover_laws(source="flk_npc")
        _, kwargs = adapter.search_calls[0]
        self.assertEqual(kwargs.get("order"), "gbrq")
        self.assertEqual(kwargs.get("sort"), "DESC")

    def test_discover_court_gongbao_raises(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            discover_mod.discover_laws(source="court_gongbao", status="repealed")
        self.assertIn("court_gongbao", str(ctx.exception))

    def test_discover_unsupported_source_raises(self) -> None:
        """source 不在 DISCOVER_SOURCES 时即使 status=None 也拒绝。"""

        with self.assertRaises(ValueError) as ctx:
            discover_mod.discover_laws(source="court_gongbao")
        self.assertIn("does not support source", str(ctx.exception))

    def test_discover_query_passed_through(self) -> None:
        """query 字符串被 search_list 接收为第一位置参数。"""

        adapter = _StatusAwareFakeAdapter()
        with self._patch_adapter(adapter):
            discover_mod.discover_laws(source="flk_npc", query="合同法")
        query_arg, _kwargs = adapter.search_calls[0]
        self.assertEqual(query_arg, "合同法")

    def test_discover_current_only_status_source_accepts_current(self) -> None:
        for source_name in ("gov_xzfgk", "csrc_gov_cn"):
            adapter = _StatusAwareFakeAdapter()
            with self._patch_adapter(adapter):
                result = discover_mod.discover_laws(
                    source=source_name,
                    query="信息披露",
                    status="current",
                    limit=5,
                )
            self.assertEqual(result["source"], source_name)
            _, kwargs = adapter.search_calls[0]
            self.assertNotIn("sxx", kwargs)
            self.assertNotIn("order", kwargs)
            self.assertNotIn("sort", kwargs)

    def test_discover_current_only_status_source_rejects_non_current(self) -> None:
        for source_name in ("gov_xzfgk", "csrc_gov_cn"):
            with self.assertRaises(ValueError) as ctx:
                discover_mod.discover_laws(source=source_name, status="repealed")
            self.assertIn(source_name, str(ctx.exception))


# ---------------------------------------------------------------------------
# 4. STATUS_TO_SXX / status_to_sxx 单元
# ---------------------------------------------------------------------------


class StatusToSxxTests(unittest.TestCase):
    """sources.status_to_sxx 单元 + 与 cleaning.SXX_TO_STATUS 反向对称。"""

    def test_repealed_maps_to_one(self) -> None:
        self.assertEqual(sources.status_to_sxx("repealed"), 1)

    def test_amended_maps_to_two(self) -> None:
        self.assertEqual(sources.status_to_sxx("amended"), 2)

    def test_current_maps_to_three(self) -> None:
        self.assertEqual(sources.status_to_sxx("current"), 3)

    def test_pending_effective_maps_to_four(self) -> None:
        self.assertEqual(sources.status_to_sxx("pending_effective"), 4)

    def test_unknown_status_raises_value_error(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            sources.status_to_sxx("garbage")
        message = str(ctx.exception)
        self.assertIn("unknown status", message)
        # error 列出合法值，便于调用方自检
        for status in ("repealed", "amended", "current", "pending_effective"):
            self.assertIn(status, message)

    def test_status_to_sxx_is_inverse_of_sxx_to_status(self) -> None:
        """单点不变量：sources.STATUS_TO_SXX 必须由 cleaning.SXX_TO_STATUS
        反向派生，保证未来加 sxx 值时正反两路不漂移。"""

        for sxx, status in cleaning.SXX_TO_STATUS.items():
            self.assertEqual(sources.STATUS_TO_SXX[status], sxx)
        self.assertEqual(
            len(sources.STATUS_TO_SXX), len(cleaning.SXX_TO_STATUS)
        )

    def test_status_filter_supported_contains_only_flk(self) -> None:
        """多源对照矩阵守门（spec §1.1）：只有 flk_npc 原生支持过滤。"""

        self.assertEqual(sources.STATUS_FILTER_SUPPORTED, frozenset({"flk_npc"}))
        self.assertEqual(
            sources.CURRENT_ONLY_STATUS_SOURCES,
            frozenset(
                {
                    "gov_xzfgk",
                    "csrc_gov_cn",
                    "bse_cn",
                    "sse_com_cn",
                    "szse_cn",
                    "chinaclear_cn",
                    "sac_net_cn",
                }
            ),
        )

    def test_discover_sources_include_searchable_sources(self) -> None:
        """discover 支持 flk_npc 与具备站内搜索/列表语义的公开源。"""

        self.assertEqual(
            discover_mod.DISCOVER_SOURCES,
            (
                "flk_npc",
                "gov_xzfgk",
                "csrc_gov_cn",
                "bse_cn",
                "sse_com_cn",
                "szse_cn",
                "chinaclear_cn",
                "sac_net_cn",
            ),
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
