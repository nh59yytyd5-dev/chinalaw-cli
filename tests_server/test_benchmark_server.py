"""The low-resource harness exercises real protocols without mutating its input."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path


def test_isolated_server_benchmark_preserves_source_and_audits_requests(api):
    database = api.app.state.config.db_path
    before = hashlib.sha256(database.read_bytes()).digest()
    script = Path(__file__).resolve().parents[1] / "scripts" / "benchmark-server"
    result = subprocess.run(
        [sys.executable, str(script), "--db", str(database),
         "--law", "公开查询测试资料", "--query", "公开全文", "--seconds", "1", "--concurrency", "2"],
        capture_output=True, text=True, encoding="utf-8", timeout=45,
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["source_modified"] is False
    assert report["independent_tokens"] == 2
    assert report["digest_ignored_fields"] == ["freshness_days"]
    total = 0
    for protocol in ("rest", "mcp"):
        (sample,) = report[protocol]
        assert sample["errors"] == {}
        assert sample["requests"] > 0
        total += sample["requests"]
    assert report["query_log_rows"] == total + 6  # REST/MCP warmups and two resolves.
    assert hashlib.sha256(database.read_bytes()).digest() == before
