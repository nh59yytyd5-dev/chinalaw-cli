"""Host/origin checks and bounded streaming requests for local and remote servers."""

from __future__ import annotations

from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse

from chinalaw.server.config import ServerConfig

MAX_REQUEST_BYTES = 34 * 1024 * 1024
# The official SDK serves these endpoints with permissive CORS on purpose:
# browser-based MCP clients (for example MCP Inspector) discover, register,
# exchange and revoke tokens from another origin. None of them accept the
# owner's cookie session, so the panel's same-origin rule must not reject their
# preflight or the request itself. ``/authorize`` stays strict: it is a redirect
# target rather than an XHR endpoint, and its consent page is the owner's UI.
CROSS_ORIGIN_OAUTH_PATHS = frozenset({"/register", "/token", "/revoke"})
SECURITY_HEADERS = {
    b"x-content-type-options": b"nosniff",
    b"x-frame-options": b"DENY",
    b"referrer-policy": b"no-referrer",
    b"content-security-policy": (
        b"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        b"img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; "
        b"base-uri 'none'; form-action 'self'"
    ),
}


class RequestGuard:
    def __init__(self, app, config: ServerConfig):
        self.app = app
        self.config = config

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = dict(scope["headers"])
        host = headers.get(b"host", b"").decode("latin-1").lower()
        origin = headers.get(b"origin")
        if host not in {value.lower() for value in self.config.allowed_hosts}:
            return await _reject(
                scope,
                receive,
                send,
                421,
                "host_denied",
                f"访问地址不匹配，请使用 {self.config.origin} 打开。",
            )
        if (
            origin is not None
            and origin.decode("latin-1") != self.config.origin
            and not allows_cross_origin(scope["path"])
        ):
            return await _reject(scope, receive, send, 403, "origin_denied", "请求来源不被允许。")
        limit = MAX_REQUEST_BYTES
        if scope["path"] == "/api/v1/backups/restore/preview":
            limit = 513 * 1024 * 1024
        if scope["path"].startswith("/api/v1/auth/") or scope["path"] in {
            "/register",
            "/authorize",
            "/token",
            "/revoke",
        }:
            limit = 64 * 1024
        try:
            length = int(headers.get(b"content-length", b"0"))
        except ValueError:
            return await _reject(scope, receive, send, 400, "invalid_length", "请求长度不正确。")
        if length < 0 or length > limit:
            return await _reject(
                scope, receive, send, 413, "request_too_large", "请求超过大小限制。"
            )
        size, response_started = 0, False

        async def bounded_receive():
            nonlocal size
            message = await receive()
            if message["type"] == "http.request":
                size += len(message.get("body", b""))
                if size > limit:
                    raise HTTPException(413, "请求超过大小限制。")
            return message

        async def secured_send(message):
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
                existing = {name.lower() for name, _ in message.get("headers", [])}
                extra = [
                    (key, value) for key, value in SECURITY_HEADERS.items() if key not in existing
                ]
                if scope["path"].startswith("/api/") or scope["path"] in {"/token", "/register"}:
                    extra.append((b"cache-control", b"no-store"))
                message = {**message, "headers": [*message.get("headers", []), *extra]}
            await send(message)

        try:
            await self.app(scope, bounded_receive, secured_send)
        except HTTPException as exc:
            if response_started:
                raise
            await _reject(
                scope, receive, secured_send, exc.status_code, "request_rejected", str(exc.detail)
            )


def allows_cross_origin(path: str) -> bool:
    """OAuth discovery and token endpoints are meant to be called cross-origin."""
    return path in CROSS_ORIGIN_OAUTH_PATHS or path.startswith("/.well-known/")


async def _reject(scope, receive, send, status: int, code: str, message: str):
    response = JSONResponse(
        {"kind": "library_error", "error": code, "message": message}, status_code=status
    )
    await response(scope, receive, send)
