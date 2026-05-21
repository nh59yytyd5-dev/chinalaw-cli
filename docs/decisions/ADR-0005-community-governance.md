# ADR-0005: Community governance — small core, ADR-driven, early users first

- 状态：Accepted（早期治理决策）
- 日期：2026-04-26
- 关联：[CONTRIBUTING.md](../CONTRIBUTING.md)

## Context

chinalaw-cli 是 1 人在 13 周内交付的项目，但目标是**长期主义协议层**：
要避免变成"一个人无限维护的工具"，又不能在没用户时盲目搭社区。

参考过的失败模式：
- "建社区 → 拉新人 → 但没真实使用场景" → 社区空转、维护者燃尽。
- "完全 BDFL，所有 PR 必须维护者写" → 失去外部反馈，协议越来越偏。
- "一上来就治理委员会" → 形式 > 实质，新人贡献被流程吓走。

观察成功的小型基础设施项目（如 ripgrep、fd、jq 等）发现规律：
1. 早期靠 1-3 个核心维护者把协议钉死；
2. 任何破坏协议的改动必须先写决策记录；
3. 把"5 个真实用户"看得比"500 个 GitHub stars"重；
4. 主仓只接收符合协议的高质量贡献，社区贡献可以以"独立仓 + 链接"形式存在。

## Decision

**v0.x 阶段采用"小核心 + ADR 驱动 + 早期用户优先"的轻量治理。**

### 角色

- **maintainer（本期 = 1 人）**：拥有 master 推送权 + ADR 终决权。
- **contributor**：通过 PR 提交代码 / 数据 / 文档。
- **early user**：被邀请的真实用户，反馈直接进入公开 issue 或维护者路线图。

### 决策机制

1. **协议层改动**（CONTRACT.md / schema / CLI 契约）→ 必须先写 ADR，
   讨论 ≥ 48 小时，maintainer 合并 ADR 视作决议。
2. **实现层改动**（不影响协议的 bug fix / refactor）→ PR 直接合并。
3. **数据层改动**（fixture / pack）→ 走 PR + 数据来源核查；
   不允许引入商业版权材料（见 ADR-0004）。
4. **路线图改动** → maintainer 决策，重大调整需要在 issue 公开讨论。

### 贡献流程（详见 CONTRIBUTING.md）

- issue 模板：bug / feature-request / data-correction / protocol-change。
- PR 必须满足：
  - 改协议 → 链接对应 ADR；
  - 改代码 → 加测试；
  - 改 fixture → 提供来源 URL + checked_at；
  - 通过 `python3 -m unittest discover -s tests`。
- commit 风格：`feat:` / `fix:` / `docs:` / `refactor:` / `test:` 前缀。
- 大改之前先开 issue，避免做了一半发现方向不对。

### 沟通

- 主沟通：GitHub Issues / Discussions（英文 + 中文都接受）。
- 实时聊天：暂不建。等 W10（07/08-07/14）真实有人加入再考虑。
- 外部传播：W11 v0.1.0 发布后再写"Show HN"草稿；
  在那之前不主动宣传，避免吸引"看热闹的"压制真实反馈信号。

### 早期用户机制

- 早期用户反馈定期进入路线图复盘。
- 私下反馈应先抽象为不含个人信息和私域材料的公开 issue，再进入项目路线图。
- 任何用户提到"我每周用，如果它消失我会失望" → 那条反馈优先级 P0。

## Consequences

正面：
- 协议被钉得很紧，外部贡献的 surface 变小但**信噪比变高**。
- ADR 给了 future-self 一份对决策的留痕，避免"我为什么之前做了 X"。
- 早期用户占用决策权重远大于"GitHub 路过 issue"，避免被噪声拖走方向。
- 1 人维护可持续：treat issue 库为 backlog，不是邮箱；不承诺 24h 响应。

负面：
- 没有"开放治理委员会"会让一些贡献者觉得透明度不够；
  对策：所有协议层决策都写 ADR，全部公开；私域只剩用户反馈细节。
- 新贡献者的第一次 PR 经常因为"先开 issue"流程而延迟。
  对策：CONTRIBUTING.md 把"3 行修字 / 修正引用"列为可直接 PR 的快速通道。
- "1 个外部 PR 被合并"是 v0.2 硬目标，意味着 maintainer 主动培养 1 个外部贡献者
  ——这是工作量，要算入 13 周时间里。

## Alternatives considered

- **完全 BDFL，无 ADR**：失去对未来的留痕；
  即便 1 人也建议写 ADR，是给"半年后的自己"看。
- **早期就治理委员会**：与"5 个真实用户"的目标对冲。
- **所有外部 PR 都要 maintainer 写测试** —— 把贡献门槛抬高，
  最终扼杀 v0.2 的"1 个外部 PR" 目标。

## Follow-ups

- v0.1.0 发布后 4 周做一次治理复盘（写进 RETROSPECTIVE_v0.1.md）：
  - ADR 数量是否过多 / 过少？
  - 5 位用户中 ≥ 3 位说"每周用"？
  - 外部 PR 是 1 个还是 0 个？
- 当 maintainer 数量 ≥ 2 时，重写本 ADR，引入 2 名维护者的合并仲裁机制。
- 当出现 ≥ 1 个商用 fork 时，考虑公开 governance.md，引入"商标 / 项目名"
  的共识规则。
