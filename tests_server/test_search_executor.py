"""Admission bounds, cancellation and protocol audit behavior."""
from __future__ import annotations

import contextvars
import threading
from concurrent.futures import ThreadPoolExecutor

import anyio
import pytest

from chinalaw.admin import catalog
from chinalaw.server.auth_store import PUBLIC_SCOPE
from chinalaw.server.search_executor import SearchBusy, SearchExecutor
from tests_server.test_query_log import _call, _mcp_session


def test_waiters_cancel_without_losing_running_permit():
    async def scenario():
        executor = SearchExecutor(1, 1)
        entered, release = threading.Event(), threading.Event()
        context = contextvars.ContextVar("scope", default="missing")
        context.set("private")
        observed = []

        def work():
            entered.set()
            assert release.wait(5)
            observed.append(context.get())

        async def waiting(scope):
            with scope:
                await executor.run(lambda: observed.append("cancelled"))

        async with anyio.create_task_group() as group:
            group.start_soon(executor.run, work)
            while not entered.is_set():
                await anyio.sleep(0.001)
            scope = anyio.CancelScope()
            group.start_soon(waiting, scope)
            while executor.outstanding != 2:
                await anyio.sleep(0.001)
            with pytest.raises(SearchBusy):
                await executor.run(lambda: None)
            scope.cancel()
            while executor.outstanding != 1:
                await anyio.sleep(0.001)
            # A light operation can still use the ordinary thread pool.
            assert await anyio.to_thread.run_sync(lambda: 42) == 42
            release.set()
        assert observed == ["private"]
        assert executor.outstanding == 0
        assert await executor.run(lambda: 7) == 7

    anyio.run(scenario)


def test_running_cancel_keeps_actual_work_bounded():
    async def scenario():
        executor = SearchExecutor(1, 0)
        entered, release = threading.Event(), threading.Event()
        scope = anyio.CancelScope()

        def work():
            entered.set()
            assert release.wait(5)

        async def running():
            with scope:
                await executor.run(work)

        async with anyio.create_task_group() as group:
            group.start_soon(running)
            while not entered.is_set():
                await anyio.sleep(0.001)
            scope.cancel()
            await anyio.sleep(0)
            assert executor.outstanding == 1
            with pytest.raises(SearchBusy):
                await executor.run(lambda: None)
            release.set()
        assert executor.outstanding == 0

    anyio.run(scenario)


def test_http_mcp_share_budget_and_audit_rejections(owner_api, monkeypatch):
    api = owner_api
    api.app.state.search_executor.capacity = 1
    token = api.app.state.auth.issue_query_token("admission", [PUBLIC_SCOPE])["token"]
    headers = _mcp_session(api, token)
    entered, release = threading.Event(), threading.Event()
    original = catalog.search_library

    def slow(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(*args, **kwargs)

    monkeypatch.setattr(catalog, "search_library", slow)
    with ThreadPoolExecutor(max_workers=1) as pool:
        running = pool.submit(api.get, "/api/v1/search", params={"q": "公开"})
        try:
            assert entered.wait(5)
            rest = api.get("/api/v1/search", params={"q": "公开"})
            assert rest.status_code == 429
            assert rest.headers["Retry-After"] == "1"
            assert rest.json()["error"] == "search_busy"
            mcp = _call(api, headers, "chinalaw_search", {"query": "公开"}, 2)
            result = mcp.json()["result"]
            assert result["isError"]
            assert result["structuredContent"]["error"] == "search_busy"
            assert api.get("/healthz").status_code == 200
        finally:
            release.set()
        assert running.result().status_code == 200
    rows = api.app.state.query_log.export()
    assert len(rows) == 3
    assert sum(row["error"] == "search_busy" for row in rows) == 2


@pytest.mark.parametrize(("workers", "queue"), [(0, 16), (33, 16), (4, -1), (4, 129)])
def test_invalid_search_budget_rejected(tmp_path, workers, queue):
    from chinalaw.server.config import ServerConfig

    with pytest.raises(ValueError):
        ServerConfig(tmp_path / "library.db", tmp_path / "state",
                     search_concurrency=workers, search_queue=queue)


def test_search_budget_cli_environment(monkeypatch):
    from chinalaw.server.cli import build_parser

    monkeypatch.setenv("CHINALAW_SEARCH_CONCURRENCY", "2")
    monkeypatch.setenv("CHINALAW_SEARCH_QUEUE", "8")
    args = build_parser().parse_args(["serve"])
    assert (args.search_concurrency, args.search_queue) == (2, 8)
    args = build_parser().parse_args(["serve", "--search-concurrency", "4", "--search-queue", "0"])
    assert (args.search_concurrency, args.search_queue) == (4, 0)
