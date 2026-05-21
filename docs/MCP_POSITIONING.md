# CLI / Skill / MCP Positioning

> 状态：实施中。对应 issue #86。本文约束 agent-facing surface 的边界，避免把
> `chinalaw` 做成又厚又漂移的多协议系统。

## 1. 结论

`chinalaw` 的主协议仍是 CLI + JSON + exit code。Skill 是 agent 使用纪律，MCP
是偏 MCP agent 的轻量适配层。

三者分工：

| 层 | 责任 | 不做 |
| --- | --- | --- |
| CLI | 原子能力、稳定 JSON、退出码、快照 / audit / fetch / cleaning | 不输出最终法律意见 |
| Skill | 告诉 agent 何时查、怎么查、缺失时怎么降级、哪些行为禁止 | 不承诺机器 schema |
| MCP | 暴露少量低上下文 tool，适配不擅长 shell 的 agent | 不复制 skill 文档，不绕过 CLI 契约 |

## 2. MCP 上下文预算

`tools/list` 目标：常规工具描述总量控制在 **6000 字符以内**。

原则：

- tool description 只写调用目的、风险等级和关键降级信号。
- 法律检索方法、合同审查流程、时间效力纪律放在 `.claude/skills/`。
- 详细参数和退出码放在 `chinalaw schema` / `docs/CONTRACT.md`。
- MCP 输出必须保留 `source_url`、status、warning / diagnosis、article-level evidence。

## 3. MCP parity checklist

新增 MCP tool 前必须回答：

- 是否能映射到公开 CLI 命令。
- 是否需要写库、联网、或触发 authority risk。
- 是否能通过 `chinalaw schema mcp --format json` 自省。
- 是否有 CLI 同等测试或 service 层测试。
- 是否会让 agent 绕过 `audit` / `snapshot` / `fetch` 诊断链。

当前 MCP tool 均为薄包装：

- `chinalaw_resolve` → `chinalaw resolve`
- `chinalaw_search` → `chinalaw search`
- `chinalaw_article` → `chinalaw article`
- `chinalaw_articles` → `chinalaw articles`
- `chinalaw_applicable` → `chinalaw applicable`
- `chinalaw_ensure` → `chinalaw ensure`

## 4. 设计边界

- CLI parser 负责真实参数解析。
- `src/chinalaw/metadata.py` 负责 agent-facing 命令元数据、risk、MCP tools schema。
- `docs/CONTRACT.md` 负责长期协议文本。
- `.claude/skills` 负责 agent 操作纪律。

如果四者漂移，以 CLI parser 的实际行为和 `metadata.py` 的 schema 自省为修复入口。

