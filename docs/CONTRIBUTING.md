# Contributing to chinalaw-cli

> 谢谢你愿意贡献。这个项目目标是给中国法律 agent 工作流提供**协议级**的事实底座；
> 协议比代码重要、数据来源比花活重要、真实使用比 stars 重要。
>
> 第一次贡献？先读 [docs/CONTRACT.md](./CONTRACT.md)（30 分钟），
> 这是项目唯一的契约。

## 1. 我能贡献什么？

| 类型 | 说明 | 入口 |
|------|------|------|
| Bug 修复 | 任何与 [`CONTRACT.md`](./CONTRACT.md) 描述不一致的实际行为 | 直接 PR |
| 改善 import | 数据加载器、私域规范 ingest、解析器更稳健 | 直接 PR |
| 数据修正 | fixture / pack JSON 的条文勘误、来源 URL 失效 | 直接 PR + 来源链接 |
| 新规范包 | 围绕一个具体场景的 norm_pack JSON | 直接 PR + `validate` 通过 |
| 新公开法规 fixture | 民商事相关的 P0/P1 法规种子 | 直接 PR + 来源核查 |
| 文档 / 示例 | README / EXAMPLES / CONTRACT 修订 | 直接 PR |
| 协议层修改 | 改变 schema / CLI 契约 / JSON 输出 | **先开 issue + ADR** |

## 2. 准备开发环境

需求：Python ≥ 3.11，仅依赖 stdlib。

```bash
git clone https://github.com/<your-fork>/chinalaw-cli.git
cd chinalaw-cli
PYTHONPATH=src python3 -m chinalaw status      # 应报告当前 schema_version
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

如果你想在本地装为可执行命令：

```bash
pip install -e .
chinalaw --version
```

> **不要**引入 pip 依赖。如必须新增（罕见），请先开 issue + ADR。

## 3. 标准工作流

```text
issue → branch → 改代码 / 数据 / 文档 → 测试 → commit → PR → review → merge
```

### 3.1 起一个 branch

```bash
git checkout -b feat/<short-slug>
```

### 3.2 写改动

参考[第 4 节](#4-改不同东西的具体规则)。

### 3.3 跑测试

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

新增功能必须有 pytest / unittest 用例；bug 修复必须先写复现测试，再修代码。

### 3.4 commit

每个 commit 一个目的，粒度小，前缀清晰：

| 前缀 | 用途 |
|------|------|
| `feat:` | 新功能 / 新数据 |
| `fix:` | bug 修复 |
| `docs:` | 文档（含 ADR） |
| `refactor:` | 不改行为的重构 |
| `test:` | 仅测试 |
| `chore:` | 杂项（CI、依赖版本） |

例：

```text
feat: add company-law-2024 fixture with full text
fix: normalize 第十四条之一 to 14-1 instead of 141
docs: clarify article hit JSON schema in CONTRACT.md
```

### 3.5 提 PR

PR 描述里说明：

- 改了什么 / 为什么改；
- 是否影响协议（影响 → 链接 ADR）；
- 测试情况；
- 数据改动 → 提供来源 URL + checked_at。

## 4. 改不同东西的具体规则

### 4.1 改公开法规 fixture（`data/fixtures/*.json`）

- 必须有 `source_url`、`source_name`、`source_checked_at`、`source_hash`（可省，loader 会派生）。
- 优先来源：`flk.npc.gov.cn`（国家法律法规数据库）。
- 不收录任何商业数据库的"附加性内容"（裁判要旨综述、专家点评等），见 [ADR-0004](./decisions/ADR-0004-license-and-data-rights.md)。
- 条款号必须能被 `normalize_article_number` 处理：阿拉伯（`"71"` / `"14-1"`）或中文（`"第七十一条"`）。
- 加完后跑：

```bash
PYTHONPATH=src python3 -m chinalaw sync --from-dir data/fixtures
PYTHONPATH=src python3 -m chinalaw status
PYTHONPATH=src python3 -m chinalaw article 民法典 第一百四十三条
```

### 4.2 改规范包

- 看 [`docs/CONTRACT.md §5`](./CONTRACT.md) 的 schema。
- `core` / `important` 角色必须填 `reason`。
- 引用的法规 / 私域规范必须能在本地公开规范基线或授权私域文件里解析（导入后跑 `pack validate`）。
- 加完后跑：

```bash
PYTHONPATH=src python3 -m chinalaw pack import ./authorized/<your-pack>.json
PYTHONPATH=src python3 -m chinalaw pack validate <pack-name>
```

`validate` 必须 exit 0、`ok=true`。

### 4.3 改私域规范导入模板

- 私域规范不应提交真实客户内容；测试用材料必须化名、脱敏，并明确标注用途。
- `source_type` 推荐用受控集合：`private_policy` / `lender_requirement` /
  `internal_compliance` / `industry_standard`。

### 4.4 改协议（CONTRACT.md / schema / CLI 契约）

**先开 issue。**

接受协议改动的流程：

1. issue 标 `protocol-change`。
2. 维护者评估是否需要 ADR：
   - 影响 JSON 输出 schema → 必须 ADR；
   - 影响 SQL 表 → 必须 ADR；
   - 仅改实现细节（如 FTS tokenizer）→ 通常不需要 ADR。
3. 起 ADR 草稿（`docs/decisions/ADR-XXXX-<slug>.md`）：固定 Context / Decision /
   Consequences / Alternatives / Follow-ups 五段。
4. ADR 合并即视为决议，再起实现 PR。
5. 实现 PR 同时更新 CONTRACT.md，并加测试。

## 5. Commit / PR 风格细节

- commit 标题 ≤ 72 字符。中文 / 英文均可。
- PR 标题与 commit 标题一致风格。
- PR 不要堆 50 个 commit；请先 rebase / squash 到合理粒度。
- 不要带任何 `--no-verify` / 跳过 hook 的提交。

## 6. 数据贡献的版权与边界

详见 [ADR-0004](./decisions/ADR-0004-license-and-data-rights.md)。要点：

- 法律法规、司法解释本身在公有领域，可自由收录。
- 商业数据库的"汇编 / 综述 / 评注"**不要**贡献，会被拒。
- 私域规范贡献者保留所有权；提交到主仓即视为按 Apache-2.0 释出，
  但**包含真实客户数据的私域规范不应提交到主仓**。
- 任何来源 URL 必须真实可访问；prefer 政府或官方页面。

## 7. 测试覆盖期望

| 类型 | 期望 |
|------|------|
| 新 CLI 命令 | 必须有 CLI 层 + service 层双重测试 |
| 新 JSON 字段 | 至少一个 assertion 命中该字段 |
| 新 fixture | smoke test 至少跑通 `sync` + `get` + `article` |
| 新规范包 | smoke test 至少跑通 `import` + `validate` 通过 |
| bug fix | 必须先写复现测试 |

## 8. issue 模板（暂用文字模板，无须自动化）

### Bug 报告

```text
**重现步骤**：
1. ...
2. ...

**期望行为**：

**实际行为**：

**版本**：chinalaw <VERSION>，Python <X.Y>
```

### Feature 请求

```text
**用例**：我作为 <角色>，需要 <能力>，以便 <目的>。
**当前怎么做**：
**为什么不够**：
**协议影响**：是 / 否（是 → 需要 ADR）
```

### Data correction

```text
**条款 / 字段**：
**当前值**：
**正确值**：
**来源 URL**：
**核查时间**：
```

## 9. 行为准则

- 直接、简洁、对事不对人。
- 用例和数据来源比口号有说服力。
- 不要在 PR 里塞 marketing 语言（"This will revolutionize..."）。
- 拒绝任何形式的人身攻击、歧视或骚扰。
- 见过太多炒作的法律 AI 项目了 —— 我们做长期主义的工具，不做营销。

## 10. 维护者承诺

- 不锁 issue、不删 issue（除 spam）。
- ADR 全部公开。
- 不主动追求 stars；**追求"5 个真实用户每周用"**。
- 不会 24h 内必回 —— 但 1 周内一定会看。

谢谢你的贡献。
