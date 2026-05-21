# ADR-0001: Data Model — three-layer (laws / norm_sources / norm_packs)

- 状态：Accepted（历史决策；schema v7 时间效力部分由 ADR-0007 覆盖）
- 日期：2026-04-26
- 关联：[CONTRACT.md §2](../CONTRACT.md)

> 注：本文记录 schema v6 的建模决策。`law_relations` / `applicability_rules` 已由 ADR-0007 作为最小时间效力闭环引入；`alias_records` / `call_log` 仍需另写 ADR。

## Context

chinalaw-cli 想给 agent 提供一份"既准确又可追溯"的中国法律调用底座，
但具体要建模哪些实体、哪些表，是协议层最重要的决定之一。

观察早期用户（律师、学者、法律科技 builder、企业法务）的实际工作模式，
发现真实需求其实是 3 层叠加，而不是单层：

1. **公开法规**——人人共享的法条原文，必须可引用、可追溯。
2. **私域规范**——客户内部合规手册、风控政策、行业标准、企业规章。
   它们对真实判断的影响并不亚于公开法规，却没有任何工具显式建模。
3. **规范包**——围绕一个具体场景（如"合同纠纷裁判依据"）把几条法条
   + 几条私域条款 + 工作流提示打成一组，agent 一次拿走就能用。

如果只建模公开法规，私域规范全靠用户脑子记或写在 prompt 里——
失去了"事实底座"的意义。如果把三层揉成一张大表，校验和检索就会模糊。

也考虑过 v0.1 路线图里的 4 张高级表（law_relations / applicability_rules /
alias_records / call_log），但发现：
- 早期用户没有"我需要法规关系图"的诉求。
- alias / call_log 都是为了优化 agent 体验，**当前体验已经够用**。
- 一旦把这些表加进来，schema 复杂度会让协议层变厚，违反"30 分钟可读完"。

## Decision

把数据模型钉在三层 + 一张版本表 + 一张元数据表，schema 版本固定为 6：

| 表 | 角色 |
|----|------|
| `laws` / `articles` | 公开法规与条文 |
| `revisions` | 法规版本快照（含 `snapshot_json` 支持 as-of 查询） |
| `categories` / `law_categories` | 公开法规的分类树（来自 flk 等数据源） |
| `norm_sources` / `norm_clauses` | 私域规范来源与条款 |
| `norm_packs` / `norm_pack_items` | 规范包及其成员 |
| `*_fts` | 各实体的 FTS5 全文索引 |
| `meta` | 数据库级元数据（schema_version、last_sync_at、sync 进度） |

**本期不新增 schema 版本**，不引入 v0.1 路线图里的 4 张高级表。
任何新需求先 issue → ADR → 升级 schema，而不是直接加表。

## Consequences

正面：
- 协议层只需要描述 6 张业务表，CONTRACT.md 控制在可读完的篇幅。
- 私域规范 / 规范包都是 first-class 实体，agent 可以同样信任地引用。
- `revisions.snapshot_json` 让 as-of 查询不依赖 ALTER TABLE，
  历史版本的兼容压力降到最低。
- 三层之间的引用通过 `norm_pack_items.item_type` 显式区分（`law` /
  `article` / `norm_source` / `norm_clause` / `reference`），下游消费简单。

负面：
- 公开法规的"法规之间引用 / 援引关系"暂时无法表达——本期可接受，
  早期用户没有这个诉求，等真实信号再加 `law_relations`。
- `articles.UNIQUE(law_id, number)` 假设每法规内条款号唯一；遇到极少数
  法规（如中央 / 地方版本同号）需要在 `id` 命名上回避，不在 schema 上特殊处理。
- 时间效力推理（applicable）暂时只靠 `as-of` + `revisions`，不引入
  `applicability_rules`，意味着复杂的"溯及既往 / 法不溯及既往"判断要靠
  调用方自己结合 `effective_at` / `repealed_at` 推。

## Alternatives considered

- **统一一张 `documents` 大表**：不区分公开/私域，靠 `kind` 字段区分。
  否决理由：检索语义、引用元数据、校验规则差异大，糅合后任何变更都会
  牵动全部消费者，丧失协议清晰度。
- **直接上 schema v7 含 4 张高级表**：照搬 v0.1 路线图。
  否决理由：早期用户不需要、协议变厚、文档读不完。
- **把分类树和 FTS5 也写进 CONTRACT**：只把 `*_fts` 暴露成虚拟视图，
  不进入协议承诺。否决理由：FTS5 tokenizer 选择是实现细节，承诺会绑死。

## Follow-ups

- 当 ≥ 2 个早期用户在反馈中说"我需要看法规之间的引用关系"，开 ADR-0010 评估 `law_relations`。
- 如果有数据源开始提供精确的 `effective_at`/`repealed_at` + 修订条文 diff，
  再讨论是否引入 `applicability_rules`。
