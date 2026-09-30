# v0.7.1 MCP diagnostics and temporal guidance

Released 2026-09-30: https://github.com/nh59yytyd5-dev/chinalaw-cli/releases/tag/v0.7.1

Implementation: PR #21, design #20, merge f5c12c748518205f3fb4c112a04c8db15a982940.

## Changes and verification

- Expected MCP business failures retain isError=true, plus JSON text and structuredContent. Public-only list/search/document norm requests returned private_access_denied and status 403 through the real hosted MCP protocol. Unexpected exceptions remain masked in SDK regression tests; query failures remain logged as failures.
- document accepts public law names and aliases after permission checks. Hosted 民法典 read returned 1,260 articles with pagination; unknown law names returned candidates. Explicit IDs retain their original version selection.
- Bare article-number searches retain content hits and warn that those hits may only reference the number. Hosted 第500条 / 第五百条 verified.
- Twelve temporal retrieval rules now have reviewed source references, bounded old-version dates, and explicit mappings between fixture IDs and public corpus IDs. Company Law guidance flags the later article 88 non-retroactivity reply. Coverage is incomplete and is reported in the response; primary/fallback are not applicability conclusions.
- Hosted applicability queries for 合同 and 担保 at 2019-06-01, 2021-06-01, 2024-01-01 and 2026-09-01 all matched one rule with no missing law references. A 2010 公司治理 query correctly avoided assigning the 2018 version.
- Claimed 100-character search truncation was not reproduced: hosted 民法典584 article text and its citation search result were equal. No truncation patch was introduced.

All PR CI checks passed, including cross-platform core/server tests, lint, installation/container smoke, web tests and build. Fourteen local browser tests passed. Regression tests exercise real SDK HTTP transport. Source review records distinguish checking the stored official text from fetching a newly updated upstream text.

## Public artifacts

Freshly built from the same reviewed law JSON as v0.7.0: 2,486 laws, 87,962 article entries, 2,499 revisions. This release adds 12 applicability rules, with 96 total law relations including existing corpus relations. No general law-text refresh is claimed. Original source timestamps remain available.

Both JSON and SQLite archives include rules/source review documentation; manifest hashes were checked against the archived files. SQLite integrity and public-only table gates passed. Current-version consistency audit passed (1,513 current, 440 amended, 258 repealed, 265 unknown, 10 pending effective). This audit is an internal consistency check, not a guarantee of exhaustive legal correctness.

## Oracle deployment

Service and public site now run v0.7.1. Python 3.12 / SQLite 3.34.1 compatibility was checked in an isolated server-side copy before deployment. Importing rules left all non-rule tables and every law's derived text status unchanged in that copy.

Backup: `/srv/chinalaw/releases/20260930-v0.7.1/backup` (root-only). Contains SQLite backup, runtime-and-auth.tar.gz (venv + server-state), systemd unit, nginx route snippet, and previous site symlink. Release wheel SHA-256: `ddc23dd6ab5bd97de8c13bd9b67c3de3cf38143cd4fe735acb49ca245bd5ba3f`.

Deployment stopped the service for backup/install/rule import, restored SELinux labels and restarted successfully. No full-corpus replacement, password reset or nginx route change. Rollback must stop the service and restore the saved DB/runtime/auth and site pointer together; preserve any post-upgrade writes before restoring an earlier DB snapshot.

Public site: `/usr/share/nginx/html-chinalaw-public/current` → `v0.7.1`. `/` → `/about/`; `/console` and `/console/` normalization verified, without an internal 8443 redirect. MCP endpoint and existing query token remain usable. A dedicated public-only test token was revoked after verification; credentials are absent from release artifacts and this report.
