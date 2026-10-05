# MCP 用量优化方案

基于 [已归档的模型用量](2026-10-05-mcp-token-evidence/README.md) 和最终版的 193 次
固定重放。这里只提出设计与离线测算，没有修改工具行为或再次消费 API。

后续实施记录：[第一批读条 compact 已完成 Docker 验收](2026-10-05-article-compact.md)。
下文保留实施前的投影与计划，实际返回体积及实现边界以该验收记录为准。

## 第一优先：读条结果的简洁视图

现有单条响应同时返回相同的 `article` 和 `item`，`law` 内还携带 `revisions`、
`work_versions`、`current_revision`、`selected_revision`。最后两者可能不同，不能
无条件合并。在 MCP 层增加显式 `detail=compact|full`，先保留现有默认以便对照验收：

- compact 正文只返回一次，消除相同对象的兼容别名。
- 保留法规身份、完整条文、所选版本、当前版本、事实日期/检索日期、状态、出处和错误
  诊断；没有正文时继续明确标记缺失，不把缺失改成空字符串。
- 完整历史列表通过 `detail=full` 取得。日常读一条不必每次附送整套版本列表，历史研究
  仍有清晰的入口。不能让模型误以为 compact 中没有列出的版本就不存在。
- 将现有返回作为兼容视图，不直接删除底层 service 或 REST 的字段。HTTP 与 stdio 的
  同一视图应保持一致；最终默认值需待质量和客户端兼容性验证后确定。

固定重放中的 83 次读条都存在完全相等的 `article`/`item`。离线投影结果：

| 投影 | 减少的 UTF-8 字节 | 83 次读条体积降幅 | 全部 193 次响应体积降幅 |
|---|---:|---:|---:|
| 只去掉相等且非空的 item | 68,935 | 8.67% | 3.43% |
| 再把 revisions/work_versions 列表移出简洁视图 | 521,728 | 65.58% | 25.97% |

第二行包含第一行，不应叠加。投影保留 current_revision 和 selected_revision；只是
计算输出形状，没有验证模型会不会为历史列表额外调用，因此不是已经实现的费用降幅。
原始结果、源码与客户端均未修改。逐条字节差和输入 hash 已保存到
[测算 JSON](2026-10-05-mcp-optimization-estimate.json)。

## 第二优先：搜索先定位，正文按需读取

237 次 search 占模型可见返回字节的 48.89%，最大一次 98,463 字节。设计显式的
`view=brief|full`：brief 保留结果 ID、标题、条号、版本/来源、精确或模糊标记，以及
有明确“摘录”标识的命中片段；需要引用时再读完整条文。

保持同一批候选和排序，不能为了省体积悄悄减少召回范围、改变 as_of 或过滤条件。
较大列表按完整对象分页，提供稳定续页位置；若单条正文也超长，应提供可核对版本和
连续段落位置的续读方案，不能头尾拼接。短条文可能直接完整返回更省交互，需在同一批
任务上比较 brief 导致的额外读条次数。此方案尚没有可信的净 token 节省比例。

## 第三优先：批量读条，共享同一版本的元数据

底层已有批量接口，HTTP MCP 当前只提供单条。新增有数量/大小限制的批量读取入口，
同一法规和同一所选版本的元数据只返回一次，条文逐项返回命中或错误。必须与单条接口
复用相同的跨版本选择逻辑，不能简单暴露语义不同的旧批量路径。

当前已有很多同轮并行调用，批量不一定减少模型生成轮数；主要待验证收益是减少重复
元数据和协议调用。不要让无状态 MCP 服务基于“客户端应该已经看过”而静默省略正文。

## 第四优先：客户端推理策略和上下文管理

推理 token 占输出的 70.31%。对“确定法规＋条号”的机械读取，可对照 high 与 off
配置；复杂研究仍按实际任务需求选择。固定任务和模型版本后，比较引用准确性、任务
完成率、额外调用数、用量与等待时间，不把较短回答直接认定为更好。

上下文压缩应保存已核验的法规 ID、所选版本、来源和未完成步骤，引用时能够重新取回
原文。不能用模型摘要替代最终核验所需的原文。精简工具定义时也保留公开/私域、范围、
日期和分页等关键说明；它们带来的误用减少可能比删掉少量提示更有价值。

当前输入缓存命中比例已达 78.89%；服务端查询缓存主要改善时延，不会自动减少模型
看到的 token。网络压缩也不等于模型输入压缩，二者不列为本轮节省 token 的主要方案。

## 实施与验收顺序

先实现兼容的读条 compact 视图并重放固定样本，再做搜索 brief 和批量读条，各自单独
对照，最后调整模型推理策略。每次保存原始/候选输入、输出、脚本与 hash；不得覆盖
本轮历史数据。现有 DeepSeek 余额已耗尽，现阶段可继续离线实现和确定性验证，不能
据此宣称已完成新的真实模型用量对照。

验收同时看：

1. 完整正文、法规身份、所选版本与来源一致；筛选条件、权限和缺失诊断不变。
2. 旧客户端仍能使用 full；简洁视图的全文/历史读取入口实际可用。
3. 记录每个视图的响应字节、额外工具调用和模型整段工作流用量。接受节省的前提是任务
   完成与引用核验不变差，而非只看到单个回复变小。

离线投影可用以下代码复算；它不会调用模型或修改原始资料：

```python
import copy, json
from pathlib import Path

size = lambda value: len(json.dumps(value, ensure_ascii=False, indent=2).encode())
rows = [json.loads(line) for line in Path(
    'var/ux-deepseek-20261005/replay-final/replay.jsonl'
).read_text().splitlines()]
saved_alias = saved_compact = 0
for row in rows:
    if row['tool'] != 'article':
        continue
    original = json.loads(''.join(block['text'] for block in row['result']['content']
                                  if block['type'] == 'text'))
    candidate = copy.deepcopy(original)
    if candidate.get('article') is not None and candidate.get('item') == candidate['article']:
        candidate.pop('item')
    saved_alias += size(original) - size(candidate)
    candidate['law'].pop('revisions', None)
    candidate['law'].pop('work_versions', None)
    saved_compact += size(original) - size(candidate)
assert (saved_alias, saved_compact) == (68935, 521728)
```
