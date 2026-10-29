<h1 align="center">chinalaw</h1>

<p align="center"><strong>Trace every legal citation back to the original text</strong></p>

<p align="center">A library of Chinese laws and regulations for legal research and AI workflows: verify articles, tell versions apart, keep the sources.</p>

<p align="center">
  <a href="https://github.com/nh59yytyd5-dev/chinalaw-cli/actions/workflows/test.yml"><img alt="tests" src="https://github.com/nh59yytyd5-dev/chinalaw-cli/actions/workflows/test.yml/badge.svg"></a>
  <a href="https://github.com/nh59yytyd5-dev/chinalaw-cli/releases"><img alt="release" src="https://img.shields.io/github/v/release/nh59yytyd5-dev/chinalaw-cli?include_prereleases&amp;label=release"></a>
  <img alt="python 3.10+" src="https://img.shields.io/badge/python-3.10%2B-blue">
  <a href="LICENSE"><img alt="license Apache-2.0" src="https://img.shields.io/badge/license-Apache--2.0-green"></a>
</p>

<p align="center"><a href="README.md">简体中文</a> · <strong>English</strong></p>

<p align="center">
  <a href="#getting-started">Getting started</a> ·
  <a href="#connect-ai-clients">Connect AI clients</a> ·
  <a href="#self-hosting-and-the-admin-panel">Self-hosting</a> ·
  <a href="#data-and-sources">Data and sources</a> ·
  <a href="#documentation">Documentation</a>
</p>

`chinalaw` organizes public Chinese laws, administrative regulations and judicial interpretations into a library you can query article by article. Every article it returns carries its validity status, effective date, version, official source and last-checked time. A model may suggest leads, but citations go back to the original text the tool returns, so people and agents can both check them. It does not answer legal questions and does not replace legal judgment.

The legal texts, the CLI's messages and the documentation beyond this README are in Chinese.

```console
$ chinalaw article 民法典 533 --format md
## 民法典 第五百三十三条

> 合同成立后，合同的基础条件发生了当事人在订立合同时无法预见的、不属于商业风险的重大变化，继续履行合同对于当事人一方明显不公平的，受不利影响的当事人可以与对方重新协商；在合理期限内协商不成的，当事人可以请求人民法院或者仲裁机构变更或者解除合同。
人民法院或者仲裁机构应当结合案件的实际情况，根据公平原则变更或者解除合同。

---
- 文本状态：current
- 施行日期：2021-01-01
- 当前版本：2020-05-28 发布 / 2021-01-01 施行
- 来源：https://flk.npc.gov.cn/detail?id=ff808081729d1efe01729d50b5c500bf
- 最后核查：核查 181 天前
```

The same material can be used in four ways, alone or together:

| Option | Suited to | Where the data lives |
| --- | --- | --- |
| [Public snapshot](https://law.newcombmath.com/about/data.html) | Research, batch processing, offline use | JSON / SQLite downloaded to your machine |
| CLI and local MCP | Agents such as Claude Code, Codex and Cursor; personal research | A local SQLite file; contracts and case files never leave your machine |
| [Hosted MCP](https://law.newcombmath.com/about/mcp.html) | Light online use, trying it out | law.newcombmath.com, with read-only tokens issued on request |
| Self-hosted server | Teams, several clients | Your own server: a browser admin panel plus read-only REST / MCP over HTTP |

## A citation you can check

1. **Locate**: if you know the law and the article number, read the original text directly (`article`). If you are unsure of the law's name, resolve its full title and candidates first (`resolve`, which accepts common names, short titles and document numbers).
2. **Verify**: the same article number can mean different text in different versions. Check that the text is on point, along with its validity status, effective date and source. When the facts lie in the past, query the version in force at the time (`--as-of`); `applicable` offers leads on temporal applicability, not conclusions.
3. **Fill gaps**: search (`search`) only when you have no leads or need related provisions, and cite the full article rather than a search snippet. A law or article missing from the local library produces an explicit error (`law_missing`, `article_null`, `needs_fetch`) instead of a citation that looks successful; following the diagnostics, you can fetch it from an official source (`ensure` / `fetch`).

## Getting started

Requires Python 3.10 or later.

### Install

Download the wheel from the [latest release](https://github.com/nh59yytyd5-dev/chinalaw-cli/releases/latest):

```bash
python -m pip install ./chinalaw-0.7.4-py3-none-any.whl              # CLI and local MCP, no third-party dependencies
python -m pip install './chinalaw-0.7.4-py3-none-any.whl[server]'    # also the admin panel and HTTP service
```

For local agents such as Claude Code or Codex you can also install from the repository:

```bash
git clone https://github.com/nh59yytyd5-dev/chinalaw-cli.git
cd chinalaw-cli
scripts/setup-agent                 # Windows PowerShell: .\scripts\setup-agent.ps1
```

`setup-agent` creates a `.venv` inside the repository, writes `chinalaw` and `chinalaw-mcp` to `~/.local/bin` (`%USERPROFILE%\.local\bin` on Windows), and on first run loads the bundled baseline and runs a health check. If the commands are not found, add that directory to `PATH`. To update, run `scripts/update-local` after `git pull` (Windows: `.\scripts\update-local.ps1`).

### Get the data

**Public snapshot** (recommended): 2,486 law records, 87,962 article records and 2,499 versions, with data as of 2026-09-30, attached to the [v0.7.1 release](https://github.com/nh59yytyd5-dev/chinalaw-cli/releases/tag/v0.7.1). The SQLite package can be queried as soon as it is unpacked:

```bash
tar -xzf chinalaw-public-2026-09-30-sqlite.tar.gz
chinalaw --db ./chinalaw-public-2026-09-30-sqlite/library.db article 民法典 533
```

The SQLite package needs chinalaw 0.7.1+ and SQLite 3.34+ with FTS5 / trigram. If it does not work on your system, import the JSON package into a new library instead (`chinalaw --db ./public-library.db sync --from-dir <unpacked dir>/laws`). The record counts include historical versions; the snapshot is neither all of Chinese law nor updated in real time, and later software releases did not repackage the data. Scope and quality limits are on the [public data page](https://law.newcombmath.com/about/data.html#limits).

**Bundled baseline**: `chinalaw init` loads the 74 bundled public texts (49 laws once loaded) and the temporal-applicability rules, with no network access. It suits getting started offline and testing, but covers only the main codes, procedural laws and major judicial interpretations.

Keep the snapshot as a separate library and point `--db` at it (the CLI and the local MCP server also read the `CHINALAW_DB` environment variable) rather than overwriting your working library. `status` and `doctor` report the size of whichever library is in use.

### Query

```bash
chinalaw resolve 民法典 --format json             # common name / short title / document number to the official record
chinalaw article 民法典 第一百四十三条 --format md
chinalaw articles 民法典 --numbers "143,464,509,577" --format json
chinalaw search 合同效力 --kind article --limit 5 --format md
chinalaw outline 民法典 --limit 20 --format md
chinalaw ensure 劳动合同法 --format json           # skip if present locally, fetch only when missing
chinalaw doctor --format md                      # health check of install, database, MCP and skills
```

Output is JSON by default; most commands accept `--format md`. `fetch` and `ensure` reach public official sources and can be affected by upstream redesigns, the network and rate limits, so scripts should check the returned `ok`, `error` and `source_*` fields. The full command and field reference is [docs/CONTRACT.md](docs/CONTRACT.md); more examples are in [docs/EXAMPLES.md](docs/EXAMPLES.md).

## Connect AI clients

### Local stdio MCP (recommended)

`chinalaw-mcp` installs with the CLI, queries only your local SQLite file and needs no token. In clients that support `mcpServers`, configure it like this, with absolute paths from your machine:

```json
{
  "mcpServers": {
    "chinalaw": {
      "command": "/absolute/path/to/chinalaw-mcp",
      "args": ["--db", "/absolute/path/to/library.db"]
    }
  }
}
```

Its tools are `chinalaw_resolve`, `chinalaw_search`, `chinalaw_article`, `chinalaw_articles`, `chinalaw_applicable` and `chinalaw_ensure`. Private norms are not exposed over MCP by default; start it with `--allow-private-norms` if you need them.

### Hosted HTTP MCP

- Endpoint `https://law.newcombmath.com/mcp` (Streamable HTTP), with the header `Authorization: Bearer <your token>`. Tokens grant read-only access to the public library (`chinalaw:public:read`) and nothing else.
- There is no shared token and no anonymous search. To get access, [open a request](https://github.com/nh59yytyd5-dev/chinalaw-cli/issues/new?title=MCP%20hosted%20access%20request) on GitHub describing your use and expected volume; never post a token in an issue.
- It is a small shared service with rate and concurrency limits and no availability guarantee. Queries are logged with the tool, arguments and a hit summary, but not the returned text; please do not submit identifiable case information. For batch research and production integrations, download the data or self-host.
- Its tools are `chinalaw_resolve`, `chinalaw_article`, `chinalaw_search`, `chinalaw_list`, `chinalaw_document` and `chinalaw_applicable`. Client configuration, the credentials file and the connection check (`chinalaw-remote-check`) are covered in [MCP client setup](docs/MCP_CLIENT.md) and the [online MCP guide](https://law.newcombmath.com/about/mcp.html).

### CLI and skills

Agents that can run shell commands can use the CLI directly. Add this to the global rules of Claude Code, Codex, Cursor or OpenCode:

```text
For questions involving Chinese law, statutory articles, judicial interpretations,
contract review, or labor / corporate / civil and commercial / criminal matters,
get the original text with the local chinalaw CLI first; do not cite from model memory.
- Known law and article number: chinalaw article <law> <number> --format json
- Several articles: chinalaw articles <law> --numbers <nums> --format json
- Unsure of the law's name: chinalaw resolve <name> --format json
- No leads, or related provisions needed: chinalaw search <query> --kind article --limit 10 --format json
- Facts in the past: add --as-of YYYY-MM-DD to article
- Missing locally: chinalaw ensure <law> --format json
Check that the returned text is on point, and check its validity status and effective date.
If the result is law_missing, law_stub, law_seed, article_null or needs_fetch,
follow the diagnostics to fill the gap, or say plainly that local data is insufficient.
```

The repository ships 7 skills (search method, citation checking, contract review, legal research and more). By default they are distributed only as documentation in the repository; to load them globally, run `scripts/install-skills --copy` (Windows: `.\scripts\install-skills.ps1`), and `--uninstall` removes them. Call order, output discipline, corpus installation and grounding snapshot audits are described in [docs/AGENT_WORKFLOWS.md](docs/AGENT_WORKFLOWS.md).

## Self-hosting and the admin panel

`chinalaw-server` is optional. In a browser you can see what the library holds, check imported texts article by article, and review differences before confirming any write. It also serves read-only REST and MCP over HTTP, so agents on several devices can query the same library. The hosted law.newcombmath.com runs it.

<p align="center"><img src="docs/images/library-overview.png" alt="Overview of the chinalaw library admin panel" width="900"></p>

```bash
python -m pip install './chinalaw-0.7.4-py3-none-any.whl[server]'
# From the repository: scripts/install-local --with-server (Windows: .\scripts\install-local.ps1 -WithServer)
chinalaw-server init --with-fixtures    # bundled baseline; for nationwide public law, import the snapshot as the online guide describes
chinalaw-server serve --open            # prints a one-time pairing link; set a password once in the panel
```

- Uploaded files and texts fetched from official sources are previewed first, with the full text, the original file and a diff of additions, deletions and changes. Nothing is imported until a person has checked it, and each check is tied to a specific content version.
- A single owner signs in and can issue and revoke read-only tokens; MCP clients use OAuth / PKCE.
- Backups include source attachments and can be verified, previewed for conflicts and restored in a single transaction, which is how a library moves between a local machine and a server.
- For self-hosting, use the repository's `Dockerfile` and `deploy/compose.yaml` (Caddy reverse proxy with HTTPS).

The panel needs neither Node.js nor any model API. The commands above run in local mode; a public deployment needs HTTPS, an owner password, a reverse proxy and separate tokens, so never expose local pairing mode to the internet. Importing the snapshot is covered in the [online MCP guide](https://law.newcombmath.com/about/mcp.html#selfhost); operation, deployment and client setup are in [docs/ADMIN_SERVER.md](docs/ADMIN_SERVER.md).

## Private norms

Company policies, a client's lending requirements, internal compliance manuals and industry standards can be searched alongside public law. They stay on your machine and are never bundled, and the output always marks them as **not state law**: hits carry `hierarchy: "private_norm"` and a `binding_note` on how far they bind, plus a `conflict_notice` when public law matches too.

```bash
chinalaw norm import data/norms/acme-lending-policy.json --format json   # fictional sample in the repository
chinalaw norm ingest path/to/policy.md --name "内部合规手册" --source-type internal_governance
chinalaw search 担保审批 --kind norm --format md
```

`ingest` accepts txt / md / docx / pdf, splits text at each "第 N 条" (Article N) and recognizes chapter structure. `norm history` / `norm diff` / `norm delete` manage the lifecycle, and `norm export` adds leak-prevention labels. Allowed `--source-type` values are listed in [docs/CONTRACT.md](docs/CONTRACT.md) §2.9.

## Data and sources

- **The public snapshot** is built independently from reviewed public JSON. It mainly covers nationally issued public law, including historical versions. It is not a backup of the live database and contains no credentials, logs, private norms or case files. The hosted instance keeps older IDs working, so it holds slightly more records than the download. What was checked is described in the [import quality report](docs/research/2026-09-29-national-import-quality.md) and the `QUALITY.md` inside the package.
- **The bundled baseline** is 74 complete public texts: the successive texts of the Constitution and the Criminal Law; the Civil Code; the Civil, Criminal and Administrative Procedure Laws; the Company Law, the Labor Contract Law, the Personal Information Protection Law and others; the Civil Code's implementing interpretations; and the major judicial interpretations on litigation, contracts and labor. Loaded, they make 49 laws, 7,519 articles and 74 versions (the successive texts of the Constitution and the Criminal Law are versions of one law each). The full list is in [docs/DATA_INDEX.md](docs/DATA_INDEX.md). The baseline guarantees offline use and the snapshot provides scale; neither replaces the other.
- **Official sources**: 12 official-source adapters are implemented, covering the National Database of Laws and Regulations (`flk_npc`), the National Administrative Regulations Database (`gov_xzfgk`), the Supreme People's Court Gazette and main site (`court_gongbao`, `court_main`), the Supreme People's Procuratorate (`spp_gov_cn`), the China Securities Regulatory Commission (`csrc_gov_cn`), the National Financial Regulatory Administration (`nfra_gov_cn`), and rules from the Shanghai, Shenzhen and Beijing stock exchanges, China Securities Depository and Clearing, and the Securities Association of China. Fetching follows [docs/COMPLIANCE.md](docs/COMPLIANCE.md): low frequency, public texts only, sources and hashes kept.
- Thanks to the original-text archive [lawtext/legal-data-docx](https://github.com/lawtext/legal-data-docx). Official provenance, check times and source hashes are kept in the data packages; see the [data sources and rights notice](data/public-release/NOTICE.md).

```bash
chinalaw sources list --implemented-only --format md
chinalaw sources show flk_npc --format json      # which commands each source supports
```

## Command reference

| Command | Purpose |
| --- | --- |
| `init` | Load the bundled public baseline and temporal-applicability rules, then run a health check |
| `resolve <name>` | Resolve a common name / short title / document number to the official record |
| `article <law> <number>` / `articles <law> --numbers ...` | One article / several articles; `--as-of` picks the version in force on that date |
| `search <query>` | Full-text / article search, scoped with `--kind`; also takes `--as-of` |
| `outline <law>` | Table of contents and text preview |
| `ensure <law>` / `fetch <law>` | Fill gaps local-first / fetch from a source and import |
| `history` / `diff` / `applicable` / `relation` | Version history and temporal-applicability leads |
| `law delete <id>` | Delete one public law row by exact id (run `--dry-run` first) |
| `norm ...` / `pack ...` | Manage private norms / norm packs |
| `audit <target>` / `cite-check <file>` | Check whether the citations in a file resolve and whether their text matches |
| `doctor` / `status` | Local health check and data freshness |
| `chinalaw-mcp` / `chinalaw-server` | stdio MCP / admin panel and HTTP service |

## Documentation

These documents are in Chinese.

- [docs/MCP_CLIENT.md](docs/MCP_CLIENT.md): MCP client setup, the credentials file and the connection check.
- [docs/ADMIN_SERVER.md](docs/ADMIN_SERVER.md): using, deploying and connecting to the admin panel and server.
- [docs/AGENT_WORKFLOWS.md](docs/AGENT_WORKFLOWS.md): agent call order, output discipline, corpus installation and skills.
- [docs/CONTRACT.md](docs/CONTRACT.md): the CLI / JSON / exit-code contract and data model, which commands, fields and exit codes evolve against.
- [docs/EXAMPLES.md](docs/EXAMPLES.md): runnable examples.
- [docs/DATA_INDEX.md](docs/DATA_INDEX.md), [docs/COMPLIANCE.md](docs/COMPLIANCE.md): the bundled data and the limits on fetching from sources.
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), [docs/CLEANING.md](docs/CLEANING.md): code structure and cleaning rules.
- [docs/PROJECT_CHARTER.md](docs/PROJECT_CHARTER.md): what the project is for and what it deliberately leaves out.
- [CHANGELOG.md](CHANGELOG.md), [SECURITY.md](SECURITY.md), [NOTICES.md](NOTICES.md).

## Contributing

```bash
python3 -m venv .venv && . .venv/bin/activate
python -m pip install -e ".[dev,server]"
ruff check src tests tests_server
python -m pytest tests tests_server -q
scripts/check-public-fixtures
```

You can also run it inside the repository without installing: `PYTHONPATH=src python3 -m chinalaw ...`. The panel front end is in `web/` (Node 22): build with `npm ci && npm run build`, and run the browser acceptance tests with `npm test` (run `npx playwright install chromium` first). Build output goes to `src/chinalaw/server/static/` and is committed with the source. See [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md) for the contribution process and [docs/DEVELOPMENT_GUIDE.md](docs/DEVELOPMENT_GUIDE.md) for module boundaries and test requirements. Three ground rules: a missing article must fail loudly; a fix should be a general rule for a class of problems, not a patch for one law; and a new external source must first state its origin, throttling, cleaning, traceability and failure modes.

Please file questions and suggestions as [GitHub Issues](https://github.com/nh59yytyd5-dev/chinalaw-cli/issues), and report security vulnerabilities privately as described in [SECURITY.md](SECURITY.md). Friends: [Linux.do](https://linux.do/).

## License and disclaimer

The code is licensed under the [Apache License 2.0](LICENSE); third-party dependencies and data sources are recorded in [NOTICES.md](NOTICES.md). Under Article 5 of the PRC Copyright Law, the Copyright Law does not apply to laws, regulations, or resolutions, decisions and orders of state organs; third-party websites, commentary, commercial databases and users' private materials remain subject to their own rights and terms of use.

This project only makes normative texts easier to search, organize and cite; it is not legal advice. Actual cases and transactions call for the judgment of a practicing lawyer or in-house counsel, and the latest text from the official publication channel prevails.
