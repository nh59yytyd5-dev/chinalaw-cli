<!--
在提 PR 前请检查：

1. 已读 docs/CONTRIBUTING.md（提交规范、代码风格）。
2. 已读 docs/DEVELOPMENT_GUIDE.md（模块边界、测试要求、schema 纪律）。
3. 不引入新的运行时依赖（除非已经有 ADR 并登记 NOTICES.md）。
4. 通过本地基线：
   - `PYTHONPATH=src python3 -m unittest discover -s tests -v`
   - `PYTHONPATH=src python3 -m compileall -q src tests`
   - `ruff check src tests`
5. 必要时附 `chinalaw verify-source ...` 的真实 smoke 输出。
-->

## 概述

<!-- 1-3 句话：为什么、改了什么、影响哪些命令 -->

## 变更类型

- [ ] feat（新功能 / 新数据源 / 新命令）
- [ ] fix（修 bug）
- [ ] docs（仅文档 / 注释）
- [ ] refactor（不改外部行为）
- [ ] test（仅加测试 / 调测试）
- [ ] chore（构建、CI、工具链）
- [ ] schema（涉及 SQLite migration）

## 关联 issue / ADR

> Closes #___ / Refs #___ / 见 `docs/decisions/ADR-NNNN-*.md`

## 自检清单

- [ ] 单元测试已加 / 已更新（覆盖新代码路径与已知 regression）
- [ ] `unittest discover` 全绿（贴出测试数量：旧 X → 新 Y）
- [ ] `ruff check` 零警告
- [ ] `compileall` 零错误
- [ ] 不新增运行时依赖；如新增，已在 NOTICES.md 登记并附 ADR
- [ ] 协议字段变化已同步 `docs/CONTRACT.md` 与示例
- [ ] schema 变化已写 migration + 测试 + ADR
- [ ] CHANGELOG.md `[Unreleased]` 已记录用户可见的行为变化
- [ ] 没有提交真实抓取的法规数据 / 个人路径 / 密钥 / 商业软件名

## 验证步骤

```bash
# 贴出能被 reviewer 一行复现的命令
```

## 兼容性影响

<!-- 命令协议、JSON 字段、退出码、schema 升级路径；如无勾选 None。 -->

- [ ] None
