import json
from pathlib import Path

from chinalaw import applicability, loader, service
from chinalaw.db import connect, migrate


def test_verified_mapping_prefers_exact_public_id(tmp_path):
    db = tmp_path / "rules.db"
    with connect(db) as conn:
        migrate(conn)
        for identifier in ["legacy", "official"]:
            loader.load_law_from_dict(
                conn,
                {
                    "id": identifier,
                    "title": "测试法",
                    "level": "law",
                    "status": "unknown",
                    "source_url": "https://example.com/test",
                    "articles": [{"number": "1", "text": "测试正文"}],
                },
            )
        applicability.load_applicability_from_dict(
            conn,
            {
                "law_id_map": {"legacy": "official"},
                "rules": [
                    {
                        "id": "test",
                        "topic": "测试",
                        "primary_law_id": "legacy",
                        "rule_text": "仅为线索",
                    }
                ],
            },
        )
    result = service.applicable(db, as_of="2026-01-01")
    assert result["matches"][0]["primary_law_id"] == "official"
    assert result["coverage"] == {
        "rules_loaded": 1, "topics": ["测试"], "domains": ["all"], "exhaustive": False,
    }


def test_reviewed_seeds_have_bounds_and_official_sources(tmp_path):
    db = tmp_path / "rules.db"
    applicability.load_applicability_fixtures(db)
    assert service.applicable(db, as_of="2010-01-01", topic="公司治理")["match_count"] == 0
    assert service.applicable(db, as_of="2018-10-26", topic="公司治理")["match_count"] == 1
    assert service.applicable(db, as_of="1998-01-01", topic="合同")["match_count"] == 0
    for path in Path("data/applicability").glob("*.json"):
        payload = json.loads(path.read_text())
        assert payload["source_url"].startswith("https://flk.npc.gov.cn/")
        for rule in payload["rules"]:
            assert rule["confidence"] == "source_reviewed_guidance"
            if rule.get("effective_to"):
                assert rule["effective_from"] <= rule["effective_to"]
