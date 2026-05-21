"""Tests for the lightweight MCP adapter."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest import mock

from chinalaw import loader, mcp

FIXTURES = Path(__file__).resolve().parents[1] / "data" / "fixtures"
REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_mcp_footprint_module():
    path = REPO_ROOT / "scripts" / "eval" / "mcp-footprint.py"
    spec = importlib.util.spec_from_file_location("mcp_footprint", path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class McpTests(unittest.TestCase):
    def _db(self, td: str) -> Path:
        db_path = Path(td) / "mcp.db"
        loader.load_fixtures(db_path, FIXTURES)
        return db_path

    def test_tools_list_is_small_and_stable(self) -> None:
        response = mcp.handle_request({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})

        tools = response["result"]["tools"]
        self.assertLessEqual(len(tools), 6)
        self.assertIn("chinalaw_article", {tool["name"] for tool in tools})

    def test_read_message_accepts_case_insensitive_content_length(self) -> None:
        body = b'{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
        stream = BytesIO(b"content-length: " + str(len(body)).encode("ascii") + b"\r\n\r\n" + body)

        request = mcp._read_message(stream)

        self.assertEqual("tools/list", request["method"])

    def test_stdio_response_uses_json_line_for_json_line_clients(self) -> None:
        body = b'{"jsonrpc":"2.0","id":1,"method":"tools/list"}\n'
        stdin = BytesIO(body)
        stdout = BytesIO()

        message = mcp._read_message_with_framing(stdin)
        self.assertIsNotNone(message)
        request, framing = message
        response = mcp.handle_request(request)
        mcp._write_message(stdout, response, framing=framing)

        self.assertFalse(stdout.getvalue().startswith(b"Content-Length:"))
        self.assertTrue(stdout.getvalue().endswith(b"\n"))
        self.assertEqual("2.0", json.loads(stdout.getvalue())["jsonrpc"])

    def test_article_tool_returns_structured_content(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db_path = self._db(td)
            response = mcp.handle_request(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {
                        "name": "chinalaw_article",
                        "arguments": {"law": "民法典", "number": "第一百四十三条"},
                    },
                },
                db_path=db_path,
            )

        result = response["result"]
        self.assertFalse(result["isError"])
        self.assertTrue(result["structuredContent"]["found"])
        self.assertEqual(result["structuredContent"]["article"]["number"], "143")

    def test_tools_call_rejects_non_object_params(self) -> None:
        response = mcp.handle_request(
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": []}
        )

        self.assertEqual(-32602, response["error"]["code"])

    def test_unexpected_tool_errors_are_not_hidden_as_protocol_errors(self) -> None:
        with (
            mock.patch("chinalaw.mcp.service.search", side_effect=RuntimeError("bug")),
            self.assertRaises(RuntimeError),
        ):
            mcp.handle_request(
                {
                    "jsonrpc": "2.0",
                    "id": 4,
                    "method": "tools/call",
                    "params": {
                        "name": "chinalaw_search",
                        "arguments": {"query": "合同", "limit": 1},
                    },
                }
            )

    def test_mcp_footprint_script_measures_real_stdio_server(self) -> None:
        if not (REPO_ROOT / "scripts" / "eval" / "mcp-footprint.py").exists():
            self.skipTest("optional MCP footprint script is not included")
        module = _load_mcp_footprint_module()

        result = module.measure(
            command=[sys.executable, "-m", "chinalaw.mcp"],
            timeout=5,
            target_budget_chars=6000,
        )

        self.assertEqual("mcp_tools_footprint", result["kind"])
        self.assertEqual(6, result["tool_count"])
        self.assertTrue(result["within_budget"])


if __name__ == "__main__":
    unittest.main()
