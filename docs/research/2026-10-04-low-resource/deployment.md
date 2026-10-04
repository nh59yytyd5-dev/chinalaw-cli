# 小配置优化上线记录

- PR #33 的 16 项 CI 全部通过后合并，main 提交 832310511a15e57c64af9cbd02c9fc21307788cf；代码提交 085795a31663f7e6fa4c73df1028b3045f170092。
- 北京时间 2026-10-04 23:32:23 完成线上安装，停服 13.34 秒；schema 仍为 17，包版本仍为 0.7.2，未发布新 tag。
- wheel SHA-256：52e8bbe57ca02bac78d9bc5c9b03fb73f6aca9a8c1d070a1c9df15d2764f2068。85 个 Python 文件逐项核对安装包与线上文件，安装包与合并后的源码也一致。
- 升级前后 2550 法规、94353 条文、2586 修订、96 关系及私域表逐行哈希一致；SQLite 完整性、外键和依赖检查通过。
- 公网 3 组 REST 搜索与 MCP resolve/search/article/list/document/applicable 六工具逐项哈希一致，仅排除自然变化的 freshness_days。
- 应用采用默认执行 4、等待 16；nginx 全局查询入口由 8 调至 16，每令牌 5r/s、burst20 保持。nginx -t 通过并 reload。
- 公网同时发起 16 个公开只读令牌的查询，16/16 成功，最长 349.33ms；这是短时上线冒烟测试，不能当持续性能基准。临时令牌全部撤销。
- chinalaw 和 nginx 均 active，部署后 warning 以上 journal 无记录。长期运行与维护负载不包含在本次压测承诺内。

## 备份与回退

发布目录 /srv/chinalaw/releases/20261004-low-resource-085795a/ 保存 wheel、manifest、部署脚本和 deployment.json。
root-only backup/ 含旧 venv、server-state、SQLite backup API 生成的 library.db、服务和 nginx 配置。
本轮没有结构迁移。紧急回退先停服务并保留现场，再恢复旧运行环境，恢复权限与 SELinux 标签后启动；
如需恢复库或认证状态，必须先评估上线后的新写入，不可盲目覆盖。入口限制可单独恢复
backup/nginx-before-concurrency16.conf 至原 snippet，nginx -t 后 reload。

隔离压测副本在验收后清理，原始性能结果保存于本目录。线上库与完整回退备份保留。
