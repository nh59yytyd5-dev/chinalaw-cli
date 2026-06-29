"""按需爬取 + 清洗 + 入库的协议级 high-level wrapper。

设计参见 docs/decisions/ADR-0006-fetch-command.md。

与 sync.py 的边界：
- sync 是维护者批量 / 增量工具，参数面大、不在协议（ADR-0002）。
- fetch 是 agent / 个人按需工具，参数面小且只暴露高层动作，列入协议（CONTRACT.md §4）。

fetch 在 sync 之上做了三件事：
1. 搜索 + 选最佳匹配（避免 agent 摸索 bbbs）。
2. 暴露 dry-run / to-fixture / list-matches 三种"非入库"模式。
3. --article 定位单条返回（仍随完整法律一起入库）。
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from chinalaw.alias_agent import (
    AliasAgentRecoverableError,
    derive_aliases,
)
from chinalaw.aliases import append_unique, merge_law_aliases
from chinalaw.cleaning import CLEANING_SCHEMA_VERSION
from chinalaw.db import connect, migrate, set_meta
from chinalaw.document_numbers import (
    index_document_number as _index_document_number,
)
from chinalaw.document_numbers import (
    infer_source_id,
)
from chinalaw.document_numbers import (
    looks_like_document_number as _looks_like_document_number,
)
from chinalaw.document_numbers import (
    normalize_document_number as _normalize_document_number,
)
from chinalaw.identity import law_row_matches_payload
from chinalaw.loader import load_law_from_dict
from chinalaw.service import _resolve_law_row, normalize_article_number
from chinalaw.sources import (
    CURRENT_ONLY_STATUS_SOURCES,
    STATUS_FILTER_SUPPORTED,
    _candidate_from_row,
    _clean_title,  # noqa: F401  # re-export: 既有 import 路径 + 测试 attribute
    _row_id,
    get_source_adapter,
    status_to_sxx,
)

FETCH_SOURCES = (
    "flk_npc",
    "court_gongbao",
    "court_main",
    "gov_xzfgk",
    "nfra_gov_cn",
    "spp_gov_cn",
    "csrc_gov_cn",
    "bse_cn",
    "sse_com_cn",
    "szse_cn",
    "chinaclear_cn",
    "sac_net_cn",
)

QUERY_FALLBACK_SOURCES = {"court_main", "court_gongbao", "spp_gov_cn"}
DEEP_SEARCH_MAX_PAGES = {
    # 公报站司法解释 + 司法文件约 30 页级别；31 覆盖现有 sfjs 全量。
    # 这是有界深搜，不做无界爬取。
    "court_gongbao": 31,
    # 最高检静态栏目页通常按年倒排，刑事规则/意见类目标在前 5 页内。
    "spp_gov_cn": 5,
}

# 多源 row.source_name 标准值；用于 ``_resolve_local_fetch_hint`` 校验本地命中
# 的 row 与请求源一致，避免「俗称模糊命中跨源 row」时把别源的 source_id 喂给
# 当前 source 的 fetch 主流程。多源对称化前 fetch.py 单走 flk_npc，详见
# ``docs/SYMMETRIC_LOCAL_FETCH_HINT_SPEC.md``。
SOURCE_NAME_MARKERS: dict[str, str] = {
    "flk_npc": "flk.npc.gov.cn",
    "court_gongbao": "gongbao.court.gov.cn",
    "court_main": "www.court.gov.cn",
    "gov_xzfgk": "xzfg.moj.gov.cn",
    "nfra_gov_cn": "www.nfra.gov.cn",
    "spp_gov_cn": "spp.gov.cn",
    "csrc_gov_cn": "www.csrc.gov.cn",
    "bse_cn": "www.bse.cn",
    "sse_com_cn": "www.sse.com.cn",
    "szse_cn": "www.szse.cn",
    "chinaclear_cn": "www.chinaclear.cn",
    "sac_net_cn": "www.sac.net.cn",
}

# A small number of adapters intentionally consume multiple official hosts
# under one source key. Keep the primary marker above stable for existing tests
# and allow aliases only in the local-hint consistency check.
SOURCE_NAME_MARKER_ALIASES: dict[str, tuple[str, ...]] = {
    "gov_xzfgk": ("www.gov.cn",),
}


class FetchError(Exception):
    """fetch 命令的错误基类。子类的 exit_code 用于 CLI 层映射退出码。"""

    exit_code: int = 2


class FetchNotFoundError(FetchError):
    """业务级 not found：搜索零结果，或 --article 指定的条款不在结果中。"""

    exit_code = 1


class FetchAmbiguousError(FetchError):
    """多条候选无最佳匹配，且用户未通过 --prefer-bbbs 指定。"""

    exit_code = 2

    def __init__(self, message: str, candidates: list[dict] | None = None):
        super().__init__(message)
        self.candidates = candidates or []


class FetchSourceError(FetchError):
    """数据源故障（网络 / HTTP 错误 / 站点结构变化等）。"""

    exit_code = 2


class FetchActionConflictError(FetchError):
    """``--dry-run`` / ``--to-fixture`` / ``--list-matches`` 三选一被同时传入。

    CONTRACT.md §4.11 / ADR-0006 §3 要求三种动作互斥：
    传 ``--list-matches`` 即不下载、不入库；传 ``--to-fixture`` 即写文件不入库；
    传 ``--dry-run`` 即既不写文件也不入库；都不传则入库。CLI 用 argparse
    mutually exclusive group 兜底，library 层（SDK 直接调用）由本类兜底。
    """

    exit_code = 2


# C901: 已知复杂（McCabe 27），fetch 主编排（候选→清洗→入库→文号索引）；列为待拆分
# 技术债，见 docs/decisions/ADR-0009-module-boundaries.md。
def fetch_law(  # noqa: C901
    db_path: Path | str,
    name: str,
    *,
    source: str = "flk_npc",
    article: str | None = None,
    dry_run: bool = False,
    to_fixture: Path | str | None = None,
    list_matches: bool = False,
    prefer_bbbs: str | None = None,
    limit: int = 5,
    force: bool = False,
    status: str | None = None,
) -> dict:
    """按法律名一条龙完成"取条文 + 清洗 + 入库"。

    返回 JSON schema 见 ADR-0006 §3。失败抛 FetchError 子类，由 CLI 层
    捕获并映射 exit code。
    """

    name_clean = (name or "").strip()
    if not name_clean:
        raise ValueError("law name is required")

    # 动作互斥：dry_run / to_fixture / list_matches 同时只能有一个为 truthy。
    # 见 ADR-0006 §3 + CONTRACT.md §4.11。CLI 层 argparse mutually-exclusive
    # group 已先做拦截；这里再防一道，让 SDK 直接调用也报同样的错。
    active_actions = []
    if list_matches:
        active_actions.append("list_matches")
    if to_fixture is not None:
        active_actions.append("to_fixture")
    if dry_run:
        active_actions.append("dry_run")
    if len(active_actions) > 1:
        raise FetchActionConflictError(
            "fetch actions are mutually exclusive: "
            f"{', '.join(active_actions)} cannot be combined"
        )

    normalized_source = (source or "").strip().lower().replace("-", "_")
    if normalized_source not in FETCH_SOURCES:
        raise ValueError(f"unsupported source for fetch: {source}")

    # CLI ``--status`` 仅对有明确上游状态语义的源生效。flk_npc 支持完整
    # sxx 四态；csrc_gov_cn 仅接受 current 作为"当前公开页"过滤。其它源
    # 传入 ``status`` 抛 ``ValueError`` fail loud——避免 agent 误以为
    # "筛了 repealed 候选"但其实 HTML 源没这个维度（同 spec §2 方向 X）。
    sxx_filter: list[int] | None = None
    if status is not None:
        if (
            normalized_source not in STATUS_FILTER_SUPPORTED
            and not (
                normalized_source in CURRENT_ONLY_STATUS_SOURCES
                and status == "current"
            )
        ):
            supported = STATUS_FILTER_SUPPORTED | CURRENT_ONLY_STATUS_SOURCES
            raise ValueError(
                f"--status filter is not supported by source "
                f"{normalized_source!r}; supported sources: "
                f"{sorted(supported)}"
            )
        if normalized_source in STATUS_FILTER_SUPPORTED:
            sxx_filter = [status_to_sxx(status)]

    adapter = get_source_adapter(normalized_source)

    # ``--status`` is a remote search filter. Do not let implicit local alias /
    # document-number hints short-circuit it, because a local current row can
    # share title/alias with an older amended or repealed FLK version.
    local_hint = None
    if prefer_bbbs is None and not list_matches and status is None:
        local_hint = _resolve_local_fetch_hint(db_path, name_clean, normalized_source)

    # 文号反查：``chinalaw fetch "法释〔2023〕13号"`` 直接命中
    # ``document_number_index`` 表，绕过远程标题搜索（公报站没有 ``q=`` 参数，
    # 否则要靠跨页 substring 过滤，成本高且对版本号 / 序号差异敏感）。
    doc_no_hint = None
    if (
        prefer_bbbs is None
        and not list_matches
        and status is None
        and local_hint is None
        and _looks_like_document_number(name_clean)
    ):
        doc_no_hint = _lookup_document_number_hint(
            db_path, name_clean, normalized_source
        )

    direct_hint = None
    if (
        prefer_bbbs
        and normalized_source
        in (
            "court_gongbao",
            "court_main",
            "gov_xzfgk",
            "nfra_gov_cn",
            "spp_gov_cn",
            "csrc_gov_cn",
            "bse_cn",
            "sse_com_cn",
            "szse_cn",
            "chinaclear_cn",
            "sac_net_cn",
        )
        and not list_matches
    ):
        direct_hint = _direct_id_candidate(prefer_bbbs, title_hint=name_clean)

    # 1. 搜索候选。若本地 alias 或文号索引已能解析到源主键，则直接按 id 拉取，
    # 避免 agent 输入 "合通解释" / "法释〔2023〕13号" 时被远程候选歧义干扰。
    page_size = max(limit, 1)
    if local_hint or direct_hint or doc_no_hint:
        rows = []
        candidates = [local_hint or direct_hint or doc_no_hint]
    else:
        # 仅 flk_npc + ``status`` 非空时透传 sxx；csrc_gov_cn 的 current
        # 过滤不需要传私有编码，其它路径不动 search_kwargs。
        search_kwargs: dict = {"page_size": page_size}
        if sxx_filter is not None:
            search_kwargs["sxx"] = sxx_filter
        try:
            rows, candidates = _search_candidate_rows(
                adapter,
                normalized_source,
                name_clean,
                page_size=page_size,
                search_kwargs=search_kwargs,
            )
        except Exception as exc:
            raise FetchSourceError(f"search failed: {exc}") from exc

    if not candidates:
        raise FetchNotFoundError(
            f"no results for name={name_clean!r} from source {normalized_source}"
        )

    # 2. list-matches 模式：不下载，直接返回候选
    if list_matches:
        return {
            "kind": "law_fetch_candidates",
            "source": normalized_source,
            "name": name_clean,
            "candidates": candidates,
        }

    # 3. 选最佳匹配
    chosen = _choose_best(candidates, name_clean, prefer_bbbs)
    if chosen is None:
        raise FetchAmbiguousError(
            f"multiple candidates matched name={name_clean!r}; "
            "specify --prefer-id/--prefer-bbbs from candidates",
            candidates=candidates,
        )

    chosen_id = chosen["id"]
    chosen_row = next((row for row in rows if _row_id(row) == chosen_id), None)

    # 4. 拉完整 payload（adapter 取数，cleaning 层生成 canonical payload）
    try:
        payload = adapter.build_law_payload(chosen_id, search_row=chosen_row)
    except Exception as exc:
        raise FetchSourceError(
            f"build_law_payload failed for id={chosen_id}: {exc}"
        ) from exc

    # 4.5 canonical id：在所有出口（dry_run / to_fixture / 入库 / response.law）
    # 之前统一替换 raw bbbs 为 stable id（如 ``flk-civil-code-2020``）。
    # 优先读取目标 fixture 的既有 id，其次读取已存在 DB；dry-run / to-fixture
    # 不为了 canonical lookup 创建新 DB 文件。
    canonical_id = _resolve_output_canonical_id(
        db_path,
        payload,
        to_fixture=to_fixture,
        create_db_if_missing=not (dry_run or to_fixture is not None),
    )
    if canonical_id and canonical_id != payload.get("id"):
        payload = {**payload, "id": canonical_id}
    payload = _maybe_enrich_aliases(payload)
    storage_payload = _strip_transient_fetch_metadata(payload)

    article_count = len(payload.get("articles") or [])

    # 5. 输出动作（互斥：dry_run / to_fixture / 默认入库）
    wrote_fixture = None
    loaded = False
    skipped = False

    if to_fixture is not None:
        fixture_path = Path(to_fixture)
        try:
            if fixture_path.parent and not fixture_path.parent.exists():
                fixture_path.parent.mkdir(parents=True, exist_ok=True)
            fixture_path.write_text(
                json.dumps(storage_payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as exc:
            raise FetchError(f"failed to write fixture {fixture_path}: {exc}") from exc
        wrote_fixture = str(fixture_path)
    elif not dry_run:
        loaded, skipped, article_count = _persist(
            db_path,
            storage_payload,
            normalized_source,
            name_clean,
            source_id=chosen_id,
            force=force,
        )

    # 6. 定位 article（仅在响应中返回；入库的还是整部）
    article_obj = None
    if article:
        article_obj = _locate_article(payload, article)
        if article_obj is None:
            raise FetchNotFoundError(
                f"article {article!r} not found in {payload.get('title') or chosen['title']}"
            )

    return {
        "kind": "law_fetch",
        "source": normalized_source,
        "name": name_clean,
        "matched_id": chosen_id,
        "matched_bbbs": chosen.get("bbbs") or chosen_id,
        "matched_detail_id": chosen.get("detail_id"),
        "matched_title": (
            payload.get("title") if chosen.get("direct_id") else None
        ) or chosen["title"],
        "candidates": candidates,
        "law": payload,
        "article": article_obj,
        "article_count": article_count,
        "loaded": loaded,
        "skipped": skipped,
        "dry_run": bool(dry_run),
        "force": bool(force),
        "wrote_fixture": wrote_fixture,
    }


def _search_candidate_rows(
    adapter,
    source: str,
    query: str,
    *,
    page_size: int,
    search_kwargs: dict,
) -> tuple[list[dict], list[dict]]:
    """Search a source and return raw rows plus protocol candidates.

    HTML/list-based sources do not all have real full-site search. The fallback
    is deliberately bounded:

    - ``court_main`` first-page search may fail on overly formal long titles,
      so retry normalized title variants such as stripping ``（试行）`` and
      normalizing joint-issuer separators. The variants must not drop issuing
      bodies; otherwise a non-existent joint-issuer title can silently match a
      single-issuer document.
    - ``court_gongbao`` / ``spp_gov_cn`` static lists can miss older items on
      page 1, so use adapter-level ``cross_search`` with source-specific page
      caps. The caps are constants above; no unbounded crawling.
    """

    queries = (
        _search_query_variants(query)
        if source in QUERY_FALLBACK_SOURCES
        else [query]
    )
    for candidate_query in queries:
        search_result = adapter.search_list(candidate_query, **search_kwargs)
        rows = (search_result.get("rows") or [])[:page_size]
        candidates = [_candidate_from_row(row) for row in rows if _row_id(row)]
        if candidates:
            return rows, candidates

    if source in DEEP_SEARCH_MAX_PAGES and hasattr(adapter, "cross_search"):
        max_pages = DEEP_SEARCH_MAX_PAGES[source]
        if source == "court_gongbao":
            cross = adapter.cross_search(
                query,
                max_pages_per_serial=max_pages,
            )
        else:
            cross = adapter.cross_search(
                query,
                max_pages_per_channel=max_pages,
            )
        rows = (cross.get("rows") or [])[:page_size]
        candidates = [_candidate_from_row(row) for row in rows if _row_id(row)]
        if candidates:
            return rows, candidates

    return [], []


def _search_query_variants(query: str) -> list[str]:
    """Generate conservative title-search fallbacks for official sites.

    This is not a law-name whitelist. It covers common normative-title shapes:
    joint issuer spacing variants, trailing ``（试行）`` / ``（暂行）`` status
    markers. It intentionally does not strip to the ``关于...`` core, because
    that loses issuer identity and can turn a missing joint-issuer document into
    a false positive.
    """

    variants: list[str] = []

    def add(value: str | None) -> None:
        clean = (value or "").strip()
        if clean and clean not in variants:
            variants.append(clean)

    original = (query or "").strip()
    add(original)

    no_status = _strip_trial_status_suffix(original)
    add(no_status)

    for value in list(variants):
        compact_issuer = value.replace("最高人民法院 最高人民检察院", "最高人民法院最高人民检察院")
        add(compact_issuer)
        punct_issuer = value.replace("最高人民法院 最高人民检察院", "最高人民法院、最高人民检察院")
        add(punct_issuer)

    return variants


_TRIAL_STATUS_SUFFIXES = ("（试行）", "(试行)", "（暂行）", "(暂行)")


def _strip_trial_status_suffix(query: str) -> str:
    for suffix in _TRIAL_STATUS_SUFFIXES:
        if query.endswith(suffix):
            return query[: -len(suffix)].strip()
    return query


def _persist(
    db_path: Path | str,
    payload: dict,
    source: str,
    query: str,
    *,
    source_id: str | None = None,
    force: bool = False,
) -> tuple[bool, bool, int]:
    """upsert payload 到 DB，相同 source_hash 跳过。返回 (loaded, skipped, article_count)。

    payload 的 ``id`` 已在 ``fetch_law`` 主流程里被 canonical 化（参见
    ``_try_resolve_canonical_id``），这里直接信任传入 id。

    入库时同步把 ``payload.document_number`` 写入 ``document_number_index``，
    让后续 ``chinalaw fetch "法释〔2023〕13号"`` 可以直接命中本地索引（绕过远程
    标题搜索）。``source_id`` 是当前源的主键（court_gongbao 的 detail_id /
    flk 的 bbbs），跳过缓存也照样写索引——已存在就 upsert。
    """

    with connect(db_path) as conn:
        migrate(conn)

        existing = conn.execute(
            "SELECT source_hash FROM laws WHERE id = ?", (payload["id"],)
        ).fetchone()
        if (
            existing
            and existing["source_hash"] == payload.get("source_hash")
            and not force
        ):
            article_count = conn.execute(
                "SELECT COUNT(*) FROM articles WHERE law_id = ?", (payload["id"],)
            ).fetchone()[0]
            # 即使跳过 upsert 也确保文号索引存在（适配 v0.2 之前入库的旧 row）
            _index_document_number(conn, payload, source, source_id)
            return False, True, article_count

        article_count = load_law_from_dict(conn, payload)
        checked_at = datetime.now(timezone.utc).isoformat()
        set_meta(conn, "last_sync_at", checked_at)
        set_meta(conn, f"source:{source}:last_sync_at", checked_at)
        set_meta(conn, f"source:{source}:last_mode", "fetch")
        set_meta(conn, f"source:{source}:last_query", query)
        set_meta(conn, "cleaning_schema_version", str(CLEANING_SCHEMA_VERSION))
        _index_document_number(conn, payload, source, source_id)
        return True, False, article_count


_ALIAS_AGENT_ENV = "CHINALAW_USE_ALIAS_AGENT"


def _maybe_enrich_aliases(payload: dict) -> dict:
    """Return payload with deterministic aliases (always) + optional
    LLM-derived aliases (opt-in via env var).

    LLM 路径默认关闭：fetch 主流程不调 ``alias_agent.derive_aliases``，
    确定性派生（``aliases.merge_law_aliases``）已足够覆盖大多数场景。需要
    LLM 增强时显式设置环境变量 ``CHINALAW_USE_ALIAS_AGENT=1``。

    Opt-in 时的错误处理（分级）：
    - ``AliasAgentRecoverableError`` → 写入 ``warnings`` 字段，不挂主流程；
    - 其他 ``Exception`` → 不捕获，让 fetch 主流程感知 bug。

    详见 ``docs/FETCH_LAYER_SPEC.md`` §3。
    """

    title = payload.get("title")
    short_title = payload.get("short_title")
    aliases = merge_law_aliases(
        title, short_title, list(payload.get("aliases") or [])
    )

    if not os.environ.get(_ALIAS_AGENT_ENV):
        return _maybe_replace_aliases(payload, aliases)

    warnings: list[dict] = []
    try:
        for alias in derive_aliases(str(title or "")):
            append_unique(aliases, alias)
    except AliasAgentRecoverableError as exc:
        warnings.append(
            {
                "severity": "warning",
                "code": "alias_agent_skipped",
                "reason": exc.reason,
                "message": str(exc),
            }
        )

    enriched = _maybe_replace_aliases(payload, aliases)
    if warnings:
        existing = list(enriched.get("warnings") or [])
        existing.extend(warnings)
        enriched = {**enriched, "warnings": existing}
    return enriched


def _maybe_replace_aliases(payload: dict, aliases: list[str]) -> dict:
    if aliases == payload.get("aliases"):
        return payload
    return {**payload, "aliases": aliases}


_TRANSIENT_FETCH_PAYLOAD_KEYS = {"warnings"}


def _strip_transient_fetch_metadata(payload: dict) -> dict:
    """Remove fetch-runtime diagnostics before writing canonical law data.

    ``payload`` is also used as ``response.law`` where runtime warnings are
    useful. Fixture / DB writes are canonical data paths, so transient fetch
    diagnostics (currently alias_agent warnings) must not leak into files that
    maintainers may commit.
    """

    if not any(key in payload for key in _TRANSIENT_FETCH_PAYLOAD_KEYS):
        return payload
    return {
        key: value
        for key, value in payload.items()
        if key not in _TRANSIENT_FETCH_PAYLOAD_KEYS
    }


def _resolve_local_fetch_hint(
    db_path: Path | str,
    name: str,
    source: str,
) -> dict | None:
    """用本地 alias 解析远程源主键，减少 agent 被远程候选歧义卡住。

    多源对称：每源用 ``SOURCE_NAME_MARKERS`` 校验 row 来源一致后，复用
    ``document_numbers.infer_source_id`` 推导源主键。只读访问，不为 fetch
    预解析创建新 DB。详见 ``docs/SYMMETRIC_LOCAL_FETCH_HINT_SPEC.md``。
    """

    if source not in SOURCE_NAME_MARKERS:
        return None
    db = Path(db_path)
    if not db.exists():
        return None
    try:
        with connect(db_path) as conn:
            migrate(conn)
            row = _resolve_law_row(conn, name)
            if row is None:
                return None
            actual_source_name = (row["source_name"] or "").strip()
            expected_markers = (
                SOURCE_NAME_MARKERS[source],
                *SOURCE_NAME_MARKER_ALIASES.get(source, ()),
            )
            if actual_source_name not in expected_markers:
                return None
            payload = {
                "source_url": row["source_url"] or "",
                "source_name": actual_source_name,
                "id": row["id"] or "",
            }
            source_id = infer_source_id(payload, source)
            if not source_id:
                return None
            hint: dict = {
                "id": source_id,
                "detail_id": source_id,
                "title": row["title"],
                "released_at": row["released_at"] or "",
                "status": row["status"] or "unknown",
                "local_law_id": row["id"],
                "local_alias_resolved": True,
            }
            # FLK 的 ``bbbs`` 是历史兼容字段：fetch 主流程历史上按
            # bbbs / detail_id / id 三件套读取；FLK 路径继续保留 bbbs 写入，
            # court / spp 路径**不写**该字段，下游可用 ``hint.get("bbbs")``
            # 显式区分源风味（详见 SYMMETRIC_LOCAL_FETCH_HINT_SPEC §3.2.3 / §4）。
            if source == "flk_npc":
                hint["bbbs"] = source_id
            return hint
    except sqlite3.ProgrammingError:
        raise
    except (sqlite3.OperationalError, sqlite3.DatabaseError, OSError):
        return None


def _lookup_document_number_hint(
    db_path: Path | str,
    name: str,
    source: str,
) -> dict | None:
    """用本地 ``document_number_index`` 把文号解析到源主键。

    只在 ``name`` 看起来像文号且 DB 已存在时只读查询；不为 fetch 预解析创建
    新 DB。命中时返回与 ``_direct_id_candidate`` / ``_resolve_local_fetch_hint``
    形态一致的 hint，让 fetch_law 主流程可以绕过远程标题搜索直接按 ``source_id``
    拉取详情。

    匹配口径：精确匹配 ``(document_number, source)`` 元组。不同 source 的
    upstream id 不可互换，不能跨源复用。
    """

    if not _looks_like_document_number(name):
        return None
    db = Path(db_path)
    if not db.exists():
        return None
    document_number = _normalize_document_number(name)
    try:
        with connect(db_path) as conn:
            migrate(conn)
            row = conn.execute(
                "SELECT source_id, law_id, title FROM document_number_index "
                "WHERE document_number = ? AND source = ? LIMIT 1",
                (document_number, source),
            ).fetchone()
            if row is None:
                return None
            source_id = row["source_id"]
            if not source_id:
                return None
            return {
                "id": source_id,
                "bbbs": source_id,
                "detail_id": source_id,
                "title": row["title"] or document_number,
                "released_at": "",
                "status": "unknown",
                "local_law_id": row["law_id"],
                "document_number_resolved": document_number,
            }
    except sqlite3.ProgrammingError:
        raise
    except (sqlite3.OperationalError, sqlite3.DatabaseError, OSError):
        return None


def _direct_id_candidate(source_id: str, *, title_hint: str) -> dict:
    source_id = str(source_id).strip()
    return {
        "id": source_id,
        "bbbs": source_id,
        "detail_id": source_id,
        "title": title_hint,
        "released_at": "",
        "status": "unknown",
        "direct_id": True,
    }


def _resolve_output_canonical_id(
    db_path: Path | str,
    payload: dict,
    *,
    to_fixture: Path | str | None = None,
    create_db_if_missing: bool = False,
) -> str | None:
    """解析输出 payload 应使用的 stable id。

    顺序：
    1. 若 ``--to-fixture`` 指向既有 fixture 文件，优先保留该文件 id。
    2. 若 DB 已存在（或允许创建），从 DB 里找同名同源 row。
    3. 都失败则返回 None，让调用方保留 raw bbbs。
    """

    fixture_id = _resolve_fixture_canonical_id(to_fixture, payload)
    if fixture_id:
        return fixture_id
    return _try_resolve_canonical_id(
        db_path,
        payload,
        create_if_missing=create_db_if_missing,
    )


def _resolve_fixture_canonical_id(to_fixture: Path | str | None, payload: dict) -> str | None:
    """从既有 fixture 文件保留 stable id，供 ``--to-fixture`` clean checkout 使用。

    判定路径（OR 关系）：
    1. 既有 fixture 与 incoming payload 标题完全一致且 ``released_at`` /
       ``effective_at`` 不冲突 → 保留 id（即使 source_name 已迁移；fixture
       手工写的源标识改了 adapter 后是常态）；
    2. 否则降级到 ``identity.law_row_matches_payload(strict=True)``——同源
       同日期同名的严格匹配，避免把 FLK 修订版盖到旧版 fixture。
    """

    if to_fixture is None:
        return None
    fixture_path = Path(to_fixture)
    if not fixture_path.exists() or not fixture_path.is_file():
        return None
    try:
        existing = json.loads(fixture_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(existing, dict):
        return None
    existing_id = (existing.get("id") or "").strip()
    if not existing_id:
        return None

    existing_title = (existing.get("title") or "").strip()
    incoming_title = (payload.get("title") or "").strip()
    if (
        existing_title
        and existing_title == incoming_title
        and _fixture_dates_compatible(existing, payload)
    ):
        return existing_id

    if law_row_matches_payload(existing, payload, strict=True):
        return existing_id
    return None


def _fixture_dates_compatible(existing: dict, incoming: dict) -> bool:
    """两边的发布 / 生效日期不冲突；任一为空即不挑剔。

    专用于 ``_resolve_fixture_canonical_id`` 的标题精确匹配路径——fixture 跨
    source 迁移时允许 source_name 不同，但日期一旦显式不同就视作不同修订版。
    """

    for key in ("released_at", "effective_at"):
        existing_date = (existing.get(key) or "").strip()
        incoming_date = (incoming.get(key) or "").strip()
        if existing_date and incoming_date and existing_date != incoming_date:
            return False
    return True


def _try_resolve_canonical_id(
    db_path: Path | str,
    payload: dict,
    *,
    create_if_missing: bool = False,
) -> str | None:
    """以只读 + migrate 的方式查既有 stable id，不抛错。

    非入库动作默认不创建 DB；任何 DB 访问异常都安全降级回 raw bbbs，
    不阻塞主流程。
    """

    try:
        db = Path(db_path)
        if not create_if_missing and not db.exists():
            return None
        with connect(db_path) as conn:
            migrate(conn)
            return _resolve_canonical_id(conn, payload)
    except sqlite3.ProgrammingError:
        raise
    except (sqlite3.OperationalError, sqlite3.DatabaseError, OSError):
        return None


def _escape_like(raw: str) -> str:
    return raw.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")


def _resolve_canonical_id(conn, payload: dict) -> str | None:
    """在 DB 中查既有的同名 + 同源法律 row，返回它的 stable id；找不到返回 None。

    匹配口径：exact id / title / short_title / aliases 列表内含完全相等的 alias。
    在 SQL 命中后用 ``identity.law_row_matches_payload(strict=True)`` 做二次
    校验，统一同源 + 修订版日期判定。详见 ``docs/FETCH_LAYER_SPEC.md`` §2。
    """

    incoming_id = (payload.get("id") or "").strip()

    if incoming_id:
        row = conn.execute(
            "SELECT * FROM laws WHERE id = ?", (incoming_id,)
        ).fetchone()
        if row is not None and law_row_matches_payload(row, payload, strict=True):
            return row["id"]
        # 同 id 但 strict 拒（修订版日期 / 跨源）→ 继续走 candidate 匹配，
        # 不直接返回，避免把 FLK 修订版盖到旧版 row 上。

    candidates: list[str] = []
    title = (payload.get("title") or "").strip()
    short = (payload.get("short_title") or "").strip()
    if title:
        candidates.append(title)
    if short and short != title:
        candidates.append(short)
    for alias in payload.get("aliases") or []:
        if isinstance(alias, str):
            alias_clean = alias.strip()
            if alias_clean and alias_clean not in candidates:
                candidates.append(alias_clean)

    for ident in candidates:
        alias_pattern = "%" + _escape_like(json.dumps(ident, ensure_ascii=False)) + "%"
        row = conn.execute(
            "SELECT * FROM laws "
            "WHERE title = ? OR short_title = ? OR aliases LIKE ? ESCAPE '\\' "
            "ORDER BY CASE WHEN title = ? THEN 0 WHEN short_title = ? THEN 1 ELSE 2 END "
            "LIMIT 1",
            (ident, ident, alias_pattern, ident, ident),
        ).fetchone()
        if row is None:
            continue
        if not law_row_matches_payload(row, payload, strict=True):
            continue
        return row["id"]

    return None


def _choose_best(
    candidates: list[dict], name: str, prefer_bbbs: str | None
) -> dict | None:
    """按优先级选最佳匹配；多义且无线索则返回 None（由调用方抛 ambiguous 错误）。

    匹配层级（从严到松）：
    1. ``title == name``：用户传全称。
    2. ``_infer_short_title(title) == name``：用户传简称。例如候选
       ``中华人民共和国民法典`` 推断出的简称是 ``民法典``，``fetch 民法典``
       这一层即命中，避免同样 contains ``民法典`` 的司法解释 / 实施细则
       搬走最佳匹配。
    3. ``name in title``：兜底。

    每一层内多候选时：优先 ``status == "current"``，并按 ``released_at``
    降序选最新；同层无 current 则返回 None，让调用方抛 ambiguous，由
    agent 通过 ``--list-matches`` + ``--prefer-bbbs`` 显式选。
    """

    if prefer_bbbs:
        for cand in candidates:
            if prefer_bbbs in {
                cand.get("id"),
                cand.get("bbbs"),
                cand.get("detail_id"),
            }:
                return cand
        return None

    if len(candidates) == 1:
        return candidates[0]

    layers = (
        [c for c in candidates if (c.get("title") or "") == name],
        [c for c in candidates if _infer_short_title(c.get("title") or "") == name],
        [c for c in candidates if name in (c.get("title") or "")],
    )

    for layer in layers:
        if not layer:
            continue
        if len(layer) == 1:
            return layer[0]
        currents = [c for c in layer if c.get("status") == "current"]
        if currents:
            currents.sort(key=lambda c: c.get("released_at") or "", reverse=True)
            return currents[0]
        # 同层多结果但全是 amended/repealed/unknown：宁可让 agent 显式选
        return None

    return None


def _infer_short_title(title: str) -> str | None:
    """剥掉 ``中华人民共和国`` 前缀推断简称；与 adapter 同语义、本地保留以解耦。

    ``中华人民共和国民法典`` → ``民法典``。
    司法解释 / 实施细则等不以 ``中华人民共和国`` 开头的标题不会被推断出简称，
    所以 short_title 层只接现行法本身，agent 输入 ``民法典`` 不会被解释类抢走。
    """

    if not title.startswith("中华人民共和国"):
        return None
    short = title[len("中华人民共和国"):].strip()
    if 2 <= len(short) <= 24:
        return short
    return None


def _locate_article(payload: dict, requested: str) -> dict | None:
    """从已下载的 payload.articles 中按条款号定位。接受中式 / 阿拉伯 / 插入条款。"""

    try:
        target = normalize_article_number(requested)
    except (TypeError, ValueError):
        return None

    for art in payload.get("articles") or []:
        candidate_number = art.get("number") or ""
        try:
            normalized = normalize_article_number(candidate_number)
        except (TypeError, ValueError):
            normalized = candidate_number
        if normalized == target:
            return {
                "number": art.get("number"),
                "number_display": art.get("number_display"),
                "part": art.get("part"),
                "title": art.get("title"),
                "text": art.get("text"),
            }
    return None
