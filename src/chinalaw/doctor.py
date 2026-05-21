"""Local agent-facing health checks for chinalaw."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Any

from chinalaw import __version__, service, sources
from chinalaw.db import DEFAULT_DB_PATH
from chinalaw.schema import SCHEMA_VERSION


def run_doctor(
    db_path: Path | str = DEFAULT_DB_PATH,
    *,
    strict: bool = False,
    source_smoke: str | None = None,
    freshness_warn_days: int = 90,
) -> dict[str, Any]:
    """Return a structured health report without mutating missing databases."""

    db = Path(db_path).expanduser()
    checks: list[dict[str, Any]] = []
    _add(
        checks,
        "cli_version",
        "pass",
        f"chinalaw {__version__} on Python {sys.version_info.major}.{sys.version_info.minor}",
    )
    _check_path(checks)
    _check_db(checks, db, freshness_warn_days=freshness_warn_days)
    _check_skills(checks)
    _check_mcp(checks)
    if source_smoke:
        _check_source_smoke(checks, source_smoke)
    else:
        _add(
            checks,
            "source_smoke",
            "skip",
            "联网 source smoke 默认跳过",
            hint="需要时运行 chinalaw doctor --source-smoke flk_npc --format md",
        )

    warning_count = sum(1 for item in checks if item["status"] == "warn")
    fail_count = sum(1 for item in checks if item["status"] == "fail")
    error_count = fail_count + (warning_count if strict else 0)
    return {
        "kind": "doctor_report",
        "ok": error_count == 0,
        "strict": strict,
        "db_path": str(db),
        "checks": checks,
        "error_count": error_count,
        "warning_count": warning_count,
    }


def _add(
    checks: list[dict[str, Any]],
    name: str,
    status: str,
    message: str,
    *,
    hint: str | None = None,
    data: dict[str, Any] | None = None,
) -> None:
    payload: dict[str, Any] = {"name": name, "status": status, "message": message}
    if hint:
        payload["hint"] = hint
    if data:
        payload["data"] = data
    checks.append(payload)


def _script_hint(script: str) -> str:
    if sys.platform.startswith("win"):
        return f"运行 .\\scripts\\{script}.ps1。"
    return f"运行 scripts/{script}。"


def _path_hint() -> str:
    if sys.platform.startswith("win"):
        return (
            "运行 .\\scripts\\install-local.ps1，或把 %USERPROFILE%\\.local\\bin 加入 Path。"
        )
    return "运行 scripts/install-local，或把 $HOME/.local/bin 加入 PATH。"


def _check_path(checks: list[dict[str, Any]]) -> None:
    exe = shutil.which("chinalaw")
    if not exe:
        _add(
            checks,
            "path",
            "warn",
            "`chinalaw` 不在 PATH 中",
            hint=_path_hint(),
        )
        return

    data: dict[str, Any] = {"resolved": exe}
    path = Path(exe)
    if path.is_symlink():
        target = os.readlink(path)
        data["symlink_target"] = target
    _add(checks, "path", "pass", f"PATH resolves chinalaw to {exe}", data=data)


def _check_db(
    checks: list[dict[str, Any]],
    db: Path,
    *,
    freshness_warn_days: int,
) -> None:
    if not db.exists():
        _add(
            checks,
            "db_exists",
            "warn",
            f"默认数据库不存在：{db}",
            hint="运行 chinalaw sync --fixtures，或先在任务中用 chinalaw ensure <law> 补库。",
        )
        _add(checks, "schema_version", "skip", "数据库不存在，跳过 schema 检查")
        _add(checks, "fixtures_loaded", "skip", "数据库不存在，跳过 fixture 检查")
        return

    _add(checks, "db_exists", "pass", f"数据库存在：{db}")
    try:
        status = service.status(db)
    except Exception as exc:  # pragma: no cover - defensive health boundary
        _add(
            checks,
            "db_status",
            "fail",
            f"数据库状态读取失败：{exc}",
            hint="先备份 ~/.chinalaw/chinalaw.db，再检查 schema 或重建数据库。",
            data={"error": exc.__class__.__name__},
        )
        return

    schema_version = int(status.get("schema_version") or 0)
    if schema_version == SCHEMA_VERSION:
        _add(checks, "schema_version", "pass", f"schema_version={schema_version}")
    else:
        _add(
            checks,
            "schema_version",
            "fail",
            f"schema_version={schema_version}, expected={SCHEMA_VERSION}",
            hint="运行当前版本 chinalaw status 触发迁移；迁移前建议备份数据库。",
        )

    law_count = int(status.get("laws") or 0)
    article_count = int(status.get("articles") or 0)
    if law_count and article_count:
        _add(
            checks,
            "fixtures_loaded",
            "pass",
            f"本地已有 {law_count} 部法规 / {article_count} 条条文",
        )
    else:
        _add(
            checks,
            "fixtures_loaded",
            "warn",
            f"本地法规或条文为空：laws={law_count}, articles={article_count}",
            hint="运行 chinalaw sync --fixtures，或用 chinalaw ensure <law> 按需补库。",
        )

    freshness_days = status.get("oldest_freshness_days")
    if freshness_days is None:
        _add(checks, "freshness", "skip", "没有 source_checked_at 可检查")
    elif int(freshness_days) > freshness_warn_days:
        _add(
            checks,
            "freshness",
            "warn",
            f"最旧来源已 {freshness_days} 天未核查",
            hint=(
                "对关键法规运行 chinalaw fetch <law> --force，"
                "或先 chinalaw verify-source <source>。"
            ),
        )
    else:
        _add(checks, "freshness", "pass", f"最旧来源核查 {freshness_days} 天前")

    stub_laws = status.get("stub_laws") or []
    seed_laws = status.get("seed_laws") or []
    if stub_laws or seed_laws:
        _add(
            checks,
            "seed_or_stub",
            "warn",
            f"存在 seed/stub 法规：seed={len(seed_laws)}, stub={len(stub_laws)}",
            hint="引用前用 chinalaw ensure <law> 或 chinalaw fetch <law> --article <number> 补全。",
            data={
                "seed_laws": seed_laws[:10],
                "stub_laws": stub_laws[:10],
                "truncated": len(seed_laws) + len(stub_laws) > 20,
            },
        )
    else:
        _add(checks, "seed_or_stub", "pass", "未发现 seed/stub 法规")


def _check_skills(checks: list[dict[str, Any]]) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    source = repo_root / ".claude" / "skills"
    if not source.exists():
        _add(
            checks,
            "skills_installed",
            "skip",
            "当前安装包中未发现仓库 .claude/skills 目录",
        )
        return

    expected = sorted(p.name for p in source.iterdir() if (p / "SKILL.md").exists())
    opencode_skills = (
        Path(os.environ["APPDATA"]) / "opencode" / "skills"
        if sys.platform.startswith("win") and os.environ.get("APPDATA")
        else Path.home() / ".config" / "opencode" / "skills"
    )
    targets = [
        Path.home() / ".claude" / "skills",
        Path.home() / ".agents" / "skills",
        opencode_skills,
    ]
    present: dict[str, list[str]] = {}
    for target in targets:
        names = [name for name in expected if (target / name / "SKILL.md").exists()]
        if names:
            present[str(target)] = names

    if not expected:
        _add(checks, "skills_installed", "skip", "仓库 skills 目录为空")
    elif present:
        full_targets = [target for target, names in present.items() if len(names) == len(expected)]
        status = "pass" if full_targets else "warn"
        message = (
            f"已安装 {len(present)} 个 skill 目标目录"
            if status == "pass"
            else "检测到部分 skill，但不是完整安装"
        )
        _add(
            checks,
            "skills_installed",
            status,
            message,
            hint=f"如需刷新，{_script_hint('install-skills')}",
            data={"expected": expected, "present": present},
        )
    else:
        _add(
            checks,
            "skills_installed",
            "warn",
            "未检测到用户级 chinalaw skills",
            hint=f"{_script_hint('install-skills')}让 Claude Code / Codex / OpenCode 全局加载。",
            data={"expected": expected, "targets": [str(t) for t in targets]},
        )


def _check_mcp(checks: list[dict[str, Any]]) -> None:
    exe = shutil.which("chinalaw-mcp")
    if exe:
        _add(checks, "mcp_available", "pass", f"PATH resolves chinalaw-mcp to {exe}")
    else:
        _add(
            checks,
            "mcp_available",
            "warn",
            "`chinalaw-mcp` 不在 PATH 中",
            hint=f"{_script_hint('install-local')}MCP 不是主路径，CLI+skill 仍可用。",
        )


def _check_source_smoke(checks: list[dict[str, Any]], source: str) -> None:
    try:
        report = sources.probe_source(source)
    except Exception as exc:  # pragma: no cover - network boundary
        _add(
            checks,
            "source_smoke",
            "fail",
            f"{source} probe 失败：{exc}",
            data={"error": exc.__class__.__name__},
        )
        return
    ok = bool(report.get("ok", True)) and not report.get("error")
    _add(
        checks,
        "source_smoke",
        "pass" if ok else "fail",
        f"{source} probe {'通过' if ok else '失败'}",
        data={"source": source, "status_code": report.get("status_code")},
    )
