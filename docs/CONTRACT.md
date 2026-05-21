# chinalaw-cli · Protocol Contract v0.1

> 这份文档钉住 chinalaw-cli 与外部世界（agent / CLI 用户 / 私域规范贡献者 / 重写实现者）之间的契约。
>
> **30 分钟可读完。** 如果读不完，是协议太厚了 —— 提 issue。
>
> 状态：**Draft**（Alpha 阶段可能微调）。一旦发布 v0.1.0，本文档进入 SemVer 兼容承诺。
>
> 编写时的检验问题："如果有人用 Rust / Go 重写 chinalaw-cli，这份文档够不够？" 不够 → 继续补；够 → 停。

---

## 0. 协议层包含 / 不包含

### 协议包含

1. **数据模型**（SQLite DDL + 字段语义）
2. **CLI 命令契约**（输入参数、JSON 输出 schema、退出码）
3. **规范包 JSON schema**（`norm_pack` / `norm_source` 文件格式）
4. **引用元数据**（`source_url` / `source_hash` / `source_checked_at` / `as_of`）
5. **版本承诺**（SemVer）

### 协议不包含

- Python 实现细节、SQLite 内部结构、任何模块名 / 类名 / 函数名
- FTS5 tokenizer 的具体选择（trigram 是当前实现，未来可换 ngram / jieba）
- 同步真实数据源（flk_npc）的内部协议
- 内部 helper 函数与日志格式
- 任何只在仓库内部使用、不被外部消费的字段

> **判断标准**：如果一个字段或行为只是"我们恰好这样做的"，它不属于协议；如果一个字段或行为是 agent / 重写实现者必须依赖的，它属于协议。

---

## 1. 版本承诺（SemVer）

- 本协议从 **v0.1.0** 起进入语义化版本。
- v0.x.y 阶段：可能有 breaking change，但必须先在 [`docs/decisions/`](./decisions/) 写 ADR。
- v1.x.y 起承诺向后兼容，仅在 v2 主版本中破坏 v1 字段。
- 协议版本与 CLI 二进制版本同步：`chinalaw --version`。
- 数据库 schema 单独有版本号（`meta.schema_version`），见 §2.8。schema 升级一律由 `migrate()` 自动处理，不需要手工迁移。

> 当 CLI 输出新增字段时不算 breaking；删除或重命名字段算。

---

## 2. 数据模型（SQLite DDL）

> 所有表 schema 由 `chinalaw.schema` 模块在 `migrate()` 时创建。当前 schema 版本 = **7**。
>
> `law_relations` / `applicability_rules` 已进入 alpha 协议，用于时间效力检索线索。`alias_records` / `call_log` 仍属后续方向。

### 2.1 `laws` — 公开法规

```sql
CREATE TABLE laws (
    id TEXT PRIMARY KEY,                  -- 稳定 ID（如 flk-civil-code-2020）
    title TEXT NOT NULL,                  -- 全称
    short_title TEXT,                     -- 简称（如"民法典"）
    aliases TEXT NOT NULL DEFAULT '[]',   -- JSON array of strings
    level TEXT NOT NULL,                  -- 见 §2.9 LawLevel
    issuing_body TEXT,                    -- 制定机关
    document_number TEXT,                 -- 发文号
    released_at TEXT,                     -- ISO date YYYY-MM-DD
    effective_at TEXT,                    -- ISO date YYYY-MM-DD
    repealed_at TEXT,                     -- ISO date YYYY-MM-DD（如已废止）
    status TEXT NOT NULL,                 -- 见 §2.9 LawStatus
    source_url TEXT NOT NULL,             -- 引用元数据，见 §3
    source_name TEXT NOT NULL,
    source_checked_at TEXT NOT NULL,      -- ISO 8601 datetime
    source_hash TEXT NOT NULL,            -- 内容指纹（SHA-256 hex）
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

**字段语义**：

- `id`：稳定 ID，跨数据源应保持一致。推荐 `<source-prefix>-<slug>-<year>`（如 `flk-company-law-2024`）。
- `aliases`：JSON 数组（不是逗号分隔串）。清洗阶段会为常用法律简称 / 司法解释简称派生 alias；读取阶段也会用同一规则兼容旧数据。检索时 `LIKE` 模糊匹配。
- `level` / `status`：见 §2.9。
- `source_*`：见 §3。

### 2.2 `articles` — 法规条文

```sql
CREATE TABLE articles (
    id TEXT PRIMARY KEY,                  -- 推荐 <law_id>#<number>
    law_id TEXT NOT NULL REFERENCES laws(id) ON DELETE CASCADE,
    number TEXT NOT NULL,                 -- 标准化阿拉伯数字，例 "71" / "14-1"
    number_display TEXT NOT NULL,         -- 显示用，例 "第七十一条" / "第十四条之一"
    part TEXT,                            -- 编/章/节（如"第三编 合同/第四章 合同的履行"）
    title TEXT,                           -- 条文标题（少数法规有）
    text TEXT NOT NULL,                   -- 条文正文
    position INTEGER NOT NULL,            -- 同一法规内的顺序（1 起）
    UNIQUE (law_id, number)
);
```

**字段语义**：

- `number`：必须是经 `normalize_article_number` 标准化的形式（阿拉伯数字 / 插入条款 `<base>-<inserted>`）。
- `position`：同一法规内不可重复，用于稳定排序；`UNIQUE (law_id, number)` 同时保证条款号唯一。

### 2.3 `revisions` — 法规版本快照

```sql
CREATE TABLE revisions (
    id TEXT PRIMARY KEY,                  -- 推荐 <law_id>@<source_hash[:16]>
    law_id TEXT NOT NULL REFERENCES laws(id) ON DELETE CASCADE,
    version_label TEXT NOT NULL,          -- 人读标签，例 "2020-05-28 发布版"
    released_at TEXT NOT NULL,            -- 该版本发布日期
    effective_at TEXT,                    -- 该版本施行日期
    notes TEXT,                           -- 修订说明
    content_hash TEXT NOT NULL,           -- 该版本条文内容指纹
    snapshot_json TEXT                    -- 该版本完整快照（用于 as-of 查询）
);
```

**用途**：支持 `--as-of YYYY-MM-DD` 时点查询；同一 `law_id` 可有多个 revision。

`snapshot_json` 是内部存储字段，用于重建历史版本；公开 JSON 输出中的 `revisions` / `current_revision` / `selected_revision` 不暴露该字段，避免 agent 读取接口返回整部法规快照。

### 2.4 `norm_sources` — 私域规范来源

```sql
CREATE TABLE norm_sources (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    short_name TEXT,
    aliases TEXT NOT NULL DEFAULT '[]',
    source_type TEXT NOT NULL,            -- 见 §2.9 NormSourceType
    authority TEXT,                       -- 制定主体
    binding_scope TEXT,                   -- 约束范围
    jurisdiction TEXT,                    -- 适用区域
    effective_at TEXT,
    repealed_at TEXT,
    source_url TEXT,                      -- 可空（私域文件无 URL）
    source_name TEXT NOT NULL,            -- 文件来源描述
    source_checked_at TEXT NOT NULL,
    source_hash TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',  -- 自由 JSON 元数据
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

### 2.5 `norm_clauses` — 私域规范条款

```sql
CREATE TABLE norm_clauses (
    id TEXT PRIMARY KEY,
    norm_source_id TEXT NOT NULL REFERENCES norm_sources(id) ON DELETE CASCADE,
    number TEXT,                          -- 标准化条款号（可空）
    number_display TEXT,                  -- 显示用条款号
    title TEXT,
    text TEXT NOT NULL,
    position INTEGER NOT NULL,
    UNIQUE(norm_source_id, position)
);
```

**条款号规则**：

- 中式编号（"第一条" / "第十四条之一"）→ 复用 `normalize_article_number`，归一为阿拉伯数字 / `<base>-<inserted>`。
- 数字编号（"2.1" / "3.2.1"）→ 保持原样。
- `clause` 命令支持以上两种输入。

### 2.6 `norm_packs` / `norm_pack_items` — 规范包

```sql
CREATE TABLE norm_packs (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    summary TEXT,
    scope TEXT,
    maintainer TEXT,
    version_policy TEXT NOT NULL DEFAULT 'current',  -- current / as-of:YYYY-MM-DD / pinned
    source_kind TEXT NOT NULL DEFAULT 'manual',       -- manual / generated / imported
    metadata_json TEXT NOT NULL DEFAULT '{}',
    dependencies_json TEXT NOT NULL DEFAULT '{}',     -- {laws, norm_sources, packs}
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE norm_pack_items (
    id TEXT PRIMARY KEY,
    pack_id TEXT NOT NULL REFERENCES norm_packs(id) ON DELETE CASCADE,
    item_type TEXT NOT NULL,              -- law / article / norm_source / norm_clause / reference
    law_id TEXT,
    law_title TEXT,
    article_number TEXT,
    article_number_display TEXT,
    norm_source_id TEXT,
    norm_source_name TEXT,
    clause_number TEXT,
    clause_number_display TEXT,
    role TEXT NOT NULL,                   -- core / important / supporting / background
    reason TEXT,
    note TEXT,
    reference_text TEXT,
    position INTEGER NOT NULL
);
```

**约束**：

| `item_type` | 必填字段 |
|------------|---------|
| `law` | `law_id` 或 `law_title` |
| `article` | `law_id` 或 `law_title`，并 `article_number` |
| `norm_source` | `norm_source_id` 或 `norm_source_name` |
| `norm_clause` | `norm_source_id` 或 `norm_source_name`，并 `clause_number` |
| `reference` | 至少 `reference_text`（可由 law_title / norm_source_name 自动填充） |

`role ∈ {core, important, supporting, background}`：`core` / `important` 应填 `reason`，否则 `validate` 出 warning。

### 2.7 `law_relations` / `applicability_rules` — 时间效力线索（alpha）

```sql
CREATE TABLE law_relations (
    id TEXT PRIMARY KEY,
    relation_type TEXT NOT NULL,          -- replaces / amended_by / related 等开放枚举
    from_law_id TEXT NOT NULL,
    from_law_title TEXT,
    to_law_id TEXT NOT NULL,
    to_law_title TEXT,
    effective_at TEXT,
    source_name TEXT NOT NULL,
    source_url TEXT,
    source_checked_at TEXT NOT NULL,
    notes TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE applicability_rules (
    id TEXT PRIMARY KEY,
    topic TEXT NOT NULL,                  -- 如 合同效力
    domain TEXT NOT NULL DEFAULT 'all',   -- all / litigation / contract_review 等开放枚举
    primary_law_id TEXT NOT NULL,
    primary_law_title TEXT,
    fallback_law_id TEXT,
    fallback_law_title TEXT,
    effective_from TEXT,
    effective_to TEXT,
    rule_text TEXT NOT NULL,
    transition_text TEXT,
    source_name TEXT NOT NULL,
    source_url TEXT,
    source_checked_at TEXT NOT NULL,
    confidence TEXT NOT NULL DEFAULT 'seed',
    metadata_json TEXT NOT NULL DEFAULT '{}'
);
```

**语义边界**：

- 这两张表只提供 grounding 线索，不输出最终法律适用结论。
- `applicability_rules` 的日期窗口是 `effective_from <= --date <= effective_to`，边界可空。
- 命中规则中的法规若未入库或仅为 stub，输出必须暴露 `needs_fetch` / warning。

### 2.8 `meta`

```sql
CREATE TABLE meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

**已知 key**：

- `schema_version`：当前 SQLite schema 版本（整数串）。
- `last_sync_at`：最近一次 `sync` 命令完成时间。
- `last_applicability_sync_at`：最近一次 `sync --applicability` 完成时间。
- `source:<source>:last_page` / `next_page` / `last_mode` / `last_incremental_to`：sync 进度元数据。

### 2.9 受控枚举

**`level`（LawLevel）**：

| 值 | 含义 |
|----|------|
| `law` | 法律（全国人大及其常委会，含修正案 / 修订决定） |
| `admin_regulation` | 行政法规（国务院） |
| `judicial_interpretation` | 司法解释（最高法 / 最高检发布） |
| `judicial_meeting_minutes` | 司法会议纪要（如九民纪要、八民纪要） |
| `judicial_policy` | 司法政策性文件（最高法批复 / 通知 / 复函） |
| `guiding_case` | 指导性案例（最高法 / 最高检发布） |
| `department_rule` | 部门规章 |
| `local_regulation` | 地方性法规（含自治条例 / 经济特区法规 / 浦东新区法规 / 海南自贸港法规） |
| `local_government_rule` | 地方政府规章 |
| `supervisory_regulation` | 监察法规（国家监委） |
| `self_regulatory_rule` | 交易所 / 行业协会 / 登记结算机构自律业务规则 |
| `other` | 其他规范性文件 |

`judicial_meeting_minutes` / `judicial_policy` / `guiding_case` 不在 flk.npc 数据
范围内，由 `court_gongbao` / `court_main` / `spp_gov_cn` 等多源 adapter 直接写入。

**`status`（LawStatus）**：

| 值 | 含义 |
|----|------|
| `current` | 现行有效 |
| `amended` | 已被修改 |
| `pending_effective` | 尚未生效 |
| `repealed` | 已废止 |
| `unknown` | 未知 |
| `seed` | 本仓库样例 / 核心条款 seed；不是官方效力状态，不保证全文完整 |

**`source_type`（NormSourceType，开放枚举）**：示例值 `private_policy` / `lender_requirement` / `internal_compliance` / `industry_standard`。本期不强制约束，由贡献者自行约定。

---

## 3. 引用元数据

每条公开法规、私域规范、私域规范条款都必须可追溯到来源。这是协议的核心承诺之一。

| 字段 | 必填 | 语义 |
|------|------|------|
| `source_url` | 公开法规必填、私域可空 | 来源页面或文件路径 |
| `source_name` | 必填 | 来源标识（如 `flk.npc.gov.cn` / `local-file`） |
| `source_checked_at` | 必填 | ISO 8601 datetime（带时区），最后核查时间 |
| `source_hash` | 必填 | SHA-256 hex；公开法规推荐对源响应体计算，私域对条款 JSON 标准化序列化后计算 |
| `freshness_days` | 由系统派生 | `source_checked_at` 距今天的天数（仅在 JSON 输出中存在） |

**`as_of` 语义**（`get` / `article` 命令）：

- 输入：`YYYY-MM-DD`（公历日期，无时区，按当地解读为该日 00:00 之前的最新版本）。
- 选择规则：`revisions` 中 `effective_at ≤ as_of`（无 `effective_at` 退化为 `released_at ≤ as_of`）的最近一个。
- 命中失败：返回 404（exit code 1，JSON `{"found": false, ...}`）。

---

## 4. CLI 命令契约

> 通用约定：
>
> - 除命令级另有说明外，命令支持 `--format json|md`（默认 `json`）。
> - 所有命令支持 `--db <path>`（默认 `~/.chinalaw/chinalaw.db`）。
> - JSON 输出为 UTF-8、`indent=2`、`ensure_ascii=False`。
> - **退出码**：`0` 成功；`1` 业务级 not found；`2` 参数错误 / 调用前置不满足。

### 4.0 Agent Platform Commands

#### 4.0.1 `schema [command|mcp]`

`schema` 是 agent 自省入口，返回命令参数、输出摘要、退出码、风险等级和常见误用。
命令元数据的代码单一来源为 `src/chinalaw/metadata.py`。

示例：

```bash
chinalaw schema --format json
chinalaw schema article --format json
chinalaw schema applicable --format md
chinalaw schema mcp --format json
```

JSON 输出：

```jsonc
{
  "kind": "cli_schema_index|cli_command_schema|mcp_schema",
  "schema_version": "integer",
  "risk_levels": {
    "read": "string",
    "local-write": "string",
    "network-read": "string",
    "network-write-local": "string",
    "maintenance": "string"
  },
  "global_flags": [{"name": "string", "required": "boolean", "description": "string"}],
  "command": {
    "path": "string",
    "summary": "string",
    "risk": "read|local-write|network-read|network-write-local|maintenance",
    "side_effect": "string",
    "network": "string",
    "authority_boundary": "string",
    "positional": [{"name": "string", "required": "boolean", "description": "string"}],
    "flags": [{"name": "string", "required": "boolean", "description": "string"}],
    "json_output": {"kind": "string"},
    "exit_codes": {"0": "string", "1": "string", "2": "string"},
    "common_misuse": ["string"],
    "suggested_follow_ups": ["string"]
  }
}
```

`schema mcp` 返回 `tools[]`，其中每个 tool 必须包含 `name`、`inputSchema`、
`risk` 和 `cli_equivalent`。退出码：命中返回 `0`；未知 target 返回 `1`。

#### 4.0.2 JSON `_notice`

所有 JSON 命令输出可以附加非阻塞 `_notice`。它只提示本机 agent 使用状态，
**不得**改变主结果字段、`ok` / `found` / `article` 语义或退出码。agent 应先完成
当前命令的业务判断，再把 `_notice` 作为环境维护提示报告给用户。

禁用方式：

```bash
CHINALAW_NO_NOTICE=1 chinalaw article 民法典 524 --format json
chinalaw --no-notice article 民法典 524 --format json
```

JSON shape：

```jsonc
{
  "kind": "article_result",
  "law": {},
  "article": {},
  "_notice": {
    "source_stale|seed_laws_present|skills_stale|mcp_not_installed|global_wrapper_mismatch|db_missing|db_schema_stale": {
      "severity": "info|warning",
      "message": "string",
      "command": "string"
    }
  }
}
```

当前 `_notice` 只做本地轻量检查：PATH / wrapper、用户级 skills、MCP wrapper、
DB 是否存在、schema version、source freshness、seed/stub 法规。它不联网，不自动修复，
也不写入缺失数据库。错误 envelope 默认不附加 `_notice`，避免污染严格错误解析。

#### 4.0.3 `doctor`

`doctor` 是本机 agent 使用前的健康检查入口。默认只跑本地轻量检查，不联网。

参数：

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `--strict` | bool | false | warning 也视为失败 |
| `--source-smoke <source>` | enum | 无 | 可选联网 probe，默认跳过 |

JSON 输出：

```jsonc
{
  "kind": "doctor_report",
  "ok": "boolean",
  "strict": "boolean",
  "db_path": "string",
  "checks": [
    {
      "name": "cli_version|path|db_exists|schema_version|fixtures_loaded|freshness|seed_or_stub|skills_installed|mcp_available|source_smoke",
      "status": "pass|warn|fail|skip",
      "message": "string",
      "hint": "string|null",
      "data": "object|null"
    }
  ],
  "error_count": "integer",
  "warning_count": "integer"
}
```

退出码：`ok=true` 返回 `0`；存在 fail 返回 `1`；`--strict` 下 warning 也会让
`ok=false` 并返回 `1`。`doctor` 不自动修复、不自动联网、不在数据库不存在时创建数据库。

#### 4.0.4 `init`

`init` 是首次安装后的本地初始化入口：加载随包完整 fixture，然后运行 `doctor`。
默认不联网，不批量 `fetch`，不读取用户素材目录。

参数：

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `--strict` | bool | false | 传给 `doctor`，warning 也视为失败 |
| `--source-smoke <source>` | enum | 无 | 初始化后额外跑一次联网 source smoke；默认跳过 |

JSON 输出：

```jsonc
{
  "kind": "init_result",
  "ok": "boolean",
  "db_path": "string",
  "fixture_sync": {
    "laws_loaded": "integer",
    "articles_loaded": "integer",
    "titles": ["string"],
    "fixtures_dir": "string"
  },
  "doctor": "doctor_report",
  "next_commands": ["string"]
}
```

退出码：`ok=true` 返回 `0`；`doctor` 失败返回 `1`；参数错误返回 `2`。
缺少某部法规或条文时，后续由 `ensure <law>` / `fetch <law>` 按任务补全。

### 4.1 `search <query>`

输入：

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `query` | str | required | 关键词，按空白拆分多 term |
| `--limit` | int | 20 | 各类命中各自的上限 |
| `--kind` | enum | `all` | `article` / `law` / `norm` / `all` |
| `--in` | str | 无 | 限定公开法规范围；多个法规名 / id / alias 用逗号分隔 |

JSON 输出 schema：

```jsonc
{
  "query": "string",
  "kind": "all|article|law|norm",
  "strategy": "fts5|like|empty",
  "law_filter": {
    "requested": ["string"],
    "resolved": [
      {
        "requested": "string",
        "id": "string",
        "title": "string",
        "short_title": "string|null"
      }
    ],
    "unresolved": ["string"]
  } | null,
  "article_hits": [
    {
      "law_id": "string",
      "law_title": "string",
      "law_short_title": "string|null",
      "law_status": "current|amended|repealed|pending_effective|seed|unknown",
      "number": "string",
      "number_display": "string",
      "part": "string|null",
      "text": "string",
      "source_url": "string",
      "freshness_days": "integer|null",
      "score": "number",
      "match_kind": "primary|relevant"
    }
  ],
  "law_hits": [Law],
  "norm_clause_hits": [{...}],
  "norm_source_hits": [NormSource]
}
```

**FTS5 vs LIKE**：所有 term ≥ 3 字 → FTS5（trigram，`AND` 连接）；任一 term < 3 字 → LIKE 子串匹配。`strategy` 字段告知调用方使用了哪种。

**`--in` 语义**：只限制公开法规 / 公开条文命中范围，不读取私域规范。未解析的过滤项进入 `law_filter.unresolved`；如果全部过滤项均未解析，公开法规 / 条文命中为空。

### 4.1.1 `resolve <name>`

轻量解析用户给出的法规全名 / 短称 / alias / 模糊名，返回官方记录元数据和命中路径；不返回条文、修订快照或全文。

输入：

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `name` | str | required | 法规 id / 全称 / 短称 / alias / 俗称 |

JSON 输出 schema：

```jsonc
{
  "input": "string",
  "matched": "boolean",
  "via": "id_match|title_match|short_title_match|alias_exact|alias_derived|like_fallback|null",
  "id": "string | omitted when matched=false",
  "official_title": "string | omitted when matched=false",
  "short_title": "string|null | omitted when matched=false",
  "aliases": ["string"] | "omitted when matched=false",
  "level": "LawLevel | omitted when matched=false",
  "status": "LawStatus | omitted when matched=false",
  "issuing_body": "string|null | omitted when matched=false",
  "released_at": "YYYY-MM-DD|null | omitted when matched=false",
  "effective_at": "YYYY-MM-DD|null | omitted when matched=false"
}
```

`via` 语义：

| 值 | 含义 |
|----|------|
| `id_match` | `name` 精确命中 `laws.id` |
| `title_match` | 精确命中官方全名 |
| `short_title_match` | 精确命中 `laws.short_title` |
| `alias_exact` | 精确命中 fixture / DB 的 `laws.aliases` |
| `alias_derived` | 命中规则层派生 alias（如 issuer + 宿主法 + 后缀） |
| `like_fallback` | 最后兜底的 `LIKE` 模糊匹配，调用方必须谨慎使用 |
| `null` | 未命中 |

退出码：命中返回 `0`；未命中返回 `1`，JSON 保持 `{"input": "...", "matched": false, "via": null}`。Markdown 未命中输出必须提示 `chinalaw fetch <input> --list-matches`。

### 4.2 `get <name> [--as-of YYYY-MM-DD]`

输入：法规 id / 全称 / 简称 / alias。

输出：完整 `Law` 对象（含 `articles`、`revisions`、`current_revision`、`selected_revision`、`categories`、`freshness_days`）。

未命中：exit 1，输出 `{"found": false, "name": "..."}`。

### 4.2.1 `search <query...>`

`query` 是一个或多个位置参数。推荐调用方把多关键词作为一个 shell 参数传入
（如 `chinalaw search "保证期间届满 签字" --kind article`）；为降低 agent
命令失误成本，CLI 也接受未加引号的多个 query token，并按单个空格合并后检索。

### 4.3 `article <name> <number> [--as-of YYYY-MM-DD]`

输入：法规标识 + 条款号（接受中式 / 阿拉伯 / 插入条款）。

Markdown / card 输出选项：

| 参数 | 说明 |
|------|------|
| `--format card` | 单条 agent 卡片格式：`《法规》§条号: 正文` + `source: ...`；用于避免 agent 自写 JSON 管道 |
| `--no-footer` | 仅 `--format md` 生效：只输出标题 + 条文正文，省略状态 / 来源 / 核查信息 |
| `--compact` | 仅 `--format md` 生效：footer 压缩为单行，如 `[current｜2021-01-01 施行｜核查 1 天前]` |
| `--bare` | 仅 `--format md` 生效：只输出条文正文 |
| `--inline` | 仅 `--format md` 生效：单行 `<short_title>§<number> <text>` |
| `--arabic` | Markdown 标题使用阿拉伯数字条号，如 `第524条` |

输出：

```json
{
  "law": Law,
  "article": Article | null,
  "item": Article | null,
  "requested_number": "string"
}
```

`item` 是 `article` 的兼容别名，用于和批量读取命令的 `items[]` 命名对齐；
既有 agent 仍可继续读取 `article`。

未命中：exit 1，输出必须带 `found=false` 与 `reason`，帮助 agent 在一次
调用内决定下一步，不再额外猜测。

```json
{
  "found": false,
  "name": "民法典",
  "number": "9999",
  "law": Law | null,
  "article": null,
  "requested_number": "9999",
  "reason": "law_missing | law_stub | law_seed | article_null | invalid_as_of | version_not_found_as_of | article_null_as_of",
  "law_id": "string | null",
  "as_of": "YYYY-MM-DD | null",
  "hint": "human-readable next step",
  "suggested_fetch": "chinalaw fetch ... | omitted",
  "suggested_outline": "chinalaw outline ... | omitted",
  "suggested_history": "chinalaw history ... | omitted",
  "fallback_sources": ["court_gongbao", "court_main", "spp_gov_cn"],
  "sibling_laws": ["LawSummary"],
  "suggested_sibling_articles": ["chinalaw article <law_id> <number> --format json"]
}
```

`--as-of` 相关未命中必须优先说明时间点 / 版本问题，不得提示用当前版本
`fetch --force` 伪修复历史时点判断。

### 4.3.1 `articles <name> [<spec>] [--numbers <spec>] [--batch <batch-spec>] [--as-of YYYY-MM-DD]`

批量定位同一部法规下的多个条文。`spec` 可作为位置参数传入，也可通过 `--numbers` 传入。它接受逗号 / 中文逗号 / 顿号 / 空白分隔，并支持纯数字范围：

```bash
chinalaw articles 民法典 "5,12,13,19,23-25"
chinalaw articles 民法典 --numbers "5,12,13,19,23-25"
```

`--batch` 用于一次定位多部法规下的多个条文。分组之间用 `;` / `；`，法规名与条号 spec 之间用 `:` / `：`：

```bash
chinalaw articles --batch "民法典:557-561,568;合同编通则解释:27,55-58"
```

Markdown 输出选项：

| 参数 | 说明 |
|------|------|
| `--arabic` | Markdown 标题使用阿拉伯数字条号，如 `第524条` |
| `--section` | Markdown 标题使用 `§524` 形式；与 `--arabic` 互斥 |
| `--with-title` | 有 `Article.title` 时追加 `【条名】` |
| `--no-footer` | Markdown 省略汇总头 / footer |
| `--compact` | Markdown 使用单行 compact footer |
| `--bare` | Markdown 仅输出正文；与 footer 类选项互斥 |
| `--inline` | Markdown 每条输出一行 `<short_title>§<number> <text>`；与 footer 类选项互斥 |

单法规输出：

```jsonc
{
  "kind": "law_articles",
  "law": Law,
  "as_of": "YYYY-MM-DD|null",
  "requested_numbers": ["string"],
  "normalized_numbers": ["string"],
  "item_count": "integer",
  "found_count": "integer",
  "missing_count": "integer",
  "articles": "same schema as items; compatibility alias for public-law article lists",
  "items": [
    {
      "requested_number": "string",
      "number": "string",
      "found": "boolean",
      "article": Article | null
    }
  ]
}
```

多法规 `--batch` 输出：

```jsonc
{
  "kind": "law_articles_batch",
  "ok": "boolean",
  "as_of": "YYYY-MM-DD|null",
  "law_count": "integer",
  "item_count": "integer",
  "found_count": "integer",
  "missing_count": "integer",
  "failed_section_count": "integer",
  "error_count": "integer",
  "sections": [
    {
      "name": "string",
      "numbers_spec": "string",
      "result": "law_articles|null",
      "error": "missing_numbers|law_not_found|null",
      "ok": "boolean"
    }
  ]
}
```

退出码：法规不存在、编号 spec 无效、时点无效、存在缺失条文，或 `--batch` 任一分组失败 → `1`；全部命中 → `0`。agent 引用前必须检查单法规 `items[*].found`，多法规还必须检查顶层 `ok` 与每个 `sections[*].ok`。

### 4.3.2 `outline <name> [--part <text>] [--preview-chars N] [--with-text|--full-text]`

列出一部法规的条文目录与正文预览，用于 agent 先观察结构，再批量取条。默认
是 preview，不是 verbatim；需要引用原文时必须传 `--with-text`（等价别名
`--full-text`），或改用 `article` / `articles`。

```jsonc
{
  "kind": "law_outline",
  "law": Law,
  "part_filter": "string|null",
  "preview_chars": "integer",
  "text_mode": "preview|full",
  "full_text": "boolean",
  "article_count": "integer",
  "item_count": "integer",
  "articles": "same schema as items; compatibility alias for public-law article lists",
  "items": [
    {
      "number": "string",
      "number_display": "string",
      "part": "string|null",
      "title": "string|null",
      "position": "integer",
      "text_preview": "string",
      "text_truncated": "boolean"
    }
  ]
}
```

`--with-text` / `--full-text` 时，每个 item 额外包含顶层 `text`（完整条文）、
`text_length`、`found`、`requested_number`，并保留旧字段 `article` 作为兼容：

```jsonc
{
  "number": "585",
  "number_display": "第五百八十五条",
  "text_preview": "当事人可以约定一方违约时应当根据违约情况向对方支付一定数额的违约金...",
  "text": "当事人可以约定一方违约时应当根据违约情况向对方支付一定数额的违约金...",
  "text_length": 184,
  "text_truncated": false,
  "article": Article,
  "found": true,
  "requested_number": "第五百八十五条"
}
```

`--part` 是章节文本过滤，例如 `自然人`、`合同编`、`物权编`。未找到法规时 exit
`1`。JSON 适合 agent 程序处理；`--format md` 适合人读，如需纯正文可用
`--with-text --format md --bare`。

### 4.4 `list` / `laws [--level <level>] [--status <status>] [--limit N]`

输出：`[Law, ...]`，按 `released_at DESC, title ASC`。

`laws` 是 `list` 的 agent-first 显式别名，用于列出本地法规 `id/title/short_title/status` 等字段，避免 agent 为了发现 law_id 直接连接 SQLite 或猜 schema 字段。

### 4.5 `sync`

`--fixtures` / `--from-dir <dir>` 模式属于协议（手工准备 + 一次性导入）。
`--applicability [--applicability-dir <dir>]` 加载时间效力 / 规范关系 JSON，输出：

```json
{
  "kind": "applicability_import",
  "files_loaded": "integer",
  "relations_loaded": "integer",
  "rules_loaded": "integer",
  "topics": ["string"],
  "files": ["string"]
}
```

`--source flk_npc` / `--query` / `--bbbs` / `--batch` / `--incremental` 等真实数据源模式**不属于本期协议承诺**，可能在 v0.x 内部调整。

### 4.6 `status`

输出：

```json
{
  "db_path": "string",
  "schema_version": "integer",
  "laws": "integer",
  "articles": "integer",
  "revisions": "integer",
  "categories": "integer",
  "norm_packs": "integer",
  "norm_sources": "integer",
  "norm_clauses": "integer",
  "law_relations": "integer",
  "applicability_rules": "integer",
  "last_sync_at": "string|null",
  "last_applicability_sync_at": "string|null",
  "oldest_source_checked_at": "string|null",
  "oldest_freshness_days": "integer|null",
  "by_level": [{"level": "string", "count": "integer"}],
  "by_status": [{"status": "string", "count": "integer"}]
}
```

### 4.7 `norm <subcommand>`

| 子命令 | 输入 | 输出 |
|--------|------|------|
| `list` | — | `[NormSource, ...]` |
| `show <name>` | 私域规范标识 | `NormSource`（含 `clauses`） |
| `clause <name> <number>` | 标识 + 条款号 | `{source, clause, requested_number}` |
| `import <file>` | JSON 文件 | `{kind: "norm_source_import", source_id, name, clauses_loaded, source_type}` |
| `ingest <file> --name ...` | txt/md/docx/pdf | 同上，附 `ingest_format`；PDF 依赖本机 `pdftotext` |
| `export <name>` | 标识 | 完整 `NormSource` JSON |

`norm ingest` 的来源元数据入口：

| 参数 | 说明 |
|------|------|
| `--alias <name>` | 私域规范别名，可重复；用于后续 `norm clause` / `article` fallback 解析 |
| `--metadata-json <json>` | 额外 metadata JSON object，与自动生成的 `metadata.ingest` 合并 |
| `--metadata-file <file>` | 从 JSON object 文件读取额外 metadata，与 `--metadata-json` 可叠加 |
| `--source-name <name>` | 来源名称；默认使用导入文件路径 |
| `--source-checked-at <iso>` | 固定来源核查时间 |
| `--source-hash <hash>` | 调用方已计算的来源内容哈希；不传则按 payload 自动计算 |

`norm ingest` 切条器会识别 `第N条【标题】正文`、`N. 【标题】正文` 等标题结构；
标题进入 `clauses[].title`，原始括号标题仍保留在 `clauses[].text` 里，便于人工复核。

### 4.8 `pack <subcommand>`

| 子命令 | 输入 | 输出 |
|--------|------|------|
| `list` | — | `[NormPack 摘要, ...]` |
| `show <name>` | 包标识 | `NormPack`（每个 item 带 `resolved` 字段） |
| `add <name> --type ...` | 包标识 + 成员参数 | `{kind: "norm_pack_item_add", added, duplicate, item, resolved}` |
| `import <file>` | JSON 文件 | `{kind, pack_id, name, items_loaded, dependencies, source_kind}` |
| `export <name>` | 包标识 | 完整 `NormPack` JSON |
| `validate <target> [--file]` | 包标识或文件 | 校验报告，`ok=false` 时 exit 1 |

`pack add` 是 agent 工作流沉淀入口。默认要求 `law` / `article` / `norm_source` / `norm_clause` 能在本地解析；如需占位，必须显式传 `--allow-unresolved`。

常用输入：

| 参数 | 说明 |
|------|------|
| `--create` | 规范包不存在时创建 |
| `--type law|article|norm_source|norm_clause|reference` | 成员类型；CLI 也接受 `norm-source` / `norm-clause` |
| `--law <name>` + `--article <number>` | 添加公开法规或公开法条 |
| `--norm <name>` + `--clause <number>` | 添加私域规范或私域条款 |
| `--text <text>` | 添加 reference |
| `--role core|important|supporting|background` | 成员角色 |
| `--reason <text>` / `--note <text>` | 纳入理由和备注 |
| `--allow-unresolved` | 允许加入当前本地库尚不能解析的成员；后续 `validate` 仍会报错或 warning |

校验报告 schema：

```json
{
  "kind": "norm_pack_validation",
  "ok": "boolean",
  "pack_id": "string",
  "name": "string",
  "item_count": "integer",
  "required_item_count": "integer",
  "resolved_item_count": "integer",
  "error_count": "integer",
  "warning_count": "integer",
  "dependencies": Dependencies,
  "issues": [
    {
      "severity": "error|warning",
      "code": "string",
      "message": "string",
      "item_id": "string?",
      "item_type": "string?",
      "position": "integer?",
      "role": "string?",
      "dependency": "object?"
    }
  ]
}
```

### 4.8.1 `cite-check <file>`

`cite-check` 是 agent/human 友好的 shortcut。它不新增法律判断，只展开到底层
`audit file`；传 `--grounding` 时展开到 `audit grounding`。JSON 输出必须保留
`shortcut.expanded_command`，让 agent 知道实际执行的原子命令。

参数：

| 参数 | 说明 |
|------|------|
| `<file>` | 待审查 txt/md/docx/pdf |
| `--as-of YYYY-MM-DD` | 按事实时点版本审查条文 |
| `--strict` | 将 warning 提升为 error |
| `--grounding` | 使用项目检索快照审查证据链 |
| `--snapshot <path>` | 只能配合 `--grounding` 指定 JSONL 快照；单独传入返回 `2` |
| `--format json|md` | 默认 JSON；Markdown 供人工快速复核 |

附加字段：

```jsonc
{
  "shortcut": {
    "command": "cite-check",
    "expanded_command": "audit file|audit grounding",
    "path": "string",
    "evidence_chain_visible": true
  }
}
```

退出码与展开后的 `audit` 命令一致：`0` 通过；`1` 审查发现 error；
`2` 参数错误 / 前置条件不满足。

### 4.8.2 `audit <file|pack|norm|grounding>`

引用审查门禁，用于核验文本、规范包和私域规范中的 `《法规》第N条` 引用。
注意：**引用某条作为依据** 不等于 **逐字引用条文原文**。默认审查只把
`article` 精确命中当作引用存在性核验；只有文本明确出现引号、`原文/条文/摘录`
或 `规定如下` 等原文提示时，才做 `quoted_text` 与条文正文的一致性检查。

| 子命令 | 输入 | 输出 |
|--------|------|------|
| `audit file <path>` | txt/md/docx/pdf 文件 | `AuditReport` |
| `audit pack <name>` | 规范包 id / 名称 | `AuditReport`，同时包含 `pack_validation` |
| `audit norm <name>` | 私域规范 id / 名称 | `AuditReport` |
| `audit grounding <path>` | 最终法律意见 / 合同审查报告 | `GroundingAuditReport`，按项目检索快照核对依据链 |

通用参数：

| 参数 | 说明 |
|------|------|
| `--as-of YYYY-MM-DD` | 按事实时点版本审查条文 |
| `--strict` | 将 warning 提升为 error，适合作为 agent 工作流门禁 |
| `--format json|md` | 默认 JSON；Markdown 供人工快速复核 |
| `audit grounding --snapshot <path>` | 指定项目检索快照；默认读 `CHINALAW_SNAPSHOT_OUT`、`CHINALAW_PROJECT/.chinalaw/snapshots/latest.jsonl`，或从被审文件所在目录向上查找已初始化快照 |

`AuditReport` 顶层必须给出可机器判断的门禁信号：

```jsonc
{
  "kind": "text_audit|pack_audit|norm_audit",
  "ok": false,
  "target": "string",
  "as_of": "YYYY-MM-DD|null",
  "strict": false,
  "citation_count": 1,
  "resolved_count": 1,
  "checked_text_count": 1,
  "error_count": 1,
  "warning_count": 0,
  "citations": [
    {
      "raw": "《民法典》第五百八十五条",
      "law_input": "民法典",
      "number_input": "第五百八十五条",
      "number": "585",
      "resolved": true,
      "law": {"id": "flk-civil-code-2020", "status": "current"},
      "article": {"number": "585", "text": "..."},
      "text_match": {"checked": true, "kind": "not_checked|exact_excerpt|wording_drift|mismatch"},
      "issues": [{"severity": "error|warning", "code": "string", "message": "string"}],
      "suggested_command": "chinalaw article 民法典 585 --format card"
    }
  ],
  "issues": []
}
```

审查规则：

- 只能把 `article` 精确命中当作已核验；`search` 命中不能通过审查。
- 文本中**明确逐字引用**条文内容时，必须和本地条文做一致性检查；普通法律命题、
  摘要或结论不得因为不是原文而报 `quoted_text_mismatch`。
- `reference` / `pending:` 不能因为 `pack validate` 通过就自动视为已核验；`audit pack` 必须继续解析其中的法条引用。
- 文本含日期但未传 `--as-of` 时给出 warning；严格模式下变为 error。

`audit grounding` 额外输出：

```jsonc
{
  "kind": "grounding_audit",
  "snapshot_path": ".chinalaw/snapshots/latest.jsonl",
  "snapshot_record_count": 12,
  "grounding_counts": {
    "verified": 3,
    "retrieved_only": 1,
    "ungrounded": 1,
    "unresolved": 0
  },
  "citations": [
    {
      "raw": "《民法典》第一百四十三条",
      "resolved": true,
      "grounding": {
        "status": "verified|retrieved_only|ungrounded|unresolved",
        "evidence_id": "E0007|null",
        "command": "article|articles|search|outline|fetch|null",
        "evidence_level": "article|search_hit|law|null"
      }
    }
  ]
}
```

Grounding 等级：

- `verified`：最终引用能回连到快照中的 `article` / `articles` / `fetch --article`
  / `norm clause` 级证据。
- `retrieved_only`：快照只显示 `search` / `outline` / `get` / law-level fetch 等候选或预览，
  不能算已核验；`--strict` 下提升为 error。
- `ungrounded`：最终文本出现可解析引用，但快照没有对应证据；始终为 error。
- `unresolved`：引用本身无法解析到本地 article，按普通引用审查错误处理。

项目级检索快照写入：

| 方式 | 效果 |
|------|------|
| `chinalaw snapshot init` | 在当前项目创建 `.chinalaw/snapshots/latest.jsonl`；之后从该项目树内运行支持快照的检索命令会自动追加 |
| `CHINALAW_PROJECT=/path/to/project chinalaw article 民法典 143` | 追加到 `/path/to/project/.chinalaw/snapshots/latest.jsonl` |
| `CHINALAW_SNAPSHOT_OUT=run.jsonl chinalaw search 合同效力` | 追加到指定 JSONL |
| `chinalaw article 民法典 143 --snapshot-out run.jsonl` | 单次命令写入指定 JSONL |

快照是 append-only JSONL，schema 为 `chinalaw.snapshot.v1`，每条记录包含
`evidence_id`、`command`、`argv`、`timestamp`、`laws`、`articles`、
`norm_clauses`、`time_effect` 等 compact evidence 字段；不写入聊天全文。
支持自动写入的命令范围为 `search`、`get`、`article`、`articles`、`outline`、
`fetch`、`history`、`diff`、`trace`、`relation`、`applicable`、`norm clause`。

### 4.8.2 `snapshot init|status`

项目级检索快照管理命令。它不读取或修改法律数据库，只管理项目目录下的
`.chinalaw/snapshots/latest.jsonl`。

| 子命令 | 输入 | 输出 |
|--------|------|------|
| `snapshot init [project]` | 项目目录，默认当前目录；`--reset` 可清空旧记录 | `SnapshotStatus` |
| `snapshot status [project]` | 项目目录，默认当前目录；`--snapshot <jsonl>` 可直接指定文件 | `SnapshotStatus` |

`SnapshotStatus`：

```jsonc
{
  "kind": "snapshot_init|snapshot_status",
  "project_path": "/path/to/project",
  "snapshot_path": "/path/to/project/.chinalaw/snapshots/latest.jsonl",
  "exists": true,
  "record_count": 3,
  "commands": {"article": 2, "search": 1},
  "evidence_levels": {"article": 2, "search_hit": 5},
  "first_timestamp": "2026-05-17T00:00:00+00:00",
  "last_timestamp": "2026-05-17T00:01:00+00:00",
  "write_mode": "auto_when_run_under_project"
}
```

### 4.9 `history <name>` / `diff <name> --from-as-of --to-as-of` / `trace <name> [number]`

公开但本期不重点演示。`history` 列出 revisions；`diff` 输出 `{added, removed, changed, summary, from_revision, to_revision}`。

`trace` 是条文级追溯命令，用于把旧条号 / 条文片段映射到目标版本。它不直接作时间效力结论，只返回本地版本之间的文本对应关系和置信度。

```bash
chinalaw trace 民事诉讼法 257 --from-as-of 2021-01-01 --to-as-of 2024-01-01 --items 3,5 --format json
chinalaw trace 民事诉讼法 --text "终结执行" --from-as-of 2021-01-01 --to-as-of 2024-01-01 --format json
```

输出：

```jsonc
{
  "kind": "law_article_trace",
  "ok": "boolean",
  "input": {
    "name": "string",
    "number": "string|null",
    "text": "string|null",
    "from_as_of": "YYYY-MM-DD",
    "to_as_of": "YYYY-MM-DD",
    "items": ["string"]
  },
  "law": "LawSummary|null",
  "from": {
    "as_of": "YYYY-MM-DD",
    "law": "LawSummary",
    "revision": "Revision|null",
    "as_of_version_date": "YYYY-MM-DD|null",
    "article": "Article",
    "items": [{"number": "string", "found": "boolean", "text": "string|null"}],
    "source_match_score": "number"
  },
  "to": {
    "as_of": "YYYY-MM-DD",
    "law": "LawSummary",
    "revision": "Revision|null",
    "as_of_version_date": "YYYY-MM-DD|null",
    "article": "Article|null",
    "items": [{"number": "string", "found": "boolean", "text": "string|null"}]
  },
  "status": "unchanged|renumbered|amended|moved|deleted",
  "confidence": "number",
  "evidence": ["string"],
  "diff": {
    "number_changed": "boolean",
    "text_changed": "boolean",
    "part_changed": "boolean",
    "similarity": "number",
    "confidence": "number"
  },
  "candidates": [
    {
      "article": "Article",
      "status": "string",
      "confidence": "number",
      "similarity": "number",
      "text_similarity": "number",
      "item_similarity": "number|null"
    }
  ],
  "available_versions": ["VersionSummary"],
  "warning": "low_confidence_or_deleted|null"
}
```

退出码：`ok=true` 返回 `0`；法规不存在、日期无效、版本缺失、起始条文未定位、或目标候选低于可信阈值返回 `1`。agent 不得把 `ok=false` 或 `warning=low_confidence_or_deleted` 的结果当成已核验引用。

### 4.10 `relation <name>` / `applicable --date YYYY-MM-DD` (alpha)

`relation` 查看一部规范与其它规范的显式关系线索。

```json
{
  "kind": "law_relation_result",
  "identifier": "string",
  "law": "Law|null",
  "relation_count": "integer",
  "relations": [
    {
      "id": "string",
      "relation_type": "string",
      "direction": "incoming|outgoing",
      "from_law_id": "string",
      "from_law_title": "string|null",
      "from_law": "Law|null",
      "to_law_id": "string",
      "to_law_title": "string|null",
      "to_law": "Law|null",
      "effective_at": "string|null",
      "source_name": "string",
      "source_url": "string|null",
      "source_checked_at": "string",
      "notes": "string|null",
      "metadata": {}
    }
  ],
  "warnings": [{"severity": "warning", "code": "string", "message": "string"}]
}
```

`applicable` 按日期 / 主题 / 规范 / 场景查询时间效力规则线索：

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `--date` | date | 必填 | 事实或争议时点，`YYYY-MM-DD` |
| `--topic` | str | 无 | 主题，如 `合同效力` |
| `--law` | str | 无 | 限定相关法规 id / 全称 / 简称 |
| `--domain` | str | 无 | 场景，如 `litigation` / `contract_review` |

输出：

```json
{
  "kind": "applicability_result",
  "ok": "boolean",
  "as_of": "YYYY-MM-DD",
  "topic": "string|null",
  "law": "Law|string|null",
  "domain": "string|null",
  "match_count": "integer",
  "matches": [
    {
      "id": "string",
      "topic": "string",
      "domain": "string",
      "primary_law_id": "string",
      "primary_law_title": "string|null",
      "primary_law": "Law|null",
      "fallback_law_id": "string|null",
      "fallback_law_title": "string|null",
      "fallback_law": "Law|null",
      "effective_from": "string|null",
      "effective_to": "string|null",
      "rule_text": "string",
      "transition_text": "string|null",
      "source_name": "string",
      "source_url": "string|null",
      "source_checked_at": "string",
      "confidence": "string",
      "needs_fetch": [{"law_id": "string", "law_title": "string|null", "reason": "missing_law|stub_law|seed_law"}],
      "warnings": [{"severity": "warning", "code": "string", "message": "string"}]
    }
  ],
  "warnings": [{"severity": "warning|error", "code": "string", "message": "string"}]
}
```

必须始终包含 `not_legal_conclusion` warning；无规则命中不是程序错误，返回 `ok=true` + `no_applicability_rule` warning。

### 4.11 `fetch <name>` (alpha · 协议级，参 [ADR-0006](./decisions/ADR-0006-fetch-command.md))

按法律名一条龙完成"取条文 + 清洗 + 入库"，是 sync 之上的薄高层接口。
v0.2.x 标记 alpha；1 个外部用户走通后于 v0.3.0 起去 alpha 标记。

输入：

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `name`（位置） | str | 必填 | 法律名（全称 / 简称 / alias） |
| `--source` | enum | `flk_npc` | 数据源；支持 `flk_npc` / `gov_xzfgk` / `court_gongbao` / `court_main` / `spp_gov_cn` / `csrc_gov_cn` / 证券交易所和自律规则源 |
| `--article` | str | 无 | 中式 / 阿拉伯 / 插入条款；命中后随完整法律一起入库并在响应定位返回 |
| `--dry-run` | flag | false | 不入库，仅输出清洗后的 law payload |
| `--to-fixture <path>` | path | 无 | 把 law payload 写入文件；不入库（用于 PR 审查） |
| `--list-matches` | flag | false | 仅列出搜索命中、不下载、不入库 |
| `--prefer-id <id>` | str | 无 | 多条命中时手动指定候选主键；`gov_xzfgk` / `court_gongbao` / `court_main` / `spp_gov_cn` 也可用 detail_id 直接 fetch |
| `--prefer-bbbs <id>` | str | 无 | `--prefer-id` 的兼容别名；FLK 场景中该 id 即 bbbs |
| `--status` | enum | 无 | 远程搜索候选状态过滤；仅 `flk_npc` 原生支持 `repealed|amended|current|pending_effective`；`gov_xzfgk` 和证券公开源仅接受 `current`；其它源传入时 exit 2 |
| `--limit` | int | 5 | 搜索候选上限 |
| `--force` | flag | false | 即使 `source_hash` 相同也重新清洗并 upsert；用于 cleaning 规则升级后补写 |

JSON 输出 schema（成功 / 主流程）：

```json
{
  "kind": "law_fetch",
  "source": "flk_npc|gov_xzfgk|court_gongbao|court_main|spp_gov_cn|csrc_gov_cn|...",
  "name": "string (用户输入)",
  "matched_id": "string",
  "matched_bbbs": "string",        // 兼容字段；非 FLK 源填同 matched_id
  "matched_detail_id": "string|null",
  "matched_title": "string",
  "candidates": [
    {
      "id": "string",
      "bbbs": "string",            // 兼容字段；非 FLK 源填同 id
      "detail_id": "string|null",
      "title": "string",
      "released_at": "string",
      "status": "current|amended|repealed|pending_effective|unknown",
      "local_law_id": "string|null",
      "local_alias_resolved": "boolean|null"
    }
  ],
  "law": Law,
  "article": Article | null,
  "article_count": "integer",
  "loaded": "boolean",
  "skipped": "boolean",
  "dry_run": "boolean",
  "force": "boolean",
  "wrote_fixture": "string|null"
}
```

`law.warnings`（可选）：opt-in alias_agent 路径上遇到可恢复错误时附加，结构为
`[{"severity": "warning", "code": "alias_agent_skipped", "reason": "missing_api_key|network|invalid_response", "message": "string"}]`。
未启用 `CHINALAW_USE_ALIAS_AGENT` 时不会出现该字段。详见
[`docs/FETCH_LAYER_SPEC.md`](./FETCH_LAYER_SPEC.md) §3。

JSON 输出 schema（list-matches 模式）：

```json
{
  "kind": "law_fetch_candidates",
  "source": "flk_npc|gov_xzfgk|court_gongbao|court_main|spp_gov_cn|csrc_gov_cn|...",
  "name": "string",
  "candidates": [{...}]
}
```

错误模式（exit code 0 / 1 / 2 与 ADR-0002 §6 一致）：

| code | error.error 字段值 | 触发场景 |
|------|---------------------|---------|
| 1 | `FetchNotFoundError` | 搜索零结果；`--article` 在结果中不存在 |
| 2 | `FetchAmbiguousError` | 多条候选无最佳匹配且未提供 `--prefer-id` / `--prefer-bbbs` |
| 2 | `FetchSourceError` | 网络 / 站点结构异常 |
| 2 | `FetchError` | fixture 路径不可写等其他失败 |
| 2 | `ValueError` | 参数语义错误，例如非 `flk_npc` 源传入 `--status` |

错误 JSON：`{"kind": "law_fetch_error", "error": "FetchNotFoundError", "message": "..."}`。当错误为 `FetchAmbiguousError` 且已有搜索候选时，可附带 `candidates` 数组，供 agent 继续调用 `--prefer-id`。

选最佳匹配规则（优先级）：
1. `--prefer-id` / `--prefer-bbbs` 命中候选；对 `gov_xzfgk` / `court_gongbao` / `court_main` / `spp_gov_cn`，`--prefer-id <detail_id>` 可直接按详情页 id fetch，不强制先搜索
2. 本地 alias 已解析到同源 FLK 记录且可从 `source_url` 取得 bbbs 时，直接按该 bbbs fetch
3. 唯一搜索结果直接命中
4. `title == name` 完全匹配
5. 从 `中华人民共和国<name>` 推断出的 short title 等于 `name`
6. `name` 在 `title` 中（包含匹配）
7. 同层多候选时优先 `status=current` 且 `released_at` 最新；否则 → `FetchAmbiguousError`

入库幂等：相同 `source_hash` 默认跳过（`loaded=false, skipped=true`）；不同 hash 写新 revision。传 `--force` 时，即使 `source_hash` 相同也会重新 upsert，用于清洗规则或 alias 规则升级后补写既有数据。

dry-run / to-fixture / list-matches 三种非默认动作互斥：传 `--to-fixture` 即写文件不入库；传 `--dry-run` 即不入库且不写文件；传 `--list-matches` 即只列候选；都不传则默认入库。

`--status` 是远程搜索过滤，不是本地 DB 过滤。传入该参数时，隐式本地 alias /
文号 hint 不得短路远程搜索；但显式 `--prefer-id` / `--prefer-bbbs` 仍按用户指定
id 直接取数，不再额外校验状态。

`court_gongbao` 注意：公报站没有原生全文搜索，当前 `name` 搜索是有界栏目页标题过滤；当 agent 已从公报 URL、`list-matches` 或人工输入拿到详情页 id 时，应优先使用 `--prefer-id <detail_id>` 直接 fetch，避免无界扫站。

`court_main` 注意：最高法主站通过 `https://www.court.gov.cn/search.html?content=<query>` 返回搜索结果，详情页主模式为 `/<channel>/xiangqing/<id>.html`，候选主键写作 `channel/xiangqing/id`。本源仅消费搜索第一页和显式详情页，不做无界栏目爬取；新闻稿页面只有在正文中可清洗出条文时才适合作为可引用 payload。

`gov_xzfgk` 注意：国务院入口页为 `https://www.gov.cn/zhengce/xzfgk/`，实际应用由 `https://xzfg.moj.gov.cn/search2.html` 承载；候选主键写作 `LawID`。本源用于行政法规，清洗为 `level=admin_regulation`，并在响应中保留 `related_versions` 历史沿革提示。

### 4.11.1 `discover` (alpha)

按状态 / 关键词批量列出远程候选法规；不下载、不入库。它是 `fetch` 的探测前哨，
用于 agent 先拿候选池，再用 `fetch --prefer-id <id>` 精确补库。

输入：

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `--source` | enum | `flk_npc` | 支持 `flk_npc`、`gov_xzfgk` 与具备站内搜索/列表语义的证券公开源：`csrc_gov_cn` / `bse_cn` / `sse_com_cn` / `szse_cn` / `chinaclear_cn` / `sac_net_cn` |
| `--query` | str | 空 | 标题关键词；空值表示按源默认顺序列出 |
| `--status` | enum | 无 | `repealed|amended|current|pending_effective` |
| `--limit` | int | 20 | 候选上限 |

JSON 输出 schema：

```json
{
  "kind": "law_discover_candidates",
  "source": "flk_npc|gov_xzfgk|csrc_gov_cn|bse_cn|sse_com_cn|szse_cn|chinaclear_cn|sac_net_cn",
  "query": "string",
  "status": "repealed|amended|current|pending_effective|null",
  "candidates": [
    {
      "id": "string",
      "bbbs": "string",
      "detail_id": "string|null",
      "title": "string",
      "released_at": "string",
      "status": "current|amended|repealed|pending_effective|unknown"
    }
  ]
}
```

错误 JSON：`{"kind": "law_discover_error", "error": "<class>", "message": "..."}`，exit code `2`。`error` 可为业务 / 参数错误（如 `ValueError`）或上游传输 / 解析错误（如 `URLError`、`TimeoutError`、`ConnectionError`、`JSONDecodeError`）；`AttributeError` / `KeyError` / `TypeError` 等编程错误不进入该 envelope。

### 4.12 `ensure [names...]` (alpha)

本地优先确保一批公开法规已经可引用。它先调用 `get_law` 判断本地是否为 populated；仅当法规缺失、为 stub 或为 seed 样例数据时才调用 `fetch`。`--from-dir` 只读取直接子文件名，不读取文件正文。

`--profile` 是推荐规范语料安装入口：它读取 `data/recommended_corpus.json`，按每个条目自己的 `primary_source` 调用 `fetch`。该 manifest 是安装索引，不是法律权威文本；真正可引用文本仍来自 `fetch` 清洗入库后的 source metadata。

输入：

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `names`（位置） | list[str] | 空 | 一个或多个法规名 |
| `--profile <name>` | repeatable string | 空 | 安装推荐语料 profile，如 `baseline` / `general` / `company` / `criminal`；不可与 `names` / `--from-file` / `--from-dir` 混用 |
| `--no-profile-deps` | flag | false | profile 模式下不自动安装 dependencies |
| `--from-file <path>` | path | 无 | 从文本文件读取法规名；每行一个，空行和 `#` 注释跳过 |
| `--from-dir <path>` | path | 无 | 从目录文件名提取法规名；不读取文件正文 |
| `--filenames-only` | flag | false | 显式声明目录模式只读文件名；当前为兼容性标记 |
| `--source` | enum | `flk_npc` | 普通 names 模式缺失时使用的数据源；profile 模式忽略此值，改用条目自己的 `primary_source` |
| `--limit` | int | 5 | 每个缺失法规的 fetch 搜索候选上限 |
| `--interval` | float | 1.0 | 批量 fetch 间隔秒数，仅在实际远程请求之间生效 |

JSON 输出 schema：

```json
{
  "kind": "law_ensure|law_ensure_corpus",
  "ok": "boolean",
  "source": "flk_npc|mixed",
  "db_path": "string",
  "profile_names": "string[]|null",
  "included_profiles": "string[]|null",
  "include_dependencies": "boolean|null",
  "requested_count": "integer",
  "unique_count": "integer",
  "skipped_duplicate_count": "integer",
  "present_count": "integer",
  "fixture_loaded_count": "integer|null",
  "fetched_count": "integer",
  "skipped_count": "integer",
  "failed_count": "integer",
  "rate_limited_count": "integer|null",
  "blocked_sources": "string[]|null",
  "fetch_attempt_count": "integer",
  "items": [
    {
      "name": "string",
      "profile": "string|null",
      "corpus_id": "string|null",
      "priority": "P0|P1|P2|null",
      "source": "string|null",
      "fetch_status": "repealed|amended|current|pending_effective|null",
      "status": "present|loaded_fixture|fetched|skipped|resolved|failed",
      "reason": "already_populated|builtin_fixture|missing_law|stub_law|seed_law|empty_articles|unsupported_source|manual_review|not_installable|missing_title|source_rate_limited",
      "law": "LawSummary|null",
      "fetch": "FetchSummary|null",
      "candidates": "Candidate[]|null",
      "error": "string|null",
      "message": "string|null",
      "retry_hint": "string|null"
    }
  ]
}
```

profile manifest 可为单个条目声明 `fetch_status`，用于把废止 / 已修改的历史法传给 `fetch --status`。当某一官方源返回反爬 / 限流信号时，`ensure --profile` 会停止本轮同源后续远程请求，将后续条目标记为 `source_rate_limited`，并返回 `ok=false`。

退出码：`ok=true` 时 0；参数错误时 2；部分法规无法补全、fetch 返回 0 条文，或本轮触发官方源限流时 1。

### 4.12.1 `corpus list|show` (alpha)

查看推荐规范语料 profile。该命令只读 manifest，不联网、不入库。

输入：

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `list` | literal | 无 | 列出 profile 摘要 |
| `show [profiles...]` | literal + list[str] | `baseline` | 展开一个或多个 profile；默认包含 dependencies |
| `--no-deps` | flag | false | `show` 时不展开 dependencies |

`corpus list` JSON：

```json
{
  "kind": "recommended_corpus_profiles",
  "schema_version": "integer",
  "as_of": "YYYY-MM-DD",
  "path": "string",
  "profile_count": "integer",
  "profiles": [
    {
      "name": "string",
      "priority": "P0|P1|P2",
      "description": "string",
      "dependencies": "string[]",
      "aliases": "string[]",
      "entry_count": "integer",
      "installable_count": "integer",
      "unsupported_count": "integer"
    }
  ]
}
```

`corpus show` JSON：

```json
{
  "kind": "recommended_corpus_profile",
  "schema_version": "integer",
  "as_of": "YYYY-MM-DD",
  "requested_profiles": "string[]",
  "included_profiles": "string[]",
  "include_dependencies": "boolean",
  "entry_count": "integer",
  "entries": [
    {
      "id": "string",
      "profile": "string",
      "title": "string",
      "short_title": "string|null",
      "level": "string",
      "primary_source": "string",
      "source_status": "supported|unsupported|manual_review",
      "installable": "boolean|null",
      "priority": "P0|P1|P2",
      "needs_verification": "boolean"
    }
  ]
}
```

退出码：成功 0；未知 profile 或 manifest 格式错误 2。

### 4.13 `rebuild-clean` (alpha)

用当前 cleaning 规则重建已入库公开法规或私域规范。它是公开维护入口，用于替代 agent/脚本直接查询 SQLite 或调用私有 helper。

输入：

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `--law <name>` | str | 无 | 仅重建指定法规 id / 全称 / 简称 / alias；与 `--norm` 互斥 |
| `--norm <name>` | str | 无 | 仅重建指定私域规范 id / 名称 / 简称 / alias；与 `--law` 互斥 |
| `--dry-run` | flag | false | 只报告变化，不写入数据库 |
| `--limit` | int | 无 | 最多处理多少部法规 / 私域规范；调试用 |

JSON 输出 schema：

```json
{
  "kind": "rebuild_clean",
  "ok": "boolean",
  "found": "boolean",
  "db_path": "string",
  "requested_law": "string|null",
  "requested_norm": "string|null",
  "dry_run": "boolean",
  "cleaning_schema_version": "integer",
  "law_count": "integer",
  "norm_count": "integer",
  "rebuilt_count": "integer",
  "changed_count": "integer",
  "skipped_count": "integer",
  "error_count": "integer",
  "items": [
    {
      "law_id": "string",
      "title": "string",
      "changed": "boolean",
      "loaded": "boolean",
      "article_count": "integer",
      "short_title_before": "string|null",
      "short_title_after": "string|null",
      "aliases_before": "string[]",
      "aliases_after": "string[]",
      "article_text_changed_count": "integer",
      "article_part_changed_count": "integer",
      "article_title_changed_count": "integer"
    }
  ],
  "errors": [
    {"law_id": "string?", "norm_source_id": "string?", "title": "string", "error": "string", "message": "string"}
  ]
}
```

`items[].kind == "norm_source"` 时，明细字段为 `norm_source_id`、`clause_count_before`、
`clause_count_after`、`clause_text_changed_count`、`clause_number_changed_count`。

退出码：`ok=true` 时 0；指定 `--law` / `--norm` 未找到或存在重建错误时 1；参数错误时 2。

### 4.14 `verify-source <source>` (maintenance smoke)

`verify-source` 是维护者 / 发布前验证命令，不写 DB、不写 fixture。它会对真实上游执行：

1. `probe`
2. `search`
3. `fetch/clean`
4. 可选 `article locate`

输入：

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `source` | enum | 必填 | 支持 `flk_npc` / `gov_xzfgk` / `court_gongbao` / `court_main` / `spp_gov_cn` / `csrc_gov_cn` / `bse_cn` / `sse_com_cn` / `szse_cn` / `chinaclear_cn` / `sac_net_cn` |
| `--query` | str | `中华人民共和国民法典` | 用于 smoke 的法规检索词 |
| `--article` | str | `第一条` | 用于 smoke 的条文号；空字符串表示跳过条文定位 |
| `--limit` | int | 5 | 搜索候选上限 |

输出：

```json
{
  "kind": "source_verify",
  "source": "flk_npc|gov_xzfgk|court_gongbao|court_main|spp_gov_cn|csrc_gov_cn|bse_cn|sse_com_cn|szse_cn|chinaclear_cn|sac_net_cn",
  "ok": "boolean",
  "query": "string",
  "article": "string|null",
  "limit": "integer",
  "checked_at": "string",
  "steps": [
    {
      "step": "probe|search|select|fetch_clean|article",
      "ok": "boolean",
      "message": "string",
      "data": "object?"
    }
  ],
  "candidates": [
    {
      "id": "string",
      "bbbs": "string",
      "detail_id": "string|null",
      "title": "string",
      "released_at": "string",
      "status": "current|amended|repealed|pending_effective|unknown"
    }
  ],
  // ok=false 时，selected / law / article_match 可能为 null。
  "selected": {
    "id": "string",
    "bbbs": "string",
    "detail_id": "string|null",
    "title": "string",
    "released_at": "string",
    "status": "string"
  } | null,
  "law": {
    "id": "string",
    "title": "string",
    "short_title": "string|null",
    "level": "string|null",
    "status": "string",
    "source_url": "string",
    "source_hash": "string",
    "source_checked_at": "string",
    "article_count": "integer"
  } | null,
  "article_match": {
    "number": "string",
    "number_display": "string|null",
    "part": "string|null",
    "title": "string|null",
    "text_preview": "string"
  } | null
}
```

退出码：`ok=true` 时 0；`ok=false` 时 2。该命令需要联网，不应作为默认离线 CI 的强制步骤。

### 4.15 `commentary` (alpha, local-only)

法条级注释 / 释义材料入口。该功能服务本机授权材料，不把书籍释义当作项目公开数据再分发。

子命令：

| 命令 | 输入 | 输出 |
|------|------|------|
| `commentary import <file>` | commentary bundle JSON | `CommentaryImportResult` |
| `commentary books` | 无 | `CommentaryBook[]` |
| `commentary article <law> <number>` | 法规名 / id + 条号 | `ArticleCommentaryResult` |

Bundle JSON 最小契约：

```jsonc
{
  "book": {
    "id": "book-civil-code-study",
    "title": "民法典条文释义",
    "author": "string?",
    "publisher": "string?",
    "edition": "string?",
    "published_at": "YYYY-MM-DD?",
    "isbn": "string?",
    "source_name": "local-law-data",
    "source_url": "string?",
    "license_scope": "local_only",
    "metadata": {}
  },
  "items": [
    {
      "law_id": "flk-civil-code-2020",
      "law_title": "中华人民共和国民法典",
      "article_number": "143",
      "article_number_display": "第一百四十三条",
      "page_start": 12,
      "page_end": 13,
      "summary": "string?",
      "excerpt": "string?",
      "ocr_confidence": 0.98,
      "boundary_confidence": 0.95,
      "qa_status": "unchecked|checked|needs_review",
      "license_scope": "local_only",
      "source_hash": "string?",
      "metadata": {}
    }
  ]
}
```

`commentary article` 先按公开法规解析 `<law> <number>`，再用稳定 `law_id +
article_number` 查询 commentary。若条文未命中，返回 `found=false`，不猜测书中条目。

### 4.16 `chinalaw-mcp` (alpha)

MCP 是 CLI 之外的薄 adapter，目标是让偏 MCP 的 agent 不必把整份 skill /
CONTRACT 塞进上下文。MCP server 必须复用公开服务函数，不直接读写 SQLite。

启动：

```bash
chinalaw-mcp --db ~/.chinalaw/chinalaw.db
```

初始工具集限制为低上下文、稳定语义：

| tool | 对应 CLI |
|------|----------|
| `chinalaw_resolve` | `chinalaw resolve` |
| `chinalaw_article` | `chinalaw article` |
| `chinalaw_articles` | `chinalaw articles` |
| `chinalaw_search` | `chinalaw search` |
| `chinalaw_applicable` | `chinalaw applicable` |
| `chinalaw_ensure` | `chinalaw ensure` |

工具返回 `structuredContent` 与 JSON 文本；错误以 `isError=true` 返回，不把法律结论写进自然语言描述。

---

## 5. 规范包 JSON Schema

> 给贡献者写规范包文件用。可被任何 JSON 工具校验。

```jsonc
{
  // 必填
  "name": "合同纠纷裁判依据",

  // 可选 —— 缺省由 name 派生 slug
  "id": "contract-disputes-judgment",
  "summary": "围绕民商事合同纠纷的核心法条 + 私域裁量规范",
  "scope": "民商事合同纠纷诉讼/仲裁",
  "maintainer": "your-handle",
  "version_policy": "current",              // current / as-of:YYYY-MM-DD / pinned
  "source_kind": "manual",                  // manual / generated / imported
  "metadata": { "theme": "litigation" },

  // 可选 —— import 时会自动从 items 推导补全
  "dependencies": {
    "laws": [
      { "law_id": "flk-civil-code-2020", "law_title": "中华人民共和国民法典" }
    ],
    "norm_sources": [],
    "packs": []
  },

  // 必填，至少 1 个
  "items": [
    {
      "item_type": "article",               // 见 §2.6
      "law_id": "flk-civil-code-2020",
      "law_title": "中华人民共和国民法典",
      "article_number": "第五百零九条",     // 接受中式 / 阿拉伯 / 插入条款
      "article_number_display": "第五百零九条",
      "role": "core",
      "reason": "全面履行合同义务的总则条款，几乎所有违约纠纷的引子。",
      "note": "审查时若涉及格式条款应同时引第四百九十六条。",
      "position": 1
    },
    {
      "item_type": "norm_clause",
      "norm_source_name": "甲方放款要求（示例）",
      "clause_number": "第二条",
      "role": "core",
      "reason": "..."
    },
    {
      "item_type": "reference",
      "reference_text": "诉讼时效问题须先核对民法典总则编 §188、§189。",
      "role": "supporting"
    }
  ]
}
```

**校验规则**（失败 → import 报错）：

1. `name` 非空。
2. `items` 是非空数组。
3. 每个 item 满足 §2.6 必填字段约束。
4. `dependencies` 若提供必须是 object（缺省 `{}`）。

**警告规则**（不报错但 `validate` 列出）：

1. `core` / `important` 角色没填 `reason` → `missing_reason` warning。
2. `role` 不在受控集合内 → `unknown_role` warning。
3. 引用了 DB 中找不到的法规 / 私域规范 → `validate` 报错（error）。
4. `core` / `important` 角色的 `reference` item，`note` 以 `pending:` 开头 → `pending_reference_in_pack` warning。
   该约定让 agent 在使用规范包时知道：这是"待 fixture 补全"的占位摘要，**不能**直接当作已核验法条引用，必须先经 `chinalaw search` / `chinalaw article` 取得官方原文。维护者补全 fixture 后应将该 item 改回 `article` 类型。

**article 类型严格性**（v0.2 起）：

`article` 类型 item 必须解析到具体条文，否则 `validate` 一律报 error（不是 warning）。错误码：

- `missing_law_for_article`：法规未入库；先 `sync --fixtures`。
- `stub_law_pending_articles`：法规已索引但 `articles_coverage=stub`；按 `docs/DATA_INDEX.md §3` 补全条文，或将该 item 改成 `reference`。
- `pending_article_in_dataset`：法规有部分条文，但具体条款不在数据集；与"条款号写错"在外部不可区分，需补全 fixture 或核对条款号。

不允许通过 warning 静默 article 类型未解析；这是 agent-first 项目的依据完整性闸门。

---

## 6. 私域规范文件 JSON Schema

```jsonc
{
  "name": "甲方放款要求（示例）",          // 必填
  "id": "lending-policy",                 // 可选，缺省由 name 派生
  "short_name": "放款要求",
  "aliases": ["甲方放款要求", "放款标准"],
  "source_type": "lender_requirement",    // 必填，开放枚举
  "authority": "某甲方风控部",
  "binding_scope": "某融资项目放款审查",
  "jurisdiction": "CN",
  "effective_at": "2026-01-01",
  "repealed_at": null,
  "source_url": null,
  "source_name": "local-file",            // 必填
  "source_checked_at": "2026-04-26T00:00:00+08:00",
  "source_hash": "sha256-hex-or-omit",    // 可选，缺省由 clauses 计算
  "metadata": { "ingest": { "path": "..." } },
  "clauses": [
    {
      "number": "第一条",
      "number_display": "第一条",
      "title": null,
      "text": "借款主体应提交完整、真实、有效的工商登记及授权文件。"
    },
    { "number": "2.1", "number_display": "2.1", "text": "..." }
  ]
}
```

---

## 7. 法规数据 JSON Schema（用于 `sync --from-dir`）

```jsonc
{
  "id": "flk-civil-code-2020",
  "title": "中华人民共和国民法典",
  "short_title": "民法典",
  "aliases": ["民法典"],
  "level": "law",                         // 见 §2.9
  "status": "current",                    // 见 §2.9
  "issuing_body": "全国人民代表大会",
  "document_number": "主席令第四十五号",
  "released_at": "2020-05-28",
  "effective_at": "2021-01-01",
  "repealed_at": null,
  "source_url": "https://...",
  "source_name": "flk.npc.gov.cn",
  "source_checked_at": "2026-04-26T00:00:00+08:00",
  "source_hash": "sha256...",             // 可选

  "categories": [                         // 可选；分类树节点
    {"id": "flk:1", "name": "法律", "parent_id": null, "description": null}
  ],
  "category_ids": ["flk:1"],              // 可选；该法规所属分类

  // 用于 revision 快照
  "version_label": "2020 版",
  "revision_id": "flk-civil-code-2020@2020",
  "revision_released_at": "2020-05-28",
  "revision_notes": "首次颁布",

  "articles": [                           // 必填
    {
      "number": "1",                      // 标准化号（必填）
      "number_display": "第一条",
      "part": "第一编 总则/第一章 基本规定",
      "title": null,
      "text": "为了保护民事主体的合法权益……",
      "position": 1                       // 可选，缺省按数组顺序
    }
  ]
}
```

**幂等性**：同一 `id` 重复 import → upsert，不会重复条文。如条文集合变更 → 写入新 `revision`。

---

## 8. 不属于协议的内容

以下是当前实现细节，不进入兼容承诺：

- **FTS5 tokenizer 选择**（trigram）：未来可能切到 ngram / jieba，调用方应假设"中文检索可用"，不假设具体策略。
- **LIKE 回退阈值**（< 3 字）：可能调整。`strategy` 字段告知实际策略。
- **真实数据源同步内部协议**：flk_npc 适配器、bbbs / search_list / 增量窗口等，本期不承诺稳定。
  但建立在其上的 `fetch` 命令（参 §4.11）是协议级接口，其输入 / 输出 / 退出码受协议保护。
  `verify-source`（参 §4.14）是维护者 smoke 接口，输出字段用于诊断，不等同于稳定数据源 adapter API。
- **Markdown 渲染样式**：`--format md` 的具体字符、heading 级别可能调整；JSON 输出才是契约。
- **进度 meta keys**：`source:<source>:*` 系列由实现内部使用，外部不应依赖。
- **schema 内部迁移函数**：`_migrate_v1_to_v2` 等不是公开 API。
- **Python 模块路径**：`chinalaw.service` / `chinalaw.normpacks` 等可重组。

---

## 9. 协议变更流程

1. 提 issue，标 `protocol-change`。
2. 作者写 ADR（`docs/decisions/ADR-XXXX-...md`），含 Context / Decision / Consequences。
3. ADR 合并即视为决议。
4. 实现 PR 必须同时更新本文件 + ADR 链接 + 测试。
5. 任何 breaking change 必须先发 alpha tag（如 `v0.2.0a1`），等至少 1 个早期用户跑通后再发正式 tag。

---

## 10. 协议自检清单

发布前回答这些问题。任何答 No 都需要在 ADR 中说明。

- [ ] 数据库 schema 与本文档一致？
- [ ] 每个 CLI 命令的 JSON 输出 schema 都被测试覆盖？
- [ ] 退出码与本文档一致？
- [ ] 引用元数据（`source_url` / `source_hash` / `source_checked_at`）字段在所有公开法规与私域规范上都齐全？
- [ ] `--format md` 与 `--format json` 信息量等价？
- [ ] 规范包 JSON 格式可被新人直接照抄、不依赖代码注释？
- [ ] 30 分钟内可以读完本文档？

---

## 附：术语速查

| 术语 | 含义 |
|------|------|
| 公开法规（law） | 法律 / 行政法规 / 司法解释 / 部门规章 / 地方性法规 / 地方政府规章 |
| 条文（article） | 公开法规的单个条款 |
| 私域规范（norm_source） | 用户内部规范文件（合规手册、风控政策、行业标准等） |
| 私域规范条款（norm_clause） | 私域规范的单个条款 |
| 规范包（norm_pack） | 围绕一个具体场景把法条 + 私域条款 + 提示语打包 |
| 引用元数据 | `source_url` / `source_name` / `source_checked_at` / `source_hash` |
| as-of 查询 | 按指定日期获取当时有效版本 |
| 引用追溯 | 任何输出都能回到来源 URL + 核查时间 + 内容指纹 |
