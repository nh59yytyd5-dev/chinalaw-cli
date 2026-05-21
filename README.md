# chinalaw

> 面向 AI agent 的中国法律法规检索 CLI。

`chinalaw` 是一个 local-first 的法规检索工具：把公开法律、行政法规、司法解释和部分常用规范清洗成可被本机 agent 查询、引用和复核的 SQLite 数据库。

它不是法律问答机器人，也不是商业法律数据库的替代品。它解决的是一个更基础的问题：Codex、Claude Code、Cursor、OpenCode、Aider 等能调 shell 的 agent，在写合同审查、法律备忘录、引用核对或制度分析时，应先查本机规范来源，而不是凭模型记忆编法条。

当前状态：`v0.1.0` public preview。CLI / JSON 字段仍可能在 v0.x 阶段调整；涉及法律判断时，请以官方发布渠道和专业人士复核为准。

## Why

法律 agent 的核心风险不是“写不出法律文字”，而是：

- 没检索就直接回答。
- 引用了不存在、已废止或错版本的条文。
- 缺少本地数据时没有 fail loud，反而继续幻觉补全。
- 人类很难复核它到底查了什么、用了什么来源。

`chinalaw` 的定位是本地规范基础设施：提供可脚本化查询、稳定 JSON 输出、退出码、来源元数据和内容 hash，让 agent 的法律引用链可追溯。

## Features

- **Local-first**：默认数据库是 `~/.chinalaw/chinalaw.db`，工作材料不上传远端。
- **Agent-first CLI**：核心命令支持 JSON 输出、稳定退出码和机器可读错误。
- **Article-level grounding**：支持按法规名、俗称、条号和关键词检索到条文级结果。
- **Bundled fixtures**：随仓库提供一组可离线加载的公开法规 fixture，方便首次验证和基础工作流。
- **Source metadata**：输出保留来源、核查时间、状态、版本和 `source_hash`，便于复核。
- **Optional fetch**：可按需从公开官方来源补全文本；该能力仍是 preview，默认不承诺全量覆盖。

## Install

### From Source

```bash
git clone https://github.com/nh59yytyd5-dev/chinalaw-cli.git
cd chinalaw-cli
scripts/install-local
```

如果 shell 找不到命令，把本地 bin 目录加入 `PATH`：

```bash
export PATH="$HOME/.local/bin:$PATH"
```

更新本机安装：

```bash
cd chinalaw-cli
git pull
scripts/update-local
```

### Without Installing

```bash
cd chinalaw-cli
PYTHONPATH=src python3 -m chinalaw --help
```

## Quick Start

```bash
# 1. 加载随仓库提供的公开 fixture
chinalaw sync --fixtures

# 2. 解析法规俗称 / 简称
chinalaw resolve 民法典 --format json

# 3. 关键词检索
chinalaw search 合同效力 --kind article --limit 5 --format md

# 4. 按条取法条
chinalaw article 民法典 第一百四十三条 --format md
chinalaw article 民法典 524 --format json

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

`fetch` 会访问公开来源，可能受上游结构变化、网络和限流影响；生产工作流应检查返回的 `ok`、`error`、`source_name`、`source_url`、`source_checked_at` 和 `source_hash`。

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

## Core Commands

| Command | Purpose |
| --- | --- |
| `resolve <name>` | 把俗称、简称或模糊名称解析到本地法规记录 |
| `search <query>` | 全文 / 条文检索 |
| `get <name>` | 获取法规元数据和正文摘要 |
| `article <law> <number>` | 按法规 + 条号定位单条 |
| `articles <law> --numbers ...` | 批量取条 |
| `outline <law>` | 查看条文目录和预览 |
| `sync --fixtures` | 加载随仓库发布的 fixture |
| `ensure <law>` | 本地优先检查，缺失时尝试补全 |
| `fetch <law>` | 从公开来源抓取、清洗、入库（preview） |
| `doctor` / `status` | 本机健康检查 |

默认输出 JSON。多数命令可加 `--format md` 得到人类可读输出。

## Data And Sources

随仓库发布的 `data/fixtures/` 是公开来源文本的轻量基线，不是全量法律数据库。每条记录都应包含：

- `source_name`
- `source_url`
- `source_checked_at`
- `source_hash`
- `status`

当前代码包含多个公开来源 adapter，包括国家法律法规数据库、国家行政法规库、最高法、最高检、证监会及部分证券自律规则来源。公开预览期不承诺每个来源都已达到同等稳定度；涉及外部抓取时，请阅读 [docs/COMPLIANCE.md](docs/COMPLIANCE.md) 并保持低频、可复核、不过度请求。

## MCP

CLI 是主路径；仓库也提供轻量 MCP wrapper，方便偏 MCP 的 agent 以低上下文方式调用核心检索能力。

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

PYTHONPATH=src python -m compileall -q src
ruff check src
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

法律、法规、国家机关决议、决定、命令和其他具有立法、行政、司法性质的文件，依《中华人民共和国著作权法》第五条不适用著作权法保护。第三方网站、释义材料、商业数据库和用户私域材料仍应分别遵守其来源权利和使用限制。

本项目仅提供规范文本检索、整理和引用便利，不构成法律意见。实际案件和交易事项应由执业律师或负责法务最终判断，并以官方发布渠道的最新文本为准。
