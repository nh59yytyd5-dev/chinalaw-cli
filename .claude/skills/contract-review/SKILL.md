---
name: contract-review
description: 合同审查 / 合同风险清单 / 履行 / 违约 / 放款条件审查流程 skill。何时使用：用户要求审查合同、起草风险清单、核对放款条件、分析履行 / 解除 / 违约责任、检查审批制度或处理合同纠纷材料。
---

# 合同审查 Skill 模板

## 触发场景

用户要求审查合同、起草风险清单、核对放款条件、分析履行 / 解除 / 违约责任、检查审批制度或处理合同纠纷材料时使用。

## 核心纪律

- 先查 `chinalaw`，再写审查意见；不得凭模型记忆引用法条。
- 优先使用 `--format json`，需要给人类复核时再使用 `--format md`。
- 每条风险必须区分公开法规范、私域规范、交易对手要求和项目约束。
- `applicable` / `relation` 只提供时间效力 grounding 线索，不输出最终法律结论。
- `reference` / pending reference 不是已核验条文；最终引用必须落到 `article` 或 `norm clause`。

## 预检

在项目源码目录中运行时，可使用：

```bash
PYTHONPATH=src python3 -m chinalaw status --format json
PYTHONPATH=src python3 -m chinalaw sync --fixtures
PYTHONPATH=src python3 -m chinalaw sync --applicability
```

安装为 CLI 后，可把 `PYTHONPATH=src python3 -m chinalaw` 替换为 `chinalaw`。

## 审查流程

1. 抽取合同类型、交易结构、签署 / 履行 / 争议时间、审查重点、用户提供的私域文件。
2. 如果存在事实时间或法律变动风险，先查时间效力线索：

```bash
PYTHONPATH=src python3 -m chinalaw applicable --date <YYYY-MM-DD> --topic <主题> --format json
PYTHONPATH=src python3 -m chinalaw relation <法规名> --format json
```

3. 如有规范包，先校验再展示：

```bash
PYTHONPATH=src python3 -m chinalaw pack validate <规范包名> --format json
PYTHONPATH=src python3 -m chinalaw pack show <规范包名> --format json
```

4. 按问题检索并精确取条：

```bash
PYTHONPATH=src python3 -m chinalaw search <关键词> --format json
PYTHONPATH=src python3 -m chinalaw article <法规名> <条号> --format json
```

5. 如果 `article: null`、退出码为 `1`、或 `applicable` 返回 `needs_fetch`，尝试补全：

```bash
PYTHONPATH=src python3 -m chinalaw fetch <法规名> --article <条号> --format json
```

6. 如果用户提供公司制度、甲方要求或项目规则文件，先导入本地私域规范：

```bash
PYTHONPATH=src python3 -m chinalaw norm ingest <文件路径> --name <规范名称> --source-type company_policy --format json
PYTHONPATH=src python3 -m chinalaw norm clause <规范名称> <条款号> --format json
```

## 降级处理

- `not_legal_conclusion`：只写“存在时间效力风险 / 需进一步核对”，不得写成确定适用结论。
- `needs_fetch` / `law_missing` / `law_stub`：先 fetch 或说明缺失，不得直接引用缺失法规。
- `pending_reference_in_pack`：只能当检索提示，不能当法条原文。
- `FetchAmbiguousError`：先 `fetch <name> --list-matches`，再用 `--prefer-bbbs`。
- fetch 失败：明确说明本地未命中且远程补全失败，不得继续输出确定引用。

## 输出模板

每条风险建议使用：

```text
风险点：
事实/条款：
规范依据：
依据层级：公开法规范 / 私域规范 / 交易对手要求 / 项目约束
来源与核查：source_url / source_checked_at / status
分析：
修改建议：
不确定性：
```

## 禁止事项

- 不查询就引用法条。
- 把搜索命中摘要当成条文原文。
- 把私域制度说成国家法。
- 把 pending reference 当作 resolved article。
- 把 `--as-of` 或 `applicable` 当成完整时间效力判断。
