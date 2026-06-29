"""规范包存储与读取。"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from pathlib import Path

from chinalaw import normsources, service
from chinalaw.db import connect, migrate

_PACK_ITEM_TYPES = {"law", "article", "norm_source", "norm_clause", "reference"}
_PACK_ITEM_ROLES = {"core", "important", "supporting", "background"}


class NormPackError(ValueError):
    """规范包写入错误。CLI 层用 exit_code 映射退出码。"""

    exit_code = 2


class PackItemUnresolvedError(NormPackError):
    """严格模式下新增成员无法解析到本地规范。"""

    exit_code = 1


def _clean_text(value: object | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _load_json_object(raw: str | None, default: object) -> object:
    if not raw:
        return default
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return default


def _pack_id_from_name(name: str) -> str:
    lowered = name.lower().strip()
    slug = re.sub(r"[^a-z0-9]+", "-", lowered).strip("-")
    if slug:
        return slug
    digest = hashlib.sha1(name.encode("utf-8")).hexdigest()[:12]
    return f"pack-{digest}"


def _item_id(pack_id: str, item: dict, position: int) -> str:
    explicit = _clean_text(item.get("id"))
    if explicit:
        return explicit
    return f"{pack_id}:item:{position}"


def _normalize_item(pack_id: str, item: dict, position: int) -> dict:
    item_type = (_clean_text(item.get("item_type")) or "").lower()
    if not item_type:
        if item.get("norm_source_id") or item.get("norm_source_name"):
            item_type = (
                "norm_clause"
                if item.get("clause_number") is not None
                else "norm_source"
            )
        elif item.get("reference_text"):
            item_type = "reference"
        elif item.get("article_number") is not None:
            item_type = "article"
        elif item.get("law_id") or item.get("law_title"):
            item_type = "law"
        else:
            item_type = "reference"
    if item_type not in _PACK_ITEM_TYPES:
        raise ValueError(f"unsupported norm pack item_type: {item_type}")

    law_id = _clean_text(item.get("law_id"))
    law_title = _clean_text(item.get("law_title"))
    norm_source_id = _clean_text(item.get("norm_source_id"))
    norm_source_name = _clean_text(item.get("norm_source_name"))
    article_number = None
    if item_type == "article":
        article_number = service.normalize_article_number(
            item.get("article_number") or ""
        )
        if not article_number:
            raise ValueError("article item requires article_number")
    article_number_display = _clean_text(item.get("article_number_display"))
    clause_number = None
    if item_type == "norm_clause":
        clause_number = normsources.normalize_clause_number(
            item.get("clause_number") or ""
        )
        if not clause_number:
            raise ValueError("norm_clause item requires clause_number")
    clause_number_display = _clean_text(item.get("clause_number_display"))
    reference_text = _clean_text(item.get("reference_text"))
    if item_type == "reference" and not reference_text:
        reference_text = law_title or law_id or norm_source_name or norm_source_id
    if item_type in {"law", "article"} and not (law_id or law_title):
        raise ValueError(f"{item_type} item requires law_id or law_title")
    if item_type in {"norm_source", "norm_clause"} and not (
        norm_source_id or norm_source_name
    ):
        raise ValueError(
            f"{item_type} item requires norm_source_id or norm_source_name"
        )

    return {
        "id": _item_id(pack_id, item, position),
        "item_type": item_type,
        "law_id": law_id,
        "law_title": law_title,
        "article_number": article_number,
        "article_number_display": article_number_display,
        "norm_source_id": norm_source_id,
        "norm_source_name": norm_source_name,
        "clause_number": clause_number,
        "clause_number_display": clause_number_display,
        "role": _clean_text(item.get("role")) or "supporting",
        "reason": _clean_text(item.get("reason")),
        "note": _clean_text(item.get("note")),
        "reference_text": reference_text,
        "position": position,
    }


def _add_unique(target: list[dict], item: dict) -> None:
    key = json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    existing = {
        json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        for row in target
    }
    if key not in existing:
        target.append(item)


# C901: 已知复杂（McCabe 30），规范包依赖归一；列为待拆分技术债，见
# docs/decisions/ADR-0009-module-boundaries.md。
def _normalize_dependencies(value: object, items: list[dict]) -> dict:  # noqa: C901
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise ValueError("norm pack dependencies must be an object")
    dependencies = {
        "laws": [],
        "norm_sources": [],
        "packs": [],
    }

    for law in value.get("laws", []):
        if isinstance(law, str):
            _add_unique(dependencies["laws"], {"law_id": law})
        elif isinstance(law, dict):
            law_id = _clean_text(law.get("law_id") or law.get("id"))
            law_title = _clean_text(law.get("law_title") or law.get("title"))
            if law_id or law_title:
                item = {}
                if law_id:
                    item["law_id"] = law_id
                if law_title:
                    item["law_title"] = law_title
                _add_unique(dependencies["laws"], item)

    for source in value.get("norm_sources", []):
        if isinstance(source, str):
            _add_unique(dependencies["norm_sources"], {"norm_source_id": source})
        elif isinstance(source, dict):
            source_id = _clean_text(source.get("norm_source_id") or source.get("id"))
            source_name = _clean_text(
                source.get("norm_source_name") or source.get("name")
            )
            if source_id or source_name:
                item = {}
                if source_id:
                    item["norm_source_id"] = source_id
                if source_name:
                    item["norm_source_name"] = source_name
                _add_unique(dependencies["norm_sources"], item)

    for pack in value.get("packs", []):
        if isinstance(pack, str):
            _add_unique(dependencies["packs"], {"pack_id": pack})
        elif isinstance(pack, dict):
            pack_id = _clean_text(pack.get("pack_id") or pack.get("id"))
            pack_name = _clean_text(pack.get("pack_name") or pack.get("name"))
            if pack_id or pack_name:
                item = {}
                if pack_id:
                    item["pack_id"] = pack_id
                if pack_name:
                    item["pack_name"] = pack_name
                _add_unique(dependencies["packs"], item)

    for item in items:
        if item["item_type"] in {"law", "article"}:
            dep = {}
            if item.get("law_id"):
                dep["law_id"] = item["law_id"]
            if item.get("law_title"):
                dep["law_title"] = item["law_title"]
            if dep:
                _add_unique(dependencies["laws"], dep)
        elif item["item_type"] in {"norm_source", "norm_clause"}:
            dep = {}
            if item.get("norm_source_id"):
                dep["norm_source_id"] = item["norm_source_id"]
            if item.get("norm_source_name"):
                dep["norm_source_name"] = item["norm_source_name"]
            if dep:
                _add_unique(dependencies["norm_sources"], dep)

    return dependencies


def _normalize_pack_payload(payload: dict) -> dict:
    name = _clean_text(payload.get("name"))
    if not name:
        raise ValueError("norm pack requires name")
    pack_id = _clean_text(payload.get("id")) or _pack_id_from_name(name)
    items = payload.get("items")
    if not isinstance(items, list):
        raise ValueError("norm pack requires items list")
    normalized_items = [
        _normalize_item(pack_id, item, position)
        for position, item in enumerate(items, start=1)
    ]
    metadata = payload.get("metadata") or {}
    if not isinstance(metadata, dict):
        raise ValueError("norm pack metadata must be an object")
    dependencies = _normalize_dependencies(
        payload.get("dependencies"), normalized_items
    )
    return {
        "kind": "norm_pack",
        "id": pack_id,
        "name": name,
        "summary": _clean_text(payload.get("summary")),
        "scope": _clean_text(payload.get("scope")),
        "maintainer": _clean_text(payload.get("maintainer")),
        "version_policy": _clean_text(payload.get("version_policy")) or "current",
        "source_kind": _clean_text(payload.get("source_kind")) or "manual",
        "metadata": metadata,
        "dependencies": dependencies,
        "items": normalized_items,
    }


def _pack_row_to_dict(row: sqlite3.Row) -> dict:
    metadata = _load_json_object(row["metadata_json"], {})
    dependencies_json = (
        # sqlite3.Row 的 ``in`` 比较的是值而非列名，必须显式 .keys()。
        row["dependencies_json"] if "dependencies_json" in row.keys() else "{}"  # noqa: SIM118
    )
    dependencies = _load_json_object(dependencies_json, {})
    if not isinstance(metadata, dict):
        metadata = {}
    if not isinstance(dependencies, dict):
        dependencies = {}
    return {
        "kind": "norm_pack",
        "id": row["id"],
        "name": row["name"],
        "summary": row["summary"],
        "scope": row["scope"],
        "maintainer": row["maintainer"],
        "version_policy": row["version_policy"],
        "source_kind": row["source_kind"],
        "metadata": metadata,
        "dependencies": dependencies,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "item_count": row["item_count"] if "item_count" in row.keys() else 0,  # noqa: SIM118
    }


def _item_row_to_dict(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "item_type": row["item_type"],
        "law_id": row["law_id"],
        "law_title": row["law_title"],
        "article_number": row["article_number"],
        "article_number_display": row["article_number_display"],
        "norm_source_id": row["norm_source_id"],
        "norm_source_name": row["norm_source_name"],
        "clause_number": row["clause_number"],
        "clause_number_display": row["clause_number_display"],
        "role": row["role"],
        "reason": row["reason"],
        "note": row["note"],
        "reference_text": row["reference_text"],
        "position": row["position"],
    }


def _item_semantic_key(item: dict) -> tuple:
    item_type = item.get("item_type")
    if item_type == "article":
        return (
            item_type,
            item.get("law_id") or "",
            item.get("law_title") or "",
            item.get("article_number") or "",
        )
    if item_type == "law":
        return (
            item_type,
            item.get("law_id") or "",
            item.get("law_title") or "",
        )
    if item_type == "norm_clause":
        return (
            item_type,
            item.get("norm_source_id") or "",
            item.get("norm_source_name") or "",
            item.get("clause_number") or "",
        )
    if item_type == "norm_source":
        return (
            item_type,
            item.get("norm_source_id") or "",
            item.get("norm_source_name") or "",
        )
    return (item_type, item.get("reference_text") or "")


def _compact_law(law: dict) -> dict:
    current_revision = law.get("current_revision") or {}
    return {
        "id": law.get("id"),
        "title": law.get("title"),
        "short_title": law.get("short_title"),
        "status": law.get("status"),
        "level": law.get("level"),
        "source_url": law.get("source_url"),
        "source_checked_at": law.get("source_checked_at"),
        "current_revision": (
            {
                "id": current_revision.get("id"),
                "version_label": current_revision.get("version_label"),
                "released_at": current_revision.get("released_at"),
                "effective_at": current_revision.get("effective_at"),
                "content_hash": current_revision.get("content_hash"),
            }
            if current_revision
            else None
        ),
        "categories": [category.get("name") for category in law.get("categories", [])],
    }


def _compact_norm_source(source: dict) -> dict:
    return {
        "id": source.get("id"),
        "name": source.get("name"),
        "short_name": source.get("short_name"),
        "source_type": source.get("source_type"),
        "authority": source.get("authority"),
        "binding_scope": source.get("binding_scope"),
        "jurisdiction": source.get("jurisdiction"),
        "effective_at": source.get("effective_at"),
        "repealed_at": source.get("repealed_at"),
        "source_url": source.get("source_url"),
        "source_name": source.get("source_name"),
        "source_checked_at": source.get("source_checked_at"),
        "source_hash": source.get("source_hash"),
        "clause_count": source.get("clause_count"),
    }


def _resolve_pack_item(db_path: Path | str, item: dict) -> dict | None:
    law_identifier = item.get("law_id") or item.get("law_title")
    if item["item_type"] == "law" and law_identifier:
        law = service.get_law(db_path, law_identifier)
        if law is None:
            return None
        return {"kind": "law", "law": _compact_law(law)}
    if item["item_type"] == "article" and law_identifier and item.get("article_number"):
        payload = service.get_article(db_path, law_identifier, item["article_number"])
        if payload is None or payload.get("article") is None:
            return None
        return {
            "kind": "article",
            "law": _compact_law(payload["law"]),
            "article": payload["article"],
        }
    norm_identifier = item.get("norm_source_id") or item.get("norm_source_name")
    if item["item_type"] == "norm_source" and norm_identifier:
        source = normsources.get_source(db_path, norm_identifier)
        if source is None:
            return None
        return {"kind": "norm_source", "source": _compact_norm_source(source)}
    if (
        item["item_type"] == "norm_clause"
        and norm_identifier
        and item.get("clause_number")
    ):
        payload = normsources.get_clause(
            db_path, norm_identifier, item["clause_number"]
        )
        if payload is None or payload.get("clause") is None:
            return None
        return {
            "kind": "norm_clause",
            "source": _compact_norm_source(payload["source"]),
            "clause": payload["clause"],
        }
    return None


def _resolve_pack_row(conn: sqlite3.Connection, identifier: str) -> sqlite3.Row | None:
    exact = _clean_text(identifier)
    if not exact:
        return None
    row = conn.execute(
        """
        SELECT p.*, COUNT(i.id) AS item_count
        FROM norm_packs p
        LEFT JOIN norm_pack_items i ON i.pack_id = p.id
        WHERE p.id = ? OR p.name = ?
        GROUP BY p.id
        ORDER BY CASE WHEN p.id = ? THEN 0 ELSE 1 END
        LIMIT 1
        """,
        (exact, exact, exact),
    ).fetchone()
    if row is not None:
        return row
    escaped = exact.replace("%", r"\%").replace("_", r"\_")
    fuzzy = f"%{escaped}%"
    return conn.execute(
        """
        SELECT p.*, COUNT(i.id) AS item_count
        FROM norm_packs p
        LEFT JOIN norm_pack_items i ON i.pack_id = p.id
        WHERE p.id LIKE ? ESCAPE '\\' OR p.name LIKE ? ESCAPE '\\'
        GROUP BY p.id
        ORDER BY LENGTH(p.name) ASC
        LIMIT 1
        """,
        (fuzzy, fuzzy),
    ).fetchone()


def _enrich_item_from_resolved(item: dict, resolved: dict | None) -> dict:
    if not resolved:
        return item
    enriched = dict(item)
    if resolved.get("kind") == "article":
        law = resolved.get("law") or {}
        article = resolved.get("article") or {}
        enriched["law_id"] = law.get("id") or enriched.get("law_id")
        enriched["law_title"] = law.get("title") or enriched.get("law_title")
        enriched["article_number"] = (
            article.get("number") or enriched.get("article_number")
        )
        enriched["article_number_display"] = (
            article.get("number_display") or enriched.get("article_number_display")
        )
    elif resolved.get("kind") == "law":
        law = resolved.get("law") or {}
        enriched["law_id"] = law.get("id") or enriched.get("law_id")
        enriched["law_title"] = law.get("title") or enriched.get("law_title")
    elif resolved.get("kind") == "norm_clause":
        source = resolved.get("source") or {}
        clause = resolved.get("clause") or {}
        enriched["norm_source_id"] = source.get("id") or enriched.get("norm_source_id")
        enriched["norm_source_name"] = (
            source.get("name") or enriched.get("norm_source_name")
        )
        enriched["clause_number"] = clause.get("number") or enriched.get("clause_number")
        enriched["clause_number_display"] = (
            clause.get("number_display") or enriched.get("clause_number_display")
        )
    elif resolved.get("kind") == "norm_source":
        source = resolved.get("source") or {}
        enriched["norm_source_id"] = source.get("id") or enriched.get("norm_source_id")
        enriched["norm_source_name"] = (
            source.get("name") or enriched.get("norm_source_name")
        )
    return enriched


def add_item_to_pack(
    db_path: Path | str,
    identifier: str,
    item: dict,
    *,
    create: bool = False,
    summary: str | None = None,
    scope: str | None = None,
    maintainer: str | None = None,
    version_policy: str = "current",
    source_kind: str = "workflow",
    require_resolved: bool = True,
) -> dict | None:
    """向规范包追加一个成员，作为 agent 工作流沉淀入口。

    默认严格：law/article/norm_source/norm_clause 必须能解析到本地数据；
    reference 不需要解析。重复成员按语义键幂等跳过。
    """

    pack_name = _clean_text(identifier)
    if not pack_name:
        raise NormPackError("pack name is required")
    explicit_item_id = _clean_text(item.get("id"))

    with connect(db_path) as conn:
        migrate(conn)
        row = _resolve_pack_row(conn, pack_name)
        if row is None:
            if not create:
                return None
            pack_id = _pack_id_from_name(pack_name)
            pack = {
                "id": pack_id,
                "name": pack_name,
                "dependencies": {},
                "new": True,
            }
            existing_items: list[dict] = []
            next_position = 1
        else:
            pack = _pack_row_to_dict(row)
            item_rows = conn.execute(
                """
                SELECT *
                FROM norm_pack_items
                WHERE pack_id = ?
                ORDER BY position ASC
                """,
                (row["id"],),
            ).fetchall()
            existing_items = [_item_row_to_dict(item_row) for item_row in item_rows]
            next_position = (
                max(
                    (existing.get("position") or 0 for existing in existing_items),
                    default=0,
                )
                + 1
            )

        normalized = _normalize_item(pack["id"], item, next_position)

    resolved = _resolve_pack_item(db_path, normalized)
    if (
        require_resolved
        and normalized["item_type"] != "reference"
        and resolved is None
    ):
        raise PackItemUnresolvedError(
            "pack item cannot be resolved in local database; "
            "fetch/import it first or pass --allow-unresolved"
        )
    normalized = _enrich_item_from_resolved(normalized, resolved)
    semantic_key = _item_semantic_key(normalized)

    with connect(db_path) as conn:
        migrate(conn)
        row = _resolve_pack_row(conn, pack["id"])
        if row is None:
            if not create:
                return None
            conn.execute(
                """
                INSERT INTO norm_packs (
                    id, name, summary, scope, maintainer, version_policy,
                    source_kind, metadata_json, dependencies_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    pack["id"],
                    pack["name"],
                    _clean_text(summary),
                    _clean_text(scope),
                    _clean_text(maintainer),
                    _clean_text(version_policy) or "current",
                    _clean_text(source_kind) or "workflow",
                    json.dumps({"created_by": "pack_add"}, ensure_ascii=False),
                    "{}",
                ),
            )
            row = _resolve_pack_row(conn, pack["id"])
            if row is None:
                return None
        pack = _pack_row_to_dict(row)
        item_rows = conn.execute(
            """
            SELECT *
            FROM norm_pack_items
            WHERE pack_id = ?
            ORDER BY position ASC
            """,
            (pack["id"],),
        ).fetchall()
        existing_items = [_item_row_to_dict(item_row) for item_row in item_rows]
        for existing in existing_items:
            if _item_semantic_key(existing) == semantic_key:
                return {
                    "kind": "norm_pack_item_add",
                    "pack_id": pack["id"],
                    "name": pack["name"],
                    "added": False,
                    "duplicate": True,
                    "item": existing,
                    "resolved": _resolve_pack_item(db_path, existing),
                    "item_count": len(existing_items),
                }

        normalized["position"] = (
            max(
                (existing.get("position") or 0 for existing in existing_items),
                default=0,
            )
            + 1
        )
        normalized["id"] = (
            explicit_item_id or f"{pack['id']}:item:{normalized['position']}"
        )
        conn.execute(
            """
            INSERT INTO norm_pack_items (
                id, pack_id, item_type, law_id, law_title,
                article_number, article_number_display, norm_source_id,
                norm_source_name, clause_number, clause_number_display,
                role, reason, note, reference_text, position
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                normalized["id"],
                pack["id"],
                normalized["item_type"],
                normalized["law_id"],
                normalized["law_title"],
                normalized["article_number"],
                normalized["article_number_display"],
                normalized["norm_source_id"],
                normalized["norm_source_name"],
                normalized["clause_number"],
                normalized["clause_number_display"],
                normalized["role"],
                normalized["reason"],
                normalized["note"],
                normalized["reference_text"],
                normalized["position"],
            ),
        )
        updated_items = [*existing_items, normalized]
        dependencies = _normalize_dependencies(pack.get("dependencies"), updated_items)
        conn.execute(
            """
            UPDATE norm_packs
            SET dependencies_json = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                json.dumps(dependencies, ensure_ascii=False, separators=(",", ":")),
                pack["id"],
            ),
        )
        return {
            "kind": "norm_pack_item_add",
            "pack_id": pack["id"],
            "name": pack["name"],
            "added": True,
            "duplicate": False,
            "item": normalized,
            "resolved": resolved,
            "item_count": len(updated_items),
            "dependencies": dependencies,
        }


def import_pack_from_dict(conn: sqlite3.Connection, payload: dict) -> dict:
    migrate(conn)
    normalized = _normalize_pack_payload(payload)
    pack_id = normalized["id"]
    name = normalized["name"]
    normalized_items = normalized["items"]

    conn.execute(
        """
        INSERT INTO norm_packs (
            id, name, summary, scope, maintainer, version_policy,
            source_kind, metadata_json, dependencies_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            name=excluded.name,
            summary=excluded.summary,
            scope=excluded.scope,
            maintainer=excluded.maintainer,
            version_policy=excluded.version_policy,
            source_kind=excluded.source_kind,
            metadata_json=excluded.metadata_json,
            dependencies_json=excluded.dependencies_json,
            updated_at=CURRENT_TIMESTAMP
        """,
        (
            pack_id,
            name,
            normalized["summary"],
            normalized["scope"],
            normalized["maintainer"],
            normalized["version_policy"],
            normalized["source_kind"],
            json.dumps(
                normalized["metadata"],
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            json.dumps(
                normalized["dependencies"],
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        ),
    )

    conn.execute("DELETE FROM norm_pack_items WHERE pack_id = ?", (pack_id,))
    for item in normalized_items:
        conn.execute(
            """
            INSERT INTO norm_pack_items (
                id, pack_id, item_type, law_id, law_title,
                article_number, article_number_display, norm_source_id,
                norm_source_name, clause_number, clause_number_display,
                role, reason, note, reference_text, position
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item["id"],
                pack_id,
                item["item_type"],
                item["law_id"],
                item["law_title"],
                item["article_number"],
                item["article_number_display"],
                item["norm_source_id"],
                item["norm_source_name"],
                item["clause_number"],
                item["clause_number_display"],
                item["role"],
                item["reason"],
                item["note"],
                item["reference_text"],
                item["position"],
            ),
        )

    return {
        "kind": "norm_pack_import",
        "pack_id": pack_id,
        "name": name,
        "items_loaded": len(normalized_items),
        "dependencies": normalized["dependencies"],
        "source_kind": normalized["source_kind"],
    }


def import_pack_file(db_path: Path | str, path: Path | str) -> dict:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    with connect(db_path) as conn:
        result = import_pack_from_dict(conn, payload)
    result["path"] = str(path)
    return result


def list_packs(db_path: Path | str) -> list[dict]:
    with connect(db_path) as conn:
        migrate(conn)
        rows = conn.execute(
            """
            SELECT p.*, COUNT(i.id) AS item_count
            FROM norm_packs p
            LEFT JOIN norm_pack_items i ON i.pack_id = p.id
            GROUP BY p.id
            ORDER BY p.name ASC
            """
        ).fetchall()
        return [_pack_row_to_dict(row) for row in rows]


def get_pack(
    db_path: Path | str, identifier: str, *, resolve: bool = True
) -> dict | None:
    with connect(db_path) as conn:
        migrate(conn)
        row = _resolve_pack_row(conn, identifier)
        if row is None:
            return None
        pack = _pack_row_to_dict(row)
        item_rows = conn.execute(
            """
            SELECT *
            FROM norm_pack_items
            WHERE pack_id = ?
            ORDER BY position ASC
            """,
            (row["id"],),
        ).fetchall()
    items = [_item_row_to_dict(item_row) for item_row in item_rows]
    resolved_item_count = 0
    if resolve:
        for item in items:
            resolved = _resolve_pack_item(db_path, item)
            if resolved is not None:
                item["resolved"] = resolved
                resolved_item_count += 1
    pack["items"] = items
    pack["item_count"] = len(items)
    pack["resolved_item_count"] = resolved_item_count
    return pack


def _classify_missing_article(db_path: Path | str, item: dict) -> tuple[str, str, str]:
    """区分 article 引用未解析的三种原因，全部视为 error。

    Returns: (severity, code, message)
    - "error" / "missing_article":             规范包成员缺少 law_id/law_title，无法解析
    - "error" / "missing_law_for_article":     规范包引用的法规根本未入库
    - "error" / "stub_law_pending_articles":   法规已索引但 articles 为 stub（=0 条）；
        article 类型引用必须解析到具体条文，否则维护者应改为 reference 或 norm_clause
    - "error" / "pending_article_in_dataset":  法规已有部分 articles，但规范包引用的
        具体条款尚未入库（与"条款号写错"在外部不可区分），同上需补全 fixture 或改为 reference
    """
    law_identifier = item.get("law_id") or item.get("law_title")
    if not law_identifier:
        return (
            "error",
            "missing_article",
            "规范包成员缺少 law_id / law_title，无法解析。",
        )
    law = service.get_law(db_path, law_identifier)
    if law is None:
        return (
            "error",
            "missing_law_for_article",
            "规范包引用的法规尚未入库。",
        )
    if law.get("articles_coverage") == "stub" or law.get("article_count", 0) == 0:
        return (
            "error",
            "stub_law_pending_articles",
            "规范包引用的法规为 stub（仅 metadata）；article 类型引用必须解析到具体条文。"
            "请按 docs/DATA_INDEX.md §3 补全 fixture，或将该项改为 reference / pending_reference。",
        )
    return (
        "error",
        "pending_article_in_dataset",
        "规范包引用的条款尚未在当前数据集中（fixture 仅含部分种子条款）。"
        "补全 fixture 或核对条款号；如确实是占位引用，请改为 reference 类型。",
    )


def _validation_issue(
    severity: str,
    code: str,
    message: str,
    *,
    item: dict | None = None,
    dependency: dict | None = None,
) -> dict:
    issue = {
        "severity": severity,
        "code": code,
        "message": message,
    }
    if item is not None:
        issue.update(
            {
                "item_id": item.get("id"),
                "item_type": item.get("item_type"),
                "position": item.get("position"),
                "role": item.get("role"),
            }
        )
    if dependency is not None:
        issue["dependency"] = dependency
    return issue


def _pack_exists(db_path: Path | str, identifier: str) -> bool:
    with connect(db_path) as conn:
        migrate(conn)
        return _resolve_pack_row(conn, identifier) is not None


def _dependency_identifier(dependency: dict, id_key: str, name_key: str) -> str | None:
    return _clean_text(dependency.get(id_key)) or _clean_text(dependency.get(name_key))


def _validate_dependency(db_path: Path | str, kind: str, dependency: dict) -> dict | None:
    if kind == "law":
        identifier = _dependency_identifier(dependency, "law_id", "law_title")
        if identifier and service.get_law(db_path, identifier) is None:
            return _validation_issue(
                "error",
                "missing_law_dependency",
                f"未找到依赖法规：{identifier}",
                dependency=dependency,
            )
    elif kind == "norm_source":
        identifier = _dependency_identifier(
            dependency, "norm_source_id", "norm_source_name"
        )
        if identifier and normsources.get_source(db_path, identifier) is None:
            return _validation_issue(
                "error",
                "missing_norm_source_dependency",
                f"未找到依赖私域规范：{identifier}",
                dependency=dependency,
            )
    elif kind == "pack":
        identifier = _dependency_identifier(dependency, "pack_id", "pack_name")
        if identifier and not _pack_exists(db_path, identifier):
            return _validation_issue(
                "warning",
                "missing_pack_dependency",
                f"未找到依赖规范包：{identifier}",
                dependency=dependency,
            )
    return None


def validate_pack_dict(db_path: Path | str, payload: dict) -> dict:
    pack = _normalize_pack_payload(payload)
    return _validate_normalized_pack(db_path, pack)


def validate_pack_file(db_path: Path | str, path: Path | str) -> dict:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    report = validate_pack_dict(db_path, payload)
    report["path"] = str(path)
    return report


def validate_pack(db_path: Path | str, identifier: str) -> dict | None:
    pack = get_pack(db_path, identifier, resolve=False)
    if pack is None:
        return None
    return _validate_normalized_pack(db_path, pack)


def _validate_normalized_pack(db_path: Path | str, pack: dict) -> dict:
    issues: list[dict] = []
    items = pack.get("items") or []
    resolved_item_count = 0
    required_item_count = 0

    for item in items:
        role = item.get("role")
        if role not in _PACK_ITEM_ROLES:
            issues.append(
                _validation_issue(
                    "warning",
                    "unknown_role",
                    f"未知成员角色：{role}",
                    item=item,
                )
            )
        if role in {"core", "important"} and not item.get("reason"):
            issues.append(
                _validation_issue(
                    "warning",
                    "missing_reason",
                    "core / important 成员应说明纳入理由。",
                    item=item,
                )
            )

        if item["item_type"] == "reference":
            note = (item.get("note") or "").strip()
            if note.startswith("pending:") and item.get("role") in {"core", "important"}:
                issues.append(
                    _validation_issue(
                        "warning",
                        "pending_reference_in_pack",
                        "Pending reference 占位（待 fixture 补全后升级为 article 引用）；"
                        "agent 必须在最终回答前用 chinalaw article 核验原文，不要把 reference_text "
                        "当作已核验法条。",
                        item=item,
                    )
                )
            continue

        required_item_count += 1
        resolved = _resolve_pack_item(db_path, item)
        if resolved is not None:
            resolved_item_count += 1
            continue

        item_type = item["item_type"]
        if item_type == "article":
            severity, code, message = _classify_missing_article(db_path, item)
        else:
            severity = "error"
            code = {
                "law": "missing_law",
                "norm_source": "missing_norm_source",
                "norm_clause": "missing_norm_clause",
            }.get(item_type, "unresolved_item")
            message = "规范包成员无法在当前数据库中解析。"
        issues.append(
            _validation_issue(severity, code, message, item=item)
        )

    dependencies = pack.get("dependencies") or {}
    for dependency in dependencies.get("laws", []):
        issue = _validate_dependency(db_path, "law", dependency)
        if issue is not None:
            issues.append(issue)
    for dependency in dependencies.get("norm_sources", []):
        issue = _validate_dependency(db_path, "norm_source", dependency)
        if issue is not None:
            issues.append(issue)
    for dependency in dependencies.get("packs", []):
        issue = _validate_dependency(db_path, "pack", dependency)
        if issue is not None:
            issues.append(issue)

    error_count = sum(1 for issue in issues if issue["severity"] == "error")
    warning_count = sum(1 for issue in issues if issue["severity"] == "warning")
    return {
        "kind": "norm_pack_validation",
        "ok": error_count == 0,
        "pack_id": pack.get("id"),
        "name": pack.get("name"),
        "item_count": len(items),
        "required_item_count": required_item_count,
        "resolved_item_count": resolved_item_count,
        "error_count": error_count,
        "warning_count": warning_count,
        "dependencies": dependencies,
        "issues": issues,
    }


def export_pack(db_path: Path | str, identifier: str) -> dict | None:
    pack = get_pack(db_path, identifier, resolve=False)
    if pack is None:
        return None
    return {
        "kind": "norm_pack",
        "id": pack["id"],
        "name": pack["name"],
        "summary": pack.get("summary"),
        "scope": pack.get("scope"),
        "maintainer": pack.get("maintainer"),
        "version_policy": pack.get("version_policy"),
        "source_kind": pack.get("source_kind"),
        "metadata": pack.get("metadata", {}),
        "dependencies": pack.get("dependencies", {}),
        "items": pack.get("items", []),
    }
