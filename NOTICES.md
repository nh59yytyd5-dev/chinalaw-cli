# Third-Party Notices

本项目遵守所有第三方组件的授权要求。每次引入新的依赖或数据源，都在此文件登记：来源、许可证、使用方式、登记日期。

发布前会按 [`docs/CONTRIBUTING.md`](./docs/CONTRIBUTING.md) 的门禁对依赖、
数据来源与构建产物做完整审计。

---

## 1. Python 依赖

| 包名 | 版本约束 | License | 来源 | 用途 | 登记日期 |
|------|---------|---------|------|------|---------|
| FastAPI | >=0.141.1,<0.142 | MIT | https://github.com/fastapi/fastapi | 可选 HTTP 管理服务 | 2026-09-13 |
| Uvicorn | >=0.52.4,<0.53 | BSD-3-Clause | https://github.com/encode/uvicorn | 可选 ASGI 服务 | 2026-09-13 |
| MCP Python SDK | >=2.2,<2.3 | MIT | https://github.com/modelcontextprotocol/python-sdk | 可选 MCP HTTP 与 OAuth 协议端点 | 2026-09-13 |
| python-multipart | >=0.0.20,<1 | Apache-2.0 | https://github.com/Kludex/python-multipart | 可选文件上传解析 | 2026-09-13 |
| Starlette | 由 fastapi 约束 | BSD-3-Clause | https://github.com/encode/starlette | 经 fastapi 传递引入；服务代码直接 import（`server/guard.py`、`app.py`、`routes_*.py`、`mcp_http.py`） | 2026-09-22 |
| Pydantic | 由 fastapi 约束 | MIT | https://github.com/pydantic/pydantic | 经 fastapi 传递引入；服务代码直接 import 用于请求/响应模型 | 2026-09-22 |

上述依赖仅由 `server` 可选安装项加载，CLI 核心继续使用 stdlib。`httpx`（BSD-3-Clause，https://github.com/encode/httpx）仅作为 `dev` 可选安装项的测试依赖（服务传输层测试客户端），不随运行时分发。以下是未引入核心 CLI 的候选依赖：

- `typer` — MIT — CLI 框架
- `rich` — MIT — 终端输出格式化（typer 间接依赖）
- `beautifulsoup4` / `lxml` — MIT / BSD — HTML 解析（爬虫使用）
- `python-docx` — MIT — docx 解析（若复用 docx 数据源）
- `markdown-it-py` — MIT — Markdown 解析

所有候选依赖均为 MIT / BSD / Apache 系宽松许可证，与本项目 Apache-2.0 兼容。

## 2. 数据来源

| 数据源 | URL | 性质 | 授权 | 使用方式 | 登记日期 |
|--------|-----|------|------|---------|---------|
| 国家法律法规数据库 | https://flk.npc.gov.cn | 全国人大常委会办公厅主办的官方公开数据库 | 法律 / 行政法规 / 司法解释正文属公有领域（著作权法 §5） | adapter `chinalaw.adapters.flk_npc`：probe / search_list / fetch_detail（Word docx） / build_law_payload；节流默认 500ms，硬下限 100ms；UA 含 `chinalaw-cli/<version>` 标识 | 2026-04 |
| 国家行政法规库 | https://www.gov.cn/zhengce/xzfgk/ | 中国政府网 / 司法部公开行政法规入口 | 行政法规与官方版本线索属公有领域 | adapter `chinalaw.adapters.gov_xzfgk`：probe / discover / fetch；不承诺 batch sync | 2026-05 |
| 最高人民法院公报 | https://gongbao.court.gov.cn | 最高人民法院公报站点（ASP.NET 静态 HTML） | 司法解释 / 司法文件 / 公报案例 / 工作报告等正文属公有领域 | adapter `chinalaw.adapters.court_gongbao`：probe / search_list / fetch_detail / cross_search / build_law_payload；节流 500ms / 硬下限 100ms；UA 同上 | 2026-05 |
| 最高人民法院主站 | https://www.court.gov.cn | 最高人民法院官方发布站点 | 司法解释、司法政策与通知等官方文本属公有领域 | adapter `chinalaw.adapters.court_main`：probe / search_list / fetch_detail | 2026-05 |
| 最高人民检察院 | https://www.spp.gov.cn | 最高人民检察院官方站点（Tengine 静态 .shtml） | 两高 / 两高一部联合刑事司法解释 / 检察规范文件 / 指导性案例正文属公有领域 | adapter `chinalaw.adapters.spp_gov_cn`：probe / search_list / fetch_detail / build_law_payload；节流 500ms / 硬下限 100ms；UA 同上 | 2026-05 |
| 中国证监会 | https://www.csrc.gov.cn | 国务院证券监督管理机构官方站点 | 部门规章与监管规则官方文本属公有领域 | adapter `chinalaw.adapters.csrc_gov_cn`：probe / discover / fetch | 2026-05 |
| 国家金融监督管理总局 | https://www.nfra.gov.cn | 金融监管部门官方站点 | 部门规章、规范性文件与监管规则官方文本属公有领域 | adapter `chinalaw.adapters.nfra_gov_cn`：probe / discover / fetch | 2026-05 |
| 北京证券交易所 | https://www.bse.cn | 证券交易所官方规则站点 | 自律规则官方公开文本 | `securities_rules` adapter profile：probe / discover / fetch | 2026-05 |
| 上海证券交易所 | https://www.sse.com.cn | 证券交易所官方规则站点 | 自律规则官方公开文本 | `securities_rules` adapter profile：probe / discover / fetch | 2026-05 |
| 深圳证券交易所 | https://www.szse.cn | 证券交易所官方规则站点 | 自律规则官方公开文本 | `securities_rules` adapter profile：probe / discover / fetch | 2026-05 |
| 中国结算 | https://www.chinaclear.cn | 登记结算机构官方规则站点 | 自律规则官方公开文本 | `securities_rules` adapter profile：probe / discover / fetch | 2026-05 |
| 中国证券业协会 | https://www.sac.net.cn | 行业自律组织官方规则站点 | 自律规则官方公开文本 | `securities_rules` adapter profile：probe / discover / fetch | 2026-05 |

命令能力、成熟度、host allowlist 和已知限制以
[`data/source_coverage.json`](./data/source_coverage.json) 为机器可读单一清单。

合规边界详见 [`docs/COMPLIANCE.md`](./docs/COMPLIANCE.md)：数据范围、5 条红线（不
绕反爬 / 不抓 PII / 不 DDoS / 不整站镜像 / 不冒充身份）、节流硬下限、UA 标识、
上游联系方式与使用者责任。

法律条文本身依据《中华人民共和国著作权法》第 5 条不适用著作权法，属于公有领域。

### 当前未接入但已评估的候选源

- 国家网信办等存在稳定性或反爬挑战的官方站点：仅保留 catalog 线索，不绕过访问控制。
- 公安部、司法部等尚未达到公开 adapter 门禁的官方站点：能力状态以
  `data/source_coverage.json` 的 `adapter_status` 为准。
- 北大法宝、威科、法信等商业数据库：本仓库不直接接入；用户可在其许可范围内通过
  local-only 私域导入使用。

## 3. 构建与工具链

浏览器运行产物包含 React、React DOM 和 Scheduler（MIT，Meta Platforms, Inc. and affiliates）。
完整许可随静态资源分发为 `assets/third-party-licenses.txt`，源副本在
`web/public/assets/third-party-licenses.txt`。React 来源：https://github.com/facebook/react。
Vite、TypeScript、Prettier 与 Playwright 只用于前端构建或测试，精确版本锁在 `web/package-lock.json`，运行面板不需要 Node.js。

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
