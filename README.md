# chinalaw

> 面向 AI agent 的中国法律法规检索 CLI。

`chinalaw` 是一个 local-first 的规范检索工具：把公开法律、行政法规、
司法解释和常用规范清洗成可被本机 agent 查询、引用和复核的 SQLite 数据库。

它解决的是一个基础问题：Codex、Claude Code、Cursor、OpenCode、Aider 等能调
shell 的 agent，在写合同审查、法律备忘录、引用核对或制度分析时，应先查本机规范
来源，而不是凭模型记忆编法条。

当前状态：`v0.1.1`。核心检索命令可用；fetch / 多源补全按具体来源持续迭代。

## Why

法律 agent 的核心风险不是“写不出法律文字”，而是：

- 没检索就直接回答。
- 引用了不存在、已废止或错版本的条文。
- 缺少本地数据时没有 fail loud，反而继续幻觉补全。
- 人类很难复核它到底查了什么、用了什么来源。

`chinalaw` 的定位是本地规范基础设施：提供可脚本化查询、稳定 JSON 输出、
退出码、来源元数据和内容 hash，让 agent 的法律引用链可追溯。

## Features

- **Local-first**：默认数据库是 `~/.chinalaw/chinalaw.db`，工作材料不上传远端。
- **Agent-first CLI**：核心命令支持 JSON 输出、稳定退出码和机器可读错误。
- **One-command init**：`chinalaw init` 加载随包公开规范基线并运行健康检查。
- **Article-level grounding**：按法规名、俗称、条号和关键词检索到条文级结果。
- **Bundled public corpus**：随仓库提供 74 个完整可引用的公开规范 fixture，
  不是 seed、stub 或 demo 数据。
- **Source metadata**：输出保留来源、核查时间、状态、版本和 `source_hash`。
- **On-demand fetch**：可按需从公开官方来源补全文本，并统一清洗入库；覆盖效果取决于
  具体源适配器。

## Install

macOS / Linux / WSL：

```bash
git clone https://github.com/nh59yytyd5-dev/chinalaw-cli.git
cd chinalaw-cli
scripts/install-local
scripts/setup-agent

chinalaw init
chinalaw article 民法典 524 --format card
```

Windows PowerShell：

```powershell
git clone https://github.com/nh59yytyd5-dev/chinalaw-cli.git
cd chinalaw-cli
.\scripts\install-local.ps1
.\scripts\setup-agent.ps1

chinalaw init
chinalaw article 民法典 524 --format card
```

如果 shell 找不到命令，把本地 bin 目录加入 `PATH`：

```bash
export PATH="$HOME/.local/bin:$PATH"
```

Windows 下把 `%USERPROFILE%\.local\bin` 加入用户 `Path` 后重开终端：

```powershell
[Environment]::SetEnvironmentVariable(
  'Path',
  "$HOME\.local\bin;" + [Environment]::GetEnvironmentVariable('Path', 'User'),
  'User'
)
```

更新本机安装：

```bash
cd chinalaw-cli
git pull
scripts/update-local
chinalaw init
```

Windows PowerShell：

```powershell
cd chinalaw-cli
git pull
.\scripts\update-local.ps1
chinalaw init
```

未安装时也可以在仓库内运行：

```bash
PYTHONPATH=src python3 -m chinalaw article 民法典 524 --format card
```

## Quick Start

```bash
# 1. 初始化本地库：加载内置公开规范基线并运行健康检查
chinalaw init

# 2. 解析法规俗称 / 简称
chinalaw resolve 民法典 --format json

# 3. 关键词检索
chinalaw search 合同效力 --kind article --limit 5 --format md

# 4. 按条取法条
chinalaw article 民法典 第一百四十三条 --format md
chinalaw article 民法典 524 --format card

# 5. 批量取条
chinalaw articles 民法典 --numbers "143,464,509,577" --format json

# 6. 查看目录
chinalaw outline 民法典 --limit 20 --format md
```

健康检查：

```bash
chinalaw doctor --format md
chinalaw status --format md
```

本地缺条文时，可以按需补全：

```bash
chinalaw ensure 民法典 --format json
chinalaw fetch 民法典 --article 第五百八十五条 --format json
```

`fetch` 会访问公开来源，可能受上游结构变化、网络和限流影响；生产工作流应检查
返回的 `ok`、`error`、`source_name`、`source_url`、`source_checked_at` 和
`source_hash`。

## Initial Built-in Corpus

`sync --fixtures` 加载的是完整可引用的公开规范基线。CI 会运行
`scripts/check-public-fixtures`，禁止 seed / stub、空条文、残缺覆盖和缺来源元数据的
fixture 进入公开发布集。

当前初始库包含 74 个完整 fixture：

| 范围 | 已内置规范 |
| --- | --- |
| 基础法典 / 程序法 | 宪法历次文本、民法典、刑法历次合并文本、民事诉讼法、刑事诉讼法、行政诉讼法、仲裁法 |
| 通用行政 / 劳动 / 数据 | 行政处罚法、行政复议法、国家赔偿法、劳动法、劳动合同法、劳动争议调解仲裁法、个人信息保护法、数据安全法、网络安全法、消费者权益保护法、治安管理处罚法、诉讼费用交纳办法 |
| 民商事 / 公司金融 | 公司法、合伙企业法、企业破产法、外商投资法、票据法、保险法、证券法、电子商务法 |
| 民法典配套解释 | 时间效力规定、总则编解释、物权编解释（一）、合同编通则解释、担保制度解释、侵权责任编解释（一）、婚姻家庭编解释（一）（二）、继承编解释（一） |
| 诉讼 / 合同 / 劳动司法解释 | 民事诉讼法解释、刑事诉讼法解释、行政诉讼法解释、民事诉讼证据规定、民间借贷规定、买卖合同解释、融资租赁合同解释、独立保函规定、建设工程施工合同解释（一）、商品房买卖合同解释、劳动争议解释（一） |

未列入上表的 `data/recommended_corpus.json` 条目只是安装 / 补全建议，不表示已经
随包内置。agent 只有在 `resolve` / `article` / `articles` / `search` 实际命中并返回
来源元数据后，才能把该规范作为引用依据。

## Agent Usage

把下面这段加入 Codex / Claude Code / Cursor / OpenCode 的全局规则：

```text
涉及中国法、法条、司法解释、合同审查、劳动/公司/民商事/刑事问题时，
先使用本机 chinalaw CLI 查询，不要先凭模型记忆回答。
常用命令：
- chinalaw resolve <name> --format json
- chinalaw search <query> --kind article --limit 10 --format json
- chinalaw article <law> <number> --format json
- chinalaw articles <law> --numbers <nums> --format json
- chinalaw ensure <law> --format json
如果返回 law_missing、law_stub、law_seed、article_null 或 needs_fetch，
应按诊断信息补全或明确告知本地数据不足。
```

仓库内置 `.claude/skills/`，可作为 agent 工作流说明参考。安装到用户级 skills 目录：

```bash
scripts/install-skills --copy
```

Windows PowerShell：

```powershell
.\scripts\install-skills.ps1
```

## Core Commands

| Command | Purpose |
| --- | --- |
| `init` | 加载随仓库发布的公开规范基线并运行健康检查 |
| `resolve <name>` | 把俗称、简称或模糊名称解析到本地法规记录 |
| `search <query>` | 全文 / 条文检索 |
| `get <name>` | 获取法规元数据和正文摘要 |
| `article <law> <number>` | 按法规 + 条号定位单条 |
| `articles <law> --numbers ...` | 批量取条 |
| `outline <law>` | 查看条文目录和预览 |
| `sync --fixtures` | 加载随仓库发布的公开规范基线 |
| `ensure <law>` | 本地优先检查，缺失时尝试补全 |
| `fetch <law>` | 从公开来源抓取、清洗、入库（preview） |
| `doctor` / `status` | 本机健康检查 |

默认输出 JSON。多数命令可加 `--format md` 得到人类可读输出。

## Data And Sources

当前公开源 adapter 包括：

- 国家法律法规数据库：`flk_npc`
- 国家行政法规库（国务院入口 / 司法部承载）：`gov_xzfgk`
- 最高人民法院公报：`court_gongbao`
- 最高人民法院主站：`court_main`
- 最高人民检察院：`spp_gov_cn`
- 中国证监会：`csrc_gov_cn`
- 证券交易所和自律规则：`sse_com_cn`、`szse_cn`、`bse_cn`、`chinaclear_cn`、`sac_net_cn`

数据进入本地库前必须经过 cleaning，并保留来源、检查时间、哈希和状态字段。
涉及外部抓取时，请阅读 [docs/COMPLIANCE.md](docs/COMPLIANCE.md)，保持低频、
可复核、不过度请求。

## MCP

CLI 是主路径；仓库也提供轻量 MCP wrapper，方便偏 MCP 的 agent 以低上下文方式调用
核心检索能力。

```bash
chinalaw-mcp --db ~/.chinalaw/chinalaw.db
```

MCP 只应作为 CLI 的薄封装，不应引入另一套法律判断逻辑。

## Documentation

- [docs/PROJECT_CHARTER.md](docs/PROJECT_CHARTER.md)：项目定位和边界。
- [docs/CONTRACT.md](docs/CONTRACT.md)：CLI / JSON / 退出码契约。
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)：代码结构。
- [docs/CLEANING.md](docs/CLEANING.md)：清洗规则。
- [docs/DATA_INDEX.md](docs/DATA_INDEX.md)：内置数据说明。
- [docs/EXAMPLES.md](docs/EXAMPLES.md)：使用示例。
- [docs/COMPLIANCE.md](docs/COMPLIANCE.md)：公开来源抓取合规边界。
- [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md)：贡献指南。

## Development

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e ".[dev]"

scripts/check-public-fixtures
PYTHONPATH=src python -m compileall -q src tests
ruff check src tests
PYTHONPATH=src python -m unittest discover -s tests -v
python -m build
```

本仓库的首要质量规则：

- 法条缺失时必须 fail loud，不得让 agent 误以为引用成功。
- 修复应是一类问题的一般规则，不应为单个评测题或单部法规写硬编码补丁。
- 新增外部来源必须先说明来源、节流、清洗、追溯和失败模式。

## Community

- Issues: [GitHub Issues](https://github.com/nh59yytyd5-dev/chinalaw-cli/issues)
- 友链：[Linux.do](https://linux.do/)

## License And Disclaimer

代码采用 [Apache License 2.0](LICENSE)。第三方依赖与数据来源登记见 [NOTICES.md](NOTICES.md)。

法律、法规、国家机关决议、决定、命令和其他具有立法、行政、司法性质的文件，
依《中华人民共和国著作权法》第五条不适用著作权法保护。第三方网站、释义材料、
商业数据库和用户私域材料仍应分别遵守其来源权利和使用限制。

本项目仅提供规范文本检索、整理和引用便利，不构成法律意见。实际案件和交易事项
应由执业律师或负责法务最终判断，并以官方发布渠道的最新文本为准。
