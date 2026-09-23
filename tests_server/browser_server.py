"""Disposable public-fixture server used only by browser acceptance tests.

Besides the public fixtures it wires in a deterministic offline stand-in for
the official source adapters, so the browser suite can exercise the fetch,
failure and retry paths without network access, plus one test-only route that
expires a preview on demand. Nothing here ships with the product.
"""

from __future__ import annotations

import argparse
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import uvicorn
from fastapi import Request

from chinalaw import fetch, loader
from chinalaw.admin.errors import LibraryError
from chinalaw.db import connect
from chinalaw.server.app import create_app
from chinalaw.server.auth_store import AuthStore
from chinalaw.server.config import ServerConfig
from chinalaw.server.dependencies import owner

SYNTHETIC_LAW = {
    "id": "synthetic-fetched-law",
    "title": "虚构来源抓取测试条例",
    "level": "other",
    "status": "unknown",
    "source_url": "https://example.test/synthetic-fetched-law",
    "source_name": "synthetic-source",
    "source_checked_at": "2026-09-13T00:00:00+00:00",
    "articles": [
        {"number": "1", "text": "本条例仅用于浏览器验收测试，不代表任何真实法规。"},
        {"number": "2", "text": "来源抓取生成的预览必须经过人工核对后才能入库。"},
        {"number": "3", "text": "本条为虚构结尾条款。"},
    ],
}
CANDIDATE = {
    "id": "synthetic-fetched-law",
    "title": SYNTHETIC_LAW["title"],
    "status": "unknown",
    "effective_at": "2026-01-01",
    "source_url": SYNTHETIC_LAW["source_url"],
}
FLAKY_QUERY = "重试后成功"
_failures: dict[str, int] = {}


def fake_fetch_law(db_path, name, *, source="flk_npc", list_matches=False, **_kwargs):
    """Offline replacement for ``fetch.fetch_law`` with three deterministic behaviours."""
    query = (name or "").strip()
    if "不存在" in query:
        raise fetch.FetchNotFoundError(f"no results for name={query!r} from source {source}")
    if query == FLAKY_QUERY and not list_matches and not _failures.get(query):
        _failures[query] = 1
        raise fetch.FetchSourceError("synthetic upstream outage; retry succeeds")
    if list_matches:
        return {
            "kind": "law_fetch_candidates",
            "source": source,
            "name": query,
            "candidates": [CANDIDATE],
        }
    return {
        "kind": "law_fetch",
        "source": source,
        "name": query,
        "matched_id": CANDIDATE["id"],
        "candidates": [CANDIDATE],
        "law": {**SYNTHETIC_LAW, "articles": [dict(a) for a in SYNTHETIC_LAW["articles"]]},
        "dry_run": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=18766)
    args = parser.parse_args()
    fetch.fetch_law = fake_fetch_law
    with tempfile.TemporaryDirectory(prefix="chinalaw-browser-test-") as folder:
        root = Path(folder)
        db = root / "library.db"
        loader.load_fixtures(db)
        config = ServerConfig(
            db, root / "state", port=args.port, public_url=f"http://127.0.0.1:{args.port}"
        )
        auth = AuthStore(config.auth_path, config.resource_url)
        auth.set_password("Synthetic-browser-owner-123")
        app = create_app(config, auth_store=auth)

        def expire_draft(identifier: str, request: Request) -> dict:
            owner(request)
            stamp = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
            with connect(db) as conn:
                changed = conn.execute(
                    "UPDATE library_drafts SET expires_at = ? WHERE id = ?", (stamp, identifier)
                ).rowcount
            if changed != 1:
                raise LibraryError("draft_not_found", "待确认资料不存在。", status=404)
            return {"id": identifier, "expires_at": stamp}

        # Application routes must precede the SDK catch-all mount added by create_app.
        app.add_api_route("/__test__/drafts/{identifier}/expire", expire_draft, methods=["POST"])
        app.router.routes.insert(0, app.router.routes.pop())
        uvicorn.run(
            app,
            host="127.0.0.1",
            port=args.port,
            access_log=False,
            log_level="warning",
            proxy_headers=False,
        )


if __name__ == "__main__":
    main()
