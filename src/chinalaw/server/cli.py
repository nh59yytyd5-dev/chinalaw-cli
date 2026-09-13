"""Explicit initialization, owner setup and one-worker local/server startup."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sqlite3
import sys
import threading
import webbrowser
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from chinalaw import __version__, loader, service
from chinalaw.admin.errors import LibraryError
from chinalaw.admin.lease import ProcessLease
from chinalaw.db import DEFAULT_DB_PATH, connect, connect_readonly, current_version, migrate
from chinalaw.schema import SCHEMA_VERSION


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="chinalaw-server", description="人工资料库管理服务")
    parser.add_argument("--version", action="version", version=f"chinalaw-server {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (
        ("init", "显式初始化或升级资料库"),
        ("serve", "启动浏览器面板与只读 HTTP/MCP"),
        ("password", "设置或重置所有者密码"),
    ):
        command = commands.add_parser(name, help=help_text)
        command.add_argument(
            "--db", type=Path, default=Path(os.environ.get("CHINALAW_DB", str(DEFAULT_DB_PATH)))
        )
        if name == "init":
            command.add_argument("--with-fixtures", action="store_true", help="加载随包公开法规")
        else:
            command.add_argument(
                "--state-dir", type=Path, default=os.environ.get("CHINALAW_STATE_DIR")
            )
        if name == "serve":
            command.add_argument("--host", default=os.environ.get("CHINALAW_HOST", "127.0.0.1"))
            command.add_argument(
                "--port", type=int, default=os.environ.get("CHINALAW_PORT", "8765")
            )
            command.add_argument("--public-url", default=os.environ.get("CHINALAW_PUBLIC_URL"))
            command.add_argument(
                "--server", action="store_true", help="服务器模式，要求 HTTPS 和密码"
            )
            command.add_argument("--open", action="store_true", help="本机启动时打开浏览器")
    return parser


def initialize(db_path: Path, *, with_fixtures: bool = False) -> dict:
    db_path = db_path.expanduser().resolve()
    saved = None
    with ProcessLease(db_path.with_name(db_path.name + ".worker.lock")):
        if db_path.exists():
            with connect_readonly(db_path) as conn:
                version = current_version(conn)
                if version > SCHEMA_VERSION:
                    raise LibraryError("newer_schema", "资料库由更新版本创建，请升级软件。")
                if version < SCHEMA_VERSION:
                    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
                    saved = db_path.with_name(f"{db_path.name}.before-upgrade-{stamp}.sqlite3")
                    with closing(sqlite3.connect(saved)) as target:
                        conn.backup(target)
        with connect(db_path) as conn:
            migrate(conn)
        if with_fixtures:
            loader.load_fixtures(db_path)
    return {
        "kind": "library_initialized",
        "db_path": str(db_path),
        "schema_version": SCHEMA_VERSION,
        "upgrade_backup": str(saved) if saved else None,
        "status": service.status(db_path),
    }


def _serve(args) -> int:
    try:
        import uvicorn

        from chinalaw.server.app import create_app
        from chinalaw.server.auth_store import AuthStore
        from chinalaw.server.config import ServerConfig
    except ImportError as exc:
        raise LibraryError(
            "server_dependencies_missing", '请先安装服务组件：python -m pip install ".[server]"'
        ) from exc
    db = args.db.expanduser().resolve()
    host_for_url = f"[{args.host}]" if ":" in args.host else args.host
    config = ServerConfig(
        db,
        Path(args.state_dir).expanduser().resolve()
        if args.state_dir
        else db.with_name(db.name + ".server-state"),
        public_url=args.public_url or f"http://{host_for_url}:{args.port}",
        host=args.host,
        port=args.port,
        local_mode=not args.server,
    )
    # Fail before creating credentials if the library needs initialization.
    if not db.is_file() or service.status(db)["schema_version"] != SCHEMA_VERSION:
        raise LibraryError(
            "library_not_ready", "请先执行 chinalaw-server init 初始化或升级指定资料库。"
        )
    auth = AuthStore(config.auth_path, config.resource_url)
    app = create_app(config, auth_store=auth)
    url = config.origin
    if config.local_mode:
        url += "/#pair=" + auth.create_pairing()
    print("chinalaw 资料库：" + url, flush=True)
    print("按 Ctrl+C 停止服务。", flush=True)
    if args.open and config.local_mode:
        opener = threading.Timer(1, webbrowser.open, args=(url,))
        opener.daemon = True
        opener.start()
    uvicorn.run(app, host=config.host, port=config.port, workers=1, proxy_headers=False)
    return 0


def _password(args) -> int:
    from chinalaw.server.auth_store import AuthStore

    db = args.db.expanduser().resolve()
    state = (
        Path(args.state_dir).expanduser().resolve()
        if args.state_dir
        else db.with_name(db.name + ".server-state")
    )
    # Password hashing is origin-independent; token audiences are set when serving.
    auth = AuthStore(state / "auth.db", "http://127.0.0.1:8765/mcp")
    password = getpass.getpass("设置所有者密码（至少 12 字符）：")
    if password != getpass.getpass("再次输入："):
        raise LibraryError("password_mismatch", "两次密码不一致。")
    auth.set_password(password)
    print("所有者密码已设置，旧登录会话已失效。")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            print(
                json.dumps(
                    initialize(args.db, with_fixtures=args.with_fixtures), ensure_ascii=False
                )
            )
            return 0
        if args.command == "password":
            return _password(args)
        return _serve(args)
    except (LibraryError, ValueError, OSError, sqlite3.Error) as exc:
        print(f"chinalaw-server: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
