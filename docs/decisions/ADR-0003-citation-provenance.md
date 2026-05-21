# ADR-0003: Citation provenance is mandatory for every record

- 状态：Accepted
- 日期：2026-04-26
- 关联：[CONTRACT.md §3](../CONTRACT.md), [DIFFERENTIATION.md](../DIFFERENTIATION.md)

## Context

中国法律 agent 的最大痛点：**LLM 引用幻觉**。常见表现包括：

- 编造不存在的条款号；
- 引用的条款号正确但内容错位（"某法第十条说……"实际上不是那条）；
- 引用过时版本但不告知用户；
- 引用了来源不清的所谓"司法解释"。

要解决的不只是"看起来正确"，而是要让任何下游消费者**能在 5 秒内
回到原始页面亲眼核对**。这是 chinalaw-cli 与通用 LLM 工具的根本差异。

## Decision

**每条公开法规、每个私域规范、每条私域条款都必须带可追溯的引用元数据。**
具体规则：

1. 公开法规与私域规范的表都强制 4 个字段 `NOT NULL`：
   - `source_url`：原始页面或文件路径（公开法规必须是 URL；私域可以是
     `local-file:./path/to/policy.docx`）。
   - `source_name`：来源标识（如 `flk.npc.gov.cn`、`local-file`）。
   - `source_checked_at`：ISO 8601 datetime（带时区），最后一次核查时间。
   - `source_hash`：SHA-256 hex；公开法规对原始响应体或文档计算，
     私域对 clause JSON 标准化序列化后计算。
2. 派生字段 `freshness_days`（在 JSON 输出中存在，DB 中不存）告知调用方
   "这条记录已经多久没核查"，agent 可以据此判断是否需要重新 sync。
3. **`as-of` 查询返回的 selected_revision 也必须带 `content_hash` 与
   `released_at` / `effective_at`**，调用方能复现"我看到的是这个版本的这个条款"。
4. CLI 命令在 JSON 输出中**必须**至少包含 `source_url` 与 `freshness_days`；
   `--format md` 模式必须把这两个字段渲染出来给人眼看。
5. 任何 import / sync 路径都不能写入缺失上述字段的记录——
   这是**写入门槛**，不是**输出装饰**。

## Consequences

正面：
- agent 在生成回答时可以直接拼出"《XX法》第 N 条（来源：URL，
  最后核查 X 天前）"，不再需要额外构造引用。
- 用户在审稿、辩护、合规审查中可以一键回到原始页面，
  这是"AI 法律建议"目前最缺的环节。
- 内容指纹让"是否变更"成为客观事实——可以做版本比对、变更告警、
  sync 增量识别。
- 私域规范也享受同等待遇，让用户**信任内部材料经过 chinalaw-cli 一遍**
  之后是带溯源的，不会被 agent 模糊化。

负面：
- 给数据贡献者增加门槛：贡献一部新法规必须填齐 4 个字段。
  对策：`sync --source flk_npc` 自动填；手工准备时给模板。
- 对私域规范来说，`source_url` 经常没有。**允许 `null`**（schema 是 NULL-able），
  但 `source_name` 必须填明用户可识别的标识（"风控部 2026-01 PDF"）。
- 用户离线、断网情况下无法核查 URL——可接受，因为 hash + checked_at
  仍然给了"过去某时点这条信息存在过"的承诺。

## Alternatives considered

- **只在 sync 时记录、查询时不返回**：这样 agent 拼引用还是要二次查询，
  不如一次返回。
- **把引用元数据当 metadata json 字段**：会绕开 SCHEMA 强制；
  也会让重写实现者不知道哪些字段是核心。
- **仅承诺 source_url，hash 可选**：放弃 hash 等于放弃"内容指纹"，
  无法做幂等 sync。
- **强制带签名 / 时间戳服务**：超出本期范围；hash + checked_at 已经
  给出实践上够强的承诺。

## Follow-ups

- 增加 `chinalaw verify <law>` 命令，重新拉源、重算 hash、对比当前 DB —— 等真实需求触发。
- 引用元数据若被外部生态广泛使用，可考虑标准化为 JSON-LD / `did:` 类型，
  但**不是本期**。
