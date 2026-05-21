# ADR-0002: CLI + JSON contract as the only public surface

- 状态：Accepted
- 日期：2026-04-26
- 关联：[CONTRACT.md §4](../CONTRACT.md), [PERSPECTIVE.md](../PERSPECTIVE.md)

## Context

chinalaw-cli 要被 agent / 律师 / 学者使用，候选的对外接口形态有：

1. **CLI + JSON 输出**：`chinalaw search ...` → JSON。
2. **Python SDK**：`from chinalaw import ...`，作为库被 import。
3. **HTTP API**：长期跑一个 server。
4. **MCP server**：实现 Model Context Protocol。
5. **`chinalaw://` URI 协议层**：抽象的 URI 寻址法。

v0.1 路线图把 5 当成"协议层抽象"的终点，但 [PERSPECTIVE.md](../PERSPECTIVE.md)
判断这是**过度抽象**：协议要薄，不要给 agent 多一层学习成本。

观察 Claude Code / Cursor / 自建 agent 的真实调用方式，发现：
- 它们已经熟练地从 shell 里跑 CLI 并把 JSON 解析回去。
- 它们对额外 SDK / HTTP 服务都有学习成本与运行环境成本。
- "本地 SQLite + CLI" 的组合让用户的数据**不会上传任何地方**，
  这是中立性的关键。

## Decision

**v0.1 阶段的对外契约 = CLI + JSON 输出。**

具体规则：

1. 每个命令的入参、JSON schema、退出码全部写进 [`CONTRACT.md §4`](../CONTRACT.md)；
   破坏需要 ADR + 主版本号变更。
2. JSON 是首选，Markdown (`--format md`) 信息量等价（人眼用）。
3. **不发布 Python SDK**：不承诺 `chinalaw.service.*` 模块路径稳定。
   有人需要 SDK 可以自己 fork。
4. **不实现 MCP server**：早期用户用 Claude Code / Cursor 都直接跑 shell 命令，
   多一层 MCP 反而增加部署复杂度。等真实需求出现再开 ADR。
5. **不引入 `chinalaw://` URI**：JSON 中的 `id` / `source_url` 已经够定位。
6. CLI 退出码：`0` 成功；`1` 业务级 not found；`2` 参数 / 前置错误。
   agent 可以根据退出码做 retry / fallback。
7. `--format json` 输出必须是 stdout 可解析的有效 JSON，不夹杂日志。
   日志走 stderr（本期暂未启用，但禁止把任何提示打到 stdout）。

## Consequences

正面：
- 任何能跑 shell + 解析 JSON 的环境都能接入：Claude Code、Cursor、Aider、
  自建 langchain agent、LangGraph、bash 脚本……零额外依赖。
- 协议层薄到可以写进一份文档，重写实现者（Rust / Go）只需要照 schema
  + JSON 输出复刻，不需要学专门的 RPC。
- 用户数据不离开本地。SQLite 单文件可拷贝、可备份、可 diff。

负面：
- 高频调用场景（agent 在循环里每秒 10 次）有进程启动开销。
  本期可接受，因为早期用户场景都是**人在工作流里手工触发或低频代理触发**。
- 想用富对象编程的开发者（"我想 import 一个 Law 类"）需要自己写 wrapper。
- 长进程场景（Web 后端、长期 agent）不优雅；解决方案是后续加 MCP server
  作为可选层，但**底层契约不变**。

## Alternatives considered

- **CLI + Python SDK 双契约**：双契约就有双兼容压力，且 Python SDK 会
  强迫早期用户先 `pip install`，提高门槛。先观察是否有真实需求。
- **MCP-first**：MCP 还没普及到 Claude Code 之外；以 MCP 为主会绑住
  生态选择，违反中立性原则。可以**加**而不能**只做** MCP。
- **HTTP API**：本地起 server 与"零运行时依赖"冲突；用户要承担端口管理。

## Follow-ups

- 当 ≥ 1 个早期用户主动说"我想长进程跑、CLI 启动开销让我难受" → 评估 MCP server。
- 当 ≥ 1 个外部贡献者用 Rust / Go 重写并发布 → 复盘 CONTRACT.md 是否还需要补字段。
- 持续监控：每加新命令 / 新字段时，先想清楚是不是协议层（写进 CONTRACT）。
