"""HTTP application factory. Library initialization is an explicit startup action."""

from __future__ import annotations

import logging
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from starlette.exceptions import HTTPException
from starlette.staticfiles import StaticFiles

from chinalaw import __version__, fetch, service
from chinalaw.admin import backups, drafts
from chinalaw.admin.errors import LibraryError
from chinalaw.admin.gate import MaintenanceGate
from chinalaw.admin.jobs import JobWorker
from chinalaw.schema import SCHEMA_VERSION
from chinalaw.server import routes_auth, routes_backup, routes_manage, routes_oauth, routes_query
from chinalaw.server.auth_store import AuthStore
from chinalaw.server.config import ServerConfig
from chinalaw.server.dependencies import owner
from chinalaw.server.guard import RequestGuard
from chinalaw.server.mcp_http import make_mcp
from chinalaw.server.oauth import OwnerOAuth

LOG = logging.getLogger(__name__)


def create_app(config: ServerConfig, *, auth_store: AuthStore | None = None) -> FastAPI:
    report = service.status(config.db_path)
    if report["schema_version"] != SCHEMA_VERSION:
        raise ValueError("Back up and explicitly initialize/upgrade the library before serving it")
    auth = auth_store or AuthStore(config.auth_path, config.resource_url)
    if not config.local_mode and not auth.has_password():
        raise ValueError("Server mode requires an owner password; initialize credentials first")
    oauth = OwnerOAuth(auth, config.origin)
    mcp, mcp_app = make_mcp(config, oauth)
    gate = MaintenanceGate()
    worker = (
        JobWorker(config.db_path, config.artifacts_dir, gate=gate) if config.start_worker else None
    )

    @asynccontextmanager
    async def lifespan(app):
        routes_backup.clear_exports(config.state_dir)
        backups.sweep_restores(config.restores_dir)
        drafts.sweep_drafts(config.db_path)
        if worker is not None:
            worker.start()
        try:
            async with mcp.session_manager.run():
                yield
        finally:
            if worker is not None:
                worker.stop()

    app = FastAPI(
        title="chinalaw 资料库",
        version=__version__,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.config, app.state.auth, app.state.oauth = config, auth, oauth
    app.state.worker = worker
    app.state.gate = gate
    app.add_middleware(RequestGuard, config=config)
    app.include_router(routes_auth.router)
    app.include_router(routes_query.router)
    app.include_router(routes_oauth.router)
    app.include_router(routes_manage.router)
    app.include_router(routes_backup.router)

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        return JSONResponse(
            {"kind": "library_error", "error": "request_rejected", "message": str(exc.detail)},
            status_code=exc.status_code,
            headers=exc.headers,
        )

    @app.exception_handler(LibraryError)
    async def library_error(request: Request, exc: LibraryError):
        headers = {"WWW-Authenticate": "Bearer"} if exc.status == 401 else None
        return JSONResponse(exc.payload(), status_code=exc.status, headers=headers)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        # Pydantic's default output contains the input (including passwords).
        issues = [{"location": error["loc"], "message": error["msg"]} for error in exc.errors()]
        return JSONResponse(
            {
                "kind": "library_error",
                "error": "invalid_request",
                "message": "请求参数不正确。",
                "issues": issues,
            },
            status_code=422,
        )

    @app.exception_handler(ValueError)
    async def invalid_input(request: Request, exc: ValueError):
        return JSONResponse(
            {"kind": "library_error", "error": "invalid_input", "message": str(exc)[:1000]},
            status_code=400,
        )

    @app.exception_handler(fetch.FetchError)
    async def source_error(request: Request, exc: fetch.FetchError):
        return JSONResponse(
            {"kind": "library_error", "error": "source_unavailable", "message": str(exc)[:1000]},
            status_code=502,
        )

    @app.exception_handler(sqlite3.IntegrityError)
    async def integrity_error(request: Request, exc: sqlite3.IntegrityError):
        # A constraint the preview checks did not anticipate: the write was
        # rolled back, the library is intact, and a fresh preview will show why.
        LOG.exception("constraint violated on %s %s", request.method, request.url.path)
        return JSONResponse(
            {
                "kind": "library_error",
                "error": "storage_conflict",
                "message": "写入与资料库现有内容冲突，本次写入已撤销，请重新生成预览后确认。",
            },
            status_code=409,
        )

    @app.exception_handler(OSError)
    @app.exception_handler(sqlite3.Error)
    async def storage_error(request: Request, exc: Exception):
        # The client gets a generic message; the operator needs the cause.
        LOG.exception("storage error on %s %s", request.method, request.url.path, exc_info=exc)
        return JSONResponse(
            {
                "kind": "library_error",
                "error": "storage_unavailable",
                "message": "存储操作未完成，请检查服务磁盘空间、权限和资料库占用情况。",
            },
            status_code=503,
        )

    @app.get("/healthz", include_in_schema=False)
    def health() -> dict:
        return {"ok": True, "version": __version__}

    @app.get("/api/v1/openapi.json", include_in_schema=False)
    def openapi(request: Request) -> dict:
        owner(request)
        return app.openapi()

    static = Path(__file__).parent / "static"

    @app.get("/", include_in_schema=False)
    def index():
        file = static / "index.html"
        if not file.is_file():
            return JSONResponse(
                {
                    "error": "ui_not_built",
                    "message": "尚未构建管理界面，请在 web/ 执行 npm ci 和 npm run build。",
                },
                status_code=503,
            )
        return FileResponse(file, headers={"Cache-Control": "no-cache"})

    app.mount("/assets", StaticFiles(directory=static / "assets", check_dir=False), name="assets")
    # The SDK also hosts authorization/discovery routes. This catch-all mount
    # must follow every application route. Its lifespan is owned above.
    app.mount("/", mcp_app)
    return app
