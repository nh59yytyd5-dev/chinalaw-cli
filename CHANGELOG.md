# Changelog

## 0.1.0 - 2026-05-21

Initial public preview of `chinalaw`.

### Added

- Local SQLite-backed Chinese law CLI.
- Core agent-facing commands: `resolve`, `search`, `get`, `article`, `articles`, `outline`, `sync --fixtures`, `ensure`, `fetch`, `doctor`, and `status`.
- Bundled fixture baseline for common public statutes and judicial interpretations.
- Source metadata fields including `source_name`, `source_url`, `source_checked_at`, `source_hash`, status, version, and article counts.
- Optional lightweight MCP wrapper.
- Agent workflow notes under `.claude/skills/`.
- GitHub Actions build, lint, compile, and installed-wheel smoke check.

### Notes

- This is a public preview. The project is useful for local agent grounding, but it is not a full legal database and does not provide legal opinions.
- `fetch` depends on public upstream websites and may require future adapter maintenance when upstream structures change.
