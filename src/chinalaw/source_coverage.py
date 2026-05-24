"""Machine-readable source coverage catalog.

This module deliberately keeps source maturity data in ``data/source_coverage.json``
instead of scattering release promises across README, issues, and adapter
constants. Adapter registration remains authoritative for implementation; this
catalog is the product/release boundary that agents can inspect.
"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from chinalaw.datapaths import builtin_data_file

SOURCE_COVERAGE_FILE = "source_coverage.json"

SUPPORTED_COVERAGE_CLASSES = {
    "primary",
    "supplemental",
    "industry",
    "manual_review",
    "commercial_optional",
}
SUPPORTED_ADAPTER_STATUS = {"implemented", "planned", "future_optional", "unsupported"}
SUPPORTED_MATURITY = {
    "stable_core",
    "candidate",
    "beta",
    "planned",
    "not_baseline",
    "unsupported",
}
SUPPORTED_PUBLIC_V2 = {
    "include",
    "candidate",
    "develop_only",
    "blocked_until_investigated",
    "defer",
    "not_baseline",
}
BOOLEAN_COMMANDS = ("probe", "verify_source", "fetch", "discover", "sync")
STATUS_FILTER_COMMAND = "status_filter"
SUPPORTED_BOOLEAN_COMMAND_STATUS = {"supported", "unsupported"}
SUPPORTED_STATUS_FILTER_COMMAND_STATUS = {"full", "current_only", "unsupported"}
SUPPORTED_COMMAND_STATUS = (
    SUPPORTED_BOOLEAN_COMMAND_STATUS | SUPPORTED_STATUS_FILTER_COMMAND_STATUS
)
SUPPORTED_COMMAND_STATUS_BY_NAME = {
    **{command: SUPPORTED_BOOLEAN_COMMAND_STATUS for command in BOOLEAN_COMMANDS},
    STATUS_FILTER_COMMAND: SUPPORTED_STATUS_FILTER_COMMAND_STATUS,
}


class SourceCoverageError(ValueError):
    """Raised when the source coverage catalog is missing or malformed."""


def load_catalog(path: str | Path | None = None) -> dict[str, Any]:
    catalog_path = Path(path) if path is not None else builtin_data_file(SOURCE_COVERAGE_FILE)
    try:
        payload = json.loads(catalog_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SourceCoverageError(f"source coverage catalog not found: {catalog_path}") from exc
    except json.JSONDecodeError as exc:
        raise SourceCoverageError(
            f"source coverage catalog is invalid JSON: {catalog_path}"
        ) from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("sources"), list):
        raise SourceCoverageError("source coverage catalog must contain a sources list")
    _validate_catalog(payload)
    payload.setdefault("path", str(catalog_path))
    return payload


def list_sources(
    *,
    coverage_class: str | None = None,
    public_v2: str | None = None,
    implemented_only: bool = False,
    path: str | Path | None = None,
) -> dict[str, Any]:
    catalog = load_catalog(path)
    sources = []
    for item in catalog.get("sources") or []:
        if coverage_class and item.get("coverage_class") != coverage_class:
            continue
        if public_v2 and item.get("public_v2") != public_v2:
            continue
        if implemented_only and item.get("adapter_status") != "implemented":
            continue
        sources.append(_source_summary(item))

    return {
        "kind": "source_coverage_sources",
        "schema_version": catalog.get("schema_version"),
        "as_of": catalog.get("as_of"),
        "path": catalog.get("path"),
        "filters": {
            "coverage_class": coverage_class,
            "public_v2": public_v2,
            "implemented_only": implemented_only,
        },
        "source_count": len(sources),
        "sources": sources,
    }


def show_source(source_id: str, *, path: str | Path | None = None) -> dict[str, Any]:
    catalog = load_catalog(path)
    normalized = _normalize_source_id(source_id)
    for item in catalog.get("sources") or []:
        if _normalize_source_id(item.get("id", "")) == normalized:
            return {
                "kind": "source_coverage_source",
                "schema_version": catalog.get("schema_version"),
                "as_of": catalog.get("as_of"),
                "path": catalog.get("path"),
                "source": deepcopy(item),
            }
    known = ", ".join(sorted(str(item.get("id")) for item in catalog.get("sources") or []))
    raise SourceCoverageError(f"unknown source coverage id: {source_id}; known: {known}")


def _source_summary(item: dict[str, Any]) -> dict[str, Any]:
    commands = item.get("commands") or {}
    return {
        "id": item.get("id"),
        "name": item.get("name"),
        "coverage_class": item.get("coverage_class"),
        "authority_layer": item.get("authority_layer"),
        "adapter_status": item.get("adapter_status"),
        "maturity": item.get("maturity"),
        "public_v2": item.get("public_v2"),
        "commands": deepcopy(commands),
    }


def _validate_catalog(payload: dict[str, Any]) -> None:
    seen: set[str] = set()
    for raw in payload.get("sources") or []:
        if not isinstance(raw, dict):
            raise SourceCoverageError("source coverage item must be an object")
        source_id = raw.get("id")
        if not isinstance(source_id, str) or not source_id.strip():
            raise SourceCoverageError("source coverage item is missing id")
        normalized = _normalize_source_id(source_id)
        if normalized in seen:
            raise SourceCoverageError(f"duplicate source coverage id: {source_id}")
        seen.add(normalized)
        _validate_enum(raw, "coverage_class", SUPPORTED_COVERAGE_CLASSES)
        _validate_enum(raw, "adapter_status", SUPPORTED_ADAPTER_STATUS)
        _validate_enum(raw, "maturity", SUPPORTED_MATURITY)
        _validate_enum(raw, "public_v2", SUPPORTED_PUBLIC_V2)
        commands = raw.get("commands")
        if not isinstance(commands, dict):
            raise SourceCoverageError(f"source {source_id} is missing commands object")
        for command, supported_statuses in SUPPORTED_COMMAND_STATUS_BY_NAME.items():
            if command not in commands:
                raise SourceCoverageError(f"source {source_id} commands missing {command}")
            status = commands[command]
            if status not in supported_statuses:
                known = ", ".join(sorted(supported_statuses))
                raise SourceCoverageError(
                    f"source {source_id} command {command} has invalid status {status!r}; "
                    f"expected one of: {known}"
                )


def _validate_enum(raw: dict[str, Any], key: str, supported: set[str]) -> None:
    value = raw.get(key)
    if value not in supported:
        source_id = raw.get("id") or "<unknown>"
        known = ", ".join(sorted(supported))
        raise SourceCoverageError(
            f"source {source_id} has invalid {key}: {value!r}; expected one of: {known}"
        )


def _normalize_source_id(value: str) -> str:
    return value.strip().lower().replace("-", "_")
