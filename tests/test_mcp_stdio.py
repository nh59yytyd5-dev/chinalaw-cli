"""Continuous-session tests for the MCP stdio protocol boundary."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from io import BytesIO, StringIO
from pathlib import Path
from unittest import mock

from chinalaw import loader, mcp
from chinalaw.db import DEFAULT_DB_PATH

REPO_ROOT = Path(__file__).resolve().parents[1]


def _line(payload: object) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"


def _header(payload: object) -> bytes:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body


def _module_env(**overrides: str) -> dict[str, str]:
    env = os.environ.copy()
    source_path = str(REPO_ROOT / "src")
    env["PYTHONPATH"] = os.pathsep.join(
        part for part in (source_path, env.get("PYTHONPATH", "")) if part
    )
    env.update(overrides)
    return env


def _read_all(stream: BytesIO) -> list[tuple[object, str]]:
    stream.seek(0)
    messages: list[tuple[object, str]] = []
    while True:
        message = mcp._read_message_with_framing(stream)
        if message is None:
            return messages
        messages.append(message)


class McpStdioTests(unittest.TestCase):
    def _serve(self, payload: bytes) -> tuple[list[tuple[object, str]], str]:
        stdout = BytesIO()
        stderr = StringIO()
        mcp.serve_streams(BytesIO(payload), stdout, stderr)
        return _read_all(stdout), stderr.getvalue()

    def test_malformed_json_does_not_kill_later_request(self) -> None:
        messages, _stderr = self._serve(
            b"not-json\n" + _line({"jsonrpc": "2.0", "id": 1, "method": "ping"})
        )

        self.assertEqual(2, len(messages))
        self.assertEqual(-32700, messages[0][0]["error"]["code"])
        self.assertEqual({}, messages[1][0]["result"])
        self.assertEqual(["line", "line"], [framing for _message, framing in messages])

    def test_recoverable_bad_header_does_not_kill_later_request(self) -> None:
        payload = b"Content-Length: -1\r\n\r\n" + _header(
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
        )

        messages, _stderr = self._serve(payload)

        self.assertEqual(2, len(messages))
        self.assertEqual(-32700, messages[0][0]["error"]["code"])
        self.assertEqual(6, len(messages[1][0]["result"]["tools"]))
        self.assertEqual(["header", "header"], [framing for _message, framing in messages])

    def test_oversized_known_body_is_bounded_and_recoverable(self) -> None:
        oversized_length = mcp.MAX_BODY_BYTES + 1
        payload = (
            f"Content-Length: {oversized_length}\r\n\r\n".encode("ascii")
            + (b"x" * oversized_length)
            + _line({"jsonrpc": "2.0", "id": 3, "method": "ping"})
        )

        messages, _stderr = self._serve(payload)

        self.assertEqual(2, len(messages))
        self.assertEqual(-32700, messages[0][0]["error"]["code"])
        self.assertEqual({}, messages[1][0]["result"])

    def test_unexpected_tool_exception_does_not_kill_later_request(self) -> None:
        failing = {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "chinalaw_search",
                "arguments": {"query": "合同", "limit": 1},
            },
        }
        with mock.patch("chinalaw.mcp.service.search", side_effect=RuntimeError("boom")):
            messages, stderr = self._serve(
                _line(failing) + _line({"jsonrpc": "2.0", "id": 5, "method": "ping"})
            )

        self.assertEqual(2, len(messages))
        self.assertTrue(messages[0][0]["result"]["isError"])
        self.assertEqual({}, messages[1][0]["result"])
        self.assertIn("RuntimeError: boom", stderr)

    def test_internal_dispatch_exception_uses_protocol_error_and_continues(self) -> None:
        original = mcp.handle_request
        calls = 0

        def fail_once(request, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("dispatch bug")
            return original(request, **kwargs)

        with mock.patch("chinalaw.mcp.handle_request", side_effect=fail_once):
            messages, stderr = self._serve(
                _line({"jsonrpc": "2.0", "id": 6, "method": "ping"})
                + _line({"jsonrpc": "2.0", "id": 7, "method": "ping"})
            )

        self.assertEqual(-32603, messages[0][0]["error"]["code"])
        self.assertEqual({}, messages[1][0]["result"])
        self.assertIn("dispatch bug", stderr)

    def test_notifications_produce_no_bytes_even_when_invalid_or_failing(self) -> None:
        notifications = [
            {"jsonrpc": "2.0", "method": "missing"},
            {"jsonrpc": "2.0", "method": "tools/call", "params": []},
            {
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {"name": "chinalaw_search", "arguments": {"query": []}},
            },
        ]
        stdout = BytesIO()
        mcp.serve_streams(
            BytesIO(b"".join(_line(item) for item in notifications)),
            stdout,
            StringIO(),
        )

        self.assertEqual(b"", stdout.getvalue())

    def test_header_and_line_clients_receive_matching_framing(self) -> None:
        messages, _stderr = self._serve(
            _header({"jsonrpc": "2.0", "id": 8, "method": "ping"})
            + _line({"jsonrpc": "2.0", "id": 9, "method": "resources/list"})
        )

        self.assertEqual(["header", "line"], [framing for _message, framing in messages])
        self.assertEqual({}, messages[0][0]["result"])
        self.assertEqual([], messages[1][0]["result"]["resources"])

    def test_short_header_body_is_detected(self) -> None:
        messages, _stderr = self._serve(b"Content-Length: 20\r\n\r\n{}")

        self.assertEqual(1, len(messages))
        self.assertEqual(-32700, messages[0][0]["error"]["code"])
        self.assertIn("unexpected EOF", messages[0][0]["error"]["data"]["detail"])

    def test_real_module_stdio_recovers_after_parse_error(self) -> None:
        request = b"bad-json\n" + _line({"jsonrpc": "2.0", "id": 10, "method": "ping"})

        completed = subprocess.run(
            [sys.executable, "-m", "chinalaw.mcp"],
            input=request,
            capture_output=True,
            cwd=REPO_ROOT,
            env=_module_env(),
            timeout=10,
            check=False,
        )
        messages = _read_all(BytesIO(completed.stdout))

        self.assertEqual(0, completed.returncode, completed.stderr.decode("utf-8"))
        self.assertEqual(-32700, messages[0][0]["error"]["code"])
        self.assertEqual({}, messages[1][0]["result"])


class McpDatabasePathTests(unittest.TestCase):
    """chinalaw-mcp 与 chinalaw 同样选库：--db，其次 CHINALAW_DB，最后默认路径（#52）。"""

    def _served_db(self, argv: list[str], db_env: str | None) -> Path:
        with mock.patch.dict(os.environ), mock.patch("chinalaw.mcp.serve_stdio") as serve:
            os.environ.pop("CHINALAW_DB", None)
            if db_env is not None:
                os.environ["CHINALAW_DB"] = db_env
            self.assertEqual(0, mcp.main(argv))
        return serve.call_args.kwargs["db_path"]

    def test_db_resolution_order(self) -> None:
        cases = [
            ("environment", [], "/env/library.db", Path("/env/library.db")),
            ("argument first", ["--db", "/arg/library.db"], "/env/library.db", Path("/arg/library.db")),
            ("default", [], None, DEFAULT_DB_PATH),
            ("empty environment is unset", [], "", DEFAULT_DB_PATH),
            ("home expanded", ["--db", "~/library.db"], None, Path.home() / "library.db"),
        ]
        for label, argv, db_env, expected in cases:
            with self.subTest(label):
                self.assertEqual(expected, self._served_db(argv, db_env))

    def test_tools_report_missing_library_without_creating_it(self) -> None:
        # #55：与 chinalaw-server 一样，库不存在时各工具（含 ensure）都报错，不建空库。
        calls = [
            ("chinalaw_article", {"law": "民法典", "number": "533"}),
            ("chinalaw_search", {"query": "合同"}),
            ("chinalaw_ensure", {"name": "民法典"}),
        ]
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "missing" / "library.db"
            for name, arguments in calls:
                with self.subTest(name):
                    response = mcp.handle_request(
                        {
                            "jsonrpc": "2.0",
                            "id": 1,
                            "method": "tools/call",
                            "params": {"name": name, "arguments": arguments},
                        },
                        db_path=db_path,
                    )

                    result = response["result"]
                    self.assertTrue(result["isError"])
                    self.assertEqual("LibraryMissingError", result["structuredContent"]["error"])
                    self.assertFalse(db_path.parent.exists())

    def test_real_module_reads_environment_without_creating_default_db(self) -> None:
        request = _line(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "chinalaw_article",
                    "arguments": {"law": "民法典", "number": "533"},
                },
            }
        )
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "library.db"
            home = Path(td) / "home"
            home.mkdir()
            loader.load_fixtures(db_path, loader.FIXTURES_DIR)

            completed = subprocess.run(
                [sys.executable, "-m", "chinalaw.mcp"],
                input=request,
                capture_output=True,
                cwd=REPO_ROOT,
                env=_module_env(CHINALAW_DB=str(db_path), HOME=str(home), USERPROFILE=str(home)),
                timeout=30,
                check=False,
            )

            self.assertEqual(0, completed.returncode, completed.stderr.decode("utf-8"))
            result = _read_all(BytesIO(completed.stdout))[0][0]["result"]["structuredContent"]
            self.assertTrue(result["found"])
            self.assertEqual("中华人民共和国民法典", result["law"]["title"])
            self.assertEqual("533", str(result["article"]["number"]))
            self.assertEqual([], list(home.iterdir()))


if __name__ == "__main__":
    unittest.main()
