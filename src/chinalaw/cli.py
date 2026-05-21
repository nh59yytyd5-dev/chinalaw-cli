"""CLI 入口点。"""

from __future__ import annotations

import argparse
import json
import os
import sys
from contextlib import suppress
from pathlib import Path
from urllib.error import URLError

from chinalaw import (
    __version__,
    applicability,
    commentary,
    doctor,
    ensure,
    formatters,
    loader,
    metadata,
    normpacks,
    normsources,
    notices,
    rebuild,
    service,
    snapshots,
    sources,
)
from chinalaw import (
    audit as audit_mod,
)
from chinalaw import corpus as corpus_mod
from chinalaw import (
    discover as discover_mod,
)
from chinalaw import fetch as fetch_mod
from chinalaw.db import DEFAULT_DB_PATH
from chinalaw.sync import sync_source

_NOTICE_CONTEXT: dict[str, object] = {}


def _add_format_arg(
    p: argparse.ArgumentParser, *, choices: tuple[str, ...] = ("json", "md")
) -> None:
    p.add_argument(
        "--format",
        choices=choices,
        default="json",
        help="输出格式（默认 json）",
    )
    p.add_argument(
        "--db",
        default=None,
        help=f"SQLite 数据库路径（默认 {DEFAULT_DB_PATH}）",
    )


def _add_snapshot_out_arg(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--snapshot-out",
        default=None,
        help=(
            "把本次检索结果追加到项目级 JSONL 快照；也可用 "
            "chinalaw snapshot init、CHINALAW_PROJECT 或 CHINALAW_SNAPSHOT_OUT 启用"
        ),
    )


def _fetch_to_markdown(payload: dict) -> str:
    if payload.get("kind") == "law_fetch_candidates":
        lines = [f"# fetch 候选：{payload.get('name', '')}", ""]
        for cand in payload.get("candidates") or []:
            lines.append(
                f"- {cand.get('title', '')} — id={cand.get('id') or cand.get('bbbs', '')}"
                f" / released={cand.get('released_at', '')}"
                f" / status={cand.get('status', '')}"
            )
        return "\n".join(lines) + "\n"

    matched = payload.get("matched_title") or payload.get("name", "")
    matched_id = payload.get("matched_id") or payload.get("matched_bbbs", "")
    actions = []
    if payload.get("loaded"):
        actions.append("loaded")
    if payload.get("skipped"):
        actions.append("skipped (same source_hash)")
    if payload.get("dry_run"):
        actions.append("dry-run")
    if payload.get("wrote_fixture"):
        actions.append(f"wrote_fixture={payload['wrote_fixture']}")
    action_text = ", ".join(actions) or "no-op"

    lines = [
        f"# fetch:{payload.get('name', '')} → {matched}",
        f"- id: `{matched_id}`",
        f"- 动作: {action_text}",
        f"- 条文数: {payload.get('article_count', 0)}",
    ]
    article = payload.get("article")
    if article:
        text = (article.get("text") or "").strip().replace("\n", " ")
        if len(text) > 200:
            text = text[:200] + "…"
        lines.append("")
        lines.append(f"## {article.get('number_display', '')}")
        if article.get("part"):
            lines.append(f"_位置：{article['part']}_")
        lines.append("")
        lines.append(f"> {text}")
    return "\n".join(lines) + "\n"


def _sync_to_markdown(payload: dict) -> str:
    if payload.get("kind") == "applicability_import":
        return (
            f"时间效力规则同步完成：{payload.get('relations_loaded', 0)} 条关系 / "
            f"{payload.get('rules_loaded', 0)} 条规则\n"
            + "\n".join(f"- {topic}" for topic in payload.get("topics", []))
            + "\n"
        )
    return (
        f"同步完成：{payload['laws_loaded']} 部法规 / "
        f"{payload['articles_loaded']} 条条文\n"
        + "\n".join(f"- {t}" for t in payload.get("titles", []))
        + "\n"
    )


def _corpus_to_markdown(payload: dict) -> str:
    kind = payload.get("kind")
    if kind == "recommended_corpus_profiles":
        lines = [
            "# recommended corpus profiles",
            "",
            f"- schema_version: {payload.get('schema_version')}",
            f"- as_of: {payload.get('as_of')}",
            f"- path: `{payload.get('path')}`",
            f"- profiles: {payload.get('profile_count', 0)}",
            "",
            "## Profiles",
        ]
        for profile in payload.get("profiles") or []:
            deps = ", ".join(profile.get("dependencies") or []) or "none"
            aliases = ", ".join(profile.get("aliases") or []) or "none"
            lines.append(
                f"- `{profile.get('name')}` ({profile.get('priority')}) — "
                f"{profile.get('entry_count', 0)} entries / "
                f"{profile.get('installable_count', 0)} installable / "
                f"deps: {deps} / aliases: {aliases}"
            )
        return "\n".join(lines).rstrip() + "\n"

    lines = [
        "# recommended corpus profile",
        "",
        f"- requested: {', '.join(payload.get('requested_profiles') or [])}",
        f"- included: {', '.join(payload.get('included_profiles') or [])}",
        f"- dependencies: {'yes' if payload.get('include_dependencies') else 'no'}",
        f"- entries: {payload.get('entry_count', 0)}",
        "",
        "## Entries",
    ]
    for entry in payload.get("entries") or []:
        installable = (
            entry.get("installable", True)
            and entry.get("source_status", "supported") == "supported"
        )
        status = "installable" if installable else entry.get("source_status", "unsupported")
        lines.append(
            f"- `{entry.get('profile')}` {entry.get('title')}（{entry.get('short_title') or ''}）"
            f" — {entry.get('priority')} / {entry.get('primary_source')} / {status}"
        )
        if entry.get("needs_verification"):
            lines.append("  needs_verification: true")
    return "\n".join(lines).rstrip() + "\n"


def _schema_to_markdown(payload: dict) -> str:
    kind = payload.get("kind")
    if kind == "cli_schema_index":
        lines = [
            "# chinalaw schema",
            "",
            f"- schema_version: {payload.get('schema_version')}",
            f"- commands: {payload.get('command_count', 0)}",
            "",
            "## Global Flags",
        ]
        for flag in payload.get("global_flags") or []:
            lines.append(f"- `{flag.get('name')}` — {flag.get('description') or ''}")
        lines.extend(
            [
                "",
                "## Commands",
            ]
        )
        for command in payload.get("commands") or []:
            lines.append(
                f"- `{command.get('path')}` — {command.get('risk')} / {command.get('summary')}"
            )
        return "\n".join(lines).rstrip() + "\n"

    if kind == "mcp_schema":
        budget = (payload.get("context_budget") or {}).get("target_tools_list_chars")
        lines = [
            "# chinalaw MCP schema",
            "",
            f"- tools: {payload.get('tool_count', 0)}",
            f"- context budget: {budget} chars",
            "",
            "## Tools",
        ]
        for tool in payload.get("tools") or []:
            lines.append(
                f"- `{tool.get('name')}` — {tool.get('risk')} / {tool.get('cli_equivalent')}"
            )
        return "\n".join(lines).rstrip() + "\n"

    command = payload.get("command") or {}
    lines = [
        f"# schema: {command.get('path') or payload.get('target') or ''}",
        "",
        f"- summary: {command.get('summary')}",
        f"- risk: {command.get('risk')}",
        f"- side_effect: {command.get('side_effect')}",
        f"- network: {command.get('network')}",
        f"- authority_boundary: {command.get('authority_boundary')}",
        f"- output: {command.get('json_output', {}).get('kind')}",
        "",
        "## Arguments",
    ]
    positionals = command.get("positional") or []
    flags = command.get("flags") or []
    if positionals:
        for arg in positionals:
            required = "required" if arg.get("required") else "optional"
            lines.append(f"- `{arg.get('name')}` ({required}) — {arg.get('description') or ''}")
    if flags:
        for arg in flags:
            required = "required" if arg.get("required") else "optional"
            lines.append(f"- `{arg.get('name')}` ({required}) — {arg.get('description') or ''}")
    if not positionals and not flags:
        lines.append("- 无")
    exit_codes = command.get("exit_codes") or {}
    if exit_codes:
        lines.extend(["", "## Exit Codes"])
        for code, meaning in exit_codes.items():
            lines.append(f"- `{code}`: {meaning}")
    misuse = command.get("common_misuse") or []
    if misuse:
        lines.extend(["", "## Common Misuse"])
        for item in misuse:
            lines.append(f"- {item}")
    follow_ups = command.get("suggested_follow_ups") or []
    if follow_ups:
        lines.extend(["", "## Suggested Follow-ups"])
        for item in follow_ups:
            lines.append(f"- `{item}`")
    return "\n".join(lines).rstrip() + "\n"


def _doctor_to_markdown(payload: dict) -> str:
    status = "通过" if payload.get("ok") else "失败"
    lines = [
        "# chinalaw doctor",
        "",
        f"- 状态：{status}",
        f"- strict：{payload.get('strict')}",
        f"- 数据库：`{payload.get('db_path')}`",
        f"- errors：{payload.get('error_count', 0)}",
        f"- warnings：{payload.get('warning_count', 0)}",
        "",
        "## Checks",
    ]
    for check in payload.get("checks") or []:
        lines.append(
            f"- [{str(check.get('status') or '').upper()}] "
            f"{check.get('name')}: {check.get('message')}"
        )
        if check.get("hint"):
            lines.append(f"  hint: {check['hint']}")
    return "\n".join(lines).rstrip() + "\n"


def _add_search_parser(sub) -> None:
    p_search = sub.add_parser("search", help="关键词 / 全文检索（FTS5）")
    p_search.add_argument(
        "query",
        nargs="+",
        help="检索关键词；多个未加引号的词会按空格合并，便于 agent 容错",
    )
    p_search.add_argument("--limit", type=int, default=20, help="返回结果数上限")
    p_search.add_argument(
        "--in",
        dest="in_laws",
        help="限定公开法规范围；多个名称用逗号分隔，如 民法典,劳动法",
    )
    p_search.add_argument(
        "--in-part",
        dest="in_part",
        help=(
            "限定章节文本（编/章/节），仅作用于条文检索；可与 --in 联用，先按法规过滤再按章节过滤"
        ),
    )
    p_search.add_argument(
        "--kind",
        choices=["article", "law", "norm", "all"],
        default="all",
        help="匹配范围（默认全部）",
    )
    _add_format_arg(p_search)
    _add_snapshot_out_arg(p_search)


def _add_read_parsers(sub) -> None:
    p_get = sub.add_parser("get", help="按名称精取一部法规全文")
    p_get.add_argument("name", help="法规 id / 全称 / 简称")
    p_get.add_argument("--as-of", help="按 YYYY-MM-DD 查询当时有效版本")
    _add_format_arg(p_get)
    _add_snapshot_out_arg(p_get)

    p_resolve = sub.add_parser(
        "resolve",
        help="把俗称 / 模糊名解析回官方记录（轻量版 get，仅返元数据 + 命中路径）",
    )
    p_resolve.add_argument("name", help="俗称 / 全名 / 模糊名（如 '公司法解释一'）")
    _add_format_arg(p_resolve)

    p_article = sub.add_parser("article", help="按法规名 + 条款号定位条文")
    p_article.add_argument("name", help="法规 id / 全称 / 简称")
    p_article.add_argument("number", help="条款号，如 '71' 或 '第七十一条'")
    p_article.add_argument("--as-of", help="按 YYYY-MM-DD 查询当时有效版本")
    article_footer = p_article.add_mutually_exclusive_group()
    article_footer.add_argument(
        "--no-footer",
        action="store_true",
        help="仅 --format md 生效：只保留标题和正文，省略状态/来源/核查信息",
    )
    article_footer.add_argument(
        "--compact",
        action="store_true",
        help="仅 --format md 生效：把 footer 压缩为一行",
    )
    article_footer.add_argument(
        "--bare",
        action="store_true",
        help="仅 --format md 生效：只输出条文正文，无 markdown 标题 / 引用号 / 元信息",
    )
    article_footer.add_argument(
        "--inline",
        action="store_true",
        help="仅 --format md 生效：单行 `<short_title>§<number> <text>` 形式",
    )
    article_number = p_article.add_mutually_exclusive_group()
    article_number.add_argument(
        "--arabic",
        action="store_true",
        help="Markdown 标题使用阿拉伯数字条号，如 第524条",
    )
    article_number.add_argument(
        "--section",
        action="store_true",
        help="Markdown 标题使用学术 §N 形式，如 §524",
    )
    p_article.add_argument(
        "--with-title",
        action="store_true",
        help="若数据层有 title 字段，则在标题后追加【条名】",
    )
    p_article.add_argument(
        "--no-norm-fallback",
        action="store_true",
        help="禁用：当公开法规未命中时不再尝试私域规范库（norm sources）",
    )
    _add_format_arg(p_article, choices=("json", "md", "card"))
    _add_snapshot_out_arg(p_article)

    p_articles = sub.add_parser("articles", help="按法规名 + 多个条款号批量定位条文")
    p_articles.add_argument(
        "name",
        nargs="?",
        help="法规 id / 全称 / 简称（使用 --batch 时可省略）",
    )
    p_articles.add_argument(
        "numbers_arg",
        nargs="?",
        help="条款号列表，如 '5,12,13,19,23-25'；等同于 --numbers",
    )
    p_articles.add_argument(
        "--numbers",
        help="条款号列表，如 '5,12,13,19,23-25'",
    )
    p_articles.add_argument(
        "--batch",
        help=(
            "多法批量取条 spec，如 '民法典:557-561,568;合同编通则解释:27,55-58'。"
            "使用本选项时位置参数 name / numbers 将被忽略。"
        ),
    )
    p_articles.add_argument("--as-of", help="按 YYYY-MM-DD 查询当时有效版本")
    articles_footer = p_articles.add_mutually_exclusive_group()
    articles_footer.add_argument(
        "--no-footer",
        action="store_true",
        help="仅 --format md 生效：只保留各条正文，省略汇总头部和单行 footer",
    )
    articles_footer.add_argument(
        "--compact",
        action="store_true",
        help="仅 --format md 生效：尾部追加单行状态 / 施行 / 核查 footer",
    )
    articles_footer.add_argument(
        "--bare",
        action="store_true",
        help="仅 --format md 生效：只输出每条正文（用空行分隔），无标题 / 引用号 / 元信息",
    )
    articles_footer.add_argument(
        "--inline",
        action="store_true",
        help="仅 --format md 生效：每条单行 `<short_title>§<number> <text>` 形式",
    )
    articles_number = p_articles.add_mutually_exclusive_group()
    articles_number.add_argument(
        "--arabic",
        action="store_true",
        help="Markdown 标题使用阿拉伯数字条号，如 第524条",
    )
    articles_number.add_argument(
        "--section",
        action="store_true",
        help="Markdown 标题使用学术 §N 形式，如 §524",
    )
    p_articles.add_argument(
        "--with-title",
        action="store_true",
        help="若数据层有 title 字段，则在标题后追加【条名】",
    )
    p_articles.add_argument(
        "--no-norm-fallback",
        action="store_true",
        help="禁用：当公开法规未命中时不再尝试私域规范库（norm sources）",
    )
    _add_format_arg(p_articles)
    _add_snapshot_out_arg(p_articles)

    p_outline = sub.add_parser("outline", help="列出一部法规的条文目录和正文预览")
    p_outline.add_argument("name", help="法规 id / 全称 / 简称")
    p_outline.add_argument("--part", help="按编/章/节文本过滤")
    p_outline.add_argument(
        "--preview-chars",
        type=int,
        default=80,
        help="每条正文预览字符数（默认 80）",
    )
    p_outline.add_argument(
        "--with-text",
        "--full-text",
        dest="with_text",
        action="store_true",
        help=(
            "输出章节内每条完整条文（与 articles 渲染口径对齐），"
            "可与 --no-footer / --compact / --bare / --inline / --arabic / "
            "--section / --with-title 联用；不传时维持原有目录预览输出。"
        ),
    )
    outline_footer = p_outline.add_mutually_exclusive_group()
    outline_footer.add_argument(
        "--no-footer",
        action="store_true",
        help="仅 --with-text + --format md 生效：仅保留各条正文，省略汇总头部",
    )
    outline_footer.add_argument(
        "--compact",
        action="store_true",
        help="仅 --with-text + --format md 生效：尾部追加单行状态 / 施行 / 核查 footer",
    )
    outline_footer.add_argument(
        "--bare",
        action="store_true",
        help="仅 --with-text + --format md 生效：只输出每条正文，无标题 / 引用号 / 元信息",
    )
    outline_footer.add_argument(
        "--inline",
        action="store_true",
        help="仅 --with-text + --format md 生效：每条单行 `<short_title>§<number> <text>` 形式",
    )
    outline_number = p_outline.add_mutually_exclusive_group()
    outline_number.add_argument(
        "--arabic",
        action="store_true",
        help="（仅 --with-text）Markdown 标题使用阿拉伯数字条号，如 第524条",
    )
    outline_number.add_argument(
        "--section",
        action="store_true",
        help="（仅 --with-text）Markdown 标题使用学术 §N 形式，如 §524",
    )
    p_outline.add_argument(
        "--with-title",
        action="store_true",
        help="（仅 --with-text）若数据层有 title 字段，则在标题后追加【条名】",
    )
    _add_format_arg(p_outline)
    _add_snapshot_out_arg(p_outline)

    p_cited_by = sub.add_parser(
        "cited-by",
        help="列出引用某条法规的其它法规条文（绝对引用：《民法典》第522条 / 民法典第五百二十二条）",
    )
    p_cited_by.add_argument(
        "spec",
        help="目标条文 spec，如 `民法典:522` 或 `民法典：第522条`",
    )
    p_cited_by.add_argument(
        "--in",
        dest="in_laws",
        help="限定扫描法规范围；多个名称用逗号分隔，如 合通解释,物权编解释一",
    )
    p_cited_by.add_argument(
        "--include-self",
        action="store_true",
        help="同时返回同部法规内部互引（默认排除）",
    )
    p_cited_by.add_argument(
        "--limit",
        type=int,
        default=50,
        help="返回命中数上限（默认 50，最大 500）",
    )
    _add_format_arg(p_cited_by)

    p_list = sub.add_parser("list", help="浏览法规（按级别/状态过滤）")
    p_list.add_argument("--level", help="效力级别，如 law / admin_regulation")
    p_list.add_argument("--status", help="状态，如 current / amended / repealed")
    p_list.add_argument("--limit", type=int, default=50)
    _add_format_arg(p_list)

    p_laws = sub.add_parser("laws", help="列出法规 id / 全称 / 简称，避免 agent 直连 SQLite")
    p_laws.add_argument("--level", help="效力级别，如 law / admin_regulation")
    p_laws.add_argument("--status", help="状态，如 current / amended / repealed")
    p_laws.add_argument("--limit", type=int, default=50)
    _add_format_arg(p_laws)


def _add_sync_parser(sub) -> None:
    p_sync = sub.add_parser("sync", help="同步数据（fixture / JSON / 真实数据源）")
    p_sync.add_argument(
        "--fixtures",
        action="store_true",
        help="加载内置 fixture 数据（demo 用）",
    )
    p_sync.add_argument(
        "--from-dir",
        help="从指定目录加载 *.json（替代 --fixtures）",
    )
    p_sync.add_argument(
        "--applicability",
        action="store_true",
        help="加载内置时间效力 / 规范关系规则（grounding only）",
    )
    p_sync.add_argument(
        "--applicability-dir",
        help="从指定目录加载时间效力 / 规范关系 *.json（配合 --applicability）",
    )
    p_sync.add_argument(
        "--source",
        choices=["flk_npc"],
        help="真实数据源名称",
    )
    p_sync.add_argument(
        "--query",
        help="从真实数据源按关键词搜索并同步前 N 条",
    )
    p_sync.add_argument(
        "--bbbs",
        help="从真实数据源按单个法规 bbbs 精确同步",
    )
    p_sync.add_argument(
        "--limit",
        type=int,
        default=5,
        help="真实数据源同步数量上限（默认 5）",
    )
    p_sync.add_argument(
        "--batch",
        action="store_true",
        help="按分页批量同步真实数据源",
    )
    p_sync.add_argument(
        "--start-page",
        type=int,
        default=1,
        help="批量同步起始页（默认 1）",
    )
    p_sync.add_argument(
        "--max-pages",
        type=int,
        help="批量同步最多处理多少页",
    )
    p_sync.add_argument(
        "--page-size",
        type=int,
        default=20,
        help="批量同步每页数量（默认 20）",
    )
    p_sync.add_argument(
        "--resume",
        action="store_true",
        help="从上次记录的下一页继续批量同步",
    )
    p_sync.add_argument(
        "--stop-after-stable-pages",
        type=int,
        help="连续若干页没有新变化时停止（增量同步辅助）",
    )
    p_sync.add_argument(
        "--incremental",
        action="store_true",
        help="按发布日期窗口做增量同步",
    )
    p_sync.add_argument(
        "--published-from",
        help="增量同步起始发布日期（YYYY-MM-DD）",
    )
    p_sync.add_argument(
        "--published-to",
        help="增量同步结束发布日期（YYYY-MM-DD）",
    )
    p_sync.add_argument(
        "--days-back",
        type=int,
        default=30,
        help="首次增量同步默认回看多少天（默认 30）",
    )
    p_sync.add_argument(
        "--overlap-days",
        type=int,
        default=1,
        help="增量同步窗口重叠天数（默认 1）",
    )
    _add_format_arg(p_sync)


def _add_fetch_parser(sub) -> None:
    p_fetch = sub.add_parser(
        "fetch",
        help="按法律名一条龙取条文 + 清洗 + 入库（协议级 high-level 接口，alpha）",
    )
    p_fetch.add_argument("name", help="法律名（全称 / 简称 / alias）")
    p_fetch.add_argument(
        "--source",
        choices=list(fetch_mod.FETCH_SOURCES),
        default="flk_npc",
        help="数据源（默认 flk_npc）",
    )
    p_fetch.add_argument(
        "--article",
        help="指定条款号（中式 / 阿拉伯 / 插入条款），命中后随完整法律一起入库并在响应定位返回",
    )
    # 三种"非默认入库"动作互斥（参见 ADR-0006 §3 / CONTRACT.md §4.11）。
    # argparse 在冲突时自动退出码 2，与 fetch.FetchActionConflictError 对齐。
    fetch_action_group = p_fetch.add_mutually_exclusive_group()
    fetch_action_group.add_argument(
        "--dry-run",
        action="store_true",
        help="不入库，仅输出清洗后的 law payload",
    )
    fetch_action_group.add_argument(
        "--to-fixture",
        help="把 law payload 写入指定文件（不入库；用于 PR 审查）",
    )
    fetch_action_group.add_argument(
        "--list-matches",
        action="store_true",
        help="仅列出搜索命中候选；不下载、不入库",
    )
    p_fetch.add_argument(
        "--prefer-id",
        dest="prefer_bbbs",
        help="多条命中时手动指定候选 id（FLK 为 bbbs，HTML 源为 detail_id）",
    )
    p_fetch.add_argument(
        "--prefer-bbbs",
        dest="prefer_bbbs",
        help="多条命中时手动指定 bbbs",
    )
    p_fetch.add_argument(
        "--limit",
        type=int,
        default=5,
        help="搜索候选上限（默认 5）",
    )
    p_fetch.add_argument(
        "--force",
        action="store_true",
        help="即使 source_hash 相同也重新清洗并 upsert；用于清洗规则升级后补写",
    )
    p_fetch.add_argument(
        "--status",
        choices=["repealed", "amended", "current", "pending_effective"],
        default=None,
        help=(
            "按状态过滤搜索候选；flk_npc 支持完整枚举，gov_xzfgk / 证券公开源仅接受 "
            "current，其它源传入会 fail loud。用于 v0.2 时间效力闭环"
            "（如 --status repealed 拉历史废止法）。详见 docs/CLI_STATUS_FLAG_SPEC.md。"
        ),
    )
    _add_format_arg(p_fetch)
    _add_snapshot_out_arg(p_fetch)


def _add_discover_parser(sub) -> None:
    p_discover = sub.add_parser(
        "discover",
        help=(
            "按状态/关键词批量列出候选法规（不下载、不入库；alpha）。"
            "用作 fetch 的探测前哨：先 discover 拉候选池，再 fetch "
            "--prefer-bbbs 精取。详见 docs/CLI_STATUS_FLAG_SPEC.md。"
        ),
    )
    p_discover.add_argument(
        "--source",
        choices=list(discover_mod.DISCOVER_SOURCES),
        default="flk_npc",
        help="数据源（flk_npc + 国家行政法规库 + 证监会 / 交易所 / 中证登 / 协会公开源）",
    )
    p_discover.add_argument(
        "--query",
        default=None,
        help=("标题子串过滤；为空时按数据源默认顺序列出（flk_npc 按 gbrq DESC）"),
    )
    p_discover.add_argument(
        "--status",
        choices=["repealed", "amended", "current", "pending_effective"],
        default=None,
        help=(
            "按状态过滤；flk_npc 支持完整枚举，gov_xzfgk / 证券公开源仅接受 current "
            "（详见 docs/CLI_STATUS_FLAG_SPEC.md）。"
        ),
    )
    p_discover.add_argument(
        "--limit",
        type=int,
        default=20,
        help="候选上限（默认 20）",
    )
    _add_format_arg(p_discover)


def _add_ensure_parser(sub) -> None:
    p_ensure = sub.add_parser(
        "ensure",
        help="本地优先确保公开法规已入库：已有则跳过，缺失 / stub / seed 才 fetch（alpha）",
    )
    p_ensure.add_argument(
        "names",
        nargs="*",
        help="法规名（可传多个；全称 / 简称 / alias）",
    )
    p_ensure.add_argument(
        "--profile",
        action="append",
        default=[],
        help=(
            "按推荐语料 profile 补库，可重复；例如 baseline/general/company/criminal。"
            "传此参数时不同时接收显式 names / --from-file / --from-dir。"
        ),
    )
    p_ensure.add_argument(
        "--no-profile-deps",
        action="store_true",
        help="只安装指定 profile，不自动包含 dependencies。",
    )
    p_ensure.add_argument(
        "--from-file",
        help="从文本文件读取法规名；每行一个，空行和 # 注释跳过",
    )
    p_ensure.add_argument(
        "--from-dir",
        help="从目录文件名提取法规名；只读文件名，不读取文件正文",
    )
    p_ensure.add_argument(
        "--filenames-only",
        action="store_true",
        help="显式声明目录模式只使用文件名；当前 --from-dir 默认即如此",
    )
    p_ensure.add_argument(
        "--source",
        choices=list(fetch_mod.FETCH_SOURCES),
        default="flk_npc",
        help="缺失时使用的数据源（默认 flk_npc）",
    )
    p_ensure.add_argument(
        "--limit",
        type=int,
        default=5,
        help="每个缺失法规的 fetch 搜索候选上限（默认 5）",
    )
    p_ensure.add_argument(
        "--interval",
        type=float,
        default=1.0,
        help="批量 fetch 间隔秒数；仅在实际远程 fetch 之间生效（默认 1.0）",
    )
    _add_format_arg(p_ensure)


def _add_corpus_parser(sub) -> None:
    p_corpus = sub.add_parser(
        "corpus",
        help="查看推荐规范语料 profile；用于决定 ensure --profile 安装范围",
    )
    p_corpus.add_argument(
        "corpus_command",
        choices=["list", "show"],
        help="list 列 profile；show 展开一个或多个 profile。",
    )
    p_corpus.add_argument(
        "profiles",
        nargs="*",
        help="show 时指定 profile；为空默认 baseline。",
    )
    p_corpus.add_argument(
        "--no-deps",
        action="store_true",
        help="show 时不展开 dependencies。",
    )
    _add_format_arg(p_corpus)


def _add_schema_parser(sub) -> None:
    p_schema = sub.add_parser(
        "schema",
        help="输出 agent-facing CLI / MCP 机器可读契约（命令参数、风险、退出码）",
    )
    p_schema.add_argument(
        "target",
        nargs="*",
        help="可选命令路径，如 article、applicable、audit file；传 mcp 查看 MCP tools",
    )
    _add_format_arg(p_schema)


def _add_doctor_parser(sub) -> None:
    p_doctor = sub.add_parser(
        "doctor",
        help="检查全局安装、PATH、DB、schema、skills、MCP 和本地 grounding 健康",
    )
    p_doctor.add_argument(
        "--strict",
        action="store_true",
        help="warning 也视为失败；用于 CI / agent 前置门禁",
    )
    p_doctor.add_argument(
        "--source-smoke",
        choices=sorted(sources.ADAPTER_REGISTRY),
        help="可选联网 probe；默认不跑，避免 doctor 日常调用变慢",
    )
    _add_format_arg(p_doctor)


def _add_rebuild_clean_parser(sub) -> None:
    p_rebuild = sub.add_parser(
        "rebuild-clean",
        help="用当前 cleaning 规则重建已入库法规，避免 agent 调私有 helper/SQL",
    )
    rebuild_target = p_rebuild.add_mutually_exclusive_group()
    rebuild_target.add_argument("--law", help="仅重建指定法规 id / 全称 / 简称 / alias")
    rebuild_target.add_argument("--norm", help="仅重建指定私域规范 id / 名称 / 简称 / alias")
    p_rebuild.add_argument(
        "--dry-run",
        action="store_true",
        help="只报告会发生的清洗变化，不写入数据库",
    )
    p_rebuild.add_argument(
        "--limit",
        type=int,
        help="最多处理多少部法规；调试用",
    )
    _add_format_arg(p_rebuild)


def _add_status_and_time_parsers(sub) -> None:
    p_status = sub.add_parser("status", help="数据健康与新鲜度报告")
    _add_format_arg(p_status)

    p_history = sub.add_parser("history", help="查看法规版本历史")
    p_history.add_argument("name", help="法规 id / 全称 / 简称")
    _add_format_arg(p_history)
    _add_snapshot_out_arg(p_history)

    p_diff = sub.add_parser("diff", help="对比法规两个时点的版本差异")
    p_diff.add_argument("name", help="法规 id / 全称 / 简称")
    p_diff.add_argument("--from-as-of", required=True, help="起始版本日期（YYYY-MM-DD）")
    p_diff.add_argument("--to-as-of", required=True, help="目标版本日期（YYYY-MM-DD）")
    _add_format_arg(p_diff)
    _add_snapshot_out_arg(p_diff)

    p_trace = sub.add_parser("trace", help="条文级追溯：旧条号 / 现行条号跨版本映射")
    p_trace.add_argument("name", help="法规 id / 全称 / 简称")
    p_trace.add_argument(
        "number",
        nargs="?",
        help="起始版本条号，如 257；也可省略并用 --text 定位",
    )
    p_trace.add_argument("--text", help="用条文片段定位起始版本条文")
    p_trace.add_argument("--from-as-of", required=True, help="起始版本日期（YYYY-MM-DD）")
    p_trace.add_argument("--to-as-of", required=True, help="目标版本日期（YYYY-MM-DD）")
    p_trace.add_argument(
        "--items",
        help="限定款项 / 项号，如 '3,5'；用于比对司法解释引用的旧法项号",
    )
    p_trace.add_argument("--limit", type=int, default=5, help="候选对应条文数量")
    _add_format_arg(p_trace)
    _add_snapshot_out_arg(p_trace)

    p_relation = sub.add_parser("relation", help="查看一部规范与其它规范的替代 / 关联线索")
    p_relation.add_argument("name", help="法规 id / 全称 / 简称")
    _add_format_arg(p_relation)
    _add_snapshot_out_arg(p_relation)

    p_applicable = sub.add_parser("applicable", help="按日期 / 主题查询时间效力规则线索")
    p_applicable.add_argument("--date", required=True, help="事实或争议时点（YYYY-MM-DD）")
    p_applicable.add_argument("--topic", help="主题，如 合同效力")
    p_applicable.add_argument("--law", help="限定相关法规 id / 全称 / 简称")
    p_applicable.add_argument("--domain", help="场景，如 litigation / contract_review")
    _add_format_arg(p_applicable)
    _add_snapshot_out_arg(p_applicable)

    p_probe = sub.add_parser("probe", help="探测外部数据源页面结构（只读）")
    p_probe.add_argument(
        "source",
        choices=sorted(sources.ADAPTER_REGISTRY),
        help="数据源名称（来自 sources.ADAPTER_REGISTRY，新源接入即自动可见）",
    )
    _add_format_arg(p_probe)

    p_verify_source = sub.add_parser(
        "verify-source",
        help="真实数据源 smoke：probe → search → fetch/clean → article locate（只读）",
    )
    # verify-source choices 由 sources.VERIFIABLE_SOURCES 驱动；adapter 必须同时
    # 实装 search_list / build_law_payload。详见 ADR-0008 §3.2。
    p_verify_source.add_argument(
        "source",
        choices=list(sources.VERIFIABLE_SOURCES),
        help="数据源名称",
    )
    p_verify_source.add_argument(
        "--query",
        default="中华人民共和国民法典",
        help="用于 smoke 的法规检索词（默认：中华人民共和国民法典）",
    )
    p_verify_source.add_argument(
        "--article",
        default="第一条",
        help="用于 smoke 的条文号；传空字符串则跳过条文定位",
    )
    p_verify_source.add_argument(
        "--limit",
        type=int,
        default=5,
        help="搜索候选上限（默认 5）",
    )
    _add_format_arg(p_verify_source)


def _add_norm_parser(sub) -> None:
    p_norm = sub.add_parser("norm", help="私域规范导入、导出与查看")
    norm_sub = p_norm.add_subparsers(dest="norm_command", metavar="<norm_command>")

    p_norm_list = norm_sub.add_parser("list", help="列出本地私域规范")
    _add_format_arg(p_norm_list)

    p_norm_show = norm_sub.add_parser("show", help="查看私域规范全文")
    p_norm_show.add_argument("name", help="私域规范 id / 名称")
    _add_format_arg(p_norm_show)

    p_norm_clause = norm_sub.add_parser("clause", help="按规范名 + 条款号定位私域规范条款")
    p_norm_clause.add_argument("name", help="私域规范 id / 名称")
    p_norm_clause.add_argument("number", help="条款号，如 '2.1' 或 '第二条'")
    _add_format_arg(p_norm_clause)
    _add_snapshot_out_arg(p_norm_clause)

    p_norm_import = norm_sub.add_parser("import", help="从 JSON 文件导入私域规范")
    p_norm_import.add_argument("file", help="私域规范 JSON 文件路径")
    _add_format_arg(p_norm_import)

    p_norm_ingest = norm_sub.add_parser("ingest", help="从 txt/md/docx/pdf 自动切分并导入私域规范")
    p_norm_ingest.add_argument("file", help="私域规范 txt/md/docx/pdf 文件路径")
    p_norm_ingest.add_argument("--name", required=True, help="私域规范名称")
    p_norm_ingest.add_argument("--id", dest="source_id", help="私域规范稳定 ID")
    p_norm_ingest.add_argument("--short-name", help="简称")
    p_norm_ingest.add_argument("--source-type", default="private_policy", help="规范来源类型")
    p_norm_ingest.add_argument("--authority", help="制定主体")
    p_norm_ingest.add_argument("--binding-scope", help="约束范围")
    p_norm_ingest.add_argument("--jurisdiction", help="适用区域")
    p_norm_ingest.add_argument("--effective-at", help="生效日期")
    p_norm_ingest.add_argument("--repealed-at", help="失效日期")
    p_norm_ingest.add_argument("--source-url", help="来源 URL")
    p_norm_ingest.add_argument(
        "--source-name",
        help="来源名称 / 文件名；默认使用待导入文件路径",
    )
    p_norm_ingest.add_argument(
        "--source-checked-at",
        help="来源核查时间（ISO 8601）；适合固定第三方转载核验时间",
    )
    p_norm_ingest.add_argument(
        "--source-hash",
        help="调用方已计算的来源内容哈希；不传则按规范 payload 自动计算",
    )
    p_norm_ingest.add_argument(
        "--alias",
        dest="aliases",
        action="append",
        default=[],
        help="私域规范别名；可重复传入",
    )
    p_norm_ingest.add_argument(
        "--metadata-json",
        help="额外 metadata JSON object 字符串，会与自动 ingest 元数据合并",
    )
    p_norm_ingest.add_argument(
        "--metadata-file",
        help="额外 metadata JSON object 文件路径，会与自动 ingest 元数据合并",
    )
    p_norm_ingest.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "仅切分预览，不入库；输出每条编号 / 字数 / 120 字预览，"
            "并在标题层级或编号格式异常时显式 warning"
        ),
    )
    _add_format_arg(p_norm_ingest)

    p_norm_export = norm_sub.add_parser("export", help="导出私域规范 JSON")
    p_norm_export.add_argument("name", help="私域规范 id / 名称")
    _add_format_arg(p_norm_export)


def _add_commentary_parser(sub) -> None:
    p_commentary = sub.add_parser(
        "commentary",
        help="导入和查询法条级本地释义 / 注释材料（local-only）",
    )
    commentary_sub = p_commentary.add_subparsers(
        dest="commentary_command",
        metavar="<commentary_command>",
    )

    p_books = commentary_sub.add_parser("books", help="列出本地 commentary 书目")
    _add_format_arg(p_books)

    p_import = commentary_sub.add_parser("import", help="导入 commentary bundle JSON")
    p_import.add_argument("file", help="commentary bundle JSON 文件路径")
    _add_format_arg(p_import)

    p_article = commentary_sub.add_parser(
        "article",
        help="按法规名 + 条号查询本地 commentary",
    )
    p_article.add_argument("law", help="法规 id / 全称 / 简称")
    p_article.add_argument("number", help="条款号，如 '143' 或 '第一百四十三条'")
    p_article.add_argument("--limit", type=int, default=10, help="返回 commentary 上限")
    _add_format_arg(p_article)


def _add_pack_parser(sub) -> None:
    p_pack = sub.add_parser("pack", help="规范包导入、导出与查看")
    pack_sub = p_pack.add_subparsers(dest="pack_command", metavar="<pack_command>")

    p_pack_list = pack_sub.add_parser("list", help="列出本地规范包")
    _add_format_arg(p_pack_list)

    p_pack_show = pack_sub.add_parser("show", help="查看规范包及已解析成员")
    p_pack_show.add_argument("name", help="规范包 id / 名称")
    _add_format_arg(p_pack_show)

    p_pack_add = pack_sub.add_parser(
        "add",
        help="向规范包追加一个成员，用于 agent 工作流沉淀",
    )
    p_pack_add.add_argument("name", help="规范包 id / 名称；配合 --create 可新建")
    p_pack_add.add_argument(
        "--create",
        action="store_true",
        help="规范包不存在时创建",
    )
    p_pack_add.add_argument(
        "--type",
        required=True,
        choices=[
            "law",
            "article",
            "norm_source",
            "norm-source",
            "norm_clause",
            "norm-clause",
            "reference",
        ],
        help="成员类型",
    )
    p_pack_add.add_argument("--law", help="公开法规 id / 全称 / 简称 / alias")
    p_pack_add.add_argument("--article", help="公开法规条款号")
    p_pack_add.add_argument("--norm", help="私域规范 id / 名称")
    p_pack_add.add_argument("--clause", help="私域规范条款号")
    p_pack_add.add_argument("--text", help="reference 文本")
    p_pack_add.add_argument(
        "--role",
        choices=["core", "important", "supporting", "background"],
        default="supporting",
        help="成员角色（默认 supporting）",
    )
    p_pack_add.add_argument("--reason", help="纳入理由；core / important 建议必填")
    p_pack_add.add_argument("--note", help="备注")
    p_pack_add.add_argument("--summary", help="新建规范包时的摘要")
    p_pack_add.add_argument("--scope", help="新建规范包时的适用范围")
    p_pack_add.add_argument("--maintainer", help="新建规范包时的维护者")
    p_pack_add.add_argument(
        "--allow-unresolved",
        action="store_true",
        help="允许加入当前本地库尚不能解析的 law/article/norm item",
    )
    _add_format_arg(p_pack_add)

    p_pack_import = pack_sub.add_parser("import", help="从 JSON 文件导入规范包")
    p_pack_import.add_argument("file", help="规范包 JSON 文件路径")
    _add_format_arg(p_pack_import)

    p_pack_export = pack_sub.add_parser("export", help="导出规范包 JSON")
    p_pack_export.add_argument("name", help="规范包 id / 名称")
    _add_format_arg(p_pack_export)

    p_pack_validate = pack_sub.add_parser("validate", help="校验规范包成员和依赖是否可解析")
    p_pack_validate.add_argument("target", help="规范包 id / 名称，或配合 --file 指向 JSON 文件")
    p_pack_validate.add_argument(
        "--file", action="store_true", help="把 target 当作规范包 JSON 文件"
    )
    _add_format_arg(p_pack_validate)


def _add_audit_parser(sub) -> None:
    p_audit = sub.add_parser(
        "audit",
        help="审查文件 / 规范包 / 私域规范中的法规引用是否可解析、文本是否一致",
    )
    audit_sub = p_audit.add_subparsers(dest="audit_command", metavar="<audit_command>")

    p_file = audit_sub.add_parser("file", help="审查 txt/md/docx/pdf 文件中的法规引用")
    p_file.add_argument("path", help="待审查文件路径")
    p_file.add_argument("--as-of", help="按 YYYY-MM-DD 锁定事实时点版本")
    p_file.add_argument(
        "--strict",
        action="store_true",
        help="严格模式：warning 也提升为 error，用作 agent 门禁",
    )
    _add_format_arg(p_file)

    p_pack = audit_sub.add_parser("pack", help="审查规范包成员和 reference 法规引用")
    p_pack.add_argument("name", help="规范包 id / 名称")
    p_pack.add_argument("--as-of", help="按 YYYY-MM-DD 锁定事实时点版本")
    p_pack.add_argument(
        "--strict",
        action="store_true",
        help="严格模式：warning 也提升为 error，用作 agent 门禁",
    )
    _add_format_arg(p_pack)

    p_norm = audit_sub.add_parser("norm", help="审查私域规范条款内嵌的公开法引用")
    p_norm.add_argument("name", help="私域规范 id / 名称")
    p_norm.add_argument("--as-of", help="按 YYYY-MM-DD 锁定事实时点版本")
    p_norm.add_argument(
        "--strict",
        action="store_true",
        help="严格模式：warning 也提升为 error，用作 agent 门禁",
    )
    _add_format_arg(p_norm)

    p_grounding = audit_sub.add_parser(
        "grounding",
        help="用项目检索快照审查最终文本是否有可追溯依据",
    )
    p_grounding.add_argument("path", help="待审查最终文本 / Markdown / docx / pdf")
    p_grounding.add_argument(
        "--snapshot",
        help=(
            "检索快照 JSONL；默认读 CHINALAW_SNAPSHOT_OUT，或 "
            "CHINALAW_PROJECT/.chinalaw/snapshots/latest.jsonl"
        ),
    )
    p_grounding.add_argument("--as-of", help="按 YYYY-MM-DD 锁定事实时点版本")
    p_grounding.add_argument(
        "--strict",
        action="store_true",
        help="严格模式：retrieved_only / 时间效力 warning 也提升为 error",
    )
    _add_format_arg(p_grounding)


def _add_cite_check_parser(sub) -> None:
    p_cite = sub.add_parser(
        "cite-check",
        help="快捷审查文件中的法规引用；等价于 audit file，可选 grounding snapshot",
    )
    p_cite.add_argument("path", help="待审查 txt/md/docx/pdf 文件路径")
    p_cite.add_argument("--as-of", help="按 YYYY-MM-DD 锁定事实时点版本")
    p_cite.add_argument(
        "--strict",
        action="store_true",
        help="严格模式：warning 也提升为 error，用作 agent 门禁",
    )
    p_cite.add_argument(
        "--grounding",
        action="store_true",
        help="改为执行 audit grounding，核对项目检索快照证据链",
    )
    p_cite.add_argument(
        "--snapshot",
        help="配合 --grounding 指定检索快照 JSONL",
    )
    _add_format_arg(p_cite)


def _add_snapshot_parser(sub) -> None:
    p_snapshot = sub.add_parser(
        "snapshot",
        help="管理项目级检索快照，用于 audit grounding 证据链审查",
    )
    snapshot_sub = p_snapshot.add_subparsers(
        dest="snapshot_command",
        metavar="<snapshot_command>",
    )

    p_init = snapshot_sub.add_parser(
        "init",
        help="在项目目录创建 .chinalaw/snapshots/latest.jsonl 并启用自动记录",
    )
    p_init.add_argument(
        "project",
        nargs="?",
        default=".",
        help="项目目录（默认当前目录）",
    )
    p_init.add_argument(
        "--reset",
        action="store_true",
        help="清空已有 latest.jsonl；谨慎用于新一轮工作流",
    )
    _add_format_arg(p_init)

    p_status = snapshot_sub.add_parser(
        "status",
        help="查看项目快照记录数量、命令分布和证据等级",
    )
    p_status.add_argument(
        "project",
        nargs="?",
        default=".",
        help="项目目录（默认当前目录）",
    )
    p_status.add_argument(
        "--snapshot",
        help="直接指定 JSONL 快照；不传则从项目目录向上查找 latest.jsonl",
    )
    _add_format_arg(p_status)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="chinalaw",
        description="中国法律法规检索 CLI",
    )
    parser.add_argument("--version", action="version", version=f"chinalaw {__version__}")
    parser.add_argument(
        "--db",
        dest="global_db",
        default=None,
        help=f"SQLite 数据库路径（默认 {DEFAULT_DB_PATH}）；也可放在子命令之后",
    )
    parser.add_argument(
        "--no-notice",
        action="store_true",
        help=(
            "JSON 输出不附加非阻塞 _notice；也可设置 "
            "CHINALAW_NO_NOTICE=1"
        ),
    )

    sub = parser.add_subparsers(dest="command", metavar="<command>")
    _add_search_parser(sub)
    _add_read_parsers(sub)
    _add_sync_parser(sub)
    _add_fetch_parser(sub)
    _add_discover_parser(sub)
    _add_ensure_parser(sub)
    _add_corpus_parser(sub)
    _add_schema_parser(sub)
    _add_doctor_parser(sub)
    _add_rebuild_clean_parser(sub)
    _add_status_and_time_parsers(sub)
    _add_norm_parser(sub)
    _add_commentary_parser(sub)
    _add_pack_parser(sub)
    _add_cite_check_parser(sub)
    _add_audit_parser(sub)
    _add_snapshot_parser(sub)
    return parser


def _emit(payload, fmt: str, md_fn) -> None:
    if fmt in {"md", "card"}:
        sys.stdout.write(md_fn(payload))
    else:
        if _NOTICE_CONTEXT:
            payload = notices.attach_notices(
                payload,
                db_path=_NOTICE_CONTEXT.get("db_path") or DEFAULT_DB_PATH,
                command=str(_NOTICE_CONTEXT.get("command") or ""),
                disabled_by_flag=bool(_NOTICE_CONTEXT.get("disabled")),
            )
        sys.stdout.write(formatters.to_json(payload))
        sys.stdout.write("\n")


def _snapshot_to_markdown(payload: dict) -> str:
    lines = [
        "# chinalaw snapshot",
        f"- 项目：{payload.get('project_path') or '无'}",
        f"- 快照：{payload.get('snapshot_path') or '无'}",
        f"- 已启用：{'是' if payload.get('exists') else '否'}",
        f"- 记录数：{payload.get('record_count', 0)}",
        f"- 写入模式：{payload.get('write_mode') or '无'}",
    ]
    if payload.get("first_timestamp"):
        lines.append(f"- 首条：{payload.get('first_timestamp')}")
    if payload.get("last_timestamp"):
        lines.append(f"- 末条：{payload.get('last_timestamp')}")
    commands = payload.get("commands") or {}
    if commands:
        lines.append("")
        lines.append("## Commands")
        for command, count in sorted(commands.items()):
            lines.append(f"- {command}: {count}")
    evidence_levels = payload.get("evidence_levels") or {}
    if evidence_levels:
        lines.append("")
        lines.append("## Evidence Levels")
        for level, count in sorted(evidence_levels.items()):
            lines.append(f"- {level}: {count}")
    return "\n".join(lines) + "\n"


def _record_snapshot(args, db_path: Path, command: str, payload: dict) -> None:
    snapshot_path = snapshots.resolve_snapshot_out(
        getattr(args, "snapshot_out", None),
        anchor=Path.cwd(),
    )
    if snapshot_path is None:
        return
    snapshots.append_command_record(
        snapshot_path,
        command=command,
        payload=payload,
        db_path=db_path,
        argv=getattr(args, "_raw_argv", []) or [],
    )


def _load_json_object_text(text: str, *, label: str) -> dict:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} must be valid JSON: {exc.msg}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be a JSON object")
    return payload


def _merge_json_objects(base: dict, extra: dict) -> dict:
    merged = dict(base)
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_json_objects(merged[key], value)
        else:
            merged[key] = value
    return merged


def _load_norm_ingest_metadata(args) -> dict:
    metadata: dict = {}
    if getattr(args, "metadata_file", None):
        path = Path(args.metadata_file)
        metadata = _merge_json_objects(
            metadata,
            _load_json_object_text(path.read_text(encoding="utf-8"), label="--metadata-file"),
        )
    if getattr(args, "metadata_json", None):
        metadata = _merge_json_objects(
            metadata,
            _load_json_object_text(args.metadata_json, label="--metadata-json"),
        )
    return metadata


def _handle_search(args, db_path: Path, fmt: str, parser: argparse.ArgumentParser) -> int:
    query = " ".join(args.query) if isinstance(args.query, list) else args.query
    result = service.search(
        db_path,
        query,
        limit=args.limit,
        kind=args.kind,
        in_laws=args.in_laws,
        in_part=args.in_part,
    )
    _record_snapshot(args, db_path, "search", result)
    _emit(result, fmt, formatters.search_to_markdown)
    return 0


def _handle_get(args, db_path: Path, fmt: str, parser: argparse.ArgumentParser) -> int:
    law = (
        service.get_law_as_of(db_path, args.name, args.as_of)
        if args.as_of
        else service.get_law(db_path, args.name)
    )
    if law is None:
        _emit(
            {"found": False, "name": args.name},
            fmt,
            lambda _: formatters.law_to_markdown(None),
        )
        return 1
    _record_snapshot(args, db_path, "get", law)
    _emit(law, fmt, formatters.law_to_markdown)
    return 0


_LEVEL_LABELS = {
    "law": "法律",
    "judicial_interpretation": "司法解释",
    "judicial_meeting_minutes": "司法会议纪要",
    "administrative_regulation": "行政法规",
    "departmental_rule": "部门规章",
    "local_regulation": "地方性法规",
    "constitution": "宪法",
}

_VIA_LABELS = {
    "id_match": "id 精确",
    "title_match": "全名精确",
    "short_title_match": "短称精确",
    "alias_exact": "alias 列表精确",
    "alias_derived": "规则派生 alias",
    "like_fallback": "模糊匹配（最后兜底）",
}


def _resolve_to_markdown(payload: dict) -> str:
    if not payload.get("matched"):
        lines = [
            f"- 输入：{payload.get('input') or '—'}",
            "- 命中：未找到",
            "- 提示：试 `chinalaw fetch <俗称> --list-matches` 列候选",
        ]
        return "\n".join(lines) + "\n"

    level = payload.get("level") or "?"
    via = payload.get("via") or "?"
    issuer = payload.get("issuing_body") or "?"
    lines = [
        f"- 输入：{payload.get('input')}",
        f"- 正式：{payload.get('official_title')}",
        f"- 短称：{payload.get('short_title') or '—'}",
        f"- 效力层级：{_LEVEL_LABELS.get(level, level)}（{issuer}发布）",
        f"- 状态：{payload.get('status') or '?'}",
        f"- 命中路径：{via}（{_VIA_LABELS.get(via, '?')}）",
    ]
    return "\n".join(lines) + "\n"


def _handle_resolve(args, db_path: Path, fmt: str, parser: argparse.ArgumentParser) -> int:
    payload = service.resolve(db_path, args.name)
    _emit(payload, fmt, _resolve_to_markdown)
    return 0 if payload.get("matched") else 1


def _handle_article(args, db_path: Path, fmt: str, parser: argparse.ArgumentParser) -> int:
    include_norm = not getattr(args, "no_norm_fallback", False)
    payload = (
        service.get_article_as_of(
            db_path, args.name, args.number, args.as_of, include_norm=include_norm
        )
        if args.as_of
        else service.get_article(db_path, args.name, args.number, include_norm=include_norm)
    )
    if payload is None:
        diag = service.diagnose_article_miss(db_path, args.name, args.number, as_of=args.as_of)
        miss_payload = {
            "found": False,
            "name": args.name,
            "number": args.number,
            **diag,
        }

        def _miss_md(_payload: dict) -> str:
            base = formatters.article_to_markdown(None)
            hint = diag.get("hint")
            reason = diag.get("reason", "?")
            if hint:
                return base + f"\n> 诊断 [{reason}]: {hint}\n"
            return base

        renderer = formatters.article_to_card if fmt == "card" else _miss_md
        _emit(miss_payload, fmt, renderer)
        return 1
    footer = "none" if args.no_footer else "compact" if args.compact else "full"
    number_style = "section" if args.section else "arabic" if args.arabic else "display"
    article_missing = payload.get("article") is None
    if article_missing:
        # Law resolved but article body missing → law_stub or article_null.
        # Enrich payload with diagnosis + suggested fetch so callers don't
        # need a follow-up status / outline call.
        diag = service.diagnose_article_miss(db_path, args.name, args.number, as_of=args.as_of)
        payload = {**payload, **diag, "found": False}
    if fmt == "card":
        renderer = lambda p: formatters.article_to_card(p)  # noqa: E731
    elif args.bare:
        renderer = lambda p: formatters.article_to_bare(p)  # noqa: E731
    elif args.inline:
        renderer = lambda p: formatters.article_to_inline(p)  # noqa: E731
    else:

        def renderer(p: dict) -> str:
            md = formatters.article_to_markdown(
                p,
                footer=footer,
                number_style=number_style,
                with_title=args.with_title,
            )
            if article_missing:
                hint = (p or {}).get("hint")
                reason = (p or {}).get("reason", "?")
                if hint:
                    md = md.rstrip("\n") + f"\n\n> 诊断 [{reason}]: {hint}\n"
            return md

    _record_snapshot(args, db_path, "article", payload)
    _emit(payload, fmt, renderer)
    return 0 if payload.get("article") else 1


def _handle_articles(args, db_path: Path, fmt: str, parser: argparse.ArgumentParser) -> int:
    footer = "none" if args.no_footer else "compact" if args.compact else "full"
    number_style = "section" if args.section else "arabic" if args.arabic else "display"
    include_norm = not getattr(args, "no_norm_fallback", False)

    if args.batch:
        payload = service.get_articles_batch(
            db_path, args.batch, as_of=args.as_of, include_norm=include_norm
        )
        if payload is None:
            _emit(
                {
                    "kind": "law_articles_batch_error",
                    "error": "MissingBatchSpec",
                    "message": "articles --batch 解析为空，期望 'law1:nums1;law2:nums2' 形式",
                },
                fmt,
                lambda m: f"! {m['error']}: {m['message']}\n",
            )
            return 2
        if args.bare:
            renderer = lambda p: formatters.articles_batch_to_bare(p)  # noqa: E731
        elif args.inline:
            renderer = lambda p: formatters.articles_batch_to_inline(p)  # noqa: E731
        else:
            renderer = lambda p: formatters.articles_batch_to_markdown(  # noqa: E731
                p,
                number_style=number_style,
                footer=footer,
                with_title=args.with_title,
            )
        _record_snapshot(args, db_path, "articles", payload)
        _emit(payload, fmt, renderer)
        return 0 if payload.get("ok") else 1

    if not args.name:
        _emit(
            {
                "kind": "law_articles_error",
                "error": "MissingLaw",
                "message": "articles requires law name (or use --batch for multi-law)",
            },
            fmt,
            lambda m: f"! {m['error']}: {m['message']}\n",
        )
        return 2

    numbers = args.numbers or args.numbers_arg
    if not numbers:
        _emit(
            {
                "kind": "law_articles_error",
                "error": "MissingNumbers",
                "message": "articles requires numbers positional argument or --numbers",
            },
            fmt,
            lambda m: f"! {m['error']}: {m['message']}\n",
        )
        return 2
    payload = service.get_articles(
        db_path,
        args.name,
        numbers,
        as_of=args.as_of,
        include_norm=include_norm,
    )
    if payload is None:
        _emit(
            {"found": False, "name": args.name, "numbers": numbers},
            fmt,
            lambda _: formatters.articles_to_markdown(None),
        )
        return 1
    if args.bare:
        renderer = lambda p: formatters.articles_to_bare(p)  # noqa: E731
    elif args.inline:
        renderer = lambda p: formatters.articles_to_inline(p)  # noqa: E731
    else:
        renderer = lambda p: formatters.articles_to_markdown(  # noqa: E731
            p,
            number_style=number_style,
            footer=footer,
            with_title=args.with_title,
        )
    _record_snapshot(args, db_path, "articles", payload)
    _emit(payload, fmt, renderer)
    return 0 if payload.get("missing_count", 0) == 0 else 1


def _handle_outline(args, db_path: Path, fmt: str, parser: argparse.ArgumentParser) -> int:
    payload = service.outline_law(
        db_path,
        args.name,
        part=args.part,
        preview_chars=args.preview_chars,
        with_text=args.with_text,
    )
    if payload is None:
        _emit(
            {"found": False, "name": args.name},
            fmt,
            lambda _: formatters.outline_to_markdown(None),
        )
        return 1
    _record_snapshot(args, db_path, "outline", payload)
    if args.with_text:
        if args.compact:
            footer = "compact"
        elif args.no_footer:
            footer = "none"
        else:
            footer = "full"
        if args.section:
            number_style = "section"
        elif args.arabic:
            number_style = "arabic"
        else:
            number_style = "display"
        if args.bare:
            renderer = lambda p: formatters.articles_to_bare(p)  # noqa: E731
        elif args.inline:
            renderer = lambda p: formatters.articles_to_inline(p)  # noqa: E731
        else:
            renderer = lambda p: formatters.outline_to_markdown_with_text(  # noqa: E731
                p,
                number_style=number_style,
                footer=footer,
                with_title=args.with_title,
            )
        _emit(payload, fmt, renderer)
    else:
        _emit(payload, fmt, formatters.outline_to_markdown)
    return 0


def _handle_cited_by(args, db_path: Path, fmt: str, parser: argparse.ArgumentParser) -> int:
    spec = service.parse_cited_by_spec(args.spec)
    if spec is None:
        _emit(
            {
                "kind": "law_article_cited_by",
                "spec": args.spec,
                "error": "InvalidSpec",
                "message": "无法解析 spec，期望 `民法典:522` / `民法典：第522条`",
                "hits": [],
                "hit_count": 0,
            },
            fmt,
            formatters.cited_by_to_markdown,
        )
        return 2
    law_identifier, number = spec
    payload = service.find_cited_by(
        db_path,
        law_identifier,
        number,
        in_laws=args.in_laws,
        include_self=args.include_self,
        limit=args.limit,
    )
    if payload is None:
        _emit(
            {
                "kind": "law_article_cited_by",
                "spec": args.spec,
                "error": "TargetNotFound",
                "message": f"未找到目标法规或条号：{law_identifier}:{number}",
                "hits": [],
                "hit_count": 0,
            },
            fmt,
            formatters.cited_by_to_markdown,
        )
        return 1
    _emit(payload, fmt, formatters.cited_by_to_markdown)
    return 0


def _handle_list(args, db_path: Path, fmt: str, parser: argparse.ArgumentParser) -> int:
    laws = service.list_laws(
        db_path,
        level=args.level,
        status=args.status,
        limit=args.limit,
    )
    _emit(laws, fmt, formatters.list_to_markdown)
    return 0


def _handle_sync(args, db_path: Path, fmt: str, parser: argparse.ArgumentParser) -> int:
    if args.applicability:
        result = applicability.load_applicability_fixtures(
            db_path,
            Path(args.applicability_dir) if args.applicability_dir else None,
        )
    elif args.from_dir:
        paths = sorted(Path(args.from_dir).glob("*.json"))
        result = loader.load_files(db_path, paths)
    elif args.fixtures:
        result = loader.load_fixtures(db_path)
    elif args.source:
        result = sync_source(
            db_path,
            source=args.source,
            query=args.query,
            bbbs=args.bbbs,
            limit=args.limit,
            batch=args.batch,
            start_page=args.start_page,
            max_pages=args.max_pages,
            page_size=args.page_size,
            resume=args.resume,
            stop_after_stable_pages=args.stop_after_stable_pages,
            incremental=args.incremental,
            published_from=args.published_from,
            published_to=args.published_to,
            days_back=args.days_back,
            overlap_days=args.overlap_days,
        )
    else:
        msg = {
            "status": "noop",
            "message": (
                "请提供 --fixtures、--from-dir，或使用 --source flk_npc 配合 "
                "--query / --bbbs / --batch。"
            ),
        }
        _emit(msg, fmt, lambda m: f"> {m['message']}\n")
        return 2
    _emit(result, fmt, _sync_to_markdown)
    return 0


def _handle_fetch(args, db_path: Path, fmt: str, parser: argparse.ArgumentParser) -> int:
    try:
        result = fetch_mod.fetch_law(
            db_path,
            args.name,
            source=args.source,
            article=args.article,
            dry_run=args.dry_run,
            to_fixture=args.to_fixture,
            list_matches=args.list_matches,
            prefer_bbbs=args.prefer_bbbs,
            limit=args.limit,
            force=args.force,
            status=args.status,
        )
    except fetch_mod.FetchError as exc:
        error_payload = {
            "kind": "law_fetch_error",
            "error": exc.__class__.__name__,
            "message": str(exc),
        }
        candidates = getattr(exc, "candidates", None)
        if candidates:
            error_payload["candidates"] = candidates
        _emit(
            error_payload,
            fmt,
            lambda m: f"! {m['error']}: {m['message']}\n",
        )
        return exc.exit_code
    except ValueError as exc:
        # CLI ``--status`` fail loud：非 flk 源传入 status 时 fetch_law 抛
        # ValueError，按 ensure / pack 错误处理风格 emit + 退 2。详见
        # docs/CLI_STATUS_FLAG_SPEC.md §3.5.3 / §4。
        _emit(
            {
                "kind": "law_fetch_error",
                "error": exc.__class__.__name__,
                "message": str(exc),
            },
            fmt,
            lambda m: f"! {m['error']}: {m['message']}\n",
        )
        return 2
    _record_snapshot(args, db_path, "fetch", result)
    _emit(result, fmt, _fetch_to_markdown)
    return 0


def _discover_to_markdown(payload: dict) -> str:
    lines = [
        f"# discover：{payload.get('source', '')}",
        f"- 查询：{payload.get('query') or '(空)'}",
        f"- 状态：{payload.get('status') or '(全部)'}",
        f"- 候选数：{len(payload.get('candidates') or [])}",
        "",
    ]
    for cand in payload.get("candidates") or []:
        lines.append(
            f"- {cand.get('title', '')} — id=`{cand.get('id') or cand.get('bbbs', '')}`"
            f" / released={cand.get('released_at', '')}"
            f" / status={cand.get('status', '')}"
        )
    return "\n".join(lines) + "\n"


def _handle_discover(args, db_path: Path, fmt: str, parser: argparse.ArgumentParser) -> int:
    try:
        result = discover_mod.discover_laws(
            source=args.source,
            query=args.query,
            status=args.status,
            limit=args.limit,
        )
    except (ValueError, URLError, OSError, TimeoutError, json.JSONDecodeError) as exc:
        # transport / parse 异常族对齐 ``_handle_fetch`` 的 ``law_fetch_error``
        # envelope 契约（codex P2 fixup on PR #53；详见
        # ``docs/CLI_DISCOVER_ERROR_ENVELOPE_SPEC.md`` §2 方案 A1）。
        # ``URLError`` 一刀覆盖整 urllib 族（含 ``HTTPError`` 子类），
        # ``OSError`` 覆盖 socket / connection 族（含 ``ConnectionError``），
        # ``TimeoutError`` 在 Python 3.10+ 与 ``socket.timeout`` 别名等价。
        # ``json.JSONDecodeError`` 是 ``ValueError`` 子类，列入仅作 future-proof
        # 防御（adapter 当前 wrap 成 ``ValueError`` —— flk_npc.py:511-520 ——
        # 但 wrap 是 adapter 私有约定，未来重构时若直接冒出仍能命中）。
        # AttributeError / KeyError / TypeError 等编程错误**不接**——透传，
        # 与 PR5c / PR6 / PR-A 系列窄 except 立场一致。
        _emit(
            {
                "kind": "law_discover_error",
                "error": exc.__class__.__name__,
                "message": str(exc),
            },
            fmt,
            lambda m: f"! {m['error']}: {m['message']}\n",
        )
        return 2
    _emit(result, fmt, _discover_to_markdown)
    return 0


def _handle_ensure(args, db_path: Path, fmt: str, parser: argparse.ArgumentParser) -> int:
    try:
        if args.profile:
            if args.names or args.from_file or args.from_dir:
                raise ValueError(
                    "ensure --profile cannot be combined with names, --from-file, or --from-dir"
                )
            result = ensure.ensure_corpus_profiles(
                db_path,
                args.profile,
                include_dependencies=not args.no_profile_deps,
                limit=args.limit,
                interval=args.interval,
            )
            _emit(result, fmt, formatters.ensure_to_markdown)
            return 0 if result.get("ok") else 1

        names = ensure.collect_names(
            names=args.names,
            from_file=args.from_file,
            from_dir=args.from_dir,
        )
        result = ensure.ensure_laws(
            db_path,
            names,
            source=args.source,
            limit=args.limit,
            interval=args.interval,
        )
    except ValueError as exc:
        _emit(
            {
                "kind": "law_ensure_error",
                "error": exc.__class__.__name__,
                "message": str(exc),
            },
            fmt,
            lambda m: f"! {m['error']}: {m['message']}\n",
        )
        return 2
    _emit(result, fmt, formatters.ensure_to_markdown)
    return 0 if result.get("ok") else 1


def _handle_corpus(args, db_path: Path, fmt: str, parser: argparse.ArgumentParser) -> int:
    try:
        if args.corpus_command == "list":
            result = corpus_mod.list_profiles()
        elif args.corpus_command == "show":
            result = corpus_mod.resolve_profiles(
                args.profiles,
                include_dependencies=not args.no_deps,
            )
        else:
            parser.print_help()
            return 0
    except corpus_mod.CorpusError as exc:
        _emit(
            {
                "kind": "recommended_corpus_error",
                "error": exc.__class__.__name__,
                "message": str(exc),
            },
            fmt,
            lambda m: f"! {m['error']}: {m['message']}\n",
        )
        return 2
    _emit(result, fmt, _corpus_to_markdown)
    return 0


def _handle_schema(args, db_path: Path, fmt: str, parser: argparse.ArgumentParser) -> int:
    target = " ".join(getattr(args, "target", []) or []).strip()
    if not target:
        payload = metadata.schema_index_payload()
    elif target == "mcp":
        payload = metadata.mcp_schema_payload()
    else:
        payload = metadata.command_schema_payload(target)
        if payload is None:
            _emit(
                {
                    "kind": "cli_schema_error",
                    "error": "SchemaNotFound",
                    "target": target,
                    "message": f"未找到命令契约：{target}",
                },
                fmt,
                lambda m: f"! {m['error']}: {m['message']}\n",
            )
            return 1
    _emit(payload, fmt, _schema_to_markdown)
    return 0


def _handle_doctor(args, db_path: Path, fmt: str, parser: argparse.ArgumentParser) -> int:
    report = doctor.run_doctor(
        db_path,
        strict=args.strict,
        source_smoke=args.source_smoke,
    )
    _emit(report, fmt, _doctor_to_markdown)
    return 0 if report.get("ok") else 1


def _handle_rebuild_clean(
    args,
    db_path: Path,
    fmt: str,
    parser: argparse.ArgumentParser,
) -> int:
    result = rebuild.rebuild_clean(
        db_path,
        law=args.law,
        norm=args.norm,
        dry_run=args.dry_run,
        limit=args.limit,
    )
    _emit(result, fmt, formatters.rebuild_clean_to_markdown)
    return 0 if result.get("ok") else 1


def _handle_status(args, db_path: Path, fmt: str, parser: argparse.ArgumentParser) -> int:
    report = service.status(db_path)
    _emit(report, fmt, formatters.status_to_markdown)
    return 0


def _handle_history(args, db_path: Path, fmt: str, parser: argparse.ArgumentParser) -> int:
    report = service.history(db_path, args.name)
    if report is None:
        _emit(
            {"found": False, "name": args.name},
            fmt,
            lambda _: formatters.history_to_markdown(None),
        )
        return 1
    _record_snapshot(args, db_path, "history", report)
    _emit(report, fmt, formatters.history_to_markdown)
    return 0


def _handle_diff(args, db_path: Path, fmt: str, parser: argparse.ArgumentParser) -> int:
    report = service.diff_law_as_of(
        db_path,
        args.name,
        args.from_as_of,
        args.to_as_of,
    )
    if report is None:
        _emit(
            {"found": False, "name": args.name},
            fmt,
            lambda _: formatters.diff_to_markdown(None),
        )
        return 1
    _record_snapshot(args, db_path, "diff", report)
    _emit(report, fmt, formatters.diff_to_markdown)
    return 0


def _handle_trace(args, db_path: Path, fmt: str, parser: argparse.ArgumentParser) -> int:
    report = service.trace_article_as_of(
        db_path,
        args.name,
        args.number,
        text=args.text,
        from_as_of=args.from_as_of,
        to_as_of=args.to_as_of,
        items=args.items,
        limit=args.limit,
    )
    if report is None:
        _emit(
            {"found": False, "name": args.name},
            fmt,
            lambda _: formatters.trace_to_markdown(None),
        )
        return 1
    _record_snapshot(args, db_path, "trace", report)
    _emit(report, fmt, formatters.trace_to_markdown)
    return 0 if report.get("ok") else 1


def _handle_relation(args, db_path: Path, fmt: str, parser: argparse.ArgumentParser) -> int:
    report = service.relation(db_path, args.name)
    _record_snapshot(args, db_path, "relation", report)
    _emit(report, fmt, formatters.relation_to_markdown)
    return 0 if report.get("law") or report.get("relation_count", 0) else 1


def _handle_applicable(args, db_path: Path, fmt: str, parser: argparse.ArgumentParser) -> int:
    report = service.applicable(
        db_path,
        as_of=args.date,
        topic=args.topic,
        law=args.law,
        domain=args.domain,
    )
    _record_snapshot(args, db_path, "applicable", report)
    _emit(report, fmt, formatters.applicable_to_markdown)
    return 0 if report.get("ok", True) else 2


def _handle_probe(args, db_path: Path, fmt: str, parser: argparse.ArgumentParser) -> int:
    report = sources.probe_source(args.source)
    _emit(report, fmt, formatters.probe_to_markdown)
    return 0


def _handle_verify_source(
    args,
    db_path: Path,
    fmt: str,
    parser: argparse.ArgumentParser,
) -> int:
    report = sources.verify_source(
        args.source,
        query=args.query,
        article=args.article or None,
        limit=args.limit,
    )
    _emit(report, fmt, formatters.source_verify_to_markdown)
    return 0 if report.get("ok") else 2


def _handle_norm(args, db_path: Path, fmt: str, parser: argparse.ArgumentParser) -> int:
    if args.norm_command == "list":
        norms = normsources.list_sources(db_path)
        _emit(norms, fmt, formatters.norm_source_list_to_markdown)
        return 0
    if args.norm_command == "show":
        source = normsources.get_source(db_path, args.name)
        if source is None:
            _emit(
                {"found": False, "name": args.name},
                fmt,
                lambda _: formatters.norm_source_to_markdown(None),
            )
            return 1
        _emit(source, fmt, formatters.norm_source_to_markdown)
        return 0
    if args.norm_command == "clause":
        payload = normsources.get_clause(db_path, args.name, args.number)
        if payload is None:
            _emit(
                {"found": False, "name": args.name, "number": args.number},
                fmt,
                lambda _: formatters.norm_clause_to_markdown(None),
            )
            return 1
        _record_snapshot(args, db_path, "norm clause", payload)
        _emit(payload, fmt, formatters.norm_clause_to_markdown)
        return 0 if payload.get("clause") else 1
    if args.norm_command == "import":
        result = normsources.import_source_file(db_path, Path(args.file))
        _emit(result, fmt, formatters.norm_source_import_to_markdown)
        return 0
    if args.norm_command == "ingest":
        try:
            metadata = _load_norm_ingest_metadata(args)
            result = normsources.import_text_source_file(
                db_path,
                Path(args.file),
                name=args.name,
                source_id=args.source_id,
                short_name=args.short_name,
                source_type=args.source_type,
                authority=args.authority,
                binding_scope=args.binding_scope,
                jurisdiction=args.jurisdiction,
                effective_at=args.effective_at,
                repealed_at=args.repealed_at,
                source_url=args.source_url,
                source_name=args.source_name,
                source_checked_at=args.source_checked_at,
                source_hash=args.source_hash,
                aliases=args.aliases,
                metadata=metadata,
                dry_run=getattr(args, "dry_run", False),
            )
        except ValueError as exc:
            _emit(
                {
                    "kind": "norm_ingest_error",
                    "error": exc.__class__.__name__,
                    "message": str(exc),
                },
                fmt,
                lambda m: f"! {m['error']}: {m['message']}\n",
            )
            return 2
        if result.get("kind") == "norm_ingest_preview":
            _emit(result, fmt, formatters.norm_ingest_preview_to_markdown)
            warnings = result.get("warnings") or []
            return 0 if not warnings else 2
        _emit(result, fmt, formatters.norm_source_import_to_markdown)
        return 0
    if args.norm_command == "export":
        source = normsources.export_source(db_path, args.name)
        if source is None:
            _emit(
                {"found": False, "name": args.name},
                fmt,
                lambda _: formatters.norm_source_to_markdown(None),
            )
            return 1
        _emit(source, fmt, formatters.norm_source_to_markdown)
        return 0
    parser.print_help()
    return 0


def _handle_commentary(
    args,
    db_path: Path,
    fmt: str,
    parser: argparse.ArgumentParser,
) -> int:
    if args.commentary_command == "books":
        books = commentary.list_books(db_path)
        _emit(books, fmt, formatters.commentary_books_to_markdown)
        return 0
    if args.commentary_command == "import":
        try:
            result = commentary.import_bundle_file(db_path, Path(args.file))
        except ValueError as exc:
            _emit(
                {
                    "kind": "commentary_import_error",
                    "error": exc.__class__.__name__,
                    "message": str(exc),
                },
                fmt,
                lambda m: f"! {m['error']}: {m['message']}\n",
            )
            return 2
        _emit(result, fmt, formatters.commentary_import_to_markdown)
        return 0
    if args.commentary_command == "article":
        result = commentary.get_article_commentary(
            db_path,
            args.law,
            args.number,
            limit=args.limit,
        )
        _emit(result, fmt, formatters.commentary_article_to_markdown)
        return 0 if result.get("found") else 1
    parser.print_help()
    return 0


def _handle_pack(args, db_path: Path, fmt: str, parser: argparse.ArgumentParser) -> int:
    if args.pack_command == "list":
        packs = normpacks.list_packs(db_path)
        _emit(packs, fmt, formatters.pack_list_to_markdown)
        return 0
    if args.pack_command == "show":
        pack = normpacks.get_pack(db_path, args.name, resolve=True)
        if pack is None:
            _emit(
                {"found": False, "name": args.name},
                fmt,
                lambda _: formatters.pack_to_markdown(None),
            )
            return 1
        _emit(pack, fmt, formatters.pack_to_markdown)
        return 0
    if args.pack_command == "add":
        item_type = args.type.replace("-", "_")
        item = {
            "item_type": item_type,
            "role": args.role,
            "reason": args.reason,
            "note": args.note,
        }
        if item_type in {"law", "article"}:
            item["law_title"] = args.law
        if item_type == "article":
            item["article_number"] = args.article
        if item_type in {"norm_source", "norm_clause"}:
            item["norm_source_name"] = args.norm
        if item_type == "norm_clause":
            item["clause_number"] = args.clause
        if item_type == "reference":
            item["reference_text"] = args.text
        try:
            result = normpacks.add_item_to_pack(
                db_path,
                args.name,
                item,
                create=args.create,
                summary=args.summary,
                scope=args.scope,
                maintainer=args.maintainer,
                require_resolved=not args.allow_unresolved,
            )
        except normpacks.NormPackError as exc:
            _emit(
                {
                    "kind": "norm_pack_item_add_error",
                    "error": exc.__class__.__name__,
                    "message": str(exc),
                },
                fmt,
                lambda m: f"! {m['error']}: {m['message']}\n",
            )
            return exc.exit_code
        except ValueError as exc:
            _emit(
                {
                    "kind": "norm_pack_item_add_error",
                    "error": exc.__class__.__name__,
                    "message": str(exc),
                },
                fmt,
                lambda m: f"! {m['error']}: {m['message']}\n",
            )
            return 2
        if result is None:
            _emit(
                {"found": False, "name": args.name},
                fmt,
                lambda _: "_未找到该规范包；如需新建请传 --create。_\n",
            )
            return 1
        _emit(result, fmt, formatters.pack_item_add_to_markdown)
        return 0
    if args.pack_command == "import":
        result = normpacks.import_pack_file(db_path, Path(args.file))
        _emit(result, fmt, formatters.pack_import_to_markdown)
        return 0
    if args.pack_command == "export":
        pack = normpacks.export_pack(db_path, args.name)
        if pack is None:
            _emit(
                {"found": False, "name": args.name},
                fmt,
                lambda _: formatters.pack_to_markdown(None),
            )
            return 1
        _emit(pack, fmt, formatters.pack_to_markdown)
        return 0
    if args.pack_command == "validate":
        report = (
            normpacks.validate_pack_file(db_path, Path(args.target))
            if args.file
            else normpacks.validate_pack(db_path, args.target)
        )
        if report is None:
            _emit(
                {"found": False, "name": args.target},
                fmt,
                lambda _: "_未找到该规范包。_\n",
            )
            return 1
        _emit(report, fmt, formatters.pack_validation_to_markdown)
        return 0 if report.get("ok") else 1
    parser.print_help()
    return 0


def _handle_audit(args, db_path: Path, fmt: str, parser: argparse.ArgumentParser) -> int:
    try:
        if args.audit_command == "file":
            report = audit_mod.audit_file(
                db_path,
                Path(args.path),
                as_of=args.as_of,
                strict=args.strict,
            )
        elif args.audit_command == "pack":
            report = audit_mod.audit_pack(
                db_path,
                args.name,
                as_of=args.as_of,
                strict=args.strict,
            )
        elif args.audit_command == "norm":
            report = audit_mod.audit_norm(
                db_path,
                args.name,
                as_of=args.as_of,
                strict=args.strict,
            )
        elif args.audit_command == "grounding":
            report = audit_mod.audit_grounding_file(
                db_path,
                Path(args.path),
                snapshot_path=args.snapshot,
                as_of=args.as_of,
                strict=args.strict,
            )
        else:
            parser.print_help()
            return 0
    except ValueError as exc:
        _emit(
            {
                "kind": "audit_error",
                "error": exc.__class__.__name__,
                "message": str(exc),
            },
            fmt,
            lambda m: f"! {m['error']}: {m['message']}\n",
        )
        return 2
    _emit(report, fmt, formatters.audit_to_markdown)
    return 0 if report.get("ok") else 1


def _handle_cite_check(args, db_path: Path, fmt: str, parser: argparse.ArgumentParser) -> int:
    if args.snapshot and not args.grounding:
        _emit(
            {
                "kind": "audit_error",
                "error": "SnapshotRequiresGrounding",
                "message": "--snapshot requires --grounding for cite-check",
                "shortcut": "cite-check",
            },
            fmt,
            lambda m: f"! {m['error']}: {m['message']}\n",
        )
        return 2
    try:
        if args.grounding:
            report = audit_mod.audit_grounding_file(
                db_path,
                Path(args.path),
                snapshot_path=args.snapshot,
                as_of=args.as_of,
                strict=args.strict,
            )
            expanded = "audit grounding"
        else:
            report = audit_mod.audit_file(
                db_path,
                Path(args.path),
                as_of=args.as_of,
                strict=args.strict,
            )
            expanded = "audit file"
    except ValueError as exc:
        _emit(
            {
                "kind": "audit_error",
                "error": exc.__class__.__name__,
                "message": str(exc),
                "shortcut": "cite-check",
            },
            fmt,
            lambda m: f"! {m['error']}: {m['message']}\n",
        )
        return 2
    report = {
        **report,
        "shortcut": {
            "command": "cite-check",
            "expanded_command": expanded,
            "path": str(Path(args.path)),
            "evidence_chain_visible": True,
        },
    }
    _emit(report, fmt, formatters.audit_to_markdown)
    return 0 if report.get("ok") else 1


def _handle_snapshot(args, db_path: Path, fmt: str, parser: argparse.ArgumentParser) -> int:
    if args.snapshot_command == "init":
        report = snapshots.init_project_snapshot(args.project, reset=args.reset)
    elif args.snapshot_command == "status":
        if args.snapshot:
            snapshot_path = Path(args.snapshot).expanduser()
        else:
            snapshot_path = snapshots.resolve_snapshot_in(None, anchor=args.project)
            if snapshot_path is None:
                snapshot_path = snapshots.default_project_snapshot(
                    Path(args.project).expanduser().resolve()
                )
        report = snapshots.snapshot_status(snapshot_path, project_path=args.project)
    else:
        parser.print_help()
        return 0
    _emit(report, fmt, _snapshot_to_markdown)
    return 0


_COMMAND_HANDLERS = {
    "search": _handle_search,
    "get": _handle_get,
    "resolve": _handle_resolve,
    "article": _handle_article,
    "articles": _handle_articles,
    "outline": _handle_outline,
    "cited-by": _handle_cited_by,
    "list": _handle_list,
    "laws": _handle_list,
    "sync": _handle_sync,
    "fetch": _handle_fetch,
    "discover": _handle_discover,
    "ensure": _handle_ensure,
    "corpus": _handle_corpus,
    "schema": _handle_schema,
    "doctor": _handle_doctor,
    "rebuild-clean": _handle_rebuild_clean,
    "status": _handle_status,
    "history": _handle_history,
    "diff": _handle_diff,
    "trace": _handle_trace,
    "relation": _handle_relation,
    "applicable": _handle_applicable,
    "probe": _handle_probe,
    "verify-source": _handle_verify_source,
    "norm": _handle_norm,
    "commentary": _handle_commentary,
    "pack": _handle_pack,
    "cite-check": _handle_cite_check,
    "audit": _handle_audit,
    "snapshot": _handle_snapshot,
}


def app(argv: list[str] | None = None) -> int:
    parser = build_parser()
    raw_argv = list(argv) if argv is not None else sys.argv[1:]
    args = parser.parse_args(raw_argv)
    args._raw_argv = raw_argv

    if not args.command:
        parser.print_help()
        return 0

    db_path = Path(getattr(args, "db", None) or getattr(args, "global_db", None) or DEFAULT_DB_PATH)
    fmt = getattr(args, "format", "json")
    _NOTICE_CONTEXT.clear()
    _NOTICE_CONTEXT.update(
        {
            "db_path": db_path,
            "command": args.command,
            "disabled": bool(getattr(args, "no_notice", False)),
        }
    )
    handler = _COMMAND_HANDLERS.get(args.command)
    if handler is not None:
        try:
            return handler(args, db_path, fmt, parser)
        except BrokenPipeError:
            _suppress_broken_pipe()
            return 0
    parser.print_help()
    return 0


def _suppress_broken_pipe() -> None:
    """Avoid traceback/noisy final flush when a downstream pipe closes stdout."""

    if sys.stdout is sys.__stdout__:
        with suppress(OSError):
            devnull_fd = os.open(os.devnull, os.O_WRONLY)
            try:
                os.dup2(devnull_fd, sys.stdout.fileno())
            finally:
                os.close(devnull_fd)


def main() -> None:
    raise SystemExit(app())


if __name__ == "__main__":
    main()
