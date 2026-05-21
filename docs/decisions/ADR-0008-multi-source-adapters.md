# ADR-0008 — 多源 adapter 与最高法 / 最高检 / 证监会 / 行政法规库接入

- **状态**：Accepted
- **决策日期**：2026-05-02
- **决策者**：项目维护者
- **触发**：[issue #14]、[issue #16]、[2026-05 source coverage survey][survey]

## 背景

[ADR-0006] 锁定 `flk_npc` 作为 v0.2 阶段的唯一真实数据源，并明确"多源扩展时
需要新 ADR：本 ADR 仅锁定 flk_npc，court.gov.cn / gov.cn 需要单独决策"。

[2026-05 调研][survey] 实证 flk.npc.gov.cn 在如下文件类别上**完全不收录**：

- 司法会议纪要（如九民纪要、八民纪要、商事审判工作会议纪要）
- 最高法批复 / 通知 / 复函（除少数以"司法解释"形态收录）
- 最高法 / 最高检指导性案例
- 量刑指导意见
- 两高一部联合刑事意见
- 部门规章（CSRC / CAC / MOJ）
- 国务院政策文件（非行政法规）

这些文件是民法 / 商事 / 刑事实务高频依赖的"准 - 法律渊源"，仅能走
`norm ingest` 私域路径，违反"公开规范应有标准 fetch pipeline"的协议精神
（PROJECT_CHARTER.md），且导致每个用户都要自己 ingest 同一份九民纪要。

## 决策

### 1. 接入公开源

接入调研已实证可爬（静态全文 HTML、无 JS 反爬）的公开源：

#### 1.1 `court_gongbao`（最高人民法院公报）

- 站点：<http://gongbao.court.gov.cn>
- 详情 URL 模式：`/Details/{32位hash}.html`
- 列表 URL：`/PeriodicalsDic.html`、`/order.html` 等
- 覆盖范围：**九民纪要、八民纪要、商事审判会议纪要、最高法批复 / 复函、
  司法解释、典型案例、工作报告**
- 技术形态：ASP.NET 站，静态 HTML，curl + 标准 UA 直读，title 干净，body 一段
  连续中文可直接 strip HTML 抽取
- 实测：详情页 20.7KB，无反爬

#### 1.2 `spp_gov_cn`（最高人民检察院）

- 站点：<https://www.spp.gov.cn>
- 详情 URL 模式：`/spp/{category}/{yyyymm}/t{yyyymmdd}_{seq}.shtml`
- 列表 URL：`/spp/jczdal/index.shtml`（指导案例）等
- 覆盖范围：**最高检指导性案例、最高检规范性文件、最高检与最高法 / 公安部
  联合发布的刑事司法解释和指导意见**
- 技术形态：Tengine 缓存，静态 .shtml，title 干净，HTML body 内嵌全文
- 实测：详情页 57KB，无反爬

#### 1.3 `court_main`（最高人民法院主站）

- 站点：<https://www.court.gov.cn>
- 搜索 URL：`/search.html?content=<query>`
- 详情 URL 模式：`/<channel>/xiangqing/<id>.html`，source id 写作
  `channel/xiangqing/id`
- 覆盖范围：**最高法主站发布但公报站未覆盖的司法政策、通知、新闻发布材料
  中可清洗出条文的文件**
- 技术形态：静态 HTML，详情正文容器为 `.txt_txt`，标题经常是"最高法发布
  《规范标题》"这类新闻稿标题，需要从书名号中抽取 canonical title
- 实测：`verify-source court_main --query ... --article 第一条` 可跑通；
  当前只消费搜索第一页和显式详情页，不做无界全站爬取

#### 1.4 `gov_xzfgk`（国家行政法规库）

- 入口：<https://www.gov.cn/zhengce/xzfgk/>
- 实际应用：<https://xzfg.moj.gov.cn/search2.html>
- 详情 URL 模式：`/front/law/detail?LawID=<id>`，source id 写作 `LawID`
- 覆盖范围：**国务院行政法规及其历史沿革版本提示**
- 技术形态：搜索结果和详情为服务端 HTML；正文容器为 `.law-chapter`，历史沿革
  记录在 `incident-record` 块中；清洗为 `level="admin_regulation"`。
- 状态语义：该源当前公开入口以现行有效行政法规为主，CLI 允许
  `--status current`，历史版本通过 `related_versions` 暴露，完整时间效力 schema
  另行演进。

#### 1.5 `csrc_gov_cn`（中国证监会官网）

- 站点：<https://www.csrc.gov.cn>
- 搜索 URL：`/guestweb4/s` 表单 POST
- 详情 URL 模式：`/csrc/c.../c.../content.shtml`，source id 写作
  `csrc/c.../c...`
- 覆盖范围：**证监会令、部门规章、证监会公开的证券期货监管规则**
- 技术形态：静态 HTML + 附件混合。旧版规章常在 `.content-body` 直接给全文；
  2025 年后部分证监会令详情页只给命令摘要，完整办法在 PDF 附件中，adapter
  会选择标题匹配的正文 PDF 并通过 `pdftotext -raw` 抽取后进入同一 cleaning。
- 状态语义：官网只暴露当前公开页，不提供 FLK `sxx` 四态。CLI 允许
  `--status current` 作为 agent 友好过滤，其它 status fail loud。
- 实测：`fetch "上市公司信息披露管理办法" --source csrc_gov_cn --article 第一条`
  可抓取 2025 年证监会令第 226 号并切分 67 条。

### 2. 暂缓接入

| 候选源 | 暂缓原因 | 重新评估触发条件 |
|---|---|---|
| `gov.cn/gongbao` | 国务院公报另属公报归档源，非 `gov_xzfgk` 行政法规库；需要单独建源和版本策略 | 用户出现"必须从国务院公报取行政法规原文"且行政法规库不够用的真实场景 |
| `cac.gov.cn` | jsl5 反爬（Set-Cookie `__jsluid_h`），`urllib` 客户端无法过 challenge | 真实数据合规审查需求出现 + 找到反爬绕过方案 |
| 北大法宝 / 威科 / 法信 | 商业数据库，与项目宪章 §"差异化边界"冲突 | 永不（除非用户自带订阅，作为本地 ingest 源） |

### 3. 多源 adapter 接入协议

#### 3.1 sources.py 改造为 registry

```python
ADAPTER_REGISTRY = {
    "flk_npc": flk_npc.default_adapter,
    "court_gongbao": court_gongbao.default_adapter,  # 新
    "court_main": court_main.default_adapter,        # 新
    "spp_gov_cn": spp_gov_cn.default_adapter,        # 新
    "gov_xzfgk": gov_xzfgk.default_adapter,          # 新
    "csrc_gov_cn": csrc_gov_cn.default_adapter,      # 新
}

def get_source_adapter(name: str):
    normalized = name.strip().lower().replace("-", "_")
    if normalized not in ADAPTER_REGISTRY:
        raise ValueError(f"unknown source: {name}")
    return ADAPTER_REGISTRY[normalized]
```

`if-elif` 链替换为 dict lookup，新源接入只需注册到 dict，CLI 自动可用。

#### 3.2 adapter 最小契约

每个 adapter 必须实现：

| 方法 | 必填 | 用途 |
|---|---|---|
| `probe() -> dict` | ✅ | 探测站点首页、识别 page_shape、记录 source_last_modified / etag |
| `source_hash(identifier) -> str` | 阶段性 | 内容指纹，启用 fetch 时必填 |
| `search_list(query, **kwargs) -> dict` | 阶段性 | 搜索接口，启用 fetch 时必填 |
| `fetch_detail(identifier) -> dict` | 阶段性 | 详情接口，启用 fetch 时必填 |

`court_gongbao` / `court_main` / `spp_gov_cn` / `gov_xzfgk` / `csrc_gov_cn`
已实装 `probe()` / `search_list()` / `fetch_detail()` / `build_law_payload()`，
并进入 `fetch --source ...` 与 `verify-source ...`。其中 `court_main` 为有界
实现：只读搜索第一页和显式详情页。

#### 3.3 节流与反爬

- 每个 adapter 独立节流参数（默认 500ms，可调）
- 新 adapter 沿用 `_throttle()` 与 WAF 检测模式
- 节流默认值从 ADR-0006 的 200ms 提到 500ms（实测 17 次连续 flk 搜索后偶发
  HTTP 307）

### 4. CLI 暴露

- `chinalaw probe court_gongbao` — 探测最高法公报站点
- `chinalaw probe court_main` — 探测最高法主站
- `chinalaw probe spp_gov_cn` — 探测最高检站点
- `chinalaw probe gov_xzfgk` — 探测国家行政法规库
- `chinalaw probe csrc_gov_cn` — 探测中国证监会官网
- `chinalaw fetch --source court_gongbao …` — 按名称搜索公报候选，或用
  `--prefer-id <detail_id>` 直接按详情页 id fetch
- `chinalaw fetch --source court_main …` — 按主站搜索候选，或用
  `--prefer-id <channel/xiangqing/id>` 直接按详情页 fetch
- `chinalaw fetch --source gov_xzfgk …` — 按行政法规库标题搜索候选，或用
  `--prefer-id <LawID>` 直接按详情页 fetch
- `chinalaw fetch --source csrc_gov_cn …` — 按名称搜索证监会规章候选，或用
  `--prefer-id <csrc/...>` 直接按详情页 id fetch

### 5. cleaning 写入边界

- `court_gongbao` 抽取的 HTML 正文转纯文本后复用
  `canonicalize(source_kind="markdown")`，避免为 HTML 源复制一套条文切分逻辑
- 写入 `laws.level` 时，根据"公报栏目 + 文件号"启发式映射：
  - 含"会议纪要" → `judicial_meeting_minutes`
  - 含"批复" / "复函" / "解释" → `judicial_interpretation`
  - `sfwj` 其它标题 → `judicial_policy`
  - 含"指导性案例" / "公报案例" 或 `al` 栏目 → `guiding_case`
- `spp_gov_cn` 类似启发式
- `gov_xzfgk` 抽取 `.law-chapter` HTML 后复用
  `canonicalize(source_kind="markdown")`；写入 `level="admin_regulation"`、
  `issuing_body="国务院"`，并把页面历史沿革作为 `related_versions` 返回。
- `csrc_gov_cn` 抽取 HTML 或正文 PDF 后复用
  `canonicalize(source_kind="markdown")`；写入 `level="departmental_rule"`、
  `issuing_body="中国证券监督管理委员会"`，文号从命令摘要 / 正文中提取。

LawLevel 的三个新枚举值（`judicial_meeting_minutes` / `judicial_policy` /
`guiding_case`）由 PR-1（research/source-coverage-survey-and-mapping-fix）提
供，本 ADR 假设其已 land。

## 后果

### 正面

- 九民纪要 / 最高法批复 / 最高检指导案例 / 行政法规库 / 证监会令进入标准 fetch
  pipeline，结束 issue #14 / #77 中"每个用户自己 ingest 同一份公开规范"的窘境
- LawLevel 三个新枚举值有真实写入路径，不再悬空（issue #16 闭环）
- adapter registry 之后接交易所 / 中证登 / 证券业协会 / cac / 国务院公报等源时无需再改 `if-elif`，只
  需注册 dict
- 节流默认值从 200ms 提到 500ms，降低 flk 反爬触发概率

### 负面

- 新增多个 adapter 的维护面（HTML 抽取规则随上游 DOM 变化可能失效）
- court_gongbao 是 ASP.NET 站，URL hash 不可预测（不像 flk 的 `bbbs`），
  必须先列表后详情，对增量同步策略影响待评估
- 单源同一份文件可能有多个版本（如九民纪要在公报有发布版、修订版），需要
  `revision` 模型支持

### 中性

- 与 ADR-0006 的"按需 fetch"原则保持一致；公报站搜索不是原生全文搜索，
  默认只做有界栏目页解析，旧文件可通过 `--prefer-id <detail_id>` 直接进入
  fetch/cleaning pipeline
- gov.cn / cac / 交易所 / 中证登 / 证券业协会暂缓不影响当前民商 / 刑事 /
  证券部门规章工作流，后续按真实场景分源接入

## 不在本 ADR 范围

- 公报站离线索引 / 全站标题索引 — 留给后续 PR，避免默认命令无界扫站
- 国务院公报 SPA 反爬 — 等真实需求
- jsl5 / 验证码反爬绕过 — 等真实需求
- 交易所 / 中证登 / 证券业协会自律规则 adapter — 另开 issue/ADR

## 相关

- 上一 ADR：[ADR-0006][]、[ADR-0007][]

[ADR-0006]: ./ADR-0006-fetch-command.md
[ADR-0007]: ./ADR-0007-time-effect-minimal.md
