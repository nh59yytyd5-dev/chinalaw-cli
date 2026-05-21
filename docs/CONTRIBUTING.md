# Contributing

`chinalaw` is an agent-facing legal grounding tool. Contributions should improve correctness, traceability, and maintainability rather than add ad-hoc shortcuts.

## Development Setup

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e ".[dev]"

PYTHONPATH=src python -m compileall -q src
ruff check src
python -m build
```

## Rules

- Fix a class of problems, not one prompt, one evaluation question, or one law title.
- Do not add hardcoded fallbacks for cases that do not exist in real legal texts.
- If a command can fail, make it fail loudly with a machine-readable error.
- Do not let an unresolved law or article look like a successful citation.
- Do not copy text from commercial legal databases, paid annotations, unofficial summaries, or private local material.
- New external sources must document source URL patterns, rate limits, cleaning behavior, and failure modes.
- Schema changes require migration code and compatibility notes in `docs/CONTRACT.md`.

## Data Contributions

When adding or updating fixtures:

1. Use public official sources where possible.
2. Preserve `source_name`, `source_url`, `source_checked_at`, and `source_hash`.
3. Include complete article text when the record is marked `current`.
4. Do not submit stub records that look authoritative but contain no articles.
5. Add the verification command or source URL in the PR description.

## Pull Request Checklist

- [ ] The change is general, not a one-off workaround.
- [ ] `PYTHONPATH=src python -m compileall -q src` passes.
- [ ] `ruff check src` passes.
- [ ] `python -m build` passes.
- [ ] Public command behavior is reflected in `README.md`, `docs/CONTRACT.md`, or `docs/EXAMPLES.md` when relevant.
- [ ] No secrets, local paths, eval artifacts, private materials, or commercial database content are included.

## Issue Reporting

Bug reports should include:

- command;
- input;
- expected behavior;
- actual behavior;
- `chinalaw --version`;
- Python version;
- whether the issue is offline fixture data or an external source fetch.
