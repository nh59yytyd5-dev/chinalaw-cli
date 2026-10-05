# 研究、测试与证据索引

每次结论应能追溯到输入、版本、环境和原始结果。这里汇集截至 2026-10-05 当前工作区
仍存在的测试资料；已丢失的临时文件、未记录的模型用量或其他机器上的资料不在覆盖范围。
失败运行和已排除的模型误报也保留，不能只保留支持结论的样本。

## 已有研究与数据

| 研究 / 日期 | 报告及仓库数据 | 能回答的问题与限制 |
|---|---|---|
| 真实使用与 DeepSeek / 10-05 | [体验与修复](2026-10-05-real-user-ux.md)、[模型用量与逐次数据](2026-10-05-mcp-token-evidence/README.md) | 两组用户的 193 次历史调用；4×193 次固定重放；39 次模型运行、561 次 MCP；仅模型试验有 provider usage，不能推算历史真实会话费用 |
| 低资源 REST/MCP / 10-04 | [报告](2026-10-04-low-resource/README.md)、[结果目录](2026-10-04-low-resource)、[部署](2026-10-04-low-resource/deployment.md) | 1 CPU/256 MiB 查询层与 2 CPU/2 GiB 完整 API、16 客户端；吞吐和等待时间，不是模型 token 或用户满意度 |
| 搜索 CPU 与并发 / 10-04 | [研究](2026-10-04-remote-concurrency.md)、[部署](2026-10-04-cpu-deployment.md)、[基线](2026-10-04-search-before.json)、[候选](2026-10-04-search-after.json)、[批量前](2026-10-04-search-batch-before.json)/[后](2026-10-04-search-batch-after.json)、[CPU](2026-10-04-search-cpu-after.json)/[复测](2026-10-04-search-cpu-repeat.json) | 查询链路、索引及资源开销；进程/网络/负载口径按各报告，不能与本地 Docker 时延直接排名 |
| 来源候选 / 10-04 | [研究](2026-10-04-fetch-source-candidates.md) | 覆盖与抓取入口调查；不等于资料已入库并通过核验 |
| MCP 客户端与诊断 / 09-30—10-02 | [诊断发布](2026-09-30-mcp-diagnostics-release.md)、[客户端发布](2026-10-02-mcp-client-release.md) | 接入、错误诊断与发布验收；原始发布目录私有归档 |
| 近似检索 / 09-29 | [验证报告](2026-09-29-minimal-fuzzy-validation.md)、[step5 数据](step5-validation)、[query 集](step5-validation/queries.json) | 固定 query、不同规模库的召回/排序与压力测试；不能替代具体问题的法律正确性评价 |
| 全国入库 / 09-29 | [质量报告](2026-09-29-national-import-quality.md)、[审计](2026-09-29-release-data-audit.md)、[原始指标](national-import-20260929)、[发布验收](2026-09-29-v070-deployment.md) | ID 保留、合并、状态与引用完整性；数据快照不同于 10-05 库，跨日命中变化不可全算算法收益 |
| MCP 研究工作流 / 09-29 | [报告](2026-09-29-mcp-research-workflow.md)、[cases](mcp-research-20260929/cases.json)、[trace](mcp-research-20260929/trace.jsonl) | 直接核验、旧号、辅助检索的案例轨迹；没有 provider usage，不能追补精确模型费用 |
| 早期设计 / 08-22—09-25 | [私域结构](2026-08-22-private-norm-structure-survey.md)、[近似检索设计](2026-09-23-fuzzy-search-design.md)、[规模检索](2026-09-24-fulltext-search-at-scale.md)、[导入](2026-09-25-flk-full-import.md)、[修订计划](2026-09-25-revised-plan.md) | 设计和调查背景，不能把计划里的指标当成已经完成的测试 |

本轮新增索引前已跟踪的 48 个研究文件均保留，原内容的 SHA-256 见
[evidence-catalog-20261005.json](evidence-catalog-20261005.json)。报告更新前的原始版本也
存在 `research.tar.gz`，其仓库基准为 `8ea707d`。

## 独立私有归档

本机归档根为 `~/chinalaw-evidence/`，目录权限 0700、文件 0600。两个批次：
`20261005-tests-v1/` 和 `20261005-web-tests-v1/`。共 10 个归档、29,129 个常规文件，
源文件合计 **2,418,516,916 字节**，压缩后 **1,088,384,977 字节**。这是同机独立副本，
并非异地备份；原工作区文件没有删除。归档和内部逐文件清单不进入 Git。

| 数据集 | 保留范围 |
|---|---|
| `ux-deepseek-20261005` | 完整会话、历史原始日志及姓名映射、匿名重放、模型结果、失败日志、余额、公开数据库、冻结代码和测试日志 |
| `step5-validation` | 检索验证的原始库及实验产物 |
| `national-import` | 原始入库评测资料 |
| `mcp-research` | 早期研究运行产物 |
| `release-0.7.0` / `release-0.7.1` / `release-0.7.2` | 本地发布与验收目录完整副本，可能含认证状态，不公开 |
| `admin-preview20260913v1` | 管理界面预览资料 |
| `research` | 本轮开始时的 48 个研究报告及已跟踪结果 |
| `test-results` | 现存浏览器测试截图、便携库和状态文件 |

归档工具先记录源文件清单与内容哈希，打包后逐成员读回校验，再复核源目录未变化。
每包包含 `MANIFEST.json` 和 `data/` 下的文件；符号链接按链接保留，不追读链接外的文件。
公开目录只保存数据集级大小、数量、归档 SHA-256 和校验状态，内部文件路径和正文保密。

校验归档整体可对照公开 catalog 运行 `shasum -a 256`；逐文件校验无需解压：

```sh
python3 - <<'PY'
import importlib.util, json, tarfile
from pathlib import Path
spec = importlib.util.spec_from_file_location('archive', 'scripts/ux_eval/archive_evidence.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
path = Path.home() / 'chinalaw-evidence/20261005-tests-v1/ux-deepseek-20261005.tar.gz'
with tarfile.open(path) as archive:
    manifest = json.load(archive.extractfile('MANIFEST.json'))
module.verify_archive(path, manifest)
print('verified')
PY
```

需要复算时，在新的权限 0700 私有目录解压已校验归档，以其中 `data/` 作为
`token_analysis.cjs` 的输入根；不要覆盖生产数据库或现有原始目录。

## 后续沉淀约定

- 每次实验使用新的带日期目录；记录原始输入 hash、数据快照、代码/镜像版本、模型和
  Harness 配置、工具定义、退出状态、完整日志。生成汇总时保留输入和脚本校验值。
- 结论同时链接报告、可机器读取的脱敏指标与私有原始证据定位。公开前检查姓名、凭据、
  用户原始文本和模型推理；公开结果采用字段白名单，不能依靠简单的字符串替换脱敏。
- 原始产物冻结后独立归档并校验；新实验或新分析版本另存，失败也归档。已有数据不因
  模型意见被否定而删除。新指标缺失时写未知，不用 0 补齐。
- 对比时固定测试条件并保留差异；命中率、法律核验结果、任务完成、服务时延、模型
  token 与费用分开报告。没有数据支撑的判断明确列为下一轮待验证事项。

工具说明见 [ux_eval README](../../scripts/ux_eval/README.md)。本轮仅补充证据、统计脚本
和报告；没有改动生产返回结构，也没有再次消费 DeepSeek 余额。
