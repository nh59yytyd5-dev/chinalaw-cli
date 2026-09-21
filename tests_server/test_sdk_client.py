"""Use the installed MCP SDK over a real TCP server, in both protocol modes."""

from __future__ import annotations

import asyncio
import json
import socket
import threading
import time

import httpx2
import pytest
import uvicorn
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from chinalaw import loader, normsources
from chinalaw.db import connect, migrate
from chinalaw.server.app import create_app
from chinalaw.server.auth_store import PRIVATE_SCOPE, PUBLIC_SCOPE, AuthStore
from chinalaw.server.config import ServerConfig


@pytest.fixture
def tcp_library(tmp_path):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    db = tmp_path / "library.db"
    with connect(db) as conn:
        migrate(conn)
        loader.load_law_from_dict(
            conn,
            {
                "id": "sdk-law",
                "title": "公开协议测试",
                "level": "other",
                "status": "unknown",
                "source_url": "https://example.org/synthetic",
                "source_name": "synthetic",
                "articles": [{"number": "1", "text": "SDK完整正文。" * 30}],
            },
        )
        normsources.import_source_from_dict(
            conn,
            {
                "id": "sdk-private",
                "name": "私域协议测试",
                "source_type": "other",
                "clauses": [{"number": "1", "text": "私域协议测试独有内容"}],
            },
        )
    config = ServerConfig(
        db, tmp_path / "state", public_url=f"http://127.0.0.1:{port}", port=port, start_worker=False
    )
    auth = AuthStore(config.auth_path, config.resource_url)
    server = uvicorn.Server(
        uvicorn.Config(
            create_app(config, auth_store=auth), host="127.0.0.1", port=port, log_level="error"
        )
    )
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert server.started
    try:
        yield config, auth
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        sock.close()


@pytest.mark.parametrize("mode", ["legacy", "auto"])
def test_standard_sdk_reads_full_text_and_enforces_private_scope(tcp_library, mode):
    config, auth = tcp_library

    async def query(private: bool):
        scopes = [PUBLIC_SCOPE, PRIVATE_SCOPE] if private else [PUBLIC_SCOPE]
        token = auth.issue_query_token("standard-sdk", scopes)["token"]
        async with httpx2.AsyncClient(headers={"Authorization": "Bearer " + token}) as http:
            transport = streamable_http_client(config.resource_url, http_client=http)
            async with Client(transport, mode=mode, read_timeout_seconds=10) as client:
                listing = await client.list_tools()
                assert all(tool.annotations.read_only_hint for tool in listing.tools)
                assert not any(
                    "ensure" in tool.name or "import" in tool.name for tool in listing.tools
                )
                result = await client.call_tool(
                    "chinalaw_article", {"law": "sdk-law", "number": "1"}
                )
                assert not result.is_error
                assert "SDK完整正文。" * 30 in json.dumps(result.model_dump(), ensure_ascii=False)
                private_result = await client.call_tool("chinalaw_list", {"kind": "norm"})
                assert bool(private_result.is_error) is (not private)
                return client.protocol_version

    public_version = asyncio.run(query(False))
    private_version = asyncio.run(query(True))
    assert public_version == private_version
    if mode == "auto":
        assert public_version == "2026-07-28"
    else:
        assert public_version == "2025-11-25"
