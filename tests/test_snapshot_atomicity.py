"""Concurrency and crash-boundary tests for the grounding snapshot ledger."""

from __future__ import annotations

import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from chinalaw import snapshots


class SnapshotAtomicityTests(unittest.TestCase):
    def test_concurrent_appends_have_unique_ordered_evidence_ids(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "evidence.jsonl"

            def append(index: int) -> dict:
                return snapshots.append_command_record(
                    path,
                    command="search",
                    payload={"query": f"query-{index}", "article_hits": []},
                    db_path=Path(td) / "db.sqlite",
                    argv=["search", f"query-{index}"],
                )

            with ThreadPoolExecutor(max_workers=8) as pool:
                records = list(pool.map(append, range(40)))

            loaded = snapshots.load_records(path)

        self.assertEqual(40, len(records))
        self.assertEqual(40, len(loaded))
        self.assertEqual(
            [f"E{index:04d}" for index in range(1, 41)],
            [record["evidence_id"] for record in loaded],
        )
        self.assertEqual(40, len({record["evidence_id"] for record in records}))

    def test_append_isolates_a_truncated_tail_from_the_next_record(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "evidence.jsonl"
            path.write_bytes(b'{"schema_version":')

            record = snapshots.append_command_record(
                path,
                command="search",
                payload={"query": "合同", "article_hits": []},
                db_path=Path(td) / "db.sqlite",
                argv=["search", "合同"],
            )
            loaded = snapshots.load_records(path)

        self.assertEqual("E0002", record["evidence_id"])
        self.assertEqual([record], loaded)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
