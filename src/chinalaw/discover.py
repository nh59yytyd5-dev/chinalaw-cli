"""按状态/关键词批量列出候选法规（不下载、不入库）。

discover 是 fetch 的"探测前哨"：agent 不知道具体法规名时，可先用
status / keyword 过滤拉一批候选，再用 ``fetch --prefer-id <id>`` 精取
某一部。

设计目标（与 ``fetch.fetch_law(..., list_matches=True)`` 的边界）：

- ``fetch`` 需要必填 ``name``，必返回**单一**最佳匹配（或抛 ambiguous）。
- ``discover`` 不需要 ``name``，不抛 ambiguous，纯候选流。

``flk_npc`` 原生支持完整 ``status`` 过滤。国家行政法规库、NFRA、证监会 /
交易所 / 中证登 /
协会公开页只有当前公开页语义，允许 ``--status current`` 作为 agent 友好过滤，
其它 status fail loud。

公开行为见 ``docs/CONTRACT.md`` §4.11.1。
"""

from __future__ import annotations

from chinalaw.sources import (
    CURRENT_ONLY_STATUS_SOURCES,
    STATUS_FILTER_SUPPORTED,
    _candidate_from_row,
    _row_id,
    get_source_adapter,
    status_to_sxx,
)

# discover 支持具备站内搜索/列表意义的源。court_gongbao / court_main /
# spp_gov_cn 当前没有稳定"按关键词列候选池"语义，保留 fetch --list-matches。
DISCOVER_SOURCES: tuple[str, ...] = (
    "flk_npc",
    "gov_xzfgk",
    "nfra_gov_cn",
    "csrc_gov_cn",
    "bse_cn",
    "sse_com_cn",
    "szse_cn",
    "chinaclear_cn",
    "sac_net_cn",
)


def discover_laws(
    *,
    source: str = "flk_npc",
    query: str | None = None,
    status: str | None = None,
    limit: int = 20,
) -> dict:
    """按 status / 关键词列出 source 候选。

    Args:
        source: 数据源；目前支持 ``flk_npc``、``gov_xzfgk``、``nfra_gov_cn``
            与证券规则类公开源。
        query: 标题子串过滤；空字符串 / None 表示返回当页全部条目（按 flk
            站点默认 gbrq DESC 顺序）。
        status: 状态过滤关键字。``flk_npc`` 支持完整枚举；``gov_xzfgk`` /
            证券规则类公开源仅接受 ``current``。
        limit: 候选上限（默认 20）。

    Returns:
        ``{"kind": "law_discover_candidates", "source": ..., "query": ...,
        "status": ..., "candidates": [...]}``，candidates 形态与
        ``fetch --list-matches`` 一致（通过
        :func:`chinalaw.sources._candidate_from_row`）。

    Raises:
        ValueError: 未支持的 source / status；或 status 非合法关键字。
    """

    normalized = (source or "").strip().lower().replace("-", "_")
    if normalized not in DISCOVER_SOURCES:
        raise ValueError(
            f"discover does not support source {source!r}; "
            f"supported: {list(DISCOVER_SOURCES)}"
        )

    if (
        status is not None
        and normalized not in STATUS_FILTER_SUPPORTED
        and not (normalized in CURRENT_ONLY_STATUS_SOURCES and status == "current")
    ):
        supported = STATUS_FILTER_SUPPORTED | CURRENT_ONLY_STATUS_SOURCES
        raise ValueError(
            f"--status filter is not supported by source {normalized!r}; "
            f"supported sources: {sorted(supported)}"
        )

    adapter = get_source_adapter(normalized)
    page_size = max(int(limit), 1)
    search_kwargs: dict = {"page_size": page_size}
    if normalized in STATUS_FILTER_SUPPORTED:
        search_kwargs.update({"order": "gbrq", "sort": "DESC"})
    if status is not None and normalized in STATUS_FILTER_SUPPORTED:
        search_kwargs["sxx"] = [status_to_sxx(status)]

    search_result = adapter.search_list(query or "", **search_kwargs)
    rows = (search_result.get("rows") or [])[:page_size]
    candidates = [_candidate_from_row(row) for row in rows if _row_id(row)]

    return {
        "kind": "law_discover_candidates",
        "source": normalized,
        "query": query or "",
        "status": status,
        "candidates": candidates,
    }
