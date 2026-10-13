# MCP 体验改进与简洁视图：线上部署验收

2026-10-05 北京时间 **23:41:39** 完成 Oracle 线上部署，公网地址
[law.newcombmath.com](https://law.newcombmath.com)。应用代码为
`c4fc8c981c6f716bf6f38564ce2c6b9cbbc69e54`，来自 `codex/real-user-ux-dsh`；本次未合并
main、发布 tag 或变更版本号，包仍为 0.7.2、数据库 schema 仍为 17。按提交和 wheel
校验值辨认本次部署。

上线范围包含此前已经验证的文号/条号检索、范围与错误诊断、全文续页和领域提示，以及：

- `chinalaw_article(..., detail="compact")`：完整条文只返回一次，完整修订列表按需恢复。
- `chinalaw_search(..., view="brief")`：保留候选、来源和版本标记，返回明确标记的连续
  原文摘录，以及全文读取参数和正文校验值。

两项新视图均显式启用，默认 full 保持各自原有完整视图。客户端重新连接 MCP、刷新
tools/list 后可看到新参数。用法见 [客户端说明](../MCP_CLIENT.md)。

## 发布与数据检查

- 从干净的 c4fc8c9 源码构建 wheel；SHA-256：
  `44df6c0f255a873bac6acf9990997840ddddc39d942637471ca0a26930e8ff52`。
- 线上原有 85 个 Python 文件中 77 个未变、8 个更新，新增 3 个。安装后 **98 个应用包
  文件**与 wheel 逐项哈希一致。复用已安装依赖，未升级依赖；pip check 通过。
- 停服前检查无 queued/running 导入任务。安装前备份并验证旧 venv、SQLite 一致性副本、
  认证与查询日志状态，以及 systemd/nginx 配置；当时无附件目录。
- 安装与显式 init 后，**22 张非 FTS 业务表逐行哈希一致**，认证库逐表哈希一致。
  SQLite integrity_check、foreign_key_check 通过，schema=17，没有结构迁移。
- 仍为 **2550 法规、94353 条文、2586 修订、0 私域来源/条款**。没有导入测试数据。
- 未修改 nginx 配置、查询限额或密码/令牌。chinalaw、nginx 均 active；部署后检查
  warning 及以上的服务 journal 无记录。

## 公网验证

使用本机原有凭据经过真实 HTTPS 和官方 MCP SDK 验证，密钥未写入命令行或公开报告。
升级前 6 次工具基线，升级后 17 次工具调用均通过：

- 六个工具可列出并使用；article 的 detail、search 的 view 枚举可从线上 schema 发现。
- compact 读条与 view.full 实际恢复的完整响应一致；普通长正文搜索的 2 个截断摘录、
  历史条号命中的 1 个完整条文均按 read 参数读回，核对法规 ID、条号、正文及 SHA-256。
- 缺失条号仍明确 found=false；未解析法规范围仍是零命中，不回退全库。
- 健康检查和介绍页为 200；控制台路径正常重定向；匿名资料 API 返回 401。
- 升级前后所选 resolve/search/article/list 默认响应完全一致。document 仅增加
  returned/next_offset；applicable 增加 coverage.domains 和 law_metadata_is_reference
  提示，原规则与结果不变。这些是此前体验修复的预期增量，未作为异常忽略。

离线验收见 [compact](2026-10-05-article-compact.md) 与
[brief](2026-10-05-search-brief.md)：全量 1117 passed、32 skipped、1739 subtests passed，
以及 Docker 中的 552/728 次查询。公网本轮不作并发压力测试，也没有调用付费模型。

## 首次失败与回退记录

第一次安装因发布目录的 0750 模式被 umask 收紧到 0700，服务账号无法读取 wheel。
安装未成功；自动回退恢复旧运行环境并通过健康检查，未覆盖数据库或认证状态。
该次停服/回退区间约 11.5 秒，原日志、备份和失败运行目录完整保留。

第二次显式设定目录 0750，并在停服前验证服务账号可读安装包，随后成功完成部署。
成功切换的停服区间 **13.37 秒**；不是把两次操作总停服量声称为 13.37 秒。

## 备份、回滚与证据

成功发布目录：`/srv/chinalaw/releases/20261005-mcp-views-c4fc8c9-v2/`。
该目录保存 wheel、manifest、部署脚本和 deployment.json；`backup/` 权限 root-only
0700，包含 `venv.tar.gz`、SQLite backup API 生成的 library.db、server-state，以及
服务/代理配置和数据校验值。首轮目录 `20261005-mcp-views-c4fc8c9/` 另行保留。

本次没有迁移 schema。若要回退程序，先停服务、另存当前现场，恢复旧 venv，校正
chinalaw 所有权和 venv/bin 的 SELinux 标签，再启动并验证。数据库和认证状态应保留
上线后的新写入；只有确认需要数据恢复并核对新增写入后，才使用对应备份，不能盲目覆盖。

本地完整发布资料：`var/deploy-mcp-views-20261005v1/`，含冻结源码、构建包、两次部署
脚本和日志、升级前后真实响应、默认差异与校验摘要。脱敏部署指标及独立私有归档校验
值见 [部署证据](2026-10-05-mcp-views-deployment.json)。服务器上的数据/认证备份仍留在
服务器受限目录，未发布或混入软件仓库。
