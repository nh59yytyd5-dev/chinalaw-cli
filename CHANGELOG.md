# Changelog

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 格式，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

## [0.2.1] — 2026-06-30

### 修复

- `resolve` / `article <title>` 在同名 `current` 法规并存时，优先选择较新的
  发布 / 施行版本，而不是被旧版本的条文数量压过；同时在 resolve payload
  中保留 `document_number`，便于确认命中的具体版本。
- Windows 测试和安装路径更稳：CLI 入口统一配置 UTF-8 stdio，损坏 SQLite
  初始化失败时会关闭连接，Windows 下不再断言 POSIX executable bit。
- `audit file` 短引解析支持多字短引 `九民§28`，避免误抽为 `民§28` 并错误指向
  《民法典》。

### 新增

- 新增 `nfra_gov_cn` adapter 和测试，支持有稳定 `docId` 或已核验线索的国家金融
  监督管理总局 / 原银保监会 bounded `fetch` / `discover` / `verify-source`；
  catalog 中仍标为 `develop_only`，不作为公开稳定推荐来源承诺。
- `gov_xzfgk` 增加少量已核验 `www.gov.cn/zhengce/.../content_*.htm`
  国家规章库静态页面线索，清洗为 `departmental_rule`，并在 source catalog /
  CONTRACT 中说明不是全量国家规章库 adapter。

### 发行硬化

- 统一所有注册 adapter 的 User-Agent 到包级 `USER_AGENT_TOKEN`，随
  `__version__` 自动更新，避免 release 后仍发送旧版本 UA。
- 恢复跨平台 `install-smoke` CI，并保留 `scripts/install-local` /
  `scripts/install-local.ps1` 在 venv 或 pip 不完整环境下的 PYTHONPATH fallback。
- 新增 release metadata 测试，校验 `pyproject.toml`、`chinalaw.__version__`、
  MCP server info 和 adapter UA 一致。

### 重构（模块边界与防屎山机制，2026-06-19，ADR-0009）

- 把 `service.py` 的 trace 子系统（`_TRACE_*` 常量 + 13 个 `_trace_*` helper +
  公开 `trace_article_as_of`，约 555 行）拆分到独立模块 `chinalaw.trace`；
  `service.py` 3380→2831 行，末尾 re-export `trace_article_as_of` 保持
  `chinalaw.service.trace_article_as_of` 向后兼容（cli 调用与测试 `@patch` 依赖此路径）。
- 把 `cli.py` 内联的 10 个 `_*_to_markdown` 渲染函数及 `_LEVEL_LABELS`/`_VIA_LABELS`
  常量收口到 `formatters.py`，改为公开 `xxx_to_markdown`；cli 调用点统一走
  `formatters.xxx_to_markdown`。`cli.py` 2759→2385 行，`formatters.py` 1578→1960 行。
- ruff 新增圈复杂度 gate：`select` 加 `C901`，`max-complexity = 25`；当前 5 个存量
  热点（`infer_source_id`/`_normalize_dependencies`/`fetch_law`/`analyze`/`aggregate`）
  逐个 `# noqa: C901` 标记为待拆分技术债。
- 新增 `docs/decisions/ADR-0009-module-boundaries.md`，固化模块边界纪律、CLI 层职责、
  复杂度 gate 理由与渐进收紧（ratchet）路线。
- 纯搬移、零行为变更：675 tests 全绿、ruff 全绿、公开 API 39 项不变。

## [0.2.0] — 2026-05-24

### 新增（public v0.2 source coverage catalog，2026-05-24，issue #105）

- 新增 `data/source_coverage.json` 作为 source 覆盖范围、命令能力、成熟度和
  公开 v0.2 迁移状态的机器可读事实表。
- 新增 `chinalaw sources list|show`，支持 `--implemented-only`、`--class`、
  `--public-v2` 与 JSON / Markdown 输出；`metadata.py`、`CONTRACT.md`、
  `DATA_INDEX.md`、`README.md`、`PUBLIC_README.md`、`EXAMPLES.md` 和
  `MVP_PLAN.md` 同步声明该契约。
- `pyproject.toml` 将 `data/source_coverage.json` 纳入 wheel/sdist 包数据。
- `gov_xzfgk` 在 source coverage 中升级为 `public_v2=include`：公开 v0.2 纳入
  `fetch/discover` 预览路径；2026-05-24 直连 `verify-source` smoke 已验证
  `行政法规制定程序条例` 可清洗 48 条并定位第一条；`sync --source gov_xzfgk`
  仍不承诺。
- source coverage catalog 测试现在校验 `ADAPTER_REGISTRY`、`FETCH_SOURCES`、
  `DISCOVER_SOURCES`、`VERIFIABLE_SOURCES`、`SYNC_SOURCES`、
  `STATUS_FILTER_SUPPORTED` 与 `CURRENT_ONLY_STATUS_SOURCES` 全部和 catalog
  一致。
- 修复 #105：catalog validator 不再用一个混合枚举校验所有 command；
  `probe` / `verify_source` / `fetch` / `discover` / `sync` 只允许
  `supported|unsupported`，`status_filter` 只允许
  `full|current_only|unsupported`。
- 修复公开仓库 #2：`CONTRACT.md` 不再把协议标题钉死为 v0.1，并把当前
  SQLite schema 文档从过期的 v7 更新为 v9；`CONTRIBUTING.md` 不再硬编码
  `schema_version=6` 示例。

### 新增（agent platform 稳定性，2026-05-20，issues #81/#82/#83/#84/#86/#88/#89）

- 新增 `chinalaw doctor`：本地检查 PATH wrapper、默认 DB、schema、fixtures、
  freshness、seed/stub、用户级 skills、MCP wrapper；默认不联网，`--strict`
  可把 warning 视为失败。
- 新增 `chinalaw schema`：输出 agent-facing CLI / MCP 机器可读契约，包括
  risk、side effect、network、退出码、常见误用和全局 flag。
- 新增 JSON `_notice`：在不改变主结果和退出码的前提下提示本机 wrapper /
  skills / MCP / DB freshness / seed-stub 状态；可用 `--no-notice` 或
  `CHINALAW_NO_NOTICE=1` 关闭。
- 轻量 MCP 的 `tools/list` 改为复用 `metadata.py`，去掉 MCP tool schema
  重复定义；当前 tools/list 约 2400 字符，低于 6000 字符预算。
- 新增 `scripts/setup-agent` 并增强 `scripts/update-local`，统一全局 wrapper、
  skills、fixture sync 和 doctor 检查路径。
- 补 source-text-as-data 安全边界：来源文本是数据不是 agent 指令；fixture /
  DB 持久化不得保存 transient runtime diagnostics。
- 新增 `cite-check <file>` shortcut，显式展开到底层 `audit file` / `audit grounding`，
  用于高频引用核对但不隐藏证据链。
- OpenCode eval runner 增加 `EVAL_SURFACE=bare|cli|skills|mcp`，支持同题横向比较
  CLI、skill、MCP/fallback 接入面。

### 新增（csrc_gov_cn 证券监管源，2026-05-17，issue #77）

- `fetch` / `discover` / `probe` / `verify-source` 接入 `csrc_gov_cn`，覆盖证监会官网公开的证监会令与部门规章。
- 新增 `src/chinalaw/adapters/csrc_gov_cn.py`：使用 `/guestweb4/s` 站内搜索，详情页支持 `.content-body` / `.detail-news` / `.Custom_UnionStyle`，并在新版证监会令只有命令摘要时选择标题匹配的正文 PDF 附件，用 `pdftotext -raw` 抽取后进入统一 cleaning。
- `fetch --source csrc_gov_cn --status current` 允许作为当前公开页过滤；`repealed` / `amended` / `pending_effective` 仍 fail loud，避免 agent 误以为证监会官网提供历史状态筛选。
- `document_numbers` / 本地 fetch hint / adapter registry / source verify 契约同步支持 `csrc_gov_cn` 的 `detail_id` 与 `source_name=www.csrc.gov.cn`。
- 修复 PDF 清洗一般性边界：跨行出现“本办法第六十九条规定”时，不把续行的 `第六十九条规定...` 误切成新条文，避免重复条号导致入库唯一约束失败。
- 已用默认本地库实测入库 6 部证券监管核心规章：上市公司信息披露管理办法、上市公司收购管理办法、证券期货投资者适当性管理办法、证券发行与承销管理办法、首次公开发行股票注册管理办法、公司债券发行与交易管理办法。

### 修复（postPRB1 eval issue batch，2026-05-07）

- `service.py`：同名 / 同 alias 法规解析排序改为一般性规则：精确 `id`
  仍最高优先；同层候选优先非 `seed`、条文数更多、状态更新、日期更新的 row。
  `diagnose_article_miss()` 现在在 `article_null` / `law_seed` / `law_stub`
  中返回 `sibling_laws` 与 `suggested_sibling_articles`，避免 agent 在同名
  seed 与完整版本之间兜圈。
- `data/fixtures/{criminal_law,labor_law}.json`：历史遗留 5 条样例数据从
  `status=current` 改为 `status=seed`；`get_law` / `status` / formatter /
  `ensure` 均识别 seed，不能把它当作可跳过 fetch 的完整现行法。
- `.claude/skills`：补合法 flag 速查和 DON'T 反例；修掉
  `laws --query` 错例；说明 `--inline` / `--bare` / `--compact` 仅
  `--format md` 生效；补同名 row 的 `sibling_laws[].id` 兜底路径。
- `scripts/eval/analyze.py`：把 stream result 的 `error_max_turns` /
  `terminal_reason=max_turns` 归因为 `max_turns`，不再混作 `api_error`；
  同时扩展裸引用正则噪声过滤，避免把"现在尝试获取刑法"等过渡句当作法名。
- `scripts/host-eval.sh` / `aggregate.py`：评测 run 同时输出
  `db-state-prewarm.json` 与 `db-state-final.json`，报告中标明两者，避免
  误把 prewarm 状态看成 batch 终态。

### 修复（cli.py discover handler 加 structured error envelope，2026-05-05，PR-B.1 / codex P2）

- `src/chinalaw/cli.py`：`_handle_discover` 的 except tuple 从仅 `ValueError`
  扩到 `(ValueError, URLError, OSError, TimeoutError, json.JSONDecodeError)`，
  对齐 `_handle_fetch` 的 `law_fetch_error` envelope 契约（schema：
  `{"kind": "law_discover_error", "error": <class>, "message": <str>}`，
  退出码 2）。新增 `import json` / `from urllib.error import URLError`。
- 新接住的异常族（来自 `discover_laws()` → `adapter.search_list()` →
  `_request_json()` → `urlopen()` 调用栈）：
  - `URLError` —— DNS / 连接拒绝 / HTTP 5xx（含 `HTTPError` 子类一刀覆盖）
  - `OSError` —— socket / connection 族（含 `ConnectionError` /
    `ConnectionResetError`）
  - `TimeoutError` —— 上游超时（Python 3.10+ 后与 `socket.timeout` 别名等价）
  - `json.JSONDecodeError` —— defense in depth（adapter 当前 wrap 成
    `ValueError`，列入仅 future-proof）
- 编程错误（`AttributeError` / `KeyError` / `TypeError` 等）**不接**——透传，
  与 PR5c（`fetch.py` 5 处 `except Exception` 收窄）/ PR6（`verify_source` 4
  处收窄）/ PR-A 系列窄 except 立场严格一致。设计意图：让 prod 第一次出现
  编程 bug 时立刻 traceback 被发现，不被 envelope 静默退化为 "discover 失败"。
- 修复路径：codex 在 PR #53 inline at `cli.py:1304` 留下 P2 评论，指出
  discover 当前只接 `ValueError`，transport / runtime 失败会冒原生 traceback，
  破坏 JSON envelope 自动化契约（agent 解析 stdout 期待 `law_discover_error`
  payload，得到 multi-line traceback）。本 PR 仅扩 except tuple，不改业务层
  / adapter / envelope schema。
- 新增 `docs/CLI_DISCOVER_ERROR_ENVELOPE_SPEC.md`（§0 立场 / §1 现状含 grep
  锚点 + 三处 envelope 引用 / §2 决策含拒绝方案 A2（catch all Exception）
  + B（造 `DiscoverError` 业务基类）+ C（不 catch 让 traceback 冒）+ 同层
  不变量决策方向 X / 拒绝方向 Y / §3 实施 / §4 风险 / §5 不在范围 / §6 验收）。
- `docs/CONTRACT.md` 同步 `discover` 错误 envelope：`error` 不再写死
  `ValueError`，而是 `<class>`，可表达上游传输 / 解析错误。
- 新增 `tests/test_cli_discover_error_envelope.py`（12 用例 / 4 类）：
  - `DiscoverHandlerTransportErrorEnvelopeTests` × 5：TimeoutError / URLError
    / HTTPError / OSError(ConnectionError) / JSONDecodeError 路径全部命中
    `law_discover_error` envelope + 退 2。
  - `DiscoverHandlerValueErrorRegressionTests` × 2：业务层 ValueError 仍
    命中 envelope；argparse 层 `--status invalid` 仍 SystemExit(2)。
  - `DiscoverHandlerProgrammingErrorPropagationTests` × 3：AttributeError /
    KeyError / TypeError 透传守门。
  - `DiscoverEnvelopeSchemaSymmetryTests` × 2：envelope schema 三字段与
    `law_fetch_error` 形态对称。
- 不在范围（详见 spec §5）：
  - 不动 `discover_laws()` 业务层签名 / 返回值（envelope 在 CLI 层组装是
    设计决策；spec §2 方案 B 拒绝理由）。
  - 不动 `_handle_fetch`（fetch envelope 已齐全，本 PR 范围严格收口）。
  - 不动 `_handle_ensure`（同款问题如有，单独 PR）。
  - 不引入网络重试 / backoff（属调用方 agent skill 职责）。
  - 不区分 transient vs permanent 故障（细分 exit code 需要 `DiscoverError`
    子类分级，本 PR 不做）。

### 新增（fetch / discover 暴露 --status flag，2026-05-05，applicability 闭环 PR-B）

- `src/chinalaw/cli.py`：
  - `fetch` 子命令新增 `--status {repealed,amended,current,pending_effective}`
    flag。CLI 接受语义 keyword（不暴露 flk 内部 `sxx` 编码），由
    `chinalaw.sources.status_to_sxx()` 映射后透传到
    `flk_npc.search_list(sxx=[...])`。
  - 新增 `discover` 子命令（alpha）：按 status / 关键词批量列出候选法规
    （不下载、不入库），用作 fetch 的探测前哨。形态：
    `chinalaw discover [--source flk_npc] [--query Q] [--status STATUS] [--limit N]`。
- `src/chinalaw/sources.py`：新增三个对外暴露的 symbol：
  - `STATUS_TO_SXX: dict[str, int]` —— 由 `cleaning.SXX_TO_STATUS` dict
    comprehension 反向派生，单点维护，未来 flk 加新 sxx 值时只改
    `cleaning.SXX_TO_STATUS` 一处，正反两路自动同步。
  - `STATUS_FILTER_SUPPORTED: frozenset[str]` —— 当前仅含 `flk_npc`。三源
    对照矩阵详见 `docs/CLI_STATUS_FLAG_SPEC.md` §1.1：仅 flk_npc 站点本身有
    四态语义；court_gongbao / spp_gov_cn 站点无 status 维度。
  - `status_to_sxx(status: str) -> int` —— string→int 翻译；非合法 keyword
    抛 `ValueError` 列出合法值。
- `src/chinalaw/fetch.py`：`fetch_law()` 函数加 `status: str | None = None`
  kwarg。非 flk 源传入 `status` 时抛 `ValueError` fail loud（
  `docs/CLI_STATUS_FLAG_SPEC.md` §2 方向 X）；error message 列出 supported
  sources 让 agent 自学边界。flk_npc 路径把 `sxx=[<int>]` 通过
  `**search_kwargs` 透传到 adapter（adapter 签名不变，仅复用既有
  `**overrides` 通道）。传入 `status` 时禁用隐式本地 alias / 文号 hint，
  避免本地现行同名版本短路远程历史版本过滤。CLI handler 加
  `except ValueError` 捕获并 emit
  `law_fetch_error` 结构化 payload + 退 2。
- 新增 `src/chinalaw/discover.py`：`discover_laws()` 业务函数 +
  `DISCOVER_SOURCES = ("flk_npc",)` 常量。当前仅支持 flk_npc（court /
  spp 站点没有"按 status 批量列出"的服务器端语义；spec §2 拒绝方向 Z 论证
  为什么不做客户端过滤）。discover 统一传 `order="gbrq", sort="DESC"`，
  空 query 时按发布日期倒序列候选。
- 新增 `docs/CLI_STATUS_FLAG_SPEC.md`（§0 立场 / §1 现状含三源对照矩阵 +
  grep 锚点 / §2 决策含拒绝方案 A2（int sxx）+ B（强制 adapter 抽象）+ C
  （query 层后过滤）+ 同层不变量决策方向 X / 拒绝方向 Y（noop 静默）+ Z
  （客户端拉全量过滤）/ §3 实施 / §4 风险表 / §5 不在范围 / §6 验收 +
  附录 A 实施 checklist + 附录 B 不动相邻设计 + 附录 C 历史 PR 关系图）。
- 行为差异：
  - `chinalaw fetch <name> --source flk_npc --status repealed`：用 sxx=1
    过滤搜索候选，命中废止法（如 1999 合同法 / 2007 物权法 / 2009 侵权
    责任法）。
  - `chinalaw fetch <name> --source court_gongbao --status repealed`：抛
    ValueError、退 2、message 含 `supported sources: ['flk_npc']`。
  - `chinalaw discover --status repealed --query 合同法`：列出当前 flk
    所有 sxx=1 + 标题含"合同法"的候选（不下载、不入库）。
  - 不传 `--status` 时 `fetch` / 既有调用方零行为差异（search_kwargs 不
    含 sxx 键，与修前等价）。
- 不在范围（详见 spec §5）：
  - court_gongbao / spp_gov_cn 客户端 status 启发式（"按发布日期 +
    颁布机关推断已废止"）—— 单独 PR；本 PR fail loud 立场不改。
  - `repealed_at` 日期推断（从 `applicability_v0_2.json` `superseded_by`
    反推或抓 flk "修改、废止的决定"分类）—— 属 PR-C 范围。
  - discover `--since` / `--until` gbrq 时间窗口（flk_npc.list_laws 底层
    已支持，可单独跟进）。
  - `fetch --status` 与 `--prefer-bbbs` 组合（直接候选路径不走过滤；
    spec §3.3 + §5 第 4 项注释）。
  - `ensure --status`、`list --status` / `laws --status`（已存在的本地
    DB 检索过滤，与远程 fetch 过滤是不同语义层面）。
- `src/chinalaw/adapters/*.py`、`src/chinalaw/cleaning.py`、
  `src/chinalaw/service.py`、`src/chinalaw/db.py`、schema、migration 均
  零改动。
- `docs/CONTRACT.md` 同步 `fetch --status`、`discover` 输入 / 输出 / 退出码
  契约。
- `.claude/skills/chinalaw-fetching/SKILL.md` 同步跨期旧法补全流程，明确
  `discover --status` → `fetch --prefer-id` 的 agent 路径。
- 新增 `tests/test_cli_status_flag.py`（26 用例 / 5 类）：
  - `CliFetchStatusParseTests` × 5：argparse 解析 / default None / 非法
    值被 SystemExit(2) 拦 / 全 4 个合法 keyword 接受 / discover 子命令
    解析。
  - `FetchLawStatusFilterTests` × 6：sxx=[1] / sxx=[2] / 默认不传 sxx /
    court_gongbao 抛 ValueError / spp_gov_cn 抛 ValueError / `--status`
    禁用本地 alias hint；error message 含 supported sources 列表。
  - `CliHandleFetchStatusErrorTests` × 1：CLI 层端到端 court_gongbao +
    status → 结构化 `law_fetch_error` payload + exit 2。
  - `DiscoverLawsTests` × 6：sxx 透传 / 默认不传 sxx / 按 `gbrq DESC` /
    court_gongbao 拒绝 / 不支持 source 拒绝 / query 透传。
  - `StatusToSxxTests` × 8：四个 status 各一条单元 + unknown 抛错 +
    `STATUS_TO_SXX` 与 `cleaning.SXX_TO_STATUS` 反向对称守门 +
    `STATUS_FILTER_SUPPORTED` 仅 flk_npc + `DISCOVER_SOURCES` 仅 flk_npc。
- 测试基线 472 → 498 passed（+26，无回归）；`ruff check src/ tests/` 全绿。

### 修复（cleaning.py docx 文号 fallback 限 preamble，2026-05-05，PR-A.1 / codex P2）

- `src/chinalaw/cleaning.py::_extract_document_number_from_docx_bytes` 在
  PR-A 的 squash merge（``b779ab0``）已带入第二个 commit
  ``fix(cleaning): limit flk document number extraction to preamble``，
  实测 master HEAD 实现已经只扫 metadata preamble（``_metadata_preamble_text`` /
  ``_metadata_preamble_text_from_lines``，在第一个 ``ARTICLE_RE`` /
  ``_is_structural_heading`` / ``目录`` marker 处截断）。本 PR 把该
  invariant 从 PR-A 的实施细节里**提升为一级 invariant**，独立成 spec、
  补 4 条边界测试钉住未来 regression。
- 不动实现（`_extract_document_number_from_docx_bytes` /
  `_metadata_preamble_text` / `_metadata_preamble_text_from_lines` 一行不改）。
- 新增 `docs/CLEANING_FLK_PREAMBLE_ONLY_SPEC.md`（§0 立场 / §1 现状含
  master HEAD grep 锚点 / §2 决策含拒绝方案 A1（前 N 段）/ A3（OR 兜底）/
  B（下游去重）/ C（正则带位置）共 4 个 / §3 实施 / §4 风险 / §5 不在范围
  / §6 验收）。
- `tests/test_cleaning_flk_npc_metadata.py` 追加测试类
  `FlkNpcDocumentNumberPreambleBoundaryTests` × 4 用例（PR-A 主测试类
  `FlkNpcDocumentNumberTests` 已覆盖 DOCX zip 路径"正文引用文号不得误抽"，
  本 PR 补 PR-A 未触及的 4 个边界）：
  - `test_document_number_from_legacy_doc_preamble` —— OLE legacy ``.doc``
    路径正面：题注含 ``法释〔2023〕13号`` → 抽出（patch
    ``_convert_legacy_doc_to_text`` 注入 textutil / antiword 转出文本，
    避免对 host 工具依赖；与 ``test_corrupt_docx_falls_back_to_none``
    patch ``parse_articles_from_word_bytes`` 同型）。
  - `test_document_number_legacy_doc_ignores_article_body_citations` ——
    OLE legacy ``.doc`` 路径负面：题注无、正文引用文号 → None。
    镜像 DOCX zip 路径的 ``test_document_number_ignores_article_body_citations``，
    确保两条解码路径（DOCX zip / OLE legacy）走同一个
    ``_metadata_preamble_text_from_lines`` 切片，invariant 同步生效。
  - `test_document_number_empty_preamble_when_first_paragraph_is_article` ——
    边界：第一段就是 ``第一条 ...`` → preamble 切片为空 →
    ``extract_document_number("")`` 返回 None；不抛错。
  - `test_document_number_directory_marker_breaks_preamble` —— 边界：
    题注后接 ``目录`` paragraph，目录条目含合规文号字符串（构造场景：
    flk 司法解释 docx 真实形态）→ preamble 已在 ``目录`` marker 截断，
    目录条目不被吃进 preamble → None。
- 行为差异：零（实现未变，本 PR 为测试 + spec + changelog + progress 四
  文件改动，把 PR-A 的 codex P2 fixup 锁成 invariant）。
- 不在范围（详见 spec §5）：扩 ``DOCUMENT_NUMBER_INLINE_RE`` 覆盖
  ``主席令`` / ``国务院令`` / ``extract_document_number`` 加位置约束 /
  题注内"引用文号 vs 本法规文号"消歧 / `_convert_legacy_doc_to_text`
  缺工具退化 / `_metadata_preamble_text_from_lines` `enum_ordinal` 隐式约束 /
  缓存 `_iter_docx_paragraphs` 结果。
- 测试基线 468 → 472 passed（+4，无回归）；`ruff check src/ tests/` 全绿。

### 修复（cleaning.py 恢复 flk_npc metadata，2026-05-05，applicability 闭环 PR-A）

- `src/chinalaw/cleaning.py` 顶部新增
  `from chinalaw.document_numbers import extract_document_number`。
- `canonicalize_flk_npc` 替换两处硬编码（修前同模块其他 3 条 canonicalize
  路径都"输入有就读、缺则 None"，仅 flk_npc 路径无视输入直接写 None；
  本次同层不变量恢复）：
  - L171 `"document_number": None` →
    `"document_number": _flk_document_number(detail_data, docx_bytes)`。
    新 helper 优先读 detail JSON 候选键 `wenhao` / `wh` / `documentNumber`
    （防御未来 flk schema 升级 / caller 注入合成 payload），找不到时调
    `extract_document_number` 抽 docx 题注 / 首个条文前前言区的首匹配子串（与
    court_gongbao / spp_gov_cn adapter 对正文调
    `_extract_document_number(text)` 同型）。
  - L174 `"repealed_at": None` →
    `"repealed_at": detail_data.get("fzrq")`。实测当前 flk JSON 详情接口
    不返回 `fzrq`，但移除硬编码后 caller 注入 / 未来 schema 升级即可使用，
    与原硬编码语义对外等价。
- 新增私有 helper：
  - `_flk_document_number(detail_data, docx_bytes)` —— detail JSON 候选键
    优先链 + docx 题注兜底。
  - `_extract_document_number_from_docx_bytes(docx_bytes)` —— 复用
    `_iter_docx_paragraphs` / `_convert_legacy_doc_to_text`，只扫描首个章节 /
    条文前的 metadata preamble，再喂给 `extract_document_number`。窄 except
    （`KeyError, ValueError, ET.ParseError, zipfile.BadZipFile`）swallow
    解析错误回退 None，与 PR5c / PR6 风格一致；编程错误透传。
- 行为差异：
  - flk 司法解释 docx 题注（``法释〔2023〕13号`` 等带 ``〔YYYY〕`` 年份段
    的发文体）→ `document_number` 自动抽出。
  - flk 法律 / 行政法规 docx 题注（``主席令第N号`` / ``国务院令第N号`` 无
    ``〔YYYY〕`` 年份段）→ 不在共享正则覆盖范围，document_number 仍 None
    （与修前等价）。扩 regex 覆盖这两种形态需要重审
    `docs/UNIFY_DOCUMENT_NUMBER_REGEX_SPEC.md` invariant，单独 PR 跟进。
  - caller 在 `detail_payload.data` 中提供 `wenhao` / `fzrq` → 永远生效
    （此前永远丢失）。
  - 同层不变量恢复：4 条 canonicalize 路径全部"输入有就读、缺则 None"。
- 不在范围（详见 spec §5）：
  - court_gongbao / spp_gov_cn cleaning 同类硬编码审计——它们经
    `_canonicalize_local_text_payload` 已正确从 metadata 读，无该问题。
  - `repealed_at` 的实际值推导（lsyg 同名版本链反推 / `applicability_v0_2.json`
    `superseded_by` 跨名替代反推）——cleaning 层不应做跨数据反查，留给
    后续 PR-B / fetch / sync / applicability resolver 层。
  - CLI `fetch --status` flag 暴露 / `xgwj` / `xgzl` 等 flk 关联资源抓取。
- `docs/CLEANING_FLK_NPC_RESTORE_METADATA_SPEC.md` 落地（§0 立场 / §1 现状
  含 grep 锚点 / §2 决策含拒绝方案 B（cleaning 层做跨法反查）+ 拒绝方案
  C（扩函数签名）/ §3 实施 / §4 风险 / §5 不在范围 / §6 验收）；
  补正 `docs/APPLICABILITY_FETCH_PROBE.md` §1.3 注释里"flk 详情藏 wenhao /
  repealed_at 字段"的过度声明（实测 flk JSON 详情接口完全没有这两个字段）。
- 新增 `tests/test_cleaning_flk_npc_metadata.py`（10 用例 / 4 类）：
  - `FlkNpcDocumentNumberTests` × 6：detail_data `wenhao` / `wh` 候选键、
    docx 题注 ``法释〔2023〕13号``、正文引用文号不得误抽、缺失 fallback、
    损坏 docx swallow。
  - `FlkNpcRepealedAtTests` × 2：`fzrq` 命中 / 缺失 fallback。
  - `FlkNpcMetadataExistingFixtureRegressionTests` × 1：现有
    `data/fixtures/contract_chapter_interpretation_2023.json` metadata 不漂移。
  - `FlkNpcMetadataPriorityTests` × 1：detail_data `wenhao` 优先于 docx 题注。
- 测试基线 458 → 468 passed（+10，无回归）；`ruff check src/ tests/` 全绿。

### 修复（verify_source 4 处 except Exception 收窄，2026-05-05，PR6）

- `src/chinalaw/sources.py` 顶部加 `from urllib.error import URLError`。
- `verify_source()` 4 处 `except Exception as exc:` 按 try 块业务边界分四段窄：
  - L120 `get_source_adapter(source_name)` —— 工厂查找：改为 `except ValueError as exc:`。
  - L126 `adapter.probe()` —— HTTP 探活：改为
    `except (URLError, OSError, TimeoutError) as exc:`。
  - L139 `adapter.search_list(...)` —— HTTP + JSON / HTML 解析：改为
    `except (URLError, OSError, TimeoutError, ValueError, KeyError) as exc:`。
  - L166 `adapter.build_law_payload(...)` —— HTTP detail + cleaning：改为
    `except (URLError, OSError, TimeoutError, ValueError, KeyError) as exc:`。
- 行为差异：
  - 修前 `except Exception:` 把 adapter 实现 bug（`AttributeError` 拼写错误 /
    `NameError` 未定义变量 / `TypeError` 类型误用 / cleaning 防御性
    `RuntimeError`）静默吞成
    `{ok: False, message: "<step> failed: <exc>"}`，让用户把 **代码 bug**
    诊断为 **source 不可用**；
  - 修后业务降级（unknown source / 网络断 / 解析失败）
    仍正常 swallow 走 `ok=False` 报告路径（与修前等价），但编程错误透传
    出 verify_source，能在第一次出现时看到真 stacktrace。
- 与 PR5c (`fetch.py`) 同型反模式收尾：`docs/EXCEPT_EXCEPTION_AUDIT.md`
  标记的 Class A 反模式（4 处全部集中在本函数）全部消除。
- 新增 `tests/test_verify_source_narrow_exception.py`（12 用例 / 3 类）：
  业务降级 swallow 守门 + 编程 bug 透传守门 + URLError import 守门。
- 详见 `docs/VERIFY_SOURCE_NARROW_EXCEPTION_SPEC.md`。

### 修复（fetch.py 5 处 except Exception 收窄，2026-05-05，reviewer H1）

- `src/chinalaw/fetch.py` 顶部加 `import sqlite3`。
- 5 处 `except Exception:` 按 try 块业务边界分两类窄：
  - DB 路径（3 处）—— `_resolve_local_fetch_hint` / `_lookup_document_number_hint`
    / `_try_resolve_canonical_id`：改为
    `(sqlite3.OperationalError, sqlite3.DatabaseError, OSError)`。
  - 解析路径（2 处）—— `_locate_article` 内两次 `normalize_article_number`
    调用：改为 `(TypeError, ValueError)`。
- 行为差异：
  - 修前 `except Exception:` 把真正的编程 bug（`AttributeError` /
    `KeyError`）和配置错误（`db.migrate` 缺 migrator 抛 `RuntimeError`）
    静默吞成"hint 不命中"，与 row 真不存在的现象**不可区分**；
  - 修后 DB 异常 / 解析异常仍被吞（业务等价），但编程错误与 schema
    配置错误正常透传，能在第一次出现时定位。
- 不动 3 处 `except Exception as exc:` —— 这些已识别异常做包装重抛 / 写
  `payload.warnings`，属于「显式处理」模式，不在本 PR 范围。
- 新增 `tests/test_fetch_narrow_exception_handlers.py`（12 用例 / 4 类）：
  DB 异常仍 swallow 守门 + 编程错误透传守门 + `_locate_article` 解析路径
  守门 + sqlite3 import 守门。
- 详见 `docs/FETCH_NARROW_EXCEPTION_HANDLERS_SPEC.md`。

### 重构（_resolve_local_fetch_hint 三源对称，2026-05-05，reviewer C4）

- `src/chinalaw/fetch.py:_resolve_local_fetch_hint` 单走 flk_npc 改为三源对称：
  - 顶部新增 `SOURCE_NAME_MARKERS: dict[str, str]` 常量（三源 ↔ 写入侧
    `source_name` 字面量），用于校验本地命中的 row 与请求源一致；
  - 函数体改 `if source not in SOURCE_NAME_MARKERS: return None`，复用
    `chinalaw.document_numbers.infer_source_id(payload, source)` 推导各源主键
    （flk_npc → bbbs / court_gongbao → detail_id / spp_gov_cn → path fragment）。
- `_extract_flk_bbbs` / `_raw_flk_bbbs_from_id` / `_looks_like_flk_bbbs` 三个
  flk-only helper 函数体完全删除；fetch.py 顶部 `import re` 与
  `from urllib.parse import parse_qs, urlparse` 同步清理。
- court_gongbao / spp_gov_cn 的 fetch hint 路径**首次**贯通：之前两个源的
  fetch 都退化到远程标题搜索，agent 拿到一堆候选要再次消歧；现在本地 row
  已有的 source_id（court 的 detail_id / spp 的 path fragment）能直接喂给主流程。
- FLK 路径继续保留 `hint["bbbs"]` 兼容字段（fetch 主流程历史按 bbbs / detail_id
  / id 三件套读取）；court / spp 路径**不写**该字段，下游可用 `hint.get("bbbs")`
  显式区分源风味。
- 新增 `tests/test_symmetric_local_fetch_hint.py`（13 用例）：每源一个正例 +
  unknown source / 跨源 marker 不匹配 / DB 缺失 / row 缺失 + 三个 helper
  hasattr 守门 + `SOURCE_NAME_MARKERS` 与 `FETCH_SOURCES` key 一致守门。
- 详见 `docs/SYMMETRIC_LOCAL_FETCH_HINT_SPEC.md`。

### 重构（adapter HTML helper + fetch row helper 收口，2026-05-05，reviewer H2 + H3）

- `src/chinalaw/adapters/_html.py`（新建）收口四个站点无关 helper：
  - `html_extract_title(html)`：`<title>` 标签内文本抽取 + HTML unescape
  - `html_to_text(content_html)`：detail 页正文 HTML → 段落保留的纯文本，
    归一 EN/EM/全角空格三种 Unicode 空格变体（spp 修前的超集，court 修前
    只归一全角空格；fixture 已 grep 验证不漂）
  - `strip_known_title_suffix(raw_title, suffixes)`：参数化 suffix 列表
  - `infer_short_title(title, *, site_prefixes)`：参数化 issuer prefix 列表，
    内核保留 `aliases.preferred_short_title` 优先 + `cleaning.infer_short_title`
    兜底
- `court_gongbao.py` / `spp_gov_cn.py`：删本地 4 个 helper 函数体，改成
  module-level alias（`_extract_title`）或薄 wrapper（`_html_to_text` /
  `_strip_title_suffix` / `_infer_short_title`）转发到 `_html`，私有 site
  数据（标题后缀清单 / issuer 前缀清单）以模块常量传入。既有
  `court_gongbao._html_to_text` / `court_gongbao._infer_short_title` 等测试
  attribute 全部保留。
- `src/chinalaw/fetch.py`：删 `_clean_title` / `_row_id` /
  `_candidate_from_row` / `_normalize_row_status` 四个本地 helper，改 `from
  chinalaw.sources import ...`。`_normalize_row_status` 与
  `sources._status_from_row` 修前优先级相反（fetch 先 `status` 后 `sxx`，
  sources 先 `sxx` 后 `status`），收口选 sources 版作权威；`fetch.py` 顶层
  `from chinalaw.adapters.flk_npc import SXX_TO_STATUS` 同步删除。
- `src/chinalaw/sources.py` `_status_from_row` docstring 增加权威声明。
- 新增 `tests/test_adapter_html_helpers.py`（7 用例）+ `tests/test_sources_status.py`
  （4 用例）守门：HTML helper 行为 / module alias / status 优先级 / fetch
  attribute 等价于 sources。
- 详见 `docs/ADAPTER_HTML_HELPERS_SPEC.md`（§0 立场 / §1 现状 / §2 决策 /
  §3 实施 / §4 风险 / §5 不在范围 / §6 验收 / 附录 A 不动的相邻设计 / 附录
  B 历史变化轨迹）。

### 重构（db migrator 注册表化，2026-05-05，reviewer C3）

- `src/chinalaw/db.py`：把 8 档 `if/elif from_version == N` 阶梯换成
  `_MIGRATORS: dict[int, Callable]` 注册表 + module-level 完整性 assert +
  while loop 主控流。解决 silent corruption 风险：未来加 v9 时若忘
  追加 elif 分支，旧路径会 early-return 不调任何 migrator，但收尾仍把
  `schema_version` 升到最新——schema_version 升号但表未升。注册表化后
  漏加键会被 `assert set(_MIGRATORS) == set(range(0, SCHEMA_VERSION))` 在
  import 时立刻拒绝；运行时仍兜底 `RuntimeError`，避免 `python -O` 关
  assert 时静默漂移。
- 新增 `_migrate_v0_to_v1` wrapper：保留"空 DB 一次性 executescript
  SCHEMA_V8_SQL"的修前快路径，让 v0 → v1 也满足"单步升一档"的注册表
  contract；后续 ALTER migrator 在自身 `IF NOT EXISTS` / `PRAGMA
  table_info` 守护下重复跑无副作用。既有 `_migrate_v*_to_v*` 函数体一字未动。
- 新增 `tests/test_db_migrators.py`（3 用例）：注册表完整性 / 已最新时幂等 /
  参数化 0..7 八个起点都能升到最新且关键表齐全。
- 详见 `docs/DB_MIGRATOR_REGISTRY_SPEC.md`（§0 立场 / §1 现状 / §2 决策 /
  §3 实施 / §4 风险 / §5 不在范围 / §6 验收 / 附录 A 加 v9 SOP）。

### 重构（document_numbers 正则收敛跨 adapter，2026-05-05，reviewer C5）

- `src/chinalaw/document_numbers.py` 提升为文号识别的唯一权威定义。修前
  3 处独立正则口径不一：
  - `document_numbers.DOCUMENT_NUMBER_RE`：fullmatch，无空白容忍，无前缀约束
  - `court_gongbao.DOCUMENT_NUMBER_RE`：search，强制 `法` 开头，1-4 字
  - `spp_gov_cn.DOCUMENT_NUMBER_RE`：search，最少 2 字，无前缀约束
- 新增三种导出形态：
  - `DOCUMENT_NUMBER_FULLMATCH_RE`：`DOCUMENT_NUMBER_RE` 的语义别名
    （已 `^...$` 锚定，供 input 识别 / `document_number_index` key）
  - `DOCUMENT_NUMBER_INLINE_RE`：不锚定 + `\s*` 容忍，供正文抽取
  - `extract_document_number(text)` helper：用 INLINE_RE search +
    normalize_document_number 折叠空白；None / "" / 无文号 → None
- `court_gongbao.py` 与 `spp_gov_cn.py`：删本地正则常量 + 函数体，改用
  `from chinalaw.document_numbers import extract_document_number as
  _extract_document_number`；module-level alias 保留作为 adapter API，
  既有 `tests/test_core.py` 中的 `court_gongbao._extract_document_number`
  断言不需要修改即可继续 pass
- 修复 court_gongbao 修前漏召场景：
  - 高检发释字〔2017〕7号 → 修前 None / 修后命中
  - 中办发〔2022〕10号 → 修前 None / 修后命中
  - 国发〔2021〕23号 → 修前 None / 修后命中
- 新增 `tests/test_document_numbers.py`（6 用例）：5 类前缀参数化 / 空白容忍 /
  无文号 / 两个 adapter 都委托共享 helper / 模块常量语义边界
- 详见 `docs/UNIFY_DOCUMENT_NUMBER_REGEX_SPEC.md`（§0 立场 / §1 现状 / §2
  决策（采用 A：唯一权威定义；拒绝 B 保留 court_gongbao 前缀过滤 / 拒绝 C
  仅同步 normalize）/ §3 实施 / §4 风险 / §5 不在范围（不引入前缀白名单 /
  不动 schema / 不动 fetch 用户输入识别路径）/ §6 验收 / 附录）。

### 重构（cleaning，枚举式 heading 改为序列判断，2026-05-04，Tier 3）

- 删 `src/chinalaw/cleaning.py` `ENUM_HEADING_ACTION_WORDS` tuple，不再靠
  「应当 / 不得 / 可以」等 modal 词表判断 `一、XXXX` 是否是正文枚举。
- `ENUM_STRUCTURAL_HEADING_RE` 增加 `ordinal` 捕获，`_is_structural_heading`
  改为基于 enum 序列判断：第一个 enum heading 必须是 `一、` / `1、`，后续
  必须按 `二、三...` 递增；遇到编 / 章 / 节 / 附则后重置序列。
- `normalize_articles()` 的 trailing heading 修复路径保留 `allow_orphan_enum`
  兼容口，用于 `rebuild-clean` 修复旧快照中缺失前序 heading 的情形；实时 parse
  不放宽。
- 保留并改写反向测试为 `test_cleaning_keeps_nonsequential_enumerated_body_lines`：
  用 `一、甲方负责履行合同` 证明防护不是动词硬编码，而是结构递增规则。
- 详见 `docs/DROP_ENUM_HEADING_ACTION_WORDS_SPEC.md`。

### 数据（合通解释 fixture 刷新 + 守门测试，2026-05-04，Tier 1 follow-up of PR #42）

- `data/fixtures/contract_chapter_interpretation_2023.json` 用 PR #42 修复后的
  `canonicalize_flk_npc` 重新落盘：69 article 全部带 part 字段（9 个 distinct
  section heading 一→九），trailing heading 已剥离。
  `source_checked_at` 2026-04-29 → 2026-05-04；`source_hash` 不变（同份 detail +
  docx → hash 一致，证明只是协议字段对称恢复，未误改源数据）。
- 新增 `tests.test_core.LoaderAndServiceTests.test_contract_chapter_fixture_has_complete_part_coverage`：
  断言 69 article / 0 missing part / 9 distinct part / set 等于一→九 9 个一级段落。
  未来 fixture 再漂移会立刻 fail。
- ⚠️ 浮现的 fetch CLI lifecycle 嫌疑点（**未在本 PR 修，单开 spec**）：
  `chinalaw fetch --to-fixture --force` 重新落盘时不保留 fixture 既有的
  人工 alias（领域圈内黑话如"合通解释"）+ 把 `category_ids` 重置为空数组
  （`categories` 详细数组保留）。本次手动 post-fetch restore 让 4 个 alias
  / cited_by 测试继续 pass。

### 修复（cleaning canonicalize_flk_npc 加 normalize_articles 兜底，2026-05-04）

- `src/chinalaw/cleaning.py` `canonicalize_flk_npc` 在落盘前补一次
  `normalize_articles`，与其他 3 个 source_kind（`external_json` / `markdown`
  / `docx`）对称。修前 `articles` 直接用 `parse_articles_from_word_bytes`
  返回值落盘，缺 trailing heading 剥离 / part 兜底 / number 兜底。
  这是 `data/fixtures/contract_chapter_interpretation_2023.json`
  `articles[*].part` 全 None 漂移的根因（fixture 在 commit `21a02ab` 之前由
  `chinalaw fetch --to-fixture` 落盘，当时 cleaning 不补 part）。
- 新增回归 `tests.test_core.SourceProbeTests.test_canonicalize_flk_npc_normalizes_articles`：
  用 `patch.object(cleaning, "parse_articles_from_word_bytes", ...)` 注入
  pathological articles（trailing heading 嵌在 art №1 末尾、part 全 None），
  断言 canonicalize 输出已剥离 heading 并把 context 传播给 art №2 的 part。
- 详见 `docs/CANONICALIZE_FLK_NPC_NORMALIZE_SPEC.md`。fixture 刷新作为
  follow-up commit（合并本 PR 后跑 `fetch --force`）。

### 新增（alias 系统三层重写 + resolve 命令，2026-05-04）

- 按 `docs/ALIAS_SYSTEM_SPEC.md` 三层职责（规则层 / 数据层 / agent 层）
  重写 `src/chinalaw/aliases.py`：删除按法名硬编码的 alias 分支（合同编通则
  解释 / 总则编解释 / 担保制度解释 / 民法典时间效力规定 / 劳动争议解释 /
  商法系 6 组等），改为 `_ISSUER_RULES` + `_identify_issuer` /
  `_extract_host_law` / `_extract_ordinal` 全域规则。fixture 数据层补齐
  `short_title` / `aliases` 兜底。详见 commit `5fc0991`
  `refactor(aliases): rewrite as issuer-based three-layer rule`
- 新增 `chinalaw resolve <俗称>` CLI 子命令（commit `d56ad17`
  `feat(resolve): chinalaw resolve command + skill 协议`）：
  - 复用 `service._resolve_law_row` 但回带 `via` 命中路径字段
    （`id_match` / `title_match` / `short_title_match` / `alias_exact` /
    `alias_derived` / `like_fallback`），返回扁平元数据
    （official_title / short_title / aliases / level / status / issuing_body /
    released_at / effective_at），不返回条文 / 修订快照 / 全文
  - `--format md|json` 双输出；未命中 exit 1 + md hint 指向
    `chinalaw fetch <input> --list-matches`
  - 3 份 SKILL.md（`chinalaw-using` / `-searching` / `-fetching`）补
    "用户俗称解析协议"段落，`chinalaw-using` 明确"绝不脑补
    『中华人民共和国』前缀"；commit `956e6db` 修正 skill 示例只用仓库已有
    fixture，避免 agent 复现失败

### 变更（fetch 层隐式耦合点收口 per FETCH_LAYER_SPEC，2026-05-04）

- `aliases.preferred_short_title` 显式契约函数：把 court_gongbao /
  spp_gov_cn `_infer_short_title` 中的 `common_law_aliases(...)[0]` 隐式
  「取首选」抽出为命名 helper，docstring 列出全部调用方，下次再动 alias
  列表顺序前必须 grep 该函数
- 新建 `src/chinalaw/identity.py` + 纯函数
  `law_row_matches_payload(row, payload, *, strict)`：把 fetch / service /
  fixture 三处"同一法判定"的口径差异显式参数化。`strict=True` 校验
  source_name + 修订日期 + 名称交集（fetch canonical id 路径用）；
  `strict=False` 固定宽松口径的语义边界，当前仅由 identity 单元测试覆盖，
  `service.resolve` 仍保留 SQL resolver，不通过该 helper。删除
  `fetch._same_law_identity` / `_identity_names` / `_same_version_dates`
  inline 逻辑
- alias_agent 改默认关闭：`fetch._maybe_enrich_aliases` 默认只跑
  deterministic `merge_law_aliases`；显式设 `CHINALAW_USE_ALIAS_AGENT=1`
  才会调 `derive_aliases`。新增 `AliasAgentRecoverableError` 区分
  `missing_api_key` / `network` / `invalid_response` 三类已知可恢复错误，
  fetch 主流程把 recoverable 写入 `payload.warnings`（不挂主流程），
  unknown Exception 透明上抛
- `chinalaw status` 输出新增 `alias_agent: enabled / disabled` 字段，便于
  troubleshooting 时确认默认关闭状态
- `docs/CONTRACT.md §4.11` 增补 `payload.warnings` 字段（`alias_agent_skipped`
  code）和 `CHINALAW_USE_ALIAS_AGENT` 环境变量说明
- `fetch --to-fixture` 落盘前过滤 `payload.warnings`，避免把 runtime 警告
  序列化进 PR 用的 fixture 文件（commit `dc2352b`
  `fix(fetch): keep runtime warnings out of fixtures`）
- 测试基线 374 → 386 → 395 → 396 passed
- 详见 `docs/FETCH_LAYER_SPEC.md`（头部状态 `待审查` → `已实施 2026-05-04`）

### 文档（spec 实施落地后的过期信息清理，2026-05-04）

- `docs/FETCH_LAYER_SPEC.md` / `docs/ALIAS_SYSTEM_SPEC.md` 头部状态字段从
  `待审查` / `待执行` 改为 `已实施 2026-05-04` + commit ref；保留全文作为
  历史记录与下次回查依据
- `HANDOFF.md` §2：日期 2026-05-03 → 2026-05-04；测试基线 342 → 396；
  补 ALIAS_SYSTEM_SPEC + FETCH_LAYER_SPEC 实施摘要；补
  `data/fixtures/contract_chapter_interpretation_2023.json` 是 2026-04-29
  早期 cleaning 落盘的 stale 存档说明（详见下一条）
- `HANDOFF.md` §3 已实现能力：加 `resolve <name>` 命令条目
- `PROGRESS.md` 追加 2026-05-04 条目，记录 ALIAS_SYSTEM / FETCH_LAYER 两轮
  实施 + 上一会话 cleanup/cleaning-heading 分支调查结论：上一会话基于
  fixture 表象认定为"cleaning article 边界贪婪 bug"实际是 fixture 过时存档
  问题——现行 cleaning 跑同一份合通解释 docx 已能正确切出 9 个 distinct
  part；fixture `source_checked_at = 2026-04-29` 早于关键 helper
  `_split_trailing_structural_headings`（commit `21a02ab`）落地。诊断结论
  是无 cleaning 代码 bug，修复 fixture 用一条命令：
  `chinalaw fetch 合通解释 --source flk_npc --to-fixture
  data/fixtures/contract_chapter_interpretation_2023.json --force`
- 留作未决 issue（不属于本 PR）：`canonicalize_flk_npc` 路径独缺
  `normalize_articles` 调用，新 fetch 落盘的 fixture 仍可能 part = None；
  是否在 cleaning 层统一所有路径都 normalize、还是在 `fetch --to-fixture`
  出口前再跑一次 normalize，需另开 spec

### 新增（DeepSeek harness eval）

- 新增 `scripts/host-eval.sh` / `scripts/docker-eval.sh`：用 DeepSeek 后端驱动
  Claude Code 在隔离 HOME / cwd / DB 中运行 chinalaw + 7 份 skill，评估 agent
  是否按 skill 决策树完成法律研究，而不是评模型文风
- 新增 `scripts/eval/run-batch.sh`、`analyze.py`、`aggregate.py` 和 6 个法律研究
  场景 TSV：自动记录 skill 激活、工具调用、chinalaw 子命令、引用条文验证、
  降级 token、耗时，并生成 `runs/<RUN_ID>/report.md`
- 新增 `BARE_PROMPT=1` 裸提示模式和 per-question TSV，方便测试 agent 是否能在
  没被显式要求使用 CLI 时自行激活 chinalaw skills
- 新增 `tests/test_eval_analyze.py`，覆盖 stream-json analyzer 的子命令解析、
  最终答案扫描和 ISO timestamp 耗时计算

### 变更

- CLI 现在同时支持 `chinalaw --db DB <cmd>` 与 `chinalaw <cmd> --db DB`，
  降低 agent / 文档示例因参数顺序产生的失败
- eval analyzer 不再把 user prompt / tool result 计入最终答案文本，避免
  hallucination / degradation 指标被提示词或工具输出污染
- Docker eval 的网络 smoke 与实际 run 都透传显式 proxy env；host eval 的
  env 诊断读取隔离 DB，不再误读本机 `~/.chinalaw`

### 新增（agent skills 系统化 rollout）

把 chinalaw 给 agent 用的"craft 知识"系统化为一套基于事实标准
（[choutos/agent-skills-spec](https://github.com/choutos/agent-skills-spec) +
[obra/superpowers](https://github.com/obra/superpowers) +
[opencode.ai/docs/skills](https://opencode.ai/docs/skills/)）的 SKILL.md
目录，可被 Claude Code / OpenCode / Codex CLI / Cursor / Cline / superpowers
等多框架 lazy-load。

**目录与事实标准对齐**：

- 仓库 skill 目录 `examples/skills/` 重命名为 `.claude/skills/`（git mv，
  保留 history）。`.claude/skills/` 是上述工具栈共同识别的事实标准
  （opencode.ai/docs/skills 明文兼容 `.claude/skills/`），用户克隆仓库后
  无需任何路径改写 skill 即可 lazy-load
- 所有 SKILL.md 头部带 YAML frontmatter（`name` + `description`），让 agent
  能基于摘要判断何时激活，不再需要整篇读取污染上下文；description 字段被
  精心调整以避免 YAML 解析歧义（如 `chinalaw-fetching` 把 `article: null`
  字面量替换为 `article=null` 并整段加引号，避免被 YAML 误判为 mapping）
- `.claude/skills/README.md` 是 skill 目录总索引：说明 SKILL.md 事实标准、
  `references/` / `scripts/` / `assets/` 子目录约定、工具中立载入路径、
  craft 横切 vs 场景流程的两类划分
- `scripts/install-skills`：把 `.claude/skills/*` 安装到用户级
  `~/.claude/skills/` + `~/.agents/skills/`，默认 symlink（更新仓库时
  自动跟进）；`--copy` 适配 Windows / WSL；`--target` 可追加 OpenCode
  全局 `~/.config/opencode/skills/` 等路径；`--uninstall` 只清理回指
  本仓库的 symlink，不会动用户自己写的同名 skill

**5 个新 craft 横切 skill**（方法 / 决策 / 反模式，agent 长期 internalize）：

- `.claude/skills/chinalaw-using/SKILL.md`：总入口 skill。4 句心法
  （不查就答=错 / search 命中≠条文 / applicable 是线索不是结论 /
  norm≠law）+ 任务路由决策树 + 工具中立指引
- `.claude/skills/chinalaw-checking/SKILL.md` + `references/output-templates.md`：
  AI 引用核对 craft skill。6 步 procedure（拆解 / 逐条 article / 文本比对 /
  状态校验 / 时间效力校验 / 缺失补全），文本比对分 5 档（exact /
  cosmetic_drift / wording_drift / structural_mismatch / hallucination），
  状态分 5 档（current / amended / repealed / pending_effective / unknown）；
  4 种典型输出模板（单条 / 批量汇总 / 时间效力专项 / 大面积失实）
- `.claude/skills/chinalaw-searching/SKILL.md` + `references/walkthroughs.md`：
  检索方法 craft skill。6 大方法（法规名归一化 / 版本时间锁定 / 跨法
  transition / LawLevel 11 档语义判别 / 粒度选择 / 关联解释链式检索）+
  4 个实战 walkthrough（公司法版本不明 / 九民纪要跨源 / 合同法 113→民法典
  584 跨期 / 私域+国家法混合）
- `.claude/skills/chinalaw-maintaining/SKILL.md` + `scripts/doctor.sh`：
  本地数据库维护 craft skill。默认目录布局（`~/.chinalaw/chinalaw.db`）+
  3 种多项目模式（全局共享 / 项目独立 / 分层）+ 6 个 SOP（首次安装 /
  定期保鲜 / 怀疑漂移 / 清洗规则升级 / 备份 / 迁移）+ status 自检字段
  解读；doctor.sh 是一键自检脚本（laws / articles / applicability /
  freshness 阈值检查），异常时非零退出便于 cron / CI 接入
- `.claude/skills/chinalaw-fetching/SKILL.md`：多源爬取补全 craft skill。
  6 种缺失模式 + 源选择决策树（按文件类型映射到 `flk_npc` /
  `court_gongbao` / `spp_gov_cn`）+ fetch 关键 flag 表 + 多候选歧义处理
  （`--list-matches` + `--prefer-id`）+ 三源失败降级 + fetch→verify-source
  验证回路 + 文号 one-shot 路径

**既有 2 个场景流程 skill**（在具体场景下串 craft）：

- `.claude/skills/legal-research/SKILL.md`：通用法律检索 / 法律备忘录 /
  法规梳理（已有，本期补 frontmatter）
- `.claude/skills/contract-review/SKILL.md`：合同审查 / 风险清单 / 履行 /
  违约 / 放款条件审查（已有，本期补 frontmatter）

**文档导航与测试**：

- `README.md` 文档入口第 10 项更新到 `.claude/skills`，并新增 "Agent skills
  （craft 横切 + 场景流程）" 子章节列出 7 份 SKILL.md 用途与类型
- `README.md` 安装段把 `scripts/install-skills` 列为可选步骤，靠近
  `scripts/install-local` / `update-local`
- `docs/AGENT_WORKFLOWS.md §5` 重写为 craft 横切 + 场景流程两类索引，
  末尾加 "see also: `.claude/skills/README.md`" 互链
- `HANDOFF.md` / `PROGRESS.md` / `docs/MVP_PLAN.md` 旧路径同步更新
- `pyproject.toml` sdist include 把 `/examples` 替换为 `/.claude/skills`
  与 `/scripts/install-skills`，对齐新目录结构
- `tests/test_agent_assets.py` `SKILLS_DIR` 路径改写，并新增
  `test_skill_files_have_yaml_frontmatter` 覆盖所有 `.claude/skills/*/SKILL.md`
  的 frontmatter 完整性
- `docs/research/2026-05-skills-rollout-plan.md`：本次 rollout 的设计文档与
  PR 拆分计划（PR A 重命名 + frontmatter + install script / PR B 5 个新
  craft skill / PR C 文档导航 + CHANGELOG 收口），留作后续维护参考

### 变更（agent skills 系统化 rollout）

- `examples/` 目录在迁移后为空，从仓库移除；后续 examples 类内容统一通过
  `data/`（fixture）/ `docs/EXAMPLES.md`（agent 调用示例）承载

### 新增（applicability 时间效力 seed 扩展）
- `data/applicability/` 下新增 4 个时间效力 seed fixture，沿用 ADR-0007
  "grounding only / 不输出适用结论"协议：
  - `property-law-to-civil-code.json`：物权法 → 民法典物权编（2021-01-01 锚点），
    topic `物权`
  - `tort-liability-to-civil-code.json`：侵权责任法 → 民法典侵权责任编
    （2021-01-01 锚点），topic `侵权责任`
  - `security-to-civil-code.json`：担保法 → 民法典物权编 / 合同编担保部分
    （2021-01-01 锚点），topic `担保`
  - `company-law-2023-revision.json`：公司法 2018 修正 → 2023 修订
    （2024-07-01 锚点），topic `公司治理`，relation_type 为 `revises`
    （示范同一部法律的修订关系，区别于既有 `replaces` 关系类型）
- 全部新增 fixture 都标记 `confidence: seed_review_required`，由人工审核驱动；
  `rule_text` / `transition_text` 严格遵循 ADR-0007 "检索线索、不直接输出
  适用结论"语义边界
- 测试 `ApplicabilityTests::test_import_relation_and_query_applicable_rules`
  与 `CliTests::test_cli_status` / `test_cli_sync_applicability_json` /
  `test_cli_relation_and_applicable_json` 中的 `==` 强约束断言放宽为
  `>=` + 具体内容断言，使 fixture 库可以在不破坏既有测试的前提下持续扩展

### 关闭 issue
- issue #16（LawLevel 11 档枚举）：在 PR #24 + 后续相关 PR 全部 land 后正式关闭
- issue #14（非 NPC fetch pipeline）：在 court_gongbao + spp_gov_cn 端到端
  fetch（PR #25 → #28 / PR #25 → #30）合并后正式关闭；长尾源（gov.cn /
  cac / csrc / 各省高院规则）按真实需求另开 issue

### 新增（spp_gov_cn 端到端 fetch + 两高联合刑事司法解释）
- `chinalaw.adapters.spp_gov_cn` 升级为端到端可用 adapter（不再只 probe，
  落 ADR-0008 §1.2 承诺）：
  - `search_list(query, *, channel, page, page_size)`：解析
    `/spp/{channel}/index.shtml` 列表（`<li><a href><span>YYYY-MM-DD</span></li>`），
    page≥2 翻页用 `index_{N}.shtml` 静态形态（spp 站点没有 URLScan 拦截，
    与 court_gongbao 的 GET-page1 / POST-page2 协议不同）；`query` 在客户端
    按标题子串过滤
  - `fetch_detail(detail_id)`：抓 `/<detail_id>.shtml` 并切出
    `<div id="fontzoom">` 内的正文 HTML（spp 详情页与 court_gongbao 的
    `gb_content` 完全不同，正文容器、标题后缀、文号位置全部独立）
  - `build_law_payload(detail_id, *, search_row)`：HTML → 纯文本 →
    `cleaning.canonicalize_markdown` 复用既有"第N条"切分；level 启发式：
    `sfjs` → `judicial_interpretation`；`gfwj` → `judicial_policy`；
    `jczdal` → `guiding_case`；title 含"纪要"/"批复"/"复函"/"指导意见"
    再做二次启发式覆盖
  - `source_hash(detail_id)`：详情正文文本的 sha256
- 重点覆盖**两高联合刑事司法解释**：spp 站点 `/spp/sfjs/` 栏目聚合最高法 +
  最高检（部分含公安部）联合发布的所有刑事司法解释，flk_npc 数据库不收录
  这一类内容，实测包括"袭警""危害税收征管""侵犯知识产权""洗钱""拒不执行
  判决裁定"等大量刑法实务高频解释。`fetch --source spp_gov_cn "袭警刑事案件解释"`
  端到端跑通
- detail_id 设计：spp URL 不规整（同时有 `/xwfbh/wsfbt/` / `/spp/sfjs/` /
  `/zdgz/` / 重复双斜杠 / `#fragment` 形态），`_normalize_detail_id` 统一
  规整为路径片段（去 leading `/`、剥 `.shtml`、剥 fragment、折叠 `//`），
  反推 URL 时拼回；agent 把列表里抓到的任意 href 直接喂给 fetch_detail 都 work
- 文号识别：宽容正则覆盖最高检常见前缀 `高检发释字〔YYYY〕N号` /
  `高检发〔YYYY〕N号` 与两高联合发布的 `法释〔YYYY〕N号` / `法发〔YYYY〕N号`
- 发布主体启发式：title 同时含"最高人民法院"和"最高人民检察院" → 联合
  `最高人民法院 最高人民检察院`；含"公安部"/"国家安全部"/"司法部" → 三方
  联合；都不含 → 默认 `最高人民检察院`
- `chinalaw verify-source --source spp_gov_cn`：真实数据源 smoke 现已覆盖
  最高检；CLI choices 由 `sources.VERIFIABLE_SOURCES` 驱动自动暴露
- `chinalaw fetch --source spp_gov_cn`：fetch 协议级入口现可使用最高检；
  `--prefer-id <detail_id>` 直接按详情页 path 拉取（与 court_gongbao 同模式）
- 指导性案例（`jczdal` 栏目）顺带兼容：列表 + 详情可解析、`level=guiding_case`，
  但案例文档不是条文化结构，`articles` 通常为空——按 ADR-0008 §1.2 边界，
  本期不为它专门做 `norm_source` 切分

### 变更（spp_gov_cn 端到端 fetch + 两高联合刑事司法解释）
- `sources.VERIFIABLE_SOURCES` 加入 `spp_gov_cn`，verify-source 三个公开源
  全部覆盖（flk_npc / court_gongbao / spp_gov_cn），ADR-0008 落地完成
- `fetch.py` 的 `prefer_bbbs` 直通分支扩展到 `spp_gov_cn`（与 court_gongbao
  并列），让 agent 已知 detail_id 时跳过远程列表搜索
- `document_numbers.infer_source` / `infer_source_id` 新增 `spp_gov_cn`
  分支：spp 入库时也能写入 `document_number_index` 反查表，让
  `chinalaw fetch "高检发释字〔2025〕1号" --source spp_gov_cn` 可绕过远程
  列表搜索直接命中本地索引
- 测试套件 300 → 331：新增 `SppGovCnFetchTests`（15 例：detail_id 规整、
  search_list 解析 / 客户端过滤 / 翻页协议、fetch_detail 抽 fontzoom / 标题
  后缀剥离 / 接受完整 URL 与 fragment、build_law_payload 两高联合解释端到端、
  指导性案例兼容路径、short_title 长前缀剥离、source_hash 稳定性、level /
  issuing_body 启发式）+ `SppGovCnVerifySourceTests`（1 例：verify-source
  全链路离线 fixture）；`SourceVerifyTests` 翻转为"spp_gov_cn IN
  VERIFIABLE_SOURCES"

### 新增（开源前合规与社区基础设施）
- `CODE_OF_CONDUCT.md`：直接采用 Contributor Covenant 2.1 中文版，与官方
  英文版语义等价；歧义以英文版为准
- `SECURITY.md`：固化漏洞报告渠道（GitHub Security Advisory 优先 / 直接私信
  维护者备用）与响应 SLA（72 h ack / 7 d triage / 30-90 d fix / 14 d 协调
  披露）；明确范围与豁免；削弱 `docs/COMPLIANCE.md` 节流硬下限或 PII 红线
  的 PR 同样视为安全问题
- `.github/workflows/test.yml`：默认离线 CI，矩阵覆盖 Python 3.10 / 3.11 /
  3.12 / 3.13；执行 compileall + unittest discover + ruff check + 构建
  smoke（sdist/wheel build + `chinalaw --help`），不联网执行 verify-source
- `.github/ISSUE_TEMPLATE/bug_report.md` / `feature_request.md` /
  `.github/PULL_REQUEST_TEMPLATE.md`：固化 issue 与 PR 模板，提示读
  PROJECT_CHARTER / DIFFERENTIATION / DEVELOPMENT_GUIDE，避免新贡献者
  落入"不做范围"

### 变更（开源前合规与社区基础设施）
- `pyproject.toml`：
  - `[project.urls]` 填入正式 GitHub URL（Homepage / Repository / Issues /
    Changelog），不再是占位注释
  - `[project.optional-dependencies].dev` 加入 `ruff>=0.5`
  - `[tool.ruff.lint]` 加入 `RUF001/002/003` 豁免（中文全角标点是正确写法，
    不视为 ambiguous-unicode）
  - `[tool.ruff.lint.per-file-ignores]` 仅对 `tests/*` 豁免 E501（长中文
    fixture 强行换行反而割裂法条语义）
- `NOTICES.md` 第 2 节"数据来源"：从占位骨架填表为 flk_npc / court_gongbao /
  spp_gov_cn 三源（含 URL / 性质 / 授权 / 使用方式 / 登记日期），并附
  暂缓接入候选（gov.cn 行政法规库 SPA / cac.gov.cn jsl5 反爬 / csrc.gov.cn /
  商业数据库）

### 修复（lint baseline）
- `src/chinalaw/normpacks.py:432`、`src/chinalaw/normsources.py:345`：修复
  f-string 内反斜杠在 Python 3.10 / 3.11 上属于语法错误的 bug
  （`r'\\%'` 三字符 escape 重写为外部变量 `r"\%"` 两字符 escape）。
  顺带把 SQLite ESCAPE 字符与 LIKE 模式语义对齐
- 常规 ruff 整改：`I001` 排序 / `UP035` typing 模块迁移 /
  `F401` 未使用 import / `SIM117` 嵌套 with / `RUF005` list 拼接 /
  `RUF012` ClassVar 注解 / `E741` 单字母变量名（`l` → `item`）
- 测试基线 master 333 / 333 通过；`ruff check src tests` 全绿

### 新增（court_gongbao 文号反查 + 跨栏目 fetch 一行命中）
- 文号反查：`chinalaw fetch "法释〔2023〕13号" --source court_gongbao` 现在会
  直接命中本地 `document_number_index` 表，绕过远程标题搜索。这条路径只读
  本地索引，不发任何远程请求；DB 不存在或文号未索引时降级回原有 search_list
  流程。文号格式覆盖 `法释〔YYYY〕N号` / `法发〔YYYY〕N号` / `中办发〔YYYY〕N号`
  / `国发〔YYYY〕N号` / `高检发释字〔YYYY〕N号` 等中央 / 部门发文常见形式
- 跨栏目 fetch 一行命中：`court_gongbao` 默认 `search_list` 只搜 `sfjs`
  栏目，对"破产纪要"等 `sfwj` 内容会零命中。fetch 现在在零候选时自动 fallback
  到 `cross_search`（默认 sfjs + sfwj，每个栏目最多翻 5 页），让
  `chinalaw fetch "破产纪要" --source court_gongbao` 一次到位，不再需要
  agent 先 `search_all_pages` 拿 detail_id 再 `--prefer-id` 喂回
- `court_gongbao.cross_search(query, *, serials=("sfjs", "sfwj"), max_pages_per_serial=5)`：
  跨栏目搜索新方法。按 `detail_id` 全局去重；同一 detail_id 在多个 serial
  出现时只保留首次命中。`per_serial` 字段返回每个栏目独立的 scanned_pages /
  matched / total_count 摘要，便于 agent 评估覆盖度。空 query 抛 ValueError；
  `max_pages_per_serial` 自动 cap 到实际 `total_pages`
- `chinalaw.adapters.court_gongbao` 新增模块级 `cross_search` 便利函数，
  与 `default_adapter.cross_search` 等价

### 变更（court_gongbao 文号反查 + 跨栏目 fetch 一行命中）
- schema 版本 `7 → 8`：新增 `document_number_index` 表
  （`(document_number, source) PRIMARY KEY`）；fetch / sync / fixture 入库时由
  cleaning 抽取的 `document_number` 自动写入索引，跳过 source_hash 缓存
  的路径也保证索引存在
- 新增 `document_numbers.py`，集中维护文号识别、空格归一化、source/source_id
  推断与索引写入逻辑；`fetch.py` 保留兼容导入，避免后续 sync / fixture /
  fetch 各写一套文号逻辑
- `fetch.py` 新增 `_lookup_document_number_hint` 辅助函数；
  `fetch_law` 主流程在 `prefer_bbbs` / `local_hint` 之外新增 `doc_no_hint`
  分支（优先级：`prefer_bbbs` > `local_hint` > `doc_no_hint` > 远程搜索）
- `_persist` 接受新参数 `source_id`，在 upsert 之后调用文号索引写入；
  同 (document_number, source) 重复入库时 upsert 最新 source_id / law_id / title
- 测试套件覆盖 cross_search 跨栏目去重 / 空 query 拒绝 / 未知 serial 拒绝 /
  max_pages_per_serial cap 行为；文号识别正负例 / 空格归一化；索引写表 /
  跳过空 doc_no / upsert / DB 缺失降级 / 跨 source 隔离；fetch 文号路径绕过
  search_list / cross_search fallback / 入库时索引被写入

### 新增（合规底线 + 节流硬下限 + UA 标识）
- `docs/COMPLIANCE.md`：抓取行为合规边界——数据范围（仅公开法规、著作权法
  §5 公有领域）、5 条红线（突破反爬 / PII / DDoS 倾向 / 整站镜像 / 冒充身份）、
  节流策略、UA 标识、上游联系方式与 24 小时响应承诺、使用者责任
- `README.md` 文档入口与"授权与数据来源"段同步加 `docs/COMPLIANCE.md` 链接，
  让二次分发本工具的人能看到合规边界
- 三个 adapter（`flk_npc` / `court_gongbao` / `spp_gov_cn`）共享
  `MIN_REQUEST_INTERVAL = 0.1` 节流硬下限：调用方传 `request_interval=0`
  / 负值 / 低于 0.1s 的值都会被静默 clamp 到 100ms。也就是任何调用路径都
  无法在 adapter 层关闭节流，避免 sync / fetch / cross_search 等批量代码
  绕过合规约束
- 三个 adapter 的 UA 都附带 `chinalaw-cli/0.1.0 (+https://github.com/...)`
  标识：`court_gongbao` / `spp_gov_cn` 在浏览器兼容前缀后追加 token（避开
  ASP.NET URLScan 等遗留 WAF 的 UA 启发式），`flk_npc` 改为纯工具 UA
  （旧值 `+https://local` 不再使用，仓库 URL 占位 chinalaw-cli/chinalaw-cli）

### 变更（合规底线 + 节流硬下限 + UA 标识）
- `flk_npc.DEFAULT_USER_AGENT` 从 `chinalaw-cli/0.1.0 (+https://local)` 改为
  `chinalaw-cli/0.1.0 (+https://github.com/chinalaw-cli/chinalaw-cli)`，便于
  上游 access log 识别本工具并联系维护者
- `court_gongbao.DEFAULT_USER_AGENT` / `spp_gov_cn.DEFAULT_USER_AGENT` 拆出
  `TOOL_UA_TOKEN` 模块常量，直接嵌入完整 UA 字符串，agent 想要在自定义
  adapter 复用同一标识可以从 token 拼装
- 测试套件 280 → 293：新增 `AdapterComplianceTests`（13 例）覆盖三个 adapter
  的 UA 含 `chinalaw-cli` / `_build_request` 拼装含 UA / 节流硬下限对 0 / 负值
  / 低于 floor 值的 clamp 行为 / 高于 floor 不被 clamp / 三个 adapter 共享
  常量值 0.1 的契约

### 新增（court_gongbao 分页 + 短名优化）
- `docs/research/2026-05-court-gongbao-scenarios.md`：场景实测报告——
  司法解释 / 司法文件 / 公报案例三类栏目跨页搜索结果，九民纪要等不在
  公报刊载范围的负例验证，已修 bug 与待做事项清单
- `court_gongbao.search_list` 现在用正确的协议翻页：page=1 走 GET（拿
  完整页 footer + 内联 `var totalCount`），page≥2 切换到 POST + form body
  `serial_no=<code>&page=<N>`。修复关键 bug：之前 GET `?serial_no=X&page=N`
  会被 ASP.NET URLScan 拦截到 `/Rejected-By-UrlScan` 404，导致**所有翻页都
  返回 page=1 内容**（`search_list(serial_no='sfwj', page=N)` 在 N=1..29 时
  rows 全部相同，跨页搜九民纪要 / 破产纪要类长尾源直接失效）
- `court_gongbao.search_all_pages(query, *, serial_no, max_pages)`：跨页
  关键词搜索；自动按 `detail_id` 去重，限制 `max_pages` 防止误翻太多页。
  专为 sfwj 这种 29 页 / 861 条的栏目找会议纪要 / 批复设计
- `search_list` 返回新增 `total_count` 字段（从内联 `var totalCount = 'N';`
  抽取），便于 agent 早判 "翻完所有页是否值得"
- `aliases.common_law_aliases` 扩展：
  - 民法典侵权责任编解释（一）→ 「侵权责任编解释一/（一）」/「侵权责任编解释」
    （只有一份时去掉序号也命中）/「民法典侵权责任编解释」
  - 劳动争议司法解释（带序号 / 不带序号）→ 「劳动争议解释」/「劳动争议解释二」
    /「劳动争议司法解释二」
- `court_gongbao._infer_short_title` 现在优先用 `common_law_aliases` 第一个
  命中作为 short_title。fix：合同编通则解释原本 short_title 是 27 字超长的
  "适用《中华人民共和国民法典》合同编通则若干问题的解释"，现在简短为
  "合同编通则解释"，与社区写法对齐，agent 笔记里 `民§§合同编通则解释§24`
  长度可控

### 变更（court_gongbao 分页 + 短名优化）
- `_build_request` / `_fetch_text` 接受可选 `data` 参数：传 `data` 时切换
  到 POST + `application/x-www-form-urlencoded`，并加 `X-Requested-With:
  XMLHttpRequest`（与公报站自带 jQuery unobtrusive-ajax 一致）
- 测试套件 272 → 280：新增协议契约测试守住 GET-page1 / POST-page2 行为，
  覆盖 search_all_pages 跨页去重、空 query 拒绝、total_count 抽取、alias-first
  短名（合同编通则解释 / 侵权责任编解释一）以及新增 alias 模式

### 新增（court_gongbao 端到端 fetch）
- `chinalaw.adapters.court_gongbao` 升级为端到端可用 adapter（不再只 probe）：
  - `search_list(query, *, serial_no, page, page_size)`：解析
    `/ArticleList.html?serial_no=<code>&page=N` 列表的 `<ul id="datas">` 行，
    站底分页推断 `total_pages`；`query` 在客户端按标题子串过滤（公报站本身
    没有 `q=` 参数）
  - `fetch_detail(detail_id)`：抓 `/Details/<hash30>.html` 并切出
    `<div id="gb_content">` 内的正文 HTML（公报站 detail_id 实测是 30 位 hex，
    非标准 32/40 位摘要，正则放宽为 20–40 位）
  - `build_law_payload(detail_id, *, search_row)`：HTML → 纯文本 →
    `cleaning.canonicalize_markdown`（复用现有 `第N条` 切分），自动识别
    `法释〔YYYY〕N号` / `法发〔YYYY〕N号` 等文号写入 `document_number`；
    level 启发式：`sfjs` → `judicial_interpretation`；`sfwj` 标题含「纪要」
    → `judicial_meeting_minutes`（覆盖九民纪要等场景），含「批复」/「复函」
    → `judicial_interpretation`，其余 → `judicial_policy`；`al` →
    `guiding_case`
  - `source_hash(detail_id)`：详情正文文本的 sha256，便于换源检测 / 增量同步
- `chinalaw verify-source --source court_gongbao`：真实数据源 smoke 现已
  覆盖最高法公报，CLI choices 由 `sources.VERIFIABLE_SOURCES` 驱动
- `chinalaw fetch --source court_gongbao`：`fetch` 协议级入口现可使用最高法公报；
  `--prefer-id <detail_id>` 可绕过公报站低效标题搜索，直接按详情页 id 拉取、
  清洗、入库或写 fixture
- `sources.VERIFIABLE_SOURCES`：声明哪些源同时实装 `search_list` /
  `build_law_payload`，作为 verify-source CLI choices 的单一事实源；
  `spp_gov_cn` 仍仅有 probe，本期不进入 smoke

### 变更（court_gongbao 端到端 fetch）
- `sources._candidate_from_row` / `verify_source` 解耦 flk 的 `bbbs` 字段：
  改用通用 `_row_id()`（`bbbs` → `detail_id` → `id` 优先级），候选项同时附带
  `id` 通用主键和 `bbbs` / `detail_id` 兼容字段；这样 court_gongbao 这类
  非 flk 风格 row 也能进 verify-source pipeline 而无需在源侧伪造 `bbbs`
- `court_gongbao.search_list()` 返回的 row 保留 `serial_no` 与 `status`，确保
  `search_list → build_law_payload` 的真实流水线可以正确推断 `level`；`verify_source`
  的 law 摘要同步暴露 `level`，防止清洗层级错误被 smoke 输出隐藏
- `fetch.py` 候选主键从 FLK 私有 `bbbs` 泛化为 `id`（兼容字段 `bbbs` 保留），
  让 `detail_id` 风格的数据源复用同一套 list-matches / dry-run / to-fixture /
  入库 / article 定位流程
- 测试套件从 266 个用例扩展到 272 个用例，新增 / 扩展 `CourtGongbaoFetchTests`
  （12 例：list/detail/payload/level/HTML→text/document_number/source_hash
  各路径离线 fixture 覆盖）与 `CourtGongbaoVerifySourceTests`（3 例：
  detail_id 风格 row 通过 verify-source、verifiable sources 集合契约、
  `_row_id` 优先级），并补充 `fetch --source court_gongbao` 覆盖

### 新增
- `docs/research/2026-05-source-coverage-survey.md`：18 类样本（含民商 / 公司 /
  知产 / 数据 / 资本市场 / 反垄断 / 刑法 / 行政 / 地方 / 历史废止）实证 flk.npc
  覆盖矩阵；外部候选源（gongbao.court.gov.cn / spp.gov.cn / gov.cn /
  cac.gov.cn / csrc.gov.cn）反爬与可爬性探查；为 issue #14 / #16 提供
  数据基础与改造建议
- `LawLevel` 扩展到 11 档：新增 `judicial_meeting_minutes`（会议纪要，如九民
  纪要）/ `judicial_policy`（最高法批复 / 通知 / 复函）/ `guiding_case`
  （最高法 / 最高检指导性案例）三个值，应 issue #16；同时补 `supervisory_regulation`
  （此前 cleaning 已经在写，但 enum 漏声明，构成数据契约破裂的潜在 bug）
- `docs/CONTRACT.md §2.9` LawLevel 表同步扩展，并标注新枚举值的写入边界
  （flk 不返回这三类 flxz，由后续 court_gongbao / spp_gov_cn adapter 直接写入）
- `docs/decisions/ADR-0008-multi-source-adapters.md`：多源 adapter 决策；
  接入 `court_gongbao`（最高法公报）/ `spp_gov_cn`（最高检）两个公开源；
  暂缓 `gov.cn` / `cac.gov.cn` / `csrc.gov.cn`（SPA / 反爬阻塞）；adapter
  最小契约定义 + 暂缓 fetch 实装的边界
- `chinalaw probe --source court_gongbao`：探测最高人民法院公报站点
  （`gongbao.court.gov.cn`），覆盖文件类别包括会议纪要（如九民纪要）/ 最高法
  批复 / 司法解释 / 公报案例 / 工作报告（实测站点静态全文 HTML，无 JS 反爬）
- `chinalaw probe --source spp_gov_cn`：探测最高人民检察院站点
  （`spp.gov.cn`），覆盖最高检指导性案例 + 联合刑事司法解释（实测站点静态
  `.shtml` 全文 HTML）
- `chinalaw.adapters.court_gongbao` / `chinalaw.adapters.spp_gov_cn`：probe-only
  adapter（默认节流 500ms，遵循 ADR-0008 §3.3），`search_list` / `fetch_detail`
  留给后续 PR 按需补全
- `sources.ADAPTER_REGISTRY`：把 `if-elif` adapter 解析改为 dict 注册表，
  新源接入只需注册即可被 CLI `probe` 自动识别（CLI choices 由注册表驱动）；
  错误消息附已知源列表，便于 agent 自我纠错
- `formatters.probe_to_markdown` 在 probe 报告含 `error` 字段时显式渲染
  错误信息（HTTPError / URLError），便于在批量探测时定位失败源

### 修复
- `cleaning.FLXZ_TO_LEVEL` 4 个 mapping bug（实测 flk 真实返回值反推）：
  - `修正案 → law`：刑法修正案系列、立法法修正案、反垄断法修订决定原本落 `other`
  - `地方法规 → local_regulation`：flk 实际返回短形式 `地方法规`，但 mapping
    key 写成书面 `地方性法规`，导致约 2.2 万件地方性法规命中 `other`，
    `list --level local_regulation` 完全漏召回
  - `部门规章 → department_rule`：LawLevel 早就声明，但 mapping 缺失
  - `地方政府规章 → local_government_rule`：同上

### 变更
- 测试套件从 231 扩展到 252 个用例，新增 `CleaningLevelMappingTests`（7 例）
  / `LawLevelEnumTests`（5 例）/ `CourtGongbaoProbeTests`（3 例）/
  `SppGovCnProbeTests`（2 例）/ `SourceRegistryTests`（4 例），覆盖
  4 个 mapping bug 回归、LawLevel 契约校验、probe-only adapter 标准 shape、
  HTTPError / URLError 容错、注册表分发与 source name 标准化（dash / case 兼容）

### 新增（先前合并到 Unreleased 的条目）
- `outline --with-text`：直接返回章节内每条完整条文，省去 `outline` + `articles`
  两步流水线；与 `articles` 共用 `--no-footer` / `--compact` / `--bare` /
  `--inline` / `--arabic` / `--section` / `--with-title` 输出 flag
- `search --in-part`：限定章节文本（编/章/节），仅作用于 article_hits，可与
  `--in` 联用先按法规再按章节过滤；专为民法典 1260 条等长法的章节级精准检索；
  传入 `--in-part` 时同时抑制 law_hits 和 norm_hits，避免噪声
- `norm ingest --dry-run`：仅切分预览不入库，每条输出编号 / 字数 / 120 字预览，
  并在切分异常时显式 warning（`single_clause_large_text` / `no_numbered_clauses`）；
  存在 warning 时退出码 2，便于 agent 检测异常源材料
- `norm ingest` 切分识别 markdown 标题前缀：`## 第N条【条名】` / `### 第30条`
  / `**第一条**` 等形式现在被正确切分；非条款 markdown 标题（如 `# 文档标题`）
  跳过不混入正文，避免 130 条文本被切成 1 项的常见失败
- `cited-by 民法典:522`：扫描全库条文正文，找出引用某条法规的他法条文；
  支持中文 / 阿拉伯数字双形态匹配（`第522条` / `第五百二十二条`）；
  默认排除同部法规自引（`--include-self` 可关闭）；可与 `--in 合通解释,...`
  联用限定扫描范围；MVP 版本仅识别绝对引用（`《民法典》第522条`），
  不识别「前条」/「本法」等相对引用
- `article` / `articles` / `articles --batch`：当公开法规 `_resolve_law_row` 未命中时
  自动 fallback 到私域规范库（norm sources），即可用 `chinalaw article 九民纪要 30`
  直接取私域规范条款，不再需要切换到 `chinalaw norm clause`。命中私域规范时 payload
  增加 `via: "norm_fallback"` 标记（`law` / `article` 两层都带），便于 agent 区分来源
- `article --no-norm-fallback` / `articles --no-norm-fallback`：禁用上述 fallback，
  保持只查公开法规的旧行为；service 层等价为 `include_norm=False`
- `norm clause <name> <N>`：纯数字编号未命中显式 `number` / `number_display` / `title`
  时，按 position 兜底取"第 N 项"，对齐 `norm show` 的"项"显示语义。返回 payload
  新增 `match_strategy` 字段（`number` / `display_or_title` / `position` / `null`）便于
  agent 区分命中路径
- `search` 顶层 payload 新增 `counts: {article, law, norm_clause, norm_source, total}`，
  agent 不再需要 `len(article_hits) + len(norm_clause_hits) + ...` 自行汇总；命中
  零结果时 counts 仍存在且各字段为 0
- `search --format md` 顶部追加一行 `_命中合计 N：条文 X / 私域条款 Y / ..._` 摘要，
  让 norm 命中在长输出中也清晰可见，对齐 issue #15「search 应同时命中公开法规
  + 私域规范」的可见性诉求
- `articles --batch '民法典:557-561,568;合同编通则解释:27,55-58'`：跨多部法规
  一次取条；分隔符兼容半角 `;:` 与全角 `；：`；JSON 输出聚合 law_count /
  item_count / found_count / missing_count / failed_section_count / error_count /
  ok 与每部法规的 section；Markdown 输出
  支持 `--bare` / `--inline` / `--no-footer` / `--compact` / `--section` /
  `--with-title` 等单法已有的全部选项
- `article --bare` / `articles --bare`：只输出条文正文（多条用空行分隔），完全
  省略 markdown 标题、引用号和元信息；面向「批量取 25+ 条直接喂笔记重写」场景
- `article --inline` / `articles --inline`：每条单行 `<short_title>§<number> <text>`
  形式（多行正文压缩为单空格分隔），便于直接 grep / 拼装行内引用
- `article --section` / `articles --section`：Markdown 标题改用学术 `§N` 形式（如
  `§524`），与既有 `--arabic`（`第524条`）互斥；面向直接拼装 `民§545` 类笔记格式
- `article --with-title` / `articles --with-title`：当 `articles.title` 字段存在时
  在标题后追加 `【条名】`，便于直接复制为 `民§545【债权让与一般规则】` 形式。
  数据层缺少 title 时输出与之前完全一致
- `articles --no-footer` / `--compact`：让批量取条 Markdown 输出与单条 `article`
  共享同一套 footer 控制选项；`--no-footer` 去掉汇总头，`--compact` 在尾部追加
  单行状态 / 施行 / 核查 footer
- `scripts/chinalaw` wrapper：未 `pip install` 时的临时入口，自动定位仓库根并设置
  `PYTHONPATH`，避免每次手工 `cd ~/chinalaw-cli && PYTHONPATH=src python3 -m chinalaw …`
- `scripts/install-local` / `scripts/update-local`：为 Claude Code / Codex 等本机 agent
  安装 PATH 稳定的 `chinalaw` 命令，使用仓库内 `.venv` 避免污染系统 Python，并提供
  fast-forward 更新入口
- `README.md` 新增「安装与使用」一节，明确推荐 `pip install -e .` 路径，并给出
  wrapper 与模块入口两种回退方式
- `aliases` 别名规则补登：用户社区习惯把「关于审理民事案件适用诉讼时效制度
  若干问题的规定」称为「诉讼时效解释」，现同时登记 `诉讼时效规定` 和
  `诉讼时效解释` 两个短名

### 变更
- `[project.scripts]` 入口由 `chinalaw.cli:app` 改为 `chinalaw.cli:main`，遵循
  console_script 的标准 `main()` 约定（`main` 内部 `SystemExit(app())`），
  `python -m chinalaw` 行为不变

### 历史新增（先前合并到 Unreleased 的条目）
- `articles` 命令：同一部法规下批量定位多个条文，支持 `5,12,23-25` 形式的编号 spec
- `article --no-footer/--compact/--arabic`：Markdown 取条可压缩元数据并使用阿拉伯数字标题
- `articles <name> <spec>`：批量取条支持位置参数写法，`--numbers` 保留兼容
- `outline` 命令：列出法规条文目录和正文预览，支持 `--part` 过滤
- `search --in`：限定公开法规 / 条文检索范围，减少 agent 已知法规场景下的误召回
- 清洗阶段自动派生常用法律简称 / 司法解释 alias，读取阶段用同一规则兼容旧数据
- `pack add` 命令：agent 可将工作流中确认过的公开法条、私域条款或 reference 显式沉淀到规范包；默认要求成员可本地解析
- `ensure` 命令：本地优先批量补库，已有 populated 法规跳过，缺失 / stub 才调用 `fetch`；目录模式只读取文件名
- `fetch --force`：清洗 / alias 规则升级后，即使 `source_hash` 相同也重新 upsert
- `rebuild-clean` 命令：用当前 cleaning 规则重建已入库法规，替代 agent 直连 SQLite 或调用私有 helper
- `docs/CLEANING.md`：记录 cleaning 职责边界、版本、alias 规则、重建路径和 agent 禁止路径
- `verify-source flk_npc`：真实官方源只读 smoke，执行 probe / search / fetch-clean / article locate，用于发布前发现上游结构变化
- `norm ingest` 支持 PDF：本机存在 `pdftotext` 时可从 PDF 抽取文本并进入私域规范切条流程
- FLK 旧版 Word `.doc` 清洗支持：新版 DOCX 继续走内置解析，旧司法解释 `.doc` 在本机有 `textutil` 或 `antiword` 时自动转文本再切条
- `docs/PROJECT_CHARTER.md`：项目宪章、设计哲学、核心场景、规范来源模型、规范群 / 规范包与阶段路线图
- `docs/DEVELOPMENT_GUIDE.md`：开发总原则、模块边界、测试要求、schema 规则、文档同步与 git 纪律
- `probe` 命令与 `flk_npc` 只读探测器，可识别官方站点首页形态、主资源路径与核心栏目
- `FlkNpcAdapter` 最小 HTTP 客户端：搜索列表、法规详情、命中展示、相关推荐、相关资料和相关文件接口封装
- `sync --source flk_npc --query/--bbbs`：真实官方源同步 MVP，可下载官方 Word 文档、解析正文并落库
- `sync --source flk_npc --batch --max-pages/--page-size`：分页批量同步官方源 MVP
- `sync --source flk_npc --batch --resume --stop-after-stable-pages`：支持续跑与稳定页停止
- revision 快照开始落库，`get/status` 已可暴露基础版本信息
- `sync --source flk_npc --incremental`：基于发布日期窗口的增量同步
- `diff` 命令：按两个时点对比同一法规的条文变化
- `norm list/show/import/export/clause`：私域规范 MVP，可导入、查看、条款定位、导出并进入统一检索
- `pack list/show/add/import/export`：规范包 MVP，可导入、导出、查看、追加并解析本地已同步的法规 / 条文
- 规范包成员新增 `norm_source` / `norm_clause`，可引用私域规范来源与具体私域条款
- `pack validate`：校验规范包成员、依赖、角色和核心成员理由
- `norm ingest`：从 `txt/md/docx/pdf` 自动切分并导入私域规范
- `data/norms/acme-lending-policy.json`：示例私域规范
- `data/norms/acme-lending-policy.txt`：可被 `norm ingest` 导入的示例私域规范文本
- `data/packs/contract-validity.json`：示例规范包
- `data/packs/lending-review.json`：公开法条 + 私域规范组合的示例规范包
- `docs/AGENT_WORKFLOWS.md`：agent 调用顺序、合同审查流程和输出纪律
- `docs/DIFFERENTIATION.md`：项目与法律科技公司、商业 MCP 服务的差异化边界，以及给 Claude Code 的研究任务
- `examples/skills/contract-review/SKILL.md`：合同审查 skill 模板

### 修复
- 空白法规名不再进入 `_resolve_law_row()` 模糊匹配，避免 `article '   ' 3`
  错误返回任意法规条文
- `articles --batch` 顶层输出现在暴露 `ok` / `failed_section_count` / `error_count`，
  CLI 退出码也按顶层 `ok` 判断，避免跨法规批量中某些分组失败却被 agent 误判成功
- 清洗层通用识别独立的中文序号章节标题（如 `二、合同的订立`），防止其混入上一条正文；同时保留含 `应当/不得/可以` 等行为词的条文列项，避免误删正文
- `fetch` 在本地 alias 已能解析到同源 FLK 记录时优先使用既有 bbbs，减少简称 / alias 触发远程候选歧义
- `flk_npc` adapter 现在能明确识别官方源返回的反爬 JavaScript / HTML 页面，并给出可诊断的 `FetchSourceError`，不再伪装成 JSON 或 zip 解析错误
- 条号归一化支持插入条款号（如 `第十四条之一` / `第14条之1` / `14-1`）
- `get_law()` / `get_article()` 统一 law resolver，alias 与 `search()` 行为对齐
- 多关键词检索不再被整句短语化；短 term 组合查询自动回退到 `LIKE`

### 变更
- `flk_npc` adapter 默认对真实 HTTP 请求做轻量节流，降低批量 fetch 触发官方源反爬挑战的概率
- `HANDOFF.md` 重写为当前状态交接笔记，移除旧的 stub、69 tests、schema v6、未推 GitHub 等过期信息
- `ADR-0006` 状态更新为 Accepted，并补充 fetch 从“补缺 fixture”演进为长期按需补全入口
- `README.md` 顶部定位调整为“规范来源 CLI 内核”，并补充当前实现边界
- `docs/ARCHITECTURE.md` 重写为“当前实现 + 目标演进”结构，明确当前代码并未实现 Typer / Repository / Source Adapter 分层
- `HANDOFF.md` 的下一步路线调整为先完成 M1 正确性修复，再推进真实数据源同步
- `README.md` 增加开发规范入口
- `docs/PROJECT_CHARTER.md` 新增“战略边界”章节，明确项目不与成熟法律数据库公司正面竞争“内容规模 + 商业检索 + 远程 SaaS / MCP 接口”
- `README.md`、`docs/ARCHITECTURE.md`、`HANDOFF.md` 同步对齐“面向 AI 工作流的规范来源基础设施”定位
- `README.md`、`docs/PROJECT_CHARTER.md`、`docs/ARCHITECTURE.md` 对齐差异化文档入口，明确商业 MCP 应被视为可选上游而非复制对象
- sdist 打包范围加入 `docs` 与 `examples`
- schema 版本 `2 -> 3`，新增规范包本地存储
- schema 版本 `3 -> 4`，新增私域规范与私域规范条款本地存储
- schema 版本 `4 -> 5`，扩展规范包成员以支持私域规范引用
- schema 版本 `5 -> 6`，新增规范包依赖声明存储
- 测试套件从 20 个用例扩展到 25 个用例，覆盖插入条号、alias 和多关键词检索边界
- 测试套件从 25 个用例扩展到 27 个用例，覆盖 probe adapter 与 CLI probe
- 测试套件从 27 个用例扩展到 30 个用例，覆盖 `FlkNpcAdapter` 请求契约与 `source_hash()` 稳定性
- 测试套件从 30 个用例扩展到 34 个用例，覆盖 docx 解析、payload 组装、sync workflow 与 CLI sync
- 测试套件从 34 个用例扩展到 36 个用例，覆盖 batch sync workflow 与 CLI batch sync
- 测试套件从 36 个用例扩展到 39 个用例，覆盖 batch resume、稳定页停止与 CLI resume
- 测试套件从 39 个用例扩展到 40 个用例，覆盖 revision 快照行为与输出
- 测试套件从 40 个用例扩展到 45 个用例，覆盖 schema v2 migration、as-of/history 与 incremental sync
- 测试套件从 45 个用例扩展到 47 个用例，覆盖 diff workflow 与 CLI diff
- 测试套件从 50 个用例扩展到 55 个用例，覆盖 norm pack migration、读写和 CLI workflow
- 测试套件从 55 个用例扩展到 61 个用例，覆盖 norm source migration、读写、搜索与 CLI workflow
- 测试套件从 61 个用例扩展到 64 个用例，覆盖 norm pack 对私域规范来源 / 条款的引用、迁移与 CLI 解析
- 测试套件从 64 个用例扩展到 69 个用例，覆盖规范包依赖校验、私域文件导入和新增 CLI
- 测试套件当前扩展到 167 个用例，新增 pack add 工作流沉淀、ensure 本地优先补库、PDF 私域清洗、verify-source smoke、FLK 反爬诊断、旧版 `.doc` 清洗、批量取条、outline、alias 清洗、`search --in`、读取输出瘦身、通用章节标题清洗、`fetch --force`、本地 alias fetch、`rebuild-clean` 和 Markdown 取条压缩输出覆盖
- 测试套件扩展到 198 个用例，新增 `outline --with-text` service / CLI / 渲染 flag
  互斥与默认行为覆盖
- 测试套件扩展到 194 个用例，新增 `search --in-part` 章节级条文过滤、与
  `--in` 联用、Markdown 章节限定头与抑制 law_hits/norm_hits 行为覆盖
- 测试套件扩展到 197 个用例，新增 norm ingest markdown 标题识别、切分质量启发式
  告警、CLI dry-run 预览与 warning 退出码覆盖
- 测试套件扩展到 201 个用例，新增 `cited-by` 反向引用 service / CLI、阿拉伯到
  中文数字转换、spec 解析与 markdown 输出无重复前缀覆盖

## [0.1.1] — 2026-05-21

### 修复

- 修复 Windows 默认 cp1252 输出中文 JSON 时可能触发 `UnicodeEncodeError` 的问题，CLI 入口和 wrapper 统一启用 UTF-8。
- 修复 SQLite 初始化异常时连接未关闭导致 Windows 临时数据库文件被锁的问题。
- 增强 `scripts/install-local` / `scripts/install-local.ps1`：venv 创建失败或 pip 缺失时不让安装直接中断，自动尝试 `ensurepip`，否则安装可用的 PYTHONPATH fallback wrapper。
- GitHub Actions 新增 Ubuntu / macOS / Windows 三平台安装烟测，要求公开安装脚本可以完成 `init`、`article`、`doctor`。

## [0.1.0] — 2026-04-19

### 新增
- SQLite schema v1：`laws` / `articles` / `categories` / `law_categories` / `revisions` / `meta`
- FTS5 虚表 `articles_fts` / `laws_fts`，采用 trigram tokenizer 支持中文检索
- `chinalaw.schema` 模块：DDL 定义与版本号常量
- `chinalaw.db` 扩展：`migrate()` / `current_version()` / `set_meta()` / `get_meta()`
- `chinalaw.loader` 模块：fixture JSON → SQLite，幂等 upsert
- `chinalaw.service` 模块：`search` / `get_law` / `get_article` / `list_laws` / `status`
- 中文条款号归一化（`第七十一条` ↔ `71`）
- 2 字短查询自动 LIKE 回退（trigram ≥3 字符限制的兼容）
- `chinalaw.formatters` 模块：JSON / Markdown 双格式输出
- CLI 命令落地：`search` / `get` / `article` / `list` / `sync --fixtures` / `status`
- CLI 通用参数：`--format {json,md}` / `--db <path>`
- 内置 fixture 数据：民法典、劳动法、刑法节选共 16 条
- 测试套件（20 cases）：schema / 归一化 / loader / service / CLI 端到端
- README Quickstart + 命令状态表

### 变更
- 版本号 `0.0.1` → `0.1.0`
- classifier `Pre-Alpha` → `Alpha`
- `pyproject.toml` sdist/wheel 打包包含 `data/fixtures/`

### 未实现（待 v0.2）
- 真实数据源 adapter（flk.npc.gov.cn / gov.cn 政策文件库）
- 增量同步 `chinalaw sync`（真实抓取）
- 分类树构建
- Revision（法规修订版本）管理与历史比对

---

## [0.0.1] — 2026-04-19（初始骨架）

### 新增
- 项目骨架初始化
- Apache-2.0 授权
- CLI 命令占位：`search` / `get` / `article` / `list` / `sync` / `status`
- SQLite 本地存储层占位
- 领域模型：`Law` / `Article` / `Category` / `Revision`
- 文档：README、NOTICES、OPEN_SOURCE_CHECKLIST、PROGRESS、ARCHITECTURE、HANDOFF
