"""数据源编排入口。

各 adapter 通过 ``ADAPTER_REGISTRY`` 注册（ADR-0008 §3.1）；新源接入只需把
adapter 实例加入注册表，CLI ``probe`` / ``verify-source`` 等命令自动支持。

行 / 候选项的 "主键" 抽象（ADR-0008 §3.2）：

flk_npc 用 ``bbbs`` 字段标识一条法规；court_gongbao / court_main /
spp_gov_cn 用 ``detail_id``。本模块通过 :func:`_row_id` 在不同 row 风格
之间统一识别 id，避免 verify-source 等命令对单个源的字段绑死。返回的候选项
同时附带 ``id``（通用主键）与 ``bbbs``（向后兼容 flk 的字段名，非 FLK 源
也填充以便老调用方继续工作）。
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from html import unescape
from urllib.error import URLError

from chinalaw.adapters import (
    bse_cn,
    chinaclear_cn,
    court_gongbao,
    court_main,
    csrc_gov_cn,
    flk_npc,
    gov_xzfgk,
    nfra_gov_cn,
    sac_net_cn,
    spp_gov_cn,
    sse_com_cn,
    szse_cn,
)
from chinalaw.service import normalize_article_number

_HTML_TAG_RE = re.compile(r"<[^>]+>")


# 多源 adapter 注册表。Key 是 source name（CLI ``--source`` 接受的值，规整为
# 小写下划线形式后比对）；value 是 adapter 实例。
#
# 新源接入 checklist（ADR-0008 §3.2）：
# 1. 在 ``src/chinalaw/adapters/`` 下创建 adapter 模块，至少实装 ``probe()``
# 2. 在本表注册 ``default_adapter`` 实例
# 3. 在 ADR / 调研报告里说明覆盖范围与暂缓字段
ADAPTER_REGISTRY = {
    "flk_npc": flk_npc.default_adapter,
    "court_gongbao": court_gongbao.default_adapter,
    "court_main": court_main.default_adapter,
    "csrc_gov_cn": csrc_gov_cn.default_adapter,
    "gov_xzfgk": gov_xzfgk.default_adapter,
    "nfra_gov_cn": nfra_gov_cn.default_adapter,
    "bse_cn": bse_cn.default_adapter,
    "spp_gov_cn": spp_gov_cn.default_adapter,
    "sse_com_cn": sse_com_cn.default_adapter,
    "szse_cn": szse_cn.default_adapter,
    "chinaclear_cn": chinaclear_cn.default_adapter,
    "sac_net_cn": sac_net_cn.default_adapter,
}

# 支持 verify-source 的源（adapter 必须同时实装 search_list / build_law_payload）。
# CLI 的 verify-source ``source`` choices 也由本集合驱动。
VERIFIABLE_SOURCES = (
    "flk_npc",
    "court_gongbao",
    "court_main",
    "spp_gov_cn",
    "csrc_gov_cn",
    "gov_xzfgk",
    "nfra_gov_cn",
    "bse_cn",
    "sse_com_cn",
    "szse_cn",
    "chinaclear_cn",
    "sac_net_cn",
)

# CLI ``--status`` flag → flk_npc adapter ``sxx`` int 映射。
#
# CLI 层（``cli.py``）暴露 string keyword 给 agent，sources 这层做
# string→int 变换并由 fetch / discover 透传到 adapter。仅 ``flk_npc`` 原生支
# 持此过滤维度（详见 ``docs/CLI_STATUS_FLAG_SPEC.md`` §1.1 多源对照矩阵）；
# gov_xzfgk / 证券公开源仅接受 current；court_gongbao / court_main /
# spp_gov_cn 站点本身没有 status 维度，CLI 层在传入其它 status 时抛
# ``ValueError`` fail loud（同 spec §2 方向 X）。
#
# 反向 ``SXX_TO_STATUS`` 在 ``cleaning.py:50`` 是权威定义；本表通过 dict
# comprehension 反向派生，单点维护——未来 flk 加新 sxx 值时只改
# ``SXX_TO_STATUS`` 一处，正反两路自动同步。
STATUS_TO_SXX: dict[str, int] = {
    status: sxx for sxx, status in flk_npc.SXX_TO_STATUS.items()
}

# 已知支持 ``--status`` 过滤的源集合。CLI / fetch / discover 层用此判断
# 是否 fail loud。frozenset 单点常量便于未来扩源时单点添加。
STATUS_FILTER_SUPPORTED: frozenset[str] = frozenset({"flk_npc"})

# 部分来源没有历史 / 废止枚举，但其搜索入口只暴露当前公开规范。允许
# ``--status current`` 作为 agent 友好过滤；其它 status 仍 fail loud。
CURRENT_ONLY_STATUS_SOURCES: frozenset[str] = frozenset(
    {
        "csrc_gov_cn",
        "gov_xzfgk",
        "nfra_gov_cn",
        "bse_cn",
        "sse_com_cn",
        "szse_cn",
        "chinaclear_cn",
        "sac_net_cn",
    }
)


def status_to_sxx(status: str) -> int:
    """把 CLI ``--status`` keyword 翻译成 flk_npc adapter 的 ``sxx`` int。

    Raises:
        ValueError: ``status`` 不在 :data:`STATUS_TO_SXX` 的合法 key 集合时。
    """

    if status not in STATUS_TO_SXX:
        known = ", ".join(sorted(STATUS_TO_SXX))
        raise ValueError(
            f"unknown status {status!r}; expected one of: {known}"
        )
    return STATUS_TO_SXX[status]


def _normalize_source_name(name: str) -> str:
    return name.strip().lower().replace("-", "_")


def _row_id(row: dict | None) -> str | None:
    """统一抽取 row 的主键。

    优先级：``bbbs``（flk_npc）→ ``detail_id``（HTML 源）→ ``id``。
    """

    if not row:
        return None
    for key in ("bbbs", "detail_id", "id"):
        value = row.get(key)
        if value:
            return str(value)
    return None


def get_source_adapter(name: str):
    normalized = _normalize_source_name(name)
    if normalized not in ADAPTER_REGISTRY:
        known = ", ".join(sorted(ADAPTER_REGISTRY))
        raise ValueError(f"unknown source: {name} (known: {known})")
    return ADAPTER_REGISTRY[normalized]


def probe_source(name: str) -> dict:
    return get_source_adapter(name).probe()


def verify_source(
    name: str,
    *,
    query: str = "中华人民共和国民法典",
    article: str | None = "第一条",
    limit: int = 5,
) -> dict:
    """对真实数据源做只读 smoke verify：probe → search → fetch/clean → article locate。

    该函数不写 DB、不写 fixture。它用于发现上游页面/API/docx 模板是否变化，
    不能替代单元测试，也不应在默认 CI 中强制联网运行。
    """

    source_name = name.strip().lower().replace("-", "_")
    checked_at = datetime.now(timezone.utc).isoformat()
    steps: list[dict] = []
    candidates: list[dict] = []
    selected: dict | None = None
    law_summary: dict | None = None
    article_match: dict | None = None

    def add_step(step: str, ok: bool, message: str, **data) -> None:
        item = {"step": step, "ok": ok, "message": message}
        if data:
            item["data"] = data
        steps.append(item)

    def finish(ok: bool) -> dict:
        return {
            "kind": "source_verify",
            "source": source_name,
            "ok": ok,
            "query": query,
            "article": article,
            "limit": limit,
            "checked_at": checked_at,
            "steps": steps,
            "candidates": candidates,
            "selected": selected,
            "law": law_summary,
            "article_match": article_match,
        }

    try:
        adapter = get_source_adapter(source_name)
    except ValueError as exc:
        add_step("adapter", False, f"failed to initialize source adapter: {exc}")
        return finish(False)

    try:
        probe = adapter.probe()
    except (URLError, OSError, TimeoutError) as exc:
        add_step("probe", False, f"probe failed: {exc}")
        return finish(False)
    add_step(
        "probe",
        True,
        "homepage probe succeeded",
        status_code=probe.get("status_code"),
        page_shape=probe.get("page_shape"),
    )

    try:
        search_result = adapter.search_list(query, page_size=max(limit, 1))
    except (URLError, OSError, TimeoutError, ValueError, KeyError) as exc:
        add_step("search", False, f"search failed: {exc}")
        return finish(False)

    rows = (search_result.get("rows") or [])[: max(limit, 1)]
    candidates = [_candidate_from_row(row) for row in rows if _row_id(row)]
    if not candidates:
        add_step("search", False, "search returned no candidates")
        return finish(False)
    add_step("search", True, f"search returned {len(candidates)} candidate(s)")

    selected = _select_verify_candidate(candidates, query)
    selected_id = selected.get("id") or selected.get("bbbs")
    chosen_row = next(
        (row for row in rows if _row_id(row) == selected_id),
        None,
    )
    add_step(
        "select",
        True,
        f"selected candidate {selected.get('title')}",
        id=selected_id,
        bbbs=selected.get("bbbs"),
    )

    try:
        payload = adapter.build_law_payload(selected_id, search_row=chosen_row)
    except (URLError, OSError, TimeoutError, ValueError, KeyError) as exc:
        add_step("fetch_clean", False, f"fetch/clean failed: {exc}")
        return finish(False)

    articles = payload.get("articles") or []
    law_summary = {
        "id": payload.get("id"),
        "title": payload.get("title"),
        "short_title": payload.get("short_title"),
        "level": payload.get("level"),
        "status": payload.get("status"),
        "source_url": payload.get("source_url"),
        "source_hash": payload.get("source_hash"),
        "source_checked_at": payload.get("source_checked_at"),
        "article_count": len(articles),
    }
    if not articles:
        add_step("fetch_clean", False, "cleaned payload contains no articles")
        return finish(False)
    add_step("fetch_clean", True, f"cleaned payload has {len(articles)} article(s)")

    if article:
        article_match = _find_article(articles, article)
        if article_match is None:
            add_step("article", False, f"article {article} not found in cleaned payload")
            return finish(False)
        add_step(
            "article",
            True,
            f"article {article_match.get('number_display') or article_match.get('number')} found",
        )

    return finish(True)


def _candidate_from_row(row: dict) -> dict:
    row_id = _row_id(row)
    return {
        "id": row_id,
        "bbbs": row.get("bbbs") or row_id,  # 兼容老调用方（flk 风格）
        "detail_id": row.get("detail_id") or row_id,
        "title": _clean_title(row.get("title")),
        "released_at": row.get("gbrq") or row.get("released_at") or row.get("issue") or "",
        "status": _status_from_row(row),
    }


def _status_from_row(row: dict) -> str:
    """通用 status 提取：flk 用 ``sxx`` int，其它源直接用 ``status`` 字符串。

    优先级：``sxx`` > ``status``。FLK 的 ``sxx`` 是数值化生效状态语义最强的
    表达，遇到既有 ``status`` 文本兜底。``fetch.py`` 亦从此模块 import 此
    helper 作为权威；曾经存在的 ``fetch._normalize_row_status`` 已收口删除
    （详见 docs/ADAPTER_HTML_HELPERS_SPEC.md §2.3）。
    """

    if "sxx" in row:
        try:
            return flk_npc.SXX_TO_STATUS.get(int(row.get("sxx")), "unknown")
        except (TypeError, ValueError):
            return "unknown"
    explicit = row.get("status")
    return str(explicit) if explicit else "unknown"


def _clean_title(raw: str | None) -> str:
    if not raw:
        return ""
    return unescape(_HTML_TAG_RE.sub("", raw)).strip()


def _select_verify_candidate(candidates: list[dict], query: str) -> dict:
    exact = [candidate for candidate in candidates if candidate.get("title") == query]
    if exact:
        current = [candidate for candidate in exact if candidate.get("status") == "current"]
        return (current or exact)[0]
    current = [candidate for candidate in candidates if candidate.get("status") == "current"]
    return (current or candidates)[0]


def _find_article(articles: list[dict], requested: str) -> dict | None:
    target = normalize_article_number(requested)
    for article in articles:
        if normalize_article_number(article.get("number") or "") != target:
            continue
        text = (article.get("text") or "").strip()
        return {
            "number": article.get("number"),
            "number_display": article.get("number_display"),
            "part": article.get("part"),
            "title": article.get("title"),
            "text_preview": text[:120],
        }
    return None
