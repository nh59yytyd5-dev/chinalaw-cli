"""Compare actual Docker MCP replays; export metrics without queries or text.

Expected input: replay-baseline/replay.jsonl (original IDs), replay-pairs/replay.jsonl
(ID/default and ID/compact), replay-full/replay.jsonl (ID/full, from view.full).
The output is a new file. Raw evidence is read-only; no model or network is used.
"""

import argparse
import copy
import hashlib
import json
from pathlib import Path


def read_rows(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def payload(row):
    return json.loads("".join(
        block["text"] for block in row["result"]["content"] if block["type"] == "text"
    ))


def text_bytes(row):
    return sum(len(block["text"].encode()) for block in row["result"]["content"]
               if block["type"] == "text")


def compare(root):
    paths = [root / name for name in (
        "replay-baseline/replay.jsonl", "replay-pairs/replay.jsonl", "replay-full/replay.jsonl",
    )]
    baseline, pairs, full = map(read_rows, paths)
    paired = {row["id"]: row for row in pairs}
    restored = {row["id"]: row for row in full}
    if len(paired) != len(pairs) or len(restored) != len(full):
        raise ValueError("Duplicate replay IDs")
    expected_pairs = {f"{row['id']}/default" for row in baseline} | {
        f"{row['id']}/compact" for row in baseline if row["tool"] == "article"
    }
    expected_full = {f"{row['id']}/full" for row in baseline if row["tool"] == "article"}
    if set(paired) != expected_pairs or set(restored) != expected_full:
        raise ValueError("Replay coverage differs")
    metrics = []
    for row in baseline:
        rid = row["id"]
        default = paired[f"{rid}/default"]
        if row["result"] != default["result"] or row["params"] != default["params"]:
            raise ValueError(f"Default compatibility mismatch: {rid}")
        if row["tool"] != "article":
            continue
        compact = paired[f"{rid}/compact"]
        if compact["params"] != {**default["params"], "detail": "compact"}:
            raise ValueError(f"Compact request differs: {rid}")
        original, projected = payload(default), payload(compact)
        candidate = copy.deepcopy(projected)
        view = candidate.pop("view")
        arguments = {key: value for key, value in default["params"].items()
                     if key != "as_of" or value is not None}
        arguments["detail"] = "full"
        if view["detail"] != "compact" or view["full"] != {
            "tool": "chinalaw_article", "arguments": arguments,
        }:
            raise ValueError(f"Full lookup lost request scope: {rid}")
        for field in view["omitted_fields"]:
            if field == "item":
                if not isinstance(original.get("article"), dict) or (
                    original.get("item") != original["article"]
                ):
                    raise ValueError(f"Nonduplicate item dropped: {rid}")
                candidate["item"] = original["item"]
            elif field in {"law.revisions", "law.work_versions"}:
                key = field.split(".")[1]
                candidate["law"][key] = original["law"][key]
            else:
                raise ValueError(f"Unexpected omitted field: {rid}")
        if candidate != original:
            raise ValueError(f"Article, metadata or diagnosis changed: {rid}")
        if compact["result"]["is_error"] != default["result"]["is_error"]:
            raise ValueError(f"Error flag changed: {rid}")
        recovered = restored[f"{rid}/full"]
        if recovered["params"] != arguments or recovered["result"] != default["result"]:
            raise ValueError(f"Actual full lookup did not restore original: {rid}")
        metrics.append({"id": rid, "default_bytes": text_bytes(default),
                        "compact_bytes": text_bytes(compact), "full_bytes": text_bytes(recovered),
                        "omitted_fields": view["omitted_fields"], "content_preserved": True})
    before = sum(row["default_bytes"] for row in metrics)
    after = sum(row["compact_bytes"] for row in metrics)
    all_before = sum(text_bytes(paired[f"{row['id']}/default"]) for row in baseline)
    return {
        "schema_version": 1, "method": "Real SDK MCP replay inside Docker; no model calls.",
        "inputs": [{"path": str(p.relative_to(root)),
                    "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
                   for p in paths],
        "default_requests": len(baseline), "default_results_identical": len(baseline),
        "compact_requests": len(metrics), "full_followup_requests": len(full),
        "total_mcp_calls": len(baseline) + len(pairs) + len(full), "article_default_bytes": before,
        "article_compact_bytes": after, "article_reduction_pct": (before - after) / before * 100,
        "all_default_bytes": all_before,
        "all_with_compact_articles_bytes": all_before - before + after,
        "all_response_reduction_pct": (before - after) / all_before * 100,
        "provider_tokens": None, "rows": metrics,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    result = compare(args.root)
    with args.output.open("x") as stream:
        stream.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    public = {key: value for key, value in result.items() if key not in {"inputs", "rows"}}
    print(json.dumps(public))


if __name__ == "__main__":
    main()
