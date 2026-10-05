"""Presentation must not change source selection, evidence, or error semantics."""

import copy

import pytest

from chinalaw import mcp
from chinalaw.article_views import article_view


def test_compact_preserves_distinct_selected_current_and_all_unlisted_fields():
    article = {"number": "1", "text": "完整正文。\n第二段。", "source_url": "https://example.test/old"}
    old = {"id": "old", "effective_at": "2020-01-01"}
    new = {"id": "new", "effective_at": "2025-01-01"}
    payload = {
        "article": article, "item": article, "requested_number": "第一条",
        "law": {"id": "test", "revisions": [new, old], "work_versions": [old, new],
                "revision_count": 2, "selected_revision": old, "current_revision": new,
                "warnings": [{"code": "retain-me"}], "unknown_future_field": "retain-me"},
        "diagnostic": {"code": "retain-me"},
    }
    original = copy.deepcopy(payload)
    compact = article_view(payload, law="原始名称", number="第一条", as_of="2021-01-01",
                           detail="compact")
    assert payload == original
    assert article_view(payload, law="原始名称", number="第一条") == original
    assert compact["article"] == article
    assert compact["law"]["selected_revision"] == old
    assert compact["law"]["current_revision"] == new
    assert compact["view"]["omitted_fields"] == ["item", "law.revisions", "law.work_versions"]
    assert compact["view"]["full"] == {
        "tool": "chinalaw_article",
        "arguments": {"law": "原始名称", "number": "第一条", "as_of": "2021-01-01", "detail": "full"},
    }
    # Restoring only the documented omitted fields exactly reconstructs the original.
    compact.pop("view")
    compact["item"] = original["item"]
    for key in ["revisions", "work_versions"]:
        compact["law"][key] = original["law"][key]
    assert compact == original


@pytest.mark.parametrize("payload", [
    {"article": None, "item": None, "found": False, "error": "article_not_found"},
    {"law": "unknown", "found": False, "diagnosis": {"reason": "law_missing"}},
    {"article": {"text": "A"}, "item": {"text": "B"}},
])
def test_missing_and_nonidentical_items_are_never_dropped(payload):
    compact = article_view(payload, law="test", number="1", detail="compact")
    assert compact.pop("view")["omitted_fields"] == []
    assert compact == payload


def test_stdio_rejects_unknown_detail_before_calling_service(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Invalid view must be rejected before query execution")

    monkeypatch.setattr("chinalaw.mcp.service.get_article", forbidden)
    result = mcp.handle_request({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "chinalaw_article", "arguments": {
            "law": "test", "number": "1", "detail": "invalid",
        }},
    })
    assert result["error"]["code"] == -32602
    assert "detail" in result["error"]["message"]
