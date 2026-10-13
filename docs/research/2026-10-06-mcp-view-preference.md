# 将简洁视图的优先策略直接交付给 MCP 客户端

2026-10-06 北京时间 01:26:33 已部署代码 `e20af6c8e5f0d435b916f86c2e1c0e141d3023e7`。
优先策略写在 HTTP 和 stdio 的工具描述开头，由 tools/list 交付，不要求客户安装或
同步 chinalaw skill：

- 常规读条显式优先 `detail=compact`。它已经包含完整条文、来源和所选版本，历史日期
  也支持 as_of；需要完整修订/版本列表或兼容字段时才使用 full。
- 搜索定位显式优先 `view=brief`。truncated=false 时条文已完整，不为取得相同正文
  重复调用；只对需要的截断条文执行 read，需要同时阅读多个命中的全文时用 full。
- 缺省仍为 full，以兼容旧调用。说明明确区分兼容默认与推荐策略；服务没有强制改写
  客户端参数。客户需刷新工具列表；实际选择仍由其 agent/客户端决定。

本次只改变两份 Python 文件中的工具说明及客户端文档。去除 docstring 后，HTTP
模块的执行 AST 与前一提交完全相同。55 项既有 MCP/授权/诊断测试、14 个子测试及
Ruff 通过；stdio 工具描述集合 4373 字符，未超过 6000 字符预算。

公网通过真实 MCP 握手和 tools/list 确认推荐文字已出现，六个工具的输入 schema 与
上线前完全一致；健康检查、匿名拒绝及既有凭据验证通过。核验只读取元数据，没有
article/search 工具调用，也没有付费模型调用，不计入 agent 实际采用率。

按上一轮流程备份后部署，成功切换停服 13.06 秒；98 个应用文件与 wheel 一致，
22 张业务表及认证状态哈希一致，schema 17、0.7.2 版本号、2550/94353/2586 数据量
不变。服务与 nginx active，部署后 warning 以上日志无记录。

回滚目录：`/srv/chinalaw/releases/20261006-mcp-preference-e20af6c/backup/`（root-only）。
本轮无数据迁移；回退程序时保留上线后的数据库/认证写入。前一轮回滚说明见
[部署记录](2026-10-05-mcp-views-deployment.md)。

本地资料 `var/deploy-mcp-preference-20261006v1/` 保留冻结源码、wheel、部署脚本、日志、
前后 tools/list 及测试记录。[证据清单](2026-10-06-mcp-view-preference.json) 记录独立
私有归档校验值。策略已上线不等于已有采用率或费用收益数据，后续应按真实调用判断。
