"""法规 row × payload 的「是否同一部法」判定。

历史背景：fetch / service 各有一套「同一法」判定，口径差异是预期的，
但散落 3 处导致改 alias 顺序时碰到隐式行为漂移。本模块把判定逻辑
集中、参数化，调用方显式声明严格度。

本模块不替代 ``service.py`` 的 SQL 查询路径——SQL 查询走索引，本模块
在拿到候选 row 后做 Python 层二次校验。

设计：纯函数，不读 DB、不抛异常。详见 ``docs/FETCH_LAYER_SPEC.md`` §2。
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any


def _names_from(payload: Any) -> set[str]:
    """从 row / dict 中收集 ``title`` / ``short_title`` / ``aliases`` 三组名称。

    支持 ``sqlite3.Row``、``dict`` 两种输入。``aliases`` 字段可能是 list（payload
    形态）或 JSON-encoded str（laws row 形态）。空字符串与非字符串都跳过。
    """

    title = _read(payload, "title")
    short = _read(payload, "short_title")
    aliases_raw = _read(payload, "aliases")

    names: set[str] = set()
    for value in (title, short):
        if isinstance(value, str) and value.strip():
            names.add(value.strip())

    if isinstance(aliases_raw, str):
        try:
            decoded = json.loads(aliases_raw) if aliases_raw else []
        except json.JSONDecodeError:
            decoded = []
    else:
        decoded = aliases_raw or []

    if isinstance(decoded, list):
        for alias in decoded:
            if isinstance(alias, str) and alias.strip():
                names.add(alias.strip())

    return names


def _read(payload: Any, key: str) -> Any:
    """统一访问 sqlite3.Row / dict 字段；缺字段返回 None。"""

    if payload is None:
        return None
    if isinstance(payload, dict):
        return payload.get(key)
    # sqlite3.Row 支持 row[key]，但 KeyError 而非 None
    try:
        return payload[key]
    except (KeyError, IndexError):
        return None


def _same_version_dates(row: Any, payload: Any) -> bool:
    """两边的 ``released_at`` / ``effective_at`` 都非空时，要求一致；任一为空则不挑剔。

    用途：fetch 写入前的修订版区分——避免把 FLK 拉到的 2023 修订版盖到
    手工导入的 2018 旧版 row 上。
    """

    for key in ("released_at", "effective_at"):
        existing = _read(row, key)
        incoming = _read(payload, key)
        existing_s = existing.strip() if isinstance(existing, str) else ""
        incoming_s = incoming.strip() if isinstance(incoming, str) else ""
        if existing_s and incoming_s and existing_s != incoming_s:
            return False
    return True


def law_row_matches_payload(
    row: sqlite3.Row | dict | None,
    payload: dict | None,
    *,
    strict: bool,
) -> bool:
    """判断 ``row`` 是否与 incoming ``payload`` 描述同一部法。

    Args:
        row: DB 返回的 laws row（或 dict 形态）。``aliases`` 字段允许 JSON 字符串。
        payload: 待入库 / 待对齐的 canonical payload。``aliases`` 字段为 list。
        strict: ``True`` → fetch canonical id 解析口径；
                ``False`` → 用户查询命中后的兜底校验口径。

    严格 (``strict=True``)：用于 fetch 决定 stable id 替换，避免把 FLK 拉来
    的修订版盖到手工导入的同名旧版。判定规则：
        1. 若 row.source_name 与 payload.source_name 都非空但不同 → ``False``;
        2. 若 row.released_at / effective_at 与 payload 同字段都非空但不同
           → ``False``（修订版日期不同 = 不同 row）;
        3. 名称交集（title / short_title / aliases）非空 → ``True``，否则 ``False``。

    宽松 (``strict=False``)：用于 service.resolve 命中后的二次校验，避免
    like_fallback 把"民事诉讼法"误命到"民事诉讼法解释"。判定规则：
        - 名称交集非空 → ``True``，否则 ``False``;
        - 不校验 source_name / 日期。

    本函数是纯函数：不读 DB、不抛异常。
    """

    if row is None or payload is None:
        return False

    if strict:
        existing_source = _read(row, "source_name")
        incoming_source = _read(payload, "source_name")
        existing_source_s = (
            existing_source.strip() if isinstance(existing_source, str) else ""
        )
        incoming_source_s = (
            incoming_source.strip() if isinstance(incoming_source, str) else ""
        )
        if (
            existing_source_s
            and incoming_source_s
            and existing_source_s != incoming_source_s
        ):
            return False
        if not _same_version_dates(row, payload):
            return False

    return bool(_names_from(row) & _names_from(payload))
