"""最高人民检察院（spp.gov.cn）adapter。

本期同时实装：

- ``probe()``        — 站点首页健康度
- ``search_list()``  — ``/spp/{channel}/index.shtml`` 列表解析；翻页用
  ``index_{N}.shtml``；query 客户端按标题子串过滤（站点本身没有 ``q=`` 参数）
- ``fetch_detail()`` — 详情页解析（``<div id="fontzoom">`` 抽正文）。详情页
  路径多变（``/xwfbh/wsfbt/...`` / ``/spp/sfjs/...`` / ``/zdgz/...``），
  ``detail_id`` 设计为"路径片段"，统一去掉 leading ``/`` 和 ``.shtml``，反推
  URL 时拼回
- ``build_law_payload()`` — HTML → 纯文本 → ``cleaning.canonicalize`` 的标准载荷
- ``source_hash()``  — 详情正文文本的 sha256

Channel 启发式见 ``CHANNEL_TO_LEVEL_HINT``：``sfjs`` 默认司法解释；``gfwj``
归到司法政策；``jczdal`` 归到指导案例；其余落 ``other``。``title`` 层再做二次
启发式（含「指导意见」/「意见」 → ``judicial_policy``；含「指导性案例」 →
``guiding_case``）。

候选源详情见 ``docs/decisions/ADR-0008-multi-source-adapters.md`` §1.2
与 ``docs/research/2026-05-source-coverage-survey.md`` §4.2。
"""

from __future__ import annotations

import hashlib
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html import unescape
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

from chinalaw import USER_AGENT_TOKEN, cleaning
from chinalaw.adapters import _html as _adapter_html
from chinalaw.document_numbers import extract_document_number

_extract_document_number = extract_document_number

DEFAULT_BASE_URL = "https://www.spp.gov.cn"
DEFAULT_TIMEOUT = 10
DEFAULT_REQUEST_INTERVAL = 0.5  # ADR-0008 §3.3：默认节流提到 500ms
# 节流硬下限。任何调用方传入 ``<= 0`` 或低于此值的间隔都会被 clamp 到
# ``MIN_REQUEST_INTERVAL``，无法在 adapter 层关闭节流。详见 docs/COMPLIANCE.md §3。
MIN_REQUEST_INTERVAL = 0.1

# UA 同时含浏览器兼容前缀和 chinalaw-cli 标识，详见 docs/COMPLIANCE.md §4。
TOOL_UA_TOKEN = USER_AGENT_TOKEN
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36 "
    f"{TOOL_UA_TOKEN}"
)

# 最高检站点常见栏目（来自 2026-05 调研，仅 probe 健康度判断用）。
KNOWN_SECTIONS = (
    "指导性案例",
    "司法解释",
    "权威发布",
    "新闻发布会",
    "检察新闻",
)

# 已知 channel → 中文标签 + 默认 level 提示。``title`` 层会做二次启发式。
# - sfjs（司法解释 tab）：聚合两高 / 两高一部联合刑事司法解释 / 法律解释
# - gfwj（规范文件）：最高检规范性文件、意见、通知
# - nbgz（内部规章）：检察机关内部规章
# - jczdal（检察典型案例）：每"批"指导性案例公告页（注：详情非条文化结构，
#   articles 通常为空，按 ADR-0008 §1.2 与 user 指示，本期"顺带兼容"——能跑
#   通 fetch 但不为它专门做 norm_source 切分）
CHANNEL_TO_LABEL = {
    "sfjs": "司法解释",
    "gfwj": "规范文件",
    "nbgz": "内部规章",
    "jczdal": "检察典型案例",
}

CHANNEL_TO_LEVEL_HINT = {
    "sfjs": "judicial_interpretation",
    "gfwj": "judicial_policy",
    "nbgz": "other",
    "jczdal": "guiding_case",
}

# spp 列表 URL：``/spp/<channel>/index.shtml`` 或 ``/spp/<channel>/index_<N>.shtml``。
LIST_PATH_FMT = "/spp/{channel}/{index}.shtml"

# 列表行：``<li><a href="..." target="_blank"  >{title}</a><span>YYYY-MM-DD</span></li>``。
# 日期 ``<span>`` 可缺；标题文本可能含全角空格。
LIST_ITEM_RE = re.compile(
    r"<li[^>]*>\s*<a\s+href=\"(?P<href>[^\"]+)\"[^>]*>"
    r"(?P<title>[^<]+)</a>"
    r"(?:\s*<span[^>]*>(?P<date>[^<]+)</span>)?",
    re.IGNORECASE,
)

# 详情正文：``<div id="fontzoom">`` 起，到 ``<div id="pageBreak">`` 或
# ``<div class="footer">`` 为止；不取闭合 div 个数（spp 内嵌 div 不固定）。
FONTZOOM_RE = re.compile(
    r"<div[^>]*id=\"fontzoom\"[^>]*>(?P<body>.*?)"
    r"(?=<div[^>]*id=\"pageBreak\"|<div\s+class=\"footer\"|</body>)",
    re.IGNORECASE | re.DOTALL,
)
MATCH_IGNORED_RE = re.compile(r"[\s　、，,]+")

# 文号识别已上提到 ``chinalaw.document_numbers.extract_document_number``，
# adapter 不再维护本地正则；详见
# ``docs/UNIFY_DOCUMENT_NUMBER_REGEX_SPEC.md``。Module-level alias
# ``_extract_document_number`` 保留作为 adapter API（见上方 import 行）。
# 该 helper 覆盖最高检常见前缀 ``高检发释字〔YYYY〕N号`` / ``高检发〔YYYY〕N号``，
# 以及联合解释的 ``法释〔YYYY〕N号`` / ``法发〔YYYY〕N号`` / ``中办发〔...〕`` 等。

# detail_id 校验：路径片段，允许字母 / 数字 / 下划线 / 斜杠；不允许空字符串。
# 例：``xwfbh/wsfbt/202501/t20250116_679579`` / ``spp/sfjs/201802/t20180201_363640``。
DETAIL_ID_FULLMATCH_RE = re.compile(r"^[\w/\-]+$")


@dataclass
class FetchResult:
    url: str
    status_code: int
    headers: Mapping[str, str]
    text: str


def _build_request(url: str) -> Request:
    return Request(
        url,
        headers={
            "User-Agent": DEFAULT_USER_AGENT,
            "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.5",
        },
        method="GET",
    )


def _fetch_text(url: str, timeout: int = DEFAULT_TIMEOUT) -> FetchResult:
    req = _build_request(url)
    with urlopen(req, timeout=timeout) as resp:
        charset = resp.headers.get_content_charset() or "utf-8"
        body = resp.read().decode(charset, errors="replace")
        return FetchResult(
            url=resp.geturl(),
            status_code=resp.getcode(),
            headers=dict(resp.headers.items()),
            text=body,
        )


# 最高检站 ``<title>`` 后缀清单（adapter 私有数据）。共用 strip 算法在
# ``chinalaw.adapters._html.strip_known_title_suffix``。详见
# docs/ADAPTER_HTML_HELPERS_SPEC.md。
_TITLE_SUFFIXES: tuple[str, ...] = (
    "_中华人民共和国最高人民检察院",
    "-中华人民共和国最高人民检察院",
    " - 中华人民共和国最高人民检察院",
    "_最高人民检察院",
)


# Module-level alias 保留 ``spp_gov_cn._extract_title`` 接口（既有测试 +
# adapter 内部 fetch_detail / probe 仍用此名）。
_extract_title = _adapter_html.html_extract_title


def _strip_title_suffix(raw_title: str) -> str:
    """剥离 spp 站点统一的标题后缀。

    页面 ``<title>`` 形如 ``X_中华人民共和国最高人民检察院``。
    """

    return _adapter_html.strip_known_title_suffix(raw_title, _TITLE_SUFFIXES)


def _detect_known_sections(html: str) -> list[str]:
    return [section for section in KNOWN_SECTIONS if section in html]


def _detect_page_shape(html: str, *, status_code: int) -> str:
    if not (200 <= status_code < 300):
        return "error"
    if "<title>" in html.lower():
        return "ok"
    return "unknown"


def _normalize_detail_id(raw: str | None) -> str | None:
    """把任意 spp URL / 路径 / id 规整成"detail_id"（不带 leading /，不带 .shtml）。

    接受形态：

    - ``https://www.spp.gov.cn/xwfbh/wsfbt/202501/t20250116_679579.shtml#1``
    - ``/xwfbh/wsfbt/202501/t20250116_679579.shtml``
    - ``//xwfbh/wsfbt/...``（spp 列表里偶见双斜杠）
    - ``xwfbh/wsfbt/202501/t20250116_679579``

    全部规整为：``xwfbh/wsfbt/202501/t20250116_679579``。
    """

    if not raw:
        return None
    text = str(raw).strip()
    if not text:
        return None
    # 形如 http(s):// 时先剥 host
    if "://" in text:
        parsed = urlparse(text)
        text = parsed.path or ""
    # 剥 fragment / query 残余
    for sep in ("#", "?"):
        idx = text.find(sep)
        if idx >= 0:
            text = text[:idx]
    # 折叠多斜杠 + 剥前导斜杠 + 剥 .shtml
    text = re.sub(r"/+", "/", text)
    text = text.lstrip("/")
    if text.endswith(".shtml"):
        text = text[: -len(".shtml")]
    text = text.strip("/")
    if not text or not DETAIL_ID_FULLMATCH_RE.match(text):
        return None
    return text


def _parse_list_rows(
    html: str,
    *,
    base_url: str,
    channel: str | None = None,
) -> list[dict]:
    rows: list[dict] = []
    seen_ids: set[str] = set()
    for match in LIST_ITEM_RE.finditer(html):
        href = match.group("href").strip()
        title = unescape(match.group("title")).replace("　", " ").strip()
        date = (match.group("date") or "").strip() or None
        detail_id = _normalize_detail_id(href)
        if not detail_id or detail_id in seen_ids:
            continue
        seen_ids.add(detail_id)
        # 列表 anchor 的 href 可能是相对路径 / 绝对 URL / 含双斜杠形态；
        # 用 detail_id 拼回干净 URL，避免下游 source_url 携带 fragment。
        clean_url = urljoin(
            base_url if base_url.endswith("/") else f"{base_url}/",
            f"/{detail_id}.shtml",
        )
        rows.append(
            {
                "detail_id": detail_id,
                "channel": channel,
                "title": title,
                "released_at": date,
                "url": clean_url,
                "status": "current",
            }
        )
    return rows


def _extract_content_html(html: str) -> str:
    """从详情页抽 ``<div id="fontzoom">`` 内的 HTML。"""

    match = FONTZOOM_RE.search(html)
    if match:
        return match.group("body").strip()
    return ""


def _match_text(raw: str | None) -> str:
    return MATCH_IGNORED_RE.sub("", raw or "")


def _title_matches_query(title: str | None, query: str | None) -> bool:
    needle = _match_text(query)
    if not needle:
        return True
    return needle in _match_text(title)


def _html_to_text(content_html: str) -> str:
    """把抽出的内容 HTML 转成纯文本，保留段落分隔。

    实装委托至 ``chinalaw.adapters._html.html_to_text``——adapter 公共能力，
    详见 docs/ADAPTER_HTML_HELPERS_SPEC.md。Module-level 名字保留供既有测试
    与 adapter 内部 build_law_payload 调用。
    """

    return _adapter_html.html_to_text(content_html)


def _infer_level(channel: str | None, title: str) -> str:
    """level 启发式。

    title 中"指导性案例" / "意见" / "解释" 等关键字优先于 channel 默认值。
    """

    base = CHANNEL_TO_LEVEL_HINT.get((channel or "").lower(), "other")
    if "指导性案例" in title or "公报案例" in title:
        return "guiding_case"
    if "纪要" in title:
        return "judicial_meeting_minutes"
    if "解释" in title or "批复" in title or "复函" in title:
        return "judicial_interpretation"
    if "意见" in title or "通知" in title:
        return "judicial_policy"
    return base


def _infer_issuing_body(title: str) -> str:
    """根据标题前缀识别发布主体。

    spp 站收录大量两高 / 两高一部联合发布。识别原则：
    - 同时含"最高人民法院" + "最高人民检察院" → 联合（可再叠加"公安部"等）
    - 仅含一方 → 仅写该方
    - 含"两高" / "两高一部"简称（无 explicit 全名）→ 视为联合
    - 都不含 → 默认 ``最高人民检察院``（spp 站点本源）
    """

    if not title:
        return "最高人民检察院"
    bodies: list[str] = []
    has_court = "最高人民法院" in title
    has_proc = "最高人民检察院" in title
    # "两高" / "两高一部" 简称：spp /sfjs/ 列表里大量历史条目用这种缩写
    # （如"两高关于办理刑事赔偿案件适用法律若干问题的解释"），如果不识别
    # 简称就会误归到默认的"最高人民检察院"，下游 publisher 过滤会漏召回。
    # 仅在两个全名都缺失时启用，避免重复或与 explicit 形态打架。
    if not has_court and not has_proc and "两高" in title:
        bodies.extend(("最高人民法院", "最高人民检察院"))
    else:
        if has_court:
            bodies.append("最高人民法院")
        if has_proc:
            bodies.append("最高人民检察院")
    for extra in ("公安部", "国家安全部", "司法部"):
        if extra in title:
            bodies.append(extra)
    if not bodies:
        return "最高人民检察院"
    return " ".join(bodies)


# spp 标题前缀清单（adapter 私有数据；含联合发布 "公安部" 前缀）。共用算法
# 在 ``chinalaw.adapters._html.infer_short_title``。
_ISSUER_PREFIXES: tuple[str, ...] = (
    "最高人民法院 最高人民检察院 公安部 ",
    "最高人民法院 最高人民检察院 ",
    "最高人民法院 ",
    "最高人民法院",
    "最高人民检察院 ",
    "最高人民检察院",
)


def _infer_short_title(title: str) -> str | None:
    """spp 标题常见前缀剥离 + alias 优先短名。

    实装委托至 ``chinalaw.adapters._html.infer_short_title``。Module-level
    名字保留供既有测试与 build_law_payload 调用。
    """

    return _adapter_html.infer_short_title(title, site_prefixes=_ISSUER_PREFIXES)


@dataclass
class SppGovCnAdapter:
    """最高人民检察院 adapter。

    覆盖文件类别：两高 / 两高一部联合刑事司法解释、最高检规范文件 / 意见 /
    指导意见、最高检指导性案例。详见 ADR-0008 §1.2。
    """

    base_url: str = DEFAULT_BASE_URL
    timeout: int = DEFAULT_TIMEOUT
    request_interval: float = DEFAULT_REQUEST_INTERVAL
    _last_request_at: float = field(default=0.0, repr=False)

    def _throttle(self) -> None:
        # 节流硬下限，详见 docs/COMPLIANCE.md §3。
        interval = max(float(self.request_interval or 0), MIN_REQUEST_INTERVAL)
        now = time.monotonic()
        wait = interval - (now - self._last_request_at)
        if wait > 0:
            time.sleep(wait)
        self._last_request_at = time.monotonic()

    def probe(self) -> dict:
        """探测最高检站首页，与 ``flk_npc`` / ``court_gongbao`` 字段对齐。"""

        self._throttle()
        checked_at = datetime.now(timezone.utc).isoformat()
        homepage_url = self.base_url if self.base_url.endswith("/") else f"{self.base_url}/"
        try:
            homepage = _fetch_text(homepage_url, timeout=self.timeout)
        except HTTPError as exc:
            return {
                "source": "spp_gov_cn",
                "homepage_url": homepage_url,
                "final_url": homepage_url,
                "status_code": exc.code,
                "title": None,
                "page_shape": "error",
                "detected_sections": [],
                "bundle_contains_known_sections": False,
                "source_last_modified": None,
                "source_etag": None,
                "checked_at": checked_at,
                "error": f"HTTPError {exc.code}: {exc.reason}",
            }
        except URLError as exc:
            return {
                "source": "spp_gov_cn",
                "homepage_url": homepage_url,
                "final_url": homepage_url,
                "status_code": None,
                "title": None,
                "page_shape": "error",
                "detected_sections": [],
                "bundle_contains_known_sections": False,
                "source_last_modified": None,
                "source_etag": None,
                "checked_at": checked_at,
                "error": f"URLError: {exc.reason}",
            }

        sections = _detect_known_sections(homepage.text)
        return {
            "source": "spp_gov_cn",
            "homepage_url": homepage_url,
            "final_url": homepage.url,
            "status_code": homepage.status_code,
            "title": _extract_title(homepage.text),
            "page_shape": _detect_page_shape(
                homepage.text, status_code=homepage.status_code
            ),
            "detected_sections": sections,
            "bundle_contains_known_sections": bool(sections),
            "source_last_modified": homepage.headers.get("Last-Modified"),
            "source_etag": homepage.headers.get("ETag"),
            "checked_at": checked_at,
        }

    def list_url(self, *, channel: str = "sfjs", page: int = 1) -> str:
        page_int = max(int(page), 1)
        index = "index" if page_int == 1 else f"index_{page_int}"
        return urljoin(
            self.base_url if self.base_url.endswith("/") else f"{self.base_url}/",
            LIST_PATH_FMT.format(channel=channel, index=index),
        )

    def detail_url(self, detail_id: str) -> str:
        normalized = _normalize_detail_id(detail_id)
        if not normalized:
            raise ValueError(f"invalid spp_gov_cn detail_id: {detail_id!r}")
        return urljoin(
            self.base_url if self.base_url.endswith("/") else f"{self.base_url}/",
            f"/{normalized}.shtml",
        )

    def search_list(
        self,
        query: str | None = None,
        *,
        channel: str = "sfjs",
        page: int = 1,
        page_size: int = 20,
    ) -> dict:
        """抓 ``/spp/{channel}/index.shtml`` 列表，返回标准 rows。

        spp 站点本身没有服务器端 ``q=`` 搜索，``query`` 在客户端按标题子串过滤。
        翻页：``page=1`` 走 ``index.shtml``；``page>=2`` 走 ``index_{N}.shtml``
        （这是站点静态页约定，无 URLScan 拦截问题）。

        Args:
            query: 标题子串过滤；为空时返回当页全部条目
            channel: 栏目 code，详见 :data:`CHANNEL_TO_LABEL`
            page: 1 起；超过实际页数会拿到 0 行
            page_size: 本期忽略（spp 列表页固定 20 条），保留参数仅用于 verify-source
        """

        if channel not in CHANNEL_TO_LABEL:
            known = ", ".join(sorted(CHANNEL_TO_LABEL))
            raise ValueError(
                f"unknown spp_gov_cn channel: {channel} (known: {known})"
            )

        page_int = max(int(page), 1)
        url = self.list_url(channel=channel, page=page_int)
        self._throttle()
        result = _fetch_text(url, timeout=self.timeout)
        rows = _parse_list_rows(result.text, base_url=self.base_url, channel=channel)
        if query:
            # 折叠空白与常见连接符后再做子串比较，应对 spp 列表标题里
            # "最高人民法院 最高人民检察院"、"最高人民法院、最高人民检察院"
            # 和无分隔形态混排，避免精确名查询假阴。
            rows = [
                row
                for row in rows
                if _title_matches_query(row.get("title"), query)
            ]

        return {
            "source": "spp_gov_cn",
            "channel": channel,
            "label": CHANNEL_TO_LABEL[channel],
            "query": query or "",
            "page": page_int,
            "page_size": page_size,
            "rows": rows,
            "url": url,
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }

    def search_all_pages(
        self,
        query: str,
        *,
        channel: str = "sfjs",
        max_pages: int = 5,
    ) -> dict:
        """Bounded client-side search across one static spp channel.

        spp.gov.cn exposes static list pages, not a reliable server-side search
        API for these channels. This helper walks ``index.shtml`` /
        ``index_N.shtml`` up to ``max_pages`` and stops on 404, preserving the
        adapter-level throttle. It is intentionally bounded for compliance.
        """

        if not query or not query.strip():
            raise ValueError("search_all_pages requires a non-empty query")

        rows: list[dict] = []
        seen_ids: set[str] = set()
        scanned_pages = 0
        max_pages_int = max(int(max_pages), 1)

        for page_num in range(1, max_pages_int + 1):
            try:
                result = self.search_list(query, channel=channel, page=page_num)
            except HTTPError as exc:
                if exc.code == 404 and page_num > 1:
                    break
                raise
            scanned_pages += 1
            for row in result["rows"]:
                if row["detail_id"] in seen_ids:
                    continue
                seen_ids.add(row["detail_id"])
                rows.append(row)

        return {
            "source": "spp_gov_cn",
            "channel": channel,
            "label": CHANNEL_TO_LABEL[channel],
            "query": query,
            "scanned_pages": scanned_pages,
            "max_pages": max_pages_int,
            "rows": rows,
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }

    def cross_search(
        self,
        query: str,
        *,
        channels: tuple[str, ...] = ("sfjs", "gfwj", "nbgz"),
        max_pages_per_channel: int = 5,
    ) -> dict:
        """Bounded search across multiple spp channels.

        This is the public adapter primitive used by ``fetch`` when the default
        ``sfjs`` first page misses older or policy documents. It deduplicates by
        ``detail_id`` and reports per-channel scan cost for diagnostics.
        """

        if not query or not query.strip():
            raise ValueError("cross_search requires a non-empty query")

        clean_channels: list[str] = []
        for channel in channels:
            if channel not in CHANNEL_TO_LABEL:
                known = ", ".join(sorted(CHANNEL_TO_LABEL))
                raise ValueError(
                    f"unknown spp_gov_cn channel: {channel} (known: {known})"
                )
            if channel not in clean_channels:
                clean_channels.append(channel)

        rows: list[dict] = []
        seen_ids: set[str] = set()
        per_channel: list[dict] = []
        max_pages_int = max(int(max_pages_per_channel), 1)

        for channel in clean_channels:
            result = self.search_all_pages(
                query,
                channel=channel,
                max_pages=max_pages_int,
            )
            matched_in_channel = 0
            for row in result["rows"]:
                if row["detail_id"] in seen_ids:
                    continue
                seen_ids.add(row["detail_id"])
                rows.append(row)
                matched_in_channel += 1
            per_channel.append(
                {
                    "channel": channel,
                    "label": CHANNEL_TO_LABEL[channel],
                    "scanned_pages": result["scanned_pages"],
                    "max_pages": max_pages_int,
                    "matched": matched_in_channel,
                }
            )

        return {
            "source": "spp_gov_cn",
            "query": query,
            "channels": clean_channels,
            "max_pages_per_channel": max_pages_int,
            "rows": rows,
            "per_channel": per_channel,
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }

    def fetch_detail(self, detail_id: str) -> dict:
        """抓详情页并切出 ``<div id="fontzoom">`` 内的正文 HTML。"""

        normalized = _normalize_detail_id(detail_id)
        if not normalized:
            raise ValueError(f"invalid spp_gov_cn detail_id: {detail_id!r}")

        url = self.detail_url(normalized)
        self._throttle()
        result = _fetch_text(url, timeout=self.timeout)
        raw_title = _extract_title(result.text)
        title = _strip_title_suffix(raw_title or "")
        content_html = _extract_content_html(result.text)
        return {
            "source": "spp_gov_cn",
            "detail_id": normalized,
            "url": result.url,
            "raw_title": raw_title,
            "title": title or normalized,
            "content_html": content_html,
            "source_last_modified": result.headers.get("Last-Modified"),
            "source_etag": result.headers.get("ETag"),
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }

    def build_law_payload(
        self,
        detail_id: str,
        *,
        search_row: dict | None = None,
        detail: dict | None = None,
    ) -> dict:
        """把 fetch_detail 的结果 + channel 提示拼成 chinalaw 标准载荷。

        与 court_gongbao 一致复用 ``cleaning.canonicalize(source_kind="markdown")``，
        共享"第N条"切分逻辑；level / issuing_body / document_number 三项启发
        式独立。
        """

        normalized = _normalize_detail_id(detail_id) or detail_id
        detail = detail or self.fetch_detail(normalized)
        title = detail.get("title") or normalized
        text = _html_to_text(detail.get("content_html") or "")
        if not text.strip():
            raise ValueError(
                f"spp_gov_cn detail {normalized} produced empty article text"
            )

        channel = (search_row or {}).get("channel")
        level = _infer_level(channel, title)
        short_title = _infer_short_title(title)
        document_number = cleaning.extract_document_number_from_preamble(text)
        issuing_body = _infer_issuing_body(title)
        articles = cleaning.parse_articles_from_text(text)

        if not articles and level != "guiding_case":
            return cleaning.canonicalize(
                {
                    "id": f"spp_gov_cn:{normalized}",
                    "title": title,
                    "short_title": short_title,
                    "aliases": [],
                    "level": level,
                    "status": "current",
                    "issuing_body": issuing_body,
                    "document_number": document_number,
                    "released_at": None,
                    "effective_at": None,
                    "repealed_at": None,
                    "source_url": detail.get("url"),
                    "source_name": "spp.gov.cn",
                    "source_checked_at": detail.get("checked_at"),
                    "source_hash": self._hash_text(text),
                    "articles": cleaning.single_body_article(text),
                },
                source_kind="external_json",
            )

        return cleaning.canonicalize(
            text,
            source_kind="markdown",
            id=f"spp_gov_cn:{normalized}",
            title=title,
            short_title=short_title,
            level=level,
            status="current",
            issuing_body=issuing_body,
            document_number=document_number,
            source_url=detail.get("url"),
            source_name="spp.gov.cn",
            source_checked_at=detail.get("checked_at"),
            source_hash=self._hash_text(text),
        )

    @staticmethod
    def _hash_text(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def source_hash(self, detail_id: str) -> str:
        """详情正文文本的 sha256，用于换源检测 / 增量同步。"""

        detail = self.fetch_detail(detail_id)
        return self._hash_text(_html_to_text(detail.get("content_html") or ""))


default_adapter = SppGovCnAdapter()


def probe(timeout: int = DEFAULT_TIMEOUT) -> dict:
    """模块级便利函数，与 ``flk_npc.probe`` / ``court_gongbao.probe`` 对齐。"""

    adapter = SppGovCnAdapter(timeout=timeout)
    return adapter.probe()


def search_list(query: str | None = None, **kwargs) -> dict:
    return default_adapter.search_list(query, **kwargs)


def search_all_pages(query: str, **kwargs) -> dict:
    return default_adapter.search_all_pages(query, **kwargs)


def cross_search(query: str, **kwargs) -> dict:
    return default_adapter.cross_search(query, **kwargs)


def fetch_detail(detail_id: str) -> dict:
    return default_adapter.fetch_detail(detail_id)
