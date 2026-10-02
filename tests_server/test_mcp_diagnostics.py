"""Regression coverage through the SDK HTTP transport, including error flags."""

import json

import pytest

from chinalaw.server.auth_store import PUBLIC_SCOPE


@pytest.fixture
def call_mcp(owner_api):
    token = owner_api.app.state.auth.issue_query_token("diagnostics", [PUBLIC_SCOPE])["token"]
    headers = {"Authorization": "Bearer " + token, "Accept": "application/json, text/event-stream"}
    init = owner_api.post(
        "/mcp",
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "diagnostics", "version": "1"},
            },
        },
    )
    headers.update(
        {"Mcp-Session-Id": init.headers["mcp-session-id"], "MCP-Protocol-Version": "2025-11-25"}
    )
    owner_api.post(
        "/mcp", headers=headers, json={"jsonrpc": "2.0", "method": "notifications/initialized"}
    )

    def call(tool_name, **arguments):
        response = owner_api.post(
            "/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "chinalaw_" + tool_name, "arguments": arguments},
            },
        )
        assert response.status_code == 200
        result = response.json()["result"]
        if not result.get("isError") and "structuredContent" not in result:
            result["structuredContent"] = json.loads(result["content"][0]["text"])
        return result

    return call


@pytest.mark.parametrize(
    "name,arguments",
    [
        ("list", {"kind": "norm"}),
        ("search", {"kind": "norm", "query": "测试"}),
        ("document", {"kind": "norm", "id": "private-test"}),
        ("document", {"kind": "norm", "id": "does-not-exist"}),
    ],
)
def test_private_denial_is_actionable(call_mcp, name, arguments):
    result = call_mcp(name, **arguments)
    assert result["isError"] is True
    payload = result["structuredContent"]
    assert payload["error"] == "private_access_denied"
    assert payload["status"] == 403
    assert json.loads(result["content"][0]["text"]) == payload


def test_document_name_and_pagination(call_mcp):
    by_name = call_mcp("document", kind="law", id="公开查询测试资料", limit=1)
    by_id = call_mcp("document", kind="law", id="public-test", limit=1)
    assert not by_name.get("isError")
    assert by_name["structuredContent"] == by_id["structuredContent"]
    assert len(by_name["structuredContent"]["document"]["articles"][0]["text"]) > 100
    empty = call_mcp("document", kind="law", id="公开查询测试资料", offset=1)
    assert empty["structuredContent"]["document"]["articles"] == []


def test_missing_name_and_bad_bounds(call_mcp):
    missing = call_mcp("document", kind="law", id="不存在的法")
    assert missing["isError"]
    assert "candidates" in missing["structuredContent"]["details"]
    bad = call_mcp("document", kind="law", id="public-test", limit=101)
    assert bad["isError"]
    assert bad["structuredContent"]["status"] == 400


def test_unexpected_errors_stay_masked(call_mcp, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("secret database location")

    monkeypatch.setattr("chinalaw.server.mcp_http.service.resolve", broken)
    result = call_mcp("resolve", name="测试")
    assert result["isError"]
    assert "secret database location" not in json.dumps(result)


@pytest.mark.parametrize("query", ["第500条", "第五百条", "500", "第十四条之一"])
def test_bare_number_has_guidance(call_mcp, query):
    result = call_mcp("search", query=query)["structuredContent"]
    assert result["retrieval"]["bare_article_number"] is True
    assert "article" in result["hint"]
    assert result["retrieval"]["citation"] is False


def test_empty_applicability_reports_coverage(call_mcp):
    result = call_mcp("applicable", date="2021-06-01", topic="合同")["structuredContent"]
    assert result["coverage"]["rules_loaded"] == 0
    assert any(w["code"] == "applicability_data_missing" for w in result["warnings"])


def test_http_law_scope_does_not_fall_back_to_global(call_mcp):
    found = call_mcp("search", query="公开全文", in_laws="公开查询测试资料")["structuredContent"]
    assert found["counts"]["article"] == 1
    assert found["law_filter"]["resolved"][0]["id"] == "public-test"
    missing = call_mcp("search", query="公开全文", in_laws="不存在的法规")["structuredContent"]
    assert missing["counts"]["total"] == 0
    assert missing["law_filter"]["unresolved"] == ["不存在的法规"]
    many = call_mcp("search", query="公开全文", in_laws=["public-test"])["structuredContent"]
    assert many["counts"]["article"] == 1


def test_http_law_scope_is_bounded_and_preserves_permission(call_mcp):
    bad = call_mcp("search", query="测试", in_laws=["x"] * 21)
    assert bad["isError"] and bad["structuredContent"]["error"] == "invalid_search"
    denied = call_mcp("search", query="测试", kind="norm", in_laws="公开查询测试资料")
    assert denied["isError"] and denied["structuredContent"]["status"] == 403


def test_rest_law_scope(owner_api):
    result = owner_api.get("/api/v1/search", params={"q": "公开全文", "in_laws": "不存在的法规"})
    assert result.status_code == 200
    assert result.json()["counts"]["total"] == 0
    assert result.json()["law_filter"]["unresolved"]
