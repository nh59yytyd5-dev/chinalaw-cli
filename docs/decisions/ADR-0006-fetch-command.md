# ADR-0006: chinalaw fetch — 按需爬取与清洗接口

- 状态：Accepted
- 日期：2026-04-27
- 关联：[ADR-0002](./ADR-0002-cli-contract.md)（CLI + JSON 契约）、[ADR-0004](./ADR-0004-license-and-data-rights.md)（数据权利）、[CONTRACT.md §4](../CONTRACT.md)、[DATA_INDEX.md §3](../DATA_INDEX.md)

## Context

本 ADR 起草时，v0.2 fixture 覆盖度有限：民法典只有少量 seed 条文，公司法、民事诉讼法、合同编通则解释还是 metadata stub。实战验证暴露出一个核心问题：agent 想引用高频条款时，`chinalaw article ...` 可能返回 `article: null`，而维护者只能手工补 fixture，失去机读引用 + 内容指纹的核心价值。

截至 2026-04-30，P0/P1 合同审查基础 fixture 已补全为全文：民法典 1260 条、公司法 2024 版 266 条、民事诉讼法 306 条、合同编通则解释 69 条。该事实不削弱本 ADR，反而说明 `fetch` 的定位应从“抢救缺失 fixture”升级为更长期的能力：当真实工作流遇到本地库尚未覆盖的规范时，agent 可以用一条高层命令完成搜索、候选选择、清洗、入库和条文定位。

底层能力其实已经齐备：

| 已有 | 位置 |
|------|------|
| flk.npc.gov.cn 适配器（搜索 / 详情 / 下载 Word） | `src/chinalaw/adapters/flk_npc.py` |
| Word → article 清洗管道 | `parse_articles_from_word_bytes` / `parse_articles_from_docx` |
| 法律 payload 入库 upsert + revision 快照 | `src/chinalaw/loader.py` |
| 同步编排（query / bbbs / batch / incremental） | `src/chinalaw/sync.py` |
| sync CLI 子命令 | `src/chinalaw/cli.py` |

但 sync 命令**不整体进入协议承诺**（参 [`ADR-0002`](./ADR-0002-cli-contract.md)、[`CONTRACT.md §4.5`](../CONTRACT.md)）：真实数据源模式参数面太大（query / bbbs / batch / incremental / page / resume / overlap_days / ……），暴露了过多内部细节，agent 不知道该传什么。这是 sync 不能直接作为 agent 主入口的根因。

## Decision

**新增 `chinalaw fetch <law-name>` 命令**，作为 sync 之上的**高层、薄、协议级**接口。

### 1. 命令形态

```bash
# 默认行为：按法律名一条龙取用并入库
chinalaw fetch 民法典

# 取整部并定位某条返回（自动入库）
chinalaw fetch 民法典 --article 第五百六十八条

# 预览不入库（输出清洗后的 law payload JSON）
chinalaw fetch 民法典 --dry-run

# 落到 fixture 文件供 PR 审查
chinalaw fetch 民法典 --to-fixture data/fixtures/civil_code.json

# 多条命中歧义时列出候选不下载
chinalaw fetch 公司法 --list-matches

# 歧义时手动指定 bbbs
chinalaw fetch 公司法 --prefer-bbbs <bbbs-id>

# 清洗规则升级后，即使 source_hash 相同也重新 upsert
chinalaw fetch 民法典 --force
```

### 2. fetch 与 sync 的边界（关键）

| 维度 | sync（仍内部） | fetch（协议级） |
|------|---------------|----------------|
| 协议状态 | ADR-0002 排除 | 本 ADR 列入 |
| 参数面 | 大（10+ 参数） | 小，且只暴露 agent 需要的高层动作 |
| 接受输入 | bbbs / query / 分页参数 | 仅 law-name + 行为 flag |
| 典型用户 | 维护者批量 / 增量 | agent / 个人按需 |
| 失败行为 | 跳过继续 | 单次明确成功或失败 |

sync 保持现状不动，作为 fetch 的实现底座。fetch 是面向 agent 的"瘦封装"。

### 3. 契约（CLI + JSON）

**输入**：

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `name` (位置参数) | str | 必填 | 法律名（全称 / 简称 / alias） |
| `--article` | str | 无 | 指定条款号；命中后随完整法律一起入库，并在响应中定位返回该条 |
| `--dry-run` | flag | false | 不入库，输出清洗后的 law payload JSON |
| `--to-fixture <path>` | path | 无 | 把 law payload 写入文件（不入库；用于 PR 审查） |
| `--list-matches` | flag | false | 仅列出搜索命中、不下载、不入库 |
| `--prefer-id <id>` | str | 无 | 多条命中时手动指定候选主键；HTML 源可直接按 detail_id fetch |
| `--prefer-bbbs <id>` | str | 无 | `--prefer-id` 的兼容别名；FLK 场景中该 id 即 bbbs |
| `--limit` | int | 5 | 搜索候选数量上限（用于 list-matches / 选最佳） |
| `--force` | flag | false | source_hash 相同也重新清洗并 upsert |
| `--source` | enum | flk_npc | 数据源；支持 `flk_npc` / `gov_xzfgk` / `court_gongbao` / `court_main` / `spp_gov_cn` / `csrc_gov_cn` / 证券交易所和自律规则源 |
| `--format` | enum | json | json / md |
| `--db` | path | 默认 | 数据库路径 |

**输出 JSON schema**（成功）：

```json
{
  "kind": "law_fetch",
  "source": "flk_npc|gov_xzfgk|court_gongbao|court_main|spp_gov_cn|csrc_gov_cn|...",
  "name": "民法典",
  "matched_id": "string",
  "matched_bbbs": "string",
  "matched_detail_id": "string|null",
  "matched_title": "中华人民共和国民法典",
  "candidates": [
    {"id": "...", "bbbs": "...", "detail_id": "...", "title": "...", "released_at": "...", "status": "current"}
  ],
  "law": Law,
  "article": Article | null,
  "loaded": true,
  "skipped": false,
  "force": false,
  "wrote_fixture": null
}
```

**输出 JSON schema**（list-matches 模式）：

```json
{
  "kind": "law_fetch_candidates",
  "source": "flk_npc|gov_xzfgk|court_gongbao|court_main|spp_gov_cn|csrc_gov_cn|...",
  "name": "公司法",
  "candidates": [
    {"id": "...", "bbbs": "...", "detail_id": "...", "title": "...", "released_at": "...", "status": "current"}
  ]
}
```

候选 `status` 取值与法规 `status` 一致：`current` / `amended` / `repealed` /
`pending_effective` / `unknown`。

**退出码**（与 ADR-0002 §6 一致）：

| code | 含义 | 触发场景 |
|------|------|---------|
| 0 | 成功 | 入库 / dry-run / to-fixture / list-matches 任一成功 |
| 1 | 业务级 not found | 搜索零结果；--article 指定的条款在法规中不存在 |
| 2 | 参数 / 前置错误 | 多条命中且未指定 --prefer-id；网络失败；fixture 路径不可写 |

**stdout 严格 JSON，日志走 stderr**（与 ADR-0002 §7 一致）。

### 4. 选最佳匹配规则

按优先级：

1. `--prefer-bbbs` 命中候选
2. 本地 alias 已解析到同源 FLK 记录且可从 `source_url` 取得 bbbs
3. 唯一搜索结果
4. `title == name` 完全匹配
5. 从 `中华人民共和国<name>` 推断出的 `short_title == name` 简称匹配
6. `name in title` 包含匹配
7. 同层多候选时优先 `status=current` 且 `released_at` 最新；否则 exit 2 + `candidates` 列表 + 提示 `--prefer-bbbs`

### 5. 入库幂等性

复用 `loader.load_law_from_dict` 的 upsert 语义：相同 `source_hash` 默认不重复写入；不同 hash 写新 revision（参 `CONTRACT.md §7`）。

例外：`--force` 用于 cleaning / alias 规则升级后的补写。`source_hash` 表示上游内容身份，不表示 cleaning 代码版本，所以同 hash 场景仍可能需要重新 upsert。

### 6. 与本机素材目录的关系

**fetch 不读取维护者或用户的本机素材目录**。理由：

- 来源不可追溯：PDF 没有 `source_url` 锚点，违反 ADR-0003（引用追溯）
- 可能含商业数据库 export 的增值内容（裁判要旨综述等），违反 ADR-0004
- PDF 解析复杂度高于官方 Word 下载（flk 通常给 DOCX，部分旧司法解释给 `.doc`）

本机目录最多只能作为**文件名 → 法律全称 → fetch 关键词**的人工参考，不进入数据流。

### 7. 协议升级路径

- v0.2.x：fetch 作为 **experimental 协议**进入 CONTRACT.md §4，标注"alpha"
- 1 个早期用户跑通 + 接口字段稳定 → v0.3.0 起去 alpha 标记
- 字段删除 / 重命名走主版本 + ADR

## Consequences

**正面**：

- agent 一条命令完成"补条文"工作流；不再需要手抄 fixture
- 引用追溯链完整：fetch 直接派生 `source_url` + `source_hash` + `source_checked_at`
- 维护者也能用同一接口；fixture 文件由 `--to-fixture` 生成后 PR
- sync 保持现状，没有破坏现有维护流程
- 命令面薄：agent 学习成本低，只暴露按需补全所需的高层参数

**负面**：

- 网络强依赖：flk.npc.gov.cn 抖动时整命令失败（mitigation：清晰错误信息 + probe 命令做健康检查）
- 对 flk 站点结构变化敏感：HTML / Word 模板变 → adapter 失效（mitigation：probe 检测 + adapter 单独升级）
- 批量 fetch 可能触发官方源反爬 JavaScript challenge（mitigation：adapter 轻量节流 + 明确错误诊断；不实现绕过反爬逻辑）
- 不支持单条精取（HTTP）：flk 只暴露整部 Word 文档，--article 仍要下载整部后定位（mitigation：整部入库本身有价值，单条仅用于响应字段）
- 多源扩展时需要新 ADR：本 ADR 仅锁定 flk_npc，court.gov.cn / gov.cn 需要单独决策

## Alternatives considered

1. **直接升级 sync 进协议**：被否。sync 参数面太大、是维护者工具；agent 接入会困惑应该传 query 还是 bbbs。
2. **走用户素材目录的 PDF 解析**：被否。违反 ADR-0003 / ADR-0004；PDF 解析门槛高于官方 Word 下载；且把"数据来源不明的内容"引入数据库。
3. **手抄 fixture 永远是主路径**：被否。v0.2 实战已证明覆盖度无法及时跟上需求（出资债权抵销案例）。
4. **MCP server / HTTP API**：被否。与 ADR-0002 一致——协议层先做薄。

## Follow-ups

- 已实现 `src/chinalaw/fetch.py` + CLI 子命令 + mock 单测。
- 已更新 `CONTRACT.md §4`、`EXAMPLES.md`、`DATA_INDEX.md`。
- 已新增 `verify-source flk_npc` / `gov_xzfgk` / `court_gongbao` / `court_main` / `spp_gov_cn` / `csrc_gov_cn` 作为只读真实源 smoke：probe → search → fetch/clean → article locate。
- 已新增 HTML 源 fetch：支持 `detail_id` 作为 source primary id，`--prefer-id <detail_id>` 可直接 fetch。
- 已新增 FLK 旧版 `.doc` 支持：本机存在 `textutil` 或 `antiword` 时，可把官方源旧 Word 文档转文本后进入同一 cleaning pipeline。
- 后续把 `verify-source` 纳入 release 前手工 checklist；默认 CI 不强制联网。
- 当 ≥ 1 个外部用户走通 fetch 后，去 alpha 标记（v0.3.0）
