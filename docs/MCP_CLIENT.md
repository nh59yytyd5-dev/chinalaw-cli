# 将 chinalaw 接入你的 MCP 客户端

本页是 Claude Code、DeepSeek Harness（DSH）和其他 agent 客户端接入已有 HTTP 服务的说明。安装 CLI、保存令牌都不会自动替客户端注册 MCP；注册必须发生在正在使用的 harness 中。

## 先选本地还是远端

| 方式 | 配置 | 数据在哪里 |
|---|---|---|
| 本地 stdio | command=`chinalaw-mcp`，args=`["--db", "/absolute/library.db"]` | 自己的 SQLite；不读取 remote.env，不连接托管服务器 |
| 远程 Streamable HTTP | URL=`https://law.newcombmath.com/mcp`，Authorization 请求头 | 已部署的只读资料库 |

`chinalaw-mcp` 是本地服务器，不是远端客户端或 HTTP 转发器。不要尝试给它传 `--remote`。远端未接好时可使用本地库，但必须说明数据来源不同。

## 凭据与客户端配置

自托管时，由所有者在管理面板签发查询令牌，或通过客户端支持的 OAuth 流程授权。本站管理面板为 `/console`；托管访问需向维护者申请独立令牌，公开 Issue 只说明需求，不贴令牌。公开只读权限为 `chinalaw:public:read`。

在支持 `mcpServers` 配置格式的客户端中，可参考以下 HTTP 配置，将占位符替换成自己的令牌：

```json
{
  "mcpServers": {
    "chinalaw": {
      "type": "http",
      "url": "https://law.newcombmath.com/mcp",
      "headers": {"Authorization": "Bearer YOUR_PERSONAL_TOKEN"}
    }
  }
}
```

这是常见 HTTP 配置形态，不是所有 harness 通用的配置文件格式。DSH Web GUI 应在其实际支持的 MCP 注册入口配置 Streamable HTTP、完整 URL 和请求头；若版本只支持 stdio，需要先升级或使用本地 stdio 库。不要把上述 JSON 随意写进 DSH 未确认的配置路径，也不要把它作为普通聊天消息提交给模型。客户端若支持安全凭据存储或环境变量替换，优先使用其明确支持的方式；`${VAR}` 并不是所有客户端都会展开。

保存后重新连接 MCP，必要时开启新会话。工具列表应出现六个 `chinalaw_*` 工具。HTTP 服务已连通但 agent 的工具列表仍无这些工具，说明还需要检查 harness 的注册、工具启用或会话刷新，不能判定服务器没有数据。

## remote.env 由谁读取

`~/.config/chinalaw/remote.env` 是可选的本机凭据文件，不会被所有客户端自动发现。自 0.7.2 起，本项目的 `chinalaw-remote-check` 连接检查命令支持读取它；CLI 查询命令、本地 `chinalaw-mcp`、服务器和 DSH **不会因此自动注册或切换到远端**。

```dotenv
CHINALAW_REMOTE_URL=https://law.newcombmath.com
CHINALAW_QUERY_TOKEN=YOUR_PERSONAL_TOKEN
```

URL 可填写基址或完整 `/mcp` 地址。文件不要提交到仓库；自行创建时限制为仅本人可读。检查工具只解析赋值，不执行 shell；同名进程环境变量优先于文件。没有文件时，可只设置这两个环境变量。

安装本版 wheel 的 server 可选组件（含官方 MCP SDK）后运行：

```sh
python -m pip install './chinalaw-0.7.2-py3-none-any.whl[server]'
chinalaw-remote-check
# 也可显式指定凭据文件，并验证法规内搜索：
chinalaw-remote-check --env-file /absolute/remote.env --query 保证期间 --law 民法典
```

源码环境可用 `python -m chinalaw.remote_check`。命令会真正握手、列工具、搜索一次，并退出；它不是常驻代理，也不修改 harness 配置。成功打印 `ok=true`、工具名和命中数；搜索零命中仍是成功的协议调用。缺凭据、401、工具缺失、解析失败或超时输出 `ok=false` 并以非零退出，不输出令牌。指定 `--law` 时先检查工具是否宣告 `in_laws`，旧服务器会明确报能力不足，不会退回全库搜索。

## 探测、握手与响应格式

- `GET /healthz` 无需令牌，返回 `ok`、`version`。它仅证明 Web 进程存活，不证明数据库可查询、凭据有效或 MCP 已注册。
- `POST /mcp` 是 Streamable HTTP 端点。无凭据返回 401 属于正常鉴权。不要探测 `/health`、`/api/health`、`/sse`、`/messages` 来推断这个部署是否正常。
- MCP 检查以“握手 → 列工具 → 实际查询”全部完成为准，推荐官方 SDK 或上面的检查命令。

自己实现客户端时：

1. POST JSON-RPC 2.0 `initialize`，请求头包含 Authorization、`Content-Type: application/json`、`Accept: application/json, text/event-stream`。
2. 读取协商的协议版本；如果响应返回 `Mcp-Session-Id`，后续请求携带它。发送 `notifications/initialized`，再发 `tools/list` / `tools/call`。本部署的兼容协议会建立会话；不同协商版本不能硬套同一会话假设。
3. 根据**响应 Content-Type**解析。`application/json` 是一个完整 JSON-RPC 对象；`text/event-stream` 才按 SSE 事件解析其 data 内容。不要只提取 `data:` 行后将空列表当成功。通知的 202/空正文可以正常，等待有 ID 的请求响应时“没解析出匹配 ID 的结果”必须报错。
4. 检查 JSON-RPC `error`、工具结果 `isError` 和必要字段。`tools/list` 的空工具列表是能力/配置异常，不是“库中没有法规”；它不查询法规数量。
5. 对 429 按 Retry-After 重试；会话失效应重新握手。不要无限重试或吞掉解析错误。

## 六个远端工具及边界

| 工具 | 用途 |
|---|---|
| chinalaw_resolve | 名称、简称和别名解析；失败给候选 |
| chinalaw_article | 按 law、number 取完整原文；as_of 可指定历史时点 |
| chinalaw_search | 原文检索；支持 kind/limit/as_of/status/level/region/versions；0.7.2 起支持 in_laws |
| chinalaw_list | 分页目录 |
| chinalaw_document | 分页全文；公开法规 id 可用法规名、别名或 ID |
| chinalaw_applicable | 已录入的时间效力检索指引，覆盖不完整，不输出选法结论 |

法规内搜索示例（对应 CLI `search 保证期间 --in 民法典`）：

```json
{"name":"chinalaw_search","arguments":{"query":"保证期间","in_laws":["民法典"],"kind":"article"}}
```

in_laws 接受字符串或列表（最多 20 个名称/ID，每项最多 200 字）；明确旧版 ID 仍按该 ID 限定。不认识的名称列在 `law_filter.unresolved/unresolved_candidates`，不会改搜全库；多个名称只有部分解析成功时，检查 unresolved，不能把部分覆盖当完整覆盖。REST 对应 `GET /api/v1/search?q=保证期间&in_laws=民法典`，多个名称以逗号分隔。旧版客户端应先读 tools/list 的 schema 确认支持，不能仅发送未知字段并假设已生效。

HTTP 暂无 CLI 的章节限定 `--in-part`、批量取条、history/diff/trace、抓取导入和写操作。批量离线工作使用本地库。最终引用使用 article 核验，不能把 search 的正文引用条号当成指定条文。

`kind=law` 指库中的公开资料，包含司法解释等公开文件，不是仅指法律这一效力层级。
`kind=article` 检索公开条文；`kind=norm` 指私人导入资料，需要额外私域权限。
`kind=all` 检索当前凭据获准访问的内容。不要为检索司法解释而选择 `norm`。

零命中时可查看 `guidance.code/message/next_steps`。`next_steps` 是尚未执行的建议，
其中 `tool=search/resolve` 对应 `chinalaw_search/chinalaw_resolve`，`arguments` 可直接使用。
搜索建议保留原日期、版本、法规范围及筛选条件；未解析范围会先建议 resolve，不能把它
改成全库结果。建议不会证明相关资料已收录。`search(kind="law" 或 "all")` 可直接匹配
`法释〔2024〕10号` 这类文号元数据；标题和文号同时给出时，两者都须匹配。
`resolve` 也支持单独的文号；`via=document_number_match` 表示唯一的文号匹配。
同一文号对应多个文件时返回候选，请选明确 ID 后读取，不能任选其中一个。

`resolve.via=like_fallback` 仅表示名称子串命中，须核对 `official_title`；命中修改决定
不能自动证明它就是所问的完整法规。`document` 的 `offset` 从 0 开始，`limit` 按条目数
计数；按 `next_offset` 续读，`next_offset=null` 表示结束，`returned` 是本页条目数。

## 不要混淆三种“没找到”

1. **接入失败**：没有工具、401、超时、响应解析失败。先修客户端，不能据此判断数据覆盖。
2. **数据未收录或用词不同**：成功搜索后零命中。现有全国公开快照不是全量部门规章/交易所自律规则库；证券法相关法律在库不代表《上市公司收购管理办法》、减持规则等已收录。也不能从采集源名称推断全部规范是否存在。核对正式名称和覆盖，必要时从官方来源补全，经审查后导入。
3. **时间效力主题未匹配**：`applicable` 的 topic 是文字筛选，不是语义问题理解。先看 `coverage.topics`；“公司对外担保”可分别查询 topic="担保" 和 topic="公司治理"，结合事实日期读取相关指引。两条指引不自动组成该问题的完整答案。`rules_loaded=12` 只说明有 12 条规则，零命中不说明没有过渡规定；不要为了命中把案例问题硬标成一条已审核规则。

MCP 业务错误的 `isError=true` 与 `structuredContent.error/message/status/details` 见 [CONTRACT.md](CONTRACT.md)。其中 `status=403` 是工具业务状态，HTTP 可以仍为 200。

`article.found=false` 表示条文未找到，即使同一次返回包含法规元数据也不能当成功读条。
`article_count` 是条目总数，不一定等于最大条号。`applicable.coverage.domains` 给出本库
实际标签，domain 按字面筛选；未知标签可能只命中 all 规则，注意对应 warning。
具体 domain 会并入标记为 all 的规则；`domain="all"` 只取 all 标签，要检索该日全部
领域的规则应不传 domain。
`applicable` 顶层 law 是定位元数据，历史条文仍须用 article 的 as_of 获取。

对于默认会对工具结果做头尾裁剪的客户端，过长 JSON 可能在裁剪后误拼不同文件的标题、
正文和来源。应保留完整结构化结果，或按条目分页读取；看到 Omitted/truncated 提示时，
先重新取单个文件核验，再判断数据是否有误。本项目 Docker/DSH 评测 profile 已关闭这类裁剪。
