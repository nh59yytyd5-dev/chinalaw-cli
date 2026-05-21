# Cleaning 规范

> 目标：所有上游来源进入本地库前，都清洗成 agent 可读取、可引用、可追溯的统一结构。

## 1. 职责边界

| 层 | 职责 |
|----|------|
| adapter | 只负责取数：搜索、详情、下载原始正文 |
| cleaning | 解析正文、归一化条号、识别结构标题、派生简称和 alias、生成 `source_hash` |
| loader | 幂等入库、维护 FTS、写 revision 快照 |
| service | 查询、名称解析、条文定位 |
| CLI | 对 agent 暴露稳定入口、JSON schema 和退出码 |

agent 和外部脚本不应直接调用 `_split_trailing_structural_headings()`、`_resolve_law_row()` 等私有 helper，也不应直接读写 SQLite。缺公开能力时，先补 CLI / module API。

## 2. 当前 source_kind

`chinalaw.cleaning.canonicalize(raw, source_kind=...)` 当前支持：

- `flk_npc_detail`：国家法律法规数据库详情 + Word 正文。
- `external_json`：已接近 canonical schema 的 JSON fixture。
- `markdown`：本地 Markdown / txt 文本。
- `docx` / `docx_bytes`：本地 DOCX；旧版 `.doc` 在有 `textutil` 或 `antiword` 时转换成文本。

商业 MCP、第三方数据库、本地 PDF 等新来源，必须先实现 adapter / ingest 层，再进入同一 cleaning 输出结构。

## 3. Canonical Payload

清洗输出必须至少包含：

- `id`
- `title`
- `short_title`
- `aliases`
- `level`
- `status`
- `source_url`
- `source_name`
- `source_checked_at`
- `source_hash`
- `articles[]`

`articles[]` 每项至少包含：

- `number`
- `number_display`
- `text`
- `part`
- `position`

`source_hash` 表示上游内容身份，不表示 cleaning 代码版本。因此 cleaning 规则升级后，即使 `source_hash` 不变，也可能需要重建本地库。

## 4. Alias 规则

清洗阶段会通过 `aliases.py` 派生常用简称，例如：

- `合同编通则解释` / `合通解释`
- `总则编解释`
- `物权编解释一` / `物权编解释`
- `担保制度解释` / `担保解释`
- `民诉法解释`

读取阶段会用同一套规则兼容旧数据，所以旧库即使未持久化新 alias，也可以被 `get/article/search --in/fetch` 的本地解析路径识别。

## 5. Cleaning 版本与重建

当前 cleaning schema version：`CLEANING_SCHEMA_VERSION = 1`。

规则：

1. 修改条文结构、条号、alias 派生、正文清洗语义时，应评估是否需要提升 `CLEANING_SCHEMA_VERSION`。
2. 只改内部代码组织、不影响输出语义时，不需要提升版本。
3. 清洗规则升级后，维护者应运行 `rebuild-clean` 或 `fetch --force` 补写既有数据。

推荐命令：

```bash
# 先看会改什么
PYTHONPATH=src python3 -m chinalaw rebuild-clean --dry-run --format md

# 只重建一部法规
PYTHONPATH=src python3 -m chinalaw rebuild-clean --law 合同编通则解释 --format json

# 远程重新 fetch，但 source_hash 相同时仍强制重新 upsert
PYTHONPATH=src python3 -m chinalaw fetch 民法典 --force --format json
```

`rebuild-clean` 优先使用 `revisions.snapshot_json` 重放 cleaning；旧库没有快照时，会从当前 `laws/articles` 行重建 payload。

## 6. Agent 禁止路径

为了降低 agent 不遵循规范的概率，以下做法视为错误：

- 直接查询 SQLite 表替代 `laws/search/get/article/articles/outline/status`。
- 直接 import `_...` 私有函数来清洗或解析条文。
- 在 `source_hash` 未变时假设 cleaning 不需要重建。
- 把 pending reference 或搜索候选当成 resolved article 引用。
- 跳过 `pack validate` 后输出确定法律依据。

正确路径：

- 缺法规：`ensure` 或 `fetch`。
- 缺条文：`fetch <law> --article <number>`。
- 清洗规则升级：`rebuild-clean` 或 `fetch --force`。
- 需要目录/批量条文：`outline` / `articles`。
- 需要复用问题域：`pack add/show/validate`。
