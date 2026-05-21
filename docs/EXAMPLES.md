# 调用示例

> 面向 agent 和人类的最小可执行示例。未实现的计划功能不写成命令示例。

## 1. 初始化

```bash
PYTHONPATH=src python3 -m chinalaw sync --fixtures
PYTHONPATH=src python3 -m chinalaw sync --applicability
PYTHONPATH=src python3 -m chinalaw status --format md
```

使用临时数据库：

```bash
tmpdb=$(mktemp -t chinalaw.XXXXXX.db)
PYTHONPATH=src python3 -m chinalaw sync --fixtures --db "$tmpdb"
```

## 2. 基础检索

```bash
PYTHONPATH=src python3 -m chinalaw search 合同效力 --format json
PYTHONPATH=src python3 -m chinalaw search 合同效力 --format md
```

agent 使用规则：

- JSON 用于后续程序处理。
- Markdown 用于人类复核。
- 搜索结果只能作为线索，不能直接当作精确引用。

## 3. 精确取法条

```bash
PYTHONPATH=src python3 -m chinalaw article 民法典 第一百四十三条 --format json
PYTHONPATH=src python3 -m chinalaw article 民法典 143 --format md
PYTHONPATH=src python3 -m chinalaw article 民法典 143 --format card
PYTHONPATH=src python3 -m chinalaw article 民法典 524 --format md --no-footer
PYTHONPATH=src python3 -m chinalaw article 民法典 524 --format md --compact --arabic
```

agent 引用法条前，应优先使用 `article` 精确定位。

如果返回 `article: null` 或退出码为 `1`，不要编造条文，进入 fetch 或人工补全路径。

批量读取同一部法规下的多个条文：

```bash
PYTHONPATH=src python3 -m chinalaw articles 民法典 "5,12,13,19,23-25" --format json
PYTHONPATH=src python3 -m chinalaw articles 民法典 --numbers "5,12,13,19,23-25" --format json
```

agent 使用规则：

- `articles` 返回 `missing_count > 0` 或任一 `items[*].found=false` 时，不得引用缺失条文。
- 纯数字范围如 `23-25` 会展开为 `23,24,25`；插入条款仍应写成 `第十四条之一` 或 `14-1`。

先看目录再取条：

```bash
PYTHONPATH=src python3 -m chinalaw outline 民法典 --part 自然人 --preview-chars 60 --format md
```

限定法规范围检索：

```bash
PYTHONPATH=src python3 -m chinalaw search 民事主体 --in 民法典 --kind article --format json
```

`--in` 适合 agent 已知道大致法规范围时降低误召回；它不替代 `article` 精确定位。

## 4. 按需 Ensure / Fetch

日常工作流优先用 `ensure`：先看本地是否已有可引用条文，缺失或 stub 才联网补全。

```bash
PYTHONPATH=src python3 -m chinalaw ensure 民法典 公司法 --format md
PYTHONPATH=src python3 -m chinalaw ensure --from-dir "/path/to/法条" --filenames-only --format json
```

目录模式只读取文件名并推断法规名，不读取 PDF / Word 正文。它适合把“我关心的一批公开法规名称”转成可查询的本地缓存；私域制度、合同、甲方要求仍应走 `norm ingest`。

推荐规范语料 profile 是开源前的默认安装索引：

```bash
PYTHONPATH=src python3 -m chinalaw corpus list --format md
PYTHONPATH=src python3 -m chinalaw corpus show criminal --no-deps --format md
PYTHONPATH=src python3 -m chinalaw ensure --profile baseline --format md
PYTHONPATH=src python3 -m chinalaw ensure --profile contracts --no-profile-deps --format md
# 开源预览期优先按需逐部补；不要在 agent 工作流里无脑跑完整 general。
PYTHONPATH=src python3 -m chinalaw ensure 劳动合同法 --format md
```

`data/recommended_corpus.json` 只决定“建议抓哪些官方源”；真正可引用的文本仍必须来自 `ensure/fetch` 入库后的 `source_url`、`source_checked_at` 和条文正文。
`ensure --profile baseline` 会先加载随包 fixture；宪法/刑法会加载现行文本和历史版本快照。宪法序言可用 `article 宪法 序言` 查询。刑法跨期问题仍须单独核查溯及力，不能把 `--as-of` 当成最终适用结论。
`ensure --profile contracts --no-profile-deps` 当前可离线加载民间借贷、买卖、融资租赁、独立保函、票据、保险、电子商务等民商合同基础规范。
`ensure --profile ...` 仍是 alpha，遇到 FLK 反爬挑战应停止批量安装并改为按需补全。

`fetch` 是 agent 友好的补全入口，适合“缺哪条补哪条”。

```bash
PYTHONPATH=src python3 -m chinalaw fetch 民法典 --article 第五百八十五条 --format json
```

只看候选，不入库：

```bash
PYTHONPATH=src python3 -m chinalaw fetch 公司法 --list-matches --format md
```

指定候选：

```bash
PYTHONPATH=src python3 -m chinalaw fetch 公司法 --prefer-bbbs <bbbs-id> --format json
```

写 fixture，供 maintainer 审查后提交：

```bash
PYTHONPATH=src python3 -m chinalaw fetch 民法典 --to-fixture data/fixtures/civil_code.json
```

清洗规则或 alias 规则升级后，同一 `source_hash` 也需要重新写入时：

```bash
PYTHONPATH=src python3 -m chinalaw fetch 民法典 --force --format json
PYTHONPATH=src python3 -m chinalaw rebuild-clean --dry-run --format md
PYTHONPATH=src python3 -m chinalaw rebuild-clean --law 合同编通则解释 --format json
```

agent 使用规则：

- fetch 失败时必须向用户说明“本地未命中且远程补全失败”。
- 不要把 fetch 错误吞掉后继续输出确定引用。
- fetch 返回的 `source_url`、`source_checked_at`、`source_hash` 必须进入最终引用信息。
- 不要直接读写 SQLite 或调用 `_...` 私有 helper；清洗补写走 `rebuild-clean` / `fetch --force`。

## 4.1 真实源 Verify（维护者 / 发布前）

`verify-source` 是只读 smoke，不写 DB、不写 fixture。它用于确认真实官方源的 probe、search、fetch/clean、article locate 链路仍然可用。

```bash
PYTHONPATH=src python3 -m chinalaw verify-source flk_npc --format json
PYTHONPATH=src python3 -m chinalaw verify-source flk_npc --query 中华人民共和国公司法 --article 第一条 --format md
```

使用规则：

- 该命令需要联网，不应作为默认离线 CI 的强制步骤。
- `ok=false` 时应先处理上游结构变化或网络问题，再发布新版本。
- 它只验证链路健康，不代表本地数据库已经同步了对应法规。

## 5. 版本快照

```bash
PYTHONPATH=src python3 -m chinalaw history 民法典 --format md
PYTHONPATH=src python3 -m chinalaw get 民法典 --as-of 2021-01-01 --format json
PYTHONPATH=src python3 -m chinalaw diff 民法典 --from-as-of 2021-01-01 --to-as-of 2021-06-01 --format md
```

当前 `--as-of` 基于本地 `revisions` 快照，不等于完整时间效力判断。

如果事实时间可能影响适用法律，agent 应提示不确定性，并尝试查询历史版本、旧法和相关司法解释。

条文级旧条号核注使用 `trace`，不要人工拼 `history + diff + search` 后直接猜：

```bash
PYTHONPATH=src python3 -m chinalaw trace 民事诉讼法 257 \
  --from-as-of 2021-01-01 \
  --to-as-of 2024-01-01 \
  --items 3,5 \
  --format json
```

预期结果会显示 2017 版《民事诉讼法》第二百五十七条第（三）、（五）项可追到 2024 时点版本的第二百六十八条，并给出 `confidence`、`evidence` 与候选列表。`ok=false` 时不得引用为已核验结论，应先按返回的 hint 补全旧版本。

## 6. 时间效力线索（Alpha）

`applicable` / `relation` 只提供 grounding 线索，不输出最终法律意见。

```bash
PYTHONPATH=src python3 -m chinalaw sync --applicability
PYTHONPATH=src python3 -m chinalaw applicable --date 2022-01-01 --topic 合同效力 --domain litigation --format json
PYTHONPATH=src python3 -m chinalaw applicable --date 2019-01-01 --topic 合同效力 --format md
PYTHONPATH=src python3 -m chinalaw relation 民法典 --format md
```

agent 使用规则：

- 看到 `not_legal_conclusion` warning 时，不得把结果写成最终适用结论。
- 看到 `needs_fetch` 时，必须先补全缺失旧法或 stub 法规，再引用具体条文。
- 没有命中规则时，应回到 `search` / `history` / `fetch` / 上游数据库继续检索。

## 7. 私域规范

导入 JSON 私域规范：

```bash
PYTHONPATH=src python3 -m chinalaw norm import ./authorized/company-policy.json
PYTHONPATH=src python3 -m chinalaw norm show 放款要求 --format md
PYTHONPATH=src python3 -m chinalaw norm clause 放款要求 第二条 --format json
```

从文本 / Word / PDF 切条导入：

```bash
PYTHONPATH=src python3 -m chinalaw norm ingest ./authorized/lending-policy.txt \
  --name 甲方放款要求文本版 \
  --source-type lender_requirement \
  --alias 放款标准 \
  --metadata-json '{"verification":{"note":"内部制度原件清洗"}}'
PYTHONPATH=src python3 -m chinalaw norm ingest company-policy.pdf \
  --name 公司制度 \
  --source-type company_policy \
  --source-checked-at 2026-05-01T00:00:00+08:00
```

agent 使用规则：

- 私域规范不是国家法。
- 输出时必须标明制定主体、约束范围和来源类型。
- 不要把甲方要求、公司制度、项目 memo 表述为“法律规定”。

## 8. 规范包

规范包当前作为标签 / 收藏 / 问题域清单使用。

```bash
PYTHONPATH=src python3 -m chinalaw pack import ./authorized/contract-validity-pack.json
PYTHONPATH=src python3 -m chinalaw pack add 合同效力沉淀 \
  --create \
  --type article \
  --law 民法典 \
  --article 第一百四十三条 \
  --role core \
  --reason 合同效力判断基础条款
PYTHONPATH=src python3 -m chinalaw pack validate 民事法律行为有效条件 --format md
PYTHONPATH=src python3 -m chinalaw pack show 民事法律行为有效条件 --format json
```

agent 使用规则：

- 使用前先 `pack validate`。
- 工作流中沉淀新成员时用 `pack add`，默认只允许加入本地可解析的法规 / 条文 / 私域条款。
- `article` 成员可以作为精确取条入口。
- `reference` 成员只是提示或占位，不是已核验原文。
- pending reference 必须在输出中标明“待补全 / 未核验”。

## 9. 引用审查

审查文件里的法条引用是否真实、明确原文摘录是否一致：

```bash
PYTHONPATH=src python3 -m chinalaw audit file memo.md --format json
PYTHONPATH=src python3 -m chinalaw audit file memo.md --as-of 2024-01-01 --format md
```

审查规范包或私域规范：

```bash
PYTHONPATH=src python3 -m chinalaw audit pack 合同效力沉淀 --strict --format json
PYTHONPATH=src python3 -m chinalaw audit norm 公司制度 --format md
```

项目级 grounding 审计：先在工作流中打开检索快照，再审查最终报告是否回连到快照证据。

```bash
PYTHONPATH=src python3 -m chinalaw snapshot init
PYTHONPATH=src python3 -m chinalaw article 民法典 143 --format card
PYTHONPATH=src python3 -m chinalaw search "违约金 酌减" --kind article --format json

PYTHONPATH=src python3 -m chinalaw audit grounding final.md \
  --strict \
  --format json
```

agent 使用规则：

- `audit` 是门禁，不是检索替代。未通过时先按 `suggested_command` 精确复核。
- `audit grounding` 是项目级证据链审查：`verified` 才代表最终文本引用回连到
  article 级证据；`retrieved_only` 只是搜过候选，不能当作已核验。
- `reference` / `pending:` 会被继续审查，不能当作已核验条文。
- 普通法律命题不是原文摘录；只有显式引号 / 原文 / 条文 / 摘录 / 规定如下
  才触发文字一致性审查。
- 文本出现事实日期时，优先带 `--as-of` 重跑，避免当前法误用到历史事实。

## 10. 合同审查最小流程

```text
1. 识别合同问题：效力、履行、解除、违约责任、损害赔偿、管辖等。
2. 抽取关键事实时间。
3. 先用 applicable / relation 检查是否存在时间效力线索。
4. 再用 search 找法条线索。
5. 用 article 精确取条。
6. article 缺失或 needs_fetch 时用 fetch 补全。
7. 如果有私域规范，使用 norm clause 精确定位。
8. 如果有规范包，先 validate，再 show。
9. 输出结论时标注来源、状态、时间、不确定性。
```

示例命令：

```bash
PYTHONPATH=src python3 -m chinalaw search 违约责任 --format json
PYTHONPATH=src python3 -m chinalaw applicable --date 2022-01-01 --topic 合同效力 --format json
PYTHONPATH=src python3 -m chinalaw article 民法典 第五百零九条 --format json
PYTHONPATH=src python3 -m chinalaw fetch 民法典 --article 第五百八十五条 --format json
```

## 11. 推荐输出纪律

agent 输出法律依据时，应包含：

- 法规 / 规范名称
- 条号
- 来源类型：公开法规范 / 私域规范 / 参考文本
- 状态：现行 / 已修改 / 已废止 / 未知
- `source_url`
- `source_checked_at`
- 是否为 pending reference
- 是否存在时间效力不确定性

## 12. 禁止行为

- 不查询就凭模型记忆引用法条。
- 把搜索摘要当成法条原文。
- 把私域制度说成国家法。
- 把 pending reference 当作 resolved article。
- fetch 失败后继续输出确定引用。
- 把 `--as-of` 当成完整时间效力判断。
