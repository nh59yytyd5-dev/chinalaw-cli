"""Locate data bundled with source, wheel, sdist, or user-site installs.

Hatchling ``shared-data`` is installed below the active installation scheme's
``data`` root. That root is often ``sys.prefix`` for virtual environments, but
``pip install --user`` uses the user scheme while leaving ``sys.prefix``
unchanged. Keep every supported location in one ordered, inspectable list.
"""

from __future__ import annotations

import os
import site
import sys
import sysconfig
from collections.abc import Iterable
from pathlib import Path


def builtin_data_candidates(name: str) -> tuple[Path, ...]:
    """Return all plausible paths for one bundled data file or directory."""

    relative = Path("chinalaw") / "data" / name
    repo_candidate = Path(__file__).resolve().parents[2] / "data" / name
    package_candidate = Path(__file__).resolve().parent / "data" / name

    data_roots: list[Path] = []
    for root in (
        _sysconfig_data_path(),
        _user_data_path(),
        Path(site.USER_BASE) if site.USER_BASE else None,
        Path(sys.prefix),
        Path(sys.base_prefix),
    ):
        if root is not None:
            data_roots.append(root)

    candidates: list[Path] = [repo_candidate, package_candidate]
    candidates.extend(root / relative for root in data_roots)
    return tuple(_dedupe_paths(candidates))


def builtin_data_dir(name: str) -> Path:
    return _first_existing_or_default(builtin_data_candidates(name))


def builtin_data_file(name: str) -> Path:
    return _first_existing_or_default(builtin_data_candidates(name))


def builtin_data_search_message(name: str) -> str:
    searched = ", ".join(str(path) for path in builtin_data_candidates(name))
    return f"searched bundled data paths: {searched}"


def _sysconfig_data_path() -> Path | None:
    try:
        value = sysconfig.get_path("data")
    except (KeyError, TypeError, ValueError):
        return None
    return Path(value) if value else None


def _user_data_path() -> Path | None:
    preferred = "nt_user" if os.name == "nt" else "posix_user"
    try:
        schemes = set(sysconfig.get_scheme_names())
        if preferred not in schemes:
            return None
        value = sysconfig.get_path("data", scheme=preferred)
    except (KeyError, TypeError, ValueError):
        return None
    return Path(value) if value else None


def _dedupe_paths(paths: Iterable[Path]) -> list[Path]:
    seen: set[str] = set()
    ordered: list[Path] = []
    for path in paths:
        key = os.path.normcase(os.path.abspath(os.fspath(path)))
        if key in seen:
            continue
        seen.add(key)
        ordered.append(path)
    return ordered


def _first_existing_or_default(candidates: tuple[Path, ...]) -> Path:
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]
