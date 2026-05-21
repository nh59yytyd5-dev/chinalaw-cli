# Agent 安装规范语料指南

本指南给 Claude Code / Codex / OpenCode / Cursor 等 agent 使用。目标是把用户需要的公开规范装进本地 `chinalaw` 数据库，避免法律任务中凭模型记忆引用条文。

## 核心原则

- 先问用途，再装 profile。不要默认把所有领域都装上。
- `data/recommended_corpus.json` 是安装索引，不是法律权威文本。
- 入库必须走 `chinalaw ensure --profile ...` 或 `chinalaw fetch ...`，不要直接改 SQLite。
- 遇到 `needs_verification=true`、`law_stub`、`law_seed`、`article_null` 时，必须继续 fetch / verify-source，不能直接引用。
- 不要读取用户素材目录正文来猜法规名；如果用户给目录，只能用文件名或让用户确认。
- 开源预览期不要一次性批量安装大型 profile。`ensure --profile baseline` 会优先加载随包 fixture；P1/P2 先按用户当前工作流缺口逐部 `ensure <law>`。遇到官方源限流必须停止。

## 标准流程

1. 先确认本机命令和数据库健康：

```bash
chinalaw doctor --format md
chinalaw status --format md
```

2. 查看可安装 profile：

```bash
chinalaw corpus list --format md
```

3. 询问用户主要用途，并映射 profile：

| 用途 | profile |
| --- | --- |
| 通用法律检索 / 合同 / 民商基础 | `general` |
| 合同审查 / 民商争议 | `contracts` |
| 公司商事 / 投融资 / 破产 | `company` |
| 劳动争议 / HR 合规 | `labor` |
| 刑事辩护 / 刑事合规 | `criminal` |
| 行政诉讼 / 政府合规 | `admin` |
| 房地产 / 建设工程 | `real-estate` |
| 婚姻家庭 / 继承 | `family` |
| 知识产权 / 反不正当竞争 | `ip` |
| 证券 / 资本市场 | `securities` |

4. 展开清单给用户看：

```bash
chinalaw corpus show general --format md
chinalaw corpus show criminal --no-deps --format md
```

5. 安装用户确认的 profile：

```bash
chinalaw ensure --profile baseline --format md

# baseline 会先用内置 fixture；宪法/刑法会加载现行文本和历史版本快照。

# P1/P2 建议按需或分批。先让用户确认本次任务真正需要哪些规范，再逐部补：
chinalaw ensure 劳动合同法 --format md
chinalaw ensure 民法典 --format md

# 仅在用户明确要批量预装、并接受官方源限流风险时使用：
chinalaw ensure --profile general --no-profile-deps --format md
```

6. 安装后复核：

```bash
chinalaw status --format md
chinalaw resolve 民法典 --format json
chinalaw outline 民法典 --format md
```

## 失败处理

- `unsupported_source`：当前 adapter 尚未实现；把法规名和 source 反馈给用户，不要声称已安装。
- `manual_review`：该条目重要但当前公开 fetch 路径尚未稳定；不要引用为已安装规范，除非用户提供官方 URL / detail_id 后另行 fetch 或人工入库。
- `FetchAmbiguousError`：运行 `chinalaw fetch <name> --source <source> --list-matches --format json`，让用户或上游逻辑选择 `--prefer-id`。
- `FetchNotFoundError`：换官方源前先 `chinalaw resolve <name>` 排除本地别名问题；仍失败再报告 unfindable。
- `FetchSourceError`：运行 `chinalaw verify-source <source> --format json` 判断是否上游改版；如果是项目缺陷，创建 GitHub issue。
- `FLK returned anti-bot JavaScript challenge`：停止批量安装，不要立即重试。改成单部 `ensure <law>`，间隔一段时间后再试；该批量安装问题见 issue #97。

## 反模式

- 不要为了省时间跳过 `ensure/fetch` 直接回答法律结论。
- 不要把商业数据库、网页复制文本或用户本机私域材料提交进仓库 fixture。
- 不要把 `九民纪要`、监管问答、交易所规则等非法律/司法解释文本当成同等位阶法源。
- 不要在时间效力问题中只查现行法；需要用 `applicable` / `relation` / `history` 查线索。
