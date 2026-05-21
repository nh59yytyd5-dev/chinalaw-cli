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
    source_defaults = {
        "source_name": payload.get("source_name") or "manual-rule-seed",
        "source_url": payload.get("source_url") or _local_source_url(source_path),
        "source_checked_at": payload.get("source_checked_at") or _utc_now(),
    }
    relation_count = 0
    rule_count = 0
    topics: set[str] = set()

    for relation in payload.get("relations") or []:
        _upsert_relation(conn, {**source_defaults, **relation})
        relation_count += 1

    for rule in payload.get("rules") or []:
        _upsert_rule(conn, {**source_defaults, **rule})
        rule_count += 1
        if rule.get("topic"):
            topics.add(str(rule["topic"]))

    return {
        "relations_loaded": relation_count,
        "rules_loaded": rule_count,
        "topics": sorted(topics),
    }


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
