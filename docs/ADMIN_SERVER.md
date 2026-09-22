# 资料库管理面板与服务器

0.6 起提供人工管理面板、只读 REST 与 MCP HTTP。一个服务由一个所有者管理一套资料库，支持本机运行或自托管服务器。CLI / stdio MCP 的安装方式保持兼容。

## 本机开始使用

在项目目录中安装可选服务组件。`--with-server` 会在仓库 `.venv` 里安装 `.[server]` 可选依赖，并在 `~/.local/bin` 写入 `chinalaw-server` shim（与 `chinalaw` / `chinalaw-mcp` 同一位置）：

```bash
scripts/install-local --with-server
chinalaw-server init --with-fixtures
chinalaw-server serve --open
```

Windows PowerShell 用 `.\scripts\install-local.ps1 -WithServer`，其他命令相同。也可以在任意 venv 里 `python -m pip install '.[server]'`，此时 `chinalaw-server` 由该 venv 的 `bin/`（Windows：`Scripts\`）提供。运行面板不需要 Node.js、CDN、Kimi 或模型 API。发布包包含构建好的前端。

默认资料库是 `~/.chinalaw/chinalaw.db`。也可以用 `--db /path/library.db` 指定另一套库；`init`、`serve` 和 `password` 必须指向同一套库。示例：

```bash
chinalaw-server init --db ./var/my-library.db --with-fixtures
chinalaw-server serve --db ./var/my-library.db --open
```

`init` 是显式写入操作。新库默认建立空库，`--with-fixtures` 会加载随包公开规范。旧 schema 升级前自动产生同目录的 `*.before-upgrade-时间.sqlite3` 数据库备份；来源附件目录保持原位。`init` 默认输出人类可读摘要，加 `--json` 输出机器可读 JSON（含 `db_path`、`schema_version`、`upgrade_backup` 与库状态），便于脚本判断。`serve` 不会自动初始化或升级资料库。

默认库 `~/.chinalaw/chinalaw.db` 由 `init` 升级到 schema 14 后，CLI（`chinalaw` / `chinalaw-mcp`）继续兼容同一文件，不需要另建库；升级前已自动生成上述备份。服务相关目录默认与资料库同级：`--state-dir` 默认为 `<db>.server-state/`（认证数据库 `auth.db`、会话与令牌），来源附件目录为 `<db>.assets/`（不可通过参数改动，随库迁移）。

升级资料库（再次运行 `init`）前必须先停止运行中的 `serve`：`init` 与服务的维护 worker 共用同一把 `<db>.worker.lock`，服务未停时 `init` 会报"此资料库已有维护服务运行"。

OAuth 发现、注册、换取和撤销令牌端点允许浏览器跨域访问（供 MCP Inspector 之类的浏览器客户端使用）；授权页、面板 API 与 MCP 端点仍只接受本服务来源。

本机默认只监听 `127.0.0.1:8765`。启动输出一次性配对链接，有效期 3 分钟，令牌放在 URL fragment 中，不进入 HTTP 访问日志。进入面板后可以设置密码；已有密码也可直接登录。遗失配对链接时重新启动本机服务可生成新链接。

## 人工维护流程

1. 在“公开法规”或“私域规范”中筛选资料，进入详情阅读全部条文、按条号定位、查看章节目次、来源与历史版本。
2. 在“导入与核对”上传文件，或选择官方来源并查找候选。公开法规和私域规范分别选择类型；已有资料可在详情页点击“重新导入”，自动绑定目标标识。
3. 处理任务只生成待确认预览。查看待入库全文、原文以及增删改差异，核对后勾选确认框并提交。
4. 已确认内容发生并发变化时，服务返回冲突，须重新生成预览。重复确认同一草稿返回同一维护记录。预览有效期为 24 小时，过期后一周内仍可查看和取消，之后自动清理；已确认的内容保留在维护记录中。私域规范名称在库内唯一：与另一份规范重名的预览在生成时即被拒绝；预览生成后名称被占用的，详情页提示冲突且不能确认。公开法规的共享分类定义在预览后变化的，同样先在详情页提示，而不是在确认时才报错。
5. “标记已核对”记录对当前内容版本的人工核对与备注。正文或来源元数据变化后，旧标记不再显示为当前版本已核对。
6. 历史版本或维护记录可以生成恢复预览，仍须核对后确认。缺少历史完整快照时明确提示，不能恢复不存在的正文。

支持 canonical JSON、TXT、Markdown、DOCX 和具有文本层的 PDF。公开 JSON 保留显式分类关联与版本标识/标签/备注；分类定义若与库内共享目录冲突，会提示核对而不会自动覆盖。单文件最多 20 MiB。PDF 使用本机 `pdftotext`：macOS 可安装 `brew install poppler`；Debian / Ubuntu 使用 `apt-get install poppler-utils`。页面会显示能力是否可用。扫描件需要先取得文本，本版不进行 OCR。

新上传原件和提取文本按内容指纹保存在 `library.db.assets/`。历史入库没有保留原件时，页面显示原件缺失并保留来源地址。来源链接打开的是网站当前内容，不能代替历史原件。CLI 后续更新的内容若与上次面板导入不同，不会沿用那次导入的原件标记。

任务持久保存在资料库中，关闭页面不丢进度。一个服务使用一个受控维护 worker，排队和运行任务合计最多 50 个。服务重启后，排队/运行中的旧任务标为中断，须手动重试；重试生成关联原任务的新尝试。等待确认的预览继续保留。取消网络任务需等待当前来源请求结束，取消不会写入正文。

## 单用户服务器部署

先把同一版本安装到服务器，再初始化库、设置所有者密码：

```bash
chinalaw-server init --db /srv/chinalaw/library.db --with-fixtures
chinalaw-server password --db /srv/chinalaw/library.db --state-dir /srv/chinalaw/server-state
chinalaw-server serve --server --host 127.0.0.1 --port 8765 \
  --db /srv/chinalaw/library.db --state-dir /srv/chinalaw/server-state \
  --public-url https://law.example.com
```

升级到新版本时同样先停掉 `serve`，再运行 `init` 升级资料库，最后重新启动服务（原因见上文的 `<db>.worker.lock`）。

前置 HTTPS 反向代理把请求转发到 `127.0.0.1:8765`，保留外部 `Host`。服务器模式下服务只信任来自本机（`127.0.0.1` / `::1`）反向代理的 `X-Forwarded-For` 头，用于按真实客户端地址限制登录，请让代理运行在同一主机并设置该头（Caddy 的 `reverse_proxy` 默认转发；Nginx 需显式 `proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;`）。登录失败按来源地址计数，5 分钟内 10 次后拒绝，且每次失败后响应会逐步延迟。修改密码需输入当前密码。服务器模式强制 HTTPS 对外地址与已设置的所有者密码。TLS 可以由代理终止；Uvicorn 后端只接收代理流量。使用域名根路径，不支持子路径挂载。一个库只运行一个 ASGI worker；多端通过 HTTP 访问同一服务，不把活跃 SQLite/WAL 放在多台机器共享的网络文件系统上。

也可用仓库的 Dockerfile 与 Caddy 配置（需要域名 DNS 指向服务器并开放 80/443）：

```bash
export CHINALAW_DOMAIN=law.example.com
docker compose -f deploy/compose.yaml build
docker compose -f deploy/compose.yaml run --rm library chinalaw-server init --with-fixtures
docker compose -f deploy/compose.yaml run --rm library chinalaw-server password
docker compose -f deploy/compose.yaml up -d
```

镜像内已设置 `CHINALAW_DB=/data/library.db`、`CHINALAW_STATE_DIR=/data/server-state`、`CHINALAW_HOST=0.0.0.0`，所以 `init` / `password` 不必重复传 `--db` / `--state-dir`。镜像以非 root 用户运行，包含 PDF 提取工具。资料、附件和认证状态保存在持久卷。首次初始化和密码设置由上述命令显式完成，重启容器不会自动重置数据。已有面板登录后的日常维护均可在浏览器完成。

升级镜像后需要重新 `init` 时，先停止服务容器再执行，否则 `init` 会因 `<db>.worker.lock` 被占用而拒绝：

```bash
docker compose -f deploy/compose.yaml stop library
docker compose -f deploy/compose.yaml run --rm library chinalaw-server init
docker compose -f deploy/compose.yaml up -d
```

环境变量可替代对应参数：`CHINALAW_DB`、`CHINALAW_STATE_DIR`、`CHINALAW_HOST`、`CHINALAW_PORT`、`CHINALAW_PUBLIC_URL`。不要把密码或访问令牌写入镜像、仓库或命令行参数。`password` 使用交互式隐藏输入。

## 远程客户端

### Bearer token

在“连接与备份”生成只读令牌，仅显示一次，可随时撤销。默认只允许公开法规，勾选后才增加私域只读权限。有效期可设 1–365 天。

REST 基址为 `https://law.example.com/api/v1`；MCP Streamable HTTP 地址为 `https://law.example.com/mcp`。将令牌保存在客户端的凭据设置或环境变量中。例如：

```bash
curl -H "Authorization: Bearer $CHINALAW_QUERY_TOKEN" \
  'https://law.example.com/api/v1/documents?kind=law&page=1&page_size=20'
curl -H "Authorization: Bearer $CHINALAW_QUERY_TOKEN" \
  'https://law.example.com/api/v1/search?q=合同&limit=10'
```

MCP 暴露 `chinalaw_resolve`、`chinalaw_search`、`chinalaw_article`、`chinalaw_list`、`chinalaw_document` 五个查询工具。全文工具按条文分页，每条返回完整文本；用 `offset/limit` 继续读取。没有 `ensure`、抓取、导入、恢复、删除或路径导出工具。查询缺失资料时返回诊断，不自动抓取或迁移。

### OAuth

支持交互授权的客户端连接 `/mcp` 后，可读取 `/.well-known/oauth-protected-resource/mcp` 与 `/.well-known/oauth-authorization-server`。

服务使用官方 MCP Python SDK 的注册、授权、PKCE、令牌端点，加单所有者授权页与持久存储。授权码一次性使用，S256 PKCE 校验；访问令牌绑定本服务资源与所有者，刷新令牌轮换，撤销作用于整次授权。回调允许 HTTPS 和回环地址的 HTTP。浏览器必须先以该资料库所有者身份登录，外部应用注册成功不等于取得资料读取权限。

这是一套实际运行的内置授权服务，不需要另外安装 Keycloak。此选择替代初始计划中待验证的外部 OIDC 候选，依据是官方 SDK 协议流程与真实 TCP 客户端测试已通过。它不提供第三方身份登录、多用户或组织身份联合。

已用 MCP SDK 2.2.0 的标准客户端测试 `legacy`（2025-11-25）与 `auto`（2026-07-28）两种 HTTP 协议模式，并覆盖匿名拒绝、私域隔离、完整正文和只读工具集合。既有 stdio 协议仍保持 2025-06-18。具体商业 Work 平台的账号权限、连接入口和托管 OAuth 接入未作实际登录验证，不作全平台兼容承诺。

## 备份迁移

“下载备份”生成 ZIP，包含 SQLite 在线一致性快照、所有受管附件、版本/维护记录及 SHA-256 文件清单。认证数据库 `auth.db` 不在备份内；来源端的密码、会话、配对链接与令牌不会在目标环境生效。目标环境继续使用自身的所有者身份和凭据配置。

恢复顺序是上传、验证、查看范围、确认。ZIP 压缩大小最多 512 MiB，展开最多 2 GiB、20,000 个条目；拒绝路径穿越、符号链接、重复或未列明文件、损坏 hash、不兼容 schema、视图/触发器和缺失附件。只接受此版本生成的资料备份，不把任意 SQLite 文件当成可迁移备份。

最终确认在维护门控和 SQLite 写事务中再次校验当前资料库指纹，把经过验证的已知数据表写入现有数据库文件，重建检索索引并检查关联。**不直接替换仍被 CLI 打开的数据库文件**；现有读取保持一致快照，CLI 的写入由 SQLite 锁协调。若 CLI 在恢复预览后已更新资料，恢复会拒绝；恢复提交以后开始的 CLI 写入继续正常执行。恢复操作或索引重建失败时整个事务回滚，先落盘的无引用附件不会破坏原库。

恢复会替换资料库内容，先下载当前备份。排队/运行中的备份任务恢复为中断状态，不自动执行。普通资料备份不是服务器灾难恢复的全部内容；需要保留部署身份时，应另外妥善备份服务器的认证状态目录。

## 开发与验证

```bash
python -m pip install -e '.[dev,server]'
python -m pytest tests tests_server -q
ruff check src tests tests_server
python scripts/check-public-fixtures
cd web
npm ci
npm run build
npx playwright install chromium
npm test
```

浏览器测试使用临时数据库、公开 fixtures 与虚构材料，测试结束后清理。运行测试的 `python` 应指向安装了服务依赖的环境。前端构建产物写入 `src/chinalaw/server/static/`，需与源码一起提交；安装后的服务直接使用这些文件。不得在真实私域资料库上运行开发验收。

需要前端热更新时，后端用 `chinalaw-server serve --public-url http://127.0.0.1:5173` 启动，再在 `web/` 执行 `npm run dev`。配对链接和浏览器均使用 `http://127.0.0.1:5173`，Vite 把 API 与 MCP/OAuth 请求转发到后端 8765，并保留 Host/Origin 校验。

Docker 镜像构建/启动门禁在 CI；本机没有容器运行时的环境只能验证直接 Python 运行和服务器配置，不能将其记为容器实测。
