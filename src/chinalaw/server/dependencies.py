"""One authorization policy for UI and REST; query tokens never become owners."""

from __future__ import annotations

import hmac

from fastapi import Request

from chinalaw.admin.errors import LibraryError
from chinalaw.server.auth_store import Principal

SESSION_COOKIE = "chinalaw_session"


def same_origin(request: Request) -> None:
    if request.headers.get("origin") != request.app.state.config.origin:
        raise LibraryError("origin_denied", "请求来源不被允许。", status=403)


def principal(request: Request) -> Principal:
    authorization = request.headers.get("authorization", "")
    if authorization:
        scheme, separator, token = authorization.partition(" ")
        value = (
            request.app.state.auth.bearer(token)
            if separator and scheme.lower() == "bearer"
            else None
        )
    else:
        value = request.app.state.auth.session(request.cookies.get(SESSION_COOKIE))
    if value is None:
        raise LibraryError("authentication_required", "请登录或提供有效的只读凭据。", status=401)
    return value


def owner(request: Request) -> Principal:
    value = principal(request)
    if not value.is_owner:
        raise LibraryError("owner_required", "此操作需要所有者登录会话。", status=403)
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        same_origin(request)
        if not value.csrf or not hmac.compare_digest(
            value.csrf, request.headers.get("x-csrf-token", "")
        ):
            raise LibraryError("csrf_failed", "会话校验失败，请刷新页面后重试。", status=403)
    return value


def require_document_scope(value: Principal, kind: str) -> None:
    if kind == "norm" and not value.can_read_private:
        raise LibraryError("private_access_denied", "此凭据未获私域规范访问权限。", status=403)


def maintenance_activity(request: Request):
    owner(request)
    with request.app.state.gate.activity():
        yield
