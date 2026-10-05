"""Regression coverage through the SDK HTTP transport, including error flags."""

import json

import pytest

from chinalaw.server.auth_store import PRIVATE_SCOPE, PUBLIC_SCOPE


@pytest.fixture
def call_mcp(owner_api, request):
    scopes = getattr(request, "param", [PUBLIC_SCOPE])
    token = owner_api.app.state.auth.issue_query_token("diagnostics", scopes)["token"]
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
    assert "kind=norm" in payload["message"] and "kind=law" in payload["message"]
    assert payload["details"]["public_kinds"] == (
        ["law", "article", "all"] if name == "search" else ["law"]
    )
    assert json.loads(result["content"][0]["text"]) == payload


def test_document_name_and_pagination(call_mcp):
    by_name = call_mcp("document", kind="law", id="公开查询测试资料", limit=1)
    by_id = call_mcp("document", kind="law", id="public-test", limit=1)
    assert not by_name.get("isError")
    assert by_name["structuredContent"] == by_id["structuredContent"]
    assert len(by_name["structuredContent"]["document"]["articles"][0]["text"]) > 100
    assert by_name["structuredContent"]["returned"] == 1
    assert by_name["structuredContent"]["next_offset"] is None
    empty = call_mcp("document", kind="law", id="公开查询测试资料", offset=1)
    assert empty["structuredContent"]["document"]["articles"] == []
    assert empty["structuredContent"]["returned"] == 0
    assert empty["structuredContent"]["next_offset"] is None


def test_document_next_offset_reads_each_entry_once(call_mcp, owner_api):
    from chinalaw import loader
    from chinalaw.db import connect

    with connect(owner_api.app.state.config.db_path) as conn:
        loader.load_law_from_dict(conn, {
            "id": "paged-test", "title": "分页测试文件", "level": "other", "status": "unknown",
            "source_url": "https://example.com/test", "source_name": "synthetic-test",
            "articles": [{"number": str(i), "text": f"第{i}个测试条目"} for i in range(1, 6)],
        })
    seen, offset = [], 0
    while offset is not None:
        page = call_mcp("document", kind="law", id="paged-test", offset=offset, limit=2)
        payload = page["structuredContent"]
        entries = payload["document"]["articles"]
        assert payload["returned"] == len(entries)
        seen.extend(entry["number"] for entry in entries)
        offset = payload["next_offset"]
    assert seen == ["1", "2", "3", "4", "5"]


def test_missing_name_and_bad_bounds(call_mcp):
    missing = call_mcp("document", kind="law", id="不存在的法")
    assert missing["isError"]
    assert "candidates" in missing["structuredContent"]["details"]
    bad = call_mcp("document", kind="law", id="public-test", limit=101)
    assert bad["isError"]
    assert bad["structuredContent"]["status"] == 400


def test_missing_article_in_known_law_is_explicit_and_logged(call_mcp, owner_api):
    result = call_mcp("article", law="public-test", number="999")["structuredContent"]
    assert result["found"] is False
    assert result["error"] == "article_not_found" and result["reason"] == "article_null"
    assert result["article"] is None and result["law"]["id"] == "public-test"
    assert "条号" in result["hint"]
    log = owner_api.app.state.query_log.export()[-1]
    assert log["outcome"]["found"] is False


def test_invalid_article_date_is_not_reported_as_missing_content(call_mcp):
    result = call_mcp("article", law="public-test", number="1", as_of="2020-99-99")
    payload = result["structuredContent"]
    assert payload["found"] is False
    assert payload["error"] == "invalid_as_of"


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


@pytest.fixture
def article_versions(owner_api):
    from chinalaw import loader
    from chinalaw.db import connect

    with connect(owner_api.app.state.config.db_path) as conn:
        for year, text in [("2020", "旧版本完整条文。\n第二段。"), ("2025", "新版本完整条文。")]:
            loader.load_law_from_dict(conn, {
                "id": "view-test", "title": "视图测试文件", "level": "other", "status": "unknown",
                "source_url": f"https://example.test/{year}", "source_name": "synthetic-test",
                "released_at": f"{year}-01-01", "effective_at": f"{year}-02-01",
                "source_checked_at": f"{year}-03-01T00:00:00+00:00",
                "articles": [{"number": "1", "text": text}],
            })
    return owner_api.app.state.config.db_path


@pytest.mark.parametrize("as_of", [None, "2021-01-01"])
def test_compact_full_roundtrip_preserves_versions_on_both_transports(
    call_mcp, article_versions, as_of,
):
    from chinalaw import mcp, service

    arguments = {"law": "view-test", "number": "1"}
    if as_of:
        arguments["as_of"] = as_of
    original = call_mcp("article", **arguments)["structuredContent"]
    compact_result = call_mcp("article", **arguments, detail="compact")
    compact = compact_result["structuredContent"]
    assert json.loads(compact_result["content"][0]["text"]) == compact
    assert compact["article"]["text"] == ("旧版本完整条文。\n第二段。" if as_of else "新版本完整条文。")
    for key in ["selected_revision", "current_revision", "source_url", "effective_status_as_of"]:
        assert compact["law"][key] == original["law"][key]
    if as_of:
        assert compact["law"]["selected_revision"] != compact["law"]["current_revision"]
    assert "item" not in compact and "revisions" not in compact["law"]
    restored = call_mcp("article", **compact["view"]["full"]["arguments"])["structuredContent"]
    assert restored == original
    # A later full call and the public service still retain all history and aliases.
    assert service.get_article(article_versions, "view-test", "1")["law"]["revisions"]
    stdio = mcp.handle_request({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "chinalaw_article", "arguments": {**arguments, "detail": "compact"}},
    }, db_path=article_versions)["result"]["structuredContent"]
    assert stdio.pop("kind") == "article_result"
    assert stdio.pop("found") is True
    assert stdio == compact


@pytest.mark.parametrize("arguments", [
    {"law": "public-test", "number": "999"},
    {"law": "missing-test", "number": "1"},
    {"law": "public-test", "number": "1", "as_of": "invalid-date"},
])
def test_compact_preserves_missing_diagnosis(call_mcp, owner_api, arguments):
    original = call_mcp("article", **arguments)["structuredContent"]
    compact = call_mcp("article", **arguments, detail="compact")["structuredContent"]
    assert compact["found"] is False
    assert compact["error"] == original["error"]
    view = compact.pop("view")
    expected = json.loads(json.dumps(original))
    for field in view["omitted_fields"]:
        assert field.startswith("law.")
        expected["law"].pop(field.split(".")[1])
    assert compact == expected
    assert owner_api.app.state.query_log.export()[-1]["params"]["detail"] == "compact"


def test_compact_keeps_corrupt_snapshot_diagnostic(call_mcp, article_versions):
    from chinalaw.db import connect

    with connect(article_versions) as conn:
        conn.execute("UPDATE revisions SET snapshot_json = '{broken' WHERE released_at = '2020-01-01'")
    args = {"law": "view-test", "number": "1", "as_of": "2021-01-01"}
    original = call_mcp("article", **args)["structuredContent"]
    compact = call_mcp("article", **args, detail="compact")["structuredContent"]
    assert compact["found"] is False
    assert compact["error"] == original["error"] == "revision_snapshot_corrupt"
    assert compact["diagnostic"] == original["diagnostic"]


@pytest.mark.parametrize("call_mcp", [[PUBLIC_SCOPE], [PUBLIC_SCOPE, PRIVATE_SCOPE]], indirect=True)
def test_compact_obeys_private_scope(call_mcp, owner_api, request):
    original = call_mcp("article", law="private-test", number="1")["structuredContent"]
    compact = call_mcp("article", law="private-test", number="1", detail="compact")["structuredContent"]
    allowed = PRIVATE_SCOPE in request.node.callspec.params["call_mcp"]
    if allowed:
        assert compact["via"] == "norm_fallback"
        assert compact["article"] == original["article"]
        assert "item" not in compact
    else:
        assert compact["found"] is False
        assert "私域独有关键词正文" not in json.dumps(compact, ensure_ascii=False)


def test_http_rejects_unknown_article_detail(call_mcp, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Invalid detail must not query the library")

    monkeypatch.setattr("chinalaw.server.mcp_http.service.get_article", forbidden)
    result = call_mcp("article", law="public-test", number="1", detail="invalid")
    assert result["isError"]


def _assert_excerpt_matches_read(call_mcp, hit):
    import hashlib

    read = hit["read"]
    result = call_mcp(read["tool"].removeprefix("chinalaw_"), **read["arguments"])
    assert not result.get("isError")
    full = result["structuredContent"]
    if read["tool"] == "chinalaw_search":
        group, index = read["result_path"]
        text = full[group][index]["text"]
    else:
        assert full["law"]["id"] == hit["law_id"]
        assert full["article"]["number"] == hit["number"]
        text = full["article"]["text"]
    assert hashlib.sha256(text.encode()).hexdigest() == hit["text_version"]["sha256"]
    excerpt = hit["excerpt"]
    assert excerpt["text"] == text[excerpt["start_char"]:excerpt["end_char"]]
    return text


def test_brief_search_matches_stdio_and_restores_all_candidates(call_mcp, owner_api):
    from chinalaw import mcp

    args = {"query": "公开全文", "kind": "article", "in_laws": "public-test"}
    original = call_mcp("search", **args)["structuredContent"]
    result = call_mcp("search", **args, view="brief")
    brief = result["structuredContent"]
    assert json.loads(result["content"][0]["text"]) == brief
    hit = brief["article_hits"][0]
    assert "text" not in hit and hit["excerpt"]["truncated"] is False
    assert _assert_excerpt_matches_read(call_mcp, hit) == original["article_hits"][0]["text"]
    restored = call_mcp("search", **brief["view"]["full"]["arguments"])["structuredContent"]
    assert restored == original
    # Explicit defaults make the two transports' full-search descriptors equal.
    stdio = mcp.handle_request({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                               "params": {"name": "chinalaw_search", "arguments": {
                                   **args, "limit": 10, "view": "brief"}}},
                              db_path=owner_api.app.state.config.db_path)["result"]["structuredContent"]
    assert stdio == brief


@pytest.mark.parametrize("args", [
    {"query": "视图测试文件第1条", "as_of": "2021-01-01"},
    {"query": "完整条文", "as_of": "2021-01-01", "versions": "all", "in_laws": "view-test"},
])
def test_brief_read_matches_historical_citation_or_stored_keyword_text(
    call_mcp, article_versions, args,
):
    result = call_mcp("search", **args, view="brief")["structuredContent"]
    assert result["article_hits"]
    for hit in result["article_hits"]:
        text = _assert_excerpt_matches_read(call_mcp, hit)
        if hit["match_mode"] == "citation":
            assert hit["read"]["arguments"]["as_of"] == "2021-01-01"
            assert text == "旧版本完整条文。\n第二段。"
        else:
            assert "as_of" not in hit["read"]["arguments"]
            assert text == "新版本完整条文。"


def test_brief_reads_each_version_by_id_without_advancing_old_records(call_mcp, owner_api):
    from chinalaw import loader
    from chinalaw.db import connect

    with connect(owner_api.app.state.config.db_path) as conn:
        for name, year in [("old-search", "2020"), ("new-search", "2025")]:
            loader.load_law_from_dict(conn, {
                "id": name, "title": "搜索版本测试文件", "work_id": "search-work",
                "level": "other", "status": "unknown", "released_at": f"{year}-01-01",
                "effective_at": f"{year}-02-01", "source_url": f"https://example.test/{name}",
                "source_name": "synthetic-test",
                "articles": [{"number": "1", "text": f"担保责任{name}"}],
            })
    args = {"query": "担保责任", "as_of": "2026-01-01", "versions": "all"}
    result = call_mcp("search", **args, view="brief")["structuredContent"]
    assert {hit["law_id"] for hit in result["article_hits"]} == {"old-search", "new-search"}
    for hit in result["article_hits"]:
        assert _assert_excerpt_matches_read(call_mcp, hit) == "担保责任" + hit["law_id"]


@pytest.mark.parametrize("args", [
    {"query": "不存在的关键词", "kind": "article"},
    {"query": "公开全文", "in_laws": ["public-test", "missing"], "region": "北京市"},
    {"query": "公开全文", "in_laws": ["missing"], "as_of": "2021-01-01"},
])
def test_brief_retains_zero_hit_and_scope_diagnostics(call_mcp, owner_api, args):
    original = call_mcp("search", **args)["structuredContent"]
    brief = call_mcp("search", **args, view="brief")["structuredContent"]
    assert brief.pop("view")["mode"] == "brief"
    for group in ["article_hits", "norm_clause_hits"]:
        for full_hit, hit in zip(original[group], brief[group], strict=True):
            hit.pop("excerpt")
            hit.pop("read")
            hit.pop("text_version")
            hit["text"] = full_hit["text"]
    assert brief == original
    assert owner_api.app.state.query_log.export()[-1]["params"]["view"] == "brief"


@pytest.mark.parametrize("call_mcp", [[PUBLIC_SCOPE], [PUBLIC_SCOPE, PRIVATE_SCOPE]], indirect=True)
def test_brief_private_excerpts_obey_permissions_and_use_unambiguous_recovery(
    call_mcp, owner_api, request,
):
    # Deliberately collide IDs across namespaces: a public-first article lookup
    # would return the wrong source, so private results recover through search.
    from chinalaw import loader
    from chinalaw.db import connect

    with connect(owner_api.app.state.config.db_path) as conn:
        loader.load_law_from_dict(conn, {
            "id": "private-test", "title": "公开同ID文件", "level": "other", "status": "unknown",
            "source_url": "https://example.test/public", "source_name": "synthetic-test",
            "articles": [{"number": "1", "text": "公开正文不得当作私域条款。"}],
        })
    allowed = PRIVATE_SCOPE in request.node.callspec.params["call_mcp"]
    result = call_mcp("search", query="私域独有", kind="norm", view="brief")
    if allowed:
        hit = result["structuredContent"]["norm_clause_hits"][0]
        assert _assert_excerpt_matches_read(call_mcp, hit) == "私域独有关键词正文。"
        assert hit["text_version"]["basis"] == "private_record"
    else:
        assert result["isError"] and result["structuredContent"]["status"] == 403
        mixed = call_mcp("search", query="私域独有", kind="all", view="brief")["structuredContent"]
        assert mixed["norm_clause_hits"] == []


def test_http_rejects_unknown_search_view(call_mcp, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Invalid view must not query the library")

    monkeypatch.setattr("chinalaw.server.mcp_http.catalog.search_library", forbidden)
    assert call_mcp("search", query="test", view="invalid")["isError"]
