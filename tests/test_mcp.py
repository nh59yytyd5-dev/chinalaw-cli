"""Tests for the lightweight MCP adapter."""

from __future__ import annotations

import json
import tempfile
import unittest
from io import BytesIO, StringIO
from pathlib import Path
from unittest import mock

from chinalaw import loader, mcp

FIXTURES = Path(__file__).resolve().parents[1] / "data" / "fixtures"


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

    def test_request_validation_distinguishes_null_id_from_notification(self) -> None:
        response = mcp.handle_request({"jsonrpc": "2.0", "id": None, "method": "ping"})
        notification = mcp.handle_request({"jsonrpc": "2.0", "method": "missing"})

        self.assertIsNone(response["id"])
        self.assertEqual({}, response["result"])
        self.assertIsNone(notification)

    def test_request_validation_rejects_invalid_envelopes(self) -> None:
        cases = [
            ([], -32600),
            ({"jsonrpc": "1.0", "id": 1, "method": "ping"}, -32600),
            ({"jsonrpc": "2.0", "id": True, "method": "ping"}, -32600),
            ({"jsonrpc": "2.0", "id": [], "method": "ping"}, -32600),
            ({"jsonrpc": "2.0", "id": 1, "method": []}, -32600),
            ({"jsonrpc": "2.0", "id": 1, "method": "missing"}, -32601),
        ]
        for request, code in cases:
            with self.subTest(request=request):
                response = mcp.handle_request(request)
                self.assertEqual(code, response["error"]["code"])

    def test_tool_arguments_are_validated_without_coercion(self) -> None:
        cases = [
            ({"query": ["合同"]}, "must be a string"),
            ({"query": "合同", "limit": "1"}, "must be an integer"),
            ({"query": "合同", "limit": True}, "must be an integer"),
            ({"query": "合同", "limit": 0}, "at least 1"),
            ({"query": "合同", "limit": 51}, "at most 50"),
            ({"query": "合同", "kind": "invalid"}, "must be one of"),
            ({"query": "合同", "extra": "value"}, "unexpected argument"),
            ({}, "missing required argument"),
        ]
        for arguments, message in cases:
            with self.subTest(arguments=arguments):
                response = mcp.handle_request(
                    {
                        "jsonrpc": "2.0",
                        "id": 4,
                        "method": "tools/call",
                        "params": {"name": "chinalaw_search", "arguments": arguments},
                    }
                )
                self.assertEqual(-32602, response["error"]["code"])
                self.assertIn(message, response["error"]["message"])

    def test_unexpected_tool_errors_are_isolated_as_tool_results(self) -> None:
        error_stream = StringIO()
        with mock.patch("chinalaw.mcp.service.search", side_effect=RuntimeError("bug")):
            response = mcp.handle_request(
                {
                    "jsonrpc": "2.0",
                    "id": 4,
                    "method": "tools/call",
                    "params": {
                        "name": "chinalaw_search",
                        "arguments": {"query": "合同", "limit": 1},
                    },
                },
                error_stream=error_stream,
            )

        self.assertTrue(response["result"]["isError"])
        self.assertEqual(
            "chinalaw_mcp_internal_error",
            response["result"]["structuredContent"]["kind"],
        )
        self.assertIn("RuntimeError: bug", error_stream.getvalue())

    def test_tools_list_remains_within_context_budget(self) -> None:
        serialized = json.dumps({"tools": mcp.TOOLS}, ensure_ascii=False, separators=(",", ":"))

        self.assertEqual(6, len(mcp.TOOLS))
        self.assertLessEqual(len(serialized), 6000)


if __name__ == "__main__":
    unittest.main()
