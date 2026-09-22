"""Preserve supplied category associations without silently rewriting shared taxonomy."""

from __future__ import annotations

import sqlite3

from chinalaw.admin.errors import LibraryError

CATEGORY_FIELDS = ("id", "name", "parent_id", "description")


def category_definitions(conn: sqlite3.Connection) -> dict[str, dict]:
    return {
        row["id"]: dict(row)
        for row in conn.execute(
            "SELECT id, name, parent_id, description FROM categories ORDER BY id"
        )
    }


def category_closure(definitions: dict[str, dict], identifiers: list[str]) -> list[dict]:
    result, seen = [], set()

    def visit(identifier: str, ancestors: set[str]) -> None:
        if identifier in seen:
            return
        if identifier in ancestors or len(ancestors) > 100:
            raise LibraryError("invalid_categories", "分类目录存在循环或层级过深。")
        if identifier not in definitions:
            raise LibraryError("unknown_category", "分类关联缺少对应的目录定义。")
        item = definitions[identifier]
        if item.get("parent_id"):
            visit(item["parent_id"], ancestors | {identifier})
        result.append(item)
        seen.add(identifier)

    for identifier in sorted(set(identifiers)):
        visit(identifier, set())
    return result


def current_categories(conn: sqlite3.Connection, law_id: str) -> dict:
    identifiers = [
        row[0]
        for row in conn.execute(
            "SELECT category_id FROM law_categories WHERE law_id = ? ORDER BY category_id",
            (law_id,),
        )
    ]
    definitions = category_definitions(conn) if identifiers else {}
    return {"category_ids": identifiers, "categories": category_closure(definitions, identifiers)}


def freeze_categories(conn: sqlite3.Connection, payload: dict, before: dict | None) -> None:
    # The existing loader preserves associations when category_ids is absent/empty.
    identifiers = payload.get("category_ids") or (before or {}).get("category_ids", [])
    supplied = payload.get("categories") or []
    if not isinstance(identifiers, list) or any(not isinstance(x, str) for x in identifiers):
        raise LibraryError("invalid_categories", "分类标识必须是字符串数组。")
    if not isinstance(supplied, list):
        raise LibraryError("invalid_categories", "分类目录必须是对象数组。")
    definitions = category_definitions(conn)
    supplied_ids: set[str] = set()
    for item in supplied:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("id"), str)
            or not isinstance(item.get("name"), str)
        ):
            raise LibraryError("invalid_categories", "分类目录必须提供有效标识和名称。")
        if item["id"] in supplied_ids:
            raise LibraryError("invalid_categories", "分类目录包含重复标识。")
        if (
            not item["id"]
            or not item["name"]
            or any(
                item.get(field) is not None and not isinstance(item[field], str)
                for field in ("parent_id", "description")
            )
        ):
            raise LibraryError("invalid_categories", "分类字段必须为文本。")
        supplied_ids.add(item["id"])
        definitions[item["id"]] = {key: item.get(key) for key in CATEGORY_FIELDS}
    payload["category_ids"] = sorted(set(x for x in identifiers if x))
    payload["categories"] = category_closure(definitions, payload["category_ids"])
    check_category_conflicts(conn, payload)


def has_category_conflict(conn: sqlite3.Connection, payload: dict) -> bool:
    """True when a frozen definition no longer matches the shared taxonomy."""
    existing = category_definitions(conn)
    return any(
        item["id"] in existing and existing[item["id"]] != item
        for item in payload.get("categories", [])
    )


def check_category_conflicts(conn: sqlite3.Connection, payload: dict) -> None:
    if has_category_conflict(conn, payload):
        raise LibraryError(
            "category_conflict",
            "导入分类定义与现有目录不同，请核对分类目录后再导入。",
            status=409,
        )
