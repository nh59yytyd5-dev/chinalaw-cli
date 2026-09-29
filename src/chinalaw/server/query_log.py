"""Record read queries so the owner can see how the library is actually used.

The log lives in the server state directory, never in the library: library
backups and restores do not carry it. Recording is best effort; a storage
failure is logged and the query still succeeds.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from collections.abc import Callable
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypeVar

LOG = logging.getLogger(__name__)

T = TypeVar("T")

QUERY_LOG_SCHEMA = """
CREATE TABLE IF NOT EXISTS queries (
    id INTEGER PRIMARY KEY,
    at TEXT NOT NULL,
    channel TEXT NOT NULL,
    client TEXT,
    tool TEXT NOT NULL,
    params_json TEXT NOT NULL,
    outcome_json TEXT NOT NULL,
    error TEXT,
    duration_ms INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_queries_at ON queries(at);
"""

# Result keys worth keeping per tool: enough to tell hits from misses without
# copying normative text into the log.
_OUTCOME_KEYS = {
    "search": ("strategy", "counts"),
    "applicable": ("ok", "match_count"),
    "resolve": ("matched", "via", "id"),
    "article": ("kind", "found", "law_id", "reason", "error"),
    "list": ("total",),
    "document": ("total", "has_more"),
}


def summarize(tool: str, result: Any) -> dict:
    if not isinstance(result, dict):
        return {"found": result is not None}
    outcome = {key: result[key] for key in _OUTCOME_KEYS.get(tool, ()) if key in result}
    if tool == "article" and isinstance(result.get("article"), dict):
        law = result.get("law") if isinstance(result.get("law"), dict) else {}
        outcome.update(found=True, law_id=law.get("id"), number=result["article"].get("number"))
    return outcome


class QueryLog:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with closing(sqlite3.connect(self.path, timeout=10)) as conn:
            conn.executescript(QUERY_LOG_SCHEMA)
        if os.name == "posix":
            self.path.chmod(0o600)

    def record(
        self,
        *,
        channel: str,
        client: str | None,
        tool: str,
        params: dict,
        outcome: dict,
        error: str | None,
        duration_ms: int,
    ) -> None:
        try:
            with closing(sqlite3.connect(self.path, timeout=1)) as conn, conn:
                conn.execute(
                    "INSERT INTO queries(at, channel, client, tool, params_json, outcome_json, "
                    "error, duration_ms) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        datetime.now(timezone.utc).isoformat(timespec="seconds"),
                        channel,
                        client,
                        tool,
                        json.dumps(params, ensure_ascii=False),
                        json.dumps(outcome, ensure_ascii=False),
                        error,
                        duration_ms,
                    ),
                )
        except sqlite3.Error:
            LOG.warning("query log write failed", exc_info=True)

    def run(
        self,
        call: Callable[[], T],
        *,
        channel: str,
        client: str | None,
        tool: str,
        params: dict,
    ) -> T:
        """Run one query and record its outcome, including failures."""
        started = time.perf_counter()
        try:
            result = call()
        except Exception as exc:
            self.record(
                channel=channel,
                client=client,
                tool=tool,
                params=params,
                outcome={},
                error=getattr(exc, "code", None) or type(exc).__name__,
                duration_ms=round((time.perf_counter() - started) * 1000),
            )
            raise
        self.record(
            channel=channel,
            client=client,
            tool=tool,
            params=params,
            outcome=summarize(tool, result),
            error=None,
            duration_ms=round((time.perf_counter() - started) * 1000),
        )
        return result

    def export(self, *, since: str | None = None, limit: int | None = None) -> list[dict]:
        sql = "SELECT * FROM queries"
        args: list[Any] = []
        if since:
            sql += " WHERE at >= ?"
            args.append(since)
        sql += " ORDER BY id"
        if limit:
            sql += " LIMIT ?"
            args.append(limit)
        with closing(sqlite3.connect(self.path, timeout=10)) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(sql, args).fetchall()
        return [
            {
                "at": row["at"],
                "channel": row["channel"],
                "client": row["client"],
                "tool": row["tool"],
                "params": json.loads(row["params_json"]),
                "outcome": json.loads(row["outcome_json"]),
                "error": row["error"],
                "duration_ms": row["duration_ms"],
            }
            for row in rows
        ]
