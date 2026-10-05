"""Recovery suggestions must preserve scope and never masquerade as search hits."""

import pytest

from chinalaw import loader, service
from chinalaw.db import connect, migrate


@pytest.fixture
def library(tmp_path):
    db = tmp_path / "library.db"
    with connect(db) as conn:
        migrate(conn)
        loader.load_law_from_dict(conn, {
            "id": "test-law", "title": "测试公开文件", "level": "other", "status": "unknown",
            "source_url": "https://example.com/test", "source_name": "synthetic-test",
            "articles": [{"number": "1", "text": "证人出庭作证。"}],
        })
    return db


def test_zero_hit_suggestions_keep_all_filters_and_do_not_add_hits(library):
    result = service.search(
        library, "证人出庭 不存在的词", kind="article", in_laws=["test-law"],
        as_of="2020-01-01", level="other", status="unknown", region="上海市", versions="all",
    )
    assert result["counts"]["total"] == 0
    steps = result["guidance"]["next_steps"]
    assert steps
    for step in steps:
        args = step["arguments"]
        assert args["in_laws"] == ["test-law"]
        assert args["as_of"] == "2020-01-01"
        assert args["level"] == "other" and args["status"] == "unknown"
        assert args["region"] == "上海市" and args["versions"] == "all"
        assert args["kind"] == "article"
    assert result["article_hits"] == []


def test_missing_scope_suggests_resolution_only(library):
    result = service.search(library, "证人出庭", in_laws="不存在的法")
    assert result["counts"]["total"] == 0
    assert result["guidance"]["code"] == "unresolved_law_scope"
    assert result["guidance"]["next_steps"] == [
        {"tool": "resolve", "arguments": {"name": "不存在的法"}},
    ]


def test_title_plus_document_number_has_literal_recovery(library):
    result = service.search(library, "测试公开文件 法释〔2024〕10号", kind="law")
    assert result["counts"]["total"] == 0
    step = result["guidance"]["next_steps"][0]
    assert step["arguments"]["query"] == "测试公开文件"
    assert service.search(library, **step["arguments"])["counts"]["law"] == 1


def test_success_does_not_suggest_rewriting(library):
    result = service.search(library, "证人出庭")
    assert result["counts"]["total"] == 1
    assert "guidance" not in result


def test_substring_resolution_warns_about_title_identity(library):
    result = service.resolve(library, "公开文件")
    assert result["matched"] and result["via"] == "like_fallback"
    assert "official_title" in result["hint"]
    assert "hint" not in service.resolve(library, "测试公开文件")


def numbered_document(db, law_id, title, number, **extra):
    with connect(db) as conn:
        loader.load_law_from_dict(conn, {
            "id": law_id, "title": title, "level": "other", "status": "unknown",
            "document_number": number,
            "source_url": "https://example.com/test", "source_name": "synthetic-test",
            "articles": [{"number": "1", "text": "仅测试元数据匹配，不在正文重复文号。"}],
            **extra,
        })


def test_document_number_search_resolve_and_article(library):
    numbered_document(library, "numbered", "文号检索测试", "法释〔2024〕10号")
    result = service.search(library, "法释〔2024〕10号", kind="law")
    assert [hit["id"] for hit in result["law_hits"]] == ["numbered"]
    assert result["law_hits"][0]["match_mode"] == "document_number"
    resolved = service.resolve(library, "法释〔2024〕 10 号")
    assert resolved["id"] == "numbered" and resolved["via"] == "document_number_match"
    assert service.get_article(library, "法释〔2024〕10号", "1")["law"]["id"] == "numbered"


def test_document_number_keeps_title_and_scope_constraints(library):
    numbered_document(library, "numbered", "文号检索测试", "法释〔2024〕10号")
    good = service.search(library, "文号检索测试 法释〔2024〕10号", kind="law")
    assert good["counts"]["law"] == 1
    for arguments in [
        {"query": "无关名称 法释〔2024〕10号"},
        {"query": "法释〔2024〕10号", "in_laws": ["test-law"]},
        {"query": "法释〔2024〕10号", "level": "law"},
    ]:
        assert service.search(library, kind="law", **arguments)["counts"]["total"] == 0


def test_shared_document_number_requires_selection(library):
    for suffix in ["甲", "乙"]:
        numbered_document(library, suffix, f"同文号文件{suffix}", "法发〔2024〕12号")
    result = service.resolve(library, "法发〔2024〕12号")
    assert not result["matched"]
    assert {item["id"] for item in result["candidates"]} == {"甲", "乙"}
    search = service.search(library, "法发〔2024〕12号", kind="law")
    assert {item["id"] for item in search["law_hits"]} == {"甲", "乙"}
    assert service.get_article(library, "法发〔2024〕12号", "1") is None


def test_explicit_old_document_number_does_not_switch_to_current_version(library):
    numbered_document(library, "old", "相同标题测试", "法发〔2010〕1号",
                      work_id="synthetic:versioned", status="repealed",
                      effective_at="2010-01-01", repealed_at="2020-01-01")
    numbered_document(library, "new", "相同标题测试", "法发〔2020〕1号",
                      work_id="synthetic:versioned", status="current",
                      effective_at="2020-01-01")
    assert service.resolve(library, "法发〔2010〕1号")["id"] == "old"
    assert service.get_article(library, "法发〔2010〕1号", "1")["law"]["id"] == "old"


def test_confident_citation_does_not_add_split_digit_fuzzy_hits(library):
    numbered_document(library, "citation", "编号测试法", "法发〔2024〕1号",
                      articles=[{"number": "191", "text": "目标条文"}])
    numbered_document(library, "noise", "无关引用", "法发〔2024〕2号",
                      articles=[{"number": "1", "text": "编号测试法第19项，另见第91条"}])
    result = service.search(library, "编号测试法第191条", kind="article")
    assert result["retrieval"]["citation"]
    assert result["article_hits"][0]["law_id"] == "citation"
    assert not result["fuzzy"]["applied"]
    assert all(hit.get("match_mode") != "fuzzy" for hit in result["article_hits"])


@pytest.mark.parametrize("filters", [{"level": "law"}, {"status": "repealed"}])
def test_citation_honors_explicit_filters(library, filters):
    result = service.search(library, "测试公开文件第一条", kind="article", **filters)
    assert result["counts"]["total"] == 0
    assert not result["retrieval"]["citation"]


def test_citation_inside_scope_preserves_scope_including_inserted_articles(library):
    numbered_document(library, "inserted", "增设条文测试法", "法发〔2024〕2号",
                      articles=[{"number": "120-1", "text": "目标增设条文。"}])
    query = "增设条文测试法第一百二十条之一"
    found = service.search(library, query, in_laws=["inserted"], kind="article")
    assert found["retrieval"]["citation"]
    assert [(hit["law_id"], hit["number"]) for hit in found["article_hits"]] == [
        ("inserted", "120-1"),
    ]
    for scope in ["test-law", "不存在的法"]:
        blocked = service.search(library, query, in_laws=[scope], kind="article")
        assert blocked["counts"]["total"] == 0
        assert not blocked["retrieval"]["citation"]


def test_unknown_applicability_domain_explains_wildcard_results(library):
    with connect(library) as conn:
        conn.execute(
            "INSERT INTO applicability_rules "
            "(id,topic,domain,primary_law_id,rule_text,source_name,source_checked_at) "
            "VALUES ('test-rule','测试主题','all','test-law','测试规则','synthetic-test',"
            "'2020-01-01T00:00:00+00:00')"
        )
    result = service.applicable(library, as_of="2020-01-01", domain="不存在的领域",
                                law="test-law")
    assert result["match_count"] == 1  # Existing wildcard semantics are retained.
    assert result["coverage"]["domains"] == ["all"]
    codes = {warning["code"] for warning in result["warnings"]}
    assert {"domain_not_in_library", "law_metadata_is_reference"} <= codes


@pytest.mark.parametrize("query,text", [
    ("第500条", "依据第50项，另见10条"),
    ("刑法第191条", "刑法第19章，另见第291条"),
    ("刑法第三百零六条", "刑法第二百零六条，第三百条另有规定"),
])
def test_fuzzy_cannot_piece_together_a_different_article_number(query, text):
    from chinalaw.fuzzy import matching_fragments

    assert matching_fragments([query], text) is None
