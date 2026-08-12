---
name: chinalaw-checking
description: 中国法规引用核对 skill。何时使用：用户给一份含法条引用的内容（合同、备忘录、AI 草稿、起诉状），要求验证引用准确性 / 文本一致性 / 状态（有效 / 修订 / 废止）/ 时间效力。
---

# chinalaw-checking

> 法条引用 → 拆解 → 逐条 article → 文本比对 → 状态校验 → 时间效力校验 →
> 缺失补全 → 出报告。**核对的本质是把"AI 给的引用"变成"DB 可定位的事实"**。

## 触发场景

- "帮我核对这份合同里所有的法条引用"
- "AI 生成的备忘录里引了民法典 X 条，对吗"
- "这份起诉状引的合同法 113 条，现在还有效吗"
- "这是 ChatGPT 写的法律意见，引用看起来很多，但不知道靠不靠谱"
- 任何"已有引用、需要验证"的场景，都走本 skill。**和 `chinalaw-searching`
  正交：searching 是"问题→法条"，checking 是"法条→是否真"**。

## 6 步核对 procedure

### 0. 一键审查入口（优先）

如果用户给的是已有 Markdown / txt / docx / pdf、规范包或私域规范，先跑
`audit` 获得门禁报告，再对失败项继续人工拆解：

```bash
chinalaw audit file memo.md --format json
chinalaw audit file memo.md --as-of 2024-01-01 --format json
chinalaw audit pack 合同效力沉淀 --strict --format json
chinalaw audit norm 公司制度 --format json
chinalaw snapshot init
chinalaw audit grounding final.md --format json
```

`audit` 的 `ok/error_count/warning_count/citations[].issues` 是首要信号；
未通过时按 `suggested_command` 或 `diagnosis.suggested_fetch` 继续处理。
不要用 `search` 命中替代 `audit` / `article` 精确核验。

如果任务是审查 AI 最终报告是否“查过再写”，优先用 `audit grounding`。它审的是
最终文本里的引用/结论能否回连到本项目检索快照，不是重新研究一遍法律问题。
项目开始时先在项目根目录运行 `chinalaw snapshot init`，后续检索命令会自动追加证据。
`verified` 才代表有 article 级证据；`retrieved_only` 只是搜过候选；`ungrounded`
说明最终文本没有可追溯依据链。

### 1. 拆解

把材料里所有法条引用提取为标准化结构：

```text
[
  { law: "民法典", number: "第524条", quoted_text: null | "..." },
  { law: "公司法", number: "第32条", quoted_text: "..." },
  ...
]
```

要点：

- **法规名归一化**：用户写"民法典"不是"中华人民共和国民法典"——这步
  保留原文即可，归一化在 step 2 由 `chinalaw article` 的 alias 解析层完成
- **条号归一化**：`第五百二十四条` / `第524条` / `524` 都接受；插入条款
  `第14条之1` / `14-1` 也能识别（清洗层支持）
- **逐条独立**：哪怕同一法规连引 3 条也拆 3 项，便于后续逐条 diff

### 2. 逐条 article 跑

```bash
chinalaw article <name> <number> --format json
```

- 命中：顶层 `article` 对象含 `id` / `number` / `text`；顶层 `law` 对象含
  `status` / `source_url` / `source_checked_at` / `selected_revision` 等溯源字段。
  当前契约没有 `revision_id` 字段，不得凭空读取
- 未命中：`article: null` + 携带 `law_missing` / `law_stub` / `needs_fetch`
  之一。立即转 step 6 委托 [`chinalaw-fetching`](../chinalaw-fetching/SKILL.md)，
  不能凭 `search` 命中或模型记忆补
- 私域规范 fallback：`chinalaw article 九民纪要 30 --format json` 在公开法
  未命中时自动落到 `norm clause`，payload 带 `via: "norm_fallback"` 标记

批量场景用 `articles`：

```bash
chinalaw articles 民法典 "5,12,524,584" --format json
chinalaw articles --batch '民法典:524,584;公司法:32-35' --format json
```

不要把多个条号塞进 `article`：`chinalaw article 民法典 524 584` 是错误命令。

`articles --batch` 一次跨多部法规取条，顶层有 `ok` / `failed_section_count`
聚合字段，能直接喂给后续 diff 阶段。

### 3. 文本比对

只在文本明确逐字引用条文时，才做 quoted_text vs `article.text` diff。普通法律命题
或摘要（如“依据民法典143条，合同具备主体适格、意思表示真实且不违法时有效”）
不是原文引用，不应因措辞不同报 hallucination。

触发文本比对的信号：

- 引号：`《民法典》第143条规定：“具备下列条件……”`
- 明确原文提示：`原文为` / `条文为` / `摘录如下` / `规定如下`

对每条显式原文摘录：

| 差异类型 | 例 | 报告 |
|---------|----|----|
| 完全一致 | 字字相同 | `match: exact` |
| 标点 / 格式微差 | 全角 vs 半角分号、空格 | `match: cosmetic_drift` |
| 字词替换 | "应当" → "应该"、"或者" → "或" | `match: wording_drift` —— 视情况告警 |
| 整段缺失 / 拼接错误 | AI 把 524 + 525 拼一段 | `match: structural_mismatch` —— **必须告警** |
| 完全虚构 | DB 命中但文本完全对不上 | `match: hallucination` —— **核对失败** |

不要把"接近 90%"当通过；只要有 `wording_drift` 以上差异，必须明确报告。

### 4. 状态校验

`article` payload 中的 `status` 字段：

| 状态 | 含义 | 处理 |
|------|------|------|
| `current` | 现行有效 | OK |
| `amended` | 已被修订（仍有效，但法条文本可能不是用户想要的版本） | 告警 + 报告修订日期 |
| `repealed` | 已废止 | **不得作为引用依据**，转 `relation` 找 successor |
| `pending_effective` | 已通过未施行 | 告警 + 报告施行日期 |
| `unknown` | DB 未标注 | **告警 + 不确定**，让用户人工判断 |

不要默认 `current` —— 如果字段缺失或为 `unknown`，必须告警。

### 5. 时间效力校验

如果用户给出"事实发生时间"或"合同签订时间"，必须额外跑：

```bash
chinalaw applicable --date <YYYY-MM-DD> --topic <主题> --format json
chinalaw relation <法规名> --format json
```

- `applicable` 返回的是**检索线索**，包含 `primary_law_title` / `fallback_law_title`
  / `transition_text`，payload 必带 `not_legal_conclusion: true`，**不得改写为
  "应适用 X 法"**
- `relation` 返回 `replaces` / `revises` / `interprets` 等关系链；agent 据此
  判断"用户引的法条是不是事实时点的有效版本"
- 遇到旧条号 / 司法解释沿用旧法条号时，使用条文追溯，不要手工猜：
  `chinalaw trace <法规名> <旧条号> --from-as-of <旧时点> --to-as-of <目标时点> --items <项号> --format json`。
  只有 `ok=true` 才能写成已核验对应；`ok=false` 或低置信度候选必须报告不确定性。
- 经典反模式：用户引"侵权责任法 87 条"处理 2022 年高空抛物案 → 时间效力错误
  （应用民法典 1254 条），即使条文文本核对通过也要告警

更详细的版本时间锁定方法见 [`chinalaw-searching`](../chinalaw-searching/SKILL.md)
§"版本时间锁定"。

### 6. 缺失补全

如果 step 2 出现 `article: null` / `law_missing` / `law_stub`：

```bash
chinalaw fetch <name> --article <number> --format json
```

详细决策树（哪类文件去哪个 source）见
[`chinalaw-fetching`](../chinalaw-fetching/SKILL.md)。fetch 失败时不得凭模型
记忆补全 —— 必须在最终报告里写明 `unfindable: true` + 多源已尝试。

## 输出契约（核对报告字段）

每条引用至少输出：

```text
原引用: <law> <number>: "<quoted_text>"
归一化: <canonical_law_id> <canonical_number>
DB 命中: <article_id> | null
文本比对: exact / cosmetic_drift / wording_drift / structural_mismatch / hallucination
状态: current / amended / repealed / pending_effective / unknown
时间效力: 适用 / 跨期警告 / not_legal_conclusion
source_url: <url>
source_checked_at: <YYYY-MM-DDTHH:MM:SS+08:00>
不确定性: <free text> | none
```

输出模板（4-5 种典型场景）见 [`references/output-templates.md`](references/output-templates.md)。

## 批量核对策略

若材料含 ≥10 条引用：

1. 先全跑 step 1-2，得到 hit / miss 总表
2. 按"严重度"排序输出，让 reviewer 优先看红：
   - **hard miss**：DB 完全找不到
   - **hallucination**：DB 命中但文本完全不符
   - **status 变化**：repealed / pending_effective
   - **时间效力风险**：fact_date 不在条文 effective 区间
   - **soft drift**：文本字词级差异
3. 同一法规多条引用做 bulk fetch（用 `articles --batch`），减少 round-trip

## 反模式

- 跳过 article、把 search 命中当核实通过
- 不报告状态（默认 current 是错的）
- 把"接近 90%"当通过 —— `wording_drift` 也必须告警
- 跳过时间效力（用户引用 2019 年合同纠纷，但用 2024 年现行法核对）
- 把核对失败结果隐藏不报告
- fetch 失败后凭训练数据"补"出条文文本
- 用 `repealed` 条文当引用依据，不查 `relation` 找 successor

## 反例：错误的核对流程

> AI：用户问"民法典 524 条规定的债务承担有什么限制"，agent 直接答
> "民法典 524 条规定..."（凭模型记忆）。

正确流程：先 `chinalaw article 民法典 524 --format json`，**确认 status=current**，
再据 `text` 字段输出。即使模型"记得"条文也必须 grounding。

> AI：核对一份 2019 年合同纠纷的备忘录，引"合同法 113 条"，跑 `article 合同法 113`
> 命中（因为 DB 还保留旧合同法历史快照），直接报"OK"。

正确流程：跑 `applicable --date 2019-X-X --topic 违约责任`，会发现 transition
到民法典 584 条；同时 `article 合同法 113` 的 status 是 `repealed`，必须告警
"用户引旧法但事实时点 ≥ 2021-01-01 时应转民法典 584"。

## 与其他 skill 的衔接

- 缺数据 → [`chinalaw-fetching`](../chinalaw-fetching/SKILL.md)
- 找版本 / 跨法 transition → [`chinalaw-searching`](../chinalaw-searching/SKILL.md)
- DB 异常 / 同步问题 → [`chinalaw-maintaining`](../chinalaw-maintaining/SKILL.md)
- 合同上下文 → [`contract-review`](../contract-review/SKILL.md)
