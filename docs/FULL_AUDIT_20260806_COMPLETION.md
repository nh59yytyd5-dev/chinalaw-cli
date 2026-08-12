# chinalaw-cli 全面审计与重构完成报告

- 封版日期：2026-08-06
- 审计分支：`review/full-audit-20260726`
- 基点 HEAD：`925a8991ef2cb84cabe6a0f9f7488472415e25ce`
- 审计前离线基线：`648 passed, 33 skipped, 23 subtests passed`
- 最终离线基线：`750 passed, 32 skipped, 1695 subtests passed`
- 实施状态：Phase 0–9 全部完成；85 条证据观察均已修复或通过显式兼容/门禁核销
- 审计范围：`src/chinalaw/`、15 个 adapter、`scripts/`、`.github/workflows/`、
  打包安装矩阵、74 个内置 fixture、文档与 skills

本补充把原报告声明未覆盖的 security、fetchpipe、adapters、textproc、MCP、
packaging/CI 全部补齐，并处理原 26 条 low 候选。原报告保留审计过程和 43 条已确认
finding 的完整证据；本文件是最终口径。

## 一、最终统计

| 来源 | High | Medium | Low | 合计 |
| --- | ---: | ---: | ---: | ---: |
| 原报告已确认 | 8 | 29 | 6 | 43 |
| 原 26 条 low 候选复核 | 0 | 1 | 25 | 26 |
| 本轮补审新增 | 8 | 8 | 0 | 16 |
| **最终证据观察** | **16** | **38** | **31** | **85** |

原候选中 `pip install --user` 的 shared-data 定位失效由 low 升为 medium：端到端安装后
`init` 加载 0 部法规、`corpus list` 和 `sources list` 均无法找到随包数据；其余 25 条
维持 low。为避免重复修补，实施阶段将 85 条观察合并成根因级工作包，而不是逐条散改。

补审编号固定为 `N1`–`N16`。早期工作草稿曾把“fixture 门禁无法发现缺条”另列为
`N17`；封版时已将数据损坏根因并入 `N4/N5`，CI/发布门禁缺口并入 `N16`，不再作为
独立 finding，故最终统计仍为 85。

## 二、本轮新增 High（8 条）

### [N1] 证券规则 direct-id 允许 SSRF 与 `file://` 本地文件读取

- 位置：`src/chinalaw/adapters/securities_rules.py:391-394`，
  `src/chinalaw/fetch.py:238-257`
- 根因：`_detail_url` 只要看到 `://` 就原样返回用户通过 `--prefer-id` 传入的值。
- 实证：已实际请求 `http://127.0.0.1:<port>/private/rule.shtml`；
  `default_adapter.fetch_detail("file:///etc/hosts")` 成功读取本机文件，
  `file:///etc/passwd` 同样可读。
- 影响：本地文件泄露、内网探测、云元数据访问；重定向后也没有再次验证目标。
- 修复边界：统一 URL policy；只允许 HTTPS；按 source 配 host allowlist；拒绝 userinfo、
  非标准 scheme、IP literal/private/loopback；每次重定向后复验；附件与正文设置上限。

### [N4] TOC 识别把正文中含“目录”的整条条文静默删除

- 位置：`src/chinalaw/cleaning.py:745-759`
- 根因：`TOC_MARKER_RE = re.compile(r"目\s*录")` 配合 `search()`，任何含“目录”的
  行都会在条文识别之前被跳过。
- 实证：`20,21,22,41,42,43 -> 20,22,41,43`；网安法 `24,25,26 -> 24,26`。
- 影响：完整解释数据安全法第 21/42 条、网络安全法第 25 条缺失；74 个 fixture 的
  15,265 条文本中“目录”出现次数为 0，是系统性误删信号。
- 修复边界：仅把独立标题形态 `^目\s*录$` 视为 marker；条文识别优先于 TOC 噪声；
  对整数条号增加连续性诊断。

### [N5] canonical payload 只有“字段存在”检查，非法数据可贯穿入库

- 位置：`src/chinalaw/cleaning.py:106-138,539-567`，`src/chinalaw/loader.py:170-278`
- 实证：`level/status="invented"`、空 source URL/name、非日期字符串、整数
  `source_checked_at`、重复条号、倒序 position 全被接受；已有
  `number="第十条第三款"` 不会重新归一。
- 影响：枚举分裂、重复主键异常、时间效力排序错误和无法检索的条号都可由任一 adapter
  写入；`departmental_rule` 已证明不是理论风险。
- 修复边界：建立唯一 `validate_law_payload` choke point，所有 source_kind、loader、
  fetch/sync/rebuild 共用；验证类型、非空、枚举、ISO 日期、URL、唯一条号、position。

### [N7] canonical ID 查询 `LIMIT 1` 后才做 strict 校验，会漏掉正确修订版

- 位置：`src/chinalaw/fetch.py:878-891`
- 实证：DB 有 2020 `old-stable-id` 与 2024 `matching-stable-id`，incoming 为 2024；
  SQL 先取旧版，strict 拒绝后不继续扫描，最终 `resolved=None`。
- 影响：保留 upstream raw id 并生成重复法律 row，破坏 stable-id 与版本关系。
- 修复边界：SQL 返回全部候选或把 source/date 条件下推；逐个 strict 匹配并检测歧义。

### [N8] 相同正文 hash 会冻结状态、废止日与核查时间

- 位置：`src/chinalaw/fetch.py:511-524`，同类逻辑见 `src/chinalaw/sync.py:330-334`
- 实证：第二次 payload 从 `current` 改为 `repealed`，核查时间从 2025 改为 2026，
  DB 仍保留旧值。
- 影响：正文不变的状态变更永远无法刷新；`gov_xzfgk` related versions 更新时旧版
  仍可能显示 `current`；freshness 也被冻结。
- 修复边界：把“正文是否变化”和“元数据是否变化”分开；相同 content hash 仍 upsert
  metadata/source sync meta，但不重复替换 articles/revision 正文。

### [N10] 候选选择可把唯一但完全无关的结果当作目标法规

- 位置：`src/chinalaw/fetch.py:896-945`，
  `src/chinalaw/adapters/securities_rules.py:360-367`
- 实证：`_choose_best([完全无关法规], "目标法律", None)` 直接返回该候选；证券匹配器
  对 `管理办法`/`办法管理`、`法规`/`法法法` 均返回真，纯 ASCII query 因空 `all()`
  甚至匹配任意中文标题。
- 影响：fetch 可能下载并入库另一部规则，这是错误答案而非普通 not-found。
- 修复边界：唯一候选也必须达到明确标题相似度/包含阈值；字符多重集逻辑改为规范化后
  有序匹配或可解释评分；低置信一律 ambiguous/not-found。

### [N11] adapter 将未知状态升级为 current，且部分源允许零条文 payload

- 位置：court/spp/csrc/nfra/securities 多处 `status="current"`；
  `spp_gov_cn.py:776-815`、`court_gongbao.py:680-716`、`gov_xzfgk.py:590-649`
- 实证：最高检指导案例、法院公报会议纪要、行政法规库模拟详情均可生成
  `articles=[]`；现有测试甚至断言指导案例条数 `>= 0`。
- 影响：来源未提供效力状态却被表达为现行有效；fetch 成功但 article 永远查不到。
- 修复边界：来源没有明确证据时用 `unknown`；只有 current-only 列表语义的源才可在
  候选层标注 current；canonical public law 默认要求非空 articles，非条文化文书显式
  转成 `正文` 或编号项目。

### [N12] MCP 缺少 JSON-RPC、framing、类型、资源和异常隔离边界

- 位置：`src/chinalaw/mcp.py:26-221`
- 实证：malformed JSON、JSON scalar/list、非 `ValueError` 工具异常均杀死会话；
  `jsonrpc="1.0"` 被接受；普通 notification 出错会返回 `id:null`；负 Content-Length
  被当作 `read(-1)` 接受，短 body 也被解析；line framing 无大小上限；list/dict 参数
  被 `str()` 静默转型。
- 影响：任一坏请求可终止 agent 的常驻连接；同时存在无限内存/阻塞风险和协议错误响应。
- 修复边界：framing parser 与 request validator 分层；设置 header/body/line 上限；
  parse/invalid-request/method/params/internal error 分码；notification 永不响应；单请求
  异常不得终止后续请求；参数按 schema 严格校验。

## 三、本轮新增 Medium（8 条）

### [N2] `court_gongbao` 使用明文 HTTP，且与 source catalog 的 HTTPS 登记不一致

`DEFAULT_BASE_URL` 是 `http://gongbao.court.gov.cn`，但 `data/source_coverage.json`
登记为 HTTPS。正文、标题与文号可被链路篡改；审计原“全部 TLS”结论据此撤回。

### [N3] 所有网络/附件/DOCX/PDF 读取缺少统一大小上限，外部进程无 timeout

AST 扫描确认 15 处无界 `resp.read()`；DOCX zip entry 无解压上限；`pdftotext`、
`textutil`、`antiword` 五处 `subprocess.run` 无 timeout。恶意或异常上游可耗尽内存、磁盘
或永久挂起进程。

### [N6] 共用 HTML 清洗器保留 script/style，`br` 属性和表格单元格会粘连

复现输出分别为脚本/样式进入正文、`<br class=...>` 不换行、`td` 之间无分隔。
影响 court、spp、csrc、nfra、证券等多个 adapter，并会改变条文切分与 source hash。

### [N9] fetch 的失败与只读动作不具备无副作用语义

`--article` 在 fixture/DB 写入后才定位条文；找不到条文时虽然抛
`FetchNotFoundError`，文件或 DB row 已存在。`--dry-run`/`--to-fixture` 对已有空库或
旧库调用 `migrate()`，实测 dry-run 将空文件扩展为 39 张表、schema v9。应先完成全部
校验再原子落盘；只读 canonical lookup 使用 SQLite `mode=ro` 且禁止迁移。

### [N13] alias agent 对畸形环境变量和响应结构抛裸异常

非法 `CHINALAW_ALIAS_AGENT_MAX/TIMEOUT` 抛 `ValueError`；JSON 根为 list、choices 为
string、choices 元素为 int 均抛 `AttributeError`。这些应归入可恢复的
`invalid_config/invalid_response`，且响应体需要大小上限。

### [N14] user-site 数据缺失后 `init` 仍可返回 `ok:true`

user-site 安装下 fixture 目录找不到，`load_fixtures` 返回 0；doctor 默认只把空库记为
warning，非 strict 时仍 `ok:true`。初始化成功信号与实际可用性相反。

### [N15] 平台脚本存在可复现的 Bash 3.2 与 Windows 路径损坏

`scripts/update-local` 在 Bash 3.2 + `set -u` 下展开空数组直接 exit 127；Windows
`.cmd` shim 用 ASCII 写入，中文路径损坏，路径中的 `%` 还会被 cmd 环境变量展开。

### [N16] 打包/CI 矩阵遗漏关键发布形态，sdist broad include 可携带未跟踪文档

sdist 已把未跟踪的两份审计文件打包；CI 未运行 fixture 门禁、user-site、sdist 安装、
Unicode/`%` Windows 路径、update-local/setup-agent 真实执行、MCP 连续 stdio 恢复。
构建成功不等于发布包契约被验证。

## 四、原 26 条候选的封版结论

26 条候选均确认真实；除 user-site shared-data 失效升级为 medium 外，其余维持 low。
其中以下候选在重构阶段并入更高层根因，不单独散修：

- `search(kind/in_laws/in_part)`、负 `limit`：并入统一参数契约。
- derived alias 全表扫描、FTS UNINDEXED 删除：并入索引与查询性能阶段。
- revision 日期回退为同步当天：并入版本元数据契约。
- 安装脚本只做子串测试：并入 N15/N16 的真实执行矩阵。
- notice 的 schema dead code、pip/wheel wrapper 误报：并入只读状态与安装形态识别。
- short citation 中文前缀、范围引用与插入条款冲突：并入引用 grammar 重构。
- 空 reference pack：并入统一 norm payload validator。

## 五、审计封版结论

本次审计没有发现 SQL 字符串拼接注入或 TLS 证书校验被显式关闭；但不能据此称网络层
安全：SSRF/本地文件读取、明文 HTTP、无界读取和重定向后无 host policy 是更直接的
攻击面。系统最需要的不是更多零散 `if`，而是五个共享边界：

1. 严格 canonical payload validator；
2. 统一 URL/resource/process policy；
3. 事务与水位状态机；
4. fetch 的选择、幂等与原子落盘；
5. MCP framing/request/error boundary。

实施顺序、逐文件验收标准和实际完成状态见 `docs/REFACTOR_PLAN_20260806.md`。

## 六、Phase 0–9 核销矩阵

原报告中的 confirmed finding 按顺序编号为 `H1`–`H8`、`M1`–`M29`、`L1`–`L6`；
原 26 条 low 候选在本表记为 `C1`–`C26`。同一 finding 只在主责任阶段列一次。

| Phase | 状态 | 核销 finding | 主要实现与证据 |
| --- | --- | --- | --- |
| 0 测试骨架 | 完成 | M14；C11 | 新增 public fixture manifest、fixture 破坏性反例、独立测试 DB；保留 32 个明确可选资产 skip，并在 CI 设置上限 |
| 1 canonical contract | 完成 | H1、H4；M25、M28；L2–L4；C14、C26；N5、N11 | `contracts.py`、统一 payload/date/enum/article validator、严格条号 grammar、schema v10 数据修复、norm/pack 校验 |
| 2 文本与 fixture | 完成 | H5、H6；M24、M26；C19–C21；N4、N6 | TOC/HTML/parser 修复；恢复缺条和文号；清除零宽字符；补刑法“第一编 总则”层级；仲裁法 2025 ID 与 legacy alias |
| 3 网络与资源 policy | 完成 | M13；N1–N3、N13 | `netio.py`、`resource_limits.py`、HTTPS/host allowlist/重定向复验、响应与压缩包上限、subprocess timeout、alias-agent 边界 |
| 4 同步、并发与台账 | 完成 | H2、H3；M3、M5、M9、M11；C4、C6；N8 | 页级短事务与断点状态机、metadata-only refresh、30s busy timeout、migration 串行化、空库 WAL lock retry、snapshot 跨进程锁与截断尾隔离 |
| 5 fetch 原子性 | 完成 | N7、N9、N10 | 候选评分、全候选 canonical strict match、只读 lookup、先验证后持久化、fixture 原子替换、函数拆分 |
| 6 service/时间效力 | 完成 | H7、H8；M1、M2、M10、M27；L1、L6；C1、C2、C22、C23 | as-of snapshot 恢复、结构化错误、废止时点审计、trace 状态修正、引用 grammar、lazy trace wrapper、applicability 闭环 |
| 7 MCP | 完成 | L5；N12 | 有界 header/line framing、JSON-RPC/参数校验、notification 无响应、异常隔离、真实 stdio 连续恢复测试；可选测试 skip 预算化 |
| 8 安装/CLI/CI | 完成 | M4、M6–M8、M19、M29；C7–C10、C24、C25；N14–N16 | user-site 数据定位、init fail-loud、统一 CLI envelope、status/doctor 只读、Bash 3.2/Windows Unicode/%、wheel/sdist/user-site CI 矩阵、pipx notice 修复 |
| 9 输出/索引/文档 | 完成 | M12、M15–M18、M20–M23；C3、C5、C12、C13、C15–C18 | schema v11 alias/FTS rowid 索引、共享 footer、repealed_at、真实 UA/来源清单、skill 命令契约、文档断链门禁、节流实装、McCabe 21 |

## 七、最终发布门禁

| 门禁 | 最终结果 |
| --- | --- |
| pytest | `750 passed, 32 skipped, 1695 subtests passed`；JUnit skip budget `32/32` |
| unittest | `Ran 782 tests`，`OK (skipped=32)` |
| Ruff / whitespace | `ruff check src tests` 与 `git diff --check` 通过 |
| fixture | `public fixture check passed: 74 fixtures`；初始化为 74 部、15,268 条 |
| 构建与安装 | wheel、sdist、user-site 三种隔离安装均完成 init/article/corpus/sources/doctor 烟测 |
| 包内容 | sdist 含 public fixture manifest，不含 `FULL_AUDIT_*`、`REFACTOR_PLAN_*`、`.agents`；wheel 含 74 fixtures |
| 协议与平台 | MCP 连续恢复、Bash 3.2、Windows shim/Unicode/% 路径共 50 项通过 |
| skills | `.claude` / `.agents` 相关副本一致；两份 maintaining doctor 脚本通过，90 天仅 warning |
| 并发回归 | 空库并发 migration 连续 50 次通过；snapshot 40 路并发 evidence ID 唯一且 JSONL 可解析 |

32 个 skip 全部属于明确未随公开仓库分发的可选 eval/export/demo 资产；核心 MCP stdio
已不再依赖可选脚本，并有真实子进程恢复测试。CI 允许 skip 数只减不增，新增第 33 个
skip 会直接失败。
