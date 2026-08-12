---
name: chinalaw-maintaining
description: chinalaw 本地数据库维护与备份 skill。何时使用：首次安装、定期保鲜、数据库迁移、新机器配置、多项目布局、status 显示异常、备份 / 恢复。
---

# chinalaw-maintaining

> 本地 db 是 agent 的事实根基。维护得好 = 检索 / 核对 / 补全都顺；维护
> 不好 = 老数据 + 漂移 + 跨项目串数据。

## 触发场景

- 新机器首次安装
- 定期保鲜（每周 / 每月增量同步）
- 多项目布局（机密项目要不要独立 db？）
- `chinalaw status` 显示异常（laws 数 / articles 数 / oldest_freshness_days）
- 想备份 / 恢复 / 迁移到其他机器
- 清洗规则升级后重建（rebuild-clean）
- 怀疑上游漂移（verify-source）

## 默认目录布局

| 路径 | 内容 | 是否要备份 |
|------|------|----------|
| `~/.chinalaw/chinalaw.db` | 主数据库（laws / articles / norms / packs / applicability / law_relations 全部） | ✅ |
| `~/.chinalaw/custom-fixtures/`（建议） | 用户自定义 fixture JSON | ✅（git 控版） |
| `~/.chinalaw/norms/`（建议） | 私域规范源文件（docx / pdf / md） | ✅（git 控版） |
| `~/.chinalaw/packs/`（建议） | 规范包 JSON | ✅（git 控版） |
| `<repo>/data/fixtures/` | 仓库自带 fixture（随 sdist 一起） | 否（仓库已含） |
| `<repo>/data/applicability/` | 仓库自带 applicability seed | 否 |

`DEFAULT_DB_PATH = Path.home() / ".chinalaw" / "chinalaw.db"`，定义在
`src/chinalaw/db.py:20`。所有命令可用 `--db <path>` 覆盖。

## 多项目布局

### 模式 1：全局共享（默认 / 推荐）

```bash
# 所有 agent / 项目共用 ~/.chinalaw/chinalaw.db
chinalaw <cmd>
```

适用：

- 个人主力机器，多个项目都做法律检索
- norm / pack 内容跨项目复用
- 不存在严格机密隔离

### 模式 2：项目独立

```bash
# 每个项目自带独立 db
chinalaw --db ./project.db <cmd>

# 或 export
export CHINALAW_DB="$PWD/project.db"
chinalaw <cmd>   # --db 显式参数优先于环境变量
```

适用：

- 项目机密 norm 不能流出（甲方放款制度 / 内部审批流程）
- 多客户场景，每客户一份隔离 db
- CI / 测试环境需要可复现的 fixture state

### 模式 3：分层（公开 + 私域分离）

主 db `~/.chinalaw/chinalaw.db` 只放公开法 + 通用 norm；项目级 db 只放
项目私域 norm。agent 跑命令时同时查询不可行（CLI 一次只接一个 `--db`），
所以这种模式需要 agent 在 prompt 里明确分阶段 —— 一般场景不推荐，除非
合规明确要求隔离。

## 维护 SOP

### SOP-1：首次安装

```bash
# 1. 安装 CLI（推荐 editable install）
git clone https://github.com/<owner>/chinalaw-cli.git
cd chinalaw-cli
scripts/install-local

# 2. 加载内置 fixture（公开法基线）
chinalaw sync --fixtures
chinalaw ensure --profile baseline --format md

# 3. 查看推荐规范 profile；开源预览期不要无脑批量安装大 profile
chinalaw corpus list --format md
chinalaw corpus show general --format md

# 4. 按当前工作流缺口逐部补。遇到 FLK 反爬挑战立即停止，不要循环重试。
chinalaw ensure 劳动合同法 --format md

# 5. 加载 applicability seed
chinalaw sync --applicability

# 6. 自检
chinalaw status --format md
```

### SOP-2：定期保鲜（每周 / 每月）

```bash
# 增量同步官方源（带 --max-pages 限节流）
chinalaw sync --source flk_npc --incremental --max-pages 10

# applicability seed 扩展时拉新
chinalaw sync --applicability

# 顺便更新 CLI 版本
cd chinalaw-cli && scripts/update-local
```

`--incremental` 基于发布日期窗口拉，比全量便宜很多。`--max-pages` 防止
误翻太多页（COMPLIANCE 节流硬下限 100ms 是 adapter 层强制，但调用方应
继续用 `--max-pages` 自约束）。

### SOP-3：怀疑上游漂移 / 发布前

```bash
chinalaw verify-source flk_npc --format json
chinalaw verify-source court_gongbao --query 劳动争议 --article 第一条 --format json
chinalaw verify-source spp_gov_cn --query 袭警 --format json
```

`verify-source` 跑 probe → search → fetch / clean → article locate 全链路
smoke。任一步骤失败 → 上游可能改版，issue 报送维护者。

### SOP-4：清洗规则升级

```bash
# 仓库 cleaning.py / aliases.py 更新后
chinalaw rebuild-clean --dry-run --format md
# 看会发生什么变化（哪些法规会被重清洗 / alias 会变）

# 实跑
chinalaw rebuild-clean --format json
# 或限定单部
chinalaw rebuild-clean --law 合同编通则解释 --format json
```

**不要绕开 `rebuild-clean` 直接改 SQLite。** 它是 cleaning 规则升级时的唯一
合法路径。

### SOP-5：备份

```bash
# SQLite 关闭进程后冷拷贝即可（无 lock 风险）
cp ~/.chinalaw/chinalaw.db ~/.chinalaw/backup/chinalaw-$(date +%Y%m%d).db

# 跨机器：tar 整个 ~/.chinalaw/
tar czf chinalaw-backup-$(date +%Y%m%d).tar.gz ~/.chinalaw/
```

恢复：

```bash
# 直接覆盖（注意先备份当前 db！）
cp ~/.chinalaw/backup/chinalaw-YYYYMMDD.db ~/.chinalaw/chinalaw.db
chinalaw status   # 验证
```

私域 norm / pack / fixture 建议放 git repo，跨机器同步用 git pull 而不是
`scp`，方便审计 + 多机一致。

### SOP-6：迁移 / 重置

```bash
# 重置：先保留可恢复备份，再重新初始化
mv ~/.chinalaw/chinalaw.db ~/.chinalaw/chinalaw.db.backup.$(date +%Y%m%d%H%M%S)
chinalaw sync --fixtures
chinalaw sync --applicability

# 迁移到新机器：拷贝 ~/.chinalaw/ 整个目录
rsync -av ~/.chinalaw/ new-machine:~/.chinalaw/
```

注意 schema 版本：`chinalaw status` / `doctor` 默认只读，不会自动 migrate。
先备份，再运行 `chinalaw init`、`chinalaw sync` 或明确的写入命令触发迁移，之后
重新运行 status 验证。当前 schema 版本以 `src/chinalaw/schema.py` 的
`SCHEMA_VERSION` 为准（当前 v11）。

## status 自检解读

`chinalaw status --format json` 关键字段：

| 字段 | 健康值 | 异常处理 |
|------|-------|---------|
| `laws` | `> 0`，且与 `sync --fixtures` 输出的唯一法规数量同量级 | 为 0 → 重跑 `sync --fixtures` |
| `articles` | ≥ laws × 平均条数 | 远低于 → 重跑 sync 或 ensure |
| `norm_packs` | ≥ 0 | 视个人沉淀情况 |
| `applicability_rules` | ≥ 仓库 seed 数 | 远低于 → `sync --applicability` |
| `law_relations` | ≥ 仓库 seed 数 | 同上 |
| `schema_version` | == 仓库 schema.py 中的 SCHEMA_VERSION | 不一致 → migrate / 先备份 |
| `oldest_source_checked_at` | 非空（已有法规时） | 为空 → 检查来源元数据 |
| `oldest_freshness_days` | `<= 90` | 超过 90 天 → 计划性重跑 sync |

详细 doctor 脚本见 [`scripts/doctor.sh`](scripts/doctor.sh)。

## 反模式

- 直接读写 SQLite 或 import `_...` 私有 helper（合法路径全是公开 CLI）
- 复制 db 时不重建索引（应该用 `sync --from-dir` 或 `fetch --to-fixture`
  迁移数据，而不是 `scp` 单文件后期望 fts 索引仍可用 —— 实际上 SQLite +
  FTS5 整库拷贝是 OK 的，但跨机器架构差异需要测试）
- 跳过 `verify-source` 直接发布，结果上游已改版
- 发现 alias 漏写直接改 db，没跑 `rebuild-clean`
- 多项目都用全局 db，把客户 A 的私域 norm 串到客户 B
- backup 只 cp `chinalaw.db` 不备份 `~/.chinalaw/norms/` 源文件，丢了源
  无法 re-ingest
- 备份时 chinalaw 进程还在跑（理论 SQLite 多读单写 OK，实测最好关闭再 cp）

## 与其他 skill 的衔接

- 缺数据 / 上游漂移 → [`chinalaw-fetching`](../chinalaw-fetching/SKILL.md)
- 检索方法 → [`chinalaw-searching`](../chinalaw-searching/SKILL.md)
- 引用核对 → [`chinalaw-checking`](../chinalaw-checking/SKILL.md)

## 相关命令一览

| 命令 | 用途 |
|------|------|
| `chinalaw status` | 数据健康报告 |
| `chinalaw sync --fixtures` | 加载内置 fixture |
| `chinalaw corpus list/show` | 查看推荐规范语料 profile |
| `chinalaw ensure --profile <name>` | 按 profile 本地优先补库（alpha；大型 profile 可能触发官方源限流） |
| `chinalaw sync --applicability` | 加载时间效力 seed |
| `chinalaw sync --source flk_npc --incremental --max-pages N` | 增量同步官方源 |
| `chinalaw sync --from-dir <path>` | 从目录批量入库（自定义 fixture） |
| `chinalaw rebuild-clean [--dry-run] [--law <name>]` | 重建清洗 |
| `chinalaw verify-source <source>` | 上游 smoke |
| `chinalaw norm import / ingest / export` | 私域规范管理 |
| `chinalaw pack import / export` | 规范包管理 |
| `chinalaw laws --level <level> --status <status>` | 浏览法规清单 |
| `chinalaw search <kw> --kind law` | 按关键词找法规候选 |
| `chinalaw history <name>` | 看版本快照 |
