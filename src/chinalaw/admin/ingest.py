"""Prepare uploads or source fetches for human review, without changing current text."""

from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path

from chinalaw import cleaning, fetch, normsources
from chinalaw.admin import artifacts, drafts
from chinalaw.admin.errors import LibraryError, require_kind
from chinalaw.db import read_only_operation

PUBLIC_METADATA = frozenset(
    {
        "id",
        "title",
        "short_title",
        "aliases",
        "level",
        "status",
        "issuing_body",
        "document_number",
        "released_at",
        "effective_at",
        "repealed_at",
        "source_url",
        "source_name",
    }
)
PRIVATE_METADATA = frozenset(
    {
        "id",
        "name",
        "short_name",
        "aliases",
        "source_type",
        "authority",
        "binding_scope",
        "jurisdiction",
        "effective_at",
        "repealed_at",
        "source_url",
        "source_name",
    }
)


def import_artifact(
    db_path: Path | str,
    artifacts_root: Path | str,
    artifact_id: str,
    *,
    kind: str,
    metadata: dict | None = None,
    job_id: str | None = None,
) -> dict:
    require_kind(kind)
    metadata = metadata or {}
    allowed = PUBLIC_METADATA if kind == "law" else PRIVATE_METADATA
    if set(metadata) - allowed:
        raise LibraryError("invalid_metadata", "导入元数据包含不支持的字段。")
    with artifacts.source_file(db_path, artifacts_root, artifact_id) as (artifact, path):
        origin = {"type": "upload", "filename": artifact["filename"], "artifact_ids": [artifact_id]}
        if path.suffix == ".json":
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
            if not isinstance(payload, dict):
                raise LibraryError("invalid_payload", "JSON 文件必须包含一个完整资料对象。")
            payload = {**payload, **metadata}
            origin["text_artifact_id"] = artifact_id
            warnings = []
        else:
            if path.suffix == ".pdf" and shutil.which("pdftotext") is None:
                raise LibraryError(
                    "pdf_tool_missing",
                    "缺少 PDF 文本提取工具；macOS 安装 brew install poppler，"
                    "Debian/Ubuntu 安装 apt-get install poppler-utils。",
                )
            text = normsources.read_source_text(path)
            if not text.strip():
                raise LibraryError(
                    "source_has_no_text", "未能提取文字；请检查原件，扫描件需要先取得文本。"
                )
            extracted = artifacts.save_text(
                db_path,
                artifacts_root,
                "原文文本.txt",
                text,
            )
            origin["artifact_ids"].append(extracted["id"])
            origin["text_artifact_id"] = extracted["id"]
            payload, warnings = _text_payload(kind, text, artifact, metadata)
    return drafts.create_draft(
        db_path, kind, payload, origin=origin, warnings=warnings, job_id=job_id
    )


def _text_payload(kind: str, text: str, artifact: dict, metadata: dict) -> tuple[dict, list[dict]]:
    title = Path(artifact["filename"]).stem
    source = {
        "source_url": f"local-file://library/{artifact['id']}",
        "source_name": artifact["filename"],
    }
    if kind == "law":
        if not metadata.get("level") or not metadata.get("status"):
            raise LibraryError("metadata_required", "请明确选择公开规范类型和状态。")
        values = {"id": "local-law-" + uuid.uuid4().hex, "title": title, **source, **metadata}
        payload = cleaning.canonicalize(text, source_kind="markdown", **values)
        return payload, []
    if not metadata.get("source_type"):
        raise LibraryError("metadata_required", "请选择私域规范类型。")
    values = {"name": title, **source, **metadata}
    source_id = values.pop("id", None)
    payload = normsources.build_source_from_text(
        text,
        source_id=source_id or "local-norm-" + uuid.uuid4().hex,
        metadata={
            "ingest": {
                "artifact_id": artifact["id"],
                "original_filename": artifact["filename"],
                "format": Path(artifact["filename"]).suffix.lstrip("."),
            }
        },
        **values,
    )
    return payload, normsources.analyze_split_quality(text, payload["clauses"])


def fetch_candidates(db_path: Path | str, query: str, *, source: str = "flk_npc") -> dict:
    if not 1 <= len(query.strip()) <= 200:
        raise LibraryError("invalid_query", "请输入 1–200 字的规范名称。")
    with read_only_operation():
        return fetch.fetch_law(db_path, query, source=source, list_matches=True, limit=10)


def fetch_draft(
    db_path: Path | str,
    query: str,
    *,
    source: str = "flk_npc",
    prefer_id: str | None = None,
    job_id: str | None = None,
) -> dict:
    if not 1 <= len(query.strip()) <= 200 or (prefer_id is not None and len(prefer_id) > 2000):
        raise LibraryError("invalid_query", "法规名称或来源标识不正确。")
    with read_only_operation():
        result = fetch.fetch_law(
            db_path,
            query,
            source=source,
            dry_run=True,
            prefer_bbbs=prefer_id,
            enrich_aliases=False,
        )
    return drafts.create_draft(
        db_path,
        "law",
        result["law"],
        origin={
            "type": "fetch",
            "source": source,
            "matched_id": result.get("matched_id"),
            "source_url": result["law"].get("source_url"),
        },
        job_id=job_id,
    )
