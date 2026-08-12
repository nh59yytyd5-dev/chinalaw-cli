---
name: legal-research
description: 通用法律检索 / 法律备忘录 / 法规梳理流程 skill。何时使用：用户提出法律问题、要求写法律备忘录、做法规梳理、审查合同 / 制度、核对某个条文或要求 agent 给出规范依据。
---

# 法律检索 Skill 模板

## 触发场景

用户提出法律问题、要求写法律备忘录、做法规梳理、审查合同/制度、核对某个条文或要求 agent 给出规范依据时使用。

## 基本原则

- 先用 `chinalaw` 取得规范依据，再组织分析。
- JSON 是机器处理主输出，Markdown 只用于人类复核。
- 搜索结果只是线索；最终引用必须来自 `article`、`get`、`norm clause` 或已解析的 `pack show`。
- 公开法规范、私域规范、规范包 reference 必须分层表述。
- 遇到时间效力 warning、pending reference、fetch 失败、条文缺失时，必须显式写入不确定性。

## 标准流程

1. 抽取问题要素：法域、事实时间、争议焦点、关键词、可能相关法规、是否存在私域文件。
2. 检查本地数据状态：

```bash
chinalaw status --format json
```

不要在每次法律问题里默认跑 `sync`。`sync --fixtures` / `sync --applicability`
是初始化和维护动作，会写库并增加延迟。只有 `status` 显示本地库未初始化、
`laws/articles/applicability_rules` 明显为 0，或用户明确要求维护数据库时，才进入
`chinalaw-maintaining` 流程执行同步。

3. 如果有事实时间或新旧法风险，先查时间效力线索：

```bash
chinalaw applicable --date <YYYY-MM-DD> --topic <主题> --format json
chinalaw relation <法规名> --format json
```

4. 用关键词检索候选：

```bash
chinalaw search "<关键词或多关键词>" --kind all --format json
```

**关键纪律**：search 返回的 `article_hits` **本身就是定位答案的锚点**。如果
其中某条 article 的 text 与你的问题主题对得上，**直接拿那个 (law_short_title,
number) 去 article 命令验证全文**，**不要**继续盲猜其他条号。
多关键词推荐加引号，例如 `chinalaw search "保证期间届满 签字" --kind article --format json`；
CLI 会兼容未加引号的多个 query token，但加引号更利于审查 trace。

5. 对候选法规和条文做精确定位：

```bash
chinalaw get <法规名> --format json
chinalaw article <法规名> <条号> --format card
```

法律研究场景优先用 `--format card` 或 `--format md --compact` 取单条正文，避免
`--format json` 把整部 law 元数据 / revision / categories 灌进上下文。只有要
做程序化字段比对、audit 或脚本消费时才用 JSON。

6. 如果条文缺失或法规是 stub，按需 fetch；**fetch 完成后立刻 outline 列条号**：

```bash
chinalaw fetch <法规名> --article <条号> --format json
chinalaw fetch <法规名> --list-matches --format json
# fetch 之后必跑：把所有条号 + 标题灌进上下文，再选具体条号
chinalaw outline <法规名> --format md
chinalaw article <法规名> <条号> --format card
```

如果已经知道目标条号，必须带 `--article <条号>`，不要直接 fetch 整部法规；整部
司法解释 / 长法会产生很大的 JSON，agent 宿主可能截断工具输出。

不要在 fetch 完成后随机猜条号试错（曾经观察到模型 fetch 21 条法规后挨个 article
1/5/8/10/20/21 试，跳过了第 6 条这个真正的答案）。先 outline 列出全部条号 +
标题，扫一遍找最匹配的那条，再 article 取全文。

7. 如果用户给出内部制度、甲方要求、项目规则：

```bash
chinalaw norm ingest <文件路径> --name <规范名称> --source-type private_policy --format json
chinalaw norm clause <规范名称> <条款号> --format json
```

8. 如果存在规范包：

```bash
chinalaw pack validate <规范包名> --format json
chinalaw pack show <规范包名> --format json
```

## 错误与降级

- `article: null`：不能引用该条，先 fetch；fetch 失败则报告缺失。
- `FetchAmbiguousError`：列出候选，让用户或上游规则选择 `--prefer-id`。
- `pending_reference_in_pack`：只能作为检索线索，不能作为法条原文。
- `not_legal_conclusion`：说明时间效力仍需人工法律判断。
- `law_missing` / `law_stub` / `needs_fetch`：先补全文本，否则不得确定引用。
- `pack validate` 返回 `ok=false`：报告缺失依赖，不得把该规范包当作完整依据。

## 输出要求

输出法律依据时至少包含：

```text
依据层级：
规范名称：
条号/条款号：
命令来源：search / article / fetch / norm clause / pack show
状态：current / amended / repealed / pending_effective / unknown
source_url：
source_checked_at：
是否存在时间效力或数据缺失不确定性：
```

## 禁止事项

- 不查询就凭模型记忆回答；不得凭模型记忆引用法条或来源。
- 只凭 `search` 命中就引用。
- 把私域制度、甲方要求或项目 memo 写成国家法。
- 把 `applicable` 写成最终适用结论。
- 隐藏 fetch 失败、条文缺失或 pending reference。
