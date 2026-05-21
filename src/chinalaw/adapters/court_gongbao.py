"""最高人民法院公报（gongbao.court.gov.cn）adapter。

本期同时实装：

- ``probe()``        — 站点首页健康度
- ``search_list()``  — ``/ArticleList.html?serial_no=<code>&page=N`` 列表解析；
  关键词由客户端在标题上做 substring 过滤（站点本身没有 ``q=`` 参数）
- ``fetch_detail()`` — ``/Details/<hash>.html`` 详情解析（``<div id="gb_content">`` 抽正文）
- ``build_law_payload()`` — HTML → 纯文本 → ``cleaning.canonicalize`` 的标准载荷
- ``source_hash()``  — 详情 content_html 的 sha256

Level 启发式见 ``SERIAL_NO_TO_LEVEL_HINT``：``sfjs`` 默认司法解释；``sfwj``
按标题包含「纪要」/「批复」/「复函」分流到会议纪要 / 司法解释；``al`` 视为
公报案例并归到 ``guiding_case``；其余落 ``other``。

候选源详情见 ``docs/decisions/ADR-0008-multi-source-adapters.md`` §1.1。
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
from urllib.parse import urlencode, urljoin
from urllib.request import Request, urlopen

from chinalaw import cleaning
from chinalaw.adapters import _html as _adapter_html
from chinalaw.document_numbers import extract_document_number

_extract_document_number = extract_document_number

DEFAULT_BASE_URL = "http://gongbao.court.gov.cn"
DEFAULT_TIMEOUT = 10
DEFAULT_REQUEST_INTERVAL = 0.5  # ADR-0008 §3.3：默认节流提到 500ms
# 节流硬下限。任何调用方传入 ``<= 0`` 或低于此值的间隔都会被 clamp 到
# ``MIN_REQUEST_INTERVAL``，无法在 adapter 层关闭节流。详见 docs/COMPLIANCE.md §3。
MIN_REQUEST_INTERVAL = 0.1

# UA 同时含浏览器兼容前缀（避免 ASP.NET URLScan 等遗留 WAF 在纯工具 UA 下
# 误杀）和 chinalaw-cli 标识（让站点 access log 能识别本工具）。chinalaw-cli
# 标识始终存在，详见 docs/COMPLIANCE.md §4。
TOOL_UA_TOKEN = "chinalaw-cli/0.1.0 (+https://github.com/chinalaw-cli/chinalaw-cli)"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36 "
    f"{TOOL_UA_TOKEN}"
)

LIST_PATH = "/ArticleList.html"
DETAIL_PATH_PREFIX = "/Details/"

# 公报站常见栏目入口（来自 2026-05 调研，只用于 probe 时校验"页面里出现已知栏目"）
KNOWN_SECTIONS = (
    "司法解释",
    "公报案例",
    "工作报告",
    "规范性文件",
    "公告",
)

# 已知 serial_no → 中文标签 + 默认 level 提示。``sfwj`` 在标题层再做二次启发式。
SERIAL_NO_TO_LABEL = {
    "sfjs": "司法解释",
    "sfwj": "司法文件",
    "al": "公报案例",
    "cpwsxd": "裁判文书选登",
    "flxd": "法律选登",
    "rmsx": "任免事项",
    "sftj": "司法统计",
    "wx": "文献",
}

SERIAL_NO_TO_LEVEL_HINT = {
    "sfjs": "judicial_interpretation",
    "sfwj": "judicial_policy",
    "al": "guiding_case",
}

# 公报站详情 URL 形如 ``/Details/<hash>.html``，``<hash>`` 实测是 30 位 hex
# （非标准 32/40 位摘要；接受 20-40 位区间以容错）。
DETAIL_ID_RE = re.compile(rf"{re.escape(DETAIL_PATH_PREFIX)}([0-9a-fA-F]{{20,40}})\.html")
DETAIL_ID_FULLMATCH_RE = re.compile(r"^[0-9a-fA-F]{20,40}$")
# 文号识别已上提到 ``chinalaw.document_numbers.extract_document_number``，
# adapter 不再维护本地正则；详见
# ``docs/UNIFY_DOCUMENT_NUMBER_REGEX_SPEC.md``。Module-level alias
# ``_extract_document_number`` 保留作为 adapter API（见上方 import 行 + 测试
# ``tests/test_core.py`` 的 ``court_gongbao._extract_document_number`` 断言）。
LIST_ITEM_RE = re.compile(
    r'<a\s+href="(?P<href>/Details/[0-9a-fA-F]{20,40}\.html)"[^>]*>'
    r"(?P<title>[^<]+)</a>"
    r"(?:[\s\S]{0,200}?<lable>(?P<issue>[^<]+)</lable>)?",
    re.IGNORECASE,
)
PAGE_LINK_RE = re.compile(
    r'href="/ArticleList\.html\?serial_no=[a-z]+(?:&amp;|&)page=(\d+)"',
    re.IGNORECASE,
)
# 列表页底部由 JS 注入"共 N 条"信息：``var totalCount = '861';`` 形式硬编码
# 在内联脚本里。提前解析便于 agent 早判翻页代价。
TOTAL_COUNT_RE = re.compile(r"var\s+totalCount\s*=\s*['\"]?(\d+)['\"]?", re.IGNORECASE)
GB_CONTENT_RE = re.compile(
    r'<div[^>]*id="gb_content"[^>]*>(?P<body>.*?)</div>\s*</div>\s*</div>',
    re.IGNORECASE | re.DOTALL,
)
MATCH_IGNORED_RE = re.compile(r"[\s　、，,]+")
GB_CONTENT_OPEN_ONLY_RE = re.compile(
    r'<div[^>]*id="gb_content"[^>]*>(?P<body>.*)',
    re.IGNORECASE | re.DOTALL,
)


@dataclass
class FetchResult:
    url: str
    status_code: int
    headers: Mapping[str, str]
    text: str


def _build_request(url: str, *, data: bytes | None = None) -> Request:
    headers = {
        "User-Agent": DEFAULT_USER_AGENT,
        "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.5",
    }
    method = "GET"
    if data is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        # 公报站走 ASP.NET unobtrusive-ajax；带上这个头可以避免 URLScan 的某些
        # 启发式拦截，同时和页面自带 JS 行为保持一致
        headers["X-Requested-With"] = "XMLHttpRequest"
        method = "POST"
    return Request(url, data=data, headers=headers, method=method)


def _fetch_text(
    url: str,
    *,
    data: bytes | None = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> FetchResult:
    req = _build_request(url, data=data)
    with urlopen(req, timeout=timeout) as resp:
        charset = resp.headers.get_content_charset() or "utf-8"
        body = resp.read().decode(charset, errors="replace")
        return FetchResult(
            url=resp.geturl(),
            status_code=resp.getcode(),
            headers=dict(resp.headers.items()),
            text=body,
        )


def _detect_known_sections(html: str) -> list[str]:
    """识别页面中出现的已知栏目名（仅用于 probe 健康度判断）。"""
    return [section for section in KNOWN_SECTIONS if section in html]


def _detect_page_shape(html: str, *, status_code: int) -> str:
    """简单的页面形态识别。

    - ``ok``：状态 200 且能识别到 ASP.NET 标记或文档详情结构
    - ``error``：状态非 2xx
    - ``unknown``：其它
    """
    if not (200 <= status_code < 300):
        return "error"
    lowered = html.lower()
    if "asp.net" in lowered or 'name="generator"' in lowered or "<title>" in lowered:
        return "ok"
    return "unknown"


def _parse_list_rows(
    html: str,
    *,
    base_url: str,
    serial_no: str | None = None,
) -> list[dict]:
    rows: list[dict] = []
    for match in LIST_ITEM_RE.finditer(html):
        href = match.group("href").strip()
        title = unescape(match.group("title")).replace("　", " ").strip()
        issue = match.group("issue")
        detail_id_match = DETAIL_ID_RE.search(href)
        if not detail_id_match:
            continue
        detail_id = detail_id_match.group(1)
        rows.append(
            {
                "detail_id": detail_id,
                "serial_no": serial_no,
                "title": title,
                "issue": (issue or "").strip() or None,
                "url": urljoin(base_url, href),
                "status": "current",
            }
        )
    return rows


def _parse_total_pages(html: str) -> int | None:
    """从分页链接里推断总页数。

    公报站底部分页只暴露 ``page=2`` 与 ``page=<last>`` 两个常见链接；
    这里取 max。如果完全没有分页链接，返回 ``None``。
    """

    pages = [int(match.group(1)) for match in PAGE_LINK_RE.finditer(html)]
    return max(pages) if pages else None


def _parse_total_count(html: str) -> int | None:
    """从内联 ``var totalCount = 'N';`` 抽栏目内的总条数。"""

    match = TOTAL_COUNT_RE.search(html)
    return int(match.group(1)) if match else None


def _match_text(raw: str | None) -> str:
    return MATCH_IGNORED_RE.sub("", raw or "")


def _title_matches_query(title: str | None, query: str | None) -> bool:
    needle = _match_text(query)
    if not needle:
        return True
    return needle in _match_text(title)


def _extract_content_html(html: str) -> str:
    """从详情页抽 ``<div id="gb_content">`` 的内部 HTML。

    页面里 ``gb_content`` 之后还有若干嵌套 div 闭合，这里走两步：
    先尝试找 ``</div></div></div>`` 三层闭合的精确边界，失败则退化为
    "open 后到 ``</body>`` / ``</main_box>``" 之前的子串。
    """

    match = GB_CONTENT_RE.search(html)
    if match:
        return match.group("body").strip()

    fallback = GB_CONTENT_OPEN_ONLY_RE.search(html)
    if not fallback:
        return ""
    body = fallback.group("body")
    # 截到第一个看起来像页脚 / 包装结束的标记
    for sentinel in ("<!-- foot", '<div class="footer', "</body>"):
        idx = body.lower().find(sentinel)
        if idx >= 0:
            body = body[:idx]
            break
    return body.strip()


# 公报站 ``<title>`` 后缀清单（adapter 私有数据）。共用 strip 算法在
# ``chinalaw.adapters._html.strip_known_title_suffix``，详见
# docs/ADAPTER_HTML_HELPERS_SPEC.md。
_TITLE_SUFFIXES: tuple[str, ...] = (
    " - 中华人民共和国最高人民法院公报",
    "- 中华人民共和国最高人民法院公报",
    " — 中华人民共和国最高人民法院公报",
    "—中华人民共和国最高人民法院公报",
)


# Module-level alias 保留 ``court_gongbao._extract_title`` 接口（既有测试 +
# adapter 内部 fetch_detail / probe 仍用此名）。
_extract_title = _adapter_html.html_extract_title


def _strip_title_suffix(raw_title: str) -> str:
    """剥离公报站统一的标题后缀。

    页面 ``<title>`` 形如 ``X - 中华人民共和国最高人民法院公报``。
    """

    return _adapter_html.strip_known_title_suffix(raw_title, _TITLE_SUFFIXES)


def _html_to_text(content_html: str) -> str:
    """把抽出的内容 HTML 转成纯文本，保留段落分隔。

    实装委托至 ``chinalaw.adapters._html.html_to_text``——adapter 公共能力，
    详见 docs/ADAPTER_HTML_HELPERS_SPEC.md。Module-level 名字保留供既有测试
    与 adapter 内部 build_law_payload 调用。
    """

    return _adapter_html.html_to_text(content_html)


def _infer_level(serial_no: str | None, title: str) -> str:
    base = SERIAL_NO_TO_LEVEL_HINT.get((serial_no or "").lower(), "other")
    if "纪要" in title:
        return "judicial_meeting_minutes"
    if "批复" in title or "复函" in title or "解释" in title:
        return "judicial_interpretation"
    if "指导性案例" in title or "公报案例" in title:
        return "guiding_case"
    if (serial_no or "").lower() == "sfwj":
        return "judicial_policy"
    return base


# 公报标题前缀清单（adapter 私有数据）。共用算法在
# ``chinalaw.adapters._html.infer_short_title``。
_ISSUER_PREFIXES: tuple[str, ...] = (
    "最高人民法院 最高人民检察院 ",
    "最高人民法院 ",
    "最高人民法院",
    "最高人民检察院 ",
    "最高人民检察院",
)


def _infer_short_title(title: str) -> str | None:
    """公报标题常见前缀剥离 + alias 优先短名。

    实装委托至 ``chinalaw.adapters._html.infer_short_title``——共用算法
    （preferred_short_title 优先 → site prefix 剥离 → cleaning fallback）。
    Module-level 名字保留供既有测试与 build_law_payload 调用。
    """

    return _adapter_html.infer_short_title(title, site_prefixes=_ISSUER_PREFIXES)


@dataclass
class CourtGongbaoAdapter:
    """最高人民法院公报站 adapter。

    覆盖文件类别：会议纪要（如九民纪要）/ 最高法批复 / 复函 / 司法解释 /
    公报案例 / 工作报告。详见 ADR-0008 §1.1。
    """

    base_url: str = DEFAULT_BASE_URL
    timeout: int = DEFAULT_TIMEOUT
    request_interval: float = DEFAULT_REQUEST_INTERVAL
    _last_request_at: float = field(default=0.0, repr=False)

    def _throttle(self) -> None:
        # 节流硬下限：调用方即使传 ``request_interval=0`` 也会被 clamp 到
        # ``MIN_REQUEST_INTERVAL``。详见 docs/COMPLIANCE.md §3。
        interval = max(float(self.request_interval or 0), MIN_REQUEST_INTERVAL)
        now = time.monotonic()
        wait = interval - (now - self._last_request_at)
        if wait > 0:
            time.sleep(wait)
        self._last_request_at = time.monotonic()

    def probe(self) -> dict:
        """探测公报站首页，返回标准化的 probe 报告。"""

        self._throttle()
        checked_at = datetime.now(timezone.utc).isoformat()
        homepage_url = self.base_url if self.base_url.endswith("/") else f"{self.base_url}/"
        try:
            homepage = _fetch_text(homepage_url, timeout=self.timeout)
        except HTTPError as exc:
            return {
                "source": "court_gongbao",
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
                "source": "court_gongbao",
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
            "source": "court_gongbao",
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

    def list_url(self, *, serial_no: str | None = None, page: int | None = None) -> str:
        """列表页 URL。

        实测公报站的真正翻页是 POST + form body（jQuery unobtrusive-ajax，被
        ASP.NET URLScan 拦截 GET ``?page=N`` 形态），见
        :meth:`search_list`。本方法仍保留，便于人类直接打开 page=1（GET 形式
        不会被拦），构造形式：``/ArticleList.html?serial_no=<code>``。
        """

        params: dict[str, str] = {}
        if serial_no:
            params["serial_no"] = serial_no
        if page and page > 1:
            params["page"] = str(page)
        suffix = f"?{urlencode(params)}" if params else ""
        return urljoin(
            self.base_url if self.base_url.endswith("/") else f"{self.base_url}/",
            f"{LIST_PATH}{suffix}",
        )

    def detail_url(self, detail_id: str) -> str:
        return urljoin(
            self.base_url if self.base_url.endswith("/") else f"{self.base_url}/",
            f"{DETAIL_PATH_PREFIX}{detail_id}.html",
        )

    def search_list(
        self,
        query: str | None = None,
        *,
        serial_no: str = "sfjs",
        page: int = 1,
        page_size: int = 30,
    ) -> dict:
        """抓 ``/ArticleList.html`` 列表，返回标准 rows。

        协议细节（实测，2026-05）：

        - 分页用 ``POST`` + form body ``serial_no=<code>&page=<N>``；
          GET ``?serial_no=...&page=N`` 会被 ASP.NET URLScan 拦截到
          ``/Rejected-By-UrlScan`` 404，根本不发 page=2 之后的内容
        - 每页固定 30 条；总条数由内联 ``var totalCount`` 提供
        - ``query`` 在客户端按标题子串过滤（公报站本身没有 ``q=`` 参数）

        Args:
            query: 标题子串过滤；为空时返回当页全部条目
            serial_no: 栏目 code，详见 :data:`SERIAL_NO_TO_LABEL`
            page: 1 起；超过实际页数会拿到空 rows
            page_size: 本期忽略（公报站每页固定 30 条），保留参数仅用于报告
        """

        if serial_no not in SERIAL_NO_TO_LABEL:
            known = ", ".join(sorted(SERIAL_NO_TO_LABEL))
            raise ValueError(
                f"unknown court_gongbao serial_no: {serial_no} (known: {known})"
            )

        page_int = max(int(page), 1)
        list_endpoint = urljoin(
            self.base_url if self.base_url.endswith("/") else f"{self.base_url}/",
            LIST_PATH,
        )

        # 协议分流：
        # - page=1 走 GET，能拿到完整页 footer（pagination 链接 + totalCount）
        # - page>=2 必须 POST + form body，否则 ASP.NET URLScan 会拦截带 ``&page=``
        #   的 GET，重定向到 ``/Rejected-By-UrlScan``（404）
        self._throttle()
        if page_int <= 1:
            url = self.list_url(serial_no=serial_no)
            result = _fetch_text(url, timeout=self.timeout)
        else:
            form_body = urlencode(
                {"serial_no": serial_no, "page": str(page_int)}
            ).encode("utf-8")
            result = _fetch_text(list_endpoint, data=form_body, timeout=self.timeout)
        rows = _parse_list_rows(result.text, base_url=self.base_url, serial_no=serial_no)
        if query:
            rows = [
                row
                for row in rows
                if _title_matches_query(row.get("title"), query)
            ]

        total_pages = _parse_total_pages(result.text)
        total_count = _parse_total_count(result.text)
        return {
            "source": "court_gongbao",
            "serial_no": serial_no,
            "label": SERIAL_NO_TO_LABEL[serial_no],
            "query": query or "",
            "page": page_int,
            "page_size": page_size,
            "total_pages": total_pages,
            "total_count": total_count,
            "rows": rows,
            # 给人类一个能直接打开的 GET 链接（page>1 在浏览器里也只能点站内分页
            # 触发 POST，但 GET 链接更方便日志贴）
            "url": self.list_url(serial_no=serial_no, page=page_int),
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }

    def search_all_pages(
        self,
        query: str,
        *,
        serial_no: str = "sfjs",
        max_pages: int = 30,
    ) -> dict:
        """跨页关键词搜索，命中即整理输出。

        公报站没有服务器端 ``q=`` 搜索，标题层关键词命中只能客户端过滤。本
        方法把 :meth:`search_list` 翻 ``max_pages`` 页（不超过实际 ``total_pages``）
        合并去重；用于九民纪要这种藏在 sfwj 第 N 页的场景。

        Args:
            query: 必填；空字符串没有意义
            serial_no: 栏目 code
            max_pages: 上限页数；默认 30 页（约 900 条）
        """

        if not query or not query.strip():
            raise ValueError("search_all_pages requires a non-empty query")

        first = self.search_list(query, serial_no=serial_no, page=1)
        rows: list[dict] = list(first["rows"])
        seen_ids = {row["detail_id"] for row in rows}
        total_pages = first.get("total_pages") or 1
        last_page = min(int(max_pages), int(total_pages))
        scanned_pages = 1

        for page_num in range(2, last_page + 1):
            extra = self.search_list(query, serial_no=serial_no, page=page_num)
            scanned_pages += 1
            for row in extra["rows"]:
                if row["detail_id"] in seen_ids:
                    continue
                seen_ids.add(row["detail_id"])
                rows.append(row)

        return {
            "source": "court_gongbao",
            "serial_no": serial_no,
            "label": SERIAL_NO_TO_LABEL[serial_no],
            "query": query,
            "scanned_pages": scanned_pages,
            "max_pages": max_pages,
            "total_pages": total_pages,
            "total_count": first.get("total_count"),
            "rows": rows,
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }

    def cross_search(
        self,
        query: str,
        *,
        serials: tuple[str, ...] = ("sfjs", "sfwj"),
        max_pages_per_serial: int = 5,
    ) -> dict:
        """跨多个栏目搜索 query，按 ``detail_id`` 全局去重。

        court_gongbao 没有"全站搜索"入口，平时要 agent 先决定 serial_no（``sfjs``
        / ``sfwj`` / ``al``）再搜。``cross_search`` 把这一步打包：默认覆盖司法
        解释 + 司法文件两个 serial（涵盖大多数解释 / 纪要 / 批复 / 复函），
        每个 serial 最多翻 ``max_pages_per_serial`` 页。

        agent 用法：
        - ``chinalaw fetch "破产纪要" --source court_gongbao`` 走默认 cross_search
          后，纪要类（在 sfwj）和解释类（在 sfjs）都会被覆盖
        - ``cross_search("会议纪要", serials=("sfwj",), max_pages_per_serial=10)``
          可单独限定 sfwj 的 10 页（约 300 条）

        节流：本方法不开并发，所有请求按 ``self._throttle()`` 串行；
        2 个 serial × 默认 5 页 = 10 个请求 ≈ 5 秒（``request_interval=0.5``）。
        ``max_pages_per_serial`` 不超过实际 ``total_pages``，超过自动 cap。
        """

        if not query or not query.strip():
            raise ValueError("cross_search requires a non-empty query")

        clean_serials: list[str] = []
        for serial_no in serials:
            if serial_no not in SERIAL_NO_TO_LABEL:
                known = ", ".join(sorted(SERIAL_NO_TO_LABEL))
                raise ValueError(
                    f"unknown court_gongbao serial_no: {serial_no} (known: {known})"
                )
            if serial_no not in clean_serials:
                clean_serials.append(serial_no)

        rows: list[dict] = []
        seen_ids: set[str] = set()
        per_serial: list[dict] = []
        max_pages_int = max(int(max_pages_per_serial), 1)

        for serial_no in clean_serials:
            matched_in_serial = 0
            first = self.search_list(query, serial_no=serial_no, page=1)
            for row in first["rows"]:
                if row["detail_id"] in seen_ids:
                    continue
                seen_ids.add(row["detail_id"])
                rows.append(row)
                matched_in_serial += 1

            total_pages = int(first.get("total_pages") or 1)
            last_page = min(max_pages_int, total_pages)
            scanned_pages = 1

            for page_num in range(2, last_page + 1):
                extra = self.search_list(query, serial_no=serial_no, page=page_num)
                scanned_pages += 1
                for row in extra["rows"]:
                    if row["detail_id"] in seen_ids:
                        continue
                    seen_ids.add(row["detail_id"])
                    rows.append(row)
                    matched_in_serial += 1

            per_serial.append(
                {
                    "serial_no": serial_no,
                    "label": SERIAL_NO_TO_LABEL.get(serial_no),
                    "scanned_pages": scanned_pages,
                    "max_pages": max_pages_int,
                    "total_pages": total_pages,
                    "total_count": first.get("total_count"),
                    "matched": matched_in_serial,
                }
            )

        return {
            "source": "court_gongbao",
            "query": query,
            "serials": clean_serials,
            "max_pages_per_serial": max_pages_int,
            "rows": rows,
            "per_serial": per_serial,
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }

    def fetch_detail(self, detail_id: str) -> dict:
        """抓详情页并切出 ``<div id="gb_content">`` 内的正文 HTML。"""

        if not DETAIL_ID_FULLMATCH_RE.match((detail_id or "").strip()):
            raise ValueError(f"invalid court_gongbao detail_id: {detail_id!r}")

        url = self.detail_url(detail_id)
        self._throttle()
        result = _fetch_text(url, timeout=self.timeout)
        raw_title = _extract_title(result.text)
        title = _strip_title_suffix(raw_title or "")
        content_html = _extract_content_html(result.text)
        return {
            "source": "court_gongbao",
            "detail_id": detail_id,
            "url": result.url,
            "raw_title": raw_title,
            "title": title or detail_id,
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
        """把 fetch_detail 的结果 + serial_no 提示拼成 chinalaw 标准载荷。"""

        detail = detail or self.fetch_detail(detail_id)
        title = detail.get("title") or detail_id
        text = _html_to_text(detail.get("content_html") or "")
        if not text.strip():
            raise ValueError(
                f"court_gongbao detail {detail_id} produced empty article text"
            )

        serial_no = (search_row or {}).get("serial_no")
        level = _infer_level(serial_no, title)
        short_title = _infer_short_title(title)
        document_number = cleaning.extract_document_number_from_preamble(text)

        return cleaning.canonicalize(
            text,
            source_kind="markdown",
            id=f"court_gongbao:{detail_id}",
            title=title,
            short_title=short_title,
            level=level,
            status="current",
            issuing_body="最高人民法院",
            document_number=document_number,
            source_url=detail.get("url"),
            source_name="gongbao.court.gov.cn",
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


default_adapter = CourtGongbaoAdapter()


def probe(timeout: int = DEFAULT_TIMEOUT) -> dict:
    """模块级便利函数，与 ``flk_npc.probe`` 形态对齐。"""
    adapter = CourtGongbaoAdapter(timeout=timeout)
    return adapter.probe()


def search_list(query: str | None = None, **kwargs) -> dict:
    return default_adapter.search_list(query, **kwargs)


def search_all_pages(query: str, **kwargs) -> dict:
    return default_adapter.search_all_pages(query, **kwargs)


def cross_search(query: str, **kwargs) -> dict:
    return default_adapter.cross_search(query, **kwargs)


def fetch_detail(detail_id: str) -> dict:
    return default_adapter.fetch_detail(detail_id)
