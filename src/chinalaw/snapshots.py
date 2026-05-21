"""Project-level retrieval snapshots for grounding audits.

Snapshots are append-only JSONL ledgers. They are intentionally separate from
the SQLite database: a project can prove which legal materials an agent looked
up during a concrete workflow without mutating the authoritative law store.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SNAPSHOT_SCHEMA_VERSION = "chinalaw.snapshot.v1"
PROJECT_ENV = "CHINALAW_PROJECT"
SNAPSHOT_ENV = "CHINALAW_SNAPSHOT_OUT"
DEFAULT_SNAPSHOT_RELATIVE = Path(".chinalaw") / "snapshots" / "latest.jsonl"


def default_project_snapshot(project_path: str | Path) -> Path:
    """Return the default snapshot path under a project directory."""

    return Path(project_path).expanduser() / DEFAULT_SNAPSHOT_RELATIVE


def resolve_snapshot_out(
    explicit: str | None = None,
    *,
    anchor: str | Path | None = None,
) -> Path | None:
    """Return the JSONL path where command evidence should be appended.

    Resolution order:
    1. explicit ``--snapshot-out`` path;
    2. ``CHINALAW_SNAPSHOT_OUT``;
    3. ``CHINALAW_PROJECT/.chinalaw/snapshots/latest.jsonl``;
    4. nearest existing project snapshot found from ``anchor`` / cwd upward.
    """

    raw = explicit or os.environ.get(SNAPSHOT_ENV)
    if raw:
        return Path(raw).expanduser()
    project = os.environ.get(PROJECT_ENV)
    if project:
        return default_project_snapshot(project)
    return find_project_snapshot(anchor or Path.cwd())


def resolve_snapshot_in(
    explicit: str | Path | None = None,
    *,
    anchor: str | Path | None = None,
) -> Path | None:
    """Return the snapshot path to read for an audit."""

    if explicit:
        return Path(explicit).expanduser()
    return resolve_snapshot_out(None, anchor=anchor)


def find_project_snapshot(anchor: str | Path) -> Path | None:
    """Find the nearest initialized project snapshot from ``anchor`` upward."""

    path = Path(anchor).expanduser()
    if path.is_file():
        path = path.parent
    try:
        current = path.resolve()
    except OSError:
        current = path.absolute()
    for parent in (current, *current.parents):
        candidate = default_project_snapshot(parent)
        if candidate.exists():
            return candidate
    return None


def init_project_snapshot(project_path: str | Path, *, reset: bool = False) -> dict:
    """Create or reuse a project's default snapshot file."""

    project = Path(project_path).expanduser().resolve()
    snapshot_path = default_project_snapshot(project)
    existed = snapshot_path.exists()
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    if reset or not existed:
        snapshot_path.write_text("", encoding="utf-8")
    return snapshot_status(snapshot_path, project_path=project, kind="snapshot_init")


def snapshot_status(
    snapshot_path: str | Path,
    *,
    project_path: str | Path | None = None,
    kind: str = "snapshot_status",
) -> dict:
    """Summarize a JSONL snapshot without exposing full payload text."""

    path = Path(snapshot_path).expanduser()
    records = load_records(path)
    commands: dict[str, int] = {}
    evidence_levels: dict[str, int] = {}
    first_timestamp: str | None = None
    last_timestamp: str | None = None
    for record in records:
        command = record.get("command") or "unknown"
        commands[command] = commands.get(command, 0) + 1
        timestamp = record.get("timestamp")
        if isinstance(timestamp, str):
            first_timestamp = first_timestamp or timestamp
            last_timestamp = timestamp
        for article in record.get("articles") or []:
            level = article.get("evidence_level") or "unknown"
            evidence_levels[level] = evidence_levels.get(level, 0) + 1
        for law in record.get("laws") or []:
            level = law.get("evidence_level") or "unknown"
            evidence_levels[level] = evidence_levels.get(level, 0) + 1
        for clause in record.get("norm_clauses") or []:
            level = clause.get("evidence_level") or "unknown"
            evidence_levels[level] = evidence_levels.get(level, 0) + 1
    project = (
        Path(project_path).expanduser().resolve()
        if project_path
        else path.parent.parent.parent
    )
    return {
        "kind": kind,
        "project_path": str(project),
        "snapshot_path": str(path),
        "exists": path.exists(),
        "record_count": len(records),
        "commands": commands,
        "evidence_levels": evidence_levels,
        "first_timestamp": first_timestamp,
        "last_timestamp": last_timestamp,
        "write_mode": (
            "auto_when_run_under_project"
            if path.exists()
            else "disabled_until_snapshot_init_or_env"
        ),
    }


def append_command_record(
    snapshot_path: str | Path,
    *,
    command: str,
    payload: Any,
    db_path: str | Path,
    argv: list[str] | None = None,
) -> dict:
    """Append one compact evidence record and return it."""

    path = Path(snapshot_path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    record = build_command_record(
        command=command,
        payload=payload,
        db_path=db_path,
        argv=argv or [],
        evidence_id=_next_evidence_id(path),
    )
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False, sort_keys=True))
        fh.write("\n")
    return record


def build_command_record(
    *,
    command: str,
    payload: Any,
    db_path: str | Path,
    argv: list[str],
    evidence_id: str,
) -> dict:
    payload_dict = payload if isinstance(payload, dict) else {}
    record = {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "evidence_id": evidence_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "command": command,
        "argv": list(argv),
        "db_path": str(db_path),
        "ok": _payload_ok(command, payload_dict),
        "query": _query_summary(command, payload_dict),
        "laws": [],
        "articles": [],
        "norm_clauses": [],
        "time_effect": [],
    }

    laws: list[dict] = []
    articles: list[dict] = []
    norm_clauses: list[dict] = []
    time_effect: list[dict] = []

    if command == "search":
        for hit in payload_dict.get("article_hits") or []:
            articles.append(_compact_search_hit(hit))
        for law in payload_dict.get("law_hits") or []:
            laws.append(_compact_law(law, evidence_level="search_law"))
        for hit in payload_dict.get("norm_clause_hits") or []:
            norm_clauses.append(_compact_norm_clause(hit, evidence_level="search_hit"))
    elif command in {"article", "fetch"}:
        law = payload_dict.get("law") or {}
        article = payload_dict.get("article")
        if law:
            laws.append(_compact_law(law, evidence_level="law"))
        if isinstance(article, dict):
            articles.append(
                _compact_article(article, law=law, evidence_level="article")
            )
    elif command == "articles":
        law = payload_dict.get("law") or {}
        if law:
            laws.append(_compact_law(law, evidence_level="law"))
        for item in payload_dict.get("items") or []:
            article = item.get("article") if isinstance(item, dict) else None
            if isinstance(article, dict):
                articles.append(
                    _compact_article(article, law=law, evidence_level="article")
                )
        for section in payload_dict.get("sections") or []:
            section_law = section.get("law") or {}
            if section_law:
                laws.append(_compact_law(section_law, evidence_level="law"))
            for item in section.get("items") or []:
                article = item.get("article") if isinstance(item, dict) else None
                if isinstance(article, dict):
                    articles.append(
                        _compact_article(
                            article,
                            law=section_law,
                            evidence_level="article",
                        )
                    )
    elif command in {"get", "outline"}:
        law = payload_dict.get("law") if command == "outline" else payload_dict
        if isinstance(law, dict):
            laws.append(_compact_law(law, evidence_level=command))
    elif command == "norm clause":
        source = payload_dict.get("source") or {}
        clause = payload_dict.get("clause")
        if isinstance(clause, dict):
            norm_clauses.append(
                _compact_norm_clause(
                    {
                        **clause,
                        "norm_source_id": clause.get("norm_source_id")
                        or source.get("id"),
                        "norm_source_name": source.get("name"),
                        "source_type": source.get("source_type"),
                    },
                    evidence_level="norm_clause",
                )
            )
    elif command in {"applicable", "relation", "history", "trace", "diff"}:
        time_effect.append(_compact_time_effect(command, payload_dict))

    record["laws"] = _dedupe(laws)
    record["articles"] = _dedupe(articles)
    record["norm_clauses"] = _dedupe(norm_clauses)
    record["time_effect"] = time_effect
    return record


def load_records(snapshot_path: str | Path | None) -> list[dict]:
    if snapshot_path is None:
        return []
    path = Path(snapshot_path).expanduser()
    if not path.exists():
        return []
    out: list[dict] = []
    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                out.append(item)
    return out


def _next_evidence_id(path: Path) -> str:
    if not path.exists():
        return "E0001"
    count = 0
    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if line.strip():
                count += 1
    return f"E{count + 1:04d}"


def _payload_ok(command: str, payload: dict) -> bool:
    if not payload:
        return False
    if payload.get("found") is False:
        return False
    if command == "article":
        return isinstance(payload.get("article"), dict)
    if command == "articles":
        return not payload.get("failed_section_count") and not payload.get("missing_count")
    if command == "fetch":
        return not payload.get("error") and bool(payload.get("law"))
    return not payload.get("error")


def _query_summary(command: str, payload: dict) -> dict:
    keys = (
        "query",
        "kind",
        "name",
        "requested_number",
        "requested_numbers",
        "as_of",
        "topic",
        "date",
        "law",
    )
    return {key: payload.get(key) for key in keys if key in payload}


def _compact_law(law: dict, *, evidence_level: str) -> dict:
    return {
        "evidence_level": evidence_level,
        "law_id": law.get("id") or law.get("law_id"),
        "title": law.get("title") or law.get("law_title"),
        "short_title": law.get("short_title") or law.get("law_short_title"),
        "status": law.get("status") or law.get("law_status"),
        "level": law.get("level"),
        "source_url": law.get("source_url"),
        "source_checked_at": law.get("source_checked_at"),
        "content_hash": law.get("source_hash")
        or ((law.get("current_revision") or {}).get("content_hash")),
    }


def _compact_article(
    article: dict,
    *,
    law: dict | None = None,
    evidence_level: str,
) -> dict:
    law = law or {}
    text = article.get("text") or ""
    return {
        "evidence_level": evidence_level,
        "law_id": article.get("law_id") or law.get("id"),
        "law_title": law.get("title"),
        "law_short_title": law.get("short_title"),
        "law_status": law.get("status"),
        "number": str(article.get("number") or ""),
        "number_display": article.get("number_display"),
        "title": article.get("title"),
        "part": article.get("part"),
        "source_url": law.get("source_url") or article.get("source_url"),
        "source_checked_at": law.get("source_checked_at"),
        "text_hash": _hash_text(text),
        "text_excerpt": _excerpt(text),
    }


def _compact_search_hit(hit: dict) -> dict:
    text = hit.get("text") or ""
    return {
        "evidence_level": "search_hit",
        "law_id": hit.get("law_id"),
        "law_title": hit.get("law_title"),
        "law_short_title": hit.get("law_short_title"),
        "law_status": hit.get("law_status"),
        "number": str(hit.get("number") or ""),
        "number_display": hit.get("number_display"),
        "part": hit.get("part"),
        "source_url": hit.get("source_url"),
        "freshness_days": hit.get("freshness_days"),
        "score": hit.get("score"),
        "match_kind": hit.get("match_kind"),
        "text_hash": _hash_text(text),
        "text_excerpt": _excerpt(text),
    }


def _compact_norm_clause(hit: dict, *, evidence_level: str) -> dict:
    text = hit.get("text") or ""
    return {
        "evidence_level": evidence_level,
        "norm_source_id": hit.get("norm_source_id") or hit.get("source_id"),
        "norm_source_name": hit.get("norm_source_name") or hit.get("source_name"),
        "source_type": hit.get("source_type"),
        "number": str(hit.get("number") or ""),
        "number_display": hit.get("number_display"),
        "title": hit.get("title"),
        "text_hash": _hash_text(text),
        "text_excerpt": _excerpt(text),
    }


def _compact_time_effect(command: str, payload: dict) -> dict:
    return {
        "command": command,
        "topic": payload.get("topic"),
        "date": payload.get("date") or payload.get("as_of"),
        "law": payload.get("law") or payload.get("name"),
        "ok": payload.get("ok"),
        "not_legal_conclusion": payload.get("not_legal_conclusion"),
    }


def _hash_text(text: str) -> str | None:
    if not text:
        return None
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _excerpt(text: str, limit: int = 120) -> str | None:
    if not text:
        return None
    collapsed = " ".join(text.split())
    return collapsed if len(collapsed) <= limit else collapsed[: limit - 1] + "…"


def _dedupe(items: list[dict]) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for item in items:
        key = json.dumps(item, ensure_ascii=False, sort_keys=True)
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out
