# CPU 优化部署验收

- PR #32 全部 CI 通过后合并至 main，部署提交 `2e2d26b90ebc088fe1c9d2dfa2ce843cf4decdfd`。
- 北京时间 2026-10-04 17:23 完成 Oracle 线上升级；停服约 18 秒，schema 16 → 17。
- 程序包版本仍为 0.7.2，本次按上述提交部署，未发布新版本号或 tag。
- wheel SHA-256：`5189e2d8bc05eb5bf78a2667c0c13709ef0162919b4c0d54b40a8cb88aa170c3`；安装后核对 service/schema/db/cleaning 文件哈希与构建包一致。
- 2550 部法规、94353 条条文、2586 份修订、96 条法规关系及私域规范/条款表在迁移前后逐行哈希一致；SQLite 完整性、外键与新索引检查通过。
- 现有令牌下三组公网 REST 搜索与升级前哈希一致；MCP resolve/search/article/list/document/applicable 六个工具通过；匿名搜索返回 401，介绍页与控制台入口返回 200。
- 服务 active/running；检查部署后 warning 及以上 journal 无记录。未修改 nginx、限流参数、密码、令牌或静态站点。

## 备份与回滚

发布目录 `/srv/chinalaw/releases/20261004-cpu-2e2d26b/` 保存安装包、manifest、部署脚本和 deployment.json。
其中 `backup/` 为 root-only（0700），包含升级前 SQLite、完整 venv、认证状态及 systemd/nginx 配置。
当时不存在 `library.db.assets` 目录，无附件文件需要备份。init 另生成了升级前 SQLite 快照。
`backup/library-v17-after.zip` 为升级后通过原生备份功能生成的面板备份，约 115 MB，可用于 v17 恢复。

需要回退时先停服务，保留失败现场，再配套恢复旧 venv、数据库和认证状态，恢复 chinalaw 所有权与 SELinux 标签后启动。不能只降级程序或手改 schema 版本号。
