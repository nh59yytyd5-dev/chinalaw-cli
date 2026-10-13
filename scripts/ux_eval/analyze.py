"""Aggregate completed evaluation outcomes; counts are observations, not legal grades."""

import argparse
import json
import sqlite3
from collections import Counter
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    summaries = []
    for path in sorted(args.root.glob("*/run.json")):
        run = json.loads(path.read_text())
        if run["mode"] != "run":
            continue
        item = {
            "id": path.parent.name,
            "exit_code": run["exit_code"],
            "seconds": round(run["ended"] - run["started"]),
        }
        log = path.parent / "server-state/queries.db"
        if log.exists():
            with sqlite3.connect(f"file:{log.resolve()}?mode=ro", uri=True) as conn:
                rows = conn.execute("SELECT tool, outcome_json, error FROM queries").fetchall()
            item["mcp_calls"] = len(rows)
            item["tools"] = dict(Counter(row[0] for row in rows))
            item["errors"] = dict(Counter(row[2] for row in rows if row[2]))
            item["zero_searches"] = sum(
                row[0] == "search" and json.loads(row[1]).get("counts", {}).get("total") == 0
                for row in rows
            )
            item["missing_articles"] = sum(
                row[0] == "article" and json.loads(row[1]).get("found") is False for row in rows
            )
        summary = path.parent / "summary.json"
        if summary.exists():
            dsh = json.loads(summary.read_text())
            item.update(usage=dsh["usage"], clipped_results=dsh.get("clipped_results"))
        facts = path.parent / "profile-facts.json"
        item["profile"] = json.loads(facts.read_text()) if facts.exists() else "default-clipping"
        summaries.append(item)
    result = {
        "completed": sum(item["exit_code"] == 0 for item in summaries),
        "failed": sum(item["exit_code"] != 0 for item in summaries),
        "mcp_calls": sum(item.get("mcp_calls", 0) for item in summaries),
        "runs": summaries,
    }
    (args.root / "evaluation-summary.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2)
    )
    print(json.dumps({key: value for key, value in result.items() if key != "runs"}))


if __name__ == "__main__":
    main()
