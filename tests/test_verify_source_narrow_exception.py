"""PR6 守门测试 — `sources.py::verify_source` 4 处 `except Exception` 收窄。

详见 ``docs/VERIFY_SOURCE_NARROW_EXCEPTION_SPEC.md`` §3.3。

立场：``except Exception as exc: ... return finish(False)`` 把真正的编程
bug（``AttributeError`` / ``NameError`` / ``TypeError``）静默吞成
"source 不可用"，与业务降级（网络断 / source 真挂）的现象**不可区分**。
本 PR 把 4 处分四段窄：

- L120 ``get_source_adapter(...)`` → ``ValueError``
- L126 ``adapter.probe()`` → ``(URLError, OSError, TimeoutError)``
- L139 ``adapter.search_list(...)`` →
  ``(URLError, OSError, TimeoutError, ValueError, KeyError)``
- L166 ``adapter.build_law_payload(...)`` →
  ``(URLError, OSError, TimeoutError, ValueError, KeyError)``

本测试三组守门：

1. ``BusinessFailuresStillSwallowed``：业务降级类异常仍走 ``ok=False`` step
   报告路径（与修前等价）。
2. ``ProgrammingBugsPropagate``：``AttributeError`` / ``TypeError`` 等编程
   bug 现在透传，不再被吞成 ``ok=False``。
3. ``ImportHasUrlError``：``sources.py`` 顶部 import 了 ``URLError``。
"""

from __future__ import annotations

import unittest
import urllib.error
from typing import ClassVar
from unittest.mock import MagicMock, patch
from urllib.error import URLError

from chinalaw import sources


def _make_adapter_mock(
    *,
    probe_return=None,
    probe_side_effect=None,
    search_return=None,
    search_side_effect=None,
    build_return=None,
    build_side_effect=None,
):
    """构造一个 fake adapter，方便每个用例只 inject 关心的那一步异常。"""

    adapter = MagicMock()
    if probe_side_effect is not None:
        adapter.probe.side_effect = probe_side_effect
    else:
        adapter.probe.return_value = probe_return or {
            "status_code": 200,
            "page_shape": "ok",
        }
    if search_side_effect is not None:
        adapter.search_list.side_effect = search_side_effect
    else:
        adapter.search_list.return_value = search_return or {
            "rows": [
                {
                    "bbbs": "row-1",
                    "title": "中华人民共和国民法典",
                    "sxx": 1,
                }
            ]
        }
    if build_side_effect is not None:
        adapter.build_law_payload.side_effect = build_side_effect
    else:
        adapter.build_law_payload.return_value = build_return or {
            "id": "law-id",
            "title": "中华人民共和国民法典",
            "short_title": "民法典",
            "level": "law",
            "status": "current",
            "source_url": "https://flk.npc.gov.cn/?id=row-1",
            "source_hash": "h",
            "source_checked_at": "2026-05-05T00:00:00+00:00",
            "articles": [
                {
                    "number": "1",
                    "number_display": "第一条",
                    "text": "总则。",
                    "part": None,
                }
            ],
        }
    return adapter


class BusinessFailuresStillSwallowed(unittest.TestCase):
    """4 处 try 的业务降级类异常仍被新窄 except 接住，行为与修前等价
    （``ok=False`` + step 失败报告）。"""

    def test_unknown_source_returns_ok_false(self) -> None:
        """``get_source_adapter`` 抛 ``ValueError``（未知 source）→ ok=False，
        step `adapter` 失败。"""

        report = sources.verify_source("does_not_exist")
        self.assertFalse(report["ok"])
        self.assertEqual(report["steps"][0]["step"], "adapter")
        self.assertFalse(report["steps"][0]["ok"])

    def test_probe_url_error_swallowed(self) -> None:
        """``adapter.probe()`` 抛 ``URLError``（连不上）→ ok=False，
        step `probe` 失败。"""

        adapter = _make_adapter_mock(
            probe_side_effect=URLError("conn refused")
        )
        with patch.object(sources, "get_source_adapter", return_value=adapter):
            report = sources.verify_source("flk_npc")
        self.assertFalse(report["ok"])
        last = report["steps"][-1]
        self.assertEqual(last["step"], "probe")
        self.assertFalse(last["ok"])

    def test_search_value_error_swallowed(self) -> None:
        """``adapter.search_list(...)`` 抛 ``ValueError``（JSON 解析失败）→
        ok=False，step `search` 失败。"""

        adapter = _make_adapter_mock(
            search_side_effect=ValueError("bad json")
        )
        with patch.object(sources, "get_source_adapter", return_value=adapter):
            report = sources.verify_source("flk_npc")
        self.assertFalse(report["ok"])
        last = report["steps"][-1]
        self.assertEqual(last["step"], "search")
        self.assertFalse(last["ok"])

    def test_build_law_payload_value_error_swallowed(self) -> None:
        """``adapter.build_law_payload(...)`` 抛 ``ValueError``（detail 解析
        失败）→ ok=False，step `fetch_clean` 失败。"""

        adapter = _make_adapter_mock(
            build_side_effect=ValueError("bad detail payload")
        )
        with patch.object(sources, "get_source_adapter", return_value=adapter):
            report = sources.verify_source("flk_npc")
        self.assertFalse(report["ok"])
        last = report["steps"][-1]
        self.assertEqual(last["step"], "fetch_clean")
        self.assertFalse(last["ok"])

    def test_search_os_error_swallowed(self) -> None:
        """``adapter.search_list(...)`` 抛 ``OSError`` / `TimeoutError`（socket
        层）→ ok=False，step `search` 失败。补强网络异常 swallow。"""

        adapter = _make_adapter_mock(
            search_side_effect=TimeoutError("read timeout")
        )
        with patch.object(sources, "get_source_adapter", return_value=adapter):
            report = sources.verify_source("flk_npc")
        self.assertFalse(report["ok"])
        last = report["steps"][-1]
        self.assertEqual(last["step"], "search")


class ProgrammingBugsPropagate(unittest.TestCase):
    """编程错误（``AttributeError`` / ``NameError`` / ``TypeError`` 出现在不
    catch 该类的 try 块中）现在透传，不再被吞成 ``ok=False``。

    用 mock 注入异常验证：修前 ``except Exception`` 会把这些静默吞掉，本 PR
    后必须冒到调用方。
    """

    def test_get_source_adapter_attribute_error_propagates(self) -> None:
        """L120 try 块只 catch ``ValueError``；``AttributeError`` 透传。"""

        with (
            patch.object(
                sources,
                "get_source_adapter",
                side_effect=AttributeError("simulated bug"),
            ),
            self.assertRaises(AttributeError),
        ):
            sources.verify_source("flk_npc")

    def test_probe_attribute_error_propagates(self) -> None:
        """L126 try 块只 catch 网络类异常；``adapter.probe`` 抛
        ``AttributeError``（adapter 实现 bug）应透传。"""

        adapter = _make_adapter_mock(
            probe_side_effect=AttributeError("simulated probe bug")
        )
        with (
            patch.object(sources, "get_source_adapter", return_value=adapter),
            self.assertRaises(AttributeError),
        ):
            sources.verify_source("flk_npc")

    def test_search_attribute_error_propagates(self) -> None:
        """L139 try 块 catch 网络 + ValueError + KeyError；``AttributeError``
        透传。"""

        adapter = _make_adapter_mock(
            search_side_effect=AttributeError("simulated search bug")
        )
        with (
            patch.object(sources, "get_source_adapter", return_value=adapter),
            self.assertRaises(AttributeError),
        ):
            sources.verify_source("flk_npc")

    def test_build_law_payload_name_error_propagates(self) -> None:
        """L166 try 块 catch 网络 + ValueError + KeyError；``NameError``
        （cleaning 里 typo 一个变量）透传。"""

        adapter = _make_adapter_mock(
            build_side_effect=NameError("undefined name in cleaning")
        )
        with (
            patch.object(sources, "get_source_adapter", return_value=adapter),
            self.assertRaises(NameError),
        ):
            sources.verify_source("flk_npc")

    def test_build_law_payload_type_error_propagates(self) -> None:
        """``TypeError`` 往往是调用签名 / 内部类型误用，不能被降级成
        source 不可用。"""

        adapter = _make_adapter_mock(
            build_side_effect=TypeError("wrong call signature")
        )
        with (
            patch.object(sources, "get_source_adapter", return_value=adapter),
            self.assertRaises(TypeError),
        ):
            sources.verify_source("flk_npc")

    def test_probe_runtime_error_propagates(self) -> None:
        """``RuntimeError``（cleaning 防御性 raise / 配置错误）不被任何 except
        tuple 接住，应透传。"""

        adapter = _make_adapter_mock(
            probe_side_effect=RuntimeError("cleaning misconfigured")
        )
        with (
            patch.object(sources, "get_source_adapter", return_value=adapter),
            self.assertRaises(RuntimeError),
        ):
            sources.verify_source("flk_npc")


class ImportHasUrlError(unittest.TestCase):
    """守门：sources.py 顶部 import 了 ``URLError``（窄 except 必需）。"""

    EXPECTED_NAME: ClassVar[str] = "URLError"

    def test_sources_module_imports_url_error(self) -> None:
        self.assertIs(sources.URLError, urllib.error.URLError)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
