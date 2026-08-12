# chinalaw-cli 详尽重构计划

本计划以 `FULL_AUDIT_20260806_COMPLETION.md` 的 85 条证据观察为输入。目标不是改变
CLI 产品方向，而是把当前散落在 service/fetch/adapter/MCP 的隐含约束变成共享、可测、
失败原子的边界。每一阶段都必须独立可回滚、测试先行，并保持现有 CLI/MCP 公共命令名。

- 制定日期：2026-08-06
- 实施状态：**已完成**
- 实施范围：Phase 0–9；原报告 43 条 confirmed、26 条 low 候选、补审 N1–N16
- 最终基线：`750 passed, 32 skipped, 1695 subtests passed`；unittest `782 tests OK`

## 实施总览

| Phase | 状态 | 落地结果 |
| --- | --- | --- |
| 0 | 完成 | fixture manifest、红测骨架、独立测试状态、32 个可选 skip 的 CI 预算 |
| 1 | 完成 | `contracts.py`、严格条号 grammar、统一 enum/date/article/payload validator、schema v10 修复 |
| 2 | 完成 | TOC/HTML/parser 与缺条修复、文号/零宽字符/刑法层级/仲裁法版本 ID 数据治理 |
| 3 | 完成 | `netio.py`、`resource_limits.py`、HTTPS/host/redirect/size/process policy 与 alias-agent 边界 |
| 4 | 完成 | 页级短事务、水位状态机、metadata-only refresh、SQLite migration/WAL 并发、snapshot 原子账本 |
| 5 | 完成 | fetch 候选评分、canonical ID、只读 lookup、失败零副作用、原子持久化与复杂度拆分 |
| 6 | 完成 | as-of、audit/trace/applicability、结构化 service 错误、引用 grammar 与 import cycle 修复 |
| 7 | 完成 | MCP framing/request/schema/error 三层边界与真实连续 stdio 恢复测试 |
| 8 | 完成 | user-site/CLI/doctor/status/scripts/打包/CI 全矩阵与 pipx notice 修复 |
| 9 | 完成 | schema v11 alias/FTS 索引、formatter footer、节流、文档/skills 守门、McCabe 收紧到 21 |

## 总体不变量

1. 不返回错误法规或错误条文；低置信时显式 not-found/ambiguous。
2. public law 入库前必须通过同一个 canonical contract。
3. 网络目标、重定向、响应大小、附件和外部进程必须受统一 policy 约束。
4. dry-run、预览、失败操作不得修改 DB/fixture；持久化采用原子替换/短事务。
5. 同步崩溃最多损失当前页，已提交页和断点必须可 resume。
6. MCP 中一个坏请求不得终止后续请求；notification 永不产生响应。
7. wheel、sdist、editable、user-site、Windows Unicode 路径必须获得相同内置数据。

## Phase 0：测试骨架与兼容门禁（已完成）

文件：`tests/test_contracts.py`、`tests/test_network_policy.py`、
`tests/test_mcp_stdio.py`、现有 adapter/fetch/core tests、CI。

- 先把所有已复现 bug 写成红测：条款后缀、TOC、HTML、枚举、canonical ID、hash
  metadata、fetch 原子性、SSRF、MCP 连续请求、user-site 数据路径。
- 建立 fixture manifest：仅对宣称“全文”的整数条号法规要求连续与官方条数；允许序言、
  插入条款、decimal exchange rule 和确有断号的材料通过显式例外。
- 保存当前公共 JSON 字段快照；重构新增诊断字段可以兼容增加，不静默删除旧字段。

验收：红测准确复现审计证据；未触碰生产逻辑前现有基线除新增红测外不退化。

## Phase 1：canonical contract 与严格条号 grammar（已完成）

新增：`src/chinalaw/contracts.py`（或 `_contracts.py`）。

修改：`models.py`、`cleaning.py`、`loader.py`、`service.py`、`normsources.py`、
`normpacks.py`、三个非法 level adapter、`formatters.py`、schema migrator。

- `LawStatus` 补 `pending_effective`、`seed`；导出 `LAW_LEVEL_VALUES`、
  `LAW_STATUS_VALUES`。
- `validate_law_payload(payload, require_articles=True)`：
  - root/article 类型；必填非空；别名必须字符串数组；
  - level/status 枚举；ISO date/datetime；HTTPS source URL（本地 source 例外显式参数）；
  - 每个 article 的 number/text/position；number 严格归一；条号与 position 唯一；
  - position 重排为 1..N 或 fail loud，不接受调用者提供的冲突序列。
- `normalize_article_number` 使用 fullmatch grammar：普通条、插入条、decimal；先准确取
  “条”部分，再忽略款/项/目尾缀；未知字符返回空串。
- loader 是最终写入 choke point；所有 adapter 即使绕开 cleaning 也无法污染 DB。
- schema v10 数据修复：`departmental_rule -> department_rule`；必要时校验现存未知枚举并
  记录 warning，不删除用户数据。
- norm pack/reference 和 applicability 日期使用同一基础 validator。

验收：非法 payload 全部在写事务前拒绝；三 adapter 输出合法枚举；旧污染 row 可迁移；
H1/H4/L3/L4/M28/空 reference 等回归通过。

## Phase 2：文本解析与 fixture 数据质量（已完成）

修改：`cleaning.py`、`adapters/_html.py`、court/spp/gov adapter、
`scripts/check-public-fixtures`、fixtures、recommended corpus。

- article detection 在 TOC 判定之前；目录 marker 仅 fullmatch；dot leader/page-number 规则
  保留。
- HTML：先删除 script/style/noscript/template；`br\b` 支持属性；p/li/tr/div/h/td/th
  形成合理分隔；避免相邻条文粘连。
- 为会议纪要/政策文件统一编号项目 parser；非条文化但具规范正文的文书显式用 `正文`；
  public law 空 articles 默认拒绝。
- 连续性函数只产生诊断，不擅自“补条”；manifest 决定哪些 fixture 必须无 gap。
- 用官方文本重抓并恢复数据安全法 21/42、网安法 25；修复 position、part、corpus 条数。
- 清理 239 个零宽字符污染，并补司法解释 document_number（有可靠官方证据才写）。

验收：fixture gate 能在故意删一条时失败；74 fixture 全部通过；缺失三条可由 CLI 查询；
HTML/TOC 最小复现通过。

## Phase 3：统一网络、URL、资源与外部进程 policy（已完成）

新增：`src/chinalaw/netio.py`、`src/chinalaw/resource_limits.py`。

修改：全部 adapter、alias_agent、cleaning/normsources 的 document reader。

- `SourcePolicy`：source id、allowed HTTPS hosts、是否允许同域子域、最大文本/二进制大小、
  redirect 次数、timeout。
- 自定义 redirect handler 在每一跳复验 scheme/host；拒绝 userinfo、IP literal、
  localhost/private/link-local/metadata ranges；禁止 `file/data/ftp/gopher`。
- `read_limited` 同时检查 Content-Length 与流式累计字节；超限抛领域错误。
- DOCX：压缩包总大小、entry 数、单 entry 解压大小、压缩比限制；XML 解析前限额。
- `run_limited`：subprocess timeout、stdout/stderr 上限、稳定错误映射。
- 法院公报改 HTTPS；若官方 HTTPS 不可用则 fail loud，不回落明文。
- adapter 测试使用 injectable opener，不联网验证 allowlist/redirect。

验收：N1/N2/N3 全部关闭；允许官方多 host（如 gov.cn/xzfg.moj.gov.cn）但拒绝跨域跳转；
资源超限和 timeout 不杀主进程。

## Phase 4：同步状态机与 SQLite 并发（已完成）

修改：`sync.py`、`db.py`、`loader.py`、formatters/tests。

- 网络抓取全部移出写事务：先抓一页 payload，验证后用一个短事务写页。
- 每页法规、page checkpoint、stable counter 同事务提交；异常只回滚当前页。
- batch/incremental checkpoint namespace 分离；incremental 保存 window from/to/next_page。
- 只有 `stop_reason=no_rows` 才推进 `last_incremental_to`；早停返回
  `window_exhausted=false` 和 resume token。
- 单部 sync 也先 fetch 后开事务。
- `busy_timeout` 提升并集中配置；migration 使用显式锁/事务，避免 check-then-ALTER 竞争。
- 相同 content hash 时只更新 metadata/source freshness，不重建 articles/FTS/revision。
- 后续性能工作：为 FTS 删除建立普通映射/外部内容表，消除按 UNINDEXED 列全表扫描。

验收：第二页超时后第一页和 checkpoint 保留；并发 writer 不因网络等待持锁；水位复现
不再漏页；metadata-only 更新可见。

## Phase 5：fetch 选择、canonical ID、幂等与原子性（已完成）

修改：`fetch.py`、`identity.py`、adapter query matcher、tests。

- 拆分为 `resolve_candidates -> choose -> fetch -> validate -> plan_persistence -> persist ->
  shape_response`，降低 C901 复杂度。
- 唯一候选仍需评分；精确标题、规范简称、有序包含、issuer/date/status 分层；低分不选。
- canonical ID 查询拿到全部候选后 strict match；0/1/>1 分别处理；不再 LIMIT 1。
- `--article` 在任何持久化前定位；找不到则零副作用。
- fixture 先写同目录临时文件、fsync、`os.replace`；DB 写入单短事务。
- dry-run/to-fixture canonical lookup 只读连接，不 migrate/建库。
- same-hash metadata-only update 与 sync 共用 loader API。

验收：错误候选不再入库；canonical 修订版复现通过；失败不产生文件/row；dry-run DB 字节
不变；force 与正常幂等语义明确。

## Phase 6：时间效力、trace 与 service API 边界（已完成）

修改：`service.py`、`trace.py`、`audit.py`、`applicability.py`。

- as-of revision 无 snapshot 且 hash 等于 current 时，从当前 articles 重建快照，不返回空。
- snapshot JSON 损坏转为结构化诊断，不崩整个查询。
- audit 比较 `as_of` 与 `repealed_at`；废止后日期报 `repealed_before_as_of`。
- trace 同号条文存在时不得输出 deleted；deleted 需要“目标版本无同号且候选低置信”的
  独立证据。`ok` 与 `status` 分离，允许 `status=amended, confidence=low`。
- service 对 invalid date/law-not-found/empty numbers 返回可区分 result/error，不再用单一 None。
- trace re-export 改 lazy wrapper，消除 import cycle。
- 引用 grammar 区分 range 与 inserted article；short citation 使用 Unicode 字母边界。

验收：H7/H8/M1/M2/L1/L6 与两条 audit low candidate 全部回归。

## Phase 7：MCP 协议边界（已完成）

修改：`mcp.py`、`metadata.py`、新增真实 stdio tests。

- `read_frame` 返回 frame 或 framing error；header/body/line 上限；精确读取并检测 EOF。
- request 必须 object、`jsonrpc=2.0`、method string；id 只允许 JSON-RPC 合法标量。
- notification（无 id）无论成功失败都不输出。
- 参数按工具 schema 验证；limit 必须 int 且在范围内；不对 list/dict 做 `str()`。
- 三层异常：协议错误 JSON-RPC error；领域/工具错误 `isError=true`；未知内部错误
  `-32603`/工具内部错误，同时记录到 stderr，继续会话。
- 连续请求测试：坏 JSON、坏 framing、RuntimeError、随后 ping/tools/list 仍成功。

验收：N12 和永久 skip 的 MCP e2e 门禁关闭。

## Phase 8：数据路径、CLI 错误与脚本/打包/CI（已完成）

修改：`datapaths.py`、`cli.py`、`doctor.py`、`notices.py`、scripts、pyproject、CI。

- 数据定位优先 repo/resource，再覆盖 `sysconfig.get_path("data", scheme="posix_user")`、
  userbase 与 wheel `.data` 实际路径；错误中列出搜索位置。
- `init` 要求至少加载一部法规和一条条文；缺 bundled data 为 error，不再 ok:true。
- 文件/网络/sqlite 领域错误统一 CLI envelope；`sync --from-dir` 空目录 fail loud；limit 校验。
- doctor/status 分离 readonly 与 migrate；health check 不静默升级旧库。
- Bash 3.2 空数组使用兼容展开；Windows 优先 `.ps1` shim 或安全生成 UTF-8 cmd，避免
  把绝对路径内联进会发生 `%` 展开的 batch 文本。
- sdist 改 explicit include/排除审计与工作区临时文件；CI 增加 fixture gate、sdist wheel
  安装、user-site、Unicode/% 路径、脚本真实执行、MCP stdio。
- 不覆盖用户已修改的 install/setup 脚本：仅基于现有 diff 做小范围合并并保留其行为。

验收：wheel/sdist/editable/user-site 全矩阵 init+article+corpus+sources 成功；Windows shim
路径测试通过；Bash 3.2 update-local 不崩；CI 与 README 承诺一致。

## Phase 9：格式化、文档、skills 与性能收尾（已完成）

- law markdown 只在 part 变化时输出章节标题；补 repealed_at；full footer 不少于 compact。
- 修复文档断链、命令示例、status 字段、节流环境变量与健康阈值；同步 `.claude` 和
  `.agents` skills。
- derived alias 建持久化/索引，避免 miss 时全表 Python 扫描；拆分 service/fetch 大模块。
- 将 Ruff complexity 阈值按拆分结果逐步从 25 收紧。

验收：文档命令抽样可执行；skill 与 CLI metadata 自动对照；无新增 skip；性能基准记录。

## 实施偏差与追加修复

实施总体遵循原顺序，以下调整来自阶段间复核和最终全量门禁：

1. 原计划只预告 schema v10 数据修复；Phase 9 为 derived alias 与 FTS 删除性能新增
   schema v11：`law_alias_index` 加 `exact/derived` 分层，并为四张 FTS 建 rowid 映射表。
2. `busy_timeout + BEGIN IMMEDIATE` 修复 migration 主竞态后，全量 pytest 又暴露“两个线程
   同时首次打开空库、在 migration 前设置 WAL”的更窄竞态；最终在连接初始化仅对
   `SQLITE_BUSY/LOCKED` 做有界 retry，并连续复跑 50 次。
3. 审计闭环复核发现原实现总结遗漏 snapshot JSONL 并发、真实 User-Agent/联系方式、
   pipx notice、四套 skill、刑法层级与仲裁法版本 ID；这些均在封版前补齐并加入回归。
4. 公开仓库仍有 32 个刻意不随包分发的 eval/export/demo 可选测试；MCP e2e 已改为真实
   内建 stdio 测试，CI 另设 `skip <= 32` 预算，避免死 skip 继续增长。

## 最终发布门禁

1. `pytest -q --junitxml=...`：`750 passed, 32 skipped, 1695 subtests passed`；
   skip budget `32/32`。
2. `python -m unittest discover -q`：`Ran 782 tests`，`OK (skipped=32)`。
3. `ruff check src tests`：通过。
4. `scripts/check-public-fixtures`：74 fixtures 通过；init 加载 15,268 条。
5. `python -m build`：wheel + sdist 通过。
6. wheel、sdist、user-site：三种隔离安装均完成 init/article 143/corpus/sources/doctor。
7. Bash 3.2、Windows shim/Unicode/% 路径、MCP 连续恢复：定向 50 项通过。
8. 两份 maintaining doctor 脚本：通过；applicability/freshness 只产生 warning。
9. 包内容：sdist 含 manifest 且排除 audit/refactor/.agents；wheel 含 74 fixtures。
10. `git diff --check`：通过；未 stage、未 commit，保留用户原有工作区改动。

全部 high finding 均有生产修复和回归证据；Phase 0–9 及发布门禁全部完成。
