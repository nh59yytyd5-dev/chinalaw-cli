"""领域数据模型占位。

v0 阶段先用 dataclass 不引入 pydantic，避免绑定依赖。
正式实现时再按需切换到 pydantic.BaseModel 获得校验与 JSON schema。
详见 docs/ARCHITECTURE.md 第 2 节。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum


class LawLevel(str, Enum):
    """法律效力层级。

    分层语义见 docs/CONTRACT.md §2.9。

    新增枚举值的边界（2026-05 调研）：

    - ``supervisory_regulation`` 来自 cleaning.FLXZ_TO_LEVEL，原先 enum 漏声明
      导致数据契约破裂；此次修复。
    - ``judicial_meeting_minutes`` / ``judicial_policy`` / ``guiding_case`` 三个
      值用于会议纪要 / 司法政策 / 指导案例，flk 不收录这三类，由后续 court_gongbao
      / spp_gov_cn 等 adapter 直接写入。本期仅在 enum 中预留位置。
    """

    LAW = "law"
    ADMIN_REGULATION = "admin_regulation"
    JUDICIAL_INTERPRETATION = "judicial_interpretation"
    JUDICIAL_MEETING_MINUTES = "judicial_meeting_minutes"
    JUDICIAL_POLICY = "judicial_policy"
    GUIDING_CASE = "guiding_case"
    DEPARTMENT_RULE = "department_rule"
    LOCAL_REGULATION = "local_regulation"
    LOCAL_GOVERNMENT_RULE = "local_government_rule"
    SUPERVISORY_REGULATION = "supervisory_regulation"
    SELF_REGULATORY_RULE = "self_regulatory_rule"
    OTHER = "other"


class LawStatus(str, Enum):
    """法规的现行状态。"""

    CURRENT = "current"
    AMENDED = "amended"
    REPEALED = "repealed"
    PENDING_EFFECTIVE = "pending_effective"
    SEED = "seed"
    UNKNOWN = "unknown"


class NormSourceType(str, Enum):
    """私域规范来源类型（受控枚举，按约束力来源分类）。

    私域规范不是国家法规范、不具备法律渊源效力；本枚举只区分其约束力来源：

    - ``contractual_requirement``：合同约定型（放款条件、交易相对方要求）
    - ``internal_governance``：内部治理型（公司制度、合规手册、HR 制度；缺省值）
    - ``standard``：标准型（国标 / 行标 / 团标）
    - ``trade_usage``：习惯惯例型（交易习惯、行业惯例）
    - ``other``：兜底类型；枚举外的历史存量值在输出层按此处理

    各类型的约束力定性见 ``NORM_SOURCE_TYPE_BINDING_NOTES``；私域规范之间
    不提供绝对效力排序。分层语义见 docs/CONTRACT.md §2.9。
    """

    CONTRACTUAL_REQUIREMENT = "contractual_requirement"
    INTERNAL_GOVERNANCE = "internal_governance"
    STANDARD = "standard"
    TRADE_USAGE = "trade_usage"
    OTHER = "other"


# 各类型的约束力定性提示（binding note），只用于输出层提示，说明该类型的
# 约束力来源与边界；私域规范之间不存在线性效力高低，不做绝对排序。
NORM_SOURCE_TYPE_BINDING_NOTES: dict[str, str] = {
    NormSourceType.CONTRACTUAL_REQUIREMENT.value: (
        "合同约定型规范：仅经合同约定产生约束力，属合同义务范畴。"
    ),
    NormSourceType.INTERNAL_GOVERNANCE.value: (
        "内部治理型规范：对内约束；劳动法语境下规章制度需经民主程序并公示"
        "才对员工生效（参见《劳动合同法》第4条）。"
    ),
    NormSourceType.STANDARD.value: (
        "标准型规范：强制性标准必须执行，推荐性标准经合同援引方有约束力"
        "（参见《标准化法》第2条、第10条）。"
    ),
    NormSourceType.TRADE_USAGE.value: (
        "习惯惯例型规范：《民法典》第10条意义上的习惯，作为法源补充。"
    ),
    NormSourceType.OTHER.value: (
        "其他私域规范：约束力来源需个案判断。"
    ),
}

# 已废弃的旧枚举值 → 现行枚举值。导入侧遇旧值自动映射并附 deprecation_warning；
# 存量库中的旧值不做数据迁移，输出层归一为新值并附 legacy_source_type 原值。
LEGACY_NORM_SOURCE_TYPE_MAP: dict[str, str] = {
    "lender_requirement": NormSourceType.CONTRACTUAL_REQUIREMENT.value,
    "internal_compliance": NormSourceType.INTERNAL_GOVERNANCE.value,
    "private_policy": NormSourceType.INTERNAL_GOVERNANCE.value,
    "industry_standard": NormSourceType.STANDARD.value,
}


def normalize_norm_source_type(source_type: str | None) -> tuple[str, str | None]:
    """归一化私域规范来源类型，返回 ``(新值, 原值或 None)``。

    现行枚举值原样返回；已废弃的旧值按 ``LEGACY_NORM_SOURCE_TYPE_MAP`` 映射；
    其余历史存量值（含空值）兜底为 ``other``。发生过映射 / 兜底时第二个
    返回值是原值，供输出层附 ``legacy_source_type``。
    """

    cleaned = (source_type or "").strip()
    if cleaned in NORM_SOURCE_TYPE_BINDING_NOTES:
        return cleaned, None
    mapped = LEGACY_NORM_SOURCE_TYPE_MAP.get(cleaned)
    if mapped is not None:
        return mapped, cleaned
    return NormSourceType.OTHER.value, cleaned or None


def norm_source_type_binding_note(source_type: str | None) -> str:
    """返回来源类型的约束力定性提示；旧值 / 枚举外值先归一再取提示。"""
    normalized, _ = normalize_norm_source_type(source_type)
    return NORM_SOURCE_TYPE_BINDING_NOTES[normalized]


@dataclass
class Law:
    id: str
    title: str
    level: LawLevel
    status: LawStatus
    source_url: str
    source_name: str
    source_hash: str
    source_checked_at: datetime
    short_title: str | None = None
    aliases: list[str] = field(default_factory=list)
    issuing_body: str | None = None
    document_number: str | None = None
    released_at: date | None = None
    effective_at: date | None = None
    repealed_at: date | None = None


@dataclass
class Article:
    id: str
    law_id: str
    number: str               # 标准化阿拉伯数字，例 "71"
    number_display: str       # 显示用，例 "第七十一条"
    text: str
    position: int
    part: str | None = None
    title: str | None = None


@dataclass
class Category:
    id: str
    name: str
    parent_id: str | None = None
    description: str | None = None


@dataclass
class Revision:
    id: str
    law_id: str
    version_label: str
    released_at: date
    content_hash: str
    effective_at: date | None = None
    notes: str | None = None
