# chinalaw-cli 全面代码审查报告

> **封版说明（2026-08-06）**：此前声明未覆盖的 security、fetchpipe、全部
> adapters、textproc、MCP、packaging/CI 已完成补审；原 26 条 low 候选也已逐项
> 核对。封版结论、复现证据和最终统计见
> `docs/FULL_AUDIT_20260806_COMPLETION.md`，结构化增量见
> `docs/FULL_AUDIT_20260806_COMPLETION.findings.json`。本文件保留原始 2026-07-26
> 审计记录，以下“尚未完成/全部 TLS”等历史表述若与封版补充冲突，以封版补充为准。

- **审查对象**：chinalaw-cli（https://github.com/nh59yytyd5-dev/chinalaw-cli），基点提交 `7e20197`
- **审查分支**：`review/full-audit-20260726`
- **审查窗口**：2026-07-26 ~ 2026-07-30
- **测试基线**：`648 passed, 33 skipped, 23 subtests passed`（41.56s，全量离线）
- **审查方法**：16 维度并行审查工作流 → 每条 high/medium finding 由独立「对抗复核员」agent 实测复现（复核员的任务是尽力**反驳**该 finding）→ 完整性批判员复盘盲区 → 第二轮对盲区做直接复现验证

> 本报告是审查工作的完整存档。工作区在审查前已存在的未提交安装脚本修复（install-local / setup-agent 等）不属于本次审查产物。

## 覆盖范围与局限（诚实声明）

16 个计划维度中，**8 个完成了「发现→对抗复核」全流程**：service（核心业务）、cli、formatters（输出/模型）、datalayer（数据层）、normlayer（规范层/时间效力）、tests（测试体系）、direction（文档/方向）、dataquality（出厂数据）。

**8 个维度两轮均未能运行**：security（脚本/安全专项）、fetchpipe（抓取管线）、adaptersA/B/C（全部 15 个 adapter）、textproc（文本解析）、mcp、packaging（打包/CI）。第一轮因子 agent 网络/鉴权故障失败，第二轮续跑因组织侧禁用订阅访问全部失败。这部分恰是仓库近半代码量（fetch/adapters 约 8000 行）。

针对该盲区，完整性批判员做了快速抽查，审查主会话对关键点做了**直接复现验证**（见「四、盲区与第二轮直接复验」）：MCP 崩溃、`departmental_rule` 枚举污染扩散、`find_cited_by` IndexError 三项均复现确认；网络层 TLS 与 alias_agent 端点两项为正面结论。但 fetch/adapters 全量通读、scripts 安全专项、packaging/CI 专项**至今未完成**，本报告不声称这些面已覆盖。

## 统计总览

| 类别 | 数量 |
| --- | --- |
| 已确认 high | 8 |
| 已确认 medium | 29 |
| 已确认 low | 6 |
| **已确认合计** | **43** |
| 对抗复核驳回 | 1 |
| low 候选（未经对抗复核） | 26 |
| 完整性批判盲区 | 6（其中 3 项已直接复现确认） |

已确认 finding 按维度分布：normlayer 6、direction 8、service 5、cli 5、datalayer 5、tests 5、dataquality 5、formatters 4。

---


## 一、High 严重问题（8 条，均已实测复现确认）


### [H1] 条款号归一化把「第X条第Y款/项」静默算成另一个条号，导致返回错误条文正文

- **位置**：`src/chinalaw/service.py:105`
- **分类 / 维度**：正确性 / 核心业务逻辑

**缺陷描述**

normalize_article_number 对带款/项后缀的常见引用格式产生系统性错误归一。两个根因：(1) _number_like_to_arabic 只剥离「第/条/项」后用 re.sub(r"[^0-9]","",s) 把剩余数字直接拼接，于是「第10条第3项」→"103"、「第5条第2项」→"52"；(2) _chinese_to_arabic 对不认识的字符（款/项等）直接 continue 跳过，且后续数字覆盖 current，于是「第五百七十七条第一款」→"571"、「第七十一条第二款」→"72"、「第十条第三项」→"13"。端到端复现：在含第571/577条的库上执行 get_article(db, "民法典", "第五百七十七条第一款")，返回的是第571条的正文——法律检索工具静默给出错误法条文本，且「第N条第N款」是律师/AI agent 最常见的引用写法之一。此外该函数对无法解析的输入（如 "abc"、"附则一"）返回残余字符串或错误数字（"附则一"→"1"）而非空串，进一步放大误命中风险。

**证据 / 复现**

```text
>>> normalize_article_number("第五百七十七条第一款") -> '571'
>>> normalize_article_number("第七十一条第二款") -> '72'
>>> normalize_article_number("第十条第三项") -> '13'
>>> normalize_article_number("第10条第3项") -> '103'
>>> service.get_article(db, "民法典", "第五百七十七条第一款")["article"] -> number='571', text='第571条内容：要约的撤回。'
代码：_number_like_to_arabic: digits = re.sub(r"[^0-9]", "", s)（跨条/款/项边界拼接数字）；_chinese_to_arabic: `if v is None: continue` + `current = v`（未知字符跳过、后续数字覆盖）
```

**修复建议**

在 normalize_article_number 入口先用正则剥离「第N款/第N项/第N目」尾缀（如 re.sub(r"第[0-9〇零一二三四五六七八九十]+[款项目]$","",s) 循环去尾），只保留条号部分再归一；_number_like_to_arabic 与 _chinese_to_arabic 应改为对整体做 fullmatch 严格解析，遇到无法识别的字符时返回 ""（解析失败）而不是跳过或返回残余原文，让上层走 article_null / norm fallback 诊断路径而不是静默命中错误条文。

**对抗复核结论**：确认为真（复核后 severity=high）

> 完全复现，无调用方防护。单元层：normalize_article_number("第五百七十七条第一款")='571'、("第七十一条第二款")='72'、("第十条第三项")='13'、("第10条第3项")='103'、"abc"='abc'、"附则一"='1'，与 finding 证据逐条一致。根因核实无误：_number_like_to_arabic（service.py:95-110）剥离第/条/项后对剩余串做 re.sub(r"[^0-9]","",s) 跨边界拼接数字；_chinese_to_arabic（service.py:113-129）对款/项等未知字符 `if v is None: continue` 跳过，且 `current = v` 使款/项序数覆盖条号个位（五百七十七→570+7，随后"一"覆盖 current=7 → 571）。端到端：在含 571/577 两条的临时 DB 上，get_article(db,"民法典","第五百七十七条第一款") 返回 number='571'、text='第571条内容：要约的撤回。'，无任何 warning；get_articles 同路径亦返回 found=True/number='571'。调用链核实：cli.py article 命令把 args.number 原样传入，_get_article_internal（service.py:2032-2097）直接 WHERE number = norm 查询，主 articles 路径无 number_display 兜底（仅 norm fallback 有）。唯一小偏差：阿拉伯数字+"款"（如"第577条第1款"）归一为 '5771款' 查不到 → 安全返回 article=None，非错误命中；但这不影响主论断——全部中文数字写法（款/项）及阿拉伯+"项"写法均静默命中错误条文，而"第N条第N款"正是律师/agent 最常见引用格式，在民法典这类 1260 条的长法上错误条号几乎必然存在从而必然错误命中。法律检索工具静默返回错误法条正文，维持 severity=high。


### [H2] 批量/增量同步把全部逐页网络抓取与写库包在单个未提交写事务中：中途任何异常导致本次全部已入库数据与断点进度整体回滚，且长期持有写锁阻塞其他进程

- **位置**：`src/chinalaw/sync.py:208`
- **分类 / 维度**：数据 / 数据层

**缺陷描述**

_sync_batch 用一个 `with connect(db_path) as conn:`（sync.py L208）包住整个 while 分页循环。循环体内每页都要调用 adapter.list_laws/search_list 和逐条 adapter.build_law_payload 发起网络请求，而第一条法规写入后 Python sqlite3（默认 isolation_level）即隐式开启写事务，直到 with 块正常退出才 commit（db.py connect L37-47：异常时 rollback）。后果有三：(1) 长批量同步（可达数百页、每页多次网络请求）中任何一次网络超时/解析异常都会把本次已写入的所有法规、articles、FTS 以及 `source:*:next_page` 等断点 meta 一起回滚——数小时抓取全部作废，且 `--resume` 因断点 meta 也被回滚而无法从崩溃处续传（已实测：事务内 set_meta 后抛异常，重开库 next_page 为 None）；(2) 写事务存续期间持有 SQLite 写锁，其他进程（另一个 sync、load、MCP server 写操作）在默认 5 秒 timeout 后直接报 `sqlite3.OperationalError: database is locked`（已实测 5.2s 报错），与『多进程同时 sync』场景直接冲突；(3) WAL 文件在长事务期间无法 checkpoint，持续膨胀。非批量的 sync_source（L86）同样把 build_law_payload 网络调用放在写事务内，只是规模较小。

**证据 / 复现**

```text
sync.py L208-282:
    with connect(db_path) as conn:
        migrate(conn)
        ...
        while True:
            ...
            search_result = adapter.list_laws(...)  # 网络请求，位于已打开的写事务内
            for row in rows:
                payload = adapter.build_law_payload(law_id, search_row=row)  # 网络请求
                changed, article_count = _load_if_changed(conn, payload)  # DML，开启/延续写事务
            ...
            set_meta(conn, f"source:{source}:next_page", str(current_page + 1))

db.py L37-47: connect() 仅在 with 正常退出时 conn.commit()，异常时 conn.rollback()。
实测输出：T1 next_page after crash: None；T2 second writer error after 5.2s: database is locked
```

**修复建议**

以页为事务边界：每处理完一页（写入该页法规 + 更新 next_page/last_page 等断点 meta）立即 conn.commit()，网络抓取尽量移到事务外（先抓完一页的 payload 再统一写库）。这样崩溃最多损失当前页，`--resume` 可从最后提交页续传，写锁持有时间也从小时级降到毫秒级。

**对抗复核结论**：确认为真（复核后 severity=high）

> 复现成功，finding 所有事实陈述均准确。(1) 代码核实：db.py connect()（L37-47）仅在 with 正常退出时 commit、异常时 rollback；整个写路径只有两处 commit（db.py L42、L95），后者在 schema 已最新时被 L79-80 early-return 跳过，稳态下 _sync_batch（sync.py L208）整个 while 分页循环确为单一未提交写事务，loader.load_law_from_dict 无任何 commit。(2) 实测 T1：用 stub adapter 第 1 页写入 3 部法规、第 2 页 list 抛 TimeoutError，重开库后 laws=0、next_page=None、last_page=None，_resolve_resume_page 回落到第 1 页——已入库数据与断点 meta 全部回滚，--resume 无法从崩溃处续传（而循环内逐页写 next_page 的设计意图正是页级续传，被事务边界完全废掉）。(3) 实测 T2：sync 线程写入第 1 页后在模拟慢网络调用中持有写事务，第二个写连接（默认 5s timeout，src/ 全库未配置 busy_timeout）5.2 秒后报 sqlite3.OperationalError: database is locked；WAL 只保护读者，其他写进程（并发 sync/load/MCP 写操作）在整个长事务期间直接报错。(4) CLI 核实：cli.py L394 --max-pages 无默认值（None=无上限直到页耗尽），_handle_sync（L1559）直接调 sync_source 无任何调用方防护，且 rollback 发生在 connect() 上下文管理器内部，调用方 catch 也无法挽回。反驳点均不成立。严重度维持 high：批量同步是主数据摄入路径，外部 API 网络抖动常见，跑得越久损失越大且必然全损，同时小时级独占写锁与多进程使用场景直接冲突；建议修复方向（以页为事务边界、抓取移出事务）成立。


### [H3] 增量同步因 --max-pages / --stop-after-stable-pages 提前停止时，仍把 last_incremental_to 水位推进到 end_date，窗口内未同步的页被后续增量永久跳过，造成静默数据缺口

- **位置**：`src/chinalaw/sync.py:166`
- **分类 / 维度**：正确性 / 数据层

**缺陷描述**

_sync_incremental 把 `source:flk_npc:last_incremental_to = end_date` 放进 extra_meta（L165-168），_sync_batch 在每同步完一页后就无条件写入 extra_meta（L264-266）。当增量同步与 `--max-pages`（CLI 明确支持组合，cli.py L393-397 与 L414-418 可同时给出）或 `--stop-after-stable-pages` 联用而提前停止（stop_reason=max_pages/stable_pages，L215-217、L274-280）时，日期窗口 [start_date, end_date] 内尚有大量页未抓取，但水位已声称覆盖到 end_date 并随事务提交。下一次增量 _resolve_incremental_start（L355-357）从 `last_incremental_to - overlap_days`（默认仅 1 天）开始，窗口内那些未同步的法规不会再被任何后续增量触达——除非用户手动指定 --published-from 回补。这是无任何告警的覆盖缺口，对『本地库即权威检索基线』的产品定位是数据正确性问题。

**证据 / 复现**

```text
sync.py L165-168:
        extra_meta={
            "source:flk_npc:last_incremental_from": start_date.isoformat(),
            "source:flk_npc:last_incremental_to": end_date.isoformat(),
        },
L264-266: if extra_meta: for key, value in extra_meta.items(): set_meta(conn, key, value)  # 每页写一次，早停也已提交
L215-217: if max_pages is not None and pages_synced >= max_pages: stop_reason = "max_pages"; break
L355-357: stored = get_meta(conn, f"source:{source}:last_incremental_to"); if stored: return max(date.fromisoformat(stored) - timedelta(days=overlap_days), date.min)
```

**修复建议**

仅当 stop_reason == "no_rows"（窗口真正抓完）时才把 last_incremental_to 推进到 end_date；因 max_pages/stable_pages 早停时保持旧水位不动（或记录 next_page 供窗口内续传），并在返回结果里显式标出 `window_exhausted: false`，提示需要续跑。

**对抗复核结论**：确认为真（复核后 severity=high）

> 已用最小用例完整复现，finding 所有事实陈述均准确。复现（/tmp/repro_watermark.py，fake adapter + 临时 db，经 sync_source 公开入口触发）：窗口 [2026-07-01, 2026-07-26] 内有 2 页数据（law-A p1、law-B p2），第一次 `incremental=True, max_pages=1` 输出 `stop_reason=max_pages, pages_synced=1, laws_loaded=1`，此时 meta `source:flk_npc:last_incremental_to` 已被推进到 2026-07-26（窗口仅同步 1/2 页）；第二、三次增量的窗口被解析为 `2026-07-25 ~ 2026-07-26`（水位减默认 overlap_days=1），均 `stop_reason=no_rows, laws_loaded=0`，law-B（2026-07-05 发布、在首个窗口内）永久未入库。逐项排除了可能的防护：(1) cli.py L393-418/L1567-1571 允许 --incremental 与 --max-pages/--stop-after-stable-pages 任意组合且直接透传，无互斥校验；且 L412 的 help 文本把 --stop-after-stable-pages 描述为「增量同步辅助」，是在主动推荐这种组合；(2) sync.py L159 `_sync_incremental` 调 `_sync_batch` 时硬编码 resume=False，sync_source 的 incremental 分支也不转发 resume，续传机制对增量不生效；(3) formatters.py L1633-1638 `sync_to_markdown`（默认人类可读输出）只显示 laws_loaded/articles_loaded/titles，连 stop_reason 都不展示，早停对默认输出完全不可见，「静默」成立；(4) adapters/flk_npc.py L335-353 真实源按 gbrq（公布日期）DESC 排序，max_pages 早停漏掉的正是窗口内较早发布的法规，恰好落在下次窗口 [last_to - overlap, today] 之外，缺口必然永久化；(5) tests/test_core.py L4277 现有测试正是以 incremental+max_pages=1 组合为「正常用法」编写的，只断言窗口日期，未覆盖水位语义。严重度维持 high：静默、永久性的本地库覆盖缺口，触发路径是 CLI 明确支持且 help 文本鼓励的用法，仅能靠用户手动 --published-from 回补但用户无从得知需要回补，与「本地库即权威检索基线」的产品定位直接冲突。建议修复方向与 finding 一致：仅 stop_reason=="no_rows" 时推进 last_incremental_to，早停时保留旧水位并在结果中显式标注窗口未耗尽。


### [H4] adapter 测试固化了不在 LawLevel 枚举中的 level 值 departmental_rule，导致部门规章 level 过滤被拼写切裂

- **位置**：`tests/test_nfra_gov_cn.py:73`
- **分类 / 维度**：数据 / 测试体系

**缺陷描述**

LawLevel 枚举声明的受控值是 department_rule（src/chinalaw/models.py:35），cleaning.FLXZ_TO_LEVEL 也把"部门规章"映射为 department_rule；但 nfra_gov_cn.py:318、csrc_gov_cn.py:739、gov_xzfgk.py:566 三个 adapter 输出的是 departmental_rule，不在枚举中。三个对应测试（test_nfra_gov_cn.py:73、test_csrc_gov_cn.py:215、test_gov_xzfgk.py:188）不但没有发现这个契约破裂，反而用 assertEqual 把非法值固化为预期行为。实测后果：从 nfra/csrc/gov.cn 抓取入库的部门规章用 `chinalaw laws --level department_rule` 查不到（返回 0 条），与 flk 来源的同级法规被切成两个拼写；formatters.py:1908 还专门为错误拼写补了显示映射，进一步掩盖问题。test_core.py 已有 test_jiancha_fagui_value_is_in_law_level_enum 记录过完全同类的"监察法规"契约破裂 bug，但守门测试 test_all_flxz_values_map_to_declared_law_levels 只覆盖 FLXZ_TO_LEVEL，未覆盖 adapter 直接输出的 level。

**证据 / 复现**

```text
tests/test_nfra_gov_cn.py:73 `self.assertEqual(payload["level"], "departmental_rule")`；tests/test_csrc_gov_cn.py:215、tests/test_gov_xzfgk.py:188 同型断言。运行复现（.venv/bin/python，mock _fetch_text 后 build_law_payload 并入库）：`adapter emits level: departmental_rule / filter department_rule -> 0 / filter departmental_rule -> 1`。LawLevel 校验：`"departmental_rule" in {e.value for e in LawLevel}` → False。
```

**修复建议**

1) 三个 adapter 的 level 改为枚举值 department_rule，同步修改三处测试断言；2) 新增跨 adapter 守门测试：对每个注册 adapter 的 build_law_payload 输出断言 level ∈ {e.value for e in LawLevel}（仿照 test_all_flxz_values_map_to_declared_law_levels）；3) 增加一次性数据归一（migrator 或 rebuild-clean 规则）把已入库的 departmental_rule 更新为 department_rule；4) 删除 formatters.py 中为错误拼写补的显示映射。

**对抗复核结论**：确认为真（复核后 severity=high）

> 尽力反驳失败，finding 的每一项事实主张均被独立证实，且全链路无任何防护或归一化可以缓解：1) 契约事实：src/chinalaw/models.py:35 枚举值为 department_rule，src/chinalaw/cleaning.py:48 将"部门规章"映射为 department_rule；而 nfra_gov_cn.py:318、csrc_gov_cn.py:739、gov_xzfgk.py:566 三处硬编码 level="departmental_rule"，三个测试（test_nfra_gov_cn.py:73、test_csrc_gov_cn.py:215、test_gov_xzfgk.py:188）用 assertEqual 固化该非法值。2) 无防护核查：adapter 走 cleaning.canonicalize(source_kind="markdown") → _canonicalize_local_text_payload（cleaning.py:673）对 level 原样透传、不校验枚举；loader.py:211 原样写库；schema.py 的 laws.level 仅 TEXT NOT NULL 无 CHECK；service.list_laws（service.py:2456-2458）过滤是精确 `level = ?` 字符串匹配。3) 端到端复现（mock _fetch_text 构造 payload、写入临时 sqlite、service.list_laws 查询）输出：`adapter emits level: departmental_rule / in LawLevel enum: False / filter department_rule -> 0 / filter departmental_rule -> 1`，与 finding 的实测后果逐字一致。4) 掩盖证据甚至比 finding 所述更强：formatters.py:1903 _LEVEL_LABELS 只收录了错误拼写 "departmental_rule": "部门规章" 而未收录正确枚举值 department_rule，即 resolve 展示层是按错误拼写定制的；test_core.py:1981/1988 确实记录过同类"监察法规"契约破裂 bug 且守门测试只覆盖 FLXZ_TO_LEVEL 不覆盖 adapter 输出。严重度维持 high：同一效力层级被切成两个拼写导致三源部门规章在 --level department_rule 过滤下静默返回 0 条（法规检索工具的静默漏检），且被测试断言与显示映射双重固化。


### [H5] 数据安全法 fixture 整条缺失第21条、第42条，且第21条第2款被错误并入第20条文本

- **位置**：`data/fixtures/data_security_law.json:374`
- **分类 / 维度**：数据 / 出厂数据质量

**缺陷描述**

《数据安全法》（2021）共55条，该 fixture 只有53条：第21条（数据分类分级保护制度/重要数据目录，全法最高频被引条文之一）与第42条（政务数据开放目录/开放平台）整条缺失。更严重的是，真实第21条第2款『关系国家安全、国民经济命脉、重要民生、重大公共利益等数据属于国家核心数据，实行更加严格的管理制度』被错误拼接进第20条（人才培养条款）文本末尾，且第20条的 part 标注为『第二章 数据安全与发展』，而该款实际属于第三章。后续条目的 position 已按缺失后的序列重排（如第22条 position=21），说明是抓取/切分阶段丢条。recommended_corpus.json 中 p1-data-security-law 声称『现有 fixture 已含全文（53 条）』也随之失实。agent 用 `chinalaw article 数据安全法 21` 得到空结果，引用第20条则会把核心数据条款归到错误条号——对以引用核对为核心卖点的工具属于正常使用即出错。

**证据 / 复现**

```text
"number": "20", "text": "国家支持教育、科研机构和企业等开展数据开发利用技术和数据安全相关教育和培训……促进人才交流。\n关系国家安全、国民经济命脉、重要民生、重大公共利益等数据属于国家核心数据，实行更加严格的管理制度。"（第2段实为第21条第2款）；编号扫描输出：data_security_law.json missing=[21, 42]，count=53，max=55；第22条 "position": 21
```

**修复建议**

从 flk.npc.gov.cn 重新抓取《数据安全法》全文，恢复第21条（三款完整）与第42条，把误并入第20条的核心数据款移回第21条并修正 part/position；同步更正 recommended_corpus.json 中『已含全文（53 条）』为 55 条。建议在 CI/audit 中增加『条号连续性 + 条数与官方一致』校验，防止同类丢条再次静默出厂。

**对抗复核结论**：确认为真（复核后 severity=high）

> 全部关键事实均已复现，无审查员误读，无有效防护。(1) 直接解析 /Users/huoxihuo/chinalaw-cli/data/fixtures/data_security_law.json：articles count=53，编号 min=1/max=55，missing=[21, 42]，与 finding 的编号扫描输出完全一致。(2) 第20条 text 确为两段：第一段是人才培养条款，第二段"关系国家安全、国民经济命脉、重要民生、重大公共利益等数据属于国家核心数据，实行更加严格的管理制度"确系真实法第21条第2款，且该条 part 标注"第二章 数据安全与发展"（该款实属第三章）；第21条其余各款的标志语句（数据分类分级/重要数据目录）与第42条标志语句（政务数据开放目录/开放平台）在全 fixture 中 grep 零命中，证明系整条丢失而非移位。(3) position 重排确认：第22条 position=21，第55条 position=53。(4) 端到端复现：fixture 是 init/sync --fixtures/ensure 的生产数据源（src/chinalaw/loader.py FIXTURES_DIR=builtin_data_dir("fixtures")），用临时库 `chinalaw --db <tmp> init` 后，`article 数据安全法 21` 返回 found:false/article:null/reason:article_null；`article 数据安全法 42` 同样 found:false；`article 数据安全法 20` 原样返回含核心数据款的错误拼接文本，无任何警示。(5) data/recommended_corpus.json p1-data-security-law 条目 notes 确为"现有 fixture 已含全文（53 条）"，与真实 55 条不符。(6) src/chinalaw/audit.py 无条号连续性/条数校验，status 亦不暴露。唯一轻微缓解：第21/42条查询非静默失败，返回中带 outline+fetch --force 重抓 hint，agent 有自愈路径；但第20条的错误并入是静默错误数据——核对"第20条含核心数据条款"会得到假确认，核对"第21条"会得到假否定，对以引用核对为核心卖点的 P1 语料法律属正常使用即出错。维持 severity=high。


### [H6] 网络安全法（2025修正）fixture 缺失第25条，81条文本只有80条

- **位置**：`data/fixtures/cybersecurity_law.json:407`
- **分类 / 维度**：数据 / 出厂数据质量

**缺陷描述**

该 fixture 为2025-10-28修正、2026-01-01施行的《网络安全法》合并文本，末条为第81条（『本法自2017年6月1日起施行』），条号应连续1-81，但第25条整条缺失（仅80条）。按前后文比对：fixture 第23条=修正前第21条（等级保护）、第24条=修正前第22条（产品服务安全）、第26条=修正前第24条（真实身份），缺失的第25条对应修正前第23条『网络关键设备和网络安全专用产品……安全认证合格或者安全检测符合要求后，方可销售或者提供』。第26条 position=25 也证明是抓取阶段丢条后顺排。中国法律的『修正』合并文本条号必然连续，正式文本不存在断号；recommended_corpus.json p1-cybersecurity-law 『已含全文（80 条）』的说法同样失实。查询网安法第25条会得到空结果，agent 可能据此错误断言该条不存在。

**证据 / 复现**

```text
编号扫描输出：cybersecurity_law.json missing=[25]，min=1 max=81 count=80；"number": "26" 条目 "position": 25；第24条与第26条正文分别对应修正前第22、24条，中间缺网络关键设备/专用产品认证检测条款
```

**修复建议**

从官方来源重新抓取2025修正后《网络安全法》全文，补齐第25条并重排 position；更正 recommended_corpus.json 条数说明为81条；同样纳入条号连续性出厂校验。

**对抗复核结论**：确认为真（复核后 severity=high）

> 全部事实独立复现为真。(1) 编号扫描：data/fixtures/cybersecurity_law.json 共80条，number min=1 max=81，missing=[25]，position 连续1..80（"number":"26" 的 position=25），与丢条后顺排一致。(2) 铁证：fixture 自身第63条明文引用「违反本法第二十五条规定，销售或者提供未经安全认证、安全检测……的网络关键设备和网络安全专用产品的……」，证明修正后文本第25条存在且正是认证检测条款；而该条正文（「方可销售」等）全文件检索不到，也未并入第24条（第24条232字符仅为产品服务安全义务）——排除「2025修正删除该条」假设。末条第81条「本法自2017年6月1日起施行」证明完整文本应为81条。(3) CLI 复现：临时库 init 后 `chinalaw article 网络安全法 25` 返回 article:null, found:false, reason:article_null；第24条正常命中。(4) recommended_corpus.json p1-cybersecurity-law notes「现有 fixture 已含全文（80 条）」确认失实。唯一缓解：article 未命中时 CLI 返回 hint 建议 outline 核对/联网重抓，不会直接断言条文不存在，最坏后果略有防护；但离线不可重抓，outline/audit/cite-check 仍受误导，第63→25条法内引用链断裂。出厂 P1 现行法律基线缺整条实体条款且元数据宣称全文，维持 high。


### [H7] 指定 --as-of 后审计完全跳过废止核查：事实日期晚于法规废止日仍报 ok=true 零问题

- **位置**：`src/chinalaw/audit.py:772`
- **分类 / 维度**：正确性 / 规范层（时间效力）

**缺陷描述**

_status_issue 中只要 as_of 非空就直接 return None，从不把 as_of 与 law 的 repealed_at 比较。当引用的法规在事实日期之前已被废止（如事实发生于 2023 年而该法 2021-01-01 废止），get_article_as_of 仍会命中旧版本快照并成功解析，审计结果 ok=true、error_count=0、无任何 issue。更矛盾的是：同一文本不带 --as-of 审计会报 error repealed_law_without_as_of 并提示『请指定 --as-of』——用户照做（填真实事实日期）后错误反而消失。对『时间效力核查』这一核心功能而言，这会让 agent 把已废止法规当作事实日期的有效依据（如民法典时代仍引用合同法条文），正常使用即产生错误结果。

**证据 / 复现**

```text
audit.py L770-773:
    if status in {"current", "active"}:
        return None
    if as_of:
        return None
复现（合成 repealed 法 + 1999 版本快照，repealed_at=2021-01-01）：
no as_of -> ok: False issues: [('error', 'repealed_law_without_as_of')]
as_of=2023-05-01 (after repeal) -> ok: True issues: []
as_of=2020-05-01 (before repeal) -> ok: True issues: []
```

**修复建议**

在 _status_issue 中保留 as_of 分支但增加比较：解析 law.get("repealed_at")，若 repealed_at 非空且 as_of >= repealed_at，返回 error（如 repealed_before_as_of，提示应改用承继法/新法并给出 relation/applicable 建议命令）；as_of < repealed_at 时才静默通过。注意 audit.py 自己的 _compact_law 丢弃了 repealed_at，需在压缩前用 service 返回的完整 law 判断（当前调用点已是完整 law，可直接实现）。

**对抗复核结论**：确认为真（复核后 severity=high）

> 已完整复现，finding 属实。复现方法：构造合成库（laws 表 status=repealed、repealed_at=2021-01-01，articles 表含第52条，revisions 表含 1999 年版 snapshot_json），对文本「依据《合同法测试》第五十二条」运行 audit_text 三次，输出与 finding 的 evidence 逐字吻合：no as_of -> ok: False issues: [('error','repealed_law_without_as_of')]；as_of=2023-05-01（废止后）-> ok: True issues: []；as_of=2020-05-01（废止前）-> ok: True issues: []。代码层面确认无任何防护：(1) audit.py L772-773 `if as_of: return None` 无条件短路所有状态检查，从不读取 repealed_at；(2) service.py `_select_revision_as_of`（L872-881）只做 effective_at <= as_of 的下界筛选，无废止日上界，废止后日期仍命中旧版快照；(3) `_get_article_internal` as_of 分支（L2066-2087）返回的 payload 不含任何 warning/time_effect 字段（复现中实测为空）。误导闭环成立：不带 as_of 时错误消息明确指示「请指定 --as-of」，用户按提示填入真实事实日期（晚于废止日）后错误消失、报 ok=true，agent 会把已废止法规当作事实日期的有效依据。无既有测试断言该行为（tests 中无 repealed_law_without_as_of 引用），无文档声明此为有意设计。suggested_fix 可行性亦核实：`_status_issue` 调用点（audit.py L596）收到的完整 law payload 实测含 status=repealed 与 repealed_at=2021-01-01（as_of 路径经 _snapshot_to_law L896、当前路径经 _row_to_law 均保留），且 _compact_law（L868-879）确实丢弃 repealed_at，finding 对实现细节的描述准确。唯一可商榷点是修复时报 error 还是 warning（新旧法过渡条款下旧法偶有余留适用），但当前零信号静默通过对审计工具而言明确错误。核心功能在正常使用路径下静默产出错误的 ok=true，维持 high。


### [H8] trace 对同条号但实质修正的条文输出 status="deleted"：现行仍存在的条文被断言为已删除

- **位置**：`src/chinalaw/trace.py:534`
- **分类 / 维度**：正确性 / 规范层（时间效力）

**缺陷描述**

trace_article_as_of 用固定阈值 confidence>=0.72 决定 ok，不满足时顶层 status 一律置为 "deleted"、to.article 置 None。而 _trace_candidate_score 对『目标版本存在完全相同条号』只加 0.05 的 number_bonus，导致任何实质性修正（中国法极常见，如刑法修正案对条文的扩充）文本相似度掉到 0.72 以下后，即使目标版本中存在同号条文、且候选列表第一名就是该条并被标为 amended，顶层结论仍是 deleted。真实数据复现：追溯刑法第133条之一（危险驾驶罪，2015 修正案扩充文本，2011 与 2023 版本均存在）从 2012 到 2024，输出 ok=False、status="deleted"、confidence=0.5655，而同一 payload 的 candidates[0] 是 133-1 且 status="amended"——自相矛盾。markdown 输出首行即『状态：deleted』，MCP agent 消费 JSON 时会得到『该条已删除』的错误法律事实。

**证据 / 复现**

```text
trace.py L533-534:
    ok = confidence >= 0.72
    status = best.get("status") if ok and best else "deleted"
复现（内置刑法 16 个版本快照）：
trace_article_as_of(db, "刑法", "133-1", from_as_of="2012-01-01", to_as_of="2024-06-01")
-> ok: False, status: deleted, conf: 0.5655
   candidates[0]: number=133-1, conf=0.5655, status=amended（2011 两款 vs 2023 四项三款文本，相似度 0.4858）
```

**修复建议**

两点：(1) 当目标版本中存在与源条文归一化条号完全相同的条文时，应将其视为强证据——直接选为 target（status=amended/unchanged），或至少大幅提高 number_bonus/单独走同号分支，仅当同号条文相似度极低且另有高相似候选时才提示可能重编号；(2) 低置信时顶层 status 不应断言 "deleted"，改为 "uncertain"/"not_matched" 之类的中性值，"deleted" 仅在目标版本确认无同号条文且所有候选相似度都低时给出。

**对抗复核结论**：确认为真（复核后 severity=high）

> 复现成功，finding 属实。在 /tmp 复制用户库（~/.chinalaw/chinalaw.db，刑法 16 个版本快照齐全）后运行 trace_article_as_of(db, "刑法", "133-1", from_as_of="2012-01-01", to_as_of="2024-06-01")，输出与 finding 完全一致：ok=False、status="deleted"、confidence=0.5655、to.article=None，而同一 payload 的 candidates[0] 正是 133-1（status="amended", similarity=0.4858），evidence 中甚至含"条号未变化"——自相矛盾坐实。核对两版文本确认属实质修正而非删除：2011 版为两款（追逐竞驶/醉驾），2023 版扩为四项三款（增校车超载超速、危化品运输），条文现行有效。代码层面：trace.py L533-534 `ok = confidence >= 0.72; status = best.get("status") if ok and best else "deleted"`，L335-339 同号仅加 0.05 number_bonus，任何相似度低于约 0.65 的实质修正（中国法修正案模式下常见）都会触发假"deleted"，非边角场景。复核了所有缓解措施，均不足以推翻：(1) JSON 有 ok=False 和 warning="low_confidence_or_deleted"，但字段名为 status 的主语义字段仍断言 "deleted"，MCP/agent 按字面消费即得错误法律事实；(2) markdown 输出（formatters.py L1067）打印"状态：deleted"（在第 6 行而非 finding 所称首行，此处 finding 略有夸大，但实质成立），虽附警告与"目标：未达到可信阈值"提示；(3) metadata.py L538 的 common_misuse 仅为软性提示；(4) tests/test_core.py 中 trace 测试均只覆盖 ok=True 路径，无测试锁定低置信输出 "deleted" 的行为，说明这不是被审慎设计的契约。且函数 docstring 自称"returns low-confidence candidates instead of guessing"，而 status="deleted" 恰是一次猜测，与自身设计意图相悖。本项目核心场景是 AI 引用核对/法律事实供给，对现行条文输出"已删除"直接产出错误法律结论，维持 severity=high。


## 二、Medium 问题（29 条，均已实测复现确认）


### [M1] as_of 版本回放：revision 无 snapshot_json 且 content_hash 等于当前法时返回空条文，get_article_as_of 误报条文不存在

- **位置**：`src/chinalaw/service.py:919`
- **分类 / 维度**：正确性 / 核心业务逻辑

**缺陷描述**

_build_law_from_revision_snapshot 的 elif 分支（revision.get("content_hash") == law_row["source_hash"]）语义上表示「选中的修订版本就是 articles 表里的当前版本」，但代码却构造 article_count=0、articles=[] 的空法规（articles_coverage 还被派生成 "stub"）。后果：对从 schema v1 迁移上来的旧库（_migrate_v1_to_v2 只 ALTER TABLE 加 snapshot_json 列、不回填，所有存量 revision 该列为 NULL），任何命中当前版本的 get_law_as_of / get_article_as_of / get_articles(as_of=...) 都会返回 article=None / article_count=0，即「条文不存在」的错误结论——而 articles 表里明明有全文。已复现：将 revisions.snapshot_json 置 NULL 后，get_law_as_of(db,"民法典","2026-01-01") 返回 article_count=0、coverage='stub'；get_article_as_of(...,"577",...) 返回 article=None。

**证据 / 复现**

```text
elif revision.get("content_hash") == law_row["source_hash"]:
    law = _row_to_law(law_row, article_count=0)
    law["articles"] = []
    law["article_count"] = 0
复现输出：C) as_of law article_count: 0 coverage: stub / C) as_of article 577: None（articles 表中 577 条存在）
```

**修复建议**

该分支应从 articles 表加载当前条文：SELECT * FROM articles WHERE law_id=? ORDER BY position，填入 law["articles"] 与真实 article_count（该函数需接收 conn 或由调用方注入 articles）。或者在 _migrate_v1_to_v2 迁移时为存量 revision 回填 snapshot_json。

**对抗复核结论**：确认为真（复核后 severity=medium）

> 确认为真，已完整复现。用 loader 装入两版法规后执行 UPDATE revisions SET snapshot_json=NULL（模拟 v1 迁移库），get_law_as_of(db,"时点法","2026-01-01") 返回 article_count=0、articles_coverage='stub'、articles=[]；get_article_as_of 返回 article=None；get_articles found_count=0——而同库 get_article（无 as_of）正常返回"新版本正文。"，articles 表确有该条。触发条件属实：db.py:118-124 _migrate_v1_to_v2 仅 ALTER TABLE 加列不回填，v1 升级路径由 _MIGRATORS 注册表明确支持并有测试覆盖。三个调用点（service.py:1492/1881/2070）均无防护直接透传空 law。诊断层加重误导：该场景 diagnose_article_miss 命中 article_null_as_of（service.py:1630-1643），hint 称"不要直接用当前版本条文替代"，而选中版本恰是当前版本、当前条文正是正确答案。非有意设计的佐证：无测试断言该 elif 行为，CONTRACT.md as_of 语义未记载 metadata-only 退化，且 trace.py:131-150 对同一"revision==当前版本"场景的处理是从 articles 表加载全文（正确模式已有先例）。维持 medium：仅影响 v1 时代存量库；revision id 由 content_hash 派生，对该法任意一次重新 fetch/sync 即回填 snapshot_json 自愈；新建库不触发。但在触发窗口内为静默错误结论（法律工具误报"条文不存在"），值得修复。


### [M2] get_articles 对无效 as_of 返回 None 与「法规未找到」不可区分，批量接口误标为 law_not_found

- **位置**：`src/chinalaw/service.py:2009`
- **分类 / 维度**：正确性 / 核心业务逻辑

**缺陷描述**

get_articles 在 as_of 无法解析为日期时（`if as_of and parsed_as_of is None: return None`，line 1857-1858）返回 None，与「法规未入库」「numbers 无法解析」共用同一个返回值。get_articles_batch 对每个 section 把 result is None 一律标注为 error="law_not_found"（line 2009）。于是用户/agent 传 as_of="2020/01/01"（斜杠日期）时，批量结果把所有法规都报成「未找到」，按 skill 决策树 agent 会被误导去执行不必要的 fetch/ensure，而真正的问题只是日期格式。对比之下 applicable() 和 diagnose_article_miss() 都对 invalid date 有独立错误码，此处契约不一致。已复现：get_articles_batch(db, "民法典:577", as_of="2020/01/01") → sections[0].error='law_not_found'。

**证据 / 复现**

```text
get_articles: `parsed_as_of = _parse_iso_date(as_of) if as_of else None; if as_of and parsed_as_of is None: return None`
get_articles_batch: `"error": None if result is not None else "law_not_found"`
复现输出：D) batch section error: law_not_found ok: False（实际原因是 as_of 格式非法）
```

**修复建议**

get_articles_batch 在循环前先校验 as_of：`if as_of and _parse_iso_date(as_of) is None:` 返回带 error="invalid_as_of" 的整体错误 payload；长期看 get_articles 应把三类失败（invalid_as_of / law_not_found / empty_numbers）区分为不同返回（如带 reason 字段的 dict），与 diagnose_article_miss 的错误码体系对齐。

**对抗复核结论**：确认为真（复核后 severity=medium）

> 已完整复现并确认。（1）代码事实无误：service.py line 1856-1858 get_articles 对无效 as_of 返回 None，line 2009 get_articles_batch 把一切 None 标为 error="law_not_found"；批量层对空 numbers 有独立的 missing_numbers（line 1992），唯独漏了 invalid as_of。（2）无上游防护：cli.py line 198 的 --as-of 是无校验纯字符串，line 1341-1343 直接透传。（3）复现输出：数据库中民法典存在（无 as_of 时 found_count=1），但 get_articles_batch(db, "民法典:577", as_of="2020/01/01") → sections[0].error='law_not_found'，与真正不存在的法规（"不存在的法规XYZ:1" → 同样 law_not_found）完全不可区分；CLI 端到端实跑 `chinalaw articles --batch "民法典:577" --as-of "2020/01/01"` 输出 JSON 同样为 "error": "law_not_found"。（4）契约不一致属实：diagnose_article_miss line 1582 返回 reason="invalid_as_of" 且 hint 明确写「不要 fetch 当前法来修复日期格式问题」，applicable line 2575 返回 invalid_date warning——项目自身已认定日期格式错误必须与未找到区分，get_articles_batch 是唯一违反该契约的接口，会诱导 agent 按 skill 决策树执行不必要的 fetch/ensure。severity 维持 medium：不影响返回的法条文本正确性，但错误码直接驱动 agent 自动化决策。


### [M3] 增量模式与批量模式共用 source:{source}:next_page / last_page 断点键，跑一次 --incremental 会覆盖批量断点，随后 --batch --resume 从错误页续传（向前跳页丢数据或大量重爬）

- **位置**：`src/chinalaw/sync.py:260`
- **分类 / 维度**：正确性 / 数据层

**缺陷描述**

_sync_batch 同时服务 batch 与 incremental 两种模式（_sync_incremental 直接复用它，L152-169），两者的『页号』语义完全不同：batch 是全量列表的分页，incremental 是按发布日期过滤后列表的分页。但 L259-260 无条件用同一组 meta 键 `source:{source}:last_page` / `next_page` 记录页号。典型场景：用户批量同步到第 500 页中断（next_page=500），期间按 cron 跑了一次增量同步只翻了 3 页（next_page 被覆盖成 4），之后执行 `chinalaw sync --batch --resume`，_resolve_resume_page（L308-322）读到 4，从全量列表第 4 页重新开始——白白重爬近 500 页；反向场景（增量页数大于批量断点）则会向前跳页，第 N 到 M 页的法规被跳过，造成静默数据缺口。stable_pages_seen 键同样被两种模式互相污染。

**证据 / 复现**

```text
sync.py L259-260（batch 与 incremental 都会执行）:
            set_meta(conn, f"source:{source}:last_page", str(current_page))
            set_meta(conn, f"source:{source}:next_page", str(current_page + 1))
L308-311（--batch --resume 读同一键）:
    stored = get_meta(conn, f"source:{source}:next_page")
L152-169: _sync_incremental 复用 _sync_batch(mode="incremental") 且未隔离断点键。
```

**修复建议**

把断点键按模式隔离，如 `source:{source}:{mode}:next_page`（incremental 的分页断点本身跨窗口无意义，可只在 batch 模式下写 next_page/last_page/stable_pages_seen），并在 _resolve_resume_page 中只读取 batch 模式的键。

**对抗复核结论**：确认为真（复核后 severity=medium）

> 已用最小用例在 .venv 中完整复现，finding 陈述准确无误读。事实核查：(1) sync.py L152-169 _sync_incremental 确实直接复用 _sync_batch(mode="incremental")，L259-260 无条件写 source:{source}:last_page/next_page，L270/273 同样无条件写 stable_pages_seen，无任何按 mode 隔离；(2) L308-322 _resolve_resume_page 只读 next_page/last_page，虽然 L258 写了 last_mode，但没有任何代码在 resume 时校验 last_mode，CLI 层（cli.py L1556-1574）也是直接透传，无调用方防护；(3) tests/test_core.py 只分别测了 batch resume（L4152）和 incremental（L4277），从未测两模式交叉污染。复现输出（patch get_source_adapter 为假适配器，行不含 bbbs 故不入库，仅验证断点逻辑）：批量同步 500 页后 next_page=501；跑一次 incremental 翻 3 页后 next_page 被覆盖为 4、last_mode=incremental、stable_pages_seen 从 500 被覆盖为 3；随后 sync_source(batch=True, resume=True) 返回的 start_page=4（应为 501），即重爬近 500 页。反向场景：批量到第 3 页中断（next_page=4），incremental 翻 50 页后 resume 起始页变为 51，第 4~50 页被静默跳过，造成数据缺口。严重度维持 medium：向前跳页方向是静默数据丢失（对法规库较严重），但触发需要「批量中断 + 期间跑增量 + 再 --resume」的组合条件，且缺口可通过重新全量批扫补回；向后方向仅浪费带宽/时间。修复建议合理：断点键按 mode 隔离（incremental 的分页断点跨窗口无意义，可只在 batch 模式写 next_page/last_page/stable_pages_seen）。涉及文件：/Users/huoxihuo/chinalaw-cli/src/chinalaw/sync.py（L259-260、L270-273、L308-322、L152-169）。


### [M4] sync --from-dir 指向不存在或无 *.json 的目录时静默报告成功（exit 0，laws_loaded=0）

- **位置**：`src/chinalaw/cli.py:1554`
- **分类 / 维度**：正确性 / CLI 层

**缺陷描述**

`_handle_sync` 中 `paths = sorted(Path(args.from_dir).glob("*.json"))` 对不存在的目录不会报错——pathlib.glob 对不存在路径直接返回空迭代器——随后 loader.load_files 收到空列表，返回 `{"laws_loaded": 0, "articles_loaded": 0, "titles": []}` 且退出码 0。用户或 agent 把目录路径打错（或目录里没有 .json）时会误以为同步成功，本地库实际未更新，属于静默数据正确性问题。对比同文件的 loader.load_fixtures（loader.py:313-319）对缺失目录至少返回 `note: fixtures dir missing` 说明，而 CLI 对 --from-dir 连提示都没有；也对比无参数 sync 的 noop 分支会明确退 2。

**证据 / 复现**

```text
复现：`chinalaw --db /tmp/claw_review.db sync --from-dir /tmp/no_such_dir_9x` → stdout `{"laws_loaded": 0, "articles_loaded": 0, "titles": []}`，exit=0。代码：cli.py:1553-1555 `elif args.from_dir:\n        paths = sorted(Path(args.from_dir).glob("*.json"))\n        result = loader.load_files(db_path, paths)`。
```

**修复建议**

在 glob 之前校验 `Path(args.from_dir).is_dir()`，不是目录则 emit `law_sync_error` envelope 并返回 2；目录存在但 `paths` 为空时至少在结果中加入 warning 字段（或同样按错误处理），避免拼写错误被判定为成功同步。

**对抗复核结论**：确认为真（复核后 severity=medium）

> 复现确认，且实际比 finding 描述的还略重。(1) 复现：`/Users/huoxihuo/chinalaw-cli/.venv/bin/python -m chinalaw.cli --db /tmp/claw_review_check.db sync --from-dir /tmp/no_such_dir_reverify_9x` 输出 `{"laws_loaded": 0, "articles_loaded": 0, "titles": []}`、exit=0；空目录（有目录无 *.json）同样 exit=0 无任何提示。(2) 无任何调用方防护：cli.py 全文 grep 不到 `is_dir`/`exists()`，argparse 对 --from-dir 只是普通 str 参数（cli.py:350-353），_handle_sync（cli.py:1553-1555）直接 glob 后传给 load_files。(3) finding 引用的对比事实均准确：loader.py:313-319 的 load_fixtures 对缺失目录确实返回 `note: fixtures dir missing`；无参数 noop 分支确实返回 2（cli.py:1586）。(4) 审查员未发现的加重因素：loader.load_files（loader.py:287-301）即使收到空 paths 也会执行 `set_meta(conn, "last_sync_at", now)`——复现后查库确认 laws 计数为 0 但 last_sync_at 已被写成当前时间，而 service.py:2679 的 status 报告正是读这个字段（formatters.py:883 显示为「最近一次同步」），即打错路径不仅静默成功，还会伪造同步新鲜度信号，掩盖后续 status/doctor 排查。(5) tests/ 中无 sync --from-dir 缺失/空目录场景的测试（仅 fetch 的 from_dir 测试）。对面向 agent 的 CLI 属于静默数据正确性问题，维持 medium。


### [M5] 证据台账 evidence_id 用『重数行数+1』生成且与追加写非原子：并发 CLI 调用产生重复 evidence_id，crash 半行会让相邻两条记录被静默丢弃

- **位置**：`src/chinalaw/snapshots.py:291`
- **分类 / 维度**：正确性 / 数据层

**缺陷描述**

append_command_record（L146-168）先调用 _next_evidence_id(path)（L291-299）重新读一遍整个 JSONL 数一遍非空行得到 N，再以 E{N+1:04d} 追加。两个并发的 chinalaw 命令（agent 工作流常见并行执行多条检索命令，MCP server 同理）会各自数到相同的 N，写出重复的 evidence_id——而该 ID 正是 grounding 审计中引用证据的主键，重复直接破坏台账的可引用性。其次，L166-167 把 JSON 与换行符分两次 write 且无 flush/fsync，进程在两次 write 之间或缓冲未落盘时被杀，会留下无换行结尾的半行；下一次 append 以 "a" 模式直接接在其后，形成 `{...}{...}\n` 的粘连行，load_records（L282-285）json.loads 失败后 continue 静默跳过，导致坏行本身与紧随其后的那条完整记录一起丢失且无任何告警。审计型 append-only ledger 应当对这两点有防御。

**证据 / 复现**

```text
snapshots.py L291-299:
    def _next_evidence_id(path: Path) -> str:
        if not path.exists(): return "E0001"
        count = 0
        with path.open(...) as fh:
            for line in fh:
                if line.strip(): count += 1
        return f"E{count + 1:04d}"
L165-167:
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False, sort_keys=True))
        fh.write("\n")
L282-285: except json.JSONDecodeError: continue  # 粘连行连同其后记录静默丢弃
```

**修复建议**

把记录序列化为单个含换行的字符串一次 write；追加前后用文件锁（fcntl.flock / msvcrt.locking）保护『计数+写入』临界区，或改用与内容相关的唯一 ID（如时间戳+随机后缀）避免依赖行数；load_records 遇到解析失败的行至少记入返回统计（如 corrupt_line_count）而非完全静默。

**对抗复核结论**：确认为真（复核后 severity=medium）

> 两项指控均在真实代码路径上复现，finding 属实。(1) 并发重复 evidence_id：用 multiprocessing.Barrier 让 8 个独立进程同时调用 append_command_record（等价于 agent 并行执行多条 chinalaw 命令），5 次试验全部产生重复 ID，最严重时磁盘上 8 行记录全部为 "E0001"（trial 0/2/4: dupes {'E0001': 8}）。全仓库 grep 确认 src/ 下无任何 flock/fcntl/msvcrt/fsync/O_APPEND 防护，cli.py:1179 _record_snapshot 在项目目录下对每条命令自动写入同一 latest.jsonl，触发条件真实可及。且 evidence_id 确为审计引用主键：audit.py:709-747 _classify_grounding 以 record.get("evidence_id") 作为 verified/retrieved_only 的引用返回，formatters.py:1429 直接展示，重复即破坏可追溯性。(2) 粘连行双重丢失：模拟在 snapshots.py L166 与 L167 两次 write 之间 crash 留下无换行的半行 E0002 后，下一次 append_command_record 以 "a" 模式直接接续写出 {...}{...}\n 粘连行；load_records 仅返回 ['E0001']——坏行 E0002 与其后完整写入的 E0003 一起被 L284 的 except json.JSONDecodeError: continue 静默丢弃，无任何告警计数。一点技术修正不影响结论：由于文本 IO 缓冲（默认 8KB），两次 write 通常同缓冲区一次落盘，"恰在两次 write 之间被杀" 本身不是最可能的半行成因；但磁盘写满、单条记录超缓冲、断电均可产生同样的无换行半行，而"半行放大为相邻两条丢失"的后果与成因无关，已复现。严重度维持 medium：不影响法律内容正确性，audit 匹配按 law_id/number 内容进行仍可工作，但作为审计型 append-only ledger，其主键唯一性与记录完整性双双缺乏防御，建议修复方案（单次 write + 文件锁或内容相关 ID + 坏行计数）合理。复现脚本仅写 /tmp，未改动仓库。


### [M6] 文件输入类子命令未处理 FileNotFoundError，路径不存在时输出裸 traceback 而非 JSON error envelope

- **位置**：`src/chinalaw/cli.py:1959`
- **分类 / 维度**：错误处理 / CLI 层

**缺陷描述**

多个以文件为输入的子命令在文件缺失/不可读时直接向用户抛出未捕获的 FileNotFoundError（OSError 族），stderr 打印完整 Python traceback，退出码为解释器默认的 1，而不是项目约定的 JSON error envelope + 退出码 2。受影响路径：1) `norm import`（cli.py:1959，无任何 try）；2) `norm ingest --metadata-file`（cli.py:1964 经 _load_norm_ingest_metadata → cli.py:1221 的 path.read_text，仅 except ValueError）；3) `commentary import`（cli.py:2029，仅 except ValueError）；4) `pack import`（cli.py:2133，无 try）；5) `pack validate --file`（cli.py:2149，无 try）；6) `audit file` / `audit grounding` / `cite-check`（cli.py:2169/2190/2230/2239，底层 normsources.read_source_text 对 .txt/.md 缺失文件抛 FileNotFoundError，而 handler 仅 except ValueError）。传错路径是 agent 与人类的高频正常操作，破坏了机器可读契约（agent 解析 stdout 得到空输出），且退出码 1 与「1=未命中」语义冲突。项目自身已有正确范式：ensure.collect_names 对 --from-file 缺失抛 ValueError 并被 CLI 转成 envelope 退 2。

**证据 / 复现**

```text
复现：`chinalaw --db /tmp/claw_review.db norm import /tmp/definitely_missing_9x.json` → stderr: `FileNotFoundError: [Errno 2] No such file or directory: '/tmp/definitely_missing_9x.json'`，exit=1（无 JSON 输出）。`chinalaw audit file /tmp/missing_doc.md`、`pack import`、`commentary import`、`norm ingest --metadata-file /tmp/missing_meta.json` 均同样输出裸 traceback、exit=1。代码：cli.py:1958-1959 `if args.norm_command == "import":\n    result = normsources.import_source_file(db_path, Path(args.file))`（无 try）；cli.py:2200 `except ValueError as exc:` 无法捕获 read_source_text 抛出的 FileNotFoundError。
```

**修复建议**

统一在这些 handler 的 try 块中把 `except ValueError` 扩为 `except (ValueError, OSError)`（FileNotFoundError/PermissionError/IsADirectoryError 均为 OSError 子类），对当前无 try 的 `norm import`/`pack import`/`pack validate --file` 补上同样的 envelope 处理并返回 2；或在进入业务层前统一做 `Path.is_file()` 前置校验并抛 ValueError，复用 ensure.collect_names 的既有模式。

**对抗复核结论**：确认为真（复核后 severity=medium）

> 全部 7 条受影响路径均实测复现，finding 陈述的事实无一失实：1) `chinalaw --db /tmp/x.db norm import /tmp/definitely_missing_9x.json` → stderr 裸 traceback（FileNotFoundError 起于 cli.py:1959 → normsources.py:577 read_text），exit=1，stdout 为空；2) `audit file /tmp/miss_a.md`、`cite-check /tmp/miss_b.md`、`pack import /tmp/miss_c.json`、`pack validate --file /tmp/miss_d.json`、`commentary import /tmp/miss_e.json`、`norm ingest ... --metadata-file /tmp/miss_meta_9x.json` 全部同样输出裸 FileNotFoundError traceback、exit=1、stdout 空。代码核实：app() 调度器（cli.py:2351-2355）仅 except BrokenPipeError，无全局兜底；_handle_audit/_handle_cite_check/commentary import/norm ingest 仅 except ValueError（FileNotFoundError 是 OSError 子类，不被捕获）；norm import/pack import/pack validate --file 完全无 try。业务层（normsources.py:577、normpacks.py:767/937、commentary.py:218、audit.py:134/158）均直接 read_text，无 is_file 前置校验。对照范式也属实：`ensure --from-file /tmp/miss_names_9x.txt` 实测输出 JSON envelope {"kind":"law_ensure_error","error":"ValueError",...} 且 exit=2。exit=1 与「未命中/audit 不通过」语义冲突也属实（如 norm show 未找到返回 1、audit 报告 not ok 返回 1）。严重度更正为 medium 而非 high：该缺陷完全可复现且违反项目自身的机器可读契约，但属错误路径的工程质量问题——失败是「响亮的」（stderr 有明确 traceback）且方向 fail-safe（agent 在 CI/审计场景会把缺文件误判为「审计有问题」而非误判为通过），无数据损坏、无静默错误结果，修复成本低（扩为 except (ValueError, OSError) 即可）。


### [M7] _handle_sync 对真实数据源同步无任何异常捕获，联网错误直接裸 traceback，违背 discover/fetch 已建立的 error envelope 契约

- **位置**：`src/chinalaw/cli.py:1558`
- **分类 / 维度**：错误处理 / CLI 层

**缺陷描述**

`chinalaw sync --source flk_npc ...` 是真实联网命令，但 `_handle_sync`（cli.py:1547-1588）没有 try/except，sync.py 内部也没有任何 URLError/OSError/TimeoutError 捕获（grep 确认 sync.py 无相关 except）。网络超时、DNS 失败、HTTP 错误等常见瞬态故障都会以未捕获异常形式冒出：stderr 裸 traceback、exit 1、stdout 无 JSON。这与同为联网命令的 `_handle_discover`（cli.py:1648-1669，注释明确记载 `URLError/OSError/TimeoutError/JSONDecodeError` → `law_discover_error` envelope 退 2 的契约，见 docs/CLI_DISCOVER_ERROR_ENVELOPE_SPEC.md）和 `_handle_fetch`（FetchError → envelope）直接矛盾。另外 `sync --from-dir` 目录内存在损坏 JSON 时，loader.load_files 的 json.loads 抛 JSONDecodeError 也同样无人捕获。

**证据 / 复现**

```text
cli.py:1558-1576 `elif args.source:\n        result = sync_source(\n            db_path, source=args.source, ...` 整段无 try/except；`grep -n "URLError\|except OSError\|except Exception\|except TimeoutError" src/chinalaw/sync.py` 无任何输出。对比 cli.py:1648 `except (ValueError, URLError, OSError, TimeoutError, json.JSONDecodeError) as exc:`（discover）。
```

**修复建议**

在 `_handle_sync` 的 sync_source / load_files 调用外套上与 `_handle_discover` 相同的 `except (ValueError, URLError, OSError, TimeoutError, json.JSONDecodeError)`，emit `{"kind": "law_sync_error", "error": ..., "message": ...}` 并返回 2，与 CLI_DISCOVER_ERROR_ENVELOPE_SPEC 的方案对齐。

**对抗复核结论**：确认为真（复核后 severity=medium）

> 确认为真，两条路径均已复现。(1) 代码事实核实：cli.py:1547-1588 `_handle_sync` 整段无 try/except；sync.py（全文 359 行）无任何 except 网络异常；适配器 adapters/flk_npc.py:504 `urlopen(req, timeout=...)` 只把 JSONDecodeError wrap 成 ValueError（509-518 行），URLError/OSError/TimeoutError 原样上抛；cli.py:2351-2355 dispatch 层仅捕获 BrokenPipeError，`main()` 无兜底。全仓 grep 无 "law_sync_error"，即 sync 完全没有 error envelope。(2) 复现一（网络错误）：用 .venv python monkeypatch `chinalaw.adapters.flk_npc.urlopen` 抛 URLError 后调用 `cli.app(["sync","--source","flk_npc","--query","证券"])`，URLError 未被任何层捕获，直接冒出 app()（真实 CLI 下即裸 traceback + exit 1，stdout 无 JSON），调用链 cli.py:1559 → sync.py:75 → flk_npc.py:333 → 504 与 finding 描述完全一致。(3) 复现二（--from-dir 损坏 JSON）：目录放一个 `{ broken json` 文件后 `cli.app(["sync","--from-dir",...])` 在 cli.py:1555 loader.load_files 处裸抛 JSONDecodeError，同样无 envelope。(4) 对比契约成立：同文件 `_handle_discover`（cli.py:1648）明确捕获 (ValueError, URLError, OSError, TimeoutError, json.JSONDecodeError) 并 emit `law_discover_error` 退 2，`_handle_fetch`/`_handle_ensure` 亦有 envelope，sync 作为同样联网的命令确属矛盾。唯一小瑕疵：finding 引用的 docs/CLI_DISCOVER_ERROR_ENVELOPE_SPEC.md 实际不在仓库（只是 cli.py 注释里提及），但这不影响结论——契约由 discover/fetch 的实际代码确立。缓解因素：sqlite 连接上下文管理器在异常时回滚，不会造成数据损坏，且 exit code 仍非 0，故不升为 high，维持 medium。


### [M8] doctor 健康检查通过 service.status 静默执行 schema 迁移，schema_version 检查的 fail 分支对旧库升级场景是死代码，且备份提示为时已晚

- **位置**：`src/chinalaw/doctor.py:133`
- **分类 / 维度**：数据 / CLI 层

**缺陷描述**

`_check_db` 调用 `service.status(db)`，而 service.status（service.py:2666-2667）第一步就是 `with connect(db) as conn: migrate(conn)`，会把任何低版本 schema 直接原地升级到最新。后果有三：1) 名为只读健康检查的 `chinalaw doctor` 实际上会修改用户数据库；2) doctor.py:148-155 的 `schema_version` fail 分支（`f"schema_version={schema_version}, expected={SCHEMA_VERSION}"`）在旧库升级场景永远不可达（迁移已在读取前完成，读到的必然等于 SCHEMA_VERSION），只剩下「DB 比代码新」的降级场景可触发；3) 该分支的 hint「运行当前版本 chinalaw status 触发迁移；迁移前建议备份数据库」自相矛盾——doctor 本身已经在没有备份的情况下完成了迁移。run_doctor 的 docstring 也强调 "without mutating missing databases"，但对已存在的旧库同样发生了未告知的写操作。

**证据 / 复现**

```text
复现：将 /tmp/claw_doctor.db 的 meta.schema_version 手工改为 '7' 后运行 `chinalaw --db /tmp/claw_doctor.db doctor`，checks 输出 `{'name': 'schema_version', 'status': 'pass', 'message': 'schema_version=9'}`，且检查后 DB 内 schema_version 已被写成 '9'。代码：doctor.py:133 `status = service.status(db)`；service.py:2666-2667 `with connect(db_path) as conn:\n        migrate(conn)`。
```

**修复建议**

在 doctor 中用只读方式获取版本（如直接 `SELECT value FROM meta WHERE key='schema_version'`，或给 service.status 增加 `migrate=False` 参数 / 提供 read_schema_version helper），先报告版本差异再由用户显式运行 status/migrate；至少应在迁移发生时在 check data 中如实报告「已自动迁移 vN→vM」而不是显示 pass。

**对抗复核结论**：确认为真（复核后 severity=medium）

> 复现确认为真。(1) 旧库静默迁移：用当前代码建库后将 meta.schema_version 改为 '7'，运行 `chinalaw --db /tmp/claw_doctor_repro.db doctor --format json`，输出 `{'name': 'schema_version', 'status': 'pass', 'message': 'schema_version=9'}` 且检查后 DB 内 schema_version 已被写成 '9'——名为健康检查的 doctor 确实在无备份提示的情况下原地迁移了用户数据库。代码链路清晰：doctor.py:133 `service.status(db)` → service.py:2667 `migrate(conn)` → db.py:82-94 循环应用 migrator 并写回 schema_version。(2) fail 分支死代码：因迁移发生在 get_meta 读取之前，旧库升级场景读到的必然是 SCHEMA_VERSION=9，fail 分支（doctor.py:149-155）确实只在「DB 比代码新」时可达——将 schema_version 改为 '12' 后复现出 `fail, schema_version=12, expected=9`。且此唯一可达场景下 hint「运行当前版本 chinalaw status 触发迁移」是错误建议：db.py:79 `if current >= SCHEMA_VERSION: return current` 使 migrate 对新版本库直接早退，复现验证运行后版本仍为 12，正确建议应是升级 CLI。(3) hint 自相矛盾属实：升级场景中 doctor 已先行迁移，「迁移前建议备份」为时已晚。两点减轻情节：service.py 共 14 处入口都在 connect 后立即 migrate（search/get 等任何命令都会静默迁移），doctor 并非孤例而是全局设计；docstring "without mutating missing databases" 字面上仅承诺不创建缺失数据库（有配套测试 test_doctor_missing_db_does_not_create_database），并未承诺对已存在库只读，finding 对 docstring 的引用略有过度解读。但 doctor 作为用户在迁移决策前的诊断工具却先斩后奏、检查项对最需要它的场景失效、且唯一可达分支给出无效修复建议，三者叠加维持 medium。


### [M9] 连接未设置 busy_timeout（默认仅 5 秒）且 migration 的 check-then-ALTER 非原子，多进程并发场景下易报 database is locked / duplicate column name

- **位置**：`src/chinalaw/db.py:26`
- **分类 / 维度**：错误处理 / 数据层

**缺陷描述**

open_connection（L23-34）调用 sqlite3.connect(path) 未传 timeout 也未执行 PRAGMA busy_timeout，写锁竞争 5 秒即抛 `database is locked`（已实测 5.2s 报错）。项目的实际使用形态是『多个 agent/CLI 进程共享 ~/.chinalaw/chinalaw.db』，而每个命令入口都会先跑 migrate()，写命令（sync/load/norm import）都要拿写锁，5 秒对批量同步这类长写事务远远不够，正常并发使用就会随机崩溃且无重试。另外 migration 本身存在竞态：_migrate_v1_to_v2（L118-124）等 ALTER 型 migrator 用『PRAGMA table_info 检查列不存在 → ALTER TABLE ADD COLUMN』两步实现幂等，两个进程同时升级同一档时，A 检查后 B 先完成 ALTER 并提交，A 再执行 ALTER 会抛 `duplicate column name: snapshot_json`（OperationalError），migrate 未捕获直接崩溃；整个 migrate 也没有用 BEGIN IMMEDIATE 串行化。

**证据 / 复现**

```text
db.py L26: conn = sqlite3.connect(path)  # 无 timeout 参数、无 busy_timeout PRAGMA
L118-124:
    columns = {row[1] for row in conn.execute("PRAGMA table_info(revisions)")}
    if "snapshot_json" not in columns:
        conn.execute("ALTER TABLE revisions ADD COLUMN snapshot_json TEXT")  # 与检查非原子
实测输出：T2 second writer error after 5.2s: database is locked
```

**修复建议**

open_connection 中设置更宽裕的锁等待（如 sqlite3.connect(path, timeout=30) 或 PRAGMA busy_timeout=30000）；migrate() 开头执行 `BEGIN IMMEDIATE` 抢占写锁后再读 current_version 并逐档升级，保证同一时刻只有一个进程执行 migration，同时对 ALTER 捕获 duplicate column 错误作为幂等兜底。

**对抗复核结论**：确认为真（复核后 severity=medium）

> 两个断言均实测复现，finding 陈述的代码事实全部准确。(1) busy_timeout：db.py L26 确为 sqlite3.connect(path) 裸调用，全仓库 grep 无任何 busy_timeout/timeout 设置、无锁文件、无重试逻辑。用两进程复现：P1 经 open_connection 打开连接后执行一条 INSERT（隐式写事务不提交，模拟长写事务），P2 执行写入，输出 `second writer result: 'OperationalError: database is locked' after 5.2s`，与 finding 实测一致。且 sync.py 的 _sync_batch 在单个 `with connect()` 内循环「网络抓取→_load_if_changed 写库」全程无中间 commit（grep 确认 sync.py 无 commit() 调用），首条 DML 后写锁持续到整批结束，批量同步持锁可达分钟级，远超 5 秒，写写冲突场景真实存在（多 agent 共享 ~/.chinalaw/chinalaw.db 是默认布局，DEFAULT_DB_PATH 即全局单库）。(2) migration 竞态：构造真实 v1 库（SCHEMA_V1_SQL + schema_version=1），两条独立连接交错执行 db.migrate()——A 执行完 PRAGMA table_info(revisions) 检查后让 B 完整跑完 migrate 并提交，A 继续执行即输出 `[A] migrate CRASHED: OperationalError: duplicate column name: snapshot_json`，migrate() 无任何捕获、无 BEGIN IMMEDIATE 串行化。对冲减弱因素的核查：WAL 下只读命令不受影响（migrate 对已最新库仅读即返回）；两类故障均不损坏数据、重跑即恢复；migration 竞态仅在包升级后首批并发命令的毫秒级窗口内触发。因此维持 medium：是真实且可随机触发的可用性缺陷（对 agent 无人值守工作流影响较大），但非数据损坏，不足以升 high。


### [M10] get / diff 对格式非法的 --as-of 静默误报为「法规不存在」（found:false），与 article 命令的 invalid_as_of 诊断不一致

- **位置**：`src/chinalaw/cli.py:1248`
- **分类 / 维度**：正确性 / CLI 层

**缺陷描述**

service._parse_iso_date（service.py:453-459）对非 ISO 日期返回 None，get_law_as_of / diff_law_as_of 随之返回 None，`_handle_get`（cli.py:1246-1258）和 `_handle_diff`（cli.py:1846-1859）把它渲染成 `{"found": false, "name": ...}` 退 1——即便该法规明明存在于本地库。日期写成 `2020/06/01`、`2021.01.01` 是高频输入错误，误报「未找到」会诱导 agent 走 fetch/ensure 补库流程；项目在 article 命令里已明确防范这一失败模式（diagnose_article_miss 返回 `reason: invalid_as_of` 并附 hint「不要 fetch 当前法来修复日期格式问题」），但 get/diff 缺少同等处理，同一非法输入在不同命令得到语义相反的信号。

**证据 / 复现**

```text
复现：`chinalaw --db /tmp/claw_review.db get 民法典 --as-of "2020/06/01"` → `{"found": false, "name": "民法典"}` exit=1；同库 `get 民法典 --as-of 2021-01-01` 正常返回全文 exit=0；`diff 民法典 --from-as-of "2020/01/01" --to-as-of 2021-01-01` 同样 `{"found": false}`。对比 `article 民法典 143 --as-of "2021.01.01"` 返回 `"reason": "invalid_as_of", "hint": "--as-of 必须使用 YYYY-MM-DD…"`。代码：cli.py:1247-1251 `law = (\n        service.get_law_as_of(db_path, args.name, args.as_of)\n        if args.as_of ...`。
```

**修复建议**

在 `_handle_get` / `_handle_diff`（以及 trace 的 from/to-as-of）调用前先校验日期格式（如 date.fromisoformat try/except），非法时 emit 带 `reason: invalid_as_of` + hint 的 envelope 并返回 2，复用 diagnose_article_miss 中已有的提示文案。

**对抗复核结论**：确认为真（复核后 severity=medium）

> 复现成功，事实全部核实，无任何上游防护，finding 成立。(1) 复现：`chinalaw --db /tmp/claw_review.db get 民法典 --as-of "2020/06/01" --format json` → `{"found": false, "name": "民法典"}` exit=1，与真正不存在的法（`get 一部根本不存在的法` → 同形 payload、同 exit=1）字节级同构，无法区分；md 格式更直接输出「_未找到该法规。_」——而同库 `get 民法典 --as-of 2021-01-01` 正常返回全文，证明该法明明在库。diff 同样复现 `{"found": false}` exit=1。(2) 无防护链路核实：argparse 对 --as-of 仅有 help 文本无 type 校验（cli.py:116/744-745）；service.get_law_as_of（service.py:1464-1466）对 _parse_iso_date 返回 None 直接 return None；_handle_get（cli.py:1252-1258）/_handle_diff（cli.py:1853-1859）将 None 一律渲染为 found:false；mcp.py 也是裸传无校验。(3) 不一致性坐实且比 finding 所述更强：不仅 article 命令有 invalid_as_of 诊断（service.py:1578-1589，hint 明言「不要 fetch 当前法来修复日期格式问题」），trace 命令也在 trace.py:469-474 显式返回 `error: invalid_date` + 「--from-as-of / --to-as-of must use YYYY-MM-DD」（已实测复现），即 get/diff 是全部 as-of 命令中仅有的两个漏网者；且 tests/test_core.py:5644 的测试名注释「as-of 日期格式错 → 不应误导 agent 去 fetch 当前法」证明项目自身将「误导 agent 走 fetch 流程」认定为需防范的失败模式。唯一小瑕疵：suggested_fix 中「以及 trace 的 from/to-as-of」多余——trace 已在 service 层自带防护，无需修改；这反而强化了核心结论。severity 维持 medium：无崩溃/数据损坏，exit 码仍非 0，但 JSON 信号与 law_missing 语义完全同构，在本项目 agent 工作流（fetch skill 以 law_missing/needs_fetch 为触发词）下会诱发不必要的补库操作。


### [M11] test_migrate_from_each_version 装置使断言恒真：migrator 全部在已建满最新表的 DB 上空转，v7→v8/v8→v9 真实升级路径无有效覆盖

- **位置**：`tests/test_db_migrators.py:82`
- **分类 / 维度**：测试 / 测试体系

**缺陷描述**

该测试宣称覆盖"从 0..SCHEMA_VERSION-1 任一起点都能升到最新且关键表齐全"，但对 start>=1 的装置是先调 _migrate_v0_to_v1（其实现是 executescript(SCHEMA_V9_SQL)，一次性建满最新全部表），再把 schema_version 强改回 N。因此后续 N..latest 的每个 migrator 都在"所有表已存在"的 DB 上空转，REQUIRED_TABLES 断言恒真——即使某个 migrator 完全忘记建表也检不出来。实测：把 _migrate_v7_to_v8 替换为 no-op 后，document_number_index 依然存在、断言依然通过。同时 tests 目录对 SCHEMA_V7_SQL/SCHEMA_V8_SQL 零引用（test_core.SchemaTests 只从真实 v1..v6 schema 出发测到 v6→v7），即 v7→v8（document_number_index）和 v8→v9（commentary_books/article_commentaries）这两条真实用户 DB 的升级路径没有任何有效测试。

**证据 / 复现**

```text
src/chinalaw/db.py:398 `conn.executescript(SCHEMA_V9_SQL)`（_migrate_v0_to_v1 即最新累积 DDL）；运行复现：patch.dict(_MIGRATORS, {7: noop}) 后从"满表+version=7"migrate，输出 `broken migrator, table still present?: True`；`grep -rn "SCHEMA_V7\|SCHEMA_V8" tests/` 无结果。
```

**修复建议**

装置改为对 start=N 用 `conn.executescript(SCHEMA_V{N}_SQL)` 建真实 vN schema（与 test_core.SchemaTests v1..v6 用例同型），再 migrate 并断言新表/新列出现；补 v7→v8 断言 document_number_index、v8→v9 断言 commentary_books/article_commentaries 的独立用例。

**对抗复核结论**：确认为真（复核后 severity=medium）

> 复现确认。(1) 装置事实无误：tests/test_db_migrators.py:82 对 start>=1 先调 _migrate_v0_to_v1（src/chinalaw/db.py:398 即 executescript(SCHEMA_V9_SQL)，一次建满 v9 全部表）再强设 schema_version=N，后续 migrator 全在满表 DB 上空转。(2) 恒真断言复现：用 patch.dict(_MIGRATORS, {7: noop}) 替换 _migrate_v7_to_v8 后，按测试装置（满表+version=7）migrate，输出 final=9 且 document_number_index 仍存在——测试两条断言照样通过，坏 migrator 检不出；对照组从真实 SCHEMA_V7_SQL 起点 + 同一 noop，document_number_index 缺失，证明改用真实 vN schema 的装置能检出。(3) 覆盖空白属实：grep tests/ 对 SCHEMA_V7/V8/V9_SQL 零命中；test_core.SchemaTests 真实起点用例止于 v6→v7（test_core.py:292），test_core.py:2819 的 document_number_index 用例是空 DB 走 v0→v1 快路径，均不覆盖存量 DB 的 v7→v8、v8→v9 升级。schema.py:225/285 存在 SCHEMA_V7_SQL/SCHEMA_V8_SQL，suggested_fix 可行。(4) 从真实 v7 起点用真实 migrator 升级验证正常（表齐全），故无产品 bug，属纯测试有效性缺陷；测试对 start>=1 仍有幂等性/版本推进的残余价值，但"关键表齐全"断言恒真与两条真实升级路径零有效覆盖的核心论断成立，维持 medium。


### [M12] law_to_markdown 在每条条文前重复输出章节标题，全文输出严重冗余破损

- **位置**：`src/chinalaw/formatters.py:406`
- **分类 / 维度**：正确性 / 输出格式化/数据模型

**缺陷描述**

law_to_markdown 渲染条文列表时，对每条含 part 字段的条文都无条件输出一行 `### {part}` 章节标题，没有与上一条的 part 做去重比较。本地库 7516 条条文中 7253 条带 part，因此 `chinalaw get <法规> --format md` 的正常使用即触发：民法典输出中 `### 第一编 总则 第一章 基本规定` 等标题被逐条重复 1260 次，md 结构（本应是章节标题下挂多条条文）完全变形，输出体积与 agent token 消耗近乎翻倍，人眼阅读也被重复标题淹没。

**证据 / 复现**

```text
formatters.py:402-408:
        for a in articles:
            header = a.get("number_display")
            ...
            if a.get("part"):
                lines.append(f"### {a.get('part')}")

实测：`.venv/bin/chinalaw get 民法典 --format md | grep -c '^### '` → 1260（每条条文一个重复章节标题）；连续三条条文前均输出相同的 `### 第一编 总则 第一章 基本规定`。
```

**修复建议**

在循环外维护 `last_part = None`，仅当 `a.get("part") != last_part` 时输出 `### {part}` 并更新 last_part，使章节标题只在切换时出现一次。

**对抗复核结论**：确认为真（复核后 severity=medium）

> 确认为真。(1) formatters.py:402-411 循环内对每条含 part 的条文无条件 append f"### {a.get('part')}"，无 last_part 去重；cli.py:1246-1261 的 _handle_get 直接把 service.get_law 结果传给 law_to_markdown，无任何调用方防护。(2) 实测复现：`.venv/bin/chinalaw get 民法典 --format md` 输出 7183 行，grep -c '^### ' 得 1260，而 sort -u 后仅 110 个唯一标题；uniq -c 显示同一标题连续重复 12-19 次（如「### 第一编 总则 第一章 基本规定」在第三条至第十二条前各出现一次），md 章节结构确实变形。(3) 数据库核实 7516 条条文中 7253 条 part 非空，与 finding 陈述完全一致，正常使用即触发。(4) 无测试锁定该行为；同文件 articles_to_markdown / outline_to_markdown_with_text 对逐条位置信息用斜体 `_位置：{part}_`，佐证此处 ### 本意是章节标题、缺去重属实现缺陷。唯一不实处：「体积/token 近乎翻倍」夸大——实测标题行共 80,013 字节，占 405,341 字节输出的约 20%，去重可省约 18%，远非翻倍；但这只影响影响量级表述，不影响缺陷本身成立。综合：人眼阅读的 md 全文输出（模块 docstring 明确「人眼阅读用 Markdown」）被重复标题淹没且结构破损，JSON 路径不受影响，维持 medium。


### [M13] alias_agent 模块（LLM 网络调用与响应解析，221 行）自身逻辑零测试覆盖，所有测试均 mock 掉 derive_aliases

- **位置**：`tests/test_fetch.py:251`
- **分类 / 维度**：测试 / 测试体系

**缺陷描述**

src/chinalaw/alias_agent.py 在 fetch 时经 urllib 调用 OpenAI 兼容 chat completions 端点、解析 JSON 响应、按 CHINALAW_ALIAS_AGENT_* 环境变量取配置、区分 recoverable/非 recoverable 异常并对返回别名做数量上限截断——这是全项目唯一一处运行时对外部 LLM 服务发请求并把结果写回 laws.aliases（进而影响 resolve 命中）的代码。但 tests/ 全目录对该模块的引用仅为 import 其异常类 AliasAgentRecoverableError；test_fetch.py 的 4 个相关用例全部 patch("chinalaw.fetch.derive_aliases") 把被测逻辑整体 mock 掉，只测 fetch 包装层的开关与告警。请求体/鉴权头构造、坏 JSON、HTTP 4xx/5xx、超时、别名清洗与截断、缺配置报错分级等路径全部无覆盖，回归只能在真实 fetch 打线上 LLM 时暴露。

**证据 / 复现**

```text
grep -rn "alias_agent" tests/ 仅命中 test_fetch.py 6 处，全部是 `from chinalaw.alias_agent import AliasAgentRecoverableError` 或对 `chinalaw.fetch.derive_aliases` 的 patch（tests/test_fetch.py:234-309、340-361）；无任何测试 import 或调用 alias_agent.derive_aliases 本体。
```

**修复建议**

新增 tests/test_alias_agent.py：mock urllib.request.urlopen，覆盖 (a) 正常响应解析与别名截断（CHINALAW_ALIAS_AGENT_MAX）；(b) 缺 BASE_URL/API_KEY → AliasAgentRecoverableError('config')；(c) HTTPError/URLError/超时 → recoverable('network')；(d) 非 JSON / 缺字段响应 → recoverable('parse')；(e) 其他异常透传。全部离线。

**对抗复核结论**：确认为真（复核后 severity=medium）

> 事实全部核实：alias_agent.py 确为 221 行；grep 确认 tests/ 对该模块的引用仅为 import 异常类 AliasAgentRecoverableError 与 patch("chinalaw.fetch.derive_aliases")，四个启用 CHINALAW_USE_ALIAS_AGENT=1 的用例全部 mock 掉本体，无 test_alias_agent.py。更强的证据：模块第 46-49 行专门为测试预留 http_post 注入缝（注释明言 "Module-level seam used by tests"），但 grep "http_post" 在 tests/ 零命中——可测性已设计好却无人使用。已用 .venv python 经该缝离线复现三条未覆盖路径：缺配置→missing_api_key、正常响应→清洗+截断（实测 _ALIAS_OK 正则静默丢弃含拉丁字母的别名"别名A"，属易回归行为）、非 JSON→invalid_response，证明待测逻辑真实且非平凡。影响属实：fetch.py:566 把结果写入 laws.aliases，test_fetch.py:244 证明 aliases 直接决定 resolve 命中。唯一辩护点（opt-in + recoverable 降级为 warning）已被 medium 定级吸收，且 _normalize/截断回归会静默污染本地库、未知异常会使启用态 fetch 崩溃，均只能线上暴露。维持 medium。附注：suggested_fix 中 reason 名应为 missing_api_key/invalid_response 而非 config/parse。


### [M14] V02DataPackSmokeTests 因缺失 data/packs 整体 skip，连带本仓库实际分发的 fixture 完整性守门（民法典 1260 条等）全部失效

- **位置**：`tests/test_core.py:7177`
- **分类 / 维度**：测试 / 测试体系

**缺陷描述**

test_v02_fixtures_load_and_pack_imports 在开头因 PACK_FILE（data/packs/contract-disputes-judgment.json，仓库不存在）skipTest，但该用例后半段大量断言针对的是本仓库确实分发的 data/fixtures：民法典 article_count==1260、公司法 2024 版 ≥260 条且 effective_at==2024-07-01、民诉法 ≥300 条、合同编通则解释 69 条、status 报表无 stub 法规等。这些数据完整性守门被一个无关的可选文件条件整体禁用。其他测试只部分补位（test_corpus 检查宪法 144/刑法 505，test_core 检查合通解释 fixture 文件本身），民法典/公司法/民诉法 fixture 的条数与元数据完整性在当前测试基线下实际零守门——fixture 被截断或误提交（例如民法典丢失分编）时 648 个测试仍全绿。

**证据 / 复现**

```text
tests/test_core.py:7177-7178 `if not self.PACK_FILE.exists(): self.skipTest("optional demo norm pack data is not included in this distribution")`；`ls data/packs` → No such file or directory；被跳过的断言含 7219 `self.assertEqual(civil_code["article_count"], 1260)`、7215 `assertGreaterEqual(company_law["article_count"], 260)`、7308 `assertEqual(status_report.get("stub_laws", []), [])`。
```

**修复建议**

把 fixture 完整性断言（民法典/公司法/民诉法/合通解释条数、stub==0、coverage 统计）拆成不依赖 PACK_FILE 的独立测试方法或独立 TestCase；仅 pack import/validate 相关断言保留 skip 条件。

**对抗复核结论**：确认为真（复核后 severity=medium）

> 确认为真。复现：data/packs 目录不存在，pytest 运行 V02DataPackSmokeTests 结果 SKIPPED（skip 点 tests/test_core.py:7177-7178）。唯一性核实：全 tests/ 中民法典 article_count==1260（7219）、公司法 2024 >=260 条 + effective_at==2024-07-01（7212/7215）、民诉法 >=300（7223）、合通解释 ==69（7228）、stub_laws==[]（7308）均仅存在于该被跳过用例，grep 无第二处。补位测试确如 finding 所述仅部分覆盖：test_corpus.py 断言宪法 144/刑法 505 及 contracts profile 三部阈值，baseline fixture 检查仅"非 seed 且 articles>0"；公司法 2024 属 general profile，连 articles>0 守门都没有。test_core.py 虽大量加载真实 data/fixtures，但民法典真实条文查询仅限低编号（1/143/522），全套件无任何 >1000 编号查询——民法典丢失后部分编、公司法截断或 effective_at 错误时 648 测试仍全绿，静态分析可证。无调用方防护或其他校验脚本补位（src/scripts 中 1260 仅现于 docstring）。措辞上"零守门"对 JSON 损坏/articles 清空场景略绝对，但 finding 限定的"条数与元数据完整性"维度下陈述准确。修复建议合理：将 fixture 完整性断言与 pack 相关断言拆分。


### [M15] 合规文档承诺的 UA 溯源与维护者联系渠道全部失效：UA 指向不存在的占位仓库，README 无维护者邮箱

- **位置**：`docs/COMPLIANCE.md:65`
- **分类 / 维度**：方向/文档 / 文档/方向

**缺陷描述**

COMPLIANCE.md §4 声称所有 HTTP 请求带 `chinalaw-cli/<version> (+repo-url)` 标识，目的是『让被抓取站点的 access log 能识别我们这个工具并定位维护者』，§5 进一步承诺站点运营方可『在 GitHub 仓库提 issue』或『通过 README 中维护者邮箱』联系，收到反馈后 24 小时内调整。实际代码 src/chinalaw/__init__.py:7 的 USER_AGENT_TOKEN 指向 `https://github.com/chinalaw-cli/chinalaw-cli`——这是 CHANGELOG（第 938 行『仓库 URL 占位 chinalaw-cli/chinalaw-cli』）自认的占位符，真实仓库是 `https://github.com/nh59yytyd5-dev/chinalaw-cli`（git remote 与 pyproject [project.urls] 均可证）。同时 README 通篇没有任何维护者邮箱。结果是：政府站点运营方按 UA 回溯会到达 404 页面，§5 的两条联系渠道一条指向错误仓库、一条不存在，24 小时响应承诺在事实上不可能被触发。对一个以『可追溯、保守合规』为立场的爬取工具，这是合规声明与实现的实质性脱节。

**证据 / 复现**

```text
docs/COMPLIANCE.md:64-66 `chinalaw-cli/<version> (+https://github.com/chinalaw-cli/chinalaw-cli)`；docs/COMPLIANCE.md:81 `- 通过 README 中维护者邮箱`（README 中无邮箱）；src/chinalaw/__init__.py:7 `USER_AGENT_TOKEN = f"chinalaw-cli/{__version__} (+https://github.com/chinalaw-cli/chinalaw-cli)"`；pyproject.toml:47 `Homepage = "https://github.com/nh59yytyd5-dev/chinalaw-cli"`；运行 `python -c "from chinalaw import USER_AGENT_TOKEN; print(USER_AGENT_TOKEN)"` 输出 `chinalaw-cli/0.2.1 (+https://github.com/chinalaw-cli/chinalaw-cli)`
```

**修复建议**

把 src/chinalaw/__init__.py 的 USER_AGENT_TOKEN 仓库 URL 改为真实仓库地址（可从 pyproject [project.urls] 单点派生，release metadata 测试已有先例可扩展校验）；同步更新 COMPLIANCE.md §4 示例；§5 要么补真实维护者邮箱进 README，要么删去『README 中维护者邮箱』这条渠道，只保留 GitHub issue。

**对抗复核结论**：确认为真（复核后 severity=medium）

> 全部事实已核实并复现：(1) 运行 .venv/bin/python 实测 USER_AGENT_TOKEN 输出 `chinalaw-cli/0.2.1 (+https://github.com/chinalaw-cli/chinalaw-cli)`，且 grep 证实该 token 被全部 7 个 adapter 拼入实际发出的 User-Agent 头（flk_npc.py:26,99 等），即每次 HTTP 请求都携带该 URL；(2) CHANGELOG 自认该 URL 是占位符（"仓库 URL 占位 chinalaw-cli/chinalaw-cli"），git remote 与 pyproject [project.urls]:47-50 均证实真实仓库为 nh59yytyd5-dev/chinalaw-cli；(3) 对 README.md、docs/ 全量正则搜索邮箱零命中，pyproject authors 亦无 email，COMPLIANCE.md:81 承诺的"README 中维护者邮箱"渠道不存在。缓和因素有二但不足以推翻：COMPLIANCE.md:80 对 issue 渠道自注"待项目正式发布后填入仓库地址"，README:287 有真实 Issues 链接可作迂回联系路径；但 §4 的 UA 示例与代码无任何待定标注，而 UA 正是运营方从 access log 回溯的第一路径，该溯源机制本身指向错误地址，邮箱渠道则完全虚设。对以"明确标识自己"为合规核心立场（§7）的爬取工具属实质性脱节，值得报告；但不影响运行时正确性、修复为单行改动，维持 medium。审查员"404 页面"一说因禁网无法验证，但无论该 URL 是 404 还是被占用均非本项目仓库，不影响结论。


### [M16] articles/outline 批量输出的 full 档 footer 无任何溯源信息，反而少于 compact 档

- **位置**：`src/chinalaw/formatters.py:492`
- **分类 / 维度**：正确性 / 输出格式化/数据模型

**缺陷描述**

单条 article_to_markdown 的 footer="full"（默认）会输出 状态/当前版本/来源 URL/最后核查 等溯源信息，compact 档输出精简的 `[status｜施行日期｜核查 N 天前]`。但批量的 articles_to_markdown 与 outline_to_markdown_with_text 中，footer="full" 只渲染请求/命中/缺失计数头部，末尾不附加任何 状态/来源/核查 信息；只有 footer="compact" 才追加 _compact_article_footer。结果是默认（full）档位的溯源信息反而比 compact 档少：`chinalaw articles 民法典 1,2 --format md` 全程不含 status、source_url、核查时间。对以『来源可核验』为核心卖点的法律引用工具，agent 走默认 md 输出拿不到溯源字段，md 与 JSON 的信息量等价承诺（模块 docstring）被打破。

**证据 / 复现**

```text
formatters.py:526-530（articles_to_markdown 末尾）：
    if footer == "compact":
        compact = _compact_article_footer(law)
        if compact: ...
# footer=="full" 无任何对应分支；outline_to_markdown_with_text 791-795 同样。

实测：`chinalaw articles 民法典 1,2 --format md` 输出无 status/来源/核查；加 `--compact` 反而输出 `[current｜2021-01-01 施行｜核查 87 天前]`。
```

**修复建议**

在 articles_to_markdown / outline_to_markdown_with_text 中为 footer=="full" 增加与 article_to_markdown full footer 同口径的结尾块（状态/来源 URL/最后核查），或至少在 full 档头部补充 来源 与 核查时间 两行，保证 full ⊇ compact 的信息量单调性。

**对抗复核结论**：确认为真（复核后 severity=medium）

> 复现成功，finding 所有事实陈述均准确。(1) 代码确认：formatters.py articles_to_markdown（481-531 行）中 footer=="full" 只渲染 请求/命中/缺失/时点 计数头，唯一追加溯源的分支是 526 行 `if footer == "compact": compact = _compact_article_footer(law)`，full 档无对应分支；outline_to_markdown_with_text（748-796 行，compact 分支 791-795）结构相同。对照单条 article_to_markdown 的 full 档（465-478 行）确实输出 状态/当前版本/来源 URL/最后核查 四行。(2) CLI 无补偿：cli.py:1336 `footer = "none" if args.no_footer else "compact" if args.compact else "full"`，默认 full，渲染后直接 _emit，无任何调用方附加溯源逻辑。(3) 实测：`chinalaw articles 民法典 1,2 --format md` 输出全程无 status/source_url/核查（仅计数头+条文正文）；加 --compact 反而输出 `[current｜2021-01-01 施行｜核查 87 天前]`，full ⊂ compact 的信息量倒挂实锤；outline --with-text 默认档同样无溯源。(4) 同一 payload 的 JSON 含 status='current'、source_url='https://flk.npc.gov.cn/detail?id=...'、freshness_days=87、effective_at='2021-01-01'，而 formatters.py 第 2-3 行 docstring 承诺「两种格式互为等价信息量」，md full 档一个溯源字段都没有，承诺确被打破。(5) 非有意设计的旁证：tests 仅覆盖 with_title 渲染（test_core.py:5852），无测试固化 full 无 footer 的行为；articles_batch_to_markdown 676 行注释显示批量函数将 footer 参数挪用于控制头部计数块，属参数语义混用导致的遗漏。severity 维持 medium：不崩溃、JSON 通路完好，但默认 md 通路丢失该工具核心卖点（来源可核验）字段，且默认档信息量少于精简档，对 agent/人类使用者构成实际误导。


### [M17] 协议文档与开发流程文档大量引用仓库中不存在的规范性文件（docs/decisions/、MVP_PLAN.md、FETCH_LAYER_SPEC.md、CLI_STATUS_FLAG_SPEC.md）

- **位置**：`docs/CONTRACT.md:1254`
- **分类 / 维度**：方向/文档 / 文档/方向

**缺陷描述**

多份现行有效的规范性文档把关键依据链向已从仓库删除的文件：(1) CONTRACT.md §1(L38) 要求 breaking change『必须先在 docs/decisions/ 写 ADR』、§9(L2014) 的协议变更流程、§4.11(L1254) 链接 ADR-0006、L1312 链接 docs/FETCH_LAYER_SPEC.md §3——docs/decisions/ 目录与 FETCH_LAYER_SPEC.md 均不存在；(2) PROJECT_CHARTER.md:3 与 ARCHITECTURE.md:3 链接 MVP_PLAN.md，DEVELOPMENT_GUIDE.md:9/23-24 甚至规定『方向冲突时，以 PROJECT_CHARTER.md 和 MVP_PLAN.md 为准』——MVP_PLAN.md 不存在，使『方向冲突仲裁基准』一半落空；(3) CONTRIBUTING.md:101/156 把数据贡献版权边界『详见 ADR-0004』——文件不存在，贡献者无法查阅被承诺的授权规则细节；(4) CLI 自身的 `discover` 帮助文本（src/chinalaw/cli.py:521/534/554）提示用户『详见 docs/CLI_STATUS_FLAG_SPEC.md』——文件不存在。协议变更流程（issue→ADR→PR）在当前仓库结构下不可执行，属于契约治理层面的方向性缺口。

**证据 / 复现**

```text
`ls docs/decisions docs/FETCH_LAYER_SPEC.md docs/MVP_PLAN.md docs/CLI_STATUS_FLAG_SPEC.md` 全部 `No such file or directory`；引用点：docs/CONTRACT.md:38,1254,1312,2014；docs/PROJECT_CHARTER.md:3；docs/ARCHITECTURE.md:3；docs/DEVELOPMENT_GUIDE.md:9,23-24,30；docs/CONTRIBUTING.md:101,142,156；src/chinalaw/cli.py:521,534,554（CLI --help 实际输出含『详见 docs/CLI_STATUS_FLAG_SPEC.md』）
```

**修复建议**

二选一：把 docs/decisions/（至少 ADR-0002/0004/0006/0007/0008/0009）与被引用的 spec 文档随仓库公开；或系统性清理所有失效引用——CONTRACT §9 改为公开可执行的流程描述、CONTRIBUTING 把 ADR-0004 的数据边界要点内联、CHARTER/ARCHITECTURE/DEVELOPMENT_GUIDE 删除 MVP_PLAN 链接、cli.py discover 帮助文本删掉 spec 文件名。

**对抗复核结论**：确认为真（复核后 severity=medium）

> 确认为真。复核证据：(1) 文件缺失全部属实——`ls docs/decisions docs/FETCH_LAYER_SPEC.md docs/MVP_PLAN.md docs/CLI_STATUS_FLAG_SPEC.md` 全部 No such file or directory；git 历史显示 docs/decisions/ 下 ADR-0001~0008 共 8 份在 b47d6d9「chore: prepare public release」被整体删除，而 MVP_PLAN.md/FETCH_LAYER_SPEC.md/CLI_STATUS_FLAG_SPEC.md 从未入库（git log --all 无记录），即公开仓库从第一天起这些引用就是死链。(2) 引用点逐一核实无误：CONTRACT.md:38 明文要求 breaking change「必须先在 docs/decisions/ 写 ADR」、§9(L2011-2016) 协议变更流程第 2 步要求写入不存在的目录且第 4 步要求「更新 ADR 链接」；DEVELOPMENT_GUIDE.md:30 确规定「方向冲突时，以 PROJECT_CHARTER.md 和 MVP_PLAN.md 为准」，仲裁基准一半缺失；CONTRIBUTING.md:142 的协议改动流程同样指向缺失目录。(3) 运行时可复现：执行 .venv/bin/chinalaw discover --help 实际输出含「详见 docs/CLI_STATUS_FLAG_SPEC.md」（cli.py:521/534/554），终端用户直接可见死引用。(4) 无防护：全仓无「内部文档不随发布」类免责声明；b47d6d9 同 commit 修改过 CONTRACT.md/CONTRIBUTING.md 却漏清这些引用，属公开化清理不完整。(5) 范围实际比 finding 所列更广：src/ 下 fetch.py:3/552/851、discover.py:12/17、sources.py:84、identity.py:10、trace.py:3、adapters/spp_gov_cn.py:20、adapters/court_gongbao.py:16 等十余处 docstring 同样引用缺失文件。唯一轻微夸大处：CONTRIBUTING.md L156-162 虽链接失效但已内联 ADR-0004 要点四条，贡献者可见摘要（只是无法查阅完整版），该细节不影响结论。不改变运行时行为（648 测试全绿），但契约治理流程（issue→ADR→PR）在当前仓库结构下确实不可执行，用户可见帮助文本含死引用，维持 medium。


### [M18] fetching skill 核心恢复流程使用不存在的 --in-laws flag（正确为 --in），另有 rebuild-clean --force、stub_only 等虚构接口

- **位置**：`.claude/skills/chinalaw-fetching/SKILL.md:152`
- **分类 / 维度**：方向/文档 / 文档/方向

**缺陷描述**

skill 在『fetch 之后必跑 outline』这一被重点强调的关键纪律里，两处教 agent 使用 `search ... --in-laws "<法规>"`（L145、L152）。实测 `chinalaw search 二倍工资 --in-laws 民法典 --kind article` 直接 argparse 报错退出码 2；正确 flag 是 `--in`（chinalaw-using、CONTRACT §4.1 均为 --in）。agent 按此 skill 执行会在『避免条号试错』的推荐路径上当场失败，恰好复现 skill 自己警告的 turn 浪费。同文件还有两处虚构：L329 命令一览表列 `chinalaw rebuild-clean --force`（该命令无 --force flag，实测退出码 2，contract §4.13 也只有 --law/--norm/--dry-run/--limit）；L232/287 声称远程返回 stub 时『adapter 标记 stub_only 入库』——`grep -rn stub_only src/` 零命中。

**证据 / 复现**

```text
SKILL.md:152 `chinalaw search "二倍工资 按月" --in-laws "最高人民法院关于审理劳动争议案件适用法律问题的解释（二）" --kind article --format json` → 实测 exit 2；SKILL.md:329 `| \`chinalaw rebuild-clean --force\` | 已入库 → 重清洗 |` → 实测 `rebuild-clean --force --dry-run` exit 2；SKILL.md:232 `adapter 标记 \`stub_only\` 入库` → `grep -rn stub_only src/` 无输出
```

**修复建议**

L145/L152 的 `--in-laws` 改为 `--in`；L329 改为 `chinalaw rebuild-clean --law <name>`；删除或改写 stub_only 段落为实际存在的 `articles_coverage=stub` 语义。skills README 已有『skill 中出现的命令必须与 docs/CONTRACT.md 一致』的维护要求，建议加一个测试遍历 SKILL.md 内的 chinalaw 命令行做 argparse 干跑校验。

**对抗复核结论**：确认为真（复核后 severity=medium）

> 三处虚构接口全部实测复现，无任何防护或误读：(1) SKILL.md L145/L152 的 `--in-laws` 实测 `chinalaw search "二倍工资 按月" --in-laws "...解释（二）" --kind article --format json` → "error: unrecognized arguments" exit 2；cli.py:93/314 真实 flag 为 `--in`（仅内部 dest="in_laws"，疑为虚构来源），CONTRACT.md:507 与 chinalaw-using SKILL.md L84/L100 均为 `--in`，且后者明文告诫"不要给 search 用错误 flag 名"。(2) L329 `rebuild-clean --force` 实测 exit 2，--help 仅有 --law/--norm/--dry-run/--limit/--format/--db。(3) L232/L287 `stub_only` 全仓库 grep（排除 .venv）仅命中 SKILL.md 自身两处，src/ 零实现；真实语义为 articles_coverage∈{stub,seed}（service.py:788/1645、ensure.py:580），与 suggested_fix 吻合。SKILL.md 是 agent 直接执行的指令，无上游校验；错误恰位于"fetch 后必跑 outline/search-in-laws 避免条号试错"这一被加粗强调的核心纪律段。缓解因素仅为 argparse 报错清晰、agent 可自恢复（损失 1-2 turn），故维持 medium 不升级。


### [M19] README 声称 CI 会运行 scripts/check-public-fixtures 质量门禁，实际 CI workflow 从未执行该脚本

- **位置**：`README.md:153`
- **分类 / 维度**：方向/文档 / 文档/方向

**缺陷描述**

README『Initial Built-in Corpus』一节断言：『CI 会运行 scripts/check-public-fixtures，禁止 seed / stub、空条文、残缺覆盖和缺来源元数据的 fixture 进入公开发布集』；docs/DATA_INDEX.md §1 也写『CI / 发布前必须运行 scripts/check-public-fixtures』。但仓库唯一的 workflow `.github/workflows/test.yml` 只跑 compileall、unittest、ruff、构建 smoke 和安装 smoke，grep 全文件无任何 `check-public-fixtures` 或 fixture 门禁步骤。这意味着 README 向用户承诺的『随包 74 个 fixture 均为完整可引用规范』这一核心数据质量不变量并没有自动化守门——一个引入 seed/stub 或缺来源元数据 fixture 的 PR 可以绿灯通过 CI。对一个把『引用可追溯、fail loud』当首要质量规则的项目，公开承诺的门禁与实际 CI 不一致属于承诺失实。

**证据 / 复现**

```text
README.md:153-155 `CI 会运行 \`scripts/check-public-fixtures\`，禁止 seed / stub、空条文…`；`grep -n "fixture\|check-public" .github/workflows/test.yml` 无输出（workflow 中不存在该步骤）；脚本本身手动运行退出码 0，说明只是 CI 未接入而非脚本损坏
```

**修复建议**

在 .github/workflows/test.yml 的 test job 中加一步 `python scripts/check-public-fixtures`（离线、秒级）；或在补 CI 之前把 README/DATA_INDEX 的表述降级为『发布前必须手动运行』。

**对抗复核结论**：确认为真（复核后 severity=medium）

> 确认为真。(1) README.md:153-155 的描述性断言「CI 会运行 scripts/check-public-fixtures」与 DATA_INDEX.md:15-18 的「CI / 发布前必须运行」均已核实存在；(2) 通读 .github/workflows/test.yml 全文（test/lint/windows-smoke/install-smoke/build 五个 job），无任何步骤调用该脚本；全仓 grep 确认该脚本名仅出现在两个文档中，无 pre-commit、无 Makefile、无测试封装；(3) 复现：.venv/bin/python scripts/check-public-fixtures 输出 "public fixture check passed: 74 fixtures"（exit=0），证明脚本可用但确未接入 CI。反驳尝试均失败：tests/test_corpus.py:76-91 仅对 baseline profile 少数条目断言 status!="seed" 和 articles 非空，不覆盖 stub 状态、source_name/source_url/source_checked_at/source_hash 四个元数据字段、单条空文本和 coverage 标签，也不覆盖其余约 66 个 fixture；src/chinalaw/loader.py 将 "seed" 列为合法 status 且在 line 175 对缺失 source_hash 自动补值，故 CI 中批量加载全部 fixtures 的测试对劣质 fixture 不会报错。即一个引入 seed/stub 或缺来源元数据 fixture 的 PR 可以绿灯通过全部 CI job，README 公开承诺的自动化门禁确实不存在。维持 medium：当前 74 个 fixture 实际全部合规（脚本手动运行通过），属承诺失实/回归风险而非现存数据缺陷，且修复成本极低（CI 加一步离线秒级脚本，或降级文档表述）。


### [M20] maintaining skill 的 status 自检表引用不存在的字段（fts_status、source_freshness）且健康阈值错误（laws 实测 100+ vs 真实基线 49）

- **位置**：`.claude/skills/chinalaw-maintaining/SKILL.md:188`
- **分类 / 维度**：方向/文档 / 文档/方向

**缺陷描述**

skill『status 自检解读』表格教 agent 按以下字段判断 DB 健康：`laws` 健康值『≥ 仓库 fixture 数（实测 100+）』、`fts_status == enabled`（disabled 则『删 db 重建』）、`source_freshness`（超 30 天则重跑 sync）。实测 `chinalaw status --format json` 的输出键集合中根本没有 `fts_status` 和 `source_freshness` 字段（实际相关字段是 oldest_source_checked_at / oldest_freshness_days）；且对全部 74 个 fixture 完整加载后的全新数据库，`laws` = 49（因宪法/刑法等历次文本共享同一 stable id，fixture 文件数 ≠ law 行数）。按 skill 的『远低于 100+ → 重跑 sync --fixtures』规则，agent 会把一个完全健康的新装库诊断为异常并反复重装；按 fts_status 规则甚至可能建议删库重建。配套的 doctor.sh 读 `payload.get("source_freshness", {})` 也永远拿到空 dict，新鲜度检查静默失效。

**证据 / 复现**

```text
SKILL.md:188 `| \`laws\` | ≥ 仓库 fixture 数（实测 100+） | 远低于 → 重跑 \`sync --fixtures\` |`；SKILL.md:194-195 `fts_status` / `source_freshness` 行；实测：`sync --fixtures` 后 `status` 输出 `laws=49, articles=7516`，键集合为 [alias_agent, applicability_rules, articles, by_articles_coverage, by_level, by_status, categories, db_path, last_applicability_sync_at, last_sync_at, law_relations, laws, norm_clauses, norm_packs, norm_sources, oldest_freshness_days, oldest_source_checked_at, revisions, schema_version, seed_laws, stub_laws]——无 fts_status / source_freshness
```

**修复建议**

把自检表改为真实字段：laws 基线写实测值（当前 49，随 fixture 增长更新或改为『>0 且与 sync 输出 titles 数量级一致』）；删除 fts_status 行（或在 status 命令实现该字段后再写）；source_freshness 改为 oldest_source_checked_at / oldest_freshness_days；同步修正 doctor.sh 的 freshness 读取逻辑。

**对抗复核结论**：确认为真（复核后 severity=medium）

> 全部事实经复现确认为真，无审查员误读。(1) 复现：在 /tmp 全新库跑 `chinalaw --db /tmp/xxx.db sync --fixtures` 后 `status --format json` 输出 laws=49, articles=7516，键集合为 [alias_agent, applicability_rules, articles, by_articles_coverage, by_level, by_status, categories, db_path, last_applicability_sync_at, last_sync_at, law_relations, laws, norm_clauses, norm_packs, norm_sources, oldest_freshness_days, oldest_source_checked_at, revisions, schema_version, seed_laws, stub_laws]——确无 fts_status / source_freshness；service.py:2746-2770 的 status() 返回 dict 源码也证实这两个键从未存在。(2) laws=49 的机制确认：74 个 fixture 文件仅 49 个唯一 law id（如 flk-criminal-law-1997 被 16 个文件共享、2c909fdd... 被 11 个共享，历次文本入 revisions 而非新增 law 行），故 SKILL.md:188『≥ 仓库 fixture 数（实测 100+）』对全新健康库必然误判，『远低于 → 重跑 sync --fixtures』会导致无效重装循环；更危险的是 SKILL.md:194 fts_status 行的处置是『删 db 重建』，而 db 内含用户私域 norm_packs/norm_sources，误诊后照做会丢私域数据。(3) doctor.sh（.claude/skills/chinalaw-maintaining/scripts/doctor.sh）确实读 `payload.get("source_freshness", {}) or {}`，永远为空 dict，freshness 循环从不执行，90 天新鲜度检查静默失效（该脚本本身只在 laws/articles<=0 时报错，不会误报，但保鲜检查完全落空）。缓解因素核实：CLI 其实有内置 `chinalaw doctor` 命令（src/chinalaw/doctor.py:175 用的是正确字段 oldest_freshness_days），但 SKILL.md 的『相关命令一览』和正文完全未提及它，只指向自己的 doctor.sh，因此该缓解不经由 skill 路由、不构成防护。severity 维持 medium：属 skill 文档方向性错误，会导致 agent 误诊健康库、循环重装、并可能给出破坏性『删库重建』建议，且配套脚本部分功能静默失效，但不直接损坏代码路径本身。


### [M21] fetching skill 教 agent 用不存在的 CHINALAW_FETCH_THROTTLE_MS 环境变量『升级节流』，实际不生效

- **位置**：`.claude/skills/chinalaw-fetching/SKILL.md:258`
- **分类 / 维度**：方向/文档 / 文档/方向

**缺陷描述**

skill 在『合规节流』一节指导：遇到 WAF / HTTP 307 / IP rate limit 时 `export CHINALAW_FETCH_THROTTLE_MS=2000` 再 fetch。但 `grep -rn CHINALAW_FETCH_THROTTLE src/ scripts/` 零命中——该环境变量在整个代码库中不存在，adapter 只认构造参数 request_interval（默认 0.2–0.5s）。后果：agent 在已经触发官方源限流/反爬信号的场景下，误以为自己已把节流升到 2 秒，实际继续以默认间隔请求政府网站，与 COMPLIANCE.md『能更慢就更慢』的保守立场相反，且括号里『adapter 实测时再读取』的措辞掩盖了功能不存在的事实。这是合规敏感路径上的虚构接口。

**证据 / 复现**

```text
.claude/skills/chinalaw-fetching/SKILL.md:255-260 `# 升级节流（环境变量；adapter 实测时再读取）\nexport CHINALAW_FETCH_THROTTLE_MS=2000`；`grep -rn "CHINALAW_FETCH_THROTTLE" src/ scripts/` 无任何输出
```

**修复建议**

从 skill 删除该环境变量段落，改为已实现的降级动作（停止批量、改单部 ensure、间隔重试，与 AGENT_INSTALL_GUIDE 的 FLK 反爬处置一致）；若确实需要调节流，先在 adapter/fetch 层实现读取该 env 的逻辑并补测试，再写进 skill。

**对抗复核结论**：确认为真（复核后 severity=medium）

> 复现确认：设置 CHINALAW_FETCH_THROTTLE_MS=2000 后用 .venv/bin/python 导入 chinalaw.sources.ADAPTER_REGISTRY，全部 12 个 adapter 的 request_interval 仍为默认值（flk_npc 0.2s，其余 0.5s），环境变量完全不生效。全仓库 grep 该变量仅 SKILL.md:258 一处命中；src/ 中所有 os.environ 读取只涉及 snapshots/alias_agent/notices，与节流无关。git log --all -S 显示该 env 从未在 src/ 或 scripts/ 中实现过（仅初始提交时随 SKILL.md 引入），属虚构接口而非过时文档。且 adapter 均为模块级 default_adapter 单例（flk_npc.py:524），fetch.py/cli.py/service.py 从不传 request_interval，运行时根本不存在任何提高节流的途径——比 finding 描述更彻底。docs/COMPLIANCE.md §3「能更慢就更慢」的保守立场属实，skill 在已触发 WAF/限流信号的合规敏感场景给 agent 虚假的「已升到 2 秒」保证，误导后果成立。维持 medium：基线节流（0.2–0.5s clamp 至少 0.1s）仍生效，非实际代码缺陷，但发生在合规敏感路径且『adapter 实测时再读取』的措辞掩盖功能不存在，值得报告并按 suggested_fix 处理。


### [M22] searching skill 多个示例命令不可运行或静默返回空结果：--part 未加引号 argparse 报错、『合同编/总则编』过滤词与实际 part 数据格式不匹配、带版本号法名无法解析

- **位置**：`.claude/skills/chinalaw-searching/SKILL.md:187`
- **分类 / 维度**：方向/文档 / 文档/方向

**缺陷描述**

三组实测失败：(1) L187『经典 fly weight』示例 `outline 民法典 --part 合同编 第十一章 --full-text` 原样执行 exit 2（『第十一章』成为多余位置参数）；加引号后 item_count=0，因为民法典条文的 part 实际存储为『第三编 合同 第X章 …』格式，不含『合同编』字样（且民法典债权转让在合同编第六章，示例的『第十一章』在内容上也不成立）。(2) L207 及 L217 断言 `search 公序良俗 --kind article --in-part "总则编"`『对总则编/物权编/合同编…都生效』——实测 0 hits（part 为『第一编 总则』，改用 "总则" 才有 4 hits）。--in-part 过滤是子串匹配，用『X编』写法必然静默空结果，agent 会据此错误得出『无相关条文』的结论，这比报错更危险。(3) L111 `article 公司法（2023 修订） 第32条` 与 walkthroughs.md:30 `article "公司法（2018 修正）" 32` 中带版本括注的法名在本地库无法解析（实测 exit 1），且前者未加引号在 shell 中还会因全角括号内空格被拆参。

**证据 / 复现**

```text
实测：`outline 民法典 --part 合同编 第十一章 --full-text` → exit 2；`outline 民法典 --part "合同编 第十一章"` → item_count=0；`outline 民法典 --part 第十一章` → 24 条（属其他编的第十一章）；`search 公序良俗 --kind article --in-part 总则编` → hits 0，`--in-part 总则` → hits 4；part 实际值形如 `第一编 总则 第一章 基本规定`；`article "公司法（2023 修订）" 第32条` → exit 1
```

**修复建议**

示例统一改为与数据格式一致的过滤词（如 `--part "合同"`、`--in-part "总则"`）并给 --part 值加引号；在 skill 中明确『part 过滤是对 “第X编 …” 原文的子串匹配，不要用“X编”简写』；带版本的取条示例改为 walkthroughs 中已有的 law_id 形式（`article flk-company-law-2018 32`）。更根本的修复是让 service 层把『合同编/总则编』等常用写法归一到实际 part 前缀。

**对抗复核结论**：确认为真（复核后 severity=medium）

> 三组断言全部实测复现且无防护可反驳。(1) `outline 民法典 --part 合同编 第十一章 --full-text` 原样执行 exit 2（unrecognized arguments: 第十一章）；加引号后 exit 0 但 item_count=0。直查 ~/.chinalaw/chinalaw.db：民法典 110 个 distinct part 无一含"合同编/总则编"，合同编 part 实为"第一分编 通则​ 第X章…"/"第二分编 典型合同 第X章…"（比 finding 说的"第三编 合同 第X章"更不匹配，问题更重）；`--part 第十一章` 命中 24 条（土地承包经营权+赠与合同），而债权转让实际在"第一分编 通则 第六章 合同的变更和转让"（art 546 实测），示例内容错误也成立。(2) `search 公序良俗 --kind article --in-part 总则编` → total 0（静默空）；`--in-part 总则` → 4 hits（part="第一编 总则 第一章 基本规定"）。service.py:1077-1079 证实 in_part 为裸 `a.part LIKE %…%` 子串匹配，无"X编"归一化，SKILL.md L216-217"对总则编/物权编/合同编都生效"的断言为假。(3) `article "公司法（2023 修订）" 第32条` 与 walkthroughs.md:30 的 `article "公司法（2018 修正）" 32` 均 exit 1、found=false reason=law_missing（且 hint 误导去 fetch，而 `article 公司法 32` 实际能命中 flk-company-law-2024）；未加引号时全角括号内空格拆参 → argparse exit 2。全文无"示例仅示意"类免责说明，aliases 层不处理版本括注。唯一出入是 finding 对 part 存储格式的描述略不准确，但方向上使问题更严重。维持 medium：属 skill 文档缺陷而非代码 bug，但 --in-part "X编" 的静默空结果会引导 agent 得出"无相关条文"的错误法律结论，是实质风险。


### [M23] CONTRACT §4.3 article 未命中 JSON schema 与实现不符：law_missing 与 article_null 两种形态各缺一批契约字段

- **位置**：`docs/CONTRACT.md:643`
- **分类 / 维度**：方向/文档 / 文档/方向

**缺陷描述**

契约给出单一统一的未命中 schema，包含 `name`、`number`、`law`、`article`、`requested_number`、`fallback_sources`、`sibling_laws` 等键（仅 suggested_* 标注可省略）。实测实现输出两种互不相同的形态：reason=article_null 时键集合为 {article, as_of, found, hint, item, law, law_id, reason, requested_number, sibling_laws, suggested_fetch, suggested_outline, suggested_sibling_articles}——缺契约中的 `name`、`number`、`fallback_sources`；reason=law_missing 时键集合为 {as_of, fallback_sources, found, hint, law_id, name, number, reason, suggested_fetch}——缺契约中标注为 `Law|null`/`null` 应存在的 `law`、`article`、`item`、`requested_number`、`sibling_laws`。CONTRACT §1 承诺『删除或重命名字段算 breaking』且该文档是『重写实现者的唯一依据』；按文档 schema 解析这两种 payload 的 agent/重写实现会 KeyError 或取不到字段。文档宣称的字段并非按 reason 条件化标注，属于契约描述失真。

**证据 / 复现**

```text
docs/CONTRACT.md:643-661 统一 miss schema（含 `"name": "民法典"`、`"number": "9999"`、`"requested_number": "9999"`、`"fallback_sources": [...]`、`"sibling_laws": [...]` 同列一个对象）；实测 `article 民法典 9999 --format json`（exit 1）无 name/number/fallback_sources 键；`article 完全不存在的法规 1 --format json`（exit 1）无 law/article/item/requested_number/sibling_laws 键
```

**修复建议**

两种收敛方向择一：在实现侧统一补齐字段（law_missing 也输出 law:null/article:null/requested_number，article_null 也输出 name/number，fallback_sources 恒在）；或在 CONTRACT §4.3 按 reason 分别描述两种形态并明确哪些键条件存在。同时把 fallback_sources 示例值从三源更新为当前实际的六源列表。

**对抗复核结论**：确认为真（复核后 severity=medium）

> 复现确认，finding 事实全部准确，且实际比描述更严重（存在第三种形态）。复现：(1) `article 民法典 9999 --format json`（exit 1，reason=article_null）键集合为 {article, as_of, found, hint, item, law, law_id, reason, requested_number, sibling_laws, suggested_fetch, suggested_outline, suggested_sibling_articles}，确无契约中的 name/number/fallback_sources；(2) `article 完全不存在的法规 1 --format json`（exit 1，reason=law_missing）键集合为 {as_of, fallback_sources, found, hint, law_id, name, number, reason, suggested_fetch}，确无 law/article/item/requested_number/sibling_laws/suggested_sibling_articles；(3) 补充发现 `--as-of bad-date`（reason=invalid_as_of）键集合仅 {as_of, found, hint, law_id, name, number, reason}，缺失更多——而 invalid_as_of 正列在同一统一 schema 的 reason 枚举里。根因在源码可见：/Users/huoxihuo/chinalaw-cli/src/chinalaw/service.py:1581-1689 的 diagnose_article_miss 五个分支各返回不同键集，/Users/huoxihuo/chinalaw-cli/src/chinalaw/cli.py:1281-1307 又把 diag 合并到两种不同基座上（law_missing 基座是 {found,name,number}，article_null 基座是含 law/article/item/requested_number 的完整 payload），结构上必然产生多形态。反驳角度均不成立：docs/CONTRACT.md §4.3（643-661 行）确为单一 schema，且该文档有明确的条件字段标注惯例——§4.1 用「omitted when matched=false」、§4.3 仅对 suggested_fetch/outline/history 三键标「| omitted」——未标注的键按此惯例应恒在，不存在审查员误读；全文 grep 无任何「按 reason 条件存在」的说明；§1 第 43 行明文「删除或重命名字段算 breaking」，文档开头自设标准「Rust/Go 重写者读这份文档够不够」。fallback_sources 示例三源 vs 实现六源（service.py:1564-1571）亦属实。唯一弱化因素：640 行 prose 只强制 found=false 与 reason 两键，且本仓库自身消费方（mcp.py 包一层 diagnosis、formatters 用 .get）不受影响，属纯文档契约失真而非运行时 bug，故维持 medium 不升级。


### [M24] 民法典物权编、合同编共784条的 part 缺少编名，且17个 part 值混入 U+200B 零宽空格作为唯一区分

- **位置**：`data/fixtures/civil_code.json:1668`
- **分类 / 维度**：数据 / 出厂数据质量

**缺陷描述**

第205-462条（第二编 物权）与第463-988条（第三编 合同）的 part 字段没有编级前缀，直接以『第一分编 通则』『第二分编 典型合同』开头，而第一、四、五、六、七编都带有『第X编 XX』前缀，体例不一致。更隐蔽的是：物权编的『第一分编 通则 第一章 一般规定』与合同编的『第一分编 通则​ 第一章 一般规定​』仅靠不可见的零宽空格区分（共17个 part 值含 ​，最多连续5个，应为 FLK 页面残留），肉眼与下游文本处理完全无法分辨。后果：`chinalaw article 民法典 300` 等输出的 part 无法告知条文属于哪一编，agent 生成『民法典第二编 物权 第X条』类引用定位时缺关键信息，零宽字符还会原样进入 JSON 输出与引用文本。

**证据 / 复现**

```text
art 205: "part": "第一分编 通则 第一章 一般规定"（无『第二编 物权』前缀）；art 463 起: '第一分编 通则​ 第一章 一般规定​'；扫描输出 parts containing zero-width chars: 17，如 '第五编 婚姻家庭 第一章 一般规定​​​'
```

**修复建议**

规范化 part：为物权编/合同编补上『第二编 物权』『第三编 合同』前缀，全量剥离 ​ 等零宽字符；在 cleaning.canonicalize 中加入 Unicode 不可见字符过滤，避免上游页面残留再次进入出厂数据。

**对抗复核结论**：确认为真（复核后 severity=medium）

> 复现确认为真。(1) 编名缺失：fixture 中第205-988条共784条的 part 无『第X编』前缀（全库仅另有附则2条无前缀），art205='第一分编 通则 第一章 一般规定'、art463 起为合同编同名值，而第一/四/五/六/七编均带编名前缀，体例不一致属实。根因在 cleaning.py:813 `_update_context`：含『分编』的标题因同时含『编』字被当作 book 级覆写 context["book"]，『第二编 物权』被『第一分编 通则』顶掉。(2) 零宽字符：扫描确认恰好17个 part 值含 U+200B（与 finding 完全一致），剥离后全库仅一对碰撞——物权编205-208与合同编463-468的 part 仅靠 ZWSP 区分。(3) 无任何防护：cleaning.py `_clean_text` 仅做 `re.sub(r"\s+"," ")`，U+200B 为 Cf 类不匹配 `\s`，`str.strip()` 也不剥离（已用 Python 验证）；全 src 无零宽过滤代码；loader.py:265 将 part 原样写库。(4) 端到端影响：临时库加载全部 fixtures 后，`chinalaw --db … article 民法典 300` 输出 part='第二分编 所有权 第八章 共有'（无编名），`article 民法典 463` JSON 输出 part 含原样 ​。且 part 并非纯展示字段——service.py:1078/2141 用 `part LIKE ?` 支撑 `search --in-part` 与 `outline --part`：实测 '通则 第一章' 因 ZWSP 插在『通则』后而无法子串匹配合同编条目（静默漏检），按『第二编 物权』过滤则对784条全部落空。finding 唯一轻微不精确处：17个含 ZWSP 值中仅1对（2值）真正靠 ZWSP 作唯一区分，其余15个无碰撞，但其 evidence 原文表述与此相符。维持 medium：影响最常用法规784条的定位元数据与 part 过滤功能，但条文正文正确、按条号引用（主路径）不受影响，不致 high。


### [M25] add_item_to_pack 创建路径用模糊匹配重解析新 pack id，成员会静默落入另一个既有规范包

- **位置**：`src/chinalaw/normpacks.py:566`
- **分类 / 维度**：数据 / 规范层（时间效力）

**缺陷描述**

add_item_to_pack 第一段连接中按用户输入解析 pack 未命中且 create=True 时，用 _pack_id_from_name 生成新 slug；第二段连接却调用 _resolve_pack_row(conn, pack["id"]) 重解析，而 _resolve_pack_row 在精确匹配失败后会退回 LIKE '%slug%' 模糊匹配。若库中已有 id/name 包含该 slug 的其他 pack（如已存在 contract-review-2024，用户 --create 新建 'contract review'，slug 为 contract-review），第二次解析会模糊命中 contract-review-2024，于是既不创建新 pack，成员也被静默追加进错误的规范包，返回值 pack_id 直接变成别人的包。agent 工作流沉淀的规范依据落错容器且无任何告警。

**证据 / 复现**

```text
复现：
add_item_to_pack(db, "contract-review-2024", {...}, create=True)  # 先建既有包
add_item_to_pack(db, "contract review", {...}, create=True)
-> requested new pack 'contract review' -> item actually landed in pack: contract-review-2024
packs now: [('contract-review-2024', 2)]  # 新包从未创建
根因：normpacks.py L566 row = _resolve_pack_row(conn, pack["id"]) 复用了带模糊回退的解析（L437-448 LIKE 分支）。
```

**修复建议**

第二段连接（以及 L589 插入后的确认查询）应使用仅按 id 精确匹配的查询（SELECT ... WHERE p.id = ?），不要复用带 LIKE 回退的 _resolve_pack_row；模糊解析只应作用于用户原始输入那一次。

**对抗复核结论**：确认为真（复核后 severity=medium）

> 已在 /Users/huoxihuo/chinalaw-cli/.venv 中用最小用例完整复现，与 finding 描述逐字吻合。复现脚本：先 add_item_to_pack(db, "contract-review-2024", {reference}, create=True) 建包；再 add_item_to_pack(db, "contract review", {reference}, create=True)。实际输出：第二次调用返回 pack_id=contract-review-2024（而非新建 contract-review），packs now: [('contract-review-2024', 2)]，两条 item 全部落在 contract-review-2024，新包从未创建。根因确认：normpacks.py L516 第一段用原始输入 "contract review"（含空格）解析，精确与 LIKE '%contract review%' 均不命中（既有包 id/name 是 "contract-review-2024"，连字符），走 create 分支生成 slug "contract-review"；L566 第二段却对该 slug 调用 _resolve_pack_row，其 L437-448 的 LIKE '%contract-review%' 回退命中 p.id='contract-review-2024'，于是走既有包路径插入。调用方无防护：cli.py _handle_pack 的 pack add 直接透传 args.name/--create；tests/test_core.py 现有 4 个 add_item_to_pack 测试均未覆盖 slug 与既有包 id 子串碰撞场景。唯一减轻因素：返回值与 markdown 输出（formatters.py pack_item_add_to_markdown L1313）会显示实际落入的包名/id，细心的人类可事后发现，但无任何 warning，操作以成功态返回，对该函数明示的 agent 工作流场景（docstring：「agent 工作流沉淀入口」）就是静默落错容器。触发需要 create 路径 + 新 slug 是既有包 id/name 的子串且原始输入本身不模糊命中（空格 vs 连字符即满足，带年份后缀的包名如 -2024 很常见），数据落错但可恢复、无丢失，维持 severity=medium。suggested_fix 合理：L566 处应仅按 id 精确匹配（SELECT ... WHERE p.id = ?），模糊回退只用于用户原始输入那一次；L589 插入后的确认查询在精确匹配下会命中新插入行，一并改为精确查询即可。


### [M26] 8个司法解释 fixture 的 document_number 为 null，文号反查索引对出厂数据失效（含 schema 注释示例 法释〔2023〕13号 本身）

- **位置**：`data/fixtures/contract_chapter_interpretation_2023.json:13`
- **分类 / 维度**：数据 / 出厂数据质量

**缺陷描述**

schema.py v8 专门建立 document_number_index，注释明确以『法释〔2023〕13号』（即本文件合同编通则解释）为例说明 `chinalaw fetch "法释〔2023〕13号"` 可绕过远程标题搜索直接命中。但该 fixture 及另外7个司法解释 fixture（civil_evidence_provisions、civil_procedure_interpretation、commercial_housing_sale_interpretation、financial_leasing_interpretation、independent_guarantee_interpretation、private_lending_interpretation、sale_contract_interpretation）的 document_number 均为 null，loader 经 index_document_number 写不进任何索引行，导致这8部出厂解释按文号检索必然本地 miss、退化为联网搜索。文号是这些文件在法律社区里最稳定的引用形式（法释〔2023〕13号、法释〔2022〕11号、法释〔2020〕17号等均为公开可查），属于可直接补齐的出厂数据缺口。

**证据 / 复现**

```text
contract_chapter_interpretation_2023.json:13 "document_number": null；扫描输出8个 judicial_interpretation fixture document_number missing；schema.py v8 注释：『让 ``chinalaw fetch "法释〔2023〕13号"`` 可绕过远程标题搜索直接命中』
```

**修复建议**

为这8个 fixture 补齐官方文号（合同编通则解释=法释〔2023〕13号、民诉法解释2022修正=法释〔2022〕11号等，逐一与公布文本核对），并在数据出厂校验中对 level=judicial_interpretation 且 document_number 为空的条目告警。

**对抗复核结论**：确认为真（复核后 severity=medium）

> 已完整复现确认。(1) 扫描 data/fixtures 全部 judicial_interpretation fixture：12 部有文号、8 部 document_number 为 null，名单与 finding 完全一致。(2) fixture 加载走 cleaning.canonicalize_external_json（cleaning.py:132 仅 setdefault(None)，不做文号抽取；抽取逻辑只存在于 flk_npc_detail 路径），document_numbers.index_document_number 对空文号在 86-88 行静默跳过。(3) 临时 DB 加载 74 个 fixture 后复现：document_number_index 仅 12 行；_lookup_document_number_hint(db, "法释〔2023〕13号", "flk_npc") 返回 None——即 schema.py:293 v8 注释亲自举例的『chinalaw fetch "法释〔2023〕13号" 可绕过远程标题搜索直接命中』对本 fixture 自身失效，退化为远程标题搜索；对照组 法释〔2022〕6号/法释〔2020〕28号 正常命中 hint，证明机制本身工作、缺的只是数据。(4) 无任何防护：service._resolve_law_row（545-604 行）只匹配 id/title/short_title/aliases，不查 laws.document_number，文号输入连本地 alias 兜底都没有；audit.py/doctor.py/sync.py/scripts 均无 document_number 完整性校验。缓解因素：这 8 部解释按标题/别名仍可本地正常检索（"合同编通则解释"可解析到 court-contract-interpretation-2023），影响限于文号检索路径退化，非硬故障，故维持 medium 不升不降。


### [M27] applicability 种子里的旧法 id（flk-contract-law-1999 等5个）与 fetch 的 canonical id 机制脱节，needs_fetch 闭环永不收敛

- **位置**：`data/applicability/contract-validity.json:25`
- **分类 / 维度**：正确性 / 出厂数据质量

**缺陷描述**

6个 applicability 数据集中引用了5个尚未入库的旧法 id：flk-contract-law-1999、flk-property-law-2007、flk-security-law-1995、flk-tort-liability-law-2009、flk-company-law-2018。service._rule_row_to_dict/_law_summary_by_id 只按精确 id 关联 laws 表，并在未命中时输出 law_missing『需要先 fetch』。但 fetch.fetch_law 的 canonical id 解析（fetch.py `_resolve_canonical_id`）只会复用 DB 中已存在同名 row 的 id，DB 中不存在这些旧法时，新抓取的合同法/物权法等会以 FLK 原始 bbbs（十六进制）作为 law id 入库，且 fetch 不提供指定目标 law id 的参数。结果是：用户按提示 fetch 完旧法后，`chinalaw applicable` 仍然永远报 `flk-contract-law-1999 尚未入库`，rule 的 primary_law/fallback_law 永远为 null，law_relations 的 to_law 同样解析不到。已在临时库实测复现：入库标题为『中华人民共和国合同法』的 law 后，applicable 输出 needs_fetch 仍含 flk-contract-law-1999，warnings=['law_missing','law_missing']。这条『applicable → needs_fetch → fetch → 复查』的宣传工作流对全部5个旧法 id 均无法闭环。

**证据 / 复现**

```text
复现输出：primary_law_id: flk-contract-law-1999 / primary_law resolved: None / needs_fetch: [{'law_id': 'flk-contract-law-1999', 'reason': 'missing_law'}, ...]（DB 中已存在 title=中华人民共和国合同法 的 law row）；fetch.py `_resolve_canonical_id` 仅匹配 DB 既有同名 row，applicability_rules 中的 id 不参与解析
```

**修复建议**

二选一：(a) 在 fetch 侧让 canonical id 解析同时查询 applicability_rules/law_relations 中登记的 stable id（同名+同期匹配则采用该 id 入库）；(b) 在 _law_summary_by_id 侧为 applicability/relation 增加按 primary_law_title 的兜底解析。同时为这5部旧法提供 seed/stub fixture（含 stable id 与别名）也可直接消除脱节。

**对抗复核结论**：确认为真（复核后 severity=medium）

> 已在临时库独立复现，finding 各项陈述均准确：(1) 5 个旧法 id 确实只存在于 data/applicability/*.json（flk-contract-law-1999→contract-validity.json、flk-property-law-2007、flk-security-law-1995、flk-tort-liability-law-2009、flk-company-law-2018 各对应一个数据集），data/fixtures/ 全部 79 个 fixture 的 id 均不含这 5 个，因此 sync 装 fixture 也无法补上（对比：flk-civil-code-2020 有 civil_code.json fixture 可收敛）。(2) cleaning.py L208 `law_id = bbbs or ...` 确认新抓 payload 的 id 就是 FLK 十六进制 bbbs；fetch.py `_resolve_canonical_id` 只查 laws 表的 id/title/short_title/aliases，applicability_rules/law_relations 不参与解析，且 `chinalaw fetch --help` 确认无任何指定目标 law id 的参数（--prefer-id 只是远程候选选择器）。(3) 端到端复现：temp DB 装载内置 applicability 种子（12 rules/5 relations）后，构造 title=中华人民共和国合同法、id=raw bbbs 的 payload——`fetch._try_resolve_canonical_id` 入库前返回 None（保留 bbbs）、入库后返回该 bbbs 本身（永远不会是 flk-contract-law-1999）；随后 `service.applicable(as_of=2019-01-01, topic=合同效力)` 输出 primary_law_id=flk-contract-law-1999、primary_law=None、needs_fetch 仍含 {'law_id':'flk-contract-law-1999','reason':'missing_law'}、warnings 含 law_missing——与 finding 的 evidence 完全一致。(4) 无任何调用方防护或兜底：service._law_summary_by_id 仅精确 id 匹配（service.py L683），applicability.py 导入层不做 law id 解析，全仓 grep 也无 alias 表登记这 5 个 id；而 README L184 和 chinalaw-fetching SKILL.md 明确宣传 needs_fetch→fetch 补全工作流，skill 的「跨期旧法补全」一节给出的正是 discover --status repealed → fetch --prefer-id <bbbs> 路径，按此操作后闭环永不收敛。缓解因素（维持 medium 不升 high）：fetch 后的旧法仍可按 title/alias 经 resolve/article/search 正常引用（复现中 resolve 合同法 matched=True），受损的是 applicable/relation 报告的关联解析与 needs_fetch 诊断信号本身，属于误导性诊断而非数据不可达；且 test_core.py L5077 只断言 fetch 前 needs_fetch 存在，无测试覆盖 fetch 后收敛，说明该缺口未被基线感知。


### [M28] 时间效力规则导入不校验日期格式，非 ISO 日期会让 applicable 按字典序比较静默漏配规则

- **位置**：`src/chinalaw/applicability.py:191`
- **分类 / 维度**：数据 / 规范层（时间效力）

**缺陷描述**

_upsert_rule/_upsert_relation 把 effective_from/effective_to/effective_at 原样写库，不做任何 YYYY-MM-DD 校验；而 service.applicable 用 SQL 字符串比较（effective_from <= ? AND effective_to >= ?）判断规则是否适用。人工维护的 fixture 一旦写成 '2021/01/01'、'2021年1月1日' 等变体，导入照常成功，但同年内的 as_of 查询会因 '/'、'年' 与 '-' 的 ASCII 序差异得到错误比较结果，规则被静默排除——applicable 返回 no_applicability_rule，agent 误以为本地无新旧法适用线索。该模块自述职责就是『把人工审核过的规则写入本地库』，校验缺失直接破坏时间效力查询这一核心正确性。同时必填字段缺失（如 relation_type/topic/rule_text）会抛裸 KeyError 而非可读错误。

**证据 / 复现**

```text
复现：导入 {"effective_from": "2021/01/01", ...} 成功（rules_loaded: 1）；
service.applicable(db, as_of="2021-06-01", topic="测试时间效力") -> match_count = 0
（应命中；因 "2021/01/01" <= "2021-06-01" 为 False，'/'(0x2F) > '-'(0x2D)）
写入点 applicability.py L191-192 rule.get("effective_from")/rule.get("effective_to") 未经校验；查询点 service.py L2582-2584 为纯字符串比较。
```

**修复建议**

在 _upsert_rule/_upsert_relation 写库前用 date.fromisoformat 校验 effective_from/effective_to/effective_at（None 允许），非法格式抛出带文件路径与字段名的 ValueError 使整个导入回滚；必填字段用显式检查代替 dict[key] 裸取，报错信息包含 rule/relation 的 id 或 topic。

**对抗复核结论**：确认为真（复核后 severity=medium）

> 复现完全成立。在临时库中用 .venv/bin/python 实测：(1) 导入 {"effective_from": "2021/01/01", "effective_to": "2025/12/31", ...} 成功返回 rules_loaded=1，无任何警告；(2) service.applicable(as_of="2021-06-01", topic="测试时间效力") 返回 match_count=0，且 warnings 只有 not_legal_conclusion 和误导性的 no_applicability_rule；对照组用 ISO 日期 "2021-01-01" 则 match_count=1，证实是 '/'(0x2F)>'-'(0x2D) 的字典序比较所致；(3) "2021年1月1日" 变体同样静默漏配（match_count=0）；(4) 缺 primary_law_id 时抛裸 KeyError('primary_law_id')。防护核查均为否定：applicability.py L191-192/L134 原样写库；schema.py L227-281 的 law_relations/applicability_rules 无任何 CHECK 约束；service.py L2582-2584 纯字符串比较；_rule_row_to_dict（service.py L797）只产出 law_missing/law_stub/law_seed 警告，不检查日期格式；doctor.py 无 effective_from/fromisoformat 相关检查。触发路径是用户可达的：cli.py L1548-1551 的 sync --applicability --applicability-dir 直接加载用户目录下的人工维护 JSON，模块自述职责就是导入「人工审核过的规则」。且查询侧 applicable 对 as_of 参数有 _parse_iso_date 校验并报 invalid_date 错误（service.py L2556/L2575），说明代码库自身约定期望 ISO 校验，唯独导入侧缺失，形成不对称。缓解因素：当前 6 个内置 fixture（data/applicability/*.json）经脚本核验全部为合法 ISO 日期，已发布数据未受影响，触发需要未来人工新增/用户自带 fixture 写错格式。综合：缺陷真实、静默、破坏模块核心正确性，但需畸形人工输入才触发且现有数据干净，维持 medium。


### [M29] pack import/add 遇到 item id 或 pack name 冲突时抛裸 sqlite3.IntegrityError，CLI 无兜底直接 traceback

- **位置**：`src/chinalaw/normpacks.py:727`
- **分类 / 维度**：错误处理 / 规范层（时间效力）

**缺陷描述**

norm_pack_items.id 是全局主键、norm_packs.name 有 UNIQUE 约束，但 import_pack_from_dict 只对 norm_packs.id 做了 ON CONFLICT 处理：(1) payload 内两个成员显式 id 重复；(2) 成员显式 id 与另一个 pack 的成员 id 撞车（id 全局唯一，跨包也冲突）；(3) 导入的 pack id 不同但 name 与既有 pack 相同——三种情形都会抛未捕获的 sqlite3.IntegrityError。cli.py pack import 分支（L2132-2135）没有任何 try/except，用户复制修改导出 JSON（改了 name 忘改成员 id 是常见操作）时直接看到原始 traceback；add_item_to_pack 传显式 id 时同样裸抛（该异常不是 ValueError，CLI 的 ValueError 兜底接不住）。

**证据 / 复现**

```text
$ chinalaw --db ... pack import /tmp/pack_dup.json  # items 两条 id 均为 "same"
Traceback ... File "normpacks.py", line 727, in import_pack_from_dict
    conn.execute(
sqlite3.IntegrityError: UNIQUE constraint failed: norm_pack_items.id
库层复现：add_item_to_pack(db, "otherpack", {..., "id": "shared-id"}, create=True)（id 已在 mypack 使用）同样 IntegrityError。
```

**修复建议**

在 _normalize_pack_payload 中先做 payload 内成员 id 去重校验并抛 NormPackError；import_pack_from_dict/add_item_to_pack 捕获 sqlite3.IntegrityError 转成带上下文（哪个 id/name 冲突）的 NormPackError；cli pack import 分支加与 pack add 一致的 NormPackError/ValueError 处理并映射退出码。

**对抗复核结论**：确认为真（复核后 severity=medium）

> 三种情形全部在临时库复现，finding 陈述准确且略有保守。(1) payload 内两成员 id 均为 "same"：`chinalaw --db t.db pack import pack_dup.json` 直接输出裸 traceback，止于 normpacks.py:727 `sqlite3.IntegrityError: UNIQUE constraint failed: norm_pack_items.id`，退出码 1；(2) 跨包成员 id 撞车（pack-a 已有 id "shared-id"，导入 pack-b 同 id 成员）同样在 L727 裸抛——L725 的 DELETE 只清理本包成员，跨包冲突无防护；(3) pack id 不同但 name 相同：在 L687 抛 `UNIQUE constraint failed: norm_packs.name`（db.py L132 确认 name 有 UNIQUE 约束，而 INSERT 只写了 ON CONFLICT(id)）。库层 `add_item_to_pack(db, "otherpack", {..., "id": "shared-id"}, create=True)` 亦复现，且 isinstance 检查确认 IntegrityError 既非 NormPackError 也非 ValueError。调用方无任何防护：cli.py L2132-2135 的 pack import 分支无 try/except，app()（L2351-2355）只捕获 BrokenPipeError——连 `_normalize_pack_payload` 抛的 ValueError("norm pack requires name") 在 pack import 下也裸 traceback（pack add 分支有 L2101/L2112 兜底，import 分支没有对齐）。加重情节：项目自带的预检工具 `pack validate pack_dup.json --file` 对重复 id payload 返回 ok:true、零 issue，用户按推荐流程先 validate 再 import 仍会撞上 traceback。两点更正（不影响结论）：其一，CLI `pack add` 无 --id 参数（parser 定义 L930-975 确认），add_item_to_pack 显式 id 裸抛仅经 Python API 可达，CLI 直接触发面比描述略窄；其二，db.py connect()（L38-47）在异常时 rollback，失败导入不会留下半写状态，无数据损坏风险，故不升为 high。维持 medium：用户手改导出 JSON 是常规操作、validate 也拦不住，但后果仅是难看的 traceback + 退出码 1，数据完整性无损。


## 三、Low 问题（6 条，经复核确认）


### [L1] snapshot_json 损坏时 json.loads 未捕获异常，单条坏记录使整个 as-of 查询崩溃

- **位置**：`src/chinalaw/service.py:917`
- **分类 / 维度**：错误处理 / 核心业务逻辑

**缺陷描述**

_build_law_from_revision_snapshot 中 snapshot = json.loads(snapshot_json) 没有 try/except，且 _snapshot_to_law 里 snapshot["id"]/["title"]/["level"]/["status"]/["source_url"] 都是裸下标访问。本文件其他所有 JSON 解析（_row_to_law、_row_to_norm_source、_json_from_row、_aliases_for_law_row 等）都统一捕获 JSONDecodeError，唯独这条快照路径例外。DB 中一条被截断/损坏的 snapshot_json（写入中断、手工编辑、字段缺失）会让 get_law_as_of / get_article_as_of / get_articles(as_of) / diff_law_as_of 直接抛未处理异常，CLI 崩溃出 traceback。已复现：UPDATE revisions SET snapshot_json='{corrupt' 后 get_law_as_of 抛 json.decoder.JSONDecodeError。

**证据 / 复现**

```text
snapshot_json = revision.get("snapshot_json")
if snapshot_json:
    snapshot = json.loads(snapshot_json)   # 无 try/except
    law = _snapshot_to_law(snapshot)       # 内部裸 snapshot["id"] 等
复现输出：E) CRASH: JSONDecodeError Expecting property name enclosed in double quotes: line 1 column 2 (char 1)
```

**修复建议**

包一层 try: snapshot = json.loads(snapshot_json); law = _snapshot_to_law(snapshot) except (json.JSONDecodeError, KeyError, TypeError): 落到后续 content_hash 分支或 return None，并可附 warning（如 revision_snapshot_corrupt）提示用户重新 fetch/rebuild。

**对抗复核结论**：确认为真（复核后 severity=low）

> 确认为真。(1) service.py:917 json.loads 无防护、_snapshot_to_law(884-907) 裸下标属实；三个调用点(1492/1881/2070)及 diff_law_as_of(2796) 均无 try/except，cli.py app()/main() 无全局兜底。(2) 惯例不一致属实：_row_to_law(148-151) 捕获 JSONDecodeError，rebuild.py _latest_snapshot(336-340) 对同一 snapshot_json 字段既捕 JSONDecodeError 又校验 isinstance(dict)。(3) 已在临时 DB 独立复现：UPDATE revisions SET snapshot_json='{corrupt' 后 get_law_as_of/get_article_as_of/get_articles(as_of) 全抛 JSONDecodeError；'{"id":"demo-law"}' 抛 KeyError 'title'；'[1,2]' 抛 TypeError；真实 CLI 命令 chinalaw --db ... get demo-law --as-of 2021-01-01 输出完整 raw traceback。但严重度应下调：snapshot_json 唯一写入路径 loader._build_snapshot_json 用 json.dumps 生成且必备 key 写入时强制存在，SQLite 事务性排除截断写入，正常运行不可能产生坏数据；触发前提限于手工改库/外部工具/文件级损坏，属带外损坏边缘场景，故 medium→low。修复建议（对齐 rebuild.py 的防护并落到 content_hash 分支或 return None + warning）合理。


### [L2] find_cited_by 对条号≥10000 触发 IndexError 崩溃（_arabic_to_chinese_numeral 单位表越界）

- **位置**：`src/chinalaw/service.py:2220`
- **分类 / 维度**：错误处理 / 核心业务逻辑

**缺陷描述**

_arabic_to_chinese_numeral 的单位表 _CHINESE_UNITS = ["", "十", "百", "千"] 只覆盖 4 位数；当 value≥10000 时 unit = _CHINESE_UNITS[length - index - 1] 下标越界抛 IndexError。find_cited_by 对任何解析出的条号无上限校验就调用 _build_cited_by_pattern → _arabic_to_chinese_numeral(int(normalized_number))，因此 CLI 命令 `chinalaw cited-by 民法典:12345`（用户笔误或 agent 生成的大条号）直接崩溃输出 traceback，而不是返回 0 命中或错误提示。已复现：service.find_cited_by(db, "民法典", "12345") 抛 IndexError: list index out of range。

**证据 / 复现**

```text
_CHINESE_UNITS = ["", "十", "百", "千"]
...
unit = _CHINESE_UNITS[length - index - 1]  # length=5 时越界
复现输出：B) CRASH: IndexError list index out of range（经 find_cited_by → _build_cited_by_pattern → _arabic_to_chinese_numeral(12345)）
```

**修复建议**

_arabic_to_chinese_numeral 对 value>9999 返回 ""（_build_cited_by_pattern 已兼容 chinese 为空、只用阿拉伯写法），或在 find_cited_by 中对 number_int 做范围校验（如 >9999 时返回 None 或 hit_count=0）。

**对抗复核结论**：确认为真（复核后 severity=low）

> 复核确认为真，完整链路已复现（临时库，未触碰仓库/用户数据）：(1) 单元层面：_arabic_to_chinese_numeral(9999)='九千九百九十九' 正常，10000/12345 均抛 IndexError: list index out of range，边界正是 _CHINESE_UNITS=["","十","百","千"] 只有 4 个单位（src/chinalaw/service.py:2197、2220）。(2) 服务层面：向临时库插入「民法典」后，service.find_cited_by(tmp, "民法典", "522") 正常返回 hit_count=1，改为 "12345" 即 CRASH IndexError；"9999" OK（hit_count=0），"10000" CRASH，与 finding 描述的边界完全一致。(3) CLI 端到端：`chinalaw --db <tmp> cited-by 民法典:12345` 退出码 1，stderr 末行为 "IndexError: list index out of range"（原始 traceback，非结构化错误）。防护核查均为否：normalize_article_number（service.py:55）对 "12345" 原样返回，无上限；find_cited_by（service.py:2308-2315）只校验 int 可解析和无 "-"；parse_cited_by_spec（service.py:2429）不校验数字范围；cli.py app()（2349-2355）仅捕获 BrokenPipeError，_handle_cited_by（cli.py:1470）无 try/except。_arabic_to_chinese_numeral 在生产代码中仅此一处调用（service.py:2265）。严重度下调为 low 的理由：触发需条号≥10000，而现行中国法律最大条数约 1260（民法典），任何真实引用查询都不可能触发，仅限用户笔误/agent 生成的畸形条号；且需目标法规名本身能在库中解析成功（否则提前返回 None 走 TargetNotFound 分支，不崩溃）；影响限于单次命令以 traceback 失败，无数据损坏、无错误结果，属输入健壮性缺陷而非功能缺陷。修复建议（value>9999 返回 "" 或 find_cited_by 范围校验）与 _build_cited_by_pattern 现有的空 chinese 兼容逻辑（service.py:2267）吻合，可行。


### [L3] LawStatus 枚举缺失 pending_effective 与 seed，数据契约再次破裂

- **位置**：`src/chinalaw/models.py:43`
- **分类 / 维度**：数据 / 输出格式化/数据模型

**缺陷描述**

docs/CONTRACT.md §2.9 声明 LawStatus 受控值为 current/amended/pending_effective/repealed/unknown/seed，且运行时确实产生这两个缺失值：cleaning.SXX_TO_STATUS 把 flk sxx=4 映射为 "pending_effective" 写入 laws.status，cli/corpus/metadata 的 --status choices 均含 pending_effective；service.py 多处按 `law.get("status") == "seed"` 分支处理 seed 状态。但 LawStatus 枚举只声明了 CURRENT/AMENDED/REPEALED/UNKNOWN。这与项目此前自查并修复的 LawLevel 缺 supervisory_regulation 属同一类『enum 漏声明导致数据契约破裂』问题（models.py 文档字符串明确以此为教训），且 tests/test_core.py 已为 FLXZ_TO_LEVEL⊆LawLevel 建了守护测试，SXX_TO_STATUS⊆LawStatus 却没有对应守护，一旦按计划切换到 pydantic 校验或任何代码用 LawStatus(value) 反解 DB 值，pending_effective/seed 记录会直接抛 ValueError。

**证据 / 复现**

```text
models.py:43-49:
class LawStatus(str, Enum):
    CURRENT = "current"
    AMENDED = "amended"
    REPEALED = "repealed"
    UNKNOWN = "unknown"

cleaning.py:54-58:
SXX_TO_STATUS = {1: "repealed", 2: "amended", 3: "current", 4: "pending_effective"}

docs/CONTRACT.md §2.9 status 表含 pending_effective 与 seed；service.py:788 `law.get("status") == "seed"`。
```

**修复建议**

在 LawStatus 中补声明 PENDING_EFFECTIVE = "pending_effective" 与 SEED = "seed"（或按契约把 seed 归入覆盖度而非状态并同步修订 CONTRACT.md），并仿照 test_core.py 的 FLXZ_TO_LEVEL 守护测试，新增断言 SXX_TO_STATUS 的全部 value ∈ LawStatus。

**对抗复核结论**：确认为真（复核后 severity=low）

> finding 全部事实核实为真：(1) models.py:43-49 LawStatus 仅声明 current/amended/repealed/unknown，而 docs/CONTRACT.md §2.9 受控值表含 pending_effective 与 seed，且 §2 DDL 注释明确以「见 §2.9 LawStatus」把该枚举定为契约规范声明；(2) 两个缺失值确由运行时产生并写入 laws.status——cleaning.py:53-58/587 把 sxx=4 映射为 "pending_effective"，loader.py:11 接受 status="seed"，service.py:139/788/1645 等多处按 status=="seed" 分支，cli/metadata/corpus 的 --status choices 均含 pending_effective；(3) test_core.py:1989-1995 有 FLXZ_TO_LEVEL⊆LawLevel 守护测试而 SXX_TO_STATUS⊆LawStatus 无对应守护（全仓 grep 无命中）；(4) 复现成功：.venv 下 LawStatus('pending_effective') 与 LawStatus('seed') 均抛 ValueError。但严重度应下调：grep 确认 src/ 下除 models.py 自身外无任何模块导入 LawStatus 或实例化 Law dataclass，models.py 是显式「占位」模块，运行时全部按裸字符串处理 status，当前不存在可触发 ValueError 的执行路径；破裂为潜伏性，仅在文档字符串自述的 pydantic 迁移计划落地或未来代码用 LawStatus(value) 反解 DB 值时爆发。属真实的契约声明缺陷+守护测试缺口（与项目已修复并写入教训的 LawLevel 缺 supervisory_regulation 完全同类），但无当前用户可见影响，故 medium 降为 low。


### [L4] _LEVEL_LABELS 键名与 DB 实际 level 值不一致，行政法规/部门规章的中文标签永不生效

- **位置**：`src/chinalaw/formatters.py:1903`
- **分类 / 维度**：正确性 / 输出格式化/数据模型

**缺陷描述**

resolve_to_markdown 用 _LEVEL_LABELS 把 level 翻译成中文，但字典键 `administrative_regulation`、`departmental_rule`、`constitution` 与实际存储值不符：LawLevel 枚举与 cleaning.FLXZ_TO_LEVEL 写入 DB 的是 `admin_regulation`、`department_rule`，且不存在 constitution 值（宪法归入 law）。三个键是永不命中的死代码；行政法规、部门规章这两类常见法规在 `chinalaw resolve --format md` 里始终回退显示英文枚举原值。另外 `local_government_rule`、`supervisory_regulation`、`guiding_case`、`judicial_policy`、`self_regulatory_rule`、`other` 等已声明层级也全部缺标签。

**证据 / 复现**

```text
formatters.py:1903-1911:
_LEVEL_LABELS = {
    "law": "法律",
    ...
    "administrative_regulation": "行政法规",
    "departmental_rule": "部门规章",
    ...
}

实测（本地 DB level='admin_regulation'）：`.venv/bin/chinalaw resolve 诉讼费用交纳办法 --format md` 输出 `- 效力层级：admin_regulation（国务院发布）`，未显示『行政法规』。
```

**修复建议**

把键改为与 LawLevel 枚举值一致：admin_regulation→行政法规、department_rule→部门规章，删除 constitution 死键，并补齐 local_government_rule/supervisory_regulation/self_regulatory_rule/judicial_policy/guiding_case/other 的中文标签；可加一条测试断言 _LEVEL_LABELS 的键 ⊆ {e.value for e in LawLevel}。

**对抗复核结论**：确认为真（复核后 severity=low）

> 事实全部核实且已实机复现。(1) 键名不一致确凿：/Users/huoxihuo/chinalaw-cli/src/chinalaw/models.py:29-40 的 LawLevel 枚举值为 admin_regulation、department_rule，且无 constitution（cleaning.py:32 把「宪法」映射为 "law"）；而 formatters.py:1903-1911 的 _LEVEL_LABELS 用的是 administrative_regulation、departmental_rule、constitution，程序化对比确认这 3 个键不属于任何枚举值（死键），另有 8 个枚举值（admin_regulation、department_rule、local_government_rule、supervisory_regulation、self_regulatory_rule、judicial_policy、guiding_case、other）无标签。(2) 无上游防护：service.py:1454 resolve 直接返回 row["level"]（DB 原值），_LEVEL_LABELS 全仓库仅 formatters.py:1939 一处使用，.get(level, level) 直接回退原值。(3) 实机复现：本地 DB level 分布为 law=28/judicial_interpretation=20/admin_regulation=1；运行 `.venv/bin/chinalaw resolve 诉讼费用交纳办法 --format md` 实际输出「- 效力层级：admin_regulation（国务院发布）」，与 finding 所述完全一致；最小用例确认 department_rule、local_government_rule 同样不翻译。但严重度应下调为 low：.get(level, level) 回退保证不崩溃且显示的英文枚举值信息本身准确（只是未汉化，不产生错误信息）；影响面仅 resolve 子命令的 md 输出一行，JSON 输出不受影响；且项目其他 formatter（formatters.py:361、865、901）本来就直接显示英文原值未做翻译，属于局部汉化增强失效而非功能性错误。修复建议（改键名、补齐标签、加 _LEVEL_LABELS 键 ⊆ LawLevel 值的断言测试）合理可采纳。


### [L5] 33 个 skip 全部指向仓库中不存在的可选脚本/数据，608 行 eval 分析器测试与 MCP stdio 端到端测试永久失效

- **位置**：`tests/test_eval_analyze.py:25`
- **分类 / 维度**：测试 / 测试体系

**缺陷描述**

本仓库 33 个 skipped 测试无一是平台性/环境性跳过，全部因引用的文件不在仓库中：tests/test_eval_analyze.py 整文件 29 个用例依赖 scripts/eval/analyze.py（目录 scripts/eval 不存在），608 行测试代码在本仓库永远不执行；test_mcp.py:111 的 mcp-footprint 测试是唯一驱动真实 stdio server 子进程的端到端测试，同样永久 skip（mcp.py 的 serve 主循环因此零覆盖）；test_agent_assets.py:197 的 export-public 两个安全测试（防止误删 .git / 误删源码树）也永久 skip。CI（.github/workflows/test.yml）跑 pytest 时 648 passed 的绿灯掩盖了"这些契约在本仓库没有任何活测试"的事实，且这些死测试会随源码演进悄悄腐烂（一旦脚本恢复，测试可能已与实现不符）。

**证据 / 复现**

```text
pytest -rs 输出：29 条 `SKIPPED tests/test_eval_analyze.py:* optional eval analyzer script is not included`、`SKIPPED tests/test_mcp.py:111 optional MCP footprint script is not included`、`SKIPPED tests/test_agent_assets.py:206/219 optional export-public script is not included`。`ls scripts/eval` → No such file or directory；scripts/ 目录下无 export-public；grep 全仓库（CI/pyproject/docs）均无 scripts/eval 引用。
```

**修复建议**

二选一：把 scripts/eval/analyze.py、scripts/eval/mcp-footprint.py、scripts/export-public 收进仓库使测试生效；或把对应测试文件随脚本一起移出本仓库。至少应为 mcp.py 的 stdio serve 循环补一个不依赖外部脚本的子进程端到端测试，并在 CI 对 skip 数量设上限（如 pytest --strict-markers + 断言 skip 列表），防止死测试无声堆积。

**对抗复核结论**：确认为真（复核后 severity=low）

> 事实全部复现，但存在明显缓解因素，严重度应下调。复现结果：`pytest -rs` 输出 648 passed, 33 skipped，与 finding 完全一致——test_eval_analyze.py 29 条（skip 原因 "optional eval analyzer script is not included"）、test_mcp.py:111 1 条、test_agent_assets.py:206/219 2 条、test_core.py:7174 1 条（demo norm pack 数据），确无平台性跳过；`ls scripts/eval` → No such file or directory，scripts/ 下也无 export-public；test_eval_analyze.py 全文 608 行由文件级 `@unittest.skipUnless(ANALYZE_PATH.exists(), ...)`（第 25/332 行）整体守卫，在本仓库永不执行。serve 循环覆盖claim 也成立：grep 全 tests/ 无任何测试调用 serve_stdio/main 或 spawn `python -m chinalaw.mcp` 子进程，而 chinalaw-mcp 是 pyproject.toml:44 声明的对外 entry point。但三点缓解：(1) 这是刻意的"可选资产"设计而非意外破损——skip 消息统一写明 "optional ... is not included"，且 CHANGELOG.md 第 144/658-677 行证明 scripts/eval/analyze.py 等曾存在于开发树并持续演进（2026-05-07 还有针对它的修复批次），本仓库是有意剥离脚本、保留测试的公开发行版（finding 声称"grep 全仓库均无 scripts/eval 引用"不准确，CHANGELOG 有引用）；(2) serve_stdio（mcp.py:213-221）仅 8 行，是 _read_message_with_framing/handle_request/_write_message 的纯组合，这三者在 test_mcp.py:43-64 及多个 handle_request 用例中均有直接单测，"零覆盖"字面成立但实际未测面很小（循环+argparse）；(3) 死测试若与恢复的脚本不符会在恢复时立即红灯，属"响亮失败"而非无声腐烂。结论：finding 陈述基本准确、CI skip 无上限的流程建议有价值，但无用户可见缺陷、设计系有意为之，medium 高估，降为 low。


### [L6] trace 与 service 循环导入：先 import chinalaw.trace 直接 ImportError 崩溃

- **位置**：`src/chinalaw/trace.py:21`
- **分类 / 维度**：正确性 / 规范层（时间效力）

**缺陷描述**

trace.py 顶部 from chinalaw.service import (...)，而 service.py 末尾（L2831）又 from chinalaw.trace import trace_article_as_of 做向后兼容 re-export。导入顺序敏感：先 import chinalaw.service 再 import chinalaw.trace 正常，但任何先导入 trace 的路径（下游脚本 from chinalaw.trace import trace_article_as_of、测试文件单独 import trace、IDE/REPL 直接使用）都会因 trace 部分初始化时 service 反向取 trace_article_as_of 失败而 ImportError。这是隐藏的 API 误用陷阱：模块自身无法独立导入。

**证据 / 复现**

```text
$ .venv/bin/python -c "import chinalaw.trace"
ImportError: cannot import name 'trace_article_as_of' from partially initialized module 'chinalaw.trace' (most likely due to a circular import)
$ .venv/bin/python -c "import chinalaw.service, chinalaw.trace; print('service-first OK')"
service-first OK
```

**修复建议**

去掉循环边：把 trace 依赖的 service 私有 helper（_row_to_law/_fetch_revisions/_parse_iso_date 等）下沉到独立内部模块（如 _lawcore.py），trace 与 service 都从它导入；或让 service.trace_article_as_of 改为惰性代理（函数内 import chinalaw.trace 再转调），删除模块级 re-export。

**对抗复核结论**：确认为真（复核后 severity=low）

> 复现成功：`.venv/bin/python -c "import chinalaw.trace"` 与 `from chinalaw.trace import trace_article_as_of` 均触发 ImportError: cannot import name 'trace_article_as_of' from partially initialized module 'chinalaw.trace'，先 import chinalaw.service 则正常。chinalaw/__init__.py 不预载 service，无防护；service.py L2827-2831 注释自认循环存在但只解决了 service-first 方向。审查员对机制的判断正确。但严重度应下调：全仓库 grep 确认除 service.py 的 re-export 外无任何代码（cli.py 走 service.trace_article_as_of，测试 patch chinalaw.service.trace_article_as_of）直接导入 chinalaw.trace，trace.py docstring 也把 service 路径标为对外接口，docs/README 未宣传库级导入，CLI 运行时与 648 条测试基线均不受影响——当前零实际触发路径，仅是对下游直接导入/未来重构的潜在陷阱，故 medium→low。附带事实核误：两个文件引用的 docs/decisions/ADR-0009-module-boundaries.md 在仓库中不存在。


## 四、完整性批判盲区与第二轮直接复验

完整性批判员在 43 条确认清单之外指出 6 条盲区/系统性根因。第二轮中审查主会话对其中关键点做了直接复现验证（不依赖子 agent），结果如下。

### 4.1 直接复现确认的新增问题

**[S1] MCP stdio server 对畸形输入与非 ValueError 异常零防护，整个 server 会话直接崩溃（建议 high）**

- 位置：`src/chinalaw/mcp.py:170-221`（`_read_message_with_framing` / `serve_stdio`）、`src/chinalaw/mcp.py:71-89`（`_call_tool`）
- 复现：`printf 'this-is-not-json\n' | mcp.serve_stdio(...)` → `JSONDecodeError: Expecting value: line 1 column 1 (char 0)`，进程直接 traceback 退出；JSON-RPC 规范要求返回 `-32700` 错误响应。
- `_call_tool` 仅捕获 `ValueError`；数据层已实测必现的 `sqlite3.OperationalError(database is locked)`、service 层已确认的 `IndexError` 等任何非 ValueError 异常都会杀死整个 stdio server 会话。CLI 层精心建立的 URLError/OSError 兜底（cli.py:1652）在 MCP 层没有对应物，而 MCP 恰是产品定位中 agent 接入的核心入口。叠加 tests 维度已确认的「MCP e2e 测试永久 skip」（tests/test_eval_analyze.py:25），形成「核心入口无防护 + 无测试」的双重盲区。

**[S2] `departmental_rule` 非法枚举值污染源至少 3 个 adapter，且 ingest 全链零枚举校验（扩展 [H6] 的根因）**

- 复现（grep 全仓）：`src/chinalaw/adapters/gov_xzfgk.py:566`、`src/chinalaw/adapters/nfra_gov_cn.py:318`、`src/chinalaw/adapters/csrc_gov_cn.py:739` 三处直接写入 `level="departmental_rule"`；而 `models.py:35` 合法值是 `department_rule`（LawLevel.DEPARTMENT_RULE）。`formatters.py:1908` 还为错值 `departmental_rule` 配了中文标签「部门规章」，等于在展示层把错误固化。
- 系统性根因：`loader.py` / `db.py` / `fetch.py` 整条 ingest 链对 `LawLevel` / `LawStatus` 没有任何枚举校验（grep 证实这些文件零引用枚举），任何 adapter 都能发明新值入库。tests 维度确认的 [H4] 只是 nfra 一处被测试反向固化，真实污染面更大。
- 同类症状还包括：LawStatus 缺 `pending_effective`/`seed`（[L3]）、`_LEVEL_LABELS` 死键（[L4]），应合并为同一个「枚举契约 + ingest 写入校验」工作项。

**[S3] `find_cited_by` 大条号 IndexError 崩溃路径第二轮直接复现（对应 [L2]，建议升级）**

- 复现：`service._arabic_to_chinese_numeral(10000)` → `IndexError: list index out of range`（单位表越界）。该崩溃路径会经 MCP 层放大——见 [S1]，非 ValueError 异常直接杀死 server。

### 4.2 当时的正面结论（后续封版已纠正）

- **网络层 TLS（已撤回）**：后续全量复核确认
  `src/chinalaw/adapters/court_gongbao.py` 默认使用
  `http://gongbao.court.gov.cn` 明文 HTTP；同时证券规则 adapter 的 direct-id 路径
  接受任意 URL，已实测可访问 `http://127.0.0.1` 和读取 `file:///etc/hosts`。
  因此“全部网络默认 TLS”结论不成立，详见 2026-08-06 封版补充 N1/N2。
- **alias_agent 端点来源**：LLM 端点由运维侧环境变量 `CHINALAW_ALIAS_AGENT_BASE_URL` / `CHINALAW_ALIAS_AGENT_API_KEY` / `CHINALAW_ALIAS_AGENT_MODEL` 提供，不来自用户输入，无 SSRF/提示注入直达网络的通路；异常族（HTTPError/URLError/OSError/TimeoutError）处理完备。

### 4.3 完整性批判员指出的其余盲区（尚未全量验证）


- **fetch/adapters 网络抓取层（约 8000 行、近半代码量）整块无人审查**
  - 线索：`/Users/huoxihuo/chinalaw-cli/src/chinalaw/fetch.py; /Users/huoxihuo/chinalaw-cli/src/chinalaw/adapters/ (15 个文件约 6200 行); ensure.py; discover.py; normsources.py; alias_agent.py`
  - 16 维度中没有任何维度通读 fetch.py、discover.py、ensure.py、normsources.py 与 adapters/ 的源码（tests 维度只审了它们的测试），而这是全仓库最大的攻击面（HTML/JSON 解析、多源网络 IO、LLM 输出直接写 alias 表影响 resolve 路由）。快速抽查即发现真实缺陷：csrc_gov_cn.py:739 与 gov_xzfgk.py:566 和 nfra 一样把非法 level 值 departmental_rule 直接入库——tests 维度只从『测试固化』角度报了 nfra 一处，实际污染源是至少 3 个 adapter。

- **MCP server（mcp.py）零 finding，且异常处理击穿整个 server，与 CLI error envelope 契约不对称**
  - 线索：`/Users/huoxihuo/chinalaw-cli/src/chinalaw/mcp.py:72-89, 213-221`
  - mcp.py 是产品定位（agent 接入）的核心入口却无任何 finding，叠加 tests 维度已报『MCP e2e 测试永久 skip』形成双重盲区。实测：stdin 收到 malformed JSON 时 serve_stdio 直接 traceback 崩溃退出（JSON-RPC 规范应返回 -32700）；_call_tool 仅捕 ValueError，datalayer 已实测必现的 sqlite3.OperationalError(database is locked)、service 层已确认的 IndexError 等任何非 ValueError 异常都会杀死整个 stdio server 会话——CLI 层精心建立的 URLError/OSError 兜底（cli.py:1652）在 MCP 层完全没有对应物。

- **跨维度系统性根因未点名：枚举契约在写入路径零校验，三个症状被拆散在两个维度**
  - 线索：`/Users/huoxihuo/chinalaw-cli/src/chinalaw/models.py; loader.py; db.py; fetch.py（三者均无 LawLevel/LawStatus 引用）`
  - LawStatus 缺值（formatters/low）、departmental_rule（tests/high）、_LEVEL_LABELS 死键（formatters/low）是同一根因的三个症状：models.py 枚举纯装饰，loader/db/fetch 整条 ingest 链对 level/status 无任何枚举校验（grep 证实零引用），任何 adapter 都能发明新值入库且已在 3 处复制同一错值。单维度各修各的症状，没有一条 finding 指出应在写入路径加统一校验 choke point，同类破裂必然继续发生（formatters 摘要自己也承认这是 LawLevel 上修过的同类问题复发）。

- **cleaning.py 条文切分管线（873 行）落在 dataquality 与 datalayer 的夹缝里，fixture 缺条的根因未查**
  - 线索：`/Users/huoxihuo/chinalaw-cli/src/chinalaw/cleaning.py:441-563 (parse_articles_from_text / normalize_articles)`
  - dataquality 把数据安全法缺第21/42条、网安法缺第25条定为 high 但只当作『数据文件』问题；生成这些 fixture 的切分管线 cleaning.py 无人审查——parse_articles_* 仅按行首正则切分，源文档一处并行（第21条并入第20条正是此模式）即静默丢条，normalize_articles 只校验缺 number/text，全管线无条号连续性检查（grep 证实无 continuity/gap 逻辑）。只修 fixture 不修管线，下一次 fetch 同一法规会再生同类缺条。

- **check-public-fixtures 质量门禁本身失效：对已确认缺条的 fixture 照样全绿**
  - 线索：`/Users/huoxihuo/chinalaw-cli/scripts/check-public-fixtures`
  - direction 维度发现『CI 从未运行该脚本』，但没人追问脚本本身查什么：本地实测 .venv/bin/python scripts/check-public-fixtures 对含缺条 fixture 的 74 个文件输出 passed、exit 0——即使把它接进 CI 也拦不住 dataquality 的两个 high 级数据缺陷。门禁缺条号连续性/条数断言，与第 4 条管线缺口共同构成『缺条数据无任何防线』的闭环，宜作为一条独立 finding 补入。

- **严重度互相矛盾：同类枚举契约破裂一个定 low 一个定 high，且 low 那条实际影响运行时过滤**
  - 线索：`/Users/huoxihuo/chinalaw-cli/src/chinalaw/models.py:43 vs /Users/huoxihuo/chinalaw-cli/tests/test_nfra_gov_cn.py:73; 佐证 service.py:2701 (WHERE status <> 'seed')`
  - LawStatus 缺 pending_effective/seed 定 [formatters/low]，而完全同类的 LawLevel departmental_rule 契约破裂定 [tests/high]，定级标准不一致；且 service.py:2701/2712 的 SQL 直接按 status='seed' 做运行时过滤，说明 'seed' 不是展示层小问题而是与 level 过滤同级的行为分叉，低估了。两条 finding 宜对齐严重度并合并为同一个『枚举契约+ingest 校验』工作项，避免分头修出不一致。


## 五、对抗复核驳回记录（1 条）

对抗复核机制共复核 44 条候选，驳回 1 条。驳回理由全文如下，供后续参考（该代码路径的怪癖真实存在，但所述触发条件不成立）：


- `src/chinalaw/trace.py:142` — **laws.source_hash 与所有 revision 哈希不一致时，trace 版本池静默丢弃现行条文**（维度 normlayer）
  - 驳回理由：代码层面的去重撞键机制确实存在且可复现（我用原始 SQL 直接篡改 DB 后，_trace_law_versions 只返回 1 个旧快照版本，现行文本被丢弃），但该 finding 所述的两个触发条件经核查全部不成立，正常路径下漂移状态不可达：(1) 「rebuild-clean 重算哈希」为误述——rebuild.py L31-34 的 docstring 明确 source_hash 代表上游内容标识、清洗变更不改哈希；_payload_for_rebuild 从 snapshot_json/laws 行携带原 source_hash，cleaning.canonicalize_external_json L137 用 setdefault 只在缺失时才计算。实测在含新旧两个 revision 的库上跑 rebuild_clean 后 source_hash 仍匹配 revision、版本池仍为 2。(2) 「fetch 更新 law+articles 但 revision 写入失败」不可能发生——全仓库唯一写 laws 表的代码是 loader.load_law_from_dict（grep 确认仅 loader.py L184 一处 INSERT INTO laws，fetch/sync/rebuild 均经由它），它在同一函数内先 upsert laws 再调 _upsert_revision（L225）写入 content_hash=source_hash 的 revision，且 db.py connect()（L36-47）在异常时整体 rollback、SQLite 事务日志覆盖崩溃场景，不存在半写状态。(3) 实测正常更新流（旧内容 load 后再 load 新内容，模拟 fetch 到新版）：laws.source_hash=9a2d071c…，revisions 含新旧两个哈希，版本池正确产出 2 个版本且含现行新文本。(4) 其余潜在漂移途径也排除：无任何 DELETE FROM revisions；无 DB restore 写入路径（snapshots.py 只是 JSONL 证据记录）；无代码传入 revision_id 覆盖哈希派生 ID；旧 schema 库 revisions 为空时去重键回退 law["source_hash"]，现行行仍会入池，不触发撞键。「schema 无约束保证一致」属实，但不变式由唯一写入口在同事务内构造性保证，触发该 bug 需要绕过全部支持路径手工篡改 SQLite。至多可作为对外部损坏数据库的防御性加固建议，不构成正确性缺陷。


## 六、Low 候选清单（26 条，未经对抗复核，按维度列出）

以下 finding 由维度审查员上报但未逐条对抗复核，真实性与严重度未最终确认，修复前需先验证。


- `[service]` `src/chinalaw/service.py:1378` — search(kind="norm") 与 in_laws/in_part 组合时静默返回全空且无任何 warning，kind 参数亦未校验
- `[service]` `src/chinalaw/service.py:1280` — in_laws 传空字符串时被解析为空过滤集合，所有检索结果静默归零
- `[service]` `src/chinalaw/service.py:625` — _resolve_law_row_by_derived_alias 每次解析未命中都全表扫描并在 Python 端逐行推导别名，大库下性能陷阱
- `[datalayer]` `src/chinalaw/sync.py:167` — _sync_incremental 的水位 meta 键硬编码 source:flk_npc: 前缀而非使用 source 参数，接入第二个数据源后各源增量水位互相错写
- `[datalayer]` `src/chinalaw/loader.py:246` — 按 FTS5 UNINDEXED 列 DELETE 触发全表扫描，每写入一部法规都要线性扫描整个 articles_fts / laws_fts，批量同步整体呈 O(N x M)
- `[datalayer]` `src/chinalaw/loader.py:137` — revision.released_at 在源数据无日期时回退为『同步当天日期』，伪造的发布日期会影响 rebuild 选取最新快照的排序
- `[datalayer]` `src/chinalaw/datapaths.py:18` — 内置数据目录查找只覆盖 sys.prefix / sys.base_prefix，pip install --user（user-site）安装时 shared-data 落在 userbase 下永远找不到，corpus/fixtures 功能直接报文件不存在
- `[cli]` `src/chinalaw/cli.py:90` — --limit 负值未做边界校验，直通 SQLite 后 LIMIT 负数等于无上限，一次性倾泻全库
- `[cli]` `src/chinalaw/cli.py:1755` — sources show 缺 source id 时走 parser.error：打印顶层 usage 到 stderr 并退 2，无 JSON envelope，与其它子命令的缺参处理不一致
- `[tests]` `tests/test_agent_platform.py:466` — 安装/更新脚本测试仅做子串匹配不执行脚本，脚本功能破碎时测试仍绿
- `[tests]` `tests/test_core.py:5337` — CliTests 类级共享 DB 被多个写型用例原位修改，测试间存在字母序隐式耦合
- `[formatters]` `src/chinalaw/formatters.py:27` — --arabic/--section 条号风格在 number 缺失时回退 number_display，产生『第第三条条』
- `[formatters]` `src/chinalaw/formatters.py:367` — law_to_markdown 遗漏 repealed_at：已废止法规的 md 输出不显示废止日期
- `[formatters]` `src/chinalaw/schema.py:105` — SCHEMA_V2~V6 用无校验的字符串 .replace 拼接 DDL，pattern 失配时静默 no-op
- `[direction]` `.claude/skills/contract-review/SKILL.md:25` — skill 套件内部自相矛盾并残留评测 harness 专用内容：contract-review 预检无条件 sync、PYTHONPATH 前缀违反总入口约定、legal-research 含 eval 专属 Headless 协议、退出码描述与 CONTRACT 冲突
- `[direction]` `.claude/skills/chinalaw-checking/SKILL.md:72` — checking skill 错误描述 article 命中 payload 的字段位置：status/source_url/source_checked_at 在 law 对象上，revision_id 不存在
- `[direction]` `docs/CONTRIBUTING.md:23` — CONTRIBUTING 声称开发需求 Python ≥ 3.11，与 pyproject requires-python >= 3.10 及 README『前置要求 Python 3.10+』矛盾
- `[direction]` `docs/COMPLIANCE.md:45` — COMPLIANCE.md 的 adapter 覆盖清单严重滞后：仅列 4 个源，实际已实装 12 个（证监会、交易所、金监局、行政法规库等未纳入 §1/§3/§5）
- `[dataquality]` `data/fixtures/criminal_law_2023.json:30` — 全部16个刑法版本 fixture 的第1-12条 part 为 null，总则各章缺『第一编 总则』标签
- `[dataquality]` `data/recommended_corpus.json:719` — p2-company-article-88-reply 的 notes 自相矛盾：声称『现有 fixture 已含全文（1 条）』但仓库无对应 fixture
- `[dataquality]` `data/fixtures/arbitration_law.json:2` — 仲裁法 fixture 承载 2025 修订文本（2026-03-01 施行），law id 却仍为 flk-arbitration-law-1994-2017
- `[normlayer]` `src/chinalaw/audit.py:24` — 短引用正则的否定回顾只排除 ASCII 词字符，中文前缀词会被误判为法典引用
- `[normlayer]` `src/chinalaw/audit.py:17` — 范围引用《X》第186-187条与插入条文『第X条之Y』归一化冲突，恒报 article_null 并给出误导性 fetch 建议
- `[normlayer]` `src/chinalaw/notices.py:170` — db_schema_stale 通知是死代码：service.status 已先行 migrate，schema_version 永远等于期望值
- `[normlayer]` `src/chinalaw/notices.py:91` — 安装通知假设包一定位于 repo checkout：pipx/wheel 安装下符号链接的 chinalaw 会触发虚假 wrapper 告警
- `[normlayer]` `src/chinalaw/normpacks.py:103` — 完全空的 reference 成员可被加入规范包且 validate 零告警


## 七、各维度审查小结（8 个已完成维度）

> 说明：以下小结由各维度审查员在对抗复核**之前**撰写，个别条目经复核后被驳回或调整严重度——以第一~三章的确认清单与第五节驳回记录为准（例如 normlayer 小结提到的「source_hash 漂移」一条即被驳回）。

### service（核心业务逻辑）

service.py 整体结构清晰（helper 分层、SQL 全参数化、连接经 contextmanager 正确关闭），但核心的条文定位与时间效力回放存在实质缺陷。最严重的是条款号归一化：对「第X条第Y款/项」这一最常见引用格式会静默算出错误条号并返回另一条法条的正文（第五百七十七条第一款→第571条），属于法律工具不可接受的错误答案。其次，as_of 版本回放在 revision 缺 snapshot_json 时把当前版本回放成空条文（旧库迁移场景必现），且 snapshot_json 损坏时未捕获 JSONDecodeError 直接崩溃；find_cited_by 对大条号有 IndexError 崩溃路径。返回值契约方面，get_articles 用单一 None 混淆 invalid_as_of/law_not_found/empty_numbers 三类失败，批量接口据此误报 law_not_found；search 的 kind/in_laws 边界（非法 kind、kind=norm+过滤、空字符串过滤器）均静默返回全空、无 warning。以上问题均已在隔离临时库中只读复现。

### datalayer（数据层）

数据层整体结构清晰：migration 采用注册表+幂等守护、模块级 assert 防漂移，rebuild/corpus/source_coverage 的校验和防御性编码质量较高，snapshot 台账与 SQLite 主库分离的设计也合理。但并发与事务边界是明显短板：批量/增量同步把全部网络抓取包进单个写事务，崩溃即全量回滚且断点 meta 一并丢失，同时长期持写锁使并发进程在默认 5 秒 timeout 后直接报 database is locked（均已实测复现）；增量水位在 max-pages/stable-pages 提前停止时被错误推进，会造成静默数据缺口；incremental 与 batch 共用断点键还会互相污染 resume 位置。次级问题包括证据台账 evidence_id 并发重复与追加非原子、FTS UNINDEXED 列删除的 O(N) 扫描、硬编码 flk_npc 水位键、伪造 released_at 影响快照排序，以及 user-site 安装下内置数据定位失效。建议优先修复同步的事务粒度与水位推进逻辑，再补 busy_timeout 与 migration 串行化。

### cli（CLI 层）

CLI 层（cli.py / __main__.py / doctor.py）整体质量较高：命令注册与 handler 分发结构清晰，退出码语义（0 命中 / 1 未命中 / 2 用法及域错误）在核心检索命令上执行一致，BrokenPipeError 的捕获与 devnull 重定向、Windows 管道下的 stdout/stderr UTF-8 reconfigure 都处理得当，fetch/discover 的错误 envelope 契约有文档支撑且实现严谨。主要缺陷集中在错误路径的盲区：文件输入类子命令（norm/pack/commentary import、audit、cite-check、--metadata-file）普遍缺少 FileNotFoundError 处理，传错路径即裸 traceback 退 1，破坏 agent 机器可读契约；sync 是 envelope 契约唯一未覆盖的联网命令（网络错误裸 traceback、--from-dir 坏目录静默成功）；get/diff 把非法 --as-of 误报为法规不存在，与 article 命令已有的 invalid_as_of 诊断相矛盾；doctor 名为健康检查却会静默迁移旧 schema 数据库，使 schema_version 检查形同虚设。上述问题均已在 .venv CLI 上用临时数据库实际复现。

### tests（测试体系）

测试体系整体质量较高：全部离线运行（648 passed，44s），adapter 层用手工 HTML/JSON fixture 端到端驱动，大量守门测试附带历史 bug 出处与 spec 引用，异常收窄（narrow except）与多源对称性有专门测试文件，subTest 使用规范（5 处均为标准 with 用法，不掩盖失败），核心模块（service/loader/db/fetch/sync/audit/normpacks/normsources/cli）覆盖充分。主要问题集中在四类：(1) 一个已入库的数据契约破裂被 adapter 测试反向固化——三个 adapter 输出的 level=departmental_rule 不在 LawLevel 枚举，level 过滤被拼写切裂，属正常使用即出错的数据正确性缺陷；(2) 33 个 skip 全部指向仓库中不存在的可选脚本/数据，608 行 eval 分析器测试与唯一的 MCP stdio 端到端测试永久失效，且 V02 冒烟测试的 skip 条件连带禁用了本仓库实际分发 fixture 的完整性守门（民法典 1260 条等）；(3) DB migrator 矩阵测试的装置使断言恒真，v7→v8/v8→v9 真实升级路径无有效覆盖（已用注入 no-op migrator 实测证实）；(4) alias_agent（唯一运行时调用外部 LLM 的模块）被所有测试 mock 掉、自身逻辑零覆盖，安装脚本测试仅子串匹配不执行。另有 CliTests 类级共享 DB 的测试间耦合等次要隐患；commentary 模块覆盖也偏薄（仅 2 个用例）。

### formatters（输出格式化/数据模型）

formatters.py/models.py/schema.py 整体工程质量尚可：markdown 各 formatter 对 None/缺键普遍有 .get 防御，card/inline 输出做了换行折叠，schema 迁移链与全新安装路径经实测列集合一致。但存在一处正常使用即触发的输出破损（law_to_markdown 对每条条文重复输出章节标题，民法典 md 输出含 1260 个重复 `###` 标题）；数据契约层面 LawStatus 枚举缺 pending_effective/seed，与 CONTRACT.md §2.9 和 cleaning/service 的实际运行值脱节，恰是项目此前在 LawLevel 上修过的同类问题且缺守护测试；resolve md 的效力层级中文标签因键名拼写与实际枚举值不符而对行政法规/部门规章完全失效（含 3 个死键）。此外批量取条/outline 的 full footer 溯源信息反而少于 compact、repealed_at 在法规 md 视图中缺失，均与『md/JSON 信息量等价』『来源可核验』的产品口径相悖；schema.py 的 .replace 拼接链无命中校验属 latent 风险。未发现崩溃级或安全类问题。

### direction（文档/方向）

本维度审查了 README、docs/ 全部 14 份文档、.claude/skills/ 全部 7 个 skill（含 references 与 doctor.sh）和 CHANGELOG，并用临时数据库对文档承诺的命令逐条实测核对。总体判断：项目定位（charter/differentiation/mcp positioning）与实际投入高度一致，没有方向漂移；CONTRACT 的绝大多数命令契约（退出码、search/status/sources/mcp 工具集、DDL、resolve 语义）与实现吻合，schema v9、74 fixture、节流 clamp 等硬承诺均可验证。主要问题集中在三类：一是合规与质量承诺失实——UA 溯源 URL 指向占位仓库且维护者联系渠道不存在、README 宣称的 CI fixture 门禁从未在 workflow 中执行；二是文档基础设施断链——协议变更所依赖的 docs/decisions/、MVP_PLAN、ADR-0004 等规范性文件已不在仓库，流程按文档不可执行；三是 skill 层与 CLI 实际行为漂移——虚构的节流环境变量、错误的 --in-laws/--part 示例、不存在的 status 字段与错误健康阈值，会让严格遵循 skill 的 agent 在关键恢复路径上失败或误诊断。未发现 high 级问题，8 medium、4 low。

### dataquality（出厂数据质量）

本维度审查了 data/applicability 全部6个 JSON、source_coverage.json、recommended_corpus.json，并对 fixtures 做了全量条号连续性扫描与指定文件深度抽查。总体质量较好：民法典（1260条）、刑法2023合并文本（含修正案十二实质内容）、宪法2018（143条+序言）、公司法2024（266条）、时间效力规定、合同编通则解释的条文编号、number/number_display 换算、版本日期均与史实一致；source_coverage 的命令能力矩阵与 sources.py/discover.py/sync.py 的实际实现完全吻合；applicability 种子的新旧法衔接日期（2021-01-01、2024-07-01）法律上正确且有充分免责表述。主要问题集中在数据完整性与 id 契约两处：数据安全法与网络安全法两个出厂 fixture 存在整条缺失甚至条文错拼（高危，直接破坏引用核对的核心承诺）；applicability 种子引用的5个旧法 stable id 与 fetch 的 canonical id 机制脱节，导致宣传的 needs_fetch 补全闭环永不收敛（已实测复现）。其余为 part 元数据缺编名/零宽字符污染、司法解释文号缺失使文号反查索引失效等中低危数据缺口。

### normlayer（规范层（时间效力））

规范层（normpacks/audit/trace/commentary/applicability/notices）分层清晰、保守设计意图明确（不做法律判断、低置信不静默断言），但两条核心时间效力路径存在实质正确性缺陷并已用真实/合成数据复现：一是 audit 在用户按工具自身提示指定 --as-of 后完全跳过废止核查，事实日期晚于废止日的引用零告警通过；二是 trace 对同条号实质修正条文（如刑法133条之一）因固定 0.72 阈值与仅 0.05 的同号加分输出 status="deleted" 的错误结论。中等问题包括 trace/service 循环导入使模块无法独立导入、pack 创建路径模糊解析导致成员落错包、applicability 导入层不校验日期格式使 applicable 按字典序静默漏配、source_hash 漂移时 trace 丢弃现行条文、pack import 的约束冲突裸抛 IntegrityError。commentary.py 与 notices.py 整体健康，仅有死代码检查与安装形态假设等低危问题。全部 12 条 finding 均基于完整通读与 .venv 只读复现验证，未发现纯风格问题混入。


## 八、修复优先级建议与后续工作

### 8.1 建议的修复顺序

1. **条款号归一化（[H1]）**：法律检索工具对「第X条第Y款/项」这一最常见引用格式静默返回**另一部条文**的正文，属不可接受的错误答案，应最优先修复并补回归测试。
2. **时间效力核心正确性（[H7][H8][M1][M2][S3]）**：audit 指定 `--as-of` 后完全跳过废止核查、trace 把现行条文误报 deleted、as_of 回放返回空条文——这是法律工具的核心承诺，破损即误导。
3. **数据层事务与并发（[H2][H3][M3][M5][M9]）**：单事务全量同步一崩全滚、增量水位错误推进造成静默数据缺口、断点键互相污染。
4. **枚举契约统一校验（[H4][S2][L3][L4]）**：在 ingest 写入路径加一个枚举校验 choke point，一次修掉 3 个 adapter 污染源与全部同类症状。
5. **MCP server 加固（[S1][L5]）**：畸形 JSON 返回 -32700、`_call_tool` 扩大异常兜底，并恢复 MCP e2e 测试。
6. **数据质量防线（[H5][H6][M24][M26][M27] + 4.3 盲区）**：补缺失条文 fixture；cleaning.py 切分管线加条号连续性检查；check-public-fixtures 门禁加条数/连续性断言并真正接入 CI。
7. **错误处理契约（[M4][M6][M7][M8][M10] 等）**：文件输入类子命令统一 FileNotFoundError → JSON error envelope。
8. **文档/skills 失真（[M15][M17]~[M23] 共 8 条 direction）**：docs 与 7 个 skill 中不存在的命令/字段/阈值逐项核对修正。

### 8.2 尚未完成的审查面（建议补齐）

- `fetch.py` / `discover.py` / `ensure.py` / `normsources.py` 与 `adapters/` 全部 15 个 adapter（约 8000 行）的全量通读审查。
- scripts/ 下 bash / PowerShell 脚本的安全专项（注入、引号/word-splitting、路径处理）。
- `cleaning.py`（873 行切分管线）与 textproc 维度全量审查——两个 high 级 fixture 缺条的根因就在这条管线上。
- packaging/CI 专项（版本一致性、数据文件随包分发、CI 矩阵）。

### 8.3 数据存档

本报告配套机器可读数据：`docs/FULL_AUDIT_20260726.findings.json`（43 条确认 finding 全字段 + 驳回记录 + 26 条 low 候选 + 维度小结 + 盲区清单），供修复阶段直接消费。
