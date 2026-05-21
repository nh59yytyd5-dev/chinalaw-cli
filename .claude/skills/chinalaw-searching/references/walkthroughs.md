# chinalaw-searching 实战 walkthrough

4 个真实工作流场景，展示 6 大检索方法在实战中的组合用法。

---

## 场景 A：公司法某条，版本不明（2018 vs 2023）

**用户提问**："公司法第 32 条对未实缴股东的责任怎么规定？"

**问题**：公司法 2018 修正、2023 修订（2024-07-01 施行）。两版第 32 条文本
和实质规则不同（2023 修订加入"五年实缴期限"）。Agent 不能凭训练记忆答 ——
必须先锁版本。

**步骤**：

```bash
# Step 1：用户没给事实时点，先问 / 假设当前
chinalaw applicable --date 2026-04-19 --topic 公司治理 --format json
# → primary_law_id: flk-company-law-2024 (2023 修订)
# → fallback_law_id: flk-company-law-2018 (2018 修正)
# → effective_from: 2024-07-01
# → not_legal_conclusion: true

# Step 2：取现行版条文
chinalaw article 公司法 32 --format json
# → 命中 flk-company-law-2024 第 32 条（alias 解析层走最新版本）

# Step 3：如用户场景在 2024-07-01 之前，再取旧版
chinalaw article "公司法（2018 修正）" 32 --format json
# 或 chinalaw article flk-company-law-2018 32

# Step 4：取关联司法解释
chinalaw search 实缴 --in "公司法司法解释" --format json
chinalaw cited-by 公司法:32 --format json
```

**Agent 输出格式**（关键纪律）：

```text
依据层级：公开法（law）
规范：中华人民共和国公司法（2023 修订）/ flk-company-law-2024 / 第32条
状态：current
适用时点：2024-07-01 起
检索线索：applicable on 2026-04-19 → primary_law=2023 修订（not_legal_conclusion）
旧版（fallback）：公司法（2018 修正）第32条 / status=amended
source_url：https://flk.npc.gov.cn/...
source_checked_at：<时间>
```

**反模式**：直接 `chinalaw article 公司法 32`，输出文本但不说版本，让用户
误以为讲的是旧版（2018 修正下"五年实缴期限"还没立法）。

---

## 场景 B：九民纪要对赌协议（准法源、跨源）

**用户提问**："九民纪要怎么处理对赌协议效力？"

**问题**：九民纪要不在 flk_npc 数据库（它不是 law / admin_regulation /
judicial_interpretation，而是 `judicial_meeting_minutes`），必须走
court_gongbao 源。

**步骤**：

```bash
# Step 1：归一化
chinalaw search 九民纪要 --kind law --format json
# → 命中 "全国法院民商事审判工作会议纪要" / level=judicial_meeting_minutes

# Step 2：本地有则取，无则 fetch
chinalaw article 九民纪要 5 --format json
# 命中：直接展示
# 未命中：article=null，需 fetch

# Step 3：（如需）从 court_gongbao 补全
chinalaw fetch 九民纪要 --source court_gongbao --format json
# court_gongbao 默认搜 sfjs 栏目，0 命中时自动 cross_search 到 sfwj
# → 入库 + 走 cleaning 后，再次 article 命中

# Step 4：定位"对赌"相关条款
chinalaw search 对赌 --in 九民纪要 --format json
# → article#5（对赌纠纷处理：与目标公司对赌 vs 与股东对赌）

# Step 5：取出 article 全文
chinalaw article 九民纪要 5 --format json
```

**纪律**：

- 九民纪要是 **会议纪要 / 准法源**，agent 输出时必须明确 "依据层级：会议
  纪要（说理依据，非唯一适用根据）"
- LawLevel = `judicial_meeting_minutes`，落在 `transition_text` 里的
  "应当作为说理依据" —— 不能改写成"应当作为法律依据"

**反模式**：把九民纪要当 `judicial_interpretation` 引用 —— 法源层级误导
读者，二审 / 再审中站不住。

---

## 场景 C：旧合同法 113 → 民法典 584（跨法 transition）

**用户提问**："2019 年签的合同，对方 2023 年违约，违约损失怎么算？应该用
合同法 113 还是民法典 584？"

**问题**：跨期事实，违约责任规则在民法典施行（2021-01-01）后整合，旧合同
法 113 → 民法典 584。**直接答任何一边都错**。

**步骤**：

```bash
# Step 1：查时间效力规则
chinalaw applicable --date 2019-XX-XX --topic 违约责任 --format json
# → primary_law: 合同法 1999（事实时点 < 2021-01-01）
# → fallback_law: 民法典
# → not_legal_conclusion: true
# → transition_text: "持续性合同关系 / 跨期违约 / 解除等问题须单独审查"

# Step 2：查跨法关系
chinalaw relation 合同法 --format json
# → 关系: replaces，from=flk-civil-code-2020, to=flk-contract-law-1999, effective_at=2021-01-01

# Step 3：取两版条文做对照
chinalaw article 合同法 113 --format json   # 旧法（status=repealed）
chinalaw article 民法典 584 --format json   # 新法（status=current）
chinalaw articles --batch '合同法:113;民法典:584' --format json

# Step 4：找跨期适用的司法解释
chinalaw search 时间效力 --in 民法典时间效力规定 --format json
chinalaw search 违约损失 --in 合同编通则解释 --format json
```

**Agent 输出**（关键纪律）：

```text
事实时点：合同签订 2019-XX-XX、违约 2023-XX-XX
适用法律检索线索：
  - 合同签订时：合同法 1999（事实时点 primary_law）/ 第113条 / status=repealed
  - 违约发生时：民法典施行后，民法典 584 条 / status=current
  - 跨期问题：民法典时间效力规定第 X 条
依据层级：公开法（law）
not_legal_conclusion：true（仅检索线索；最终适用结论需结合具体合同
  条款、违约形态、当事人意思自治、最高法相关判决案例由律师判断）
```

**反模式**：

- 直接答"用民法典 584" —— 忽略合同签订时合同法 113 是适用法
- 直接答"用合同法 113" —— 忽略民法典施行后的时间效力规则
- 把 `applicable` 输出改写成"应适用合同法 1999" —— 违反
  `not_legal_conclusion`

---

## 场景 D：私域 + 国家法混合（合同附录引公司内规 + 民法典）

**用户提问**："这份合同附录引了公司放款审查制度第 4 条，又引了民法典
第 524 条。两个引用都对吗？放款制度本身合法吗？"

**问题**：合同附录混引私域规范（公司放款审查制度 = norm）+ 国家法（民法
典 = law）。两者层级不同，agent 输出时必须分层；同时要对私域规范本身的合
法性做基线核对（"它是否违反 strong law"）。

**步骤**：

```bash
# Step 1：导入私域规范（如尚未导入）
chinalaw norm ingest company-policy.docx --name "公司放款审查制度" \
  --source-type company_policy --format json

# Step 2：取私域条款
chinalaw norm clause "公司放款审查制度" 4 --format json
# → 命中 norm_clause；payload 含 source_type=company_policy / 制定主体 /
#   适用范围

# Step 3：取国家法条文
chinalaw article 民法典 524 --format json
# → status=current

# Step 4：可选——把这次工作流沉淀为规范包
chinalaw pack add "放款审查工作底稿" --type article --law 民法典 \
  --article 524 --role core --reason 债务承担基础条款 --format json
chinalaw pack add "放款审查工作底稿" --type norm_clause \
  --norm-source "公司放款审查制度" --clause 4 --role supporting \
  --format json
chinalaw pack validate "放款审查工作底稿" --format json
chinalaw pack show "放款审查工作底稿" --format json
```

**Agent 输出**（关键纪律）：

```text
依据层级 1：公开法（law）
  规范：中华人民共和国民法典 / flk-civil-code-2020 / 第524条
  状态：current
  source_url：https://flk.npc.gov.cn/...

依据层级 2：私域规范（norm / company_policy）
  规范：公司放款审查制度 第4条
  source_type：company_policy（公司内部制度，约束范围限本公司业务）
  ⚠️ 不属于"法律规定"，对外没有强制力
  规范包成员关系：放款审查工作底稿 / role=supporting

合规性：
  - 私域规范第 4 条要求"放款前须取得 X 担保"，与民法典 681-690 条担保
    通则不冲突
  - 但具体条款是否违反《商业银行法》/ 银保监监管要求，需结合用户实际
    业务场景由合规 / 律师人工判断
```

**反模式**：

- 把"公司放款审查制度第 4 条"写成"法律规定要求..."
- 不区分 norm vs law，agent 输出像"按照规定..."这种含糊用法
- 跳过 `pack validate`，直接把规范包当 audit-ready 工作底稿引用
- 把 `pending_reference_in_pack` 当作"已核验条文"

---

## 共通纪律

所有场景都遵守 [`SKILL.md`](../SKILL.md) §"输出契约"：每条引用至少给出
依据层级 / 规范名称 / 条号 / 命令来源 / 状态 / 适用时间 / source_url /
source_checked_at / 不确定性。

跨期 / 跨源 / 跨依据层级时，**显式分层**——这是 agent 与"用法律语言堆词"
的根本区别。
