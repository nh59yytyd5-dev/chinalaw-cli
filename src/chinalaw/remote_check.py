"""Diagnose hosted MCP setup using the official SDK, without exposing credentials."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shlex
from pathlib import Path
from urllib.parse import urlsplit


class CheckError(Exception):
    def __init__(self, code: str, hint: str, status: int | None = None):
        self.code, self.hint, self.status = code, hint, status
        super().__init__(code)


def settings(env_file: Path, environ: dict) -> tuple[str, str]:
    values = {}
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            for word in shlex.split(line, comments=True):
                if "=" in word:
                    key, value = word.split("=", 1)
                    if key in {"CHINALAW_REMOTE_URL", "CHINALAW_QUERY_TOKEN"}:
                        values[key] = value
    values.update(
        {
            key: environ[key]
            for key in ("CHINALAW_REMOTE_URL", "CHINALAW_QUERY_TOKEN")
            if key in environ
        }
    )
    url = values.get("CHINALAW_REMOTE_URL", "").rstrip("/")
    token = values.get("CHINALAW_QUERY_TOKEN", "")
    if not url or not token:
        raise CheckError(
            "credentials_missing",
            "设置 CHINALAW_REMOTE_URL 和 CHINALAW_QUERY_TOKEN，或使用 --env-file。",
        )
    parts = urlsplit(url)
    if (
        parts.scheme not in {"http", "https"}
        or not parts.hostname
        or parts.username
        or parts.query
        or parts.fragment
    ):
        raise CheckError("invalid_url", "URL 应为服务基址或 /mcp 地址，不得在 URL 内携带凭据。")
    return url if url.endswith("/mcp") else url + "/mcp", token


async def check(url: str, token: str, query: str, law: str | None = None) -> dict:
    # Optional server extra supplies the SDK; core/local CLI remains stdlib-only.
    import httpx2

    observed = []

    async def observe(response):
        if response.status_code in {401, 403, 404, 429}:
            observed.append(response.status_code)

    async with httpx2.AsyncClient(
        headers={"Authorization": "Bearer " + token}, event_hooks={"response": [observe]}
    ) as http:
        try:
            return await _query(http, url, query, law)
        except Exception as exc:
            if observed:
                status = observed[-1]
                hints = {
                    401: "凭据缺失、过期或被撤销。",
                    403: "凭据权限不足。",
                    404: "检查完整 /mcp 地址。",
                    429: "按 Retry-After 等待后重试。",
                }
                raise CheckError("http_error", hints[status], status) from exc
            raise


async def _query(http, url: str, query: str, law: str | None) -> dict:
    from mcp import Client
    from mcp.client.streamable_http import streamable_http_client

    transport = streamable_http_client(url, http_client=http)
    async with Client(transport, mode="legacy", read_timeout_seconds=20) as client:
        listing = await client.list_tools()
        tools = {tool.name: tool for tool in listing.tools}
        if "chinalaw_search" not in tools:
            raise CheckError(
                "search_tool_missing",
                "握手成功但没有 chinalaw_search；检查端点与服务工具配置，不要视为数据为空。",
            )
        arguments = {"query": query, "limit": 1}
        if law:
            if "in_laws" not in tools["chinalaw_search"].input_schema.get("properties", {}):
                raise CheckError(
                    "scope_unsupported",
                    "该服务未提供 in_laws；升级到 0.7.2+ 后再做法规内检索。",
                )
            arguments["in_laws"] = law
        result = await client.call_tool("chinalaw_search", arguments)
        if result.is_error:
            raise CheckError(
                "tool_error",
                "MCP 工具返回错误；在客户端查看 isError 和结构化诊断，不能将其视为零命中。",
            )
        payload = result.structured_content
        if payload is None:
            text = next((item.text for item in result.content if item.type == "text"), "")
            payload = json.loads(text)
        if not isinstance(payload, dict) or not isinstance(payload.get("counts"), dict):
            raise CheckError("invalid_tool_result", "收到无法识别的搜索结果；不是成功的空查询。")
        return {
            "ok": True,
            "protocol": client.protocol_version,
            "tools": sorted(tools),
            "counts": payload["counts"],
            "law_filter": payload.get("law_filter"),
            "note": "已完成真实 MCP 握手、列工具和搜索；零命中仅表示本次查询无结果。",
        }


def failure(exc: Exception) -> dict:
    # Exception groups from async transports may contain request URLs or headers.
    # Report actionable allowlisted diagnoses, never arbitrary exception text.
    pending = [exc]
    for _ in range(20):
        if not pending:
            break
        item = pending.pop()
        if isinstance(item, CheckError):
            return {
                "ok": False,
                "error": item.code,
                "hint": item.hint,
                **({"status": item.status} if item.status else {}),
            }
        response = getattr(item, "response", None)
        status = getattr(response, "status_code", None)
        if status in {401, 403, 404, 429}:
            return {
                "ok": False,
                "error": "http_error",
                "status": status,
                "hint": {
                    401: "凭据缺失、过期或被撤销。",
                    403: "凭据权限不足。",
                    404: "检查完整 /mcp 地址。",
                    429: "按 Retry-After 等待后重试。",
                }[status],
            }
        pending.extend(getattr(item, "exceptions", []))
        if item.__cause__:
            pending.append(item.__cause__)
    return {
        "ok": False,
        "error": "connection_or_protocol_error",
        "hint": "连接、超时或协议解析失败；检查 /healthz、/mcp 地址与网络，不要判定远端库为空。",
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--env-file", type=Path, default=Path.home() / ".config/chinalaw/remote.env"
    )
    parser.add_argument("--query", default="保证期间")
    parser.add_argument("--law", help="Optional public-law scope; requires server 0.7.2+")
    args = parser.parse_args(argv)
    try:
        url, token = settings(args.env_file.expanduser(), os.environ)
        result = asyncio.run(check(url, token, args.query, args.law))
    except ImportError:
        result = {
            "ok": False,
            "error": "sdk_missing",
            "hint": "安装 chinalaw[server] 以使用官方 MCP SDK。",
        }
    except Exception as exc:
        result = failure(exc)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
