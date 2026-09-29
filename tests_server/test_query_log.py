"""The owner-only query log records what was asked, never normative text."""

from __future__ import annotations

import json

from chinalaw.server import cli
from chinalaw.server.auth_store import PRIVATE_SCOPE, PUBLIC_SCOPE
from chinalaw.server.query_log import QueryLog


def _mcp_session(api, token: str) -> dict:
    headers = {
        "Accept": "application/json, text/event-stream",
        "Authorization": "Bearer " + token,
    }
    init = api.post(
        "/mcp",
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "query-log", "version": "1"},
            },
        },
    )
    assert init.status_code == 200, init.text
    headers.update(
        {"Mcp-Session-Id": init.headers["mcp-session-id"], "MCP-Protocol-Version": "2025-11-25"}
    )
    api.post("/mcp", headers=headers, json={"jsonrpc": "2.0", "method": "notifications/initialized"})
    return headers


def _call(api, headers: dict, name: str, arguments: dict, call_id: int):
    return api.post(
        "/mcp",
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": call_id,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
    )


def test_mcp_and_rest_queries_are_logged_without_text(owner_api):
    auth = owner_api.app.state.auth
    token = auth.issue_query_token("log test", [PUBLIC_SCOPE, PRIVATE_SCOPE])
    headers = _mcp_session(owner_api, token["token"])
    assert _call(owner_api, headers, "chinalaw_search", {"query": "私域独有关键词"}, 2).status_code == 200
    _call(owner_api, headers, "chinalaw_article", {"law": "公开查询测试资料", "number": "1"}, 3)
    _call(owner_api, headers, "chinalaw_article", {"law": "不存在的法", "number": "1"}, 4)
    rest = owner_api.get("/api/v1/search", params={"q": "公开全文"})
    assert rest.status_code == 200

    rows = QueryLog(owner_api.app.state.config.query_log_path).export()
    assert [(row["channel"], row["tool"]) for row in rows] == [
        ("mcp", "search"),
        ("mcp", "article"),
        ("mcp", "article"),
        ("http", "search"),
    ]
    search, found, missing, panel = rows
    assert search["params"] == {"query": "私域独有关键词", "kind": "all", "limit": 10}
    assert search["outcome"]["counts"]["norm_clause"] == 1
    assert search["client"] == "personal:log test"
    assert found["outcome"] == {"found": True, "law_id": "public-test", "number": "1"}
    assert missing["outcome"]["error"] == "article_not_found"
    assert panel["client"] == "owner"
    assert panel["outcome"]["counts"]["article"] == 1
    # Only parameters and counts are stored: no clause text leaks into the log.
    raw = owner_api.app.state.config.query_log_path.read_bytes()
    assert "私域独有关键词正文".encode() not in raw
    assert "公开全文测试".encode() not in raw


def test_failed_queries_are_logged_and_still_raise(owner_api):
    headers = _mcp_session(
        owner_api, owner_api.app.state.auth.issue_query_token("fail", [PUBLIC_SCOPE])["token"]
    )
    response = _call(owner_api, headers, "chinalaw_resolve", {"name": "x" * 201}, 2)
    assert response.json()["result"]["isError"]
    (row,) = QueryLog(owner_api.app.state.config.query_log_path).export()
    assert row["tool"] == "resolve" and row["error"] == "ValueError"


def test_queries_command_exports_json_lines(owner_api, capsys):
    owner_api.get("/api/v1/search", params={"q": "公开全文"})
    config = owner_api.app.state.config
    code = cli.main(
        ["queries", "--db", str(config.db_path), "--state-dir", str(config.state_dir)]
    )
    assert code == 0
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [line["params"]["query"] for line in lines] == ["公开全文"]


def test_log_write_failure_does_not_break_queries(owner_api):
    path = owner_api.app.state.config.query_log_path
    path.unlink()
    path.mkdir()  # sqlite cannot open a directory: every write fails
    try:
        assert owner_api.get("/api/v1/search", params={"q": "公开全文"}).status_code == 200
    finally:
        path.rmdir()
