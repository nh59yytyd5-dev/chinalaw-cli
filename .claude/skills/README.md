# chinalaw skills

> 给 agent（Claude Code / Codex / Cursor / Cline / OpenCode 等）的可复用法律
> 工作流 skill。每个 skill = 一个目录 + `SKILL.md`（含 YAML frontmatter）。

## 目录约定

`SKILL.md` 是事实标准（[choutos/agent-skills-spec](https://github.com/choutos/agent-skills-spec)
+ [obra/superpowers](https://github.com/obra/superpowers)
+ [opencode.ai/docs/skills](https://opencode.ai/docs/skills/) 全部兼容）：

```
.claude/skills/
└── <skill-name>/
    ├── SKILL.md          # 必需，第一行 YAML frontmatter
    ├── references/       # 可选：长文档（walkthrough / 模板 / 决策树详细分支）
    ├── scripts/          # 可选：可执行脚本
    └── assets/           # 可选：图 / 模板等资源
```

`SKILL.md` 头部必须是 YAML frontmatter，否则 agent 无法 lazy-load：

```yaml
---
name: chinalaw-checking
description: 中国法规引用核对工具。何时使用：用户给了一份含法条引用的内容（合同、备忘录、AI 草稿），要求验证引用准确性 / 状态 / 时间效力。
---

# 正文
```

## 工具中立

放在 `.claude/skills/` 下的 SKILL.md 同时被 Claude Code、OpenCode、Codex CLI、
Cursor 等主流 agent 框架识别（OpenCode 在
[官方文档](https://opencode.ai/docs/skills/)中明确兼容 `.claude/skills/`）。
也可放在用户级 `~/.claude/skills/` / `~/.agents/skills/` 下供任意目录使用。

## 现有 skill

| 路径 | 用途 | 类型 |
|------|------|------|
| [`chinalaw-using/SKILL.md`](./chinalaw-using/SKILL.md) | chinalaw CLI 总入口与决策树 | craft 总入口 |
| [`chinalaw-checking/SKILL.md`](./chinalaw-checking/SKILL.md) | 中国法规引用核对（AI 草稿 / 合同 / 起诉状） | craft 横切 |
| [`chinalaw-searching/SKILL.md`](./chinalaw-searching/SKILL.md) | 中国法规检索方法（6 大方法 + 4 walkthrough） | craft 横切 |
| [`chinalaw-maintaining/SKILL.md`](./chinalaw-maintaining/SKILL.md) | 本地数据库维护与备份 | craft 横切 |
| [`chinalaw-fetching/SKILL.md`](./chinalaw-fetching/SKILL.md) | 多源爬取补全（flk_npc / court_gongbao / court_main / spp_gov_cn） | craft 横切 |
| [`legal-research/SKILL.md`](./legal-research/SKILL.md) | 通用法律检索、法律备忘录、法规梳理 | 场景流程 |
| [`contract-review/SKILL.md`](./contract-review/SKILL.md) | 合同审查、合同风险清单、履行 / 违约 / 放款条件审查 | 场景流程 |

**两类 skill**：

- **craft 横切**：方法 / 决策 / 反模式，agent 长期 internalize；不绑具体场景
- **场景流程**：把多个 craft 在一个具体场景下串起来；用户提需求时直接命中

新场景通常组合调用：例如"审查合同里的法条引用是否准确"=
`contract-review`（流程） + `chinalaw-checking`（核对方法） +
`chinalaw-fetching`（缺条文时补全）。

## 安装到用户级

`scripts/install-skills` 把仓库内 `.claude/skills/*` symlink 到 `~/.claude/skills/`
+ `~/.agents/skills/`，更新仓库时自动跟进：

```bash
scripts/install-skills          # 安装（symlink）
scripts/install-skills --dry-run # 只打印将要做的操作
scripts/install-skills --copy   # 用 copy 替代 symlink（Windows / WSL 友好）
```

## 使用方式

agent 在仓库源码目录里跑命令时使用：

```bash
PYTHONPATH=src python3 -m chinalaw ...
```

CLI 已 `pip install -e .`（或 `scripts/install-local`）后：

```bash
chinalaw ...
```

## 维护要求

- skill 中出现的命令必须与 `docs/CONTRACT.md` 一致。
- 必须包含 `article: null`、`needs_fetch`、`pending_reference_in_pack`、
  `not_legal_conclusion` 的降级纪律。
- 不得要求 agent 凭模型记忆引用条文。
- 命令字段或语义变化时同步更新本目录所有 SKILL.md。
