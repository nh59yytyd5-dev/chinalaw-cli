"""Lightweight MCP stdio server for chinalaw.

The server is deliberately a thin adapter over public service functions. It does
not keep hidden legal state between calls; callers should pass every argument
needed for one lookup.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, BinaryIO

from chinalaw import ensure, metadata, service
from chinalaw.db import DEFAULT_DB_PATH

PROTOCOL_VERSION = "2025-06-18"
SERVER_INFO = {"name": "chinalaw-mcp", "version": "0.1.1"}


TOOLS: list[dict[str, Any]] = metadata.mcp_tools(protocol=True)


def handle_request(
    request: dict[str, Any],
    *,
    db_path: Path | str = DEFAULT_DB_PATH,
) -> dict[str, Any] | None:
    method = request.get("method")
    request_id = request.get("id")
    if request_id is None and method == "notifications/initialized":
        return None

    if method == "initialize":
        result = {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": SERVER_INFO,
        }
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        params = request.get("params")
        if params is None:
            params = {}
        if not isinstance(params, dict):
            return _error(request_id, -32602, "Invalid params: params must be an object")
        arguments = params.get("arguments")
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            return _error(request_id, -32602, "Invalid params: arguments must be an object")
        result = _call_tool(
            str(params.get("name") or ""),
            arguments,
            db_path=db_path,
        )
    elif method == "ping":
        result = {}
    elif method == "resources/list":
        result = {"resources": []}
    elif method == "prompts/list":
        result = {"prompts": []}
    else:
        return _error(request_id, -32601, f"Method not found: {method}")
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _call_tool(name: str, arguments: dict[str, Any], *, db_path: Path | str) -> dict[str, Any]:
    try:
        payload = _tool_payload(name, arguments, db_path=db_path)
        return {
            "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}],
            "structuredContent": payload,
            "isError": False,
        }
    except ValueError as exc:
        payload = {
            "kind": "chinalaw_mcp_error",
            "error": exc.__class__.__name__,
            "message": str(exc),
        }
        return {
            "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}],
            "structuredContent": payload,
            "isError": True,
        }


def _tool_payload(name: str, arguments: dict[str, Any], *, db_path: Path | str) -> dict:
    if name == "chinalaw_resolve":
        return service.resolve(db_path, _required(arguments, "name"))
    if name == "chinalaw_article":
        law = _required(arguments, "law")
        number = _required(arguments, "number")
        as_of = _optional(arguments, "as_of")
        payload = (
            service.get_article_as_of(db_path, law, number, as_of)
            if as_of
            else service.get_article(db_path, law, number)
        )
        if payload is None or payload.get("article") is None:
            return {
                "kind": "article_result",
                "found": False,
                "diagnosis": service.diagnose_article_miss(db_path, law, number, as_of=as_of),
            }
        return {"kind": "article_result", "found": True, **payload}
    if name == "chinalaw_articles":
        law = _required(arguments, "law")
        numbers = _required(arguments, "numbers")
        as_of = _optional(arguments, "as_of")
        return service.get_articles(db_path, law, numbers, as_of=as_of)
    if name == "chinalaw_search":
        return service.search(
            db_path,
            _required(arguments, "query"),
            limit=int(arguments.get("limit") or 10),
            kind=str(arguments.get("kind") or "all"),
            in_laws=_optional(arguments, "in_laws"),
        )
    if name == "chinalaw_applicable":
        return service.applicable(
            db_path,
            as_of=_required(arguments, "date"),
            topic=_optional(arguments, "topic"),
            law=_optional(arguments, "law"),
            domain=_optional(arguments, "domain"),
        )
    if name == "chinalaw_ensure":
        return ensure.ensure_laws(
            db_path,
            [_required(arguments, "name")],
            source=str(arguments.get("source") or "flk_npc"),
            limit=int(arguments.get("limit") or 5),
        )
    raise ValueError(f"unknown tool: {name}")


def _required(arguments: dict[str, Any], name: str) -> str:
    value = _optional(arguments, name)
    if not value:
        raise ValueError(f"missing required argument: {name}")
    return value


def _optional(arguments: dict[str, Any], name: str) -> str | None:
    value = arguments.get(name)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _error(
    request_id: object,
    code: int,
    message: str,
    *,
    data: dict | None = None,
) -> dict[str, Any]:
    error = {"code": code, "message": message}
    if data:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": error}


def _read_message_with_framing(stream: BinaryIO) -> tuple[dict[str, Any], str] | None:
    first = stream.readline()
    if not first:
        return None
    if first.lower().startswith(b"content-length:"):
        headers = [first]
        while True:
            line = stream.readline()
            if not line:
                return None
            if line in {b"\r\n", b"\n"}:
                break
            headers.append(line)
        length = None
        for header in headers:
            key, _, value = header.decode("ascii", errors="replace").partition(":")
            if key.lower() == "content-length":
                length = int(value.strip())
                break
        if length is None:
            raise ValueError("missing Content-Length")
        body = stream.read(length)
        return json.loads(body.decode("utf-8")), "header"
    return json.loads(first.decode("utf-8")), "line"


def _read_message(stream: BinaryIO) -> dict[str, Any] | None:
    message = _read_message_with_framing(stream)
    if message is None:
        return None
    request, _framing = message
    return request


def _write_message(stream: BinaryIO, payload: dict[str, Any], *, framing: str = "header") -> None:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if framing == "line":
        stream.write(body + b"\n")
    else:
        stream.write(f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body)
    stream.flush()


def serve_stdio(*, db_path: Path | str = DEFAULT_DB_PATH) -> None:
    while True:
        message = _read_message_with_framing(sys.stdin.buffer)
        if message is None:
            break
        request, framing = message
        response = handle_request(request, db_path=db_path)
        if response is not None:
            _write_message(sys.stdout.buffer, response, framing=framing)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="chinalaw-mcp",
        description="chinalaw MCP stdio server",
    )
    parser.add_argument(
        "--db",
        default=str(DEFAULT_DB_PATH),
        help=f"SQLite DB path (default {DEFAULT_DB_PATH})",
    )
    args = parser.parse_args(argv)
    serve_stdio(db_path=Path(args.db))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
