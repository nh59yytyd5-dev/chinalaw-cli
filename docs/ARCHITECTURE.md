# 架构设计

> 本文档只描述当前实现和近期演进边界。产品定位见
> [`PROJECT_CHARTER.md`](./PROJECT_CHARTER.md)，外部协议见 [`CONTRACT.md`](./CONTRACT.md)。

## 1. 当前形态

`chinalaw-cli` 的 CLI 核心使用 stdlib 与 SQLite，向 agent 和人类提供 JSON / Markdown。
可选的 FastAPI 服务复用同一业务层，提供人工管理面板、只读 REST 与 MCP HTTP；不替换现有 CLI / stdio MCP。

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

CLI 不引入 Typer、Click、Rich 或外部数据库。浏览器使用随包构建好的 React/TypeScript 静态资源；
FastAPI/Uvicorn/MCP 等依赖只在 `server` 可选安装项中加载。

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
| `src/chinalaw/search_indexes.py` | 精确别名索引、FTS rowid 映射与重建 |
| `src/chinalaw/db.py` | SQLite 连接、migration、meta |
| `src/chinalaw/schema.py` | 当前 schema v14 DDL |
| `src/chinalaw/admin/` | 分页目录、内容指纹、冻结草稿、受管附件、任务、维护门控、备份恢复 |
| `src/chinalaw/server/` | HTTP 路由、所有者会话、OAuth、MCP HTTP、请求边界、服务启动 |
| `web/` | React/TypeScript 面板源码与 Playwright 测试 |
| `src/chinalaw/server/static/` | 安装包内的离线静态界面与许可声明 |
| `src/chinalaw/adapters/flk_npc.py` | 国家法律法规数据库 adapter |
| `src/chinalaw/normsources.py` | 私域规范导入、导出、切条、检索 |
| `src/chinalaw/normpacks.py` | 本地规范包导入、导出、展示、校验 |
| `src/chinalaw/formatters.py` | Markdown 输出 |

## 3. 当前数据模型

当前 schema 版本是 v14。

核心表：

- `laws`
- `articles`
- `revisions`
- `norm_sources`
- `norm_clauses`
- `norm_source_revisions`
- `norm_packs`
- `norm_pack_items`
- `law_relations`
- `applicability_rules`
- `document_number_index`
- `commentary_books`
- `article_commentaries`
- `categories`
- `law_categories`
- `meta`
- `laws_fts`
- `articles_fts`
- `norm_sources_fts`
- `norm_clauses_fts`
- `law_alias_index`
- `laws_fts_rows` / `articles_fts_rows`
- `norm_sources_fts_rows` / `norm_clauses_fts_rows`
- `library_artifacts` / `library_drafts` / `library_operations`
- `library_reviews` / `library_jobs`

当前没有 `alias_records` / `call_log`。新增公开数据模型前必须先留下公开设计记录并同步
CONTRACT。

### 人工维护与远程访问边界

`admin.payloads` 通过 canonical loader / norm importer 生成与最终写入一致的规范化内容。
`admin.drafts` 保存完整预览、基线指纹和来源信息；在同一 SQLite 写事务中校验冲突、写入正文/索引/修订、记录维护事件。
HTTP 路由不通过 shell 执行 CLI，也不接受任意服务器路径。

`admin.jobs` 保存持久任务，单 worker 在网络和解析阶段不持有 SQLite 写事务；服务重启把未完成工作标为中断。
进程文件锁防止两个维护 worker 同时启动。`admin.gate` 协调后台任务、人工请求和全库恢复，SQLite 事务继续协调外部 CLI 写入。

上传文件以 SHA-256 为受管文件名，先落盘后写引用，使用安全副本交给既有 DOCX/PDF/文本 reader。
备份使用 SQLite 在线快照和附件清单；恢复校验后在原数据库文件内进行事务性数据替换与索引重建，失败回滚，不在线替换数据库 inode。

`server.auth_store` 将密码、会话、访问凭据、OAuth 状态放入独立 `auth.db`，不随资料库备份迁移。
`server.oauth` 负责单所有者同意与持久化，官方 MCP SDK 负责 OAuth/PKCE 协议端点。
公共与私域查询权限在每个 REST/MCP 请求处检查；只读请求使用 `connect_readonly` / `read_only_operation`，不初始化或迁移资料库。

部署和用户操作详见 [ADMIN_SERVER.md](ADMIN_SERVER.md)。

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

已落地的分层与生命周期机制：

- `source_type` 是受控枚举（CONTRACT §2.9），按约束力来源分类，导入时
  fail loud 校验；私域规范之间不做绝对效力排序，输出层以 `binding_note`
  附该类型的约束力定性提示，不做逐条语义冲突判断。
- `article` fallback 私域条款时 `status` 固定为 `not_applicable` 并带
  `via: "norm_fallback"`；`search` 同命中公开法与私域时顶层附加
  `conflict_notice`；markdown 输出附醒目提示与中文类型名。
- MCP 默认不暴露私域（`--allow-private-norms` 显式开启）；`norm export`
  带 `sensitivity` / `notice` 防泄漏标注，支持 `--metadata-only`。
- 每次导入写 `norm_source_revisions` 快照（schema v12），支撑
  `norm history` / `norm diff` / `norm delete`，以及 `rebuild-clean --norm`
  在原文件丢失时从快照重建。

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

## 11. Agent 接口分层：CLI / Skill / MCP

主协议是 CLI + JSON + 退出码。Skill 是 agent 的使用纪律，MCP 是给不擅长 shell 的
agent 的薄适配层。

| 层 | 责任 | 不做 |
|------|------|------|
| CLI | 原子能力、稳定 JSON、退出码、快照 / audit / fetch / cleaning | 不输出最终法律意见 |
| Skill（`.claude/skills/`） | 何时查、怎么查、缺失时怎么降级、哪些行为禁止 | 不承诺机器 schema |
| MCP（`mcp.py`、`server/mcp_http.py`） | 少量低上下文 tool | 不复制 skill 文档，不绕过 CLI 契约 |

当前 MCP tool 都是公开 CLI 命令的薄包装。stdio：`chinalaw_resolve`、`chinalaw_search`、
`chinalaw_article`、`chinalaw_articles`、`chinalaw_applicable`、`chinalaw_ensure`；
HTTP（面板服务，只读）：`chinalaw_resolve`、`chinalaw_search`、`chinalaw_article`、
`chinalaw_document`、`chinalaw_list`。tool 描述只写调用目的、风险等级和关键降级信号；
检索方法与审查流程放在 skill，参数与退出码放在 `chinalaw schema` 和 `CONTRACT.md`。
MCP 输出必须保留 `source_url`、status、warning / diagnosis 和条文级证据。

新增 MCP tool 前必须回答：

- 是否能映射到公开 CLI 命令。
- 是否需要写库、联网或触发 authority risk。
- 是否能通过 `chinalaw schema mcp --format json` 自省。
- 是否有 CLI 同等测试或 service 层测试。
- 是否会让 agent 绕过 `audit` / `snapshot` / `fetch` 诊断链。

CLI parser、`metadata.py` 的 schema、`CONTRACT.md` 和 skills 四处漂移时，以 CLI parser
的实际行为和 `metadata.py` 的自省结果为修复入口。

## 12. 演进约束

- 新 schema 表必须先在公开 issue / PR 中留下设计记录。
- 新 CLI 协议必须同步 CONTRACT 和 EXAMPLES。
- 不引入运行时依赖，除非先讨论并记录。
- 不为未来功能提前做大重构。
- 当前代码可以逐步拆模块，但不能破坏现有 JSON 输出。
