"""Literal excerpts must keep candidates, provenance, scope and retrieval identity."""

import copy
import hashlib

import pytest

from chinalaw import mcp
from chinalaw.search_views import EXCERPT_CHARS, search_view


def test_brief_keeps_order_and_metadata_and_uses_a_contiguous_unicode_slice():
    text = "😀İ前文\n" * 100 + "担保责任" + "后文\n" * 200
    hits = [{"law_id": "v1", "number": "1", "law_title": "测试文件", "text": text,
             "source_url": "https://example.test/v1", "other_versions": 2, "match_mode": "exact",
             "unknown_future_metadata": {"keep": True}},
            {"law_id": "v2", "number": "2", "text": "短条文担保责任。", "match_mode": "fuzzy",
             "fuzzy": {"matched": ["担保", "责任"]}}]
    source = {"query": "担保责任", "article_hits": hits, "law_hits": [{"id": "v1"}],
              "norm_clause_hits": [], "counts": {"total": 3}, "retrieval": {"as_of": "2021-01-01"},
              "law_filter": {"unresolved": ["missing"]}, "warnings": ["retain"]}
    before = copy.deepcopy(source)
    arguments = {"query": "担保责任", "as_of": "2021-01-01", "in_laws": ["v1", "missing"]}
    brief = search_view(source, arguments=arguments, view="brief")
    assert source == before
    assert search_view(source, arguments=arguments) == before
    restored = copy.deepcopy(brief)
    restored.pop("view")
    for old, hit in zip(hits, restored["article_hits"], strict=True):
        excerpt = hit.pop("excerpt")
        version = hit.pop("text_version")
        read = hit.pop("read")
        assert excerpt["text"] == old["text"][excerpt["start_char"]:excerpt["end_char"]]
        assert len(excerpt["text"]) <= EXCERPT_CHARS
        assert excerpt["total_chars"] == len(old["text"])
        assert "担保责任" in excerpt["text"]
        assert version["sha256"] == hashlib.sha256(old["text"].encode()).hexdigest()
        assert read["arguments"] == {"law": old["law_id"], "number": old["number"], "detail": "compact"}
        assert version["basis"] == "stored_record"
        hit["text"] = old["text"]
    assert restored == before
    assert brief["article_hits"][0]["excerpt"]["truncated"] is True
    assert brief["article_hits"][1]["excerpt"]["truncated"] is False
    assert brief["view"]["full"]["arguments"] == {**arguments, "view": "full"}


@pytest.mark.parametrize("query,text", [("missing", "a" * 500), ("i", "前" * 400 + "İ" + "后" * 200),
                                        ("a.*b", "x" * 300 + "a.*b" + "y" * 300), ("", "")])
def test_excerpt_offsets_survive_unicode_and_regex_literals(query, text):
    original = {"query": query, "article_hits": [{"law_id": "id", "number": "1", "text": text}]}
    hit = search_view(original, arguments={"query": query}, view="brief")["article_hits"][0]
    excerpt = hit["excerpt"]
    assert text[excerpt["start_char"]:excerpt["end_char"]] == excerpt["text"]
    assert len(excerpt["text"]) <= EXCERPT_CHARS
    assert 0 <= excerpt["start_char"] <= excerpt["end_char"] <= len(text)


def test_historical_citation_keeps_as_of_and_private_or_unreadable_ids_keep_search_scope():
    args = {"query": "测试第1条", "as_of": "2021-01-01", "kind": "all", "versions": "all"}
    source = {"query": args["query"],
              "article_hits": [{"law_id": "old", "number": "1", "text": "旧文", "match_mode": "citation"},
                               {"law_id": "odd", "number": "前言", "text": "前言全文"}],
              "norm_clause_hits": [{"norm_source_id": "old", "number": "1", "text": "私域正文"}]}
    brief = search_view(source, arguments=args, view="brief")
    hit = brief["article_hits"][0]
    assert hit["read"]["arguments"]["as_of"] == "2021-01-01"
    assert hit["text_version"]["basis"] == "as_of"
    assert hit["text_version"]["as_of"] == "2021-01-01"
    for group, index in [("article_hits", 1), ("norm_clause_hits", 0)]:
        read = brief[group][index]["read"]
        assert read == {"tool": "chinalaw_search", "arguments": {**args, "view": "full"},
                        "result_path": [group, index]}


def test_stdio_rejects_invalid_view_before_search(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Unknown view must be rejected before the query")

    monkeypatch.setattr("chinalaw.mcp.service.search", forbidden)
    response = mcp.handle_request({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                  "params": {"name": "chinalaw_search", "arguments": {
                                      "query": "test", "view": "invalid"}}})
    assert response["error"]["code"] == -32602
