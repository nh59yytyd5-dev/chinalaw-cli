"""Common legal short-name aliases.

设计边界见 ``docs/CLEANING.md`` §4。本模块只承载「发布主体 → 圈内后缀」
的全域规律，不承载「特定法名 → 特定圈内简称」（那是 fixture / DB 里的
``laws.short_title`` / ``laws.aliases`` 字段的责任，也是 fetch 时领域贡献者
的责任）。

铁则：
- 规则层不写"特定法名 → 特定别名"映射；
- 数据层（fixture）不写"全域规律"派生；
- agent 层（chinalaw resolve / skill 协议）负责处理用户随口说的变体。

triage：当一条规则只覆盖 1-2 部法（如 "劳动争议解释"、"票据规定"、"虚假
陈述规定"），它就是领域黑话——交给 fixture aliases，不在这里。
"""

from __future__ import annotations

import re

_SMALL_ARABIC_TO_CHINESE = {
    "1": "一",
    "2": "二",
    "3": "三",
    "4": "四",
    "5": "五",
    "6": "六",
    "7": "七",
    "8": "八",
    "9": "九",
    "10": "十",
}


def append_unique(target: list[str], value: str | None) -> None:
    alias = (value or "").strip()
    if alias and alias not in target:
        target.append(alias)


def _cn_ordinal(raw: str | None) -> str:
    value = (raw or "").strip()
    return _SMALL_ARABIC_TO_CHINESE.get(value, value)


def _litigation_prefix_abbrev(prefix: str) -> str:
    """主要诉讼法的一字简写（民诉 / 行诉 / 刑诉）。其他诉讼法不简写。"""

    return {
        "民事": "民",
        "行政": "行",
        "刑事": "刑",
    }.get(prefix, prefix)


# ---- §1.1 issuer → 后缀映射 ----
#
# 顺序：从最具体（多机关联发，前缀最长）到最一般。`_identify_issuer` 按出现
# 顺序匹配，第一个命中即返回。
_ISSUER_RULES: list[tuple[str, re.Pattern[str], list[str]]] = [
    (
        "spc_spp_mps",
        re.compile(r"^最高人民法院、最高人民检察院、公安部"),
        ["意见"],
    ),
    (
        "spc_spp",
        re.compile(r"^最高人民法院、最高人民检察院(?!、公安部)"),
        ["解释"],
    ),
    (
        "spc",
        re.compile(r"^最高人民法院(?!、最高人民检察院)"),
        ["解释"],
    ),
    (
        "spp",
        re.compile(r"^最高人民检察院(?!.*最高人民法院)"),
        ["规则", "规定"],
    ),
    (
        "mps",
        re.compile(r"^公安(?:机关|部)"),
        ["规定"],
    ),
    # 以下两类显式标记为"已识别但不派生"——避免误派生覆盖到下游。
    ("guowuyuan", re.compile(r"^国务院"), []),
    ("npc", re.compile(r"^(?:中华人民共和国|全国人民代表大会)"), []),
]


def _identify_issuer(text: str) -> str | None:
    for issuer, pattern, _ in _ISSUER_RULES:
        if pattern.search(text):
            return issuer
    return None


def _issuer_suffixes(issuer: str) -> list[str]:
    for name, _pattern, suffixes in _ISSUER_RULES:
        if name == issuer:
            return suffixes
    return []


# 民法典》 之后接「编名 / 专题名」的特殊抽取。覆盖：
#  - 分编：总则编 / 物权编 / 合同编 / 合同编通则 / 婚姻家庭编 / 继承编 / 侵权责任编 …
#  - 专题：有关担保制度（"有关" 前缀可选）。
# 注意：不固定列表——未来民法典系列司法解释新增分编 / 专题无需改代码。
# 正则细节：
#  - 主 token ``([一-鿿]{2,15}?)`` 用懒量词，遇到第一个能闭合规律的位置就收尾；
#  - lookahead ``(?=若干问题的解释|的解释)`` 锚定后续，避免懒量词越界吞掉
#    「若干问题」把 group(1) 拉脏。
_MINFADIAN_TOPIC_RE = re.compile(
    r"民法典》(?:有关)?([一-鿿]{2,15}?)"
    r"(?=若干问题的解释|的解释)"
)

# 通用：适用《X》... 的解释。X 去掉前缀「中华人民共和国」。
_APPLIES_BOOK_RE = re.compile(
    r"关于适用《(?:中华人民共和国)?([^》]+?)》"
)

# 上游 / 用户偶尔丢掉书名号；只对诉讼法这种实际会发生的形态兜底，避免
# 误抓到长描述短语。
_APPLIES_LITIGATION_NO_BRACKET_RE = re.compile(
    r"关于适用(?:中华人民共和国)?([一-鿿]{2,20}?诉讼法)的解释"
)
_LITIGATION_LAW_TITLE_RE = re.compile(
    r"^(?:中华人民共和国)?(?P<prefix>民事|行政|刑事)诉讼法$"
)
_VERSIONED_TITLE_RE = re.compile(
    r"^(?P<base>.+?)[（(](?:19|20)\d{2}年?(?:修正文本|修正|修订|修改)?[）)]$"
)

_TIME_EFFECT_RE = re.compile(
    r"关于适用《(?:中华人民共和国)?(?P<base>[^》]+?)》时间效力的若干规定"
)

_TRIAL_CASE_APPLICABLE_RE = re.compile(
    r"关于审理(?P<topic>[一-鿿A-Za-z0-9、，,（）()《》〈〉“”\"'：:·]+?)"
    r"案件(?:适用法律)?(?:若干)?问题的(?P<suffix>解释|规定)"
)
_SPC_TOPIC_PROVISIONS_RE = re.compile(
    r"关于(?P<topic>[一-鿿A-Za-z0-9、，,（）()《》〈〉“”\"'：:·]+?)的若干规定"
)
_ARTICLE_REPLY_RE = re.compile(
    r"关于《(?:中华人民共和国)?(?P<base>[^》]+?)》第(?P<article>[一二三四五六七八九十百千万零〇\d]+条)"
    r".*?的批复"
)

def _strip_prc_prefix(text: str | None) -> str:
    value = (text or "").strip()
    return value.removeprefix("中华人民共和国")


def _special_subject_aliases(text: str, issuer: str | None) -> list[str]:
    """Title-pattern aliases that are broader than one fixture but narrower
    than issuer suffix rules.

    These rules cover recurring judicial / State Council title templates where
    the generic issuer rule would be misleading, e.g. treating "公司法时间效力
    规定" as plain "公司法解释".
    """

    aliases: list[str] = []

    m = _TIME_EFFECT_RE.search(text)
    if issuer == "spc" and m:
        base = _strip_prc_prefix(m.group("base"))
        append_unique(aliases, f"{base}时间效力规定")
        append_unique(aliases, f"{base}时间效力解释")
        append_unique(aliases, f"{base}时效规定")
        return aliases

    m = _TRIAL_CASE_APPLICABLE_RE.search(text)
    if issuer == "spc" and m:
        topic = m.group("topic").strip()
        suffix = m.group("suffix").strip()
        append_unique(aliases, f"{topic}{suffix}")
        if topic.endswith("责任纠纷"):
            append_unique(aliases, f"{topic[:-len('责任纠纷')]}{suffix}")
        elif topic.endswith("纠纷"):
            append_unique(aliases, f"{topic[:-len('纠纷')]}{suffix}")
        return aliases

    m = _SPC_TOPIC_PROVISIONS_RE.search(text)
    if issuer == "spc" and m:
        topic = m.group("topic").strip()
        append_unique(aliases, f"{topic}规定")
        return aliases

    m = _ARTICLE_REPLY_RE.search(text)
    if issuer == "spc" and m:
        base = _strip_prc_prefix(m.group("base"))
        article = m.group("article").strip()
        append_unique(aliases, f"{base}第{article}批复")
        append_unique(aliases, f"{base}{article}批复")
        return aliases

    return aliases


def _extract_host_law(text: str) -> str | None:
    """从标题里抽取宿主法名 X。覆盖三种模式：

    1. 民法典分编 / 专题解释：``《民法典》<编名|有关<专题>>...的解释``
       → 编名 / 专题名。
    2. ``关于适用《X》的解释`` / ``关于适用《X》若干问题的(规定|解释)``
       → X（去 ``中华人民共和国`` 前缀）。
    3. 无书名号兜底：仅限 ``...诉讼法的解释`` 这一确定形态。

    其它模式（``审理 X 案件``、``X 时间效力的规定`` 等）一律返回 None，
    交由 fixture 的 ``aliases`` / ``short_title`` 字段兜底——那是领域圈内
    黑话，规则层不接。

    民法典》 出现但 ``_MINFADIAN_TOPIC_RE`` 抽不到 → 不再 fallback 到
    ``_APPLIES_BOOK_RE``（否则会抽到 ``民法典`` 本体派生出 ``民法典解释``
    误别名，比如 ``...民法典》时间效力的若干规定``）。
    """

    m = _MINFADIAN_TOPIC_RE.search(text)
    if m:
        return m.group(1).strip() or None

    if "民法典》" in text:
        return None

    m = _APPLIES_BOOK_RE.search(text)
    if m:
        return m.group(1).strip() or None

    m = _APPLIES_LITIGATION_NO_BRACKET_RE.search(text)
    if m:
        return m.group(1).strip() or None

    return None


_ORDINAL_RE = re.compile(r"[（(]([一二三四五六七八九十\d]+)[）)]\s*$")


def _extract_ordinal(text: str) -> str | None:
    """抽取标题尾部的 ``（一）`` / ``（二）`` 等顺序号；归一为中文。"""

    m = _ORDINAL_RE.search(text)
    if not m:
        return None
    ordinal = _cn_ordinal(m.group(1))
    return ordinal or None


def _statute_title_aliases(text: str) -> list[str]:
    """Aliases for direct statute titles, without per-law hardcoding."""

    aliases: list[str] = []
    base_title = text
    version_match = _VERSIONED_TITLE_RE.match(text)
    if version_match:
        base_title = version_match.group("base").strip()
        append_unique(aliases, base_title)

    stripped = _strip_prc_prefix(base_title)
    if stripped != base_title and 2 <= len(stripped) <= 24:
        append_unique(aliases, stripped)

    litigation_match = _LITIGATION_LAW_TITLE_RE.match(base_title)
    if litigation_match:
        abbrev = _litigation_prefix_abbrev(litigation_match.group("prefix"))
        if abbrev != litigation_match.group("prefix"):
            append_unique(aliases, f"{abbrev}诉法")

    return aliases


def common_law_aliases(title: str | None) -> list[str]:
    """根据标题派生圈内常用别名。

    本函数只承载「发布主体 → 后缀」全域规则。特定法的官方简称
    （企业破产法 → 破产法）由 fixture 的 ``short_title`` 提供；
    领域圈内黑话由 fixture 的 ``aliases`` JSON 字段提供。
    详见 ``docs/CLEANING.md`` §4。
    """

    text = (title or "").strip()
    if not text:
        return []

    aliases = _statute_title_aliases(text)
    issuer = _identify_issuer(text)
    if issuer is None:
        return aliases

    special_aliases = _special_subject_aliases(text, issuer)
    if special_aliases:
        for alias in special_aliases:
            append_unique(aliases, alias)
        return aliases

    base = _extract_host_law(text)
    if not base:
        return aliases

    ordinal = _extract_ordinal(text)
    suffixes = _issuer_suffixes(issuer)

    is_minfadian_book = bool(_MINFADIAN_TOPIC_RE.search(text))

    for suffix in suffixes:
        if ordinal:
            append_unique(aliases, f"{base}{suffix}{ordinal}")
            append_unique(aliases, f"{base}{suffix}（{ordinal}）")
            # 律师社区习惯：第一部解释的去序号写法是通用简称（婚姻家庭编两部、
            # 保险法三部、破产法三部都成立）。仅对 ordinal=='一' 生效。
            if ordinal == "一":
                append_unique(aliases, f"{base}{suffix}")
        else:
            append_unique(aliases, f"{base}{suffix}")

        # 民法典分编：同时给 ``民法典X+suffix`` 形态。
        if is_minfadian_book:
            if ordinal:
                append_unique(aliases, f"民法典{base}{suffix}{ordinal}")
                if ordinal == "一":
                    append_unique(aliases, f"民法典{base}{suffix}")
            else:
                append_unique(aliases, f"民法典{base}{suffix}")

    # 诉讼法配套：民诉/行诉/刑诉社区固定缩写。其他诉讼法（如 海事诉讼法）
    # 不在此 hook，因为没有形成稳定缩写习惯。
    if (
        base.endswith("诉讼法")
        and len(base) > len("诉讼法")
        and "解释" in suffixes
    ):
        prefix = base[: -len("诉讼法")]
        abbrev = _litigation_prefix_abbrev(prefix)
        if abbrev != prefix:  # 仅对识别到的三大诉讼法派生缩写
            append_unique(aliases, f"{abbrev}诉法解释")
            append_unique(aliases, f"{abbrev}诉解释")

    return aliases


def preferred_short_title(title: str | None) -> str | None:
    """选取「把规则派生 alias 用作 short_title 时」的首选形态。

    语义契约（**显式**）：
        ``common_law_aliases(title)`` 列表的第一个元素，是律师社区最常用
        的短形态（比如「合同编通则解释」/「公司法解释一」）。当上游标题
        超长（27+ 字）且 fixture 没给 ``short_title`` 时，court_gongbao /
        spp_gov_cn 的 ``_infer_short_title`` 用本函数推断 short_title。

    **改动 ``common_law_aliases`` 输出顺序前必须先看本函数的所有调用方**：
        - ``src/chinalaw/adapters/court_gongbao.py:_infer_short_title``
        - ``src/chinalaw/adapters/spp_gov_cn.py:_infer_short_title``

    返回 ``None`` 表示标题不属于规则可派生范围，调用方应继续 fallback
    （剥发布主体长前缀 / 剥「中华人民共和国」）。

    详见 ``docs/CLEANING.md`` §4。
    """

    aliases = common_law_aliases(title)
    return aliases[0] if aliases else None


def display_short_title(title: str | None, short_title: str | None = None) -> str | None:
    short = (short_title or "").strip()
    if short:
        return short
    return preferred_short_title(title)


def merge_law_aliases(
    title: str | None,
    short_title: str | None,
    aliases: list[str] | None,
) -> list[str]:
    merged: list[str] = []
    for alias in aliases or []:
        append_unique(merged, alias)
    append_unique(merged, short_title)
    for alias in common_law_aliases(title):
        append_unique(merged, alias)
    return merged
