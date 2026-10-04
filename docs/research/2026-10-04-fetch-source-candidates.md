# fetch 源补充调研（2026-10-04）

## 背景与结构性缺口

本次刑事规范补充工程（30 份两高文件入库）暴露的源覆盖问题：

1. **flk_npc（国家法律法规数据库）不覆盖司法指导性文件**——指导意见、
   纪要、规程、规定类"两高"规范性文件（认罪认罚指导意见、电诈意见、
   九民纪要、两规程等）全部不在 flk，这是本次工程的主要补库动因。
2. spp_gov_cn / court_main 适配器的**搜索是列表页刮取**，频道与页数
   有限（实测：量刑指导意见、黑恶势力 2018 指导意见、排非/庭前会议
   规程均搜不到，但部分文档其实在其站点上有全文）。
3. court_gongbao 新上 WAF（issue #25），机器直抓被指纹级拦截。

本调研逐一实测候选源的可达性与协议形态（2026-10-04 下午，本机网络），
给出接入优先级。**仅调研，不含代码改动。**

## 候选源实测

### P0 — SPP 全文搜索（guestweb）作 spp_gov_cn 搜索后端

- 端点：`https://www.spp.gov.cn/guestweb/s?siteCode=N000005434&searchWord=<词>`
- 实测：200，HTML 结果页（约 244KB），含全文检索命中（llyj/zdgz/xwfbh
  各频道文章级命中，远超列表刮取覆盖）。搜索词需 URL 编码；结果为
  HTML 需解析（无 JSON）。
- 增量：解决 spp 适配器"列表刮取搜不到"的主要缺口（如 2018 黑恶势力
  指导意见类老文档）；命中后仍走现有 fetch_detail 管道。
- 复杂度：低（新增搜索路径解析；详情抓取复用）。
- 注意：搜索页无需会话；结果排序与分页参数未深入验证。

### P0 — gov.cn 政策库搜索 API（系统化 gov 静态页路径）

- 端点：`GET https://sousuo.www.gov.cn/search-gov/data?t=zhengcelibrary&q=<词>&timetype=timeqb&sort=score&sortType=1&searchfield=title&p=1&n=5`
  （需 `Referer: https://sousuo.www.gov.cn/sousuo/search.shtml` 头）
- 实测：JSON 200；结果在 `searchVO.catMap.bumenfile.listVO`（title 含
  `<em>` 高亮、url、发布机构/年份 facet 在 extendresult.facetMap）。
  例：`q=恶势力` 命中 2 条（content_5427442 / content_5427511）。
- 增量：把 gov_xzfgk 的"GOV_CN_STATIC_PAGES 手工登记"升级为搜索驱动；
  覆盖国务院政策库全部部门文件（两高文件、公安部文件等，本批黑恶
  势力 2019 系列、暂予监外执行均在库）。
- 复杂度：低（JSON 协议干净；详情页仍走 gov_xzfgk 现有 UCAP-CONTENT
  抽取，注意 zhengceku/公报双路径已由 9f435ad 支持）。
- 用法建议：作为 gov_xzfgk 的第二搜索后端（moj 行政法规库之外）。

### P1 — 中国法院网 chinacourt.cn（注意域名已从 .org 迁到 .cn）

- 实测：`www.chinacourt.org` 证书过期并 301 至 `www.chinacourt.cn`；
  文章页 `https://www.chinacourt.cn/article/detail/2013/04/id/948052.shtml`
  200（该页即 2013 版量刑指导意见全文）；搜索 `/search.php?keyword=`
  301 跳转后可跟随。
- 内容：最高法直属新闻站，司法解释/司法文件全文常早于主站发布，
  且有大量主站没有的历史全文（2013 量刑指导意见等）。
- 复杂度：中（栏目/详情路径规整 detail/YYYY/MM/id/N.shtml；搜索为
  HTML；需确认内容区抽取规则与反爬）。
- 增量：最高法侧历史司法文件全文的重要补充。

### P1 — 司法部 moj.gov.cn（cookie 质询可过）

- 实测：`https://www.moj.gov.cn/pub/sfbgw/flfggz/index.html` 首访 302
  自跳（WAF cookie 质询），带 CookieJar 重试即 200（已被 netio
  e1ab035 的 CookieJar 改动覆盖此形态）。
- 内容：部令、律师/公证/司法鉴定/法考/社区矫正规范；行政法规库
  （xzfg.moj.gov.cn，已接入）之外的司法部自发文件。
- 复杂度：中（栏目结构需逐栏调研；cookie 质询已可过）。

### P2 — 中央纪委国家监委 ccdi.gov.cn 法规栏目

- 实测：`/fgk/index.html` 200 但正文为空壳（JS 渲染），内容形态待
  进一步调研（可能需要 JSON 数据接口或改抓具体法规页）。
- 说明：监察法规层面 flk 已有覆盖；监委规范性文件增量有限，
  优先级低。

### P2 — 人民法院报 rmfyb.chinacourt.com

- 实测：首页 200 但为 JS 挑战页（"Redirecting..."），正文需执行 JS。
- 内容：司法解释与司法文件的首发渠道之一（量刑指导意见 2021 官方
  全文目前仅公报与人民法院报系统可见）。
- 复杂度：高（JS 挑战 + 报纸版面结构），暂缓。

### 不可行/暂缓

- **公安部 mps.gov.cn**：521 持续不可达（2026-10-04 全天）；公安部
  文件暂由 gov.cn 政策库覆盖（facet 实测有"公安部"分类命中）。
- **chinacourt.org（旧域）**：证书过期，迁 .cn（见上）。

## 建议接入顺序

1. **SPP guestweb 搜索后端**（P0，直接消除本次"搜不到"主缺口）
2. **gov.cn 政策库搜索 API**（P0，把静态页登记升级为搜索驱动）
3. chinacourt.cn 适配器（P1，历史全文增量最大）
4. moj.gov.cn 栏目调研后接入（P1/P2）
5. 人民法院报 / ccdi 暂缓；公报文档继续走手工导入路径
   （imports-20261004/build_fafa2024_12.py 模式）直至 WAF 对策落地。

## 复现实测命令

```bash
# SPP 全文搜索
curl -sS "https://www.spp.gov.cn/guestweb/s?siteCode=N000005434&searchWord=%E9%87%8F%E5%88%91" -H "User-Agent: Mozilla/5.0"
# gov.cn 政策库 API
curl -sS "https://sousuo.www.gov.cn/search-gov/data?t=zhengcelibrary&q=%E6%81%B6%E5%8A%BF%E5%8A%9B&timetype=timeqb&sort=score&sortType=1&searchfield=title&p=1&n=5" \
  -H "User-Agent: Mozilla/5.0" -H "Referer: https://sousuo.www.gov.cn/sousuo/search.shtml"
# chinacourt.cn 文章页
curl -sSL "http://www.chinacourt.cn/article/detail/2013/04/id/948052.shtml" -H "User-Agent: Mozilla/5.0"
```
