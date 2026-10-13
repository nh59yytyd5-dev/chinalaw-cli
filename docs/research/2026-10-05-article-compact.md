# 读条简洁视图：实现与 Docker 验收

第一批 [用量优化方案](2026-10-05-mcp-optimization-plan.md) 已实现。HTTP 和 stdio
的 `chinalaw_article` 新增显式 `detail=compact`，默认 `full` 保持原返回。简洁视图
只省略重复 item 与 law 中完整的 revisions/work_versions 列表；正文、所选/当前
版本、出处、状态、缺失和错误诊断均保留。两个传输适配器共用 `article_views.py`，
原服务负责选版与授权。没有更改 CLI、REST、批量读条或检索行为。

```json
{"name":"chinalaw_article","arguments":{"law":"民法典","number":"143","detail":"compact"}}
```

响应中的 `view.omitted_fields` 标识实际省略的字段，`view.full.tool/arguments`
是恢复完整结果的真实可执行调用，保留原 law、number 和显式 as_of。字段省略不等于
没有历史。未知 detail 由 schema 验证拒绝；两种接口原有的诊断外层格式保持兼容。

## 实际结果

复用同一公开库快照和 Docker 镜像。容器 2 CPU / 2 GiB、非 root、只读根文件系统、
drop ALL capabilities，数据库在隔离工作目录复制，认证为一次性公开令牌。
本轮没有挂载 DeepSeek 密钥、调用模型或充值。

| 检查 | 结果 |
|---|---|
| 旧代码基线 | 提交 59dcfb3，193 次真实 MCP |
| 新代码默认视图 | 193 次，返回与同日重跑基线逐项完全一致 |
| 新代码 compact | 83 次读条，除明确省略的字段和新增 view 外全部一致 |
| 根据 view.full 执行恢复 | 83 次，全部恢复默认完整结果 |
| Docker MCP 总调用 | **552 次** |
| 83 次默认读条返回 | 795,545 UTF-8 字节 |
| 83 次简洁读条返回 | **302,047 字节，减少 62.03%** |
| 原 193 次流程中只将读条换为 compact | 2,008,637 → 1,515,139 字节，减少 **24.57%** |
| 模型 token / 费用 | 未测量，不能把字节降幅解释为费用降幅 |

简洁响应包含恢复入口和省略说明，因此实测降幅低于之前只删字段的离线投影（65.58%）。
193 次流程的大小比较不额外计入完整恢复调用；后者是验收步骤。真实用户如果经常读取
完整历史，会增加调用和输入，需在模型对照中另计，不能宣称全场景净节省 24.57%。

最初直接比较早先下午的结果，7 个搜索结果中的 freshness_days 从 1 变成 2；只涉及
随距离采集时间变化的天数。为避免掩盖回归，保留这次差异，并用冻结旧代码重新跑了
同一数据库的 193 次基线。最终比较未忽略任何返回字段，193 次新默认均完全一致。

## 回归验证

- 全量：**1100 passed、32 skipped、1739 subtests passed**。
- HTTP 真实 SDK 与 stdio：当前/历史条文、不同的所选/当前版本、完整恢复、文本与
  structuredContent 一致、缺条文、未知法规、非法日期、损坏历史快照、私域授权与拒绝。
- 视图处理不修改输入对象；未知字段与诊断保留；不同内容的 item 不会被误删。
- Ruff 全仓库及对照脚本通过；diff 空白检查通过。

## 证据与复算

脱敏逐条指标：[2026-10-05-article-compact-replay.json](2026-10-05-article-compact-replay.json)。
该文件含 83 条读条的前后字节数、恢复大小、输入哈希和内容一致标记，不含查询或正文。
原始数据根为 `var/ux-compact-20261005v1/`：公开库副本、冻结旧/新源码、三个 Docker
运行目录、实际 MCP 返回、由 view.full 生成的复原请求、初次差异、测试日志与分析脚本。
完整独立归档校验信息见 [本轮归档清单](2026-10-05-article-compact-archive.json)。

```sh
python3 scripts/ux_eval/compare_article_views.py \
  var/ux-compact-20261005v1 /tmp/chinalaw-compact-recomputed.json
```

脚本要求新的输出文件，并断言所有默认结果、参数、正文/元数据、错误标志和实际完整
恢复结果一致；只读取原始记录，不启动容器或模型。后续搜索 brief、批量读条和推理配置
对照仍按方案分批进行，本次只交付可选的单条 compact，线上默认尚未切换。
