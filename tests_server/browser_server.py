"""Disposable public-fixture server used only by browser acceptance tests."""

from __future__ import annotations

import argparse
import tempfile
from pathlib import Path

import uvicorn

from chinalaw import loader
from chinalaw.server.app import create_app
from chinalaw.server.auth_store import AuthStore
from chinalaw.server.config import ServerConfig


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=18766)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="chinalaw-browser-test-") as folder:
        root = Path(folder)
        db = root / "library.db"
        loader.load_fixtures(db)
        config = ServerConfig(
            db, root / "state", port=args.port, public_url=f"http://127.0.0.1:{args.port}"
        )
        auth = AuthStore(config.auth_path, config.resource_url)
        auth.set_password("Synthetic-browser-owner-123")
        uvicorn.run(
            create_app(config, auth_store=auth),
            host="127.0.0.1",
            port=args.port,
            access_log=False,
            log_level="warning",
            proxy_headers=False,
        )


if __name__ == "__main__":
    main()
