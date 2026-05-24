"""Agent-facing command and tool metadata.

This module is the small single source of truth for command risk, schema
summaries, and MCP tool declarations. The CLI parser remains authoritative for
argument parsing; this layer is intentionally descriptive so agents can inspect
how to call the stable public surface before executing a command.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

SCHEMA_INTROSPECTION_VERSION = 1

RISK_LEVELS: dict[str, str] = {
    "read": "Pure local read; no network and no local database write.",
    "local-write": "Writes local files or the local chinalaw database.",
    "network-read": "Reads remote sources without persisting legal content.",
    "network-write-local": "Reads remote sources and writes cleaned content locally.",
    "maintenance": "Changes local installation, cache, schema, or derived data.",
}

def _arg(
    name: str,
    *,
    required: bool = False,
    description: str = "",
    choices: list[str] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": name,
        "required": required,
        "description": description,
    }
    if choices:
        payload["choices"] = choices
    return payload


GLOBAL_FLAGS: list[dict[str, Any]] = [
    _arg("--db", description="SQLite DB path; may appear before or after subcommand."),
    _arg("--no-notice", description="Disable advisory JSON _notice output."),
    _arg("--version", description="Print CLI version."),
]


def _command(
    path: str,
    *,
    summary: str,
    risk: str,
    side_effect: str = "none",
    network: str = "none",
    authority_boundary: str = "grounding_only",
    positional: list[dict[str, Any]] | None = None,
    flags: list[dict[str, Any]] | None = None,
    output_kind: str = "command_result",
    exit_codes: dict[str, str] | None = None,
    common_misuse: list[str] | None = None,
    follow_ups: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "path": path,
        "summary": summary,
        "risk": risk,
        "side_effect": side_effect,
        "network": network,
        "authority_boundary": authority_boundary,
        "positional": positional or [],
        "flags": flags or [],
        "json_output": {"kind": output_kind},
        "exit_codes": exit_codes
        or {"0": "success", "1": "not found or partial failure", "2": "usage/precondition error"},
        "common_misuse": common_misuse or [],
        "suggested_follow_ups": follow_ups or [],
    }


COMMAND_SPECS: dict[str, dict[str, Any]] = {
    "schema": _command(
        "schema [command|mcp]",
        summary="Inspect machine-readable CLI command or MCP tool contracts.",
        risk="read",
        positional=[
            _arg("target", description="Optional command path, e.g. article, audit file, mcp.")
        ],
        flags=[_arg("--format", choices=["json", "md"], description="Output format.")],
        output_kind="cli_schema_index|cli_command_schema|mcp_schema",
        exit_codes={
            "0": "schema found",
            "1": "target command/tool schema not found",
            "2": "usage error",
        },
        follow_ups=[
            "chinalaw schema article --format json",
            "chinalaw schema mcp --format json",
        ],
    ),
    "doctor": _command(
        "doctor",
        summary=(
            "Check local agent-facing installation, DB, skills, MCP wrapper, "
            "and grounding health."
        ),
        risk="read",
        flags=[
            _arg("--strict", description="Treat warnings as failures."),
            _arg("--source-smoke", description="Optional short network smoke for one source."),
            _arg("--format", choices=["json", "md"], description="Output format."),
        ],
        output_kind="doctor_report",
        exit_codes={
            "0": "all checks pass, or only warnings without --strict",
            "1": "one or more checks fail",
            "2": "usage error",
        },
        follow_ups=[
            "scripts/install-local",
            ".\\scripts\\install-local.ps1",
            "scripts/install-skills",
            ".\\scripts\\install-skills.ps1",
            "chinalaw sync --fixtures",
        ],
    ),
    "init": _command(
        "init",
        summary=(
            "Initialize the local public-law database by loading bundled "
            "fixtures and then running doctor."
        ),
        risk="local-write",
        side_effect="creates or updates the local database with bundled fixture content",
        network="none unless --source-smoke is set",
        flags=[
            _arg("--strict", description="Treat doctor warnings as init failure."),
            _arg("--source-smoke", description="Optional network smoke for one source."),
            _arg("--format", choices=["json", "md"], description="Output format."),
        ],
        output_kind="init_result",
        exit_codes={
            "0": "fixtures loaded and doctor ok",
            "1": "doctor reports failure",
            "2": "usage error",
        },
        common_misuse=[
            "Do not expect init to fetch every possible law; "
            "use ensure/fetch for task-specific gaps."
        ],
        follow_ups=[
            "chinalaw article 民法典 第一百四十三条 --format md",
            "chinalaw doctor --format md",
        ],
    ),
    "resolve": _command(
        "resolve <name>",
        summary=(
            "Resolve a law nickname, alias, short title, or official title "
            "to one local record."
        ),
        risk="read",
        positional=[_arg("name", required=True, description="Law id/title/short title/alias.")],
        flags=[_arg("--format", choices=["json", "md"], description="Output format.")],
        output_kind="law_resolve_result",
        exit_codes={"0": "matched", "1": "not matched", "2": "usage error"},
        common_misuse=[
            "Do not treat fuzzy like_fallback as legal certainty without checking official_title."
        ],
        follow_ups=[
            "chinalaw article <official_title> <number> --format json",
            "chinalaw fetch <name> --list-matches --format json",
        ],
    ),
    "search": _command(
        "search <query...>",
        summary=(
            "Search local public laws, public articles, private norm sources, "
            "and private clauses."
        ),
        risk="read",
        positional=[
            _arg(
                "query",
                required=True,
                description="One or more query tokens; CLI joins tokens with spaces.",
            )
        ],
        flags=[
            _arg("--kind", choices=["article", "law", "norm", "all"], description="Search target."),
            _arg("--in", description="Comma-separated law filters."),
            _arg("--in-part", description="Part/chapter/section filter for article search."),
            _arg("--limit", description="Maximum result count."),
            _arg("--snapshot-out", description="Append result to project grounding snapshot."),
            _arg("--format", choices=["json", "md"], description="Output format."),
        ],
        output_kind="search_result",
        common_misuse=["Do not pass --top or --law-filter; use --limit and --in."],
        follow_ups=[
            "chinalaw article <law> <number> --format json",
            "chinalaw fetch <law> --article <number> --format json",
        ],
    ),
    "get": _command(
        "get <name>",
        summary="Return one local public law with article list.",
        risk="read",
        positional=[_arg("name", required=True, description="Law id/title/short title/alias.")],
        flags=[
            _arg("--as-of", description="Version date YYYY-MM-DD."),
            _arg("--snapshot-out"),
            _arg("--format", choices=["json", "md"]),
        ],
        output_kind="law",
    ),
    "article": _command(
        "article <law> <number>",
        summary="Return one article by law name/id and article number.",
        risk="read",
        positional=[
            _arg("law", required=True, description="Law id/title/short title/alias."),
            _arg("number", required=True, description="Article number, Chinese or Arabic."),
        ],
        flags=[
            _arg("--as-of", description="Version date YYYY-MM-DD."),
            _arg("--no-footer", description="Markdown only: omit metadata footer."),
            _arg("--compact", description="Markdown only: compact metadata footer."),
            _arg("--bare", description="Markdown only: article text only."),
            _arg("--inline", description="Markdown only: one-line output."),
            _arg("--arabic", description="Markdown only: Arabic article heading."),
            _arg("--section", description="Markdown only: §N article heading."),
            _arg("--with-title", description="Include article title when available."),
            _arg("--no-norm-fallback", description="Do not fall back to private norms."),
            _arg("--snapshot-out", description="Append result to project grounding snapshot."),
            _arg("--format", choices=["json", "md", "card"]),
        ],
        output_kind="article_result",
        exit_codes={
            "0": "article found",
            "1": "law/article missing or article_null",
            "2": "usage error",
        },
        common_misuse=[
            "If article is null or reason is law_stub/law_seed, "
            "call chinalaw ensure/fetch before citing."
        ],
        follow_ups=[
            "chinalaw ensure <law> --format json",
            "chinalaw fetch <law> --article <number> --format json",
        ],
    ),
    "articles": _command(
        "articles <law> <numbers> | articles --batch <spec>",
        summary="Return multiple articles for one law or a multi-law batch spec.",
        risk="read",
        positional=[
            _arg("law", description="Law name; omitted when --batch is used."),
            _arg("numbers", description="Comma/range spec such as 5,12,23-25."),
        ],
        flags=[
            _arg("--numbers", description="Comma/range article spec."),
            _arg("--batch", description="Multi-law spec like 民法典:557-561;合同编通则解释:27."),
            _arg("--as-of", description="Version date YYYY-MM-DD."),
            _arg("--no-footer"),
            _arg("--compact"),
            _arg("--bare"),
            _arg("--inline"),
            _arg("--arabic"),
            _arg("--section"),
            _arg("--with-title"),
            _arg("--no-norm-fallback"),
            _arg("--snapshot-out"),
            _arg("--format", choices=["json", "md"]),
        ],
        output_kind="law_articles_result|law_articles_batch_result",
        exit_codes={
            "0": "all requested articles found",
            "1": "partial failure",
            "2": "usage error",
        },
        common_misuse=[
            "For cross-law batches, inspect ok/error_count/failed_section_count, "
            "not only missing_count."
        ],
    ),
    "outline": _command(
        "outline <law>",
        summary="Return a law article outline and optional full text under a part filter.",
        risk="read",
        positional=[_arg("law", required=True, description="Law id/title/short title/alias.")],
        flags=[
            _arg("--part"),
            _arg("--preview-chars"),
            _arg("--with-text"),
            _arg("--snapshot-out"),
            _arg("--format", choices=["json", "md"]),
        ],
        output_kind="law_outline",
    ),
    "cited-by": _command(
        "cited-by <law:number>",
        summary="Find local articles that cite a target article.",
        risk="read",
        positional=[_arg("spec", required=True, description="Target spec such as 民法典:522.")],
        flags=[
            _arg("--in"),
            _arg("--include-self"),
            _arg("--limit"),
            _arg("--format", choices=["json", "md"]),
        ],
        output_kind="law_article_cited_by",
    ),
    "list": _command(
        "list",
        summary="List local public laws by level/status.",
        risk="read",
        flags=[
            _arg("--level"),
            _arg("--status"),
            _arg("--limit"),
            _arg("--format", choices=["json", "md"]),
        ],
        output_kind="law_list",
    ),
    "laws": _command(
        "laws",
        summary="Agent-first alias for list; exposes law ids/titles without direct SQLite access.",
        risk="read",
        flags=[
            _arg("--level"),
            _arg("--status"),
            _arg("--limit"),
            _arg("--format", choices=["json", "md"]),
        ],
        output_kind="law_list",
    ),
    "sync": _command(
        "sync",
        summary=(
            "Load built-in fixtures, local JSON files, applicability rules, "
            "or FLK source data."
        ),
        risk="local-write",
        side_effect="writes local database",
        network="optional for --source flk_npc",
        flags=[
            _arg("--fixtures"),
            _arg("--from-dir"),
            _arg("--applicability"),
            _arg("--source"),
            _arg("--query"),
            _arg("--bbbs"),
            _arg("--batch"),
            _arg("--format", choices=["json", "md"]),
        ],
        output_kind="sync_result|applicability_import",
        common_misuse=[
            "Do not use sync source options as the agent-first one-shot path; prefer fetch/ensure."
        ],
    ),
    "fetch": _command(
        "fetch <name>",
        summary=(
            "One-shot remote fetch, clean, canonicalize, and optionally "
            "persist one public law."
        ),
        risk="network-write-local",
        side_effect=(
            "default writes local database; --dry-run/--list-matches are "
            "read-only; --to-fixture writes file"
        ),
        network="yes",
        positional=[_arg("name", required=True, description="Law name/title/alias.")],
        flags=[
            _arg("--source", description="Source adapter."),
            _arg("--article", description="Locate article after fetch."),
            _arg("--dry-run", description="Do not persist."),
            _arg("--to-fixture", description="Write canonical fixture JSON."),
            _arg("--list-matches", description="Only list candidates."),
            _arg("--prefer-id"),
            _arg("--limit"),
            _arg("--force"),
            _arg("--status", choices=["repealed", "amended", "current", "pending_effective"]),
            _arg("--snapshot-out"),
            _arg("--format", choices=["json", "md"]),
        ],
        output_kind="law_fetch_result|law_fetch_candidates",
        common_misuse=["--dry-run, --to-fixture, and --list-matches are mutually exclusive."],
        follow_ups=[
            "chinalaw article <law> <number> --format json",
            "chinalaw rebuild-clean --law <law> --format json",
        ],
    ),
    "discover": _command(
        "discover",
        summary="Remote candidate discovery without downloading full law text.",
        risk="network-read",
        network="yes",
        flags=[
            _arg("--source"),
            _arg("--query"),
            _arg("--status"),
            _arg("--limit"),
            _arg("--format", choices=["json", "md"]),
        ],
        output_kind="law_discover_result",
    ),
    "ensure": _command(
        "ensure <name...>",
        summary="Local-first load/fetch only for missing/stub/seed laws.",
        risk="network-write-local",
        side_effect="writes local database only when local content is missing/incomplete",
        network=(
            "profile mode first tries bundled fixtures; otherwise only for "
            "missing/stub/seed laws"
        ),
        positional=[_arg("names", description="One or more law names.")],
        flags=[
            _arg("--profile", description="Recommended corpus profile, repeatable."),
            _arg("--no-profile-deps", description="Do not include profile dependencies."),
            _arg("--from-file"),
            _arg("--from-dir"),
            _arg("--filenames-only"),
            _arg("--source"),
            _arg("--limit"),
            _arg("--interval"),
            _arg("--format", choices=["json", "md"]),
        ],
        output_kind="law_ensure_result",
    ),
    "corpus": _command(
        "corpus <list|show>",
        summary="Inspect recommended public-law corpus profiles for profile-based install.",
        risk="read",
        positional=[
            _arg("corpus_command", required=True, description="list or show."),
            _arg("profiles", description="Optional profile names for show."),
        ],
        flags=[
            _arg("--no-deps", description="Do not expand dependencies for corpus show."),
            _arg("--format", choices=["json", "md"]),
        ],
        output_kind="recommended_corpus_profiles|recommended_corpus_profile",
        follow_ups=[
            "chinalaw corpus list --format md",
            "chinalaw ensure --profile baseline --format json",
        ],
    ),
    "sources": _command(
        "sources <list|show>",
        summary=(
            "Inspect source coverage, command capability boundaries, "
            "and public v0.2 migration maturity."
        ),
        risk="read",
        positional=[
            _arg("sources_command", required=True, description="list or show."),
            _arg("source", description="Source id for show, e.g. flk_npc."),
        ],
        flags=[
            _arg("--class", description="Filter list by coverage class."),
            _arg("--public-v2", description="Filter list by public v0.2 migration state."),
            _arg("--implemented-only", description="Only list implemented adapters."),
            _arg("--format", choices=["json", "md"]),
        ],
        output_kind="source_coverage_sources|source_coverage_source",
        exit_codes={
            "0": "success",
            "1": "unknown source or malformed source coverage catalog",
            "2": "usage error",
        },
        common_misuse=[
            "Do not infer a source supports sync just because it supports fetch; "
            "read the commands matrix."
        ],
        follow_ups=[
            "chinalaw sources list --implemented-only --format md",
            "chinalaw sources show gov_xzfgk --format json",
        ],
    ),
    "rebuild-clean": _command(
        "rebuild-clean",
        summary="Replay current cleaning rules against existing local laws/norms.",
        risk="maintenance",
        side_effect="writes local database unless --dry-run is used",
        flags=[
            _arg("--law"),
            _arg("--norm"),
            _arg("--dry-run"),
            _arg("--limit"),
            _arg("--format", choices=["json", "md"]),
        ],
        output_kind="rebuild_clean_result",
    ),
    "status": _command(
        "status",
        summary="Report local database content health and freshness.",
        risk="read",
        flags=[_arg("--format", choices=["json", "md"])],
        output_kind="status_report",
    ),
    "history": _command(
        "history <law>",
        summary="Return known version history for one law.",
        risk="read",
        positional=[_arg("law", required=True)],
        flags=[_arg("--snapshot-out"), _arg("--format", choices=["json", "md"])],
        output_kind="law_history",
    ),
    "diff": _command(
        "diff <law>",
        summary="Compare two as-of versions of one law.",
        risk="read",
        positional=[_arg("law", required=True)],
        flags=[
            _arg("--from-as-of", required=True),
            _arg("--to-as-of", required=True),
            _arg("--snapshot-out"),
            _arg("--format", choices=["json", "md"]),
        ],
        output_kind="law_diff",
    ),
    "trace": _command(
        "trace <law> [number]",
        summary="Map an article or text snippet across two law versions.",
        risk="read",
        positional=[_arg("law", required=True), _arg("number")],
        flags=[
            _arg("--text"),
            _arg("--from-as-of", required=True),
            _arg("--to-as-of", required=True),
            _arg("--items"),
            _arg("--limit"),
            _arg("--snapshot-out"),
            _arg("--format", choices=["json", "md"]),
        ],
        output_kind="law_article_trace",
        common_misuse=["Trace candidates are grounding clues, not automatic legal conclusions."],
    ),
    "relation": _command(
        "relation <law>",
        summary="Return replacement/association clues for one law.",
        risk="read",
        positional=[_arg("law", required=True)],
        flags=[_arg("--snapshot-out"), _arg("--format", choices=["json", "md"])],
        output_kind="law_relation_result",
    ),
    "applicable": _command(
        "applicable --date <YYYY-MM-DD>",
        summary="Find local time-effectivity clues by date and optional topic/law/domain.",
        risk="read",
        positional=[],
        flags=[
            _arg("--date", required=True, description="Fact/dispute date, YYYY-MM-DD."),
            _arg("--topic", description="Optional topic such as 合同效力."),
            _arg("--law", description="Optional law filter."),
            _arg("--domain", description="Optional workflow domain."),
            _arg("--snapshot-out"),
            _arg("--format", choices=["json", "md"]),
        ],
        output_kind="applicability_result",
        common_misuse=["Do not pass the topic as a positional argument; use --topic."],
        follow_ups=[
            "chinalaw relation <law> --format json",
            "chinalaw fetch <old-law> --status repealed --format json",
        ],
    ),
    "probe": _command(
        "probe <source>",
        summary="Read-only external source shape probe.",
        risk="network-read",
        network="yes",
        positional=[_arg("source", required=True)],
        flags=[_arg("--format", choices=["json", "md"])],
        output_kind="source_probe_result",
    ),
    "verify-source": _command(
        "verify-source <source>",
        summary="Read-only source smoke: probe, search, fetch-clean, article locate.",
        risk="network-read",
        network="yes",
        positional=[_arg("source", required=True)],
        flags=[
            _arg("--query"),
            _arg("--article"),
            _arg("--limit"),
            _arg("--format", choices=["json", "md"]),
        ],
        output_kind="source_verify_result",
    ),
    "norm": _command(
        "norm <list|show|clause|import|ingest|export>",
        summary="Manage private norm sources and clauses.",
        risk="local-write",
        side_effect="import/ingest write local database; list/show/clause/export are read-only",
        positional=[_arg("norm_command", required=True)],
        flags=[_arg("--format", choices=["json", "md"])],
        output_kind="norm_source_result",
        common_misuse=["Private norms are not national law; preserve authority and binding_scope."],
    ),
    "commentary": _command(
        "commentary <books|import|article>",
        summary="Local-only article commentary import and lookup.",
        risk="local-write",
        side_effect="import writes local database; books/article are read-only",
        positional=[_arg("commentary_command", required=True)],
        flags=[_arg("--format", choices=["json", "md"])],
        output_kind="commentary_result",
        common_misuse=["Commentary is secondary material; do not cite it as binding law."],
    ),
    "pack": _command(
        "pack <list|show|add|import|export|validate>",
        summary="Manage local norm packs as tags/favorites/problem-domain lists.",
        risk="local-write",
        side_effect="add/import write local database; list/show/export/validate are read-only",
        positional=[_arg("pack_command", required=True)],
        flags=[_arg("--format", choices=["json", "md"])],
        output_kind="norm_pack_result",
        common_misuse=["Run pack validate before treating pack items as grounding."],
    ),
    "cite-check": _command(
        "cite-check <file>",
        summary=(
            "Shortcut for citation checking; expands to audit file or audit grounding "
            "without hiding the evidence chain."
        ),
        risk="read",
        positional=[_arg("file", required=True, description="txt/md/docx/pdf file to audit.")],
        flags=[
            _arg("--as-of", description="Version date YYYY-MM-DD."),
            _arg("--strict", description="Treat warnings as errors."),
            _arg("--grounding", description="Expand to audit grounding instead of audit file."),
            _arg("--snapshot", description="Snapshot path for --grounding."),
            _arg("--format", choices=["json", "md"]),
        ],
        output_kind="file_audit|grounding_audit",
        exit_codes={"0": "audit ok", "1": "audit found errors", "2": "usage/precondition error"},
        common_misuse=[
            "Do not treat cite-check as a legal conclusion; inspect citations/issues.",
            "Use --grounding when auditing whether a final report actually used chinalaw.",
        ],
        follow_ups=[
            "chinalaw audit file <file> --format json",
            "chinalaw audit grounding <file> --format json",
        ],
    ),
    "audit": _command(
        "audit <file|pack|norm|grounding>",
        summary="Audit citations, norm references, or project grounding snapshots.",
        risk="read",
        positional=[_arg("audit_command", required=True)],
        flags=[
            _arg("--as-of"),
            _arg("--strict"),
            _arg("--snapshot"),
            _arg("--format", choices=["json", "md"]),
        ],
        output_kind="audit_report",
        exit_codes={"0": "audit ok", "1": "audit found errors", "2": "usage/precondition error"},
        follow_ups=[
            "chinalaw fetch <law> --article <number> --format json",
            "chinalaw snapshot init",
        ],
    ),
    "snapshot": _command(
        "snapshot <init|status>",
        summary="Manage project-level grounding snapshots.",
        risk="local-write",
        side_effect="init writes project snapshot file; status is read-only",
        positional=[_arg("snapshot_command", required=True)],
        flags=[_arg("--reset"), _arg("--snapshot"), _arg("--format", choices=["json", "md"])],
        output_kind="snapshot_result",
    ),
}


MCP_TOOL_SPECS: list[dict[str, Any]] = [
    {
        "name": "chinalaw_resolve",
        "title": "Resolve Chinese law name",
        "description": (
            "Resolve a Chinese law nickname, alias, or official title to a "
            "local law record. Risk: read."
        ),
        "cli_equivalent": "chinalaw resolve <name> --format json",
        "risk": "read",
        "inputSchema": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
            "additionalProperties": False,
        },
    },
    {
        "name": "chinalaw_article",
        "title": "Get Chinese legal article",
        "description": (
            "Return one local article by law name/id and article number. "
            "If not found, inspect diagnosis before citing. Risk: read."
        ),
        "cli_equivalent": "chinalaw article <law> <number> --format json",
        "risk": "read",
        "inputSchema": {
            "type": "object",
            "properties": {
                "law": {"type": "string"},
                "number": {"type": "string"},
                "as_of": {"type": "string"},
            },
            "required": ["law", "number"],
            "additionalProperties": False,
        },
    },
    {
        "name": "chinalaw_articles",
        "title": "Get multiple legal articles",
        "description": (
            "Return multiple local articles for one law using a comma/range "
            "number spec. Risk: read."
        ),
        "cli_equivalent": "chinalaw articles <law> <numbers> --format json",
        "risk": "read",
        "inputSchema": {
            "type": "object",
            "properties": {
                "law": {"type": "string"},
                "numbers": {"type": "string"},
                "as_of": {"type": "string"},
            },
            "required": ["law", "numbers"],
            "additionalProperties": False,
        },
    },
    {
        "name": "chinalaw_search",
        "title": "Search Chinese law database",
        "description": (
            "Search local laws/articles/norms. Use kind=article for legal "
            "basis discovery. Risk: read."
        ),
        "cli_equivalent": "chinalaw search <query> --format json",
        "risk": "read",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "kind": {"type": "string", "enum": ["article", "law", "norm", "all"]},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50},
                "in_laws": {"type": "string"},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "chinalaw_applicable",
        "title": "Find time-effect rules",
        "description": (
            "Find local applicability/time-effect clues by date and optional "
            "topic/law. Risk: read; not a legal conclusion."
        ),
        "cli_equivalent": "chinalaw applicable --date <YYYY-MM-DD> --format json",
        "risk": "read",
        "inputSchema": {
            "type": "object",
            "properties": {
                "date": {"type": "string"},
                "topic": {"type": "string"},
                "law": {"type": "string"},
                "domain": {"type": "string"},
            },
            "required": ["date"],
            "additionalProperties": False,
        },
    },
    {
        "name": "chinalaw_ensure",
        "title": "Ensure law is locally available",
        "description": (
            "Fetch missing/stub/seed laws into the local DB using chinalaw's "
            "public ensure path. Risk: network-write-local."
        ),
        "cli_equivalent": "chinalaw ensure <name> --format json",
        "risk": "network-write-local",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "source": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 10},
            },
            "required": ["name"],
            "additionalProperties": False,
        },
    },
]


def list_command_summaries() -> list[dict[str, Any]]:
    return [
        {
            "path": spec["path"],
            "summary": spec["summary"],
            "risk": spec["risk"],
            "side_effect": spec["side_effect"],
            "network": spec["network"],
        }
        for spec in COMMAND_SPECS.values()
    ]


def command_schema(path: str) -> dict[str, Any] | None:
    normalized = _normalize_path(path)
    if not normalized:
        return None
    if normalized in COMMAND_SPECS:
        return deepcopy(COMMAND_SPECS[normalized])
    root = normalized.split()[0]
    if root in COMMAND_SPECS:
        spec = deepcopy(COMMAND_SPECS[root])
        spec["requested_path"] = normalized
        return spec
    return None


def schema_index_payload() -> dict[str, Any]:
    return {
        "kind": "cli_schema_index",
        "schema_version": SCHEMA_INTROSPECTION_VERSION,
        "risk_levels": deepcopy(RISK_LEVELS),
        "global_flags": deepcopy(GLOBAL_FLAGS),
        "command_count": len(COMMAND_SPECS),
        "commands": list_command_summaries(),
    }


def command_schema_payload(path: str) -> dict[str, Any] | None:
    spec = command_schema(path)
    if spec is None:
        return None
    return {
        "kind": "cli_command_schema",
        "schema_version": SCHEMA_INTROSPECTION_VERSION,
        "risk_levels": deepcopy(RISK_LEVELS),
        "command": spec,
    }


def mcp_tools(*, protocol: bool = False) -> list[dict[str, Any]]:
    tools = deepcopy(MCP_TOOL_SPECS)
    if not protocol:
        return tools
    for tool in tools:
        tool.pop("risk", None)
        tool.pop("cli_equivalent", None)
    return tools


def mcp_schema_payload() -> dict[str, Any]:
    return {
        "kind": "mcp_schema",
        "schema_version": SCHEMA_INTROSPECTION_VERSION,
        "tool_count": len(MCP_TOOL_SPECS),
        "tools": mcp_tools(),
        "context_budget": {
            "target_tools_list_chars": 6000,
            "principle": (
                "MCP descriptions stay short; legal workflow discipline lives "
                "in skills and CLI schema."
            ),
        },
    }


def _normalize_path(path: str) -> str:
    return " ".join(str(path or "").strip().split())
