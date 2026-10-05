"""Prepare and verify real MCP recovery calls for the optional brief search view.

Raw inputs stay private. Public output contains only IDs, sizes, hashes and checks.
prepare writes a new inputs-recovery directory; compare writes a new metrics file.
"""

import argparse
import copy
import hashlib
import json
from pathlib import Path

from compare_article_views import payload, read_rows, text_bytes

GROUPS = ("article_hits", "norm_clause_hits")


def _recovery_calls(pairs):
    for row in pairs:
        if not str(row["id"]).endswith("/brief") or row["result"]["is_error"]:
            continue
        rid = str(row["id"]).split("/")[0]
        body = payload(row)
        call = body["view"]["full"]
        yield {"id": rid + "/full-search", "tool": "search", "params": call["arguments"]}
        for group in GROUPS:
            for index, hit in enumerate(body.get(group, [])):
                if "read" not in hit:
                    continue
                call = hit["read"]
                tool = call["tool"].removeprefix("chinalaw_")
                if tool not in {"article", "search"}:
                    raise ValueError("Unexpected recovery tool")
                yield {"id": f"{rid}/{group}/{index}", "tool": tool, "params": call["arguments"]}


def prepare(root):
    pairs = read_rows(root / "replay-pairs/replay.jsonl")
    calls = list(_recovery_calls(pairs))
    target = root / "inputs-recovery"
    target.mkdir(mode=0o700)
    (target / "library.db").symlink_to("../inputs-baseline/library.db")
    output = target / "history-anonymized.json"
    output.write_text(json.dumps(calls, ensure_ascii=False, indent=2) + "\n")
    output.chmod(0o600)
    print(json.dumps({"prepared_recovery_calls": len(calls)}))


def _check_hit(original, hit, recovered, group):
    excerpt = hit["excerpt"]
    text = original["text"]
    start, end = excerpt["start_char"], excerpt["end_char"]
    if not (0 <= start <= end <= len(text) and end - start <= 240):
        raise ValueError("Excerpt bounds invalid")
    if excerpt["text"] != text[start:end] or excerpt["total_chars"] != len(text):
        raise ValueError("Excerpt is not a literal original slice")
    if excerpt["truncated"] != (start != 0 or end != len(text)):
        raise ValueError("Excerpt truncation flag invalid")
    expected_hash = hashlib.sha256(text.encode()).hexdigest()
    if hit["text_version"]["sha256"] != expected_hash:
        raise ValueError("Text fingerprint differs")
    if recovered["result"]["is_error"] or recovered["params"] != hit["read"]["arguments"]:
        raise ValueError("Recovery call failed or arguments changed")
    body = payload(recovered)
    if hit["read"]["tool"] == "chinalaw_article":
        if body["law"]["id"] != original["law_id"]:
            raise ValueError("Recovery changed the law record")
        if body["article"]["number"] != original["number"]:
            raise ValueError("Recovery changed the article number")
        if body["law"].get("source_url") != original.get("source_url"):
            raise ValueError("Recovery changed provenance")
        recovered_text = body["article"]["text"]
    else:
        path = hit["read"]["result_path"]
        if path[0] != group:
            raise ValueError("Recovery changed hit group")
        full_hit = body[path[0]][path[1]]
        if full_hit != original:
            raise ValueError("Scoped search recovery changed the hit")
        recovered_text = full_hit["text"]
    if recovered_text != text:
        raise ValueError("Recovered full text differs")


def _check_brief(original, brief, rid, recovered):
    candidate = copy.deepcopy(brief)
    candidate.pop("view")
    hits, truncated = 0, 0
    for group in GROUPS:
        if len(original.get(group, [])) != len(candidate.get(group, [])):
            raise ValueError("Candidate count changed")
        hit_pairs = zip(original.get(group, []), candidate.get(group, []), strict=True)
        for index, (old, hit) in enumerate(hit_pairs):
            if "excerpt" not in hit:
                if hit != old:
                    raise ValueError("Nontext hit changed")
                continue
            _check_hit(old, hit, recovered[f"{rid}/{group}/{index}"], group)
            hits += 1
            truncated += hit["excerpt"]["truncated"]
            for key in ("excerpt", "read", "text_version"):
                hit.pop(key)
            hit["text"] = old["text"]
    if candidate != original:
        raise ValueError("Candidate order, metadata, scope or diagnostic changed")
    return hits, truncated


def compare(root):
    paths = [root / name for name in (
        "replay-baseline/replay.jsonl", "replay-pairs/replay.jsonl", "replay-recovery/replay.jsonl",
    )]
    baseline, pairs, recovery = map(read_rows, paths)
    paired = {row["id"]: row for row in pairs}
    recovered = {row["id"]: row for row in recovery}
    expected_pairs = {f"{row['id']}/default" for row in baseline} | {
        f"{row['id']}/brief" for row in baseline if row["tool"] == "search"
    }
    if len(pairs) != len(paired) or set(paired) != expected_pairs:
        raise ValueError("Paired replay coverage differs")
    expected_recovery = {call["id"]: call for call in _recovery_calls(pairs)}
    if len(recovery) != len(recovered) or set(recovered) != set(expected_recovery):
        raise ValueError("Recovery coverage differs")
    for rid, expected in expected_recovery.items():
        if any(recovered[rid][k] != expected[k] for k in ["tool", "params"]):
            raise ValueError("Recovery request differs")
    metrics = []
    for row in baseline:
        rid = row["id"]
        default = paired[f"{rid}/default"]
        if row["result"] != default["result"] or row["params"] != default["params"]:
            raise ValueError(f"Default compatibility mismatch: {rid}")
        if row["tool"] != "search":
            continue
        brief = paired[f"{rid}/brief"]
        if brief["params"] != {**default["params"], "view": "brief"}:
            raise ValueError("Brief request differs")
        hits = truncated = 0
        if row["result"]["is_error"]:
            if brief["result"] != row["result"]:
                raise ValueError("Business error changed")
        else:
            full = recovered[f"{rid}/full-search"]
            if full["result"] != default["result"]:
                raise ValueError("Full-search recovery changed")
            hits, truncated = _check_brief(payload(default), payload(brief), rid, recovered)
        metrics.append({"id": rid, "default_bytes": text_bytes(default),
                        "brief_bytes": text_bytes(brief), "excerpts": hits, "truncated": truncated,
                        "candidates_metadata_preserved": True, "full_text_reads_verified": hits})
    before = sum(row["default_bytes"] for row in metrics)
    after = sum(row["brief_bytes"] for row in metrics)
    all_before = sum(text_bytes(row) for row in baseline)
    return {
        "schema_version": 1, "method": "Real SDK MCP Docker replay; no model calls.",
        "inputs": [{"path": str(p.relative_to(root)),
                    "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in paths],
        "default_requests_identical": len(baseline), "brief_requests": len(metrics),
        "recovery_calls": len(recovery),
        "total_mcp_calls": len(baseline) + len(pairs) + len(recovery),
        "excerpts": sum(m["excerpts"] for m in metrics),
        "truncated_excerpts": sum(m["truncated"] for m in metrics),
        "search_default_bytes": before, "search_brief_bytes": after,
        "search_reduction_pct": (before - after) / before * 100,
        "all_default_bytes": all_before, "all_with_brief_search_bytes": all_before - before + after,
        "all_response_reduction_pct": (before - after) / all_before * 100,
        "provider_tokens": None, "rows": metrics,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["prepare", "compare"])
    parser.add_argument("root", type=Path)
    parser.add_argument("output", type=Path, nargs="?")
    args = parser.parse_args()
    if args.mode == "prepare":
        prepare(args.root)
        return
    if args.output is None:
        parser.error("compare requires a new output file")
    result = compare(args.root)
    with args.output.open("x") as stream:
        stream.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({k: v for k, v in result.items() if k not in {"inputs", "rows"}}))


if __name__ == "__main__":
    main()
