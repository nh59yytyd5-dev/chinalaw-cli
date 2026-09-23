# Agent 工作流接入

> 本文档给 Codex、Claude Code、Cursor 等本地 agent 提供调用约定。
> 原则：先查规范，再做判断；先输出依据，再输出结论。

## 1. 基本调用顺序

合同审查、合规核验、法律备忘录等任务中，agent 不应直接凭训练记忆回答法律依据。推荐顺序：

1. 抽取事实时间、争议焦点、关键词和可能相关的私域规范。
2. 如存在时间效力风险，用 `chinalaw applicable` / `chinalaw relation` 查 grounding 线索。
3. 用 `chinalaw search` 找候选规范。
4. 用 `chinalaw article` 精确定位关键条文。
5. 如果条文缺失，用 `chinalaw fetch <law> --article <number>` 尝试补全。
6. 如涉及公司制度、甲方要求、项目规则，先用 `chinalaw norm ingest/import` 纳入本地库。
7. 如任务可复用，可用 `pack` 作为标签 / 收藏 / 问题域清单。
8. 输出结论时明确区分国家法、私域制度和项目约束。

需要审查 agent 是否“查过再写”时，在项目目录初始化快照：

```bash
chinalaw snapshot init
```

之后 `search` / `article` / `articles` / `fetch` / `applicable` / `relation`
等命令会追加 compact evidence 到 `.chinalaw/snapshots/latest.jsonl`。最终报告生成后：

```bash
chinalaw audit grounding final.md --strict --format json
```

`verified` 表示引用回连到 article 级证据；`retrieved_only` 表示只搜过候选；
`ungrounded` 表示最终文本没有可追溯检索依据。

agent 不得直接读写 SQLite，也不得 import `_...` 私有 helper。需要浏览本地法规清单用 `laws`，需要重放清洗规则用 `rebuild-clean`，需要补条文用 `fetch`。

## 2. 合同审查最小流程

```bash
PYTHONPATH=src python3 -m chinalaw search 合同 履行 --format json
PYTHONPATH=src python3 -m chinalaw applicable --date 2022-01-01 --topic 合同效力 --format json
PYTHONPATH=src python3 -m chinalaw article 民法典 第五百零九条 --format json
PYTHONPATH=src python3 -m chinalaw fetch 民法典 --article 第五百八十五条 --format json
PYTHONPATH=src python3 -m chinalaw norm ingest company-policy.docx --name 公司合同审批制度 --source-type internal_governance
PYTHONPATH=src python3 -m chinalaw pack validate 放款审查基础包 --format json
PYTHONPATH=src python3 -m chinalaw pack show 放款审查基础包 --format json
```

## 3. Agent 输出纪律

- 引用国家法时，必须写明法规名称、条号、状态和来源。
- 引用私域规范时，必须写明制定主体、约束范围和来源类型。
- 不得把甲方要求、公司制度、项目 memo 表述为“法律规定”。
- 如果 `pack validate` 不通过，先报告缺失依赖，不要强行完成审查结论。
- 如果只检索到候选条文，尚未 `article` 精确定位，不要把候选命中当成最终依据。
- 如果事实时间可能影响适用法律，必须使用 `applicable` / `relation` / `history` 查询线索，并提示时间效力不确定性。
- 如果 `applicable` 返回 `not_legal_conclusion`、`needs_fetch`、`law_missing`、`law_stub` 或 `law_seed`，不得写成确定适用结论。
- 如果 fetch 失败，必须说明本地未命中且远程补全失败，不得继续输出确定引用。
- 如果 cleaning 规则升级或发现旧入库结果未派生 alias，使用 `rebuild-clean` 或 `fetch --force`，不要手工改 DB。

## 4. 推荐 Prompt 片段

```text
你在执行法律/合同审查任务时，必须先调用 chinalaw 查询规范依据。
优先使用 JSON 输出。
如果存在规范包，先运行 pack validate，再运行 pack show。
如果事实时间可能影响适用法律，提示时间效力风险，并查询 applicable / relation / history / as-of / fetch 可用旧法。
结论中必须区分国家法、私域规范、交易对手要求和项目约束。
不得凭模型记忆编造条文或来源。
不得直接读写 chinalaw SQLite 或调用私有 helper；缺能力时先使用/扩展公开 CLI。
```

## 5. 可复用 Skill 模板

项目内置 7 份 skill 模板（事实标准 SKILL.md 目录 + YAML frontmatter，
Claude Code / OpenCode / Codex CLI / Cursor / Cline / superpowers 都自动识别）。

**craft 横切**（方法 / 决策 / 反模式，agent 长期 internalize）：

- `.claude/skills/chinalaw-using/SKILL.md` —— 总入口与决策树（4 句心法 + 任务路由）
- `.claude/skills/chinalaw-checking/SKILL.md` —— AI 引用核对（拆解 / article / 文本比对 / 状态 / 时间效力 / 缺失补全）
- `.claude/skills/chinalaw-searching/SKILL.md` —— 检索方法（6 大方法 + 4 实战 walkthrough）
- `.claude/skills/chinalaw-maintaining/SKILL.md` —— 本地数据库维护（多项目布局 / SOP / doctor.sh）
- `.claude/skills/chinalaw-fetching/SKILL.md` —— 多源爬取补全（源选择决策树 + 失败降级 + 文号 one-shot）

**场景流程**（craft 在具体场景下串起来）：

- `.claude/skills/legal-research/SKILL.md` —— 通用法律检索 / 法律备忘录 / 法规梳理
- `.claude/skills/contract-review/SKILL.md` —— 合同审查 / 风险清单 / 履行 / 违约 / 放款条件审查

新场景通常组合调用：例如"审查合同里的法条引用是否准确" =
`contract-review`（流程） + `chinalaw-checking`（核对方法） +
`chinalaw-fetching`（缺条文时补全）。

这两类模板都是给 agent 的工作流约束，不属于 CLI 协议；如果命令字段变化，必须同步更新。

**0.5.1 起 skills 默认不装载**：以上 SKILL.md 留在仓库内作为使用说明，
`setup-agent` / `update-local` 不会再自动安装它们。如需把仓库内 skill
symlink 到用户级 `~/.claude/skills/` + `~/.agents/skills/`（opt-in）：

```bash
scripts/install-skills            # 安装（symlink）
scripts/install-skills --dry-run  # 干跑预览
scripts/install-skills --copy     # Windows / WSL 友好
scripts/install-skills --uninstall # 移除指向本仓库的安装
```

Windows PowerShell 原生环境使用复制安装：

```powershell
.\scripts\install-skills.ps1
.\scripts\install-skills.ps1 -DryRun
```

see also: [`.claude/skills/README.md`](../.claude/skills/README.md) 总索引与设计说明。

## 6. 安装规范语料

`chinalaw init` 加载的随包基线覆盖常用法典、程序法和主要司法解释（清单见
[`DATA_INDEX.md`](./DATA_INDEX.md)）。任务涉及基线之外的规范时按下面的顺序补库，
不要直接改 SQLite：

1. 确认本机健康：`chinalaw doctor --format md`、`chinalaw status --format md`。
2. 查看推荐语料 profile：`chinalaw corpus list --format md`；
   `chinalaw corpus show general --format md` 展开清单（`--no-deps` 不展开依赖）。
   `data/recommended_corpus.json` 只是安装索引，不是权威文本。
3. 先问用户用途再装，按缺口逐部补：`chinalaw ensure 劳动合同法 --format md`。
   只有用户明确要批量预装并接受官方源限流风险时才用
   `chinalaw ensure --profile general --format md`（`--no-profile-deps` 不带依赖 profile）。
4. 安装后复核：`chinalaw status --format md`、`chinalaw resolve <法规名> --format json`。

| 用途 | profile |
|------|---------|
| 随包基线 | `baseline` |
| 通用法律检索 / 合同 / 民商基础 | `general` |
| 合同审查 / 民商争议 | `contracts` |
| 民法典施行前的旧民商法 | `civil-history` |
| 公司商事 / 投融资 / 破产 | `company` |
| 劳动争议 / HR 合规 | `labor` |
| 刑事辩护 / 刑事合规 | `criminal` |
| 行政诉讼 / 政府合规 | `admin` |
| 房地产 / 建设工程 | `real-estate` |
| 婚姻家庭 / 继承 | `family` |
| 知识产权 / 反不正当竞争 | `ip` |
| 证券 / 资本市场 | `securities` |

失败处理：

- `unsupported_source`：当前 adapter 未实现，把法规名和来源反馈给用户，不要声称已安装。
- `manual_review`：该条目的公开 fetch 路径尚未稳定；除非用户提供官方 URL 另行 fetch
  或人工入库，不得引用为已安装规范。
- `FetchAmbiguousError`：`chinalaw fetch <name> --source <source> --list-matches --format json`
  列出候选，再用 `--prefer-id` 精取。
- `FetchNotFoundError`：先 `chinalaw resolve <name>` 排除本地别名问题，仍失败再报告找不到。
- `FetchSourceError`：`chinalaw verify-source <source> --format json` 判断是否上游改版；
  属于项目缺陷时创建 issue。
- FLK 返回反爬 JavaScript 挑战：停止批量安装，改为单部 `ensure`，间隔一段时间后再试
  （见 issue #97）。

`needs_verification=true`、`law_stub`、`law_seed`、`article_null` 都表示不能直接引用，
必须继续 fetch / verify-source，或明确告知用户本地数据不足。不要读取用户素材目录的正文
来猜法规名；给了目录时只用文件名，或让用户确认。
