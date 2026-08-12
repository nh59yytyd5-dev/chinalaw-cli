"""时间效力 / 规范关系的最小数据导入层。

该模块只负责把人工审核过的关系与适用规则写入本地库。它不做法律判断，
也不从案情自动推导适用法律。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path

from chinalaw.contracts import (
    validate_iso_date_value,
    validate_iso_datetime_value,
    validate_source_url_value,
)
from chinalaw.datapaths import builtin_data_dir
from chinalaw.db import connect, migrate, set_meta

DEFAULT_APPLICABILITY_DIR = builtin_data_dir("applicability")


def load_applicability_fixtures(
    db_path: Path | str,
    directory: Path | str | None = None,
) -> dict:
    """加载内置或指定目录下的 applicability JSON。"""
    root = Path(directory) if directory is not None else DEFAULT_APPLICABILITY_DIR
    paths = sorted(root.glob("*.json")) if root.exists() else []
    return load_applicability_files(db_path, paths)


def load_applicability_files(
    db_path: Path | str,
    paths: Iterable[Path | str],
) -> dict:
    relation_count = 0
    rule_count = 0
    files: list[str] = []
    topics: set[str] = set()

    with connect(db_path) as conn:
        migrate(conn)
        for raw_path in paths:
            path = Path(raw_path)
            payload = json.loads(path.read_text(encoding="utf-8"))
            loaded = load_applicability_from_dict(conn, payload, source_path=path)
            relation_count += loaded["relations_loaded"]
            rule_count += loaded["rules_loaded"]
            files.append(str(path))
            topics.update(loaded.get("topics") or [])
        set_meta(conn, "last_applicability_sync_at", _utc_now())

    return {
        "kind": "applicability_import",
        "files_loaded": len(files),
        "relations_loaded": relation_count,
        "rules_loaded": rule_count,
        "topics": sorted(topics),
        "files": files,
    }


def load_applicability_from_dict(
    conn: sqlite3.Connection,
    payload: dict,
    *,
    source_path: Path | None = None,
) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("applicability payload must be an object")
    source_defaults = {
        "source_name": payload.get("source_name") or "manual-rule-seed",
        "source_url": payload.get("source_url") or _local_source_url(source_path),
        "source_checked_at": payload.get("source_checked_at") or _utc_now(),
    }
    relations = payload.get("relations", [])
    rules = payload.get("rules", [])
    if not isinstance(relations, list):
        raise ValueError("applicability payload relations must be an array")
    if not isinstance(rules, list):
        raise ValueError("applicability payload rules must be an array")
    normalized_relations = [
        _validate_relation({**source_defaults, **_record(item, "relation", index)})
        for index, item in enumerate(relations, start=1)
    ]
    normalized_rules = [
        _validate_rule({**source_defaults, **_record(item, "rule", index)})
        for index, item in enumerate(rules, start=1)
    ]
    relation_count = 0
    rule_count = 0
    topics: set[str] = set()

    for relation in normalized_relations:
        _upsert_relation(conn, relation)
        relation_count += 1

    for rule in normalized_rules:
        _upsert_rule(conn, rule)
        rule_count += 1
        topics.add(rule["topic"])

    return {
        "relations_loaded": relation_count,
        "rules_loaded": rule_count,
        "topics": sorted(topics),
    }


def _record(value: object, kind: str, index: int) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"applicability {kind} #{index} must be an object")
    return dict(value)


def _required_text(record: dict, field: str, context: str) -> str:
    value = record.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{context} field {field!r} must be a non-empty string")
    return value.strip()


def _optional_text(record: dict, field: str, context: str) -> None:
    value = record.get(field)
    if value is not None and not isinstance(value, str):
        raise ValueError(f"{context} field {field!r} must be a string or null")


def _validate_common_source(record: dict, context: str) -> None:
    _required_text(record, "source_name", context)
    validate_source_url_value(
        record.get("source_url"),
        f"{context} field 'source_url'",
        local_schemes={"local-file", "local-seed"},
    )
    validate_iso_datetime_value(
        record.get("source_checked_at"),
        f"{context} field 'source_checked_at'",
    )
    metadata = record.get("metadata", {})
    if metadata is not None and not isinstance(metadata, dict):
        raise ValueError(f"{context} field 'metadata' must be an object")


def _validate_relation(relation: dict) -> dict:
    context = "applicability relation"
    for field in ("relation_type", "from_law_id", "to_law_id"):
        _required_text(relation, field, context)
    for field in (
        "id",
        "from_law_title",
        "to_law_title",
        "notes",
    ):
        _optional_text(relation, field, context)
    validate_iso_date_value(
        relation.get("effective_at"),
        f"{context} field 'effective_at'",
    )
    _validate_common_source(relation, context)
    return relation


def _validate_rule(rule: dict) -> dict:
    context = "applicability rule"
    for field in ("topic", "primary_law_id", "rule_text"):
        _required_text(rule, field, context)
    for field in (
        "id",
        "domain",
        "primary_law_title",
        "fallback_law_id",
        "fallback_law_title",
        "transition_text",
        "confidence",
    ):
        _optional_text(rule, field, context)
    effective_from = rule.get("effective_from")
    effective_to = rule.get("effective_to")
    validate_iso_date_value(
        effective_from,
        f"{context} field 'effective_from'",
    )
    validate_iso_date_value(
        effective_to,
        f"{context} field 'effective_to'",
    )
    if (
        isinstance(effective_from, str)
        and isinstance(effective_to, str)
        and effective_from > effective_to
    ):
        raise ValueError(
            "applicability rule effective_from must not be later than effective_to"
        )
    _validate_common_source(rule, context)
    return rule


def _upsert_relation(conn: sqlite3.Connection, relation: dict) -> None:
    relation_id = relation.get("id") or _stable_id(
        "rel",
        [
            relation.get("relation_type"),
            relation.get("from_law_id"),
            relation.get("to_law_id"),
            relation.get("effective_at"),
        ],
    )
    conn.execute(
        """
        INSERT INTO law_relations(
            id, relation_type, from_law_id, from_law_title, to_law_id,
            to_law_title, effective_at, source_name, source_url,
            source_checked_at, notes, metadata_json, updated_at
        )
        VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(id) DO UPDATE SET
            relation_type=excluded.relation_type,
            from_law_id=excluded.from_law_id,
            from_law_title=excluded.from_law_title,
            to_law_id=excluded.to_law_id,
            to_law_title=excluded.to_law_title,
            effective_at=excluded.effective_at,
            source_name=excluded.source_name,
            source_url=excluded.source_url,
            source_checked_at=excluded.source_checked_at,
            notes=excluded.notes,
            metadata_json=excluded.metadata_json,
            updated_at=CURRENT_TIMESTAMP
        """,
        (
            relation_id,
            relation["relation_type"],
            relation["from_law_id"],
            relation.get("from_law_title"),
            relation["to_law_id"],
            relation.get("to_law_title"),
            relation.get("effective_at"),
            relation["source_name"],
            relation.get("source_url"),
            relation["source_checked_at"],
            relation.get("notes"),
            _json_dump(relation.get("metadata") or {}),
        ),
    )


def _upsert_rule(conn: sqlite3.Connection, rule: dict) -> None:
    rule_id = rule.get("id") or _stable_id(
        "app",
        [
            rule.get("topic"),
            rule.get("domain"),
            rule.get("primary_law_id"),
            rule.get("fallback_law_id"),
            rule.get("effective_from"),
            rule.get("effective_to"),
        ],
    )
    conn.execute(
        """
        INSERT INTO applicability_rules(
            id, topic, domain, primary_law_id, primary_law_title,
            fallback_law_id, fallback_law_title, effective_from, effective_to,
            rule_text, transition_text, source_name, source_url,
            source_checked_at, confidence, metadata_json, updated_at
        )
        VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(id) DO UPDATE SET
            topic=excluded.topic,
            domain=excluded.domain,
            primary_law_id=excluded.primary_law_id,
            primary_law_title=excluded.primary_law_title,
            fallback_law_id=excluded.fallback_law_id,
            fallback_law_title=excluded.fallback_law_title,
            effective_from=excluded.effective_from,
            effective_to=excluded.effective_to,
            rule_text=excluded.rule_text,
            transition_text=excluded.transition_text,
            source_name=excluded.source_name,
            source_url=excluded.source_url,
            source_checked_at=excluded.source_checked_at,
            confidence=excluded.confidence,
            metadata_json=excluded.metadata_json,
            updated_at=CURRENT_TIMESTAMP
        """,
        (
            rule_id,
            rule["topic"],
            rule.get("domain") or "all",
            rule["primary_law_id"],
            rule.get("primary_law_title"),
            rule.get("fallback_law_id"),
            rule.get("fallback_law_title"),
            rule.get("effective_from"),
            rule.get("effective_to"),
            rule["rule_text"],
            rule.get("transition_text"),
            rule["source_name"],
            rule.get("source_url"),
            rule["source_checked_at"],
            rule.get("confidence") or "seed",
            _json_dump(rule.get("metadata") or {}),
        ),
    )


def _stable_id(prefix: str, parts: list[object]) -> str:
    raw = "|".join("" if part is None else str(part) for part in parts)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}-{digest}"


def _json_dump(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _local_source_url(path: Path | None) -> str:
    return f"local-file:{path}" if path else "local-seed:applicability"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
