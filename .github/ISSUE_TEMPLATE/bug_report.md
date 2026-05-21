---
name: Bug 反馈
about: 报告一个可复现的 bug
title: "[BUG] "
labels: bug
assignees: ''
---

<!--
首先感谢花时间反馈。提交前请检查：

1. 这不是安全漏洞（安全问题请按 SECURITY.md 渠道私下报告）。
2. 没有重复 issue（搜一下相似关键词）。
3. 已读 README / docs/CONTRACT.md，知道当前 alpha 阶段哪些命令稳定、哪些是 maintainer-only。
-->

## 复现步骤

> 命令、输入、预期输出、实际输出；越小可复现越好。

```bash
# 例：
PYTHONPATH=src python3 -m chinalaw search 工作时间 --format json
```

## 实际行为

<!-- 贴 traceback / 错误输出 / 不期望的行为 -->

## 期望行为

<!-- 你认为应该是什么 -->

## 环境

- chinalaw 版本（`chinalaw --version` 或 commit hash）：
- Python 版本（`python3 --version`）：
- 操作系统 / 架构：
- SQLite 版本（`python3 -c "import sqlite3; print(sqlite3.sqlite_version)"`）：

## 是否涉及真实数据源

- [ ] 离线场景（fixtures / 本地 DB），不涉及上游
- [ ] 涉及 `flk_npc` / `court_gongbao` / `spp_gov_cn`（请说明 `verify-source` 输出 / 时间）
- [ ] 涉及私域规范 (`norm ingest`) / 规范包 (`pack`)

## 其他信息

<!-- log、相关 PR、上下文等 -->
