"""Remote search takes a case date and filters; the applicability rules are exposed."""

from __future__ import annotations

import json

from chinalaw.server.auth_store import PUBLIC_SCOPE
from tests_server.test_query_log import _call, _mcp_session


def _payload(result: dict) -> dict:
    return result.get("structuredContent") or json.loads(result["content"][0]["text"])


def _session(api) -> dict:
    token = api.app.state.auth.issue_query_token("options", [PUBLIC_SCOPE])["token"]
    return _mcp_session(api, token)


def test_mcp_search_accepts_as_of_and_filters(owner_api):
    headers = _session(owner_api)
    response = _call(
        owner_api,
        headers,
        "chinalaw_search",
        {"query": "公开全文", "kind": "article", "as_of": "2020-01-01", "status": "unknown"},
        2,
    )
    result = response.json()["result"]
    assert not result.get("isError"), result
    payload = _payload(result)
    assert payload["retrieval"]["as_of"] == "2020-01-01"
    (hit,) = payload["article_hits"]
    assert hit["match_mode"] == "exact"
    assert hit["effective_status_as_of"] == "unknown"

    bad = _call(owner_api, headers, "chinalaw_search", {"query": "公开", "status": "valid"}, 3)
    assert bad.json()["result"]["isError"]


def test_mcp_applicable_is_available(owner_api):
    headers = _session(owner_api)
    response = _call(owner_api, headers, "chinalaw_applicable", {"date": "2020-06-01"}, 2)
    result = response.json()["result"]
    assert not result.get("isError"), result
    assert _payload(result)["kind"] == "applicability_result"


def test_rest_search_accepts_options(owner_api):
    response = owner_api.get(
        "/api/v1/search", params={"q": "公开全文", "as_of": "2020-01-01", "versions": "all"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["retrieval"]["versions"] == "all"
    bad = owner_api.get("/api/v1/search", params={"q": "公开全文", "level": "nonsense"})
    assert bad.status_code == 400
