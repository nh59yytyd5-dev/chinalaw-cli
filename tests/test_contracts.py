from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from chinalaw import applicability, normpacks
from chinalaw.contracts import validate_law_payload
from chinalaw.db import connect, migrate
from chinalaw.loader import load_law_from_dict


def _payload(**overrides: object) -> dict:
    payload = {
        "id": "contract-test-law",
        "title": "中华人民共和国契约测试法",
        "short_title": "契约测试法",
        "aliases": ["契约测试法"],
        "level": "law",
        "status": "current",
        "issuing_body": "测试机关",
        "document_number": None,
        "released_at": "2026-01-01",
        "effective_at": "2026-02-01",
        "repealed_at": None,
        "source_url": "https://example.test/contract-law",
        "source_name": "example.test",
        "source_checked_at": "2026-08-06T12:00:00+08:00",
        "source_hash": "a" * 64,
        "articles": [
            {
                "number": "1",
                "number_display": "第一条",
                "text": "本法用于验证统一契约。",
                "part": None,
                "position": 1,
            }
        ],
    }
    payload.update(overrides)
    return payload


class LawPayloadContractTests(unittest.TestCase):
    def test_valid_payload_is_returned_unchanged(self) -> None:
        payload = _payload()
        self.assertIs(validate_law_payload(payload, require_articles=True), payload)

    def test_rejects_unknown_level_and_status(self) -> None:
        for field, value in (
            ("level", "departmental_rule"),
            ("status", "temporarily_valid"),
        ):
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, field):
                validate_law_payload(_payload(**{field: value}))

    def test_rejects_noncanonical_dates_and_datetimes(self) -> None:
        invalid = (
            ("released_at", "2026/01/01"),
            ("effective_at", "2026-02-31"),
            ("source_checked_at", "2026-08-06"),
            ("source_checked_at", "2026-08-06T12:00:00"),
        )
        for field, value in invalid:
            with self.subTest(field=field, value=value), self.assertRaisesRegex(
                ValueError,
                field,
            ):
                validate_law_payload(_payload(**{field: value}))

    def test_rejects_unapproved_source_scheme(self) -> None:
        with self.assertRaisesRegex(ValueError, "source_url"):
            validate_law_payload(_payload(source_url="ftp://example.test/law"))

    def test_rejects_bad_root_and_article_shapes(self) -> None:
        with self.assertRaisesRegex(ValueError, "aliases"):
            validate_law_payload(_payload(aliases="契约测试法"))
        with self.assertRaisesRegex(ValueError, "articles"):
            validate_law_payload(_payload(articles={}))
        with self.assertRaisesRegex(ValueError, "article #1"):
            validate_law_payload(_payload(articles=["第一条"]))

    def test_rejects_duplicate_numbers_and_positions(self) -> None:
        duplicate_number = _payload()["articles"][0] | {
            "text": "重复条号。",
            "position": 2,
        }
        with self.assertRaisesRegex(ValueError, "duplicate article number"):
            validate_law_payload(
                _payload(articles=[_payload()["articles"][0], duplicate_number])
            )

        second = {
            "number": "2",
            "number_display": "第二条",
            "text": "重复位置。",
            "position": 1,
        }
        with self.assertRaisesRegex(ValueError, "duplicate article position"):
            validate_law_payload(
                _payload(articles=[_payload()["articles"][0], second])
            )

    def test_rejects_non_normalized_article_number(self) -> None:
        article = _payload()["articles"][0] | {"number": "第一条"}
        with self.assertRaisesRegex(ValueError, "must be normalized"):
            validate_law_payload(_payload(articles=[article]))

    def test_require_articles_distinguishes_public_payload_from_stub(self) -> None:
        validate_law_payload(_payload(articles=[]), require_articles=False)
        with self.assertRaisesRegex(ValueError, "at least one article"):
            validate_law_payload(_payload(articles=[]), require_articles=True)


class LoaderContractTests(unittest.TestCase):
    def test_loader_normalizes_before_writing(self) -> None:
        payload = _payload(
            articles=[
                {
                    "number": "第一条",
                    "number_display": "第一条",
                    "text": "第一条正文。",
                    "position": 99,
                },
                {
                    "number": "第二条",
                    "number_display": "第二条",
                    "text": "第二条正文。",
                    "position": 99,
                },
            ]
        )
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "contract.db"
            with connect(db_path) as conn:
                migrate(conn)
                self.assertEqual(load_law_from_dict(conn, payload), 2)
                rows = conn.execute(
                    "SELECT number, position FROM articles ORDER BY position"
                ).fetchall()
        self.assertEqual([(row["number"], row["position"]) for row in rows], [("1", 1), ("2", 2)])

    def test_loader_rejects_invalid_payload_before_any_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "contract.db"
            with connect(db_path) as conn:
                migrate(conn)
                with self.assertRaisesRegex(ValueError, "level"):
                    load_law_from_dict(conn, _payload(level="departmental_rule"))
                laws = conn.execute("SELECT COUNT(*) FROM laws").fetchone()[0]
                revisions = conn.execute("SELECT COUNT(*) FROM revisions").fetchone()[0]
        self.assertEqual(laws, 0)
        self.assertEqual(revisions, 0)

    def test_loader_preserves_explicit_local_stub_support(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "contract.db"
            with connect(db_path) as conn:
                migrate(conn)
                self.assertEqual(load_law_from_dict(conn, _payload(articles=[])), 0)
                row = conn.execute(
                    "SELECT id FROM laws WHERE id = ?", ("contract-test-law",)
                ).fetchone()
        self.assertIsNotNone(row)


class NormPayloadContractTests(unittest.TestCase):
    def test_empty_reference_item_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "norm.db"
            with self.assertRaisesRegex(ValueError, "reference_text"):
                normpacks.add_item_to_pack(
                    db_path,
                    "空引用包",
                    {"item_type": "reference"},
                    create=True,
                )

    def test_new_pack_slug_cannot_fuzzy_match_an_existing_pack(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "norm.db"
            normpacks.add_item_to_pack(
                db_path,
                "contract-review-2024",
                {"item_type": "reference", "reference_text": "既有引用。"},
                create=True,
            )
            created = normpacks.add_item_to_pack(
                db_path,
                "contract review",
                {"item_type": "reference", "reference_text": "新包引用。"},
                create=True,
            )
            packs = normpacks.list_packs(db_path)

        self.assertEqual(created["pack_id"], "contract-review")
        self.assertEqual(
            {pack["id"]: pack["item_count"] for pack in packs},
            {"contract-review-2024": 1, "contract-review": 1},
        )

    def test_applicability_dates_are_validated_before_any_write(self) -> None:
        payload = {
            "source_name": "manual-test",
            "source_url": "local-seed:test",
            "source_checked_at": "2026-08-06T00:00:00+00:00",
            "relations": [
                {
                    "relation_type": "replaces",
                    "from_law_id": "new-law",
                    "to_law_id": "old-law",
                    "effective_at": "2026-01-01",
                }
            ],
            "rules": [
                {
                    "topic": "测试",
                    "primary_law_id": "new-law",
                    "effective_from": "2026-02-01",
                    "effective_to": "2026-01-01",
                    "rule_text": "测试规则。",
                }
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "applicability.db"
            with connect(db_path) as conn:
                migrate(conn)
                with self.assertRaisesRegex(ValueError, "effective_from"):
                    applicability.load_applicability_from_dict(conn, payload)
                relation_count = conn.execute(
                    "SELECT COUNT(*) FROM law_relations"
                ).fetchone()[0]
                rule_count = conn.execute(
                    "SELECT COUNT(*) FROM applicability_rules"
                ).fetchone()[0]

        self.assertEqual(relation_count, 0)
        self.assertEqual(rule_count, 0)


if __name__ == "__main__":
    unittest.main()
