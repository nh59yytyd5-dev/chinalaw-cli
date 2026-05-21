# ADR-0007: 时间效力最小闭环

## Status

Accepted.

## Context

法律工作流经常依赖事实发生时间。合同成立于 2019 年、履行跨越 2021 年、争议进入诉讼时适用哪个规范，不能只查“最新法条”。但 `chinalaw-cli` 的定位不是自动裁判，也不是维护完整商业数据库。

MVP 需要给 agent 一个稳定入口：当它意识到时间效力风险时，可以先拿到关系线索、规则文本、旧法补全提示和 warning，再决定是否继续 fetch / 检索 / 人工复核。

## Decision

引入 schema v7，但只增加两张表：

- `law_relations`：规范之间的显式关系，如 `replaces`。
- `applicability_rules`：按日期窗口、主题、场景返回的适用规则线索。

新增 CLI：

- `sync --applicability [--applicability-dir <dir>]`
- `relation <law>`
- `applicable --date YYYY-MM-DD [--topic TOPIC] [--law LAW] [--domain DOMAIN]`

输出必须遵守：

- 始终声明 grounding only，不给最终法律意见。
- 命中规则但法规缺失或只有 stub 时，必须返回 `needs_fetch`。
- 无规则命中不是程序错误，返回 warning，让 agent 回到 search/history/fetch。

## Deferred

本 ADR 不实现：

- `alias_records`
- `call_log`
- `get/article --applicable-on`
- 自动案情分类
- 自动时间效力结论
- 完整规则库维护体系

这些功能必须另写 ADR。

## Consequences

好处：

- agent-first 工作流有了“时间效力风险”入口。
- 本机规则可以从真实使用中逐步沉淀。
- 旧法缺失会显式暴露，不会假装可引用。

代价：

- seed 规则覆盖很窄，不能当完整法律数据库。
- 规则质量依赖人工审核。
- 需要后续将真实司法解释和官方文本逐步清洗入库。
