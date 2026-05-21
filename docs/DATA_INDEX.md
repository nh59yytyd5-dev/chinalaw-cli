# Data Index

This document describes the data bundled with the public preview repository.

## Scope

The repository ships a lightweight baseline under `data/fixtures/`, plus a few time-effect clue records under `data/applicability/`.

The bundled data is intended for:

- offline smoke tests;
- common civil, commercial, criminal, procedural, constitutional, labor, and securities lookup scenarios;
- agent grounding examples where the exact article text must be retrieved before use.

The bundled data is not intended to be a complete legal database.

## Included Paths

| Path | Purpose |
| --- | --- |
| `data/fixtures/*.json` | Canonical law / judicial interpretation fixtures with article text and source metadata. |
| `data/applicability/*.json` | Minimal transition / relationship clues. These are hints, not legal conclusions. |
| `data/recommended_corpus.json` | A manifest of recommended laws by practice area. It is an install index, not authoritative text. |

The public preview intentionally does not ship private local materials, user documents, commercial database exports, agent eval runs, or maintainer-only research artifacts.

## Required Metadata

Every fixture should preserve enough provenance for human review:

- `id`
- `title`
- `short_title`
- `status`
- `source_name`
- `source_url`
- `source_checked_at`
- `source_hash`
- `articles`

If a fixture contains no article text, it must not be presented as an authoritative current law. Missing text should fail loudly through command output and validation.

## Local Loading

```bash
chinalaw sync --fixtures
chinalaw laws --format md
chinalaw article 民法典 第一百四十三条 --format md
```

For an isolated smoke run:

```bash
tmpdb="$(mktemp)"
chinalaw sync --fixtures --db "$tmpdb" --format json
chinalaw article 民法典 第一百四十三条 --db "$tmpdb" --format json
```

## Updating Data

Prefer this order:

1. Use `chinalaw ensure <law>` or `chinalaw fetch <law>` to retrieve and clean from a public source.
2. Verify source metadata and article count.
3. Review the normalized JSON fixture.
4. Add the fixture in a dedicated PR with the source URL and verification command.

Do not copy from commercial databases, paid annotations, unofficial summaries, or private local material.

## Time Effect

`data/applicability/` provides machine-readable clues such as replacement relationships and transition topics. These records help an agent notice possible time-effect issues, but they are not legal conclusions.

When facts involve historical dates, the agent should:

1. identify the relevant date;
2. inspect the current law and known predecessor law;
3. retrieve the applicable version where available;
4. report uncertainty if the local data does not resolve the issue.
