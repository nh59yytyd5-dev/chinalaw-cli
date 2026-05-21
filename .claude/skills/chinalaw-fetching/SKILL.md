---
name: chinalaw-fetching
description: "chinalaw 多源爬取补全 skill。何时使用：本地缺法规 / 缺条文（article=null / law_missing / law_stub / law_seed / needs_fetch）/ 跨期事实需要旧法补全 / 用户给出仓库未收录的法规名 / 文号已知但 source 不明。"
---

# chinalaw-fetching

> "缺数据 → 哪个源 → 怎么 fetch → 失败如何降级"。多个 source 各有领地，
> 走错源 = 0 命中。

## 触发场景

- `chinalaw article 民法典 999 --format json` 返回 `article: null` /
  `needs_fetch`
- `chinalaw search ...` 找到的法规是 stub / seed（缺全文或只有样例条文）
- 用户给出仓库未收录的法规名（"袭警刑事案件解释"等长尾源）
- 跨期事实需要旧法补全（民法典施行前的合同法 / 担保法 / 物权法）
- 文号已知但 source 不明（"法释〔2023〕13 号"）
- 上游 verify-source 后发现 stale，需要 force re-fetch

## 6 种缺失模式

| 模式 | 现象 | 触发条件 |
|------|-----|---------|
| `article: null` | 法规存在，条文缺失 | DB 里 law row 在但对应 article 0 行 |
| `law_missing` | 法规完全不存在 | DB 里查不到 law（含 alias 解析） |
| `law_stub` | 法规只有 title，无 articles | DB 里 law row 在但 stub 标记 |
| `law_seed` | 只有少量样例条文 | DB 里 law row 为 seed，不保证全文完整 |
| `needs_fetch` | applicable 返回缺数据 | applicability 规则引用的 law 在 DB 缺 |
| `pending_reference_in_pack` | 规范包 reference 未解析 | pack 里引用的 law 在 DB 缺 |
| `unfindable` | 多源都失败 | fetch 多源后均无命中 |

前 5 种走 fetch；第 6 种是 fetch 之后的最终降级。

## 源选择决策树

| 文件类型 | source | CLI |
|---------|--------|-----|
| 全国人大 / 国务院立法（法律 / 行政法规 / 部分司法解释） | `flk_npc` | `chinalaw fetch <name> --source flk_npc` |
| 一般部门规章（非证监会） | `flk_npc` | flk 含部分部门规章；0 命中再人工判断 |
| CSRC 证监会令 / 部门规章 | `csrc_gov_cn` | `chinalaw fetch <name> --source csrc_gov_cn` |
| 北交所自律规则 | `bse_cn` | `chinalaw fetch <name> --source bse_cn` |
| 上交所自律规则 | `sse_com_cn` | `chinalaw fetch <name> --source sse_com_cn` |
| 深交所自律规则 | `szse_cn` | `chinalaw fetch <name> --source szse_cn` |
| 中证登业务规则 | `chinaclear_cn` | `chinalaw fetch <name> --source chinaclear_cn` |
| 证券业协会自律规则 | `sac_net_cn` | `chinalaw fetch <name> --source sac_net_cn` |
| 地方人大 / 地方政府规章 | `flk_npc` | 同上 |
| 会议纪要（九民纪要 / 破产纪要） | `court_gongbao` | `chinalaw fetch <name> --source court_gongbao` |
| 最高法批复 / 复函 / 通知 | `court_gongbao` | 同上 |
| 公报案例 | `court_gongbao` | 同上 |
| 最高法主站发布但公报站未覆盖的司法政策 / 通知 / 新闻发布材料全文 | `court_main` | `chinalaw fetch <name> --source court_main` 或 `--prefer-id channel/xiangqing/id` |
| 单独的最高法司法解释 | `flk_npc` 或 `court_gongbao` | 优先 flk_npc，0 命中再 court_gongbao |
| 最高检指导性案例 | `spp_gov_cn` | `chinalaw fetch <name> --source spp_gov_cn` |
| 两高联合刑事司法解释（袭警 / 危害税收 / 洗钱 / 知产 等） | `spp_gov_cn` | 同上 |
| 工作报告 / 重要讲话 | `court_gongbao` | 仅作参考，agent 不应作为依据 |
| CAC / gov.cn 反爬或 SPA 阻塞内容 | 暂未实装 | 标记 `unfindable`，让用户人工提供 |

```bash
# 默认 flk_npc
chinalaw fetch 民法典 --article 第585条 --format json

# 显式 court_gongbao
chinalaw fetch 九民纪要 --source court_gongbao --format json

# 显式 court_main（最高法主站）
chinalaw fetch "最高人民法院关于审理道路交通事故损害赔偿案件适用法律若干问题的解释（二）" --source court_main --format json
chinalaw fetch "最高法主站文件" --source court_main --prefer-id zixun/xiangqing/499051 --format json

# 显式 spp_gov_cn
chinalaw fetch "袭警刑事案件解释" --source spp_gov_cn --format json

# 显式 csrc_gov_cn（证监会令 / 部门规章）
chinalaw fetch "上市公司信息披露管理办法" --source csrc_gov_cn --article 第一条 --format json
chinalaw fetch "证监会令第226号" --source csrc_gov_cn --format json

# 显式证券自律规则源
chinalaw fetch "北京证券交易所股票上市规则" --source bse_cn --article 1.1 --format json
chinalaw fetch "上海证券交易所股票上市规则" --source sse_com_cn --article 1.1 --format json
chinalaw fetch "深圳证券交易所股票上市规则" --source szse_cn --article 1.1 --format json

# 文号反查（任意源）
chinalaw fetch "法释〔2023〕13号" --source court_gongbao --format json
# court_gongbao adapter 会先查本地 document_number_index，再降级到远程搜索

# 跨期旧法 / 废止法：先按状态探测，再按 id 精取（仅 flk_npc 支持 --status）
chinalaw discover --source flk_npc --status repealed --query 合同法 --format json
chinalaw fetch 合同法 --source flk_npc --status repealed --list-matches --format json
chinalaw fetch 合同法 --source flk_npc --prefer-id <bbbs> --format json
```

详细 LawLevel → source 对照见
[`chinalaw-searching`](../chinalaw-searching/SKILL.md) §"方法 4"。

## fetch 关键 flag

| flag | 用途 |
|------|------|
| `--source {flk_npc,court_gongbao,court_main,spp_gov_cn,csrc_gov_cn,bse_cn,sse_com_cn,szse_cn,chinaclear_cn,sac_net_cn}` | 指定源 |
| `--article <num>` | 命中后随完整法规一起入库并定位返回 |
| `--list-matches` | 仅列出搜索命中候选（不下载、不入库），用于人工选 ID |
| `--prefer-id <id>` | 多条命中时手动指定候选 id（FLK 是 bbbs，HTML 源是 detail_id） |
| `--status <status>` | 远程候选状态过滤；`flk_npc` 支持四态，证券公开源仅接受 `current` |
| `--limit N` | 搜索候选上限（默认 5；含义合理时增至 20） |
| `--dry-run` | 不入库，仅输出清洗后 payload —— 用于 PR 审查 |
| `--to-fixture <path>` | 写 payload 到指定文件（不入库） |
| `--force` | 即使 source_hash 相同也重清洗 + upsert（清洗规则升级时用） |

## 跨期旧法补全

当事实发生在旧法时期，先用 `discover` 或 `fetch --list-matches --status`
缩小候选池。不要让模型凭记忆判断旧法版本。

```bash
# 例：民法典施行前合同纠纷，先找已废止合同法候选
chinalaw discover --source flk_npc --status repealed --query 合同法 --format json

# 候选多时用 returned id / bbbs 精取
chinalaw fetch 合同法 --source flk_npc --prefer-id <bbbs> --format json
```

`--status` 是远程搜索过滤，不是所有源通用过滤。`court_gongbao` /
`court_main` / `spp_gov_cn` 传 `--status` 会失败；证券公开源只接受
`current`。这是正确行为；不要改成静默忽略。

## fetch 之后必跑 outline（不要随机猜条号）

**fetch 一部新法规 / 司法解释之后，第一件事永远是 `outline`，不是 `article`**：

```bash
chinalaw fetch "最高人民法院关于审理劳动争议案件适用法律问题的解释（二）" --format json
chinalaw outline "最高人民法院关于审理劳动争议案件适用法律问题的解释（二）" --format md
# → 列出全部条号 + 标题 + 章节，把"一部解释里有哪些主题"灌进上下文
```

如果后续要批量引用原文，不要读默认 `outline` 的 `text_preview`。默认输出只是
目录预览；改用 `chinalaw outline <法规> --full-text --format json`，读取
`items[].text` / `articles[].text`。

为什么：fetch 拿回的 metadata 里 `article_count` 只是数字（如 21），不告诉你
哪条讲什么。如果你接着 `article 1` / `article 5` 挨个试，运气不好会跳过真正
答你问题的那一条 —— 实测 Q3"二倍工资仲裁时效"那道题，模型 fetch 完 21 条的
"解释（二）"后试了 1/5/8/10/20/21，**跳过了第 6 条**（"二倍工资按月计算"，
正是答案），白烧 6 个 turn 跑去 WebSearch。

outline 只要 1 个 turn，就能把整部法规的"骨架"拿到，再选 `--in-laws "<那部
法规>"` scope 一下 search，或者直接拿目标条号 `article` 取全文。

如果 outline 也不能直接告诉你哪条对应你的问题，就改用法规内的精检：

```bash
# 在某部法规内全文搜关键词（比 outline 更准）
chinalaw search "二倍工资 按月" --in-laws "最高人民法院关于审理劳动争议案件适用法律问题的解释（二）" --kind article --format json
# → article_hits 第一条直接命中第 6 条
```

**reasoning 上的关键纪律**：fetch 完一定要 outline 或 search-in-laws，不要
"先试 article 1 看看再说"。试错型策略在长法（民法典 1260 条 / 公司法 266 条）
和多解释（民事诉讼法解释 600+ 条）里直接 turn budget 爆炸。

## 多候选歧义处理

当 fetch 报 `FetchAmbiguousError`（多条候选）时：

```bash
# 1. 列候选
chinalaw fetch 公司法 --list-matches --limit 10 --format json
# → [
#     {id: "...", title: "中华人民共和国公司法（2018 修正）", ...},
#     {id: "...", title: "中华人民共和国公司法（2023 修订）", ...},
#     ...
#   ]

# 2. 选定 ID 后 fetch
chinalaw fetch 公司法 --prefer-id <id> --format json
```

不要让 fetch 自己猜 —— 公司法 / 民诉法 / 刑法这种多版本场景，`--prefer-id`
是 agent 唯一可靠的歧义解决路径。

## 同名 row / seed row 救援

`article` 命令本身没有 `--prefer-id`。如果 `article <短称> <条号>` 返回
`article: null` / `law_seed` / `law_stub`，并且 payload 里有 `sibling_laws`：

```bash
# 直接用 sibling_laws 里 article_count 更高、status 非 seed 的 id 取条
chinalaw article <sibling_law_id> <条号> --format json
```

不要继续对同一个短称反复 `article`；也不要给 `article` 发明 `--prefer-id`。
先用 `sibling_laws[].id` 精确指定，仍缺条再 `fetch <短称> --force`。

## 失败降级

### 第 0 步：先用 resolve 校验，再决定要不要 fetch

很多"找不到法规"的诉求其实只是俗称没解析对，本地库其实已经有。fetch 之前
先跑 resolve，避免无谓的网络抓取：

```bash
chinalaw resolve <俗称> --format json
# matched=true → 已在本地库，直接走 article / get / outline
# matched=false → 才进入下面的 fetch 流程
```

### 多源全失败 → unfindable

```bash
# 走完公开源后仍 0 命中
chinalaw fetch <name> --source flk_npc --format json   # 失败
chinalaw fetch <name> --source court_gongbao --format json   # 失败
chinalaw fetch <name> --source court_main --format json   # 失败
chinalaw fetch <name> --source spp_gov_cn --format json   # 失败
```

报告 `unfindable: true`，让用户人工提供 PDF / docx / 文档路径，再走
`norm ingest`：

```bash
chinalaw norm ingest path/to/doc.pdf \
  --name "<规范名称>" --source-type other \
  --format json
```

### 网络失败 → fetch_unavailable

报告 `fetch_unavailable: true` + 原 HTTP 错误（HTTPError / URLError）。
不要 retry 死循环；如确属临时网络问题，让用户复跑。

### 远程返回 stub（极少见）

某些 flk 详情页只返回 title 不返回正文 → adapter 标记 `stub_only` 入库；
agent 输出时显式提示 "已知存在但本期未补到正文"。

## 跨源协同：文号 one-shot

文号已知是最稳的反查路径（PR #29 实装）。`document_number_index` 表已在
`court_gongbao` / `court_main` / `spp_gov_cn` 入库时自动填写。`fetch <文号> --source X`
会优先查本地索引，命中 → 跳过远程搜索；未命中 → 降级到远程标题搜索。

```bash
chinalaw fetch "法释〔2023〕13号" --source court_gongbao --format json
chinalaw fetch "法释〔2026〕5号" --source court_main --format json
chinalaw fetch "高检发释字〔2025〕1号" --source spp_gov_cn --format json
```

agent 拿到文号优先用文号反查，比标题搜索快且唯一。

## 合规节流

`docs/COMPLIANCE.md` 规定：所有 adapter 节流硬下限 100ms（`MIN_REQUEST_INTERVAL =
0.1`）。调用方传 0 / 负值 / < 0.1s 都会被静默 clamp。

如果遇到 WAF / HTTP 307 / IP rate limit：

```bash
# 升级节流（环境变量；adapter 实测时再读取）
export CHINALAW_FETCH_THROTTLE_MS=2000
chinalaw fetch ...
```

不要尝试绕开节流（封 IP 是合规事故，不只是技术问题）。

## fetch → verify-source 验证回路

fetch 后建议 verify-source 抽查：

```bash
# fetch 一部新法规
chinalaw fetch "袭警刑事案件解释" --source spp_gov_cn --format json

# 抽查上游链路是否完整
chinalaw verify-source spp_gov_cn --query "袭警" --format json
chinalaw verify-source court_main --query "最高人民法院关于审理道路交通事故损害赔偿案件适用法律若干问题的解释（二）" --article 第一条 --format json
```

verify-source 跑 probe → search → fetch / clean → article locate 全链路；
任一步失败 → 上游可能改版，issue 报送。

## 输出契约

fetch 报告至少包含：

```text
请求: <name> [--source <s>] [--article <num>] [--prefer-id <id>]
来源: flk_npc / court_gongbao / court_main / spp_gov_cn / norm_ingest（fallback）
状态: ok / ambiguous / unfindable / fetch_unavailable / stub_only
命中: <law_id> / <article_id> | null
入库: yes / no（dry-run / to-fixture）
source_url: <url>
source_checked_at: <时间>
不确定性: <free text> | none
后续: agent 应跑 article <name> <num> 验证；建议 verify-source 抽查
```

## 反模式

- 盲目 fetch flk_npc 找九民纪要 / 破产纪要（在 flk 上 0 命中，应走
  court_gongbao）
- 把"袭警刑事案件解释"等两高联合解释发到 flk_npc（应走 spp_gov_cn）
- 多版本歧义不用 `--list-matches` + `--prefer-id`，让 fetch 自己猜
- 跳过 verify-source 直接信任 fetch 结果
- fetch 失败后吞掉错误，凭模型记忆"补"出条文文本输出
- 把 `pending_reference_in_pack` 当成"已核验条文"
- fetch 后不跑 article 验证，假设 fetch 报"ok"就等于条文可定位
- 频繁 fetch 同一法规 / 不带 --force 期待重新清洗（应该用 `rebuild-clean`）
- 搬运 PDF 直接 fetch（fetch 是远程源；本地文件应走 `norm ingest`）

## 与其他 skill 的衔接

- 缺数据来自检索失败 → [`chinalaw-searching`](../chinalaw-searching/SKILL.md)
- 缺数据导致核对失败 → [`chinalaw-checking`](../chinalaw-checking/SKILL.md)
- DB 整体异常 / 同步策略 → [`chinalaw-maintaining`](../chinalaw-maintaining/SKILL.md)

## 相关命令一览

| 命令 | 用途 |
|------|------|
| `chinalaw fetch <name>` | 高层按需获取入口 |
| `chinalaw fetch --list-matches` | 候选列表 |
| `chinalaw discover --status <status>` | 批量列出 FLK 候选，不下载不入库 |
| `chinalaw fetch --dry-run` / `--to-fixture` | 不入库预览 |
| `chinalaw ensure --from-dir <path>` | 本地优先批量补 |
| `chinalaw corpus list/show` | 查看推荐规范语料 profile |
| `chinalaw ensure --profile <name>` | 按 profile 本地优先批量补（alpha；大型 profile 可能触发官方源限流） |
| `chinalaw verify-source <source>` | 上游 smoke |
| `chinalaw probe <source>` | 探测页面结构（开发用） |
| `chinalaw norm ingest <file>` | 本地文件入库（fetch 失败兜底） |
| `chinalaw rebuild-clean --force` | 已入库 → 重清洗（不应替代 fetch） |
