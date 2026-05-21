# 架构设计

> 本文档只描述当前实现和近期演进边界。产品定位见 [`PROJECT_CHARTER.md`](./PROJECT_CHARTER.md)，当前计划见 [`MVP_PLAN.md`](./MVP_PLAN.md)。

## 1. 当前形态

`chinalaw-cli` 当前是一个 stdlib-only Python CLI，使用 SQLite 作为本地规范库，面向 agent 和人类同时提供 JSON / Markdown 输出。

```text
Agent / User
    |
    v
argparse CLI
    |
    v
service / fetch / sync / rebuild / applicability / normsources / normpacks
    |
    v
loader + db.migrate
    |
    v
SQLite + FTS5
```

当前不引入 Typer、Click、Rich、外部数据库、后台服务或远程账号系统。

## 2. 主要模块

| 模块 | 职责 |
|------|------|
| `src/chinalaw/cli.py` | CLI 参数、命令分发、退出码、输出格式、JSON `_notice` 附加 |
| `src/chinalaw/service.py` | 公开法规检索、名称解析、条文定位、版本快照、状态报告 |
| `src/chinalaw/fetch.py` | agent 友好的按需获取入口，封装候选选择、canonical id、入库 / dry-run / fixture 输出 |
| `src/chinalaw/rebuild.py` | 用当前 cleaning 规则重建已入库法规，避免脚本直连 SQLite 或私有 helper |
| `src/chinalaw/sources.py` | 数据源 adapter 编排、probe、真实源 verify smoke |
| `src/chinalaw/cleaning.py` | 外部来源清洗层；当前覆盖 FLK 详情、本地 DOCX / 旧版 `.doc`、Markdown / plain text、canonical JSON |
| `src/chinalaw/mcp.py` | 轻量 MCP stdio adapter，薄包装公开 service / ensure 能力 |
| `src/chinalaw/metadata.py` | agent-facing CLI / MCP schema、risk、side effect、常见误用的单一元数据来源 |
| `src/chinalaw/doctor.py` | 本机安装、DB、skills、MCP、source smoke 的健康检查 |
| `src/chinalaw/notices.py` | JSON `_notice` 轻量本地提示，不改变主结果语义或退出码 |
| `src/chinalaw/audit.py` | 文件 / 规范包 / 私域规范 / grounding snapshot 审计 |
| `src/chinalaw/snapshots.py` | 项目级 grounding snapshot 初始化、状态和追加 |
| `src/chinalaw/sync.py` | 维护者同步入口，支持 fixture、目录、flk_npc query / bbbs / batch / incremental |
| `src/chinalaw/applicability.py` | 时间效力 / 规范关系 seed 数据导入 |
| `src/chinalaw/loader.py` | JSON payload 幂等入库，维护 FTS |
| `src/chinalaw/db.py` | SQLite 连接、migration、meta |
| `src/chinalaw/schema.py` | 当前 schema v9 DDL |
| `src/chinalaw/adapters/flk_npc.py` | 国家法律法规数据库 adapter |
| `src/chinalaw/normsources.py` | 私域规范导入、导出、切条、检索 |
| `src/chinalaw/normpacks.py` | 本地规范包导入、导出、展示、校验 |
| `src/chinalaw/formatters.py` | Markdown 输出 |

## 3. 当前数据模型

当前 schema 版本是 v9。

核心表：

- `laws`
- `articles`
- `revisions`
- `norm_sources`
- `norm_clauses`
- `norm_packs`
- `norm_pack_items`
- `law_relations`
- `applicability_rules`
- `document_number_index`
- `commentary_books`
- `commentary_items`
- `categories`
- `law_categories`
- `meta`
- `laws_fts`
- `articles_fts`
- `norm_sources_fts`
- `norm_clauses_fts`

当前没有 `alias_records` / `call_log`。这些属于 active plan 的后续方向，进入实现前必须先写 ADR 并同步 CONTRACT。

## 4. 检索

公开法规检索：

- 长查询走 SQLite FTS5 trigram。
- 1-2 字短查询回退 SQL `LIKE`。
- 法规解析支持 id、title、short_title、aliases。
- 条号解析支持中文数字、阿拉伯数字和插入条款号。

私域规范检索：

- `norm_sources` / `norm_clauses` 单独维护 FTS。
- 输出必须保留 `source_type`、`authority`、`binding_scope` 等元数据。

## 5. 同步与 Fetch

`sync` 和 `fetch` 的边界：

| 能力 | 使用者 | 定位 |
|------|--------|------|
| `sync` | maintainer | 参数面较大，适合批量、增量、目录加载 |
| `fetch` | agent / 日常用户 | 参数面小，适合“缺哪条补哪条” |
| `ensure` | agent / 日常用户 | 本地优先，批量确认“这些法规是否已可本地引用” |
| `rebuild-clean` | maintainer / agent | 清洗规则升级后重建本地法规 |
| `verify-source` | maintainer / release | 只读 smoke，验证上游 probe/search/fetch-clean/article locate 链路 |

`fetch` 当前是协议级 alpha。它不替代 `sync`，而是在 `sync` 之上提供 agent 友好的薄入口。
`ensure` 是 `fetch` 之上的业务入口：先查本地 populated 法规，缺失或 stub 时才调用 `fetch`；目录模式只读文件名，不读取用户素材正文。
`rebuild-clean` 是 cleaning 升级后的公开维护入口；不要让 agent 直接查询 SQLite 或调用 `_...` 私有 helper 来修补旧数据。
`verify-source` 不写 DB、不写 fixture；它需要联网，不应作为默认离线 CI 的强制步骤。

## 6. Cleaning 目标

当前已经抽出 `cleaning.py`：FLK 详情、本地 DOCX / 旧版 `.doc`、Markdown / plain text、canonical JSON 都可转换成 loader 可入库 payload。FLK 新版 Word 下载通常是 DOCX；部分旧司法解释仍是 `.doc`，本机存在 `textutil` 或 `antiword` 时会先转文本再进入同一切条逻辑。私域 `norm ingest` 支持 txt/md/docx/pdf，其中 PDF 依赖本机 `pdftotext` 做文本抽取。后续要继续把商业 MCP 等外部服务返回接入同一入口：

```text
adapter / local file / commercial MCP
    |
    v
cleaning.canonicalize(raw, source_kind)
    |
    v
loader.load_payload(canonical_payload)
```

原则：

- adapter 只负责取数。
- cleaning 负责解析和规范化。
- loader 负责幂等入库。
- source metadata 和 hash 不得丢失。

外部 source contract（含商业 MCP / 第三方 API）：

- adapter 必须提供：`title`、`source_name`、`source_url` 或等价出处、`retrieved_at`
  / `source_checked_at`、上游稳定 ID、status hints、正文或条文 anchors。
- adapter 可以提供：`document_number`、`issuing_body`、`released_at`、`effective_at`、
  `license_scope`、`cache_policy`、upstream revision id。
- adapter 不得提供本地最终 stable id；stable id 由 fetch/canonicalize 结合既有 fixture
  / DB / identity 规则决定。
- commercial MCP 返回结果只能作为 upstream raw payload；不得成为 runtime 必需依赖，
  也不得绕过 cleaning 直接进入 loader。
- 许可 / 访问约束必须进入 metadata，不得被清洗丢弃；不能再分发的来源只允许本地缓存。
- 跨源去重用 title / date / source_name / document_number / source hash 的组合判定，
  不以 vendor opaque id 直接等同本地法规 id。

## 7. 时间效力演进

当前已有：

- `revisions`
- `history`
- `get/article --as-of`
- `diff`
- `relation`
- `applicable`

尚未实现：

- `get/article --applicable-on`

近期目标不是自动作出法律适用结论，而是提供检索辅助：施行 / 废止时间、版本线索、旧法 fetch 路径、过渡规则文本和 warning。

## 8. 私域规范

私域规范是 first-class 数据，不是注释。

要求：

- 保留制定主体。
- 保留约束范围。
- 保留来源类型。
- 输出时明确它不是国家法。

## 9. 规范包

规范包当前只作为本地轻量复用层：

- 标签
- 收藏
- 问题域清单
- agent 工作流提示

当前不做包仓库、签名、远程安装源、团队分发。`pack validate` 必须继续区分 resolved item、pending reference、missing dependency。

## 10. Source Text Safety

所有 adapter / 私域规范 / commercial MCP / 本地文件输入都按**数据**处理，
不是 agent 指令。实现边界：

- adapter 只返回 raw fields；不得直接写 DB。
- cleaning 只做结构化、切条、hash、metadata、alias 规范化；不得把一次性运行诊断
  写入 canonical law payload。
- loader 只接收 canonical payload 并幂等入库。
- Markdown 输出中条文和私域规范正文应以来源文本框架呈现；JSON 输出保留
  `source_name` / `source_url` / `source_checked_at` / `source_type` 等 authority
  metadata，由上层 agent 判断能否引用。
- agent skill 必须明示：来源文本中出现的"忽略前文 / 执行命令 / 删除文件"等语句
  仍是被检索材料的一部分，不得执行。

## 11. 演进约束

- 新 schema 表必须先有 ADR。
- 新 CLI 协议必须同步 CONTRACT 和 EXAMPLES。
- 不引入运行时依赖，除非先讨论并记录。
- 不为未来功能提前做大重构。
- 当前代码可以逐步拆模块，但不能破坏现有 JSON 输出。
