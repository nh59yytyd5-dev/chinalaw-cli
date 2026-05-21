---
name: 功能建议
about: 提议一项新功能或对现有功能的改进
title: "[Feature] "
labels: enhancement
assignees: ''
---

<!--
提交前请先读：
- docs/PROJECT_CHARTER.md（项目定位）
- docs/DIFFERENTIATION.md（明确不做什么）
- docs/MVP_PLAN.md（当前阶段优先级）

如果你的需求落入"不做的范围"（商业法律数据库克隆、远程 SaaS / MCP 数据服务、AI 法律问答机器人等），
issue 仍欢迎，但可能会被标记为 `wontfix` 并附说明。
-->

## 用户故事

> 作为 ___（角色），我希望 ___（能力），以便 ___（目的）。

例如：
> 作为合同审查 agent，我希望 `chinalaw fetch` 能基于裁判文书号一行命中，
> 以便不再需要先 search_all_pages 拿 detail_id。

## 现状问题

<!-- 当前为什么做不到 / 做起来很别扭 -->

## 建议方案

<!-- 命令名 / 参数 / 输出 JSON 字段 / 数据流的草稿；不需要写代码 -->

## 影响范围

- [ ] 命令协议（README / docs/CONTRACT.md）会变
- [ ] 数据库 schema 会变（需要 ADR + migration）
- [ ] 新引入运行时依赖（需要 ADR + NOTICES 登记）
- [ ] 新接入数据源（需要 docs/COMPLIANCE.md 复核）
- [ ] 仅文档 / 测试 / 内部重构

## 替代方案

<!-- 你考虑过的其他做法，以及为什么不选它们 -->

## 其他信息

<!-- 相关 issue、上游讨论、参考资料 -->
