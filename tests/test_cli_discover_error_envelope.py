"""PR-B.1 守门测试 — CLI ``_handle_discover`` 结构化 error envelope。

详见 ``docs/CLI_DISCOVER_ERROR_ENVELOPE_SPEC.md`` §6.1。

四组守门：

1. ``DiscoverHandlerTransportErrorEnvelopeTests`` —— transport 异常族
   （TimeoutError / URLError / HTTPError / OSError / JSONDecodeError）→
   CLI 退 2 + stdout ``law_discover_error`` JSON envelope。
2. ``DiscoverHandlerValueErrorRegressionTests`` —— PR-B 既有 ValueError
   路径回归（``--source court_gongbao`` / 业务层 ValueError）。
3. ``DiscoverHandlerProgrammingErrorPropagationTests`` —— 编程错误
   （AttributeError / KeyError）必须透传，**不**被 envelope 吞掉
   （PR5c / PR6 / PR-A 窄 except 立场守门）。
4. ``DiscoverEnvelopeSchemaSymmetryTests`` —— ``law_discover_error`` 与
   ``law_fetch_error`` envelope schema 三字段（``kind`` / ``error`` /
   ``message``）形态对称。

立场：codex P2 在 PR #53 inline at ``cli.py:1304`` 指出 discover 当前只接
``ValueError``，transport / runtime 失败会冒原生 traceback，破坏 JSON
envelope 自动化契约。本 PR 把 except tuple 扩到
``(ValueError, URLError, OSError, TimeoutError, json.JSONDecodeError)``，对齐
``_handle_fetch`` 的 ``law_fetch_error`` envelope 契约。
"""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from chinalaw.cli import app


def _run_discover_cli(db_path: Path) -> tuple[int, str]:
    """跑 ``chinalaw discover --source flk_npc --format json``，返回 (exit_code, stdout)。"""

    buf = io.StringIO()
    with redirect_stdout(buf):
        exit_code = app(
            [
                "--db",
                str(db_path),
                "discover",
                "--source",
                "flk_npc",
                "--format",
                "json",
            ]
        )
    return exit_code, buf.getvalue()


class DiscoverHandlerTransportErrorEnvelopeTests(unittest.TestCase):
    """transport / parse 异常族 → ``law_discover_error`` envelope + 退 2。

    spec §6.1 验收守门 1-5：异常族包含 TimeoutError / URLError / HTTPError /
    OSError / JSONDecodeError。所有路径必须 emit 同款 envelope schema。
    """

    def _assert_envelope(
        self,
        exit_code: int,
        stdout: str,
        *,
        expected_error_class: str,
        expected_message_substr: str,
    ) -> None:
        self.assertEqual(exit_code, 2, msg=f"stdout={stdout!r}")
        self.assertIn("law_discover_error", stdout)
        payload = json.loads(stdout)
        self.assertEqual(payload["kind"], "law_discover_error")
        self.assertEqual(payload["error"], expected_error_class)
        self.assertIn(expected_message_substr, payload["message"])

    def test_discover_timeout_emits_envelope_exit_two(self) -> None:
        """TimeoutError → envelope；spec §6.1 守门 1。"""

        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "t.db"
            with patch(
                "chinalaw.cli.discover_mod.discover_laws",
                side_effect=TimeoutError("upstream timeout after 30s"),
            ):
                exit_code, stdout = _run_discover_cli(db_path)
        self._assert_envelope(
            exit_code,
            stdout,
            expected_error_class="TimeoutError",
            expected_message_substr="upstream timeout",
        )

    def test_discover_urlerror_emits_envelope_exit_two(self) -> None:
        """URLError → envelope；spec §6.1 守门 2。"""

        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "t.db"
            with patch(
                "chinalaw.cli.discover_mod.discover_laws",
                side_effect=URLError("dns lookup failed"),
            ):
                exit_code, stdout = _run_discover_cli(db_path)
        self._assert_envelope(
            exit_code,
            stdout,
            expected_error_class="URLError",
            expected_message_substr="dns lookup failed",
        )

    def test_discover_httperror_emits_envelope_exit_two(self) -> None:
        """HTTPError 是 URLError 子类；同款 except 必须覆盖；spec §6.1 守门 3。

        典型场景：flk 上游返回 503 / 502 / WAF 拦截。
        """

        http_err = HTTPError(
            url="https://flk.npc.gov.cn/api/v1/searchList",
            code=503,
            msg="Service Unavailable",
            hdrs=None,
            fp=None,
        )
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "t.db"
            with patch(
                "chinalaw.cli.discover_mod.discover_laws",
                side_effect=http_err,
            ):
                exit_code, stdout = _run_discover_cli(db_path)
        self._assert_envelope(
            exit_code,
            stdout,
            expected_error_class="HTTPError",
            expected_message_substr="503",
        )

    def test_discover_jsondecodeerror_emits_envelope_exit_two(self) -> None:
        """json.JSONDecodeError → envelope；spec §6.1 守门 4。

        当前 adapter 内部把 JSONDecodeError wrap 成 ValueError，但 wrap 是
        adapter 私有契约。本测试模拟 adapter 直接冒 JSONDecodeError 的
        future-proof 路径，验证 envelope 仍命中。
        """

        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "t.db"
            with patch(
                "chinalaw.cli.discover_mod.discover_laws",
                side_effect=json.JSONDecodeError(
                    "Expecting value", "<html>not json</html>", 0
                ),
            ):
                exit_code, stdout = _run_discover_cli(db_path)
        self._assert_envelope(
            exit_code,
            stdout,
            expected_error_class="JSONDecodeError",
            expected_message_substr="Expecting value",
        )

    def test_discover_oserror_emits_envelope_exit_two(self) -> None:
        """OSError（含 ConnectionError）→ envelope；spec §6.1 守门 5。"""

        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "t.db"
            with patch(
                "chinalaw.cli.discover_mod.discover_laws",
                side_effect=ConnectionError("connection reset by peer"),
            ):
                exit_code, stdout = _run_discover_cli(db_path)
        self._assert_envelope(
            exit_code,
            stdout,
            expected_error_class="ConnectionError",
            expected_message_substr="connection reset",
        )


class DiscoverHandlerValueErrorRegressionTests(unittest.TestCase):
    """PR-B 既有 ValueError 路径回归 —— 扩 except tuple 不能破坏现有契约。"""

    def test_discover_business_value_error_still_emits_envelope(self) -> None:
        """``discover_laws`` 业务层抛 ValueError → 同款 envelope。

        argparse 层的 ``--source`` choices=['flk_npc'] 与 ``--status`` choices
        已经预先拦截非法值，业务层 ValueError 实际触发路径限于：未来扩源时
        DISCOVER_SOURCES / STATUS_FILTER_SUPPORTED 不一致 / status_to_sxx
        keyword 漂移等内部不变量破裂。本测试 mock ValueError 验证扩 except
        tuple 后 ValueError 仍命中同款 envelope（PR-B 契约回归）。
        """

        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "t.db"
            with patch(
                "chinalaw.cli.discover_mod.discover_laws",
                side_effect=ValueError(
                    "--status filter is not supported by source 'foo'; "
                    "supported sources: ['flk_npc']"
                ),
            ):
                exit_code, stdout = _run_discover_cli(db_path)
        self.assertEqual(exit_code, 2)
        payload = json.loads(stdout)
        self.assertEqual(payload["kind"], "law_discover_error")
        self.assertEqual(payload["error"], "ValueError")
        self.assertIn("--status filter is not supported", payload["message"])

    def test_discover_invalid_status_blocked_by_argparse(self) -> None:
        """argparse choices 层拦截 ``--status invalid`` → SystemExit(2)。

        PR-B 既有契约：argparse choices 是第一道防线，业务层 / handler 层不
        参与；本 PR 扩 except tuple 不影响此路径（不进入 handler）。
        """

        from contextlib import redirect_stderr

        buf_err = io.StringIO()
        with self.assertRaises(SystemExit) as ctx, redirect_stderr(buf_err):
            app(
                [
                    "discover",
                    "--source",
                    "flk_npc",
                    "--status",
                    "garbage",
                    "--format",
                    "json",
                ]
            )
        self.assertEqual(ctx.exception.code, 2)
        self.assertIn("invalid choice", buf_err.getvalue())


class DiscoverHandlerProgrammingErrorPropagationTests(unittest.TestCase):
    """编程错误（AttributeError / KeyError）必须透传，**不**被 envelope 吞掉。

    spec §6.1 验收守门 7-8：与 PR5c / PR6 / PR-A 系列窄 except 立场一致。
    透传的设计意图：让 prod 第一次出现编程 bug 时立刻 traceback 被发现，不被
    envelope 静默退化为"discover 失败"。
    """

    def test_discover_attribute_error_propagates_traceback(self) -> None:
        """AttributeError 不被 except tuple 接住，原样冒到调用方。"""

        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "t.db"
            with (
                patch(
                    "chinalaw.cli.discover_mod.discover_laws",
                    side_effect=AttributeError(
                        "'NoneType' object has no attribute 'rows'"
                    ),
                ),
                self.assertRaises(AttributeError),
            ):
                # stdout 不会有 envelope；异常直接冒出
                app(
                    [
                        "--db",
                        str(db_path),
                        "discover",
                        "--source",
                        "flk_npc",
                        "--format",
                        "json",
                    ]
                )

    def test_discover_keyerror_propagates_traceback(self) -> None:
        """KeyError 不被 except tuple 接住，原样冒到调用方。

        rationale：``discover_laws`` 全用 ``.get()`` 不会内部抛 KeyError；
        若上游冒 KeyError 等于 adapter 返回值结构变化（契约破裂），属编程
        bug 应透传。
        """

        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "t.db"
            with (
                patch(
                    "chinalaw.cli.discover_mod.discover_laws",
                    side_effect=KeyError("expected_field"),
                ),
                self.assertRaises(KeyError),
            ):
                app(
                    [
                        "--db",
                        str(db_path),
                        "discover",
                        "--source",
                        "flk_npc",
                        "--format",
                        "json",
                    ]
                )

    def test_discover_type_error_propagates_traceback(self) -> None:
        """TypeError 不被 except tuple 接住，原样冒到调用方。

        典型场景：错误 kwarg 名 / signature 不匹配 / None.attr。
        """

        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "t.db"
            with (
                patch(
                    "chinalaw.cli.discover_mod.discover_laws",
                    side_effect=TypeError(
                        "discover_laws() got unexpected keyword 'foo'"
                    ),
                ),
                self.assertRaises(TypeError),
            ):
                app(
                    [
                        "--db",
                        str(db_path),
                        "discover",
                        "--source",
                        "flk_npc",
                        "--format",
                        "json",
                    ]
                )


class DiscoverEnvelopeSchemaSymmetryTests(unittest.TestCase):
    """``law_discover_error`` 与 ``law_fetch_error`` envelope schema 形态对称。

    spec §6.1 验收守门 9：agent 解析逻辑应可同款复用，不要求 discover 专门
    schema 处理。
    """

    def test_discover_error_envelope_has_kind_error_message_fields(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "t.db"
            with patch(
                "chinalaw.cli.discover_mod.discover_laws",
                side_effect=TimeoutError("timeout"),
            ):
                exit_code, stdout = _run_discover_cli(db_path)
        self.assertEqual(exit_code, 2)
        payload = json.loads(stdout)
        # 三字段对称 fetch envelope
        self.assertEqual(set(payload.keys()), {"kind", "error", "message"})

    def test_discover_error_kind_matches_law_discover_error_constant(self) -> None:
        """``kind`` 字段固定字符串，与 PR-B 既有 ValueError 路径 / fetch
        envelope 命名规范（``law_<command>_error``）一致。"""

        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "t.db"
            with patch(
                "chinalaw.cli.discover_mod.discover_laws",
                side_effect=URLError("test"),
            ):
                _, stdout = _run_discover_cli(db_path)
        payload = json.loads(stdout)
        self.assertEqual(payload["kind"], "law_discover_error")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
