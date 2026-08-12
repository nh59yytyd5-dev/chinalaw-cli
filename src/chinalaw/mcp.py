"""Lightweight MCP stdio server for chinalaw.

The server is deliberately a thin adapter over public service functions. It does
not keep hidden legal state between calls; callers should pass every argument
needed for one lookup.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import traceback
from pathlib import Path
from typing import Any, BinaryIO, TextIO

from chinalaw import __version__, ensure, metadata, service
from chinalaw.db import DEFAULT_DB_PATH

PROTOCOL_VERSION = "2025-06-18"
SERVER_INFO = {"name": "chinalaw-mcp", "version": __version__}

# Stdio is a trust boundary. These limits are deliberately much larger than the
# current tool payloads while still preventing an unbounded readline/read call.
MAX_HEADER_LINE_BYTES = 8 * 1024
MAX_HEADER_BYTES = 32 * 1024
MAX_BODY_BYTES = 1024 * 1024
MAX_JSON_LINE_BYTES = 1024 * 1024
MAX_RECOVERY_DRAIN_BYTES = 4 * 1024 * 1024


TOOLS: list[dict[str, Any]] = metadata.mcp_tools(protocol=True)
_TOOLS_BY_NAME = {tool["name"]: tool for tool in TOOLS}


class FramingError(ValueError):
    """A bounded stdio frame could not be decoded safely."""

    def __init__(self, message: str, *, framing: str, recoverable: bool) -> None:
        super().__init__(message)
        self.framing = framing
        self.recoverable = recoverable


def handle_request(
    request: object,
    *,
    db_path: Path | str = DEFAULT_DB_PATH,
    error_stream: TextIO | None = None,
) -> dict[str, Any] | None:
    """Validate and dispatch one JSON-RPC request.

    A missing ``id`` key denotes a notification. Notifications are dispatched,
    but never produce a response, including when validation or tool execution
    fails. An explicit ``"id": null`` remains a request and receives a response.
    """

    notification = isinstance(request, dict) and "id" not in request
    validation_error = _validate_request(request)
    if validation_error is not None:
        return None if notification else validation_error

    assert isinstance(request, dict)
    method = request["method"]
    request_id = request.get("id")

    if method == "initialize":
        params_error = _validate_object_params(request)
        if params_error is not None:
            return None if notification else _error(request_id, -32602, params_error)
        result = {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": SERVER_INFO,
        }
    elif method == "tools/list":
        params_error = _validate_object_params(request)
        if params_error is not None:
            return None if notification else _error(request_id, -32602, params_error)
        result = {"tools": TOOLS}
    elif method == "tools/call":
        params = request.get("params")
        if not isinstance(params, dict):
            return None if notification else _error(
                request_id,
                -32602,
                "Invalid params: params must be an object",
            )
        name = params.get("name")
        if not isinstance(name, str) or not name.strip():
            return None if notification else _error(
                request_id,
                -32602,
                "Invalid params: name must be a non-empty string",
            )
        arguments = params.get("arguments", {})
        if not isinstance(arguments, dict):
            return None if notification else _error(
                request_id,
                -32602,
                "Invalid params: arguments must be an object",
            )
        schema_error = _validate_tool_arguments(name, arguments)
        if schema_error is not None:
            return None if notification else _error(
                request_id,
                -32602,
                f"Invalid params: {schema_error}",
            )
        result = _call_tool(
            name,
            arguments,
            db_path=db_path,
            error_stream=error_stream,
        )
    elif method == "ping":
        params_error = _validate_object_params(request)
        if params_error is not None:
            return None if notification else _error(request_id, -32602, params_error)
        result = {}
    elif method == "resources/list":
        params_error = _validate_object_params(request)
        if params_error is not None:
            return None if notification else _error(request_id, -32602, params_error)
        result = {"resources": []}
    elif method == "prompts/list":
        params_error = _validate_object_params(request)
        if params_error is not None:
            return None if notification else _error(request_id, -32602, params_error)
        result = {"prompts": []}
    elif method == "notifications/initialized":
        params_error = _validate_object_params(request)
        if params_error is not None:
            return None if notification else _error(request_id, -32602, params_error)
        result = {}
    else:
        return None if notification else _error(
            request_id,
            -32601,
            f"Method not found: {method}",
        )

    if notification:
        return None
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _validate_request(request: object) -> dict[str, Any] | None:
    if not isinstance(request, dict):
        return _error(None, -32600, "Invalid Request: request must be an object")

    request_id = request.get("id") if _valid_request_id(request.get("id")) else None
    if request.get("jsonrpc") != "2.0":
        return _error(request_id, -32600, "Invalid Request: jsonrpc must be '2.0'")
    if "id" in request and not _valid_request_id(request["id"]):
        return _error(None, -32600, "Invalid Request: id must be a string, number, or null")
    if not isinstance(request.get("method"), str):
        return _error(request_id, -32600, "Invalid Request: method must be a string")
    return None


def _valid_request_id(value: object) -> bool:
    if value is None or isinstance(value, str):
        return True
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return not isinstance(value, float) or math.isfinite(value)


def _validate_object_params(request: dict[str, Any]) -> str | None:
    if "params" in request and not isinstance(request["params"], dict):
        return "Invalid params: params must be an object"
    return None


def _validate_tool_arguments(name: str, arguments: dict[str, Any]) -> str | None:
    tool = _TOOLS_BY_NAME.get(name)
    if tool is None:
        # An unknown tool is a tool/domain failure, not malformed JSON-RPC.
        return None
    schema = tool.get("inputSchema", {})
    properties = schema.get("properties", {})
    required = schema.get("required", [])

    for field in required:
        if field not in arguments:
            return f"missing required argument: {field}"
    if schema.get("additionalProperties") is False:
        unexpected = next((key for key in arguments if key not in properties), None)
        if unexpected is not None:
            return f"unexpected argument: {unexpected}"

    for field, value in arguments.items():
        field_schema = properties.get(field)
        if field_schema is None:
            continue
        error = _validate_schema_value(f"arguments.{field}", value, field_schema)
        if error is not None:
            return error
    return None


def _validate_schema_value(path: str, value: object, schema: dict[str, Any]) -> str | None:
    expected = schema.get("type")
    if expected == "string":
        if not isinstance(value, str):
            return f"{path} must be a string"
        minimum_length = schema.get("minLength")
        if isinstance(minimum_length, int) and len(value) < minimum_length:
            return f"{path} must not be empty"
    elif expected == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            return f"{path} must be an integer"
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        if isinstance(minimum, int) and value < minimum:
            return f"{path} must be at least {minimum}"
        if isinstance(maximum, int) and value > maximum:
            return f"{path} must be at most {maximum}"
    elif expected == "object" and not isinstance(value, dict):
        return f"{path} must be an object"

    choices = schema.get("enum")
    if isinstance(choices, list) and value not in choices:
        return f"{path} must be one of: {', '.join(map(str, choices))}"
    return None


def _call_tool(
    name: str,
    arguments: dict[str, Any],
    *,
    db_path: Path | str,
    error_stream: TextIO | None = None,
) -> dict[str, Any]:
    try:
        payload = _tool_payload(name, arguments, db_path=db_path)
        return _tool_result(payload, is_error=False)
    except ValueError as exc:
        payload = {
            "kind": "chinalaw_mcp_error",
            "error": exc.__class__.__name__,
            "message": str(exc),
        }
        return _tool_result(payload, is_error=True)
    except Exception as exc:
        _log_exception(error_stream, f"tool {name!r} failed", exc)
        payload = {
            "kind": "chinalaw_mcp_internal_error",
            "error": exc.__class__.__name__,
            "message": "Internal tool error",
        }
        return _tool_result(payload, is_error=True)


def _tool_result(payload: dict[str, Any], *, is_error: bool) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}],
        "structuredContent": payload,
        "isError": is_error,
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
            diagnosis = (
                {
                    key: payload.get(key)
                    for key in ("reason", "error", "message", "diagnostic")
                    if payload and payload.get(key) is not None
                }
                if payload and payload.get("reason")
                else service.diagnose_article_miss(db_path, law, number, as_of=as_of)
            )
            return {
                "kind": "article_result",
                "found": False,
                "diagnosis": diagnosis,
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
            limit=_integer(arguments, "limit", default=10),
            kind=_optional(arguments, "kind") or "all",
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
            source=_optional(arguments, "source") or "flk_npc",
            limit=_integer(arguments, "limit", default=5),
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
    if not isinstance(value, str):
        raise ValueError(f"argument {name} must be a string")
    text = value.strip()
    return text or None


def _integer(arguments: dict[str, Any], name: str, *, default: int) -> int:
    if name not in arguments:
        return default
    value = arguments[name]
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"argument {name} must be an integer")
    return value


def _error(
    request_id: object,
    code: int,
    message: str,
    *,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": error}


def _read_message_with_framing(stream: BinaryIO) -> tuple[object, str] | None:
    first = stream.readline(MAX_JSON_LINE_BYTES + 3)
    if not first:
        return None

    if _looks_like_header(first):
        return _read_header_message(stream, first)
    return _decode_line_message(stream, first), "line"


def _looks_like_header(line: bytes) -> bool:
    stripped = line.lstrip().lower()
    return stripped.startswith(b"content-length") or stripped.startswith(b"content-type:")


def _read_header_message(stream: BinaryIO, first: bytes) -> tuple[object, str]:
    if len(first) > MAX_HEADER_LINE_BYTES:
        recovered = _finish_line_and_drain_headers(stream, first)
        raise FramingError(
            "header line exceeds limit",
            framing="header",
            recoverable=recovered,
        )

    headers = [first]
    total = len(first)
    while first not in {b"\r\n", b"\n"}:
        line = stream.readline(MAX_HEADER_LINE_BYTES + 1)
        if not line:
            raise FramingError(
                "unexpected EOF while reading headers",
                framing="header",
                recoverable=False,
            )
        if len(line) > MAX_HEADER_LINE_BYTES:
            recovered = _finish_line_and_drain_headers(stream, line)
            raise FramingError(
                "header line exceeds limit",
                framing="header",
                recoverable=recovered,
            )
        total += len(line)
        if total > MAX_HEADER_BYTES:
            recovered = line in {b"\r\n", b"\n"} or _drain_headers(
                stream,
                limit=MAX_RECOVERY_DRAIN_BYTES,
            )
            raise FramingError(
                "aggregate headers exceed limit",
                framing="header",
                recoverable=recovered,
            )
        if line in {b"\r\n", b"\n"}:
            break
        headers.append(line)

    content_lengths: list[bytes] = []
    for raw_header in headers:
        header = raw_header.rstrip(b"\r\n")
        name, separator, value = header.partition(b":")
        if not separator or not name.strip():
            raise FramingError(
                "malformed header",
                framing="header",
                recoverable=True,
            )
        try:
            normalized_name = name.strip().decode("ascii").lower()
        except UnicodeDecodeError as exc:
            raise FramingError(
                "header names must be ASCII",
                framing="header",
                recoverable=True,
            ) from exc
        if normalized_name == "content-length":
            content_lengths.append(value.strip())

    if len(content_lengths) != 1:
        detail = "missing" if not content_lengths else "duplicate"
        raise FramingError(
            f"{detail} Content-Length header",
            framing="header",
            recoverable=True,
        )
    raw_length = content_lengths[0]
    if not raw_length or not raw_length.isdigit():
        raise FramingError(
            "Content-Length must be a non-negative decimal integer",
            framing="header",
            recoverable=True,
        )
    normalized_length = raw_length.lstrip(b"0") or b"0"
    recovery_limit = str(MAX_RECOVERY_DRAIN_BYTES).encode("ascii")
    if len(normalized_length) > len(recovery_limit) or (
        len(normalized_length) == len(recovery_limit)
        and normalized_length > recovery_limit
    ):
        raise FramingError(
            "Content-Length exceeds body limit",
            framing="header",
            recoverable=False,
        )
    length = int(raw_length)
    if length > MAX_BODY_BYTES:
        recovered = _drain_exact(stream, length, limit=MAX_RECOVERY_DRAIN_BYTES)
        raise FramingError(
            "Content-Length exceeds body limit",
            framing="header",
            recoverable=recovered,
        )

    body = _read_exact(stream, length)
    if body is None:
        raise FramingError(
            "unexpected EOF while reading message body",
            framing="header",
            recoverable=False,
        )
    return _decode_json(body, framing="header"), "header"


def _decode_line_message(stream: BinaryIO, first: bytes) -> object:
    complete = first.endswith(b"\n")
    body = first.rstrip(b"\r\n") if complete else first
    if len(body) > MAX_JSON_LINE_BYTES:
        recovered = complete or _drain_to_newline(stream, MAX_RECOVERY_DRAIN_BYTES) is not None
        raise FramingError(
            "JSON line exceeds limit",
            framing="line",
            recoverable=recovered,
        )
    return _decode_json(body, framing="line")


def _decode_json(body: bytes, *, framing: str) -> object:
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise FramingError(
            "message body is not valid UTF-8",
            framing=framing,
            recoverable=True,
        ) from exc

    def reject_non_json_number(value: str) -> None:
        raise ValueError(f"invalid JSON number: {value}")

    try:
        return json.loads(text, parse_constant=reject_non_json_number)
    except (json.JSONDecodeError, ValueError) as exc:
        raise FramingError(
            "message body is not valid JSON",
            framing=framing,
            recoverable=True,
        ) from exc


def _read_exact(stream: BinaryIO, length: int) -> bytes | None:
    chunks: list[bytes] = []
    remaining = length
    while remaining:
        chunk = stream.read(remaining)
        if not chunk:
            return None
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _drain_exact(stream: BinaryIO, length: int, *, limit: int) -> bool:
    if length > limit:
        return False
    remaining = length
    while remaining:
        chunk = stream.read(min(remaining, 64 * 1024))
        if not chunk:
            return False
        remaining -= len(chunk)
    return True


def _finish_line_and_drain_headers(stream: BinaryIO, line: bytes) -> bool:
    remaining = MAX_RECOVERY_DRAIN_BYTES
    if not line.endswith(b"\n"):
        consumed = _drain_to_newline(stream, remaining)
        if consumed is None:
            return False
        remaining -= consumed
    return _drain_headers(stream, limit=remaining)


def _drain_headers(stream: BinaryIO, *, limit: int) -> bool:
    remaining = limit
    while remaining > 0:
        line = stream.readline(min(MAX_HEADER_LINE_BYTES + 1, remaining))
        if not line:
            return False
        remaining -= len(line)
        if line in {b"\r\n", b"\n"}:
            return True
        if not line.endswith(b"\n"):
            consumed = _drain_to_newline(stream, remaining)
            if consumed is None:
                return False
            remaining -= consumed
    return False


def _drain_to_newline(stream: BinaryIO, limit: int) -> int | None:
    if limit <= 0:
        return None
    tail = stream.readline(limit)
    if not tail or not tail.endswith(b"\n"):
        return None
    return len(tail)


def _read_message(stream: BinaryIO) -> object | None:
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


def serve_streams(
    input_stream: BinaryIO,
    output_stream: BinaryIO,
    error_stream: TextIO,
    *,
    db_path: Path | str = DEFAULT_DB_PATH,
) -> None:
    """Serve a continuous MCP session over injectable binary streams."""

    while True:
        try:
            message = _read_message_with_framing(input_stream)
        except FramingError as exc:
            _write_message(
                output_stream,
                _error(None, -32700, "Parse error", data={"detail": str(exc)}),
                framing=exc.framing,
            )
            if not exc.recoverable:
                break
            continue
        if message is None:
            break

        request, framing = message
        try:
            response = handle_request(
                request,
                db_path=db_path,
                error_stream=error_stream,
            )
        except Exception as exc:
            _log_exception(error_stream, "request dispatch failed", exc)
            if isinstance(request, dict) and "id" not in request:
                continue
            request_id = (
                request.get("id")
                if isinstance(request, dict) and _valid_request_id(request.get("id"))
                else None
            )
            response = _error(request_id, -32603, "Internal error")
        if response is not None:
            _write_message(output_stream, response, framing=framing)


def _log_exception(error_stream: TextIO | None, context: str, exc: BaseException) -> None:
    stream = error_stream or sys.stderr
    print(f"chinalaw-mcp: {context}: {exc.__class__.__name__}: {exc}", file=stream)
    traceback.print_exception(type(exc), exc, exc.__traceback__, file=stream)


def serve_stdio(*, db_path: Path | str = DEFAULT_DB_PATH) -> None:
    serve_streams(sys.stdin.buffer, sys.stdout.buffer, sys.stderr, db_path=db_path)


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
