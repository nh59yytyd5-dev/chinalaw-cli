"""Version-boundary equivalence with large works and incomplete date metadata."""

from __future__ import annotations

import sqlite3
import unittest
from contextlib import closing
from datetime import date

from chinalaw.service import _work_version_starts


class VersionDateTests(unittest.TestCase):
    def test_batched_dates_include_revisions_and_only_revising_relations(self):
        # Minimal schema permits malformed dates and a work larger than a batch;
        # neither bad dates nor publication dates may become effect boundaries.
        with closing(sqlite3.connect(":memory:")) as conn:
            conn.row_factory = sqlite3.Row
            conn.executescript("""
                CREATE TABLE revisions(law_id TEXT, effective_at TEXT, released_at TEXT);
                CREATE TABLE law_relations(
                    from_law_id TEXT, relation_type TEXT, effective_at TEXT
                );
            """)
            members = [{"id": str(i), "effective_at": "2020-01-01"} for i in range(1001)]
            conn.executemany(
                "INSERT INTO revisions VALUES (?,?,?)",
                [("0", "2021-02-01", "2021-01-01"),
                 ("400", None, "2022-01-01"),
                 ("800", "bad-date", "2023-01-01"),
                 ("1000", "2024-01-01", "2023-12-01"),
                 ("unrelated", "2025-01-01", "2024-12-01")],
            )
            conn.executemany(
                "INSERT INTO law_relations VALUES (?,?,?)",
                [("0", "revised_by", "2021-02-01"),
                 ("1000", "revised_by", "2026-01-01"),
                 ("400", "repealed_by", "2027-01-01"),
                 ("800", "revised_by", "invalid"),
                 ("unrelated", "revised_by", "2028-01-01")],
            )
            self.assertEqual(_work_version_starts(conn, members + members[:1]), {
                date(2020, 1, 1), date(2021, 2, 1), date(2024, 1, 1), date(2026, 1, 1),
            })
            self.assertEqual(_work_version_starts(conn, []), set())


if __name__ == "__main__":
    unittest.main()
