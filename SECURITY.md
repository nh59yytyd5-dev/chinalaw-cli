# 安全策略

## 受支持版本

`chinalaw-cli` 当前为 alpha 阶段，仅维护最新的 minor 版本。安全修复会发到下一个
patch 版本，并在 [`CHANGELOG.md`](./CHANGELOG.md) 中以 `### 修复（安全）` 段说明。

| 版本 | 是否接受安全报告 |
|------|---------|
| 0.1.x | ✅ |
| < 0.1 | ❌（早期骨架阶段，请升级） |

## 报告漏洞

请**不要**在公开 issue 中披露安全漏洞，按以下渠道之一私下报告：

1. **首选**：[GitHub Security Advisory](https://github.com/nh59yytyd5-dev/chinalaw-cli/security/advisories/new)
   —— 自动加密、可邀请协作者、可发 CVE 申请。
2. 直接私信项目主要维护者（GitHub `@nh59yytyd5-dev`）。

报告时建议附上：

- 复现步骤（最小可复现样例最佳）
- 受影响版本（含 commit hash）
- 你认为的影响面与严重程度
- 是否愿意合作披露 / 接受致谢

## 响应承诺

| 阶段 | 时限 |
|------|------|
| 首次确认收到（acknowledge） | 72 小时内 |
| 初步评估反馈（triage） | 7 天内 |
| 修复发布（fix） | 视严重程度，30-90 天内 |
| 协调披露（coordinated disclosure） | 修复发布后 14 天内或与报告者协商 |

## 范围

本仓库的安全策略覆盖：

- `chinalaw` Python 包代码（`src/chinalaw/`）
- 默认 fixture 数据（`data/fixtures/`、`data/applicability/`、`data/packs/`、
  `data/norms/`）
- CI 配置（`.github/workflows/`）
- 发布产物（PyPI wheel、sdist）

**不在范围**：

- 上游数据源站点本身的安全问题（请向上游政府机构报告）
- 用户自己 ingest 的私域规范（`norm`）内容安全
- 安装到本机后被外部恶意软件篡改的本地 SQLite 数据库

## 合规相关安全约束

本项目对官方公开数据源的抓取行为遵守 [`docs/COMPLIANCE.md`](./docs/COMPLIANCE.md)
定义的合规边界（公有领域 / 节流硬下限 100 ms / 不绕反爬 / 不抓 PII / UA 标识）。
如发现项目代码绕过这些约束、或被某条 PR / commit 削弱了它们，按本文 §"报告漏洞"
渠道报告——这同样视作安全问题。

## 致谢

修复发布时，会在 release notes 中署名报告者（除非报告者要求匿名）。
