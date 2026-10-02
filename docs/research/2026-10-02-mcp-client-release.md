# v0.7.2 client onboarding and scoped HTTP search

Released and deployed on 2026-10-02, completing the interrupted 2026-10-01 release work.

- Release: https://github.com/nh59yytyd5-dev/chinalaw-cli/releases/tag/v0.7.2
- Issue / implementation: #22 / #23.
- Merge: `de61bb78310bf496452067ea4a4260ded59123a7`.
- Validated PR head: `0988e76cb2bc1f764658108513d54848b2ab0170`; its tree matches the merge.
- CI: https://github.com/nh59yytyd5-dev/chinalaw-cli/actions/runs/36963528147

## Delivered behavior

`docs/MCP_CLIENT.md` provides HTTP client configuration, credential ownership, health-check limits, session negotiation, JSON/SSE handling and capability boundaries. The optional SDK command `chinalaw-remote-check` consumes environment variables or `remote.env`, discovers tools and performs a real query. It distinguishes protocol/authentication/tool failures from successful zero-hit queries without printing credentials.

HTTP MCP and REST search support bounded `in_laws` scopes using existing CLI semantics. Unresolved names do not trigger an unscoped fallback; partially resolved scope lists retain an explicit unresolved list. DSH registration remains an action in the client's actual MCP settings; this release does not register an external harness automatically.

## Verification

All 16 PR CI jobs passed: Python 3.10–3.13 core checks, Windows core/server checks, Linux server checks, lint, web tests, container and installation smoke checks, and wheel/sdist build/install checks. The interrupted release's remaining Ruff failure was fixed by combining the credential test context managers. Those tests remain runnable with standard-library unittest.

Local regression: **995 passed, 32 optional skips, 1,738 subtests passed**. The final wheel contains the diagnostic entry point and updated HTTP scope implementation; the sdist contains the onboarding guide.

The deployed endpoint was exercised with the official MCP SDK and a temporary public-only token:

- Six read-only tools discovered; search schema declares `in_laws`.
- `保证期间` scoped to `民法典` returned four article hits, all belonging to the resolved law ID.
- A nonexistent scope returned zero hits and its unresolved name; mixed valid/invalid scopes returned only the valid law's hits plus the unresolved name.
- More than 20 scopes returned `invalid_search`; private norm search retained structured 403 denial.
- REST search passed the same positive and unresolved-scope checks.
- The diagnostic command reported both a successful scoped query and successful zero hits; after revocation, it reported HTTP 401.
- Existing local credentials still completed the diagnostic command against the public endpoint.
- Document-by-name pagination and 12-rule applicability coverage remained available.

Temporary verification tokens were revoked, and the final verification script completed with exit code zero.

## Deployment and data preservation

Service and public site now run v0.7.2. The release wheel's SHA-256 is:

`b65df0c70756f17bd88763b38ad57c0ef29d3af28fbfa276f8a6d5cb1f3632a8`

The sdist SHA-256 is:

`f00ac73417762f3af2e1de726b44164d6fbdd68cfd4935ced3b1da0744e02b46`

Backup: `/srv/chinalaw/releases/20261002-v0.7.2/backup` (root-only), containing the SQLite backup, old runtime and authentication state, systemd unit, nginx route snippet and old public-site symlink. Deployment stopped the service for backup/install, restored SELinux labels and restarted it. The static `current` symlink now points to `v0.7.2`.

Read-only SQLite comparisons confirmed identical row counts and content hashes before/after for ten substantive tables: laws, articles, revisions, law relations, applicability rules, norm sources, norm clauses, norm source revisions, norm packs and norm pack items. SQLite quick checks passed. The public-law tables retain 2,522 records, 93,055 articles, 2,555 revisions and 12 applicability rules. No data import or legal-status reassessment was performed.

`/` still reaches `/about/`; `/console` and its trailing-slash normalization reach the management page without exposing the internal 8443 port. `/healthz`, `/about/mcp.html` and `/about/data.html` returned 200, and the published onboarding page includes the new guide and diagnostic command.

Public JSON/SQLite downloads remain the reviewed 2026-09-30 snapshot in v0.7.1. v0.7.2 publishes software artifacts and their checksums, without implying a fresh corpus update.

Rollback requires stopping the service and restoring the saved runtime, database, authentication state and site pointer together. Preserve any post-upgrade writes before restoring an earlier database snapshot.
