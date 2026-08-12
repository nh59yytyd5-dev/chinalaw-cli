# 开发规范

> 本文档规定怎么改项目，避免文档和代码继续分叉。

## 1. 改动顺序

一般顺序：

1. 读 `README.md`、`PROJECT_CHARTER.md`、当前计划和 `CONTRACT.md`。
2. 确认当前实现，不按旧文档猜。
3. 新功能先在公开 issue / PR 中写最小设计说明。
4. 改代码。
5. 补测试。
6. 同步 CONTRACT / EXAMPLES / README。
7. commit。

## 2. 文档层级

| 文档 | 职责 |
|------|------|
| `PROJECT_CHARTER.md` | 项目定位，不写实现细节 |
| `REFACTOR_PLAN_20260806.md` | 当前审计重构计划 |
| `CONTRACT.md` | 外部可依赖协议 |
| `ARCHITECTURE.md` | 当前代码结构 |
| `EXAMPLES.md` | 可执行调用示例 |
| `CLEANING.md` | 清洗规则、alias、重建路径 |
| `DATA_INDEX.md` | 数据覆盖和补全路径 |
| issue / PR 设计记录 | 重要决策的 Context / Decision / Consequences |

不要在多个文档重复长篇战略论证。方向冲突时，以 `PROJECT_CHARTER.md`、当前公开
计划和 `CONTRACT.md` 为准。

## 3. 代码边界

### CLI

- `cli.py` 只处理参数、命令分发、退出码和输出格式。
- 复杂业务逻辑不要写在 CLI 层。
- 参数错误退出码为 `2`。

### Service

- `service.py` 负责公开法规检索、名称解析、条号定位、状态统计。
- 新增大能力时优先独立模块，不继续把所有逻辑塞进 `service.py`。

### Fetch / Sync

- `sync` 是维护者工具，参数可多。
- `fetch` 是 agent 高层入口，参数必须少、错误必须清楚。
- fetch 不应绕开 source metadata / hash / canonical id。

### Loader / Cleaning

- loader 只做幂等入库和 FTS 维护。
- cleaning 负责把外部来源变成 canonical payload。
- adapter 只负责取数。
- cleaning 规则变更后，要评估 `CLEANING_SCHEMA_VERSION`、`rebuild-clean` 和 `fetch --force`。

### Norm / Pack

- 私域规范必须保留 `source_type`、`authority`、`binding_scope`。
- 规范包短期是标签 / 收藏 / 问题域清单，不做复杂包生态。
- pending reference 不得伪装成 resolved article。

## 4. JSON 契约

任何 `--format json` 输出：

- 不删除已有字段。
- 新字段必须向后兼容。
- `null` 和缺字段含义要清楚。
- 错误输出必须有 `kind`、`error`、`message`。

进入 `CONTRACT.md` 的字段，就是外部 agent 可以依赖的字段。计划中的字段不要提前写成正式承诺。

## 5. Schema 规则

当前 schema 以 `src/chinalaw/schema.py` 中的 `SCHEMA_VERSION` 为准；截至
2026-08-06 为 v11。`docs/CONTRACT.md` 只记录外部可依赖的当前协议，不负责保留
每次 migration 的历史细节。

新增表、改字段、改约束前必须：

1. 在公开 issue / PR 中写设计记录。
2. 更新 `CONTRACT.md`。
3. 写 migration。
4. 保证旧库可自动迁移。
5. 写测试覆盖旧库升级。

`law_relations`、`applicability_rules` 已进入 alpha 协议；继续扩展它们仍需同步
contract、migration 和测试。`alias_records`、`call_log` 等未落库方向不得绕过
公开设计审查直接实现。

## 6. 测试要求

新增功能必须有测试。

最低要求：

- service / module 单元测试。
- CLI smoke test。
- JSON 输出关键字段断言。
- 错误路径和退出码断言。

fetch / sync 相关测试默认 mock 网络，不把真实外网作为 CI 前提。

## 7. 数据规则

- 不提交空条文但标成 `current` 的权威 fixture。
- stub 必须显式标明不可作为权威全文。
- 法律数据必须有 `source_url`、`source_name`、`source_checked_at`、`source_hash`。
- 私域规范不得提交真实敏感客户材料。

## 8. Git 规则

- 每个 commit 一个目的。
- commit message 用 `feat:` / `fix:` / `docs:` / `test:` / `refactor:`。
- 不把无关格式化和功能改动混在一起。
- 不 amend 已共享 commit，除非 maintainer 明确要求。
- 不重置用户未审查的改动。

## 9. Agent 工作规则

给 Codex / Claude Code 的默认规则：

- 不要直接查询 SQLite 表；缺查询能力先补公开 CLI / module API。
- 不要调用 `_...` 私有 helper 完成清洗、解析、名称解析；缺入口先补 `rebuild-clean` / `fetch` / `service` 的公开路径。
- 不要按旧计划实现新功能。
- 不要为了“完整”扩范围。
- 不要把商业 MCP 当竞争对象；它是未来上游 adapter。
- 不要让 CLI 输出最终法律意见。
- 先保证本机真实工作流能跑。
