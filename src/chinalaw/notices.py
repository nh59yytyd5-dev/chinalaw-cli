"""Non-blocking local notices for agent-facing JSON output.

Notices are deliberately advisory: they never change the primary command
semantics, exit code, or business fields. They surface stale local state that
often causes agents to ground legal work poorly even when a command itself
returns successfully.
"""

from __future__ import annotations

import os
import shutil
from copy import deepcopy
from pathlib import Path
from typing import Any

from chinalaw import service
from chinalaw.schema import SCHEMA_VERSION

DISABLE_NOTICE_ENV = "CHINALAW_NO_NOTICE"
SKIP_COMMANDS = {"doctor", "schema"}
STALE_SOURCE_DAYS = 90


def enabled(*, disabled_by_flag: bool = False) -> bool:
    if disabled_by_flag:
        return False
    raw = os.environ.get(DISABLE_NOTICE_ENV, "")
    return raw.strip().lower() not in {"1", "true", "yes", "on"}


def attach_notices(
    payload: Any,
    *,
    db_path: Path | str,
    command: str | None,
    disabled_by_flag: bool = False,
) -> Any:
    """Return ``payload`` with optional ``_notice`` when safe and useful."""

    if not isinstance(payload, dict):
        return payload
    if command in SKIP_COMMANDS or _looks_like_error(payload):
        return payload
    if not enabled(disabled_by_flag=disabled_by_flag):
        return payload

    notice = collect_notices(db_path, command=command)
    if not notice:
        return payload

    enriched = dict(payload)
    existing = deepcopy(enriched.get("_notice") or {})
    existing.update(notice)
    enriched["_notice"] = existing
    return enriched


def collect_notices(db_path: Path | str, *, command: str | None = None) -> dict[str, dict]:
    """Collect local-only notices without creating a missing DB or using network."""

    notices: dict[str, dict] = {}
    _collect_install_notices(notices)
    _collect_db_notices(notices, Path(db_path).expanduser())
    return notices


def _looks_like_error(payload: dict) -> bool:
    kind = str(payload.get("kind") or "")
    return bool(payload.get("error")) or kind.endswith("_error")


def _notice(message: str, command: str, *, severity: str = "info") -> dict:
    return {"severity": severity, "message": message, "command": command}


def _collect_install_notices(notices: dict[str, dict]) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    wrapper = repo_root / "scripts" / "chinalaw"
    exe = shutil.which("chinalaw")
    if not exe:
        notices["global_wrapper_mismatch"] = _notice(
            "`chinalaw` 不在 PATH 中，agent 可能调用不到本项目 CLI。",
            "scripts/setup-agent",
            severity="warning",
        )
    else:
        exe_path = Path(exe)
        try:
            if exe_path.is_symlink():
                target = Path(os.readlink(exe_path))
                if not target.is_absolute():
                    target = exe_path.parent / target
                if target.resolve() != wrapper.resolve():
                    notices["global_wrapper_mismatch"] = _notice(
                        "`chinalaw` 指向的 wrapper 不是当前 checkout。",
                        "scripts/update-local --no-doctor",
                        severity="warning",
                    )
        except OSError:
            pass

    if not shutil.which("chinalaw-mcp"):
        notices["mcp_not_installed"] = _notice(
            "`chinalaw-mcp` 不在 PATH 中；CLI + skill 仍可用，但 MCP agent 无法接入。",
            "scripts/setup-agent",
            severity="info",
        )

    _collect_skill_notice(notices, repo_root)


def _collect_skill_notice(notices: dict[str, dict], repo_root: Path) -> None:
    source = repo_root / ".claude" / "skills"
    if not source.exists():
        return
    expected = sorted(p.name for p in source.iterdir() if (p / "SKILL.md").exists())
    if not expected:
        return
    targets = [
        Path.home() / ".claude" / "skills",
        Path.home() / ".agents" / "skills",
        Path.home() / ".config" / "opencode" / "skills",
    ]
    complete_targets = []
    for target in targets:
        if all((target / name / "SKILL.md").exists() for name in expected):
            complete_targets.append(target)
    if not complete_targets:
        notices["skills_stale"] = _notice(
            "未检测到完整用户级 chinalaw skills；agent 可能不知道检索纪律。",
            "scripts/install-skills",
            severity="warning",
        )


def _collect_db_notices(notices: dict[str, dict], db_path: Path) -> None:
    if not db_path.exists():
        notices["db_missing"] = _notice(
            f"默认数据库不存在：{db_path}",
            "chinalaw sync --fixtures",
            severity="warning",
        )
        return
    try:
        status = service.status(db_path)
    except Exception as exc:  # pragma: no cover - notice must never break command output
        notices["db_status_unreadable"] = _notice(
            f"数据库状态读取失败：{exc.__class__.__name__}",
            "chinalaw doctor --format md",
            severity="warning",
        )
        return

    schema_version = int(status.get("schema_version") or 0)
    if schema_version != SCHEMA_VERSION:
        notices["db_schema_stale"] = _notice(
            f"数据库 schema_version={schema_version}，当前期望 {SCHEMA_VERSION}。",
            "chinalaw doctor --format md",
            severity="warning",
        )

    freshness_days = status.get("oldest_freshness_days")
    if freshness_days is not None and int(freshness_days) > STALE_SOURCE_DAYS:
        notices["source_stale"] = _notice(
            f"最旧来源已 {freshness_days} 天未核查。",
            "chinalaw doctor --format md",
            severity="info",
        )

    seed_laws = status.get("seed_laws") or []
    stub_laws = status.get("stub_laws") or []
    if seed_laws or stub_laws:
        notices["seed_laws_present"] = _notice(
            f"本地存在 seed/stub 法规：seed={len(seed_laws)}, stub={len(stub_laws)}。",
            "chinalaw ensure <law> --format json",
            severity="warning",
        )
