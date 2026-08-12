"""按需爬取 + 清洗 + 入库的协议级 high-level wrapper。

公开行为见 docs/CONTRACT.md §4.11。

与 sync.py 的边界：
- sync 是维护者批量 / 增量工具，参数面大；fetch 是协议级 agent 入口。
- fetch 是 agent / 个人按需工具，参数面小且只暴露高层动作，列入协议（CONTRACT.md §4）。

fetch 在 sync 之上做了三件事：
1. 搜索 + 选最佳匹配（避免 agent 摸索 bbbs）。
2. 暴露 dry-run / to-fixture / list-matches 三种"非入库"模式。
3. --article 定位单条返回（仍随完整法律一起入库）。
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from chinalaw.alias_agent import (
    AliasAgentRecoverableError,
    derive_aliases,
)
from chinalaw.aliases import append_unique, merge_law_aliases
from chinalaw.cleaning import CLEANING_SCHEMA_VERSION
from chinalaw.db import connect, connect_readonly, migrate, set_meta
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
from chinalaw.loader import load_law_from_dict, refresh_law_metadata
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
DIRECT_ID_SOURCES = frozenset(FETCH_SOURCES) - {"flk_npc"}
DEEP_SEARCH_MAX_PAGES = {
    # 公报站司法解释 + 司法文件约 30 页级别；31 覆盖现有 sfjs 全量。
    # 这是有界深搜，不做无界爬取。
    "court_gongbao": 31,
    # 最高检静态栏目页通常按年倒排，刑事规则/意见类目标在前 5 页内。
    "spp_gov_cn": 5,
}

# 多源 row.source_name 标准值；用于 ``_resolve_local_fetch_hint`` 校验本地命中
# 的 row 与请求源一致，避免「俗称模糊命中跨源 row」时把别源的 source_id 喂给
# 当前 source 的 fetch 主流程。各源标识必须与 source_name 保持一致。
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

    CONTRACT.md §4.11 要求三种动作互斥：
    传 ``--list-matches`` 即不下载、不入库；传 ``--to-fixture`` 即写文件不入库；
    传 ``--dry-run`` 即既不写文件也不入库；都不传则入库。CLI 用 argparse
    mutually exclusive group 兜底，library 层（SDK 直接调用）由本类兜底。
    """

    exit_code = 2


def _validate_fetch_actions(
    *,
    dry_run: bool,
    to_fixture: Path | str | None,
    list_matches: bool,
) -> None:
    active_actions = [
        name
        for name, active in (
            ("list_matches", list_matches),
            ("to_fixture", to_fixture is not None),
            ("dry_run", dry_run),
        )
        if active
    ]
    if len(active_actions) > 1:
        raise FetchActionConflictError(
            "fetch actions are mutually exclusive: "
            f"{', '.join(active_actions)} cannot be combined"
        )


def _normalize_fetch_source(
    source: str,
    status: str | None,
) -> tuple[str, list[int] | None]:
    normalized = (source or "").strip().lower().replace("-", "_")
    if normalized not in FETCH_SOURCES:
        raise ValueError(f"unsupported source for fetch: {source}")
    if status is None:
        return normalized, None
    if normalized not in STATUS_FILTER_SUPPORTED and not (
        normalized in CURRENT_ONLY_STATUS_SOURCES and status == "current"
    ):
        supported = STATUS_FILTER_SUPPORTED | CURRENT_ONLY_STATUS_SOURCES
        raise ValueError(
            f"--status filter is not supported by source {normalized!r}; "
            f"supported sources: {sorted(supported)}"
        )
    if normalized in STATUS_FILTER_SUPPORTED:
        return normalized, [status_to_sxx(status)]
    return normalized, None


def _resolve_fetch_hints(
    db_path: Path | str,
    name: str,
    source: str,
    *,
    prefer_bbbs: str | None,
    list_matches: bool,
    status: str | None,
) -> tuple[dict | None, dict | None, dict | None]:
    local_hint = None
    if prefer_bbbs is None and not list_matches and status is None:
        local_hint = _resolve_local_fetch_hint(db_path, name, source)

    document_hint = None
    if (
        prefer_bbbs is None
        and not list_matches
        and status is None
        and local_hint is None
        and _looks_like_document_number(name)
    ):
        document_hint = _lookup_document_number_hint(db_path, name, source)

    direct_hint = None
    if prefer_bbbs and source in DIRECT_ID_SOURCES and not list_matches:
        direct_hint = _direct_id_candidate(prefer_bbbs, title_hint=name)
    return local_hint, direct_hint, document_hint


def _load_fetch_candidates(
    adapter,
    source: str,
    name: str,
    *,
    limit: int,
    sxx_filter: list[int] | None,
    hints: tuple[dict | None, dict | None, dict | None],
) -> tuple[list[dict], list[dict]]:
    local_hint, direct_hint, document_hint = hints
    chosen_hint = local_hint or direct_hint or document_hint
    if chosen_hint:
        return [], [chosen_hint]

    page_size = max(limit, 1)
    search_kwargs: dict = {"page_size": page_size}
    if sxx_filter is not None:
        search_kwargs["sxx"] = sxx_filter
    try:
        return _search_candidate_rows(
            adapter,
            source,
            name,
            page_size=page_size,
            search_kwargs=search_kwargs,
        )
    except Exception as exc:
        raise FetchSourceError(f"search failed: {exc}") from exc


def _build_fetch_result_payload(
    adapter,
    db_path: Path | str,
    name: str,
    candidates: list[dict],
    rows: list[dict],
    *,
    article: str | None,
    prefer_bbbs: str | None,
    dry_run: bool,
    to_fixture: Path | str | None,
) -> tuple[dict, dict, dict, dict | None]:
    chosen = _choose_best(candidates, name, prefer_bbbs)
    if chosen is None:
        raise FetchAmbiguousError(
            f"multiple candidates matched name={name!r}; "
            "specify --prefer-id/--prefer-bbbs from candidates",
            candidates=candidates,
        )
    chosen_id = chosen["id"]
    chosen_row = next((row for row in rows if _row_id(row) == chosen_id), None)
    try:
        payload = adapter.build_law_payload(chosen_id, search_row=chosen_row)
    except Exception as exc:
        raise FetchSourceError(
            f"build_law_payload failed for id={chosen_id}: {exc}"
        ) from exc

    article_obj = _locate_article(payload, article) if article else None
    if article and article_obj is None:
        title = payload.get("title") or chosen["title"]
        raise FetchNotFoundError(f"article {article!r} not found in {title}")

    canonical_id = _resolve_output_canonical_id(
        db_path,
        payload,
        to_fixture=to_fixture,
        create_db_if_missing=not (dry_run or to_fixture is not None),
    )
    if canonical_id and canonical_id != payload.get("id"):
        payload = {**payload, "id": canonical_id}
    payload = _maybe_enrich_aliases(payload)
    return chosen, payload, _strip_transient_fetch_metadata(payload), article_obj


def _apply_fetch_output(
    db_path: Path | str,
    payload: dict,
    source: str,
    name: str,
    *,
    chosen_id: str,
    dry_run: bool,
    to_fixture: Path | str | None,
    force: bool,
) -> tuple[str | None, bool, bool, int]:
    article_count = len(payload.get("articles") or [])
    if to_fixture is not None:
        fixture_path = Path(to_fixture)
        try:
            _atomic_write_fixture(fixture_path, payload)
        except OSError as exc:
            raise FetchError(f"failed to write fixture {fixture_path}: {exc}") from exc
        return str(fixture_path), False, False, article_count
    if dry_run:
        return None, False, False, article_count
    loaded, skipped, article_count = _persist(
        db_path,
        payload,
        source,
        name,
        source_id=chosen_id,
        force=force,
    )
    return None, loaded, skipped, article_count


def fetch_law(
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

    返回 JSON schema 见 CONTRACT.md §4.11。失败抛 FetchError 子类，由 CLI 层
    捕获并映射 exit code。
    """

    name_clean = (name or "").strip()
    if not name_clean:
        raise ValueError("law name is required")

    _validate_fetch_actions(
        dry_run=dry_run,
        to_fixture=to_fixture,
        list_matches=list_matches,
    )
    normalized_source, sxx_filter = _normalize_fetch_source(source, status)
    adapter = get_source_adapter(normalized_source)
    hints = _resolve_fetch_hints(
        db_path,
        name_clean,
        normalized_source,
        prefer_bbbs=prefer_bbbs,
        list_matches=list_matches,
        status=status,
    )
    rows, candidates = _load_fetch_candidates(
        adapter,
        normalized_source,
        name_clean,
        limit=limit,
        sxx_filter=sxx_filter,
        hints=hints,
    )

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

    chosen, payload, storage_payload, article_obj = _build_fetch_result_payload(
        adapter,
        db_path,
        name_clean,
        candidates,
        rows,
        article=article,
        prefer_bbbs=prefer_bbbs,
        dry_run=dry_run,
        to_fixture=to_fixture,
    )
    chosen_id = chosen["id"]
    wrote_fixture, loaded, skipped, article_count = _apply_fetch_output(
        db_path,
        storage_payload,
        normalized_source,
        name_clean,
        chosen_id=chosen_id,
        dry_run=dry_run,
        to_fixture=to_fixture,
        force=force,
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


def _atomic_write_fixture(path: Path, payload: dict) -> None:
    """Write JSON in the destination directory and atomically replace it."""

    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=parent,
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, path)
        try:
            directory_fd = os.open(parent, os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temp_path.exists():
            temp_path.unlink()


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
            refresh_law_metadata(conn, payload)
            article_count = conn.execute(
                "SELECT COUNT(*) FROM articles WHERE law_id = ?", (payload["id"],)
            ).fetchone()[0]
            # 即使跳过 upsert 也确保文号索引存在（适配 v0.2 之前入库的旧 row）
            _index_document_number(conn, payload, source, source_id)
            _set_fetch_meta(conn, source, query)
            return False, True, article_count

        article_count = load_law_from_dict(conn, payload)
        _set_fetch_meta(conn, source, query)
        _index_document_number(conn, payload, source, source_id)
        return True, False, article_count


def _set_fetch_meta(conn, source: str, query: str) -> None:
    checked_at = datetime.now(timezone.utc).isoformat()
    set_meta(conn, "last_sync_at", checked_at)
    set_meta(conn, f"source:{source}:last_sync_at", checked_at)
    set_meta(conn, f"source:{source}:last_mode", "fetch")
    set_meta(conn, f"source:{source}:last_query", query)
    set_meta(conn, "cleaning_schema_version", str(CLEANING_SCHEMA_VERSION))


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

    详见 ``docs/CONTRACT.md`` §4.11。
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
    预解析创建新 DB。
    """

    if source not in SOURCE_NAME_MARKERS:
        return None
    db = Path(db_path)
    if not db.exists():
        return None
    try:
        with connect_readonly(db_path) as conn:
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
            # 显式区分源风味，避免把非-FLK id 当作 bbbs。
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
        with connect_readonly(db_path) as conn:
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
    """Resolve a stable id without mutating DBs for read-only actions.

    非入库动作默认不创建 DB；任何 DB 访问异常都安全降级回 raw bbbs，
    不阻塞主流程。
    """

    try:
        db = Path(db_path)
        if not create_if_missing and not db.exists():
            return None
        if create_if_missing:
            with connect(db_path) as conn:
                migrate(conn)
                return _resolve_canonical_id(conn, payload)
        with connect_readonly(db_path) as conn:
            return _resolve_canonical_id(conn, payload)
    except sqlite3.ProgrammingError:
        raise
    except (sqlite3.OperationalError, sqlite3.DatabaseError, OSError):
        return None


def _escape_like(raw: str) -> str:
    return raw.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")


def _resolve_canonical_id(conn, payload: dict) -> str | None:
    """Resolve one unambiguous stable id from laws and applicability references.

    ``laws`` candidates are all strict-checked; the query must never discard a
    later matching revision merely because an older same-title row was returned
    first.  ``applicability_rules`` / ``law_relations`` provide stable ids for
    old laws that are intentionally absent from the bundled public fixtures.
    Registered references take precedence over an earlier raw upstream id so
    the documented ``needs_fetch -> fetch -> applicable`` loop can converge.

    Zero matches return ``None``.  More than one matching id is a data-integrity
    ambiguity and fails loudly before persistence.
    """

    law_matches = _matching_law_rows(conn, payload)
    reference_matches = _matching_reference_ids(conn, payload)

    if len(reference_matches) == 1:
        return next(iter(reference_matches))
    if len(reference_matches) > 1:
        overlap = set(law_matches) & set(reference_matches)
        if len(overlap) == 1:
            return next(iter(overlap))
        _raise_canonical_ambiguity(reference_matches, origin="applicability")

    if len(law_matches) == 1:
        return next(iter(law_matches))
    if len(law_matches) > 1:
        _raise_canonical_ambiguity(law_matches, origin="laws")
    return None


def _payload_identifiers(payload: dict) -> list[str]:
    identifiers: list[str] = []
    for value in (
        payload.get("id"),
        payload.get("title"),
        payload.get("short_title"),
        *(payload.get("aliases") or []),
    ):
        if not isinstance(value, str):
            continue
        clean = value.strip()
        if clean and clean not in identifiers:
            identifiers.append(clean)
    return identifiers


def _matching_law_rows(conn, payload: dict) -> dict[str, dict]:
    matches: dict[str, dict] = {}
    incoming_id = (payload.get("id") or "").strip()
    if incoming_id:
        row = conn.execute("SELECT * FROM laws WHERE id = ?", (incoming_id,)).fetchone()
        if row is not None and law_row_matches_payload(row, payload, strict=True):
            matches[row["id"]] = _canonical_candidate_from_law_row(row)

    for ident in _payload_identifiers(payload):
        alias_pattern = "%" + _escape_like(json.dumps(ident, ensure_ascii=False)) + "%"
        rows = conn.execute(
            "SELECT * FROM laws "
            "WHERE title = ? OR short_title = ? OR aliases LIKE ? ESCAPE '\\' "
            "ORDER BY CASE WHEN title = ? THEN 0 WHEN short_title = ? THEN 1 ELSE 2 END, id",
            (ident, ident, alias_pattern, ident, ident),
        ).fetchall()
        for row in rows:
            if law_row_matches_payload(row, payload, strict=True):
                matches[row["id"]] = _canonical_candidate_from_law_row(row)
    return matches


def _canonical_candidate_from_law_row(row) -> dict:
    return {
        "id": row["id"],
        "title": row["title"],
        "released_at": row["released_at"],
        "effective_at": row["effective_at"],
        "source": "laws",
    }


def _matching_reference_ids(conn, payload: dict) -> dict[str, dict]:
    references: dict[str, set[str]] = {}

    def add(law_id, law_title) -> None:
        if not isinstance(law_id, str) or not law_id.strip():
            return
        clean_id = law_id.strip()
        titles = references.setdefault(clean_id, set())
        if isinstance(law_title, str) and law_title.strip():
            titles.add(law_title.strip())

    if _table_exists(conn, "applicability_rules"):
        for row in conn.execute(
            "SELECT primary_law_id, primary_law_title, "
            "fallback_law_id, fallback_law_title FROM applicability_rules"
        ):
            add(row["primary_law_id"], row["primary_law_title"])
            add(row["fallback_law_id"], row["fallback_law_title"])
    if _table_exists(conn, "law_relations"):
        for row in conn.execute(
            "SELECT from_law_id, from_law_title, to_law_id, to_law_title "
            "FROM law_relations"
        ):
            add(row["from_law_id"], row["from_law_title"])
            add(row["to_law_id"], row["to_law_title"])

    matches: dict[str, dict] = {}
    for law_id, titles in references.items():
        if not _reference_source_matches(law_id, payload):
            continue
        matching_title = next(
            (
                title
                for title in sorted(titles)
                if _reference_matches_payload(law_id, title, payload)
            ),
            None,
        )
        if matching_title is None:
            continue
        existing = conn.execute("SELECT * FROM laws WHERE id = ?", (law_id,)).fetchone()
        if existing is not None and not law_row_matches_payload(existing, payload, strict=True):
            continue
        matches[law_id] = {
            "id": law_id,
            "title": matching_title,
            "source": "applicability",
        }
    return matches


def _table_exists(conn, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
    ).fetchone() is not None


_VERSION_QUALIFIER_RE = re.compile(
    r"[（(][^）)]*(?:19|20)\d{2}[^）)]*(?:修订|修正|施行|版本|版)[^）)]*[）)]"
)
_YEAR_RE = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")


def _reference_source_matches(law_id: str, payload: dict) -> bool:
    if not law_id.startswith("flk-"):
        return True
    return (payload.get("source_name") or "").strip() == SOURCE_NAME_MARKERS["flk_npc"]


def _reference_matches_payload(law_id: str, title: str, payload: dict) -> bool:
    incoming_forms: set[str] = set()
    for identifier in _payload_identifiers(payload):
        incoming_forms.update(_title_match_forms(identifier))
    if not (incoming_forms & _title_match_forms(title)):
        return False

    reference_years = set(_YEAR_RE.findall(f"{law_id} {title}"))
    incoming_years: set[str] = set()
    for value in (
        payload.get("title"),
        payload.get("short_title"),
        payload.get("released_at"),
        payload.get("effective_at"),
    ):
        if isinstance(value, str):
            incoming_years.update(_YEAR_RE.findall(value))
    return not reference_years or not incoming_years or bool(reference_years & incoming_years)


def _title_match_forms(title: str) -> set[str]:
    base = _VERSION_QUALIFIER_RE.sub("", title).strip()
    forms = {_compact_match_text(title), _compact_match_text(base)}
    short = _infer_short_title(base)
    if short:
        forms.add(_compact_match_text(short))
    return {form for form in forms if form}


def _raise_canonical_ambiguity(matches: dict[str, dict], *, origin: str) -> None:
    candidates = [matches[key] for key in sorted(matches)]
    raise FetchAmbiguousError(
        f"canonical id is ambiguous across {origin}: {', '.join(sorted(matches))}",
        candidates=candidates,
    )


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

    ranked = [
        (rank, candidate)
        for candidate in candidates
        if (rank := _candidate_match_rank(candidate, name)) is not None
    ]
    if not ranked:
        return None
    best_rank = min(rank for rank, _candidate in ranked)
    layer = [candidate for rank, candidate in ranked if rank == best_rank]
    if len(layer) == 1:
        return layer[0]
    currents = [candidate for candidate in layer if candidate.get("status") == "current"]
    if currents:
        currents.sort(key=lambda candidate: candidate.get("released_at") or "", reverse=True)
        return currents[0]
    # 同层多结果但全是 amended/repealed/unknown：宁可让 agent 显式选
    return None


def _candidate_match_rank(candidate: dict, name: str) -> int | None:
    if any(
        candidate.get(marker)
        for marker in ("direct_id", "local_alias_resolved", "document_number_resolved")
    ):
        return 0

    query_variants = [name]
    stripped = _strip_trial_status_suffix(name)
    if stripped != name:
        query_variants.append(stripped)
    ranks = [
        rank
        for query_variant in query_variants
        if (rank := _candidate_title_match_rank(candidate, query_variant)) is not None
    ]
    return min(ranks) if ranks else None


def _candidate_title_match_rank(candidate: dict, name: str) -> int | None:
    query = _compact_match_text(name)
    title = _compact_match_text(candidate.get("title") or "")
    if not query or not title:
        return None
    if title == query:
        return 0

    inferred_short = _infer_short_title(candidate.get("title") or "")
    if inferred_short and _compact_match_text(inferred_short) == query:
        return 1
    candidate_short = candidate.get("short_title")
    if isinstance(candidate_short, str) and _compact_match_text(candidate_short) == query:
        return 1
    aliases = candidate.get("aliases") or []
    if any(
        isinstance(alias, str) and _compact_match_text(alias) == query
        for alias in aliases
    ):
        return 1
    if query in title:
        return 2
    if _ordered_match(query, title, minimum=4):
        return 3
    return None


def _compact_match_text(value: str) -> str:
    return re.sub(r"[\s《》【】\[\]（）()：:、，,。._—-]+", "", value).casefold()


def _ordered_match(needle: str, haystack: str, *, minimum: int) -> bool:
    significant = [char for char in needle if char.isalnum() or "\u4e00" <= char <= "\u9fff"]
    if len(significant) < minimum:
        return False
    cursor = iter(haystack)
    return all(any(candidate == char for candidate in cursor) for char in significant)


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
