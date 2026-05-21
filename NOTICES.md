# Third-Party Notices

本项目遵守所有第三方组件的授权要求。每次引入新的依赖或数据源，都在此文件登记：来源、许可证、使用方式、登记日期。

开源发布前会对本文件做一次完整审计（见 [`OPEN_SOURCE_CHECKLIST.md`](./OPEN_SOURCE_CHECKLIST.md)）。

---

## 1. Python 依赖

| 包名 | 版本约束 | License | 来源 | 用途 | 登记日期 |
|------|---------|---------|------|------|---------|
| _（尚未引入）_ | | | | | |

说明：MVP 阶段计划引入的候选依赖（待正式加入 `pyproject.toml` 时再登记到上表）：

- `typer` — MIT — CLI 框架
- `httpx` — BSD-3-Clause — HTTP 客户端
- `pydantic` — MIT — 数据模型与校验
- `rich` — MIT — 终端输出格式化（typer 间接依赖）
- `beautifulsoup4` / `lxml` — MIT / BSD — HTML 解析（爬虫使用）
- `python-docx` — MIT — docx 解析（若复用 docx 数据源）
- `markdown-it-py` — MIT — Markdown 解析

所有候选依赖均为 MIT / BSD / Apache 系宽松许可证，与本项目 Apache-2.0 兼容。

## 2. 数据来源

| 数据源 | URL | 性质 | 授权 | 使用方式 | 登记日期 |
|--------|-----|------|------|---------|---------|
| 国家法律法规数据库 | https://flk.npc.gov.cn | 全国人大常委会办公厅主办的官方公开数据库 | 法律 / 行政法规 / 司法解释正文属公有领域（著作权法 §5） | adapter `chinalaw.adapters.flk_npc`：probe / search_list / fetch_detail（Word docx） / build_law_payload；节流默认 500ms，硬下限 100ms；UA 含 `chinalaw-cli/0.1.0` 标识 | 2026-04 |
| 最高人民法院公报 | http://gongbao.court.gov.cn | 最高人民法院公报站点（ASP.NET 静态 HTML） | 司法解释 / 司法文件 / 公报案例 / 工作报告等正文属公有领域 | adapter `chinalaw.adapters.court_gongbao`：probe / search_list / fetch_detail / cross_search / build_law_payload；节流 500ms / 硬下限 100ms；UA 同上 | 2026-05 |
| 最高人民检察院 | https://www.spp.gov.cn | 最高人民检察院官方站点（Tengine 静态 .shtml） | 两高 / 两高一部联合刑事司法解释 / 检察规范文件 / 指导性案例正文属公有领域 | adapter `chinalaw.adapters.spp_gov_cn`：probe / search_list / fetch_detail / build_law_payload；节流 500ms / 硬下限 100ms；UA 同上 | 2026-05 |

合规边界详见 [`docs/COMPLIANCE.md`](./docs/COMPLIANCE.md)：数据范围、5 条红线（不
绕反爬 / 不抓 PII / 不 DDoS / 不整站镜像 / 不冒充身份）、节流硬下限、UA 标识、
上游联系方式与 24 小时响应承诺、使用者责任。

法律条文本身依据《中华人民共和国著作权法》第 5 条不适用著作权法，属于公有领域。

### 当前未接入但已评估的候选源

| 候选源 | URL | 状态 | 备注 |
|--------|-----|------|------|
| 中国政府网·行政法规库 | https://www.gov.cn/zhengce/xzfgk/ | 暂缓 | SPA 框架，标准客户端只能拿到 `<title>` 框架；与 flk_npc 重叠，优先级低（ADR-0008 §2） |
| 国家网信办 | https://www.cac.gov.cn | 暂缓 | jsl5 反爬挑战，stdlib 客户端无法过；本项目不绕反爬（ADR-0008 §2 + COMPLIANCE.md §2） |
| 中国证监会 | https://www.csrc.gov.cn | 暂缓 | URL 结构碎片化，待真实资本市场合规审查需求出现再评估（ADR-0008 §2） |
| 北大法宝 / 威科 / 法信 | — | 不接入 | 商业数据库，与 PROJECT_CHARTER §"差异化边界"冲突；可作为用户自带订阅的本地 ingest 源，但本仓库不接入 |

## 3. 构建与工具链

| 工具 | 用途 | License | 备注 |
|------|------|---------|------|
| Python | 运行时 | PSF License | — |
| uv | 包与虚拟环境管理 | Apache-2.0 / MIT | 开发工具，不分发 |
| SQLite | 本地数据库（Python stdlib 内置） | Public Domain | — |
| FTS5 | 全文索引（SQLite 扩展） | Public Domain | — |

## 4. 参考资料与文档灵感

| 资源 | URL | 用途 |
|------|-----|------|
| （待补充） | | |

本项目独立开发，与任何商业法律查询软件均无派生或关联关系。设计采用通用领域建模（Law / Article / Category / Revision），不复用任何闭源系统的数据结构、字段命名或标识符。
