## 概述

<!-- 1-3 句话：为什么改、改了什么、影响哪些命令。 -->

## 变更类型

- [ ] feat（新功能 / 新数据源 / 新命令）
- [ ] fix（修 bug）
- [ ] docs（文档 / 注释）
- [ ] refactor（不改外部行为）
- [ ] data（fixture / recommended corpus）
- [ ] schema（SQLite migration）
- [ ] chore（构建、CI、工具链）

## 关联 issue / ADR

> Closes #___ / Refs #___ / ADR: ___

## 自检清单

- [ ] 改动是一类问题的一般修复，不是单 case 硬编码。
- [ ] `PYTHONPATH=src python -m compileall -q src` 通过。
- [ ] `ruff check src` 通过。
- [ ] `python -m build` 通过。
- [ ] 如改 CLI / JSON / 退出码，已同步 `docs/CONTRACT.md`。
- [ ] 如改示例或用户路径，已同步 `README.md` 或 `docs/EXAMPLES.md`。
- [ ] 如改 fixture，已提供公开来源 URL、核查日期和验证命令。
- [ ] 没有提交密钥、本机路径、私域材料、商业数据库内容或评测产物。

## 验证步骤

```bash
# 贴出 reviewer 可复现的命令
```

## 兼容性影响

<!-- 命令协议、JSON 字段、退出码、schema 升级路径；如无，写 None。 -->
