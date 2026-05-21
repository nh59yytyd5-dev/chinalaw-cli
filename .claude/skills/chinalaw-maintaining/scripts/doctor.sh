#!/usr/bin/env bash
# chinalaw doctor —— 一键自检脚本。
#
# 跑 status / fixture 数 / applicability seed 数 / source freshness。
# 任何一项异常都会以非零退出码退出，便于 CI / cron 接入。
#
# 使用：
#   bash .claude/skills/chinalaw-maintaining/scripts/doctor.sh
#   bash .claude/skills/chinalaw-maintaining/scripts/doctor.sh --db /path/to/other.db
#   bash .claude/skills/chinalaw-maintaining/scripts/doctor.sh --quiet
set -euo pipefail

DB_PATH=""
QUIET=0

while [ $# -gt 0 ]; do
    case "$1" in
        --db) DB_PATH="$2"; shift 2 ;;
        --quiet) QUIET=1; shift ;;
        -h|--help)
            cat <<'EOF'
Usage: doctor.sh [--db <path>] [--quiet]

Checks chinalaw local database health:
  - status JSON parses
  - laws count > 0 and articles count > 0
  - applicability_rules count > 0 (warning only)
  - source_freshness within 90 days (warning only)

Exits non-zero on any failure (errors). Warnings only print to stderr.
Combine with cron / CI for periodic monitoring.
EOF
            exit 0
            ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
done

if command -v chinalaw >/dev/null 2>&1; then
    CHINALAW_CMD=(chinalaw)
elif [ -d "src/chinalaw" ]; then
    CHINALAW_CMD=(env "PYTHONPATH=src" python3 -m chinalaw)
else
    echo "error: chinalaw not on PATH and src/chinalaw not present" >&2
    exit 1
fi

DB_FLAG=()
if [ -n "$DB_PATH" ]; then
    DB_FLAG=(--db "$DB_PATH")
fi

# Capture status JSON to a temp file. Avoids the heredoc-vs-pipe stdin clash
# where python3 - <<EOF would consume the heredoc and not the piped JSON.
TMP_JSON="$(mktemp -t chinalaw-doctor.XXXXXX.json)"
trap 'rm -f "$TMP_JSON"' EXIT

if ! "${CHINALAW_CMD[@]}" "${DB_FLAG[@]}" status --format json > "$TMP_JSON" 2>&1; then
    echo "fail: chinalaw status command failed:" >&2
    cat "$TMP_JSON" >&2
    exit 1
fi

QUIET="$QUIET" STATUS_FILE="$TMP_JSON" python3 <<'PYEOF'
import json
import os
import sys
import datetime

quiet = os.environ.get("QUIET") == "1"
status_file = os.environ["STATUS_FILE"]

try:
    with open(status_file, encoding="utf-8") as fh:
        payload = json.load(fh)
except json.JSONDecodeError as exc:
    print(f"fail: status output is not JSON: {exc}", file=sys.stderr)
    sys.exit(1)

errors = []
warnings = []

# field-name fallbacks accommodate older / newer status payload shapes
laws_count = payload.get("laws", payload.get("law_count", 0))
articles_count = payload.get("articles", payload.get("article_count", 0))
norm_packs = payload.get("norm_packs", payload.get("normpack_count", 0))
applicability = payload.get(
    "applicability_rules", payload.get("applicability_rule_count", 0)
)
relations = payload.get("law_relations", payload.get("law_relation_count", 0))
schema_version = payload.get("schema_version")

if laws_count <= 0:
    errors.append(f"laws count is {laws_count}; run `chinalaw sync --fixtures`")
if articles_count <= 0:
    errors.append(
        f"articles count is {articles_count}; run `chinalaw sync --fixtures`"
    )
if applicability <= 0:
    warnings.append(
        f"applicability_rules count is {applicability}; "
        "run `chinalaw sync --applicability`"
    )

# source freshness check (90-day threshold; warning only, not error)
freshness = payload.get("source_freshness", {}) or {}
now = datetime.datetime.now(datetime.timezone.utc)
for source, ts in freshness.items():
    if ts is None:
        warnings.append(f"source {source} never synced")
        continue
    try:
        synced_at = datetime.datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if synced_at.tzinfo is None:
            synced_at = synced_at.replace(tzinfo=datetime.timezone.utc)
        days = (now - synced_at).days
        if days > 90:
            warnings.append(
                f"source {source} synced {days}d ago (>90d threshold)"
            )
    except (ValueError, AttributeError):
        warnings.append(f"source {source} timestamp unparsable: {ts}")

if not quiet:
    print("chinalaw doctor:")
    print(f"  schema_version    : {schema_version}")
    print(f"  laws              : {laws_count}")
    print(f"  articles          : {articles_count}")
    print(f"  norm_packs        : {norm_packs}")
    print(f"  applicability     : {applicability}")
    print(f"  law_relations     : {relations}")
    if freshness:
        print("  source_freshness  :")
        for source, ts in freshness.items():
            print(f"    {source}: {ts}")
    print()

for w in warnings:
    print(f"warning: {w}", file=sys.stderr)
for e in errors:
    print(f"fail: {e}", file=sys.stderr)

if errors:
    sys.exit(1)
print("ok" if not warnings else "ok (with warnings)")
PYEOF
