"""Adapter 私有 HTML utility 层。

`court_gongbao`、`court_main` 与 `spp_gov_cn` 三个 HTML adapter 在标题提取 / 正文 HTML
转纯文本 / 短标题推断 / 标题后缀剥离这四件事上行为高度一致；本模块把这
些公共能力收敛到一处，让站点 adapter 只提供站点专有数据（标题后缀清单、
issuer 前缀清单），共用算法保持单一权威。

详见 `docs/ADAPTER_HTML_HELPERS_SPEC.md`。
"""

from __future__ import annotations

import re
from html import unescape

from chinalaw import cleaning
from chinalaw.aliases import preferred_short_title

_TITLE_RE = re.compile(r"<title>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_BR_RE = re.compile(r"<\s*(br|BR)\s*/?\s*>")
_BLOCK_CLOSE_RE = re.compile(r"</\s*(p|li|tr|div|h[1-6])\s*>", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
_INNER_WS_RE = re.compile(r"[ \t]+")
_TITLE_SUFFIX_TRIM = " -—_"


def html_extract_title(html: str) -> str | None:
    """抽 ``<title>`` 标签内文本，剥 HTML 标签并折叠空白。"""

    if not html:
        return None
    match = _TITLE_RE.search(html)
    if not match:
        return None
    title = match.group(1).strip()
    title = _BR_RE.sub(" ", title)
    title = _TAG_RE.sub("", title)
    title = unescape(title)
    title = (
        title.replace("　", " ")
        .replace(" ", " ")
        .replace(" ", " ")
    )
    return re.sub(r"\s+", " ", title).strip()


def html_to_text(content_html: str) -> str:
    """把抽出的 HTML 片段转成纯文本，保留段落分隔。

    规则：

    - ``</p>`` / ``</li>`` / ``</tr>`` / ``</div>`` / ``</hN>`` / ``<br>`` → 换行
    - 所有其它标签剥光
    - HTML 实体解码
    - 全角空格 ``\\u3000`` / EN space ``\\u2002`` / EM space ``\\u2003`` → 普通空格
    - 行内多余空白折叠 + 连续空行折叠

    spp 修前会同时归一三种空白；court 修前只归一全角空格。两者 fixture 已
    grep 验证不冲突，权威路径取超集。
    """

    if not content_html:
        return ""
    text = content_html
    text = _BR_RE.sub("\n", text)
    text = _BLOCK_CLOSE_RE.sub("\n", text)
    text = _TAG_RE.sub("", text)
    text = unescape(text)
    text = (
        text.replace("　", " ")
        .replace(" ", " ")
        .replace(" ", " ")
    )
    lines = [_INNER_WS_RE.sub(" ", line).strip() for line in text.splitlines()]
    out: list[str] = []
    blank = False
    for line in lines:
        if not line:
            if blank:
                continue
            blank = True
        else:
            blank = False
        out.append(line)
    return "\n".join(out).strip()


def strip_known_title_suffix(raw_title: str, suffixes: tuple[str, ...]) -> str:
    """剥离站点 ``<title>`` 统一后缀。

    ``suffixes`` 顺序敏感：先匹配的 suffix 先剥；典型形态包含""带前导空白""
    和""无前导空白""两种，让 caller 把更具体的形态排在前。
    """

    if not raw_title:
        return raw_title
    cleaned = raw_title.replace("　", " ").strip()
    for suffix in suffixes:
        if cleaned.endswith(suffix):
            cleaned = cleaned[: -len(suffix)].rstrip(_TITLE_SUFFIX_TRIM)
            break
    return cleaned.strip()


def infer_short_title(title: str, *, site_prefixes: tuple[str, ...]) -> str | None:
    """规则派生短名 + 站点 issuer 前缀剥离。

    顺序：

    1. ``aliases.preferred_short_title``——规则派生的圈内短形态优先
       （如"合同编通则解释"），避免长 title 占满 agent 笔记
    2. ``site_prefixes`` 任一前缀剥离 + ``strip(" 关于的")``，长度 [2, 30]
       才接受（避免空 / 过长结果）
    3. ``cleaning.infer_short_title``（剥""中华人民共和国""）兜底
    """

    if not title:
        return None
    cleaned = title.replace("　", " ").strip()
    cleaned = re.sub(r"\s+", " ", cleaned)

    short = preferred_short_title(cleaned)
    if short:
        return short

    for prefix in site_prefixes:
        if cleaned.startswith(prefix):
            stripped = cleaned[len(prefix):].strip(" 关于的")
            if 2 <= len(stripped) <= 30:
                return stripped
    return cleaning.infer_short_title(cleaned)
