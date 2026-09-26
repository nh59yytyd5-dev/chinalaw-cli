"""Versions of one law stored as separate records behave as one legal work."""

from __future__ import annotations

import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from chinalaw import loader, service
from chinalaw.db import connect, migrate


def _law(law_id: str, *, title: str = "测试程序法", effective_at: str, **extra) -> dict:
    payload = {
        "id": law_id,
        "title": title,
        "level": "law",
        "status": extra.pop("status", "current"),
        "issuing_body": "全国人民代表大会",
        "released_at": effective_at,
        "effective_at": effective_at,
        "source_url": f"https://example.com/{law_id}",
        "source_name": "synthetic-test",
        "articles": [{"number": "1", "text": f"{law_id} 第一条正文。"}],
    }
    payload.update(extra)
    return payload


class WorkVersionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "library.db"
        with connect(self.db) as conn:
            migrate(conn)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def load(self, *payloads: dict) -> None:
        with connect(self.db) as conn:
            for payload in payloads:
                loader.load_law_from_dict(conn, payload)

    def article(self, law: str, as_of: str | None = None) -> dict | None:
        if as_of:
            return service.get_article_as_of(self.db, law, "1", as_of)
        return service.get_article(self.db, law, "1")

    def test_as_of_picks_the_version_in_force_across_records(self) -> None:
        self.load(
            _law("v2017", effective_at="2017-07-01", status="amended"),
            _law("v2021", effective_at="2022-01-01", status="amended"),
            _law("v2023", effective_at="2024-01-01"),
        )
        for as_of, expected in (
            ("2018-01-01", "v2017"),
            ("2021-12-31", "v2017"),
            ("2022-01-01", "v2021"),
            ("2025-06-01", "v2023"),
        ):
            with self.subTest(as_of=as_of):
                result = self.article("测试程序法", as_of)
                self.assertEqual(result["law"]["id"], expected)
                self.assertEqual(result["article"]["text"], f"{expected} 第一条正文。")
                self.assertEqual(result["law"]["effective_status_as_of"], "current")
        self.assertIsNone(self.article("测试程序法", "2016-01-01"))

    def test_older_version_reports_amended_once_superseded(self) -> None:
        self.load(
            _law("old", effective_at="2010-01-01", status="amended"),
            _law("new", effective_at="2020-01-01"),
        )
        law = service.get_law_as_of(self.db, "old", "2021-01-01")
        self.assertEqual(law["id"], "new")
        with connect(self.db) as conn:
            members = conn.execute("SELECT * FROM laws ORDER BY id").fetchall()
            old = next(row for row in members if row["id"] == "old")
        self.assertEqual(
            service._status_as_of(old, members, service.legal_today()), ("amended", None)
        )

    def test_scheduled_version_takes_over_on_its_effective_date(self) -> None:
        today = service.legal_today()
        future = (today + timedelta(days=30)).isoformat()
        self.load(
            _law("in-force", effective_at="2019-01-01"),
            _law("scheduled", effective_at=future, status="pending_effective"),
        )
        current = self.article("测试程序法")
        self.assertEqual(current["law"]["id"], "in-force")
        self.assertEqual(current["law"]["effective_status_as_of"], "current")
        scheduled = self.article("测试程序法", future)
        self.assertEqual(scheduled["law"]["id"], "scheduled")
        self.assertEqual(scheduled["law"]["effective_status_as_of"], "current")

    def test_pending_status_past_its_date_is_treated_as_in_force(self) -> None:
        past = (service.legal_today() - timedelta(days=3)).isoformat()
        self.load(
            _law("previous", effective_at="2015-01-01"),
            _law("arrived", effective_at=past, status="pending_effective"),
        )
        result = self.article("测试程序法")
        self.assertEqual(result["law"]["id"], "arrived")
        self.assertEqual(result["law"]["status"], "pending_effective")
        self.assertEqual(result["law"]["effective_status_as_of"], "current")

    def test_explicit_id_keeps_the_requested_version(self) -> None:
        self.load(
            _law("old", effective_at="2010-01-01", status="amended"),
            _law("new", effective_at="2020-01-01"),
        )
        result = self.article("old")
        self.assertEqual(result["law"]["id"], "old")
        self.assertEqual(result["law"]["effective_status_as_of"], "amended")

    def test_work_id_groups_a_renamed_law(self) -> None:
        self.load(
            _law("security-1993", title="测试安全法", effective_at="1993-02-22",
                 status="amended", work_id="w-counter"),
            _law("counter-2014", title="测试反间谍法", effective_at="2014-11-01",
                 work_id="w-counter"),
        )
        result = self.article("测试反间谍法", "2000-01-01")
        self.assertEqual(result["law"]["id"], "security-1993")
        self.assertEqual(result["law"]["work_id"], "w-counter")
        self.assertEqual(
            [item["id"] for item in result["law"]["work_versions"]],
            ["security-1993", "counter-2014"],
        )

    def test_repeal_with_date(self) -> None:
        self.load(
            _law("contract", title="测试合同法", effective_at="1999-10-01",
                 status="repealed", repealed_at="2021-01-01"),
        )
        self.assertEqual(
            self.article("测试合同法", "2020-12-31")["law"]["effective_status_as_of"], "current"
        )
        self.assertEqual(
            self.article("测试合同法", "2021-01-01")["law"]["effective_status_as_of"], "repealed"
        )

    def test_repeal_without_date_is_unknown_in_the_past(self) -> None:
        self.load(_law("undated", title="测试担保法", effective_at="1995-10-01", status="repealed"))
        past = self.article("测试担保法", "2000-01-01")["law"]
        self.assertEqual(past["effective_status_as_of"], "unknown")
        self.assertIn("缺少废止日期", past["effective_status_note"])
        self.assertEqual(self.article("测试担保法")["law"]["effective_status_as_of"], "repealed")

    def test_same_title_from_another_body_is_a_different_law(self) -> None:
        self.load(
            _law("national", effective_at="2010-01-01"),
            _law("other-body", effective_at="2020-01-01", issuing_body="某省人民代表大会"),
        )
        result = self.article("national", "2021-01-01")
        self.assertEqual(result["law"]["id"], "national")

    def test_status_checked_at_and_work_id_are_stored(self) -> None:
        self.load(
            _law("checked", effective_at="2010-01-01", work_id="w1",
                 status_checked_at="2026-09-20T00:00:00+08:00")
        )
        law = service.get_law(self.db, "checked")
        self.assertEqual(law["work_id"], "w1")
        self.assertEqual(law["status_checked_at"], "2026-09-20T00:00:00+08:00")

    def test_blank_work_id_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.load(_law("blank", effective_at="2010-01-01", work_id="  "))

    def test_payload_relations_are_replaced_on_reload(self) -> None:
        repeal = {
            "relation_type": "repealed_by",
            "to_law_id": "civil-code",
            "to_law_title": "测试民法典",
            "effective_at": "2021-01-01",
        }
        self.load(_law("contract", title="测试合同法", effective_at="1999-10-01",
                       status="repealed", repealed_at="2021-01-01", relations=[repeal]))
        rows = self._relations()
        self.assertEqual(
            [(row["relation_type"], row["to_law_id"], row["effective_at"]) for row in rows],
            [("repealed_by", "civil-code", "2021-01-01")],
        )
        self.load(_law("contract", title="测试合同法", effective_at="1999-10-01",
                       status="repealed", repealed_at="2021-01-01", relations=[]))
        self.assertEqual(self._relations(), [])

    def test_invalid_relation_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.load(_law("bad", effective_at="2010-01-01",
                           relations=[{"relation_type": "repealed_by"}]))

    def test_status_follows_the_selected_revision_inside_one_record(self) -> None:
        # Bundled histories keep several versions as revisions of one record.
        for year, text in (("2011", "旧文"), ("2015", "新文")):
            self.load(
                _law("criminal", title="测试刑法", effective_at=f"{year}-05-01",
                     revision_id=f"criminal@{year}",
                     articles=[{"number": "1", "text": text}])
            )
        early = self.article("测试刑法", "2012-01-01")
        self.assertEqual(early["article"]["text"], "旧文")
        self.assertEqual(early["law"]["effective_status_as_of"], "current")
        later = service.get_law_as_of(self.db, "测试刑法", "2016-01-01")
        self.assertEqual(later["effective_status_as_of"], "current")
        with connect(self.db) as conn:
            row = conn.execute("SELECT * FROM laws WHERE id = 'criminal'").fetchone()
            starts = service._work_version_starts(conn, [row])
        self.assertEqual(
            service._status_as_of(row, [row], service.legal_today(),
                                  start=min(starts), starts=starts)[0],
            "amended",
        )

    def _relations(self) -> list:
        with connect(self.db) as conn:
            return conn.execute(
                "SELECT * FROM law_relations WHERE from_law_id = 'contract'"
            ).fetchall()


if __name__ == "__main__":
    unittest.main()
