---
name: chinalaw-searching
description: 中国法规检索方法 skill。何时使用：用户提法律问题、要起草法律意见、找规范依据；agent 不知道用户提到的法规正式 ID / 适用版本 / 关联解释 / 检索粒度。
---

# chinalaw-searching

> 检索的本质是 **"问题 → 候选 → 精确条文 → 关联解释"**，不是"google 一下"。
> 6 大方法对应 6 类常见错误。

## 一条铁律：search 命中即定位锚点

`chinalaw search <kw> --kind article` 返回的 `article_hits` 列表里**每一条
都包含 `(law_short_title, number, text)` 三件套**。如果 text 内容跟你要找的
主题对得上 —— **那条就是你要的答案**，直接拿 `(law_short_title, number)` 去
`article` 命令验证全文，**不要**再去翻法规的其他条号试错。

实测教训：Q3"二倍工资按月计算"那道题，agent 跑过 `search 二倍 --kind all`，
返回的 article_hits 第一条就是"劳动争议解释二 art 6"，原文正是"二倍工资按月
计算"。但 agent 把 search 命中当噪音忽略掉，转而 fetch 整部解释（二），然后
挨个试 article 1/5/8/10/20/21，跳过了第 6 条，又跑 WebSearch + 再回头试
20/21，最后还是 timeout。

**只要 search 出了 article 命中且 text 对得上主题，下一步永远是 `article
<那部法> <那个条号>` 验证全文，而不是 fetch + outline + 挨个 article 试错。**

## 触发场景

- "找一下劳动合同解除经济补偿的规定"
- "民法典里关于债权转让的条文有哪些"
- "九民纪要怎么处理对赌协议效力"
- "公司法新版关于横向人格否认有什么变化"
- 用户提"刑九"、"九民纪要"、"高法关于民间借贷的解释"等**俗称 / 简称 /
  社区写法**，agent 不知道对应正式 ID

## 检索 6 大方法

### 先锁命令语法

检索链路最常用命令只认这些 flag：

| 命令 | 合法过滤 |
|------|----------|
| `search <kw>` | `--kind` / `--in <law>` / `--in-part <part>` / `--limit` |
| `laws` | `--level` / `--status` / `--limit`；没有 `--query` |
| `discover` | `--query` / `--status` / `--limit`；没有位置参数 |
| `applicable` | `--date` / `--topic` / `--law` / `--domain` |

不要把 `applicable --law/--topic` 套到 `search`，不要把 `discover --query`
套到 `laws`。`--top`、`--law-filter`、`--headless` 都不是 chinalaw CLI flag。
多关键词 query 推荐作为一个 shell 参数传入：`chinalaw search "保证期间届满 签字" --kind article --format json`。
CLI 对未加引号的多个 query token 做空格合并容错，但加引号更清晰。

### 方法 1：法规名称归一化

用户给的"刑九"、"九民纪要"、"破产纪要"、"合通解释"等不是正式 ID，要先
归一化：

**首选 `chinalaw resolve`**（轻量，只校验法名 → 官方记录，不取条文）：

```bash
chinalaw resolve 合通解释 --format json
# {"matched":true,"via":"alias_exact",
#  "official_title":"...合同编通则若干问题的解释",
#  "short_title":"合同编通则解释", ...}
```

`via` 字段标示命中路径（`id_match` / `title_match` / `short_title_match`
/ `alias_exact` / `alias_derived` / `like_fallback`）。看到 `like_fallback`
要警觉是否选错。`matched=false` → 退回到下面的 search / fetch 候选。

**fallback：用 search 找法规候选**：

```bash
chinalaw search 九民纪要 --kind law --format json

# 或直接列法规清单（laws 没有 --query；需要关键词就用 search --kind law）
chinalaw laws --level law --limit 50 --format json
```

`laws` 是 agent 友好的法规清单接口，避免直连 SQLite，但只支持 `--level` /
`--status` / `--limit` 过滤；关键词找法规用 `search <kw> --kind law`。
`search --kind` 取值：
`law` / `article` / `norm` / `all`（默认 all）。

仓库已沉淀大量 alias（详见 `src/chinalaw/aliases.py`），实测可用：

| 用户写法 | 归一化命中 |
|---------|----------|
| 九民纪要 | "全国法院民商事审判工作会议纪要" |
| 破产纪要 | "全国法院破产审判工作会议纪要" |
| 合通解释 / 合同编通则解释 | "适用《中华人民共和国民法典》合同编通则若干问题的解释" |
| 诉讼时效解释 | "关于审理民事案件适用诉讼时效制度若干问题的规定" |
| 担保解释 | "关于适用《中华人民共和国民法典》有关担保制度的解释" |

如果 `search --kind law` 0 命中，进入方法 4 判断 LawLevel —— 可能不在 flk
而需要 `court_gongbao` / `court_main` / `spp_gov_cn`。

### 方法 2：版本时间锁定

很多重要法律有多版本：公司法 2005 / 2013 / 2018 / 2023；民事诉讼法 2017 /
2021 / 2023；治安管理处罚法 2005 / 2012 / 2025（待施行）等。

```bash
# 先按事实时间锁版本
chinalaw applicable --date 2024-08-15 --topic 公司治理 --format json
# → primary_law_id: flk-company-law-2024 (2023 修订，2024-07-01 施行)
# → not_legal_conclusion: true（仅检索线索）

# 再按 primary_law_id 取条，避免把自然语言版本标签误当成 alias
chinalaw article flk-company-law-2024 第32条 --format json
# 或在已确定现行版本时按 short_title
chinalaw article 公司法 第32条 --format json
# → 走 alias 解析层，命中现行有效版本
```

⚠️ `applicable` 返回 `primary_law_title` / `fallback_law_title` 必带
`not_legal_conclusion` 标记 —— **agent 不得改写为"应适用 X 法"**，只能写
"应优先检索 X 法"。

### 方法 3：跨法 transition

旧合同法 → 民法典合同编；旧担保法 → 民法典物权编 / 合同编；旧侵权责任法 →
民法典侵权责任编。**不要直接拼"民法典 113"** —— 必须查关系链：

```bash
chinalaw relation 合同法 --format json
# → relations: [{type: replaces, from: civil-code, to: 合同法, effective_at: 2021-01-01, ...}]

chinalaw relation 民法典 --format json
# → 看正向被哪些法替代
```

`relation_type` 取值：`replaces`（A 全面替代 B）/ `revises`（同一部法律的修
订关系，如公司法 2018 → 2023）/ `interprets`（解释关系）等。

旧条文映射到新条文是**人工知识**，CLI 只给关系链，不替代律师判断。例：

- 旧合同法 113 → 民法典 584（违约损失计算规则）
- 旧合同法 154 → 民法典 470（合同必备条款）
- 旧担保法 17 → 民法典 687（一般保证）

详细 walkthrough 见 [`references/walkthroughs.md`](references/walkthroughs.md)。

### 方法 4：LawLevel 11 档语义判别

不同 LawLevel 落在不同源，决定 `fetch --source` 走哪个。详细决策树见
[`chinalaw-fetching`](../chinalaw-fetching/SKILL.md)；这里只列判别要点：

**硬源（具有"法律拘束力"）**：

| level | 例 | 主要源 |
|-------|----|-------|
| `law` | 民法典、刑法、公司法 | flk_npc |
| `admin_regulation` | 行政法规（如反垄断法实施条例） | flk_npc |
| `judicial_interpretation` | 民法典合同编通则解释、刑九 | flk_npc / court_gongbao / court_main / spp_gov_cn |
| `department_rule` | 部门规章（CSRC / CAC） | flk_npc / csrc_gov_cn（证监会）/ 其它部门源暂未实装 |
| `self_regulatory_rule` | 证券交易所 / 中证登 / 证券业协会业务规则 | bse_cn / sse_com_cn / szse_cn / chinaclear_cn / sac_net_cn |
| `local_regulation` | 省人大法规 | flk_npc |
| `local_government_rule` | 省政府规章 | flk_npc |

**说理依据（agent 可引用为参考，但不是唯一适用根据）**：

| level | 例 | 主要源 |
|-------|----|-------|
| `judicial_meeting_minutes` | 九民纪要、破产纪要 | court_gongbao（不在 flk） |
| `judicial_policy` | 最高法批复、复函、通知 | court_gongbao / court_main |
| `guiding_case` | 最高法 / 最高检指导性案例 | court_gongbao / court_main / spp_gov_cn |
| `supervisory_regulation` | 监察委规则 | flk_npc / 暂未实装 |

> 经典错误：把"九民纪要"当 `judicial_interpretation` 去 flk_npc 搜 —— 0 命中，
> 因为它是 `judicial_meeting_minutes`，必须走 court_gongbao。

### 方法 5：粒度选择

| 命令 | 粒度 | 适用 |
|------|------|------|
| `article <law> <num>` | 单条精准 | 已知具体条号 |
| `articles <law> "5,12,23-25"` | 批量取条 | 同一法规多条 |
| `articles --batch '民法典:524-526;公司法:32'` | 跨法批量 | 合同审查 / 起诉状 |
| `outline <law> --part <章节>` | 结构 | 探索法规框架 |
| `outline <law> --with-text|--full-text --part <章节>` | 章节内全文 | 章节级深读 |
| `search <kw> --in <law>` | 法规内全文搜 | 已知法规缩小范围 |
| `search <kw> --in-part <章节>` | 章节内全文搜 | 长法（民法典 1260 条）的章节级精检 |
| `cited-by <law>:<num>` | 反向引用 | 看某条被哪些条引用 |

经典 fly weight：先
`outline 民法典 --part "第三编 合同 第一分编 通则 第六章 合同的变更和转让" --full-text`
一次拿“合同变更和转让”全章，再 `cited-by 民法典:546` 找关联展开。

**语法硬约束**：

- `article` 只接受一部法规 + 一个条号：`chinalaw article 民法典 147 --format json`。
- 同一部法的多条号必须用 `articles`：`chinalaw articles 民法典 "147,152" --format json`。
- 跨法规多条必须用 `articles --batch`：`chinalaw articles --batch '民法典:147,152;总则编解释:19' --format json`。
- search 多关键词推荐加引号：`chinalaw search "保证期间届满 签字" --kind article --format json`。

### 方法 6：关联解释链式检索

取主条文后，立即找配套司法解释 / 会议纪要 / 公报案例：

```bash
# 例：民法典 153 条（公序良俗 / 强制性规定）
chinalaw article 民法典 153 --format json
# → text: "...违反公序良俗的民事法律行为无效..."

# 找配套：民法典总则编司法解释
chinalaw search 公序良俗 --kind article --in-part "第一编 总则" --format json

# 找说理：九民纪要相关条款
chinalaw search 公序良俗 --in 九民纪要 --format json

# 反向：哪些法规引用了民法典 153
chinalaw cited-by 民法典:153 --format json
```

`search --in-part` 是"长法的章节级精检"功能（民法典 1260 条不可能全文 grep），
对 `第一编 总则` / `第二编 物权` / `第三编 合同` / `第四编 人格权` /
`第五编 婚姻家庭` / `第六编 继承` / `第七编 侵权责任` 等实际 part 前缀都生效。

## 4 个实战 walkthrough

详细见 [`references/walkthroughs.md`](references/walkthroughs.md)：

- **场景 A**：用户引"公司法 X 条"，版本不明（2018 vs 2023）
- **场景 B**：用户问"九民纪要 30 条对赌协议怎么处理"（准法源、跨源）
- **场景 C**：旧合同法 113 → 民法典 584（跨法 transition + 时间效力）
- **场景 D**：私域 + 国家法混合（合同附录引公司内规 + 民法典）

## 输出契约

检索结果输出至少包含：

```text
依据层级: 公开法（law / admin_regulation / judicial_interpretation / ...）
        / 私域规范（norm）
        / 规范包 reference（pack reference - 检索线索，非法条原文）
规范名称（含正式 short_title 和归一化 ID）:
条号（中式 + 阿拉伯）:
命令来源: search / article / fetch / norm clause / pack show
状态: current / amended / repealed / pending_effective / unknown
适用时间区间: <effective_from> ~ <effective_to> | null
关联解释: 司法解释 / 会议纪要 / 公报案例（链式检索结果）
source_url:
source_checked_at:
不确定性: <free text> | none
```

## 反模式

- 凭模型记忆答"民法典第 524 条规定..."而不查 CLI
- 把 `search` 命中片段（FTS5 摘要）当条文原文输出 —— 必须 `article` 精确
- 用现行法核对历史事实，不查 `applicable` / `relation`
- 跨法 transition 时直接拼"民法典对应条文"，不查 `relation`
- 把 `applicable` 写成"应适用 X 法"（payload 明确 `not_legal_conclusion`）
- 把 LawLevel 当 cosmetic：去 flk 搜会议纪要 / 去 court_gongbao 搜部门规章
  会全部 0 命中
- 长法（民法典 / 民诉法）不用 `--in-part`，直接全库 `search` 拿到 200+ 条
  噪声后乱选

## 与其他 skill 的衔接

- 检索后要核对引用 → [`chinalaw-checking`](../chinalaw-checking/SKILL.md)
- 找不到条文 / 法规是 stub → [`chinalaw-fetching`](../chinalaw-fetching/SKILL.md)
- DB 信息陈旧 / 同步问题 → [`chinalaw-maintaining`](../chinalaw-maintaining/SKILL.md)
- 完整法律备忘录流程 → [`legal-research`](../legal-research/SKILL.md)
- 合同审查上下文 → [`contract-review`](../contract-review/SKILL.md)
