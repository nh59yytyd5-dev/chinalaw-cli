"""Continuous-session tests for the MCP stdio protocol boundary."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from io import BytesIO, StringIO
from pathlib import Path
from unittest import mock

from chinalaw import mcp

REPO_ROOT = Path(__file__).resolve().parents[1]


def _line(payload: object) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"


def _header(payload: object) -> bytes:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body


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
        env = os.environ.copy()
        source_path = str(REPO_ROOT / "src")
        env["PYTHONPATH"] = os.pathsep.join(
            part for part in (source_path, env.get("PYTHONPATH", "")) if part
        )

        completed = subprocess.run(
            [sys.executable, "-m", "chinalaw.mcp"],
            input=request,
            capture_output=True,
            cwd=REPO_ROOT,
            env=env,
            timeout=10,
            check=False,
        )
        messages = _read_all(BytesIO(completed.stdout))

        self.assertEqual(0, completed.returncode, completed.stderr.decode("utf-8"))
        self.assertEqual(-32700, messages[0][0]["error"]["code"])
        self.assertEqual({}, messages[1][0]["result"])


if __name__ == "__main__":
    unittest.main()
