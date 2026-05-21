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
    UNKNOWN = "unknown"


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
