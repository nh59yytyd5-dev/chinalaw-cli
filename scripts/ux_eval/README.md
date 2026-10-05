# Docker 中的真实 DeepSeek Harness 体验测试

不是模拟模型响应。容器运行 DSH headless 和真实 Streamable HTTP MCP 服务，数据库、
认证和写入状态均为独立副本。模型只获得 MCP/规划工具，不挂载宿主 HOME、Docker socket
或生产令牌。关闭额外遥测、结果头尾拼接与工具结果裁剪，避免破坏 JSON 对象和来源对应。

## 准备

- 安装 Docker 引擎；示例 context 为独立 Colima profile `colima-chinalaw-eval`。
- 从仓库根目录构建：

```sh
docker build -t chinalaw-dsh-eval:20261005-aligned -f scripts/ux_eval/Dockerfile .
```

- 在 Git 忽略且权限为 0700 的输入目录放入 `library.db`（SQLite backup 一致性副本）、
  `history-anonymized.json`（重放时使用）和 `deepseek-secret.yaml`。
  secret 实际使用 JSON 这一 YAML 子集，只包含 `DEEPSEEK_API_KEY`；权限必须为 0600。
  从已授权的本机凭据存储程序化提取，不打印密钥或写入镜像。
- 脱敏历史数组的每项需要 `id`、`tool`（如 search）和 `params`；不放入姓名、认证值。
- 使用前检查库的公开/私域边界。本轮公开快照的 norm_sources/norm_clauses 均为 0。

## 运行

```sh
python3 scripts/ux_eval/run.py balance --inputs /absolute/private-inputs
python3 scripts/ux_eval/run.py replay --inputs /absolute/private-inputs \
  --output /absolute/private-results/replay
python3 scripts/ux_eval/run.py run --inputs /absolute/private-inputs \
  --output /absolute/private-results/one-task --prompt /absolute/prompt.txt
```

`--source` 可挂载冻结的源代码目录，对比时固定数据库与 prompt；不同输出目录不得复用。
容器为 2 CPU/2 GiB，单任务默认 900 秒，外层再给 120 秒启动/清理余量。引擎未就绪时
明确失败，不降级到宿主机执行。`--context`、`--image`、`--model` 可显式选择。

批次为人工确定的有限任务队列：

```json
[{"id":"case-01","source":"/absolute/frozen-src","prompt":"/absolute/prompt.txt"}]
```

```sh
python3 scripts/ux_eval/batch.py /absolute/plan.json --inputs /absolute/private-inputs
node scripts/ux_eval/summarize.cjs /absolute/private-inputs
```

队列每轮检查真实余额，失败立即停下，不把网络或配置失败认定为余额耗尽。
只有用户明确授权才持续消费 API。账户结算有延迟，单轮前后余额不可直接当该轮费用。
一次性消费任务应持续核对余额与已验证问题，优先完成修复后的验收。

## 输出与验收

- `run.json`：模型、时间、退出状态与余额快照。
- `profile-facts.json`：实际 Harness/插件版本、输出完整性配置。
- `server-state/queries.db`：该轮真实 MCP 查询摘要；只有隔离凭据。
- `dsh/sessions/`：完整 DSH 轨迹。zstd 文件有多个拼接帧，不能只解第一帧。
- `answer.txt`：模型最终报告；它是待核查意见，不是项目已验证结论。
- `summary.json`：工具次数、模型用量与完成状态，不输出推理文本或凭据。

对模型提出的故障，先查原始服务响应和数据库，再查模型实际看到的内容。不要依据被
拼接、裁剪或仅有来源 URL 的输出直接更改法律资料。统计日志时区分零命中、权限错误、
环境失败与任务未完成；命中数增加不等于法律结论正确。

DSH rc.8 的 HMR 依赖范围会拉到已删除 registerConfig 的 1.0.19，本 Dockerfile 固定
与本机安装一致的 HMR 1.0.16 / loader 1.0.2，启动时提供 --expose-internals。
这是启动兼容性配置，未改模型驱动或伪造工具调用。

短回归可以显式使用 `--max-tokens 1024 --effort off`；正常任务默认 16384 / high。
直接运行和批次运行都会在余额不可用时停止，不启动新的模型容器。余额 API 有结算延迟，
因此大请求被拒绝时仍可能暂时显示正余额；以实际额度错误及随后确认的账户终态为准。

本轮收尾已停止独立 Colima VM。复用时先运行
`colima start --profile chinalaw-eval --cpu 2 --memory 4`，再使用对应 Docker context；
临时 API-key 副本已清理，需要从仍保留的原始授权凭据重新准备，不能依赖旧结果目录留密钥。

## 离线统计与长期保存

先保留原始实验，另建目录导出指标；两项操作均不调用 Docker、模型或余额 API：

```sh
python3 scripts/ux_eval/archive_evidence.py \
  --destination /absolute/private-evidence/dated-snapshot-v1 /absolute/private-results
node scripts/ux_eval/token_analysis.cjs /absolute/private-results /absolute/new-metrics-dir
node --test scripts/ux_eval/token_analysis.test.cjs
.venv/bin/pytest -q tests/test_ux_evidence.py tests/test_ux_eval.py
```

归档前暂停写入源目录的进程。归档工具拒绝复用目标目录，权限 0700/0600，验证归档中
每个文件的哈希和前后源目录清单；保留符号链接但不跟随。内部清单和完整轨迹属于私有
资料，不能连同公开报告一起提交 Git。归档仍需用户自己的异地备份策略。

`token_analysis.cjs` 需要 Node 22.15+（本轮 22.23.2）及 Python 3，仅只读原始输入。
它按完整会话解码多帧 zstd，将上一组 MCP 返回与下一模型步骤关联，导出逐轮、逐步骤、
逐调用、工具定义、服务端日志、匿名历史和四轮重放指标。只导出允许的字段，不导出
提示词、姓名、参数正文、模型推理或返回正文。输出目录必须是不存在的新目录。

模型 usage、缓存输入、返回字节、服务时延是不同指标；一个模型步骤可对应多个工具，
其用量不能全算给其中一个工具。缺失 usage 保留未知，推理 token 是输出的子集。
早期 Harness 裁剪与后续完整返回必须单独标明，不能当作同条件成本对照。

本轮 [用量数据与说明](../../docs/research/2026-10-05-mcp-token-evidence/README.md) 和
[历次证据索引](../../docs/research/README.md) 提供已有统计、归档位置与校验清单。

单条 compact 的确定性验收使用 `compare_article_views.py`：输入原始基线、默认/简洁
配对轨迹、按 `view.full` 实际请求的完整恢复轨迹，严格比较结果与错误标志，另存逐次
脱敏指标。脚本参数与数据布局见文件说明；[本轮结果](../../docs/research/2026-10-05-article-compact.md)
包含实际 552 次 Docker MCP 调用、字节差和复算命令。
