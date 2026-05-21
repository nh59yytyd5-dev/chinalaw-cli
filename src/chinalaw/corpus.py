"""Recommended public-law corpus profiles.

The corpus manifest is not legal authority. It is an installer/index layer
that tells agents which official sources should be fetched for a workflow.
"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

from chinalaw.datapaths import builtin_data_file

CORPUS_FILE = "recommended_corpus.json"
SUPPORTED_SOURCE_STATUS = {"supported", "unsupported", "manual_review"}
SUPPORTED_FETCH_STATUS = {"repealed", "amended", "current", "pending_effective"}


class CorpusError(ValueError):
    """Raised when a requested corpus profile is missing or malformed."""


def load_corpus(path: str | Path | None = None) -> dict:
    corpus_path = Path(path) if path is not None else builtin_data_file(CORPUS_FILE)
    try:
        payload = json.loads(corpus_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise CorpusError(f"recommended corpus file not found: {corpus_path}") from exc
    except json.JSONDecodeError as exc:
        raise CorpusError(f"recommended corpus file is invalid JSON: {corpus_path}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("profiles"), dict):
        raise CorpusError("recommended corpus must contain a profiles object")
    _validate_corpus(payload)
    payload.setdefault("path", str(corpus_path))
    return payload


def list_profiles(path: str | Path | None = None) -> dict:
    payload = load_corpus(path)
    profiles = []
    for name, profile in (payload.get("profiles") or {}).items():
        entries = profile.get("entries") or []
        installable_entries = [
            item
            for item in entries
            if item.get("installable", True)
            and item.get("source_status", "supported") == "supported"
        ]
        profiles.append(
            {
                "name": name,
                "priority": profile.get("priority"),
                "description": profile.get("description"),
                "dependencies": list(profile.get("dependencies") or []),
                "aliases": list(profile.get("aliases") or []),
                "entry_count": len(entries),
                "installable_count": len(installable_entries),
                "unsupported_count": sum(
                    1 for item in entries if item.get("source_status", "supported") != "supported"
                ),
            }
        )
    return {
        "kind": "recommended_corpus_profiles",
        "schema_version": payload.get("schema_version"),
        "as_of": payload.get("as_of"),
        "path": payload.get("path"),
        "profile_count": len(profiles),
        "profiles": profiles,
    }


def resolve_profiles(
    names: list[str] | tuple[str, ...],
    *,
    include_dependencies: bool = True,
    path: str | Path | None = None,
) -> dict:
    payload = load_corpus(path)
    profiles = payload.get("profiles") or {}
    profile_lookup = _profile_lookup(profiles)
    requested = [name.strip() for name in names if name and name.strip()]
    if not requested:
        requested = ["baseline"]

    ordered_profile_names: list[str] = []
    for name in requested:
        _append_profile_with_deps(
            name,
            profiles,
            profile_lookup,
            ordered_profile_names,
            include_dependencies=include_dependencies,
            stack=[],
        )

    entries: list[dict] = []
    seen_ids: set[str] = set()
    for profile_name in ordered_profile_names:
        profile = profiles[profile_name]
        for raw in profile.get("entries") or []:
            entry = deepcopy(raw)
            entry.setdefault("profile", profile_name)
            entry.setdefault("source_status", "supported")
            entry_id = entry.get("id") or f"{profile_name}:{entry.get('title')}"
            if entry_id in seen_ids:
                continue
            seen_ids.add(entry_id)
            entries.append(entry)

    return {
        "kind": "recommended_corpus_profile",
        "schema_version": payload.get("schema_version"),
        "as_of": payload.get("as_of"),
        "path": payload.get("path"),
        "requested_profiles": requested,
        "included_profiles": ordered_profile_names,
        "include_dependencies": include_dependencies,
        "entry_count": len(entries),
        "entries": entries,
    }


def _append_profile_with_deps(
    name: str,
    profiles: dict,
    profile_lookup: dict[str, str],
    ordered: list[str],
    *,
    include_dependencies: bool,
    stack: list[str],
) -> None:
    canonical_name = profile_lookup.get(_normalize_profile_name(name))
    if canonical_name is None:
        known = ", ".join(sorted(profiles))
        raise CorpusError(f"unknown corpus profile: {name}; known profiles: {known}")
    if canonical_name in stack:
        chain = " -> ".join([*stack, canonical_name])
        raise CorpusError(f"cyclic corpus profile dependency: {chain}")

    if include_dependencies:
        for dep in profiles[canonical_name].get("dependencies") or []:
            _append_profile_with_deps(
                dep,
                profiles,
                profile_lookup,
                ordered,
                include_dependencies=True,
                stack=[*stack, canonical_name],
            )

    if canonical_name not in ordered:
        ordered.append(canonical_name)


def _validate_corpus(payload: dict) -> None:
    profiles = payload.get("profiles") or {}
    for profile_name, profile in profiles.items():
        if not isinstance(profile, dict):
            raise CorpusError(f"profile must be object: {profile_name}")
        if not isinstance(profile.get("entries"), list):
            raise CorpusError(f"profile entries must be list: {profile_name}")
        for entry in profile.get("entries") or []:
            _validate_entry(profile_name, entry)
    lookup = _profile_lookup(profiles)
    for profile_name, profile in profiles.items():
        for dep in profile.get("dependencies") or []:
            if _normalize_profile_name(dep) not in lookup:
                raise CorpusError(f"profile {profile_name} depends on unknown profile: {dep}")


def _validate_entry(profile_name: str, entry: dict) -> None:
    if not isinstance(entry, dict):
        raise CorpusError(f"profile {profile_name} contains a non-object entry")
    if not (entry.get("id") or entry.get("title")):
        raise CorpusError(f"profile {profile_name} entry must contain id or title")
    status = entry.get("source_status", "supported")
    if status not in SUPPORTED_SOURCE_STATUS:
        raise CorpusError(
            f"profile {profile_name} entry {entry.get('id') or entry.get('title')} "
            f"has invalid source_status: {status}"
        )
    fetch_status = entry.get("fetch_status")
    if fetch_status is not None and fetch_status not in SUPPORTED_FETCH_STATUS:
        known = ", ".join(sorted(SUPPORTED_FETCH_STATUS))
        raise CorpusError(
            f"profile {profile_name} entry {entry.get('id') or entry.get('title')} "
            f"has invalid fetch_status: {fetch_status}; expected one of: {known}"
        )
    if entry.get("installable", True) and status == "supported":
        if not entry.get("title"):
            raise CorpusError(f"profile {profile_name} installable entry is missing title")
        if not entry.get("primary_source"):
            raise CorpusError(f"profile {profile_name} installable entry is missing primary_source")


def _profile_lookup(profiles: dict) -> dict[str, str]:
    lookup: dict[str, str] = {}
    for name, profile in profiles.items():
        aliases = [name, *(profile.get("aliases") or [])]
        for alias in aliases:
            key = _normalize_profile_name(alias)
            if key in lookup and lookup[key] != name:
                raise CorpusError(f"duplicate corpus profile alias: {alias}")
            lookup[key] = name
    return lookup


def _normalize_profile_name(name: str) -> str:
    return name.strip().lower().replace("_", "-")
