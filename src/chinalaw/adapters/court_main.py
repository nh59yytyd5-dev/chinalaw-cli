"""最高人民法院主站（www.court.gov.cn）adapter。

本源不同于 ``court_gongbao``：

- ``court_gongbao`` 接最高人民法院公报站，适合公报归档；
- ``court_main`` 接最高法政务主站，适合主站发布但公报站未覆盖的解释、
  通知、司法政策和发布材料。

主站当前可用形态（2026-05 实测）：

- ``/search.html?content=<query>`` 返回静态搜索结果；
- 详情页通常是 ``/<channel>/xiangqing/<id>.html``；
- 正文容器是 ``<div class="txt_txt" ...>``。

本 adapter 不做无界全站爬取，不把新闻稿自动升级为司法解释；level 仅按
标题 / 正文启发式保守推断。
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
from urllib.parse import urlencode, urljoin, urlparse
from urllib.request import Request, urlopen

from chinalaw import USER_AGENT_TOKEN, cleaning
from chinalaw.adapters import _html as _adapter_html

DEFAULT_BASE_URL = "https://www.court.gov.cn"
DEFAULT_TIMEOUT = 10
DEFAULT_REQUEST_INTERVAL = 0.5
MIN_REQUEST_INTERVAL = 0.1

TOOL_UA_TOKEN = USER_AGENT_TOKEN
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36 "
    f"{TOOL_UA_TOKEN}"
)

KNOWN_SECTIONS = (
    "权威发布",
    "司法解释",
    "司法文件",
    "通知",
    "法院资讯",
    "审判业务",
)

SEARCH_PATH = "/search.html"
DETAIL_ID_RE = re.compile(
    r"/?(?P<path>[a-z]+/xiangqing/(?P<num>\d+))\.html",
    re.IGNORECASE,
)
DETAIL_ID_FULLMATCH_RE = re.compile(
    r"^(?:[a-z]+/xiangqing/)?\d+$",
    re.IGNORECASE,
)
DETAIL_URL_FALLBACK_CHANNELS = ("zixun", "fabu", "shenpan", "jianshe")

SEARCH_ITEM_RE = re.compile(
    r"<li[^>]*>\s*"
    r"<a\s+[^>]*href=[\"'](?P<href>[^\"']+/xiangqing/\d+\.html)[\"'][^>]*>"
    r"(?P<title>.*?)</a>"
    r"(?P<tail>.*?)(?:</li>|$)",
    re.IGNORECASE | re.DOTALL,
)
DATE_RE = re.compile(r'<i[^>]*class=["\']date["\'][^>]*>(?P<date>[^<]+)</i>')
SEARCH_TOTAL_RE = re.compile(r"为您找到相关结果约(?P<count>\d+)个")
LIST_TOTAL_RE = re.compile(
    r"共\s*<span[^>]*class=[\"']num[\"'][^>]*>(?P<count>\d+)</span>\s*篇文章",
    re.IGNORECASE,
)
PAGE_LINK_RE = re.compile(
    r"/[a-z]+/gengduo/\d+_(?P<page>\d+)\.html",
    re.IGNORECASE,
)

DETAIL_TITLE_RE = re.compile(
    r'<div[^>]*class=["\']title["\'][^>]*>(?P<title>.*?)</div>',
    re.IGNORECASE | re.DOTALL,
)
SOURCE_RE = re.compile(r"<li[^>]*>\s*来源：(?P<source>.*?)</li>", re.DOTALL)
PUBLISHED_RE = re.compile(r"<li[^>]*>\s*发布时间：(?P<date>.*?)</li>", re.DOTALL)
TXT_OPEN_RE = re.compile(
    r'<div[^>]*class=["\'][^"\']*\btxt_txt\b[^"\']*["\'][^>]*>(?P<body>.*)',
    re.IGNORECASE | re.DOTALL,
)
TITLE_SUFFIXES = (
    " - 中华人民共和国最高人民法院",
    "- 中华人民共和国最高人民法院",
)
RELATED_LINK_RE = re.compile(
    r"(<p[^>]*>\s*)?<strong>\s*　?\s*相关链接[:：].*",
    re.IGNORECASE | re.DOTALL,
)
EFFECTIVE_DATE_RE = re.compile(
    r"自(?P<year>\d{4})年(?P<month>\d{1,2})月(?P<day>\d{1,2})日起施行"
)
BOOK_TITLE_RE = re.compile(r"《(?P<title>[^》]{4,120})》")
NORMATIVE_TITLE_TOKENS = (
    "解释",
    "规定",
    "批复",
    "意见",
    "纪要",
    "通知",
    "决定",
    "办法",
    "规则",
)
UNNUMBERED_BODY_TITLE_TOKENS = (
    "批复",
    "复函",
    "答复",
    "函",
)
PRESS_TITLE_HINTS = (
    "发布",
    "公布",
    "印发",
    "解读",
    "答记者问",
    "新闻发布会",
    "负责人就",
)

ISSUER_PREFIXES = (
    "最高人民法院、最高人民检察院、公安部 ",
    "最高人民法院、最高人民检察院 ",
    "最高人民法院 最高人民检察院 ",
    "最高人民法院 ",
    "最高人民法院",
)


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


_extract_title = _adapter_html.html_extract_title


def _html_to_text(content_html: str) -> str:
    return _adapter_html.html_to_text(content_html)


def _strip_title_suffix(raw_title: str) -> str:
    return _adapter_html.strip_known_title_suffix(raw_title, TITLE_SUFFIXES)


def _detect_known_sections(html: str) -> list[str]:
    return [section for section in KNOWN_SECTIONS if section in html]


def _detect_page_shape(html: str, *, status_code: int) -> str:
    if not (200 <= status_code < 300):
        return "error"
    if "抱歉，找不到您要的页面" in html:
        return "not_found"
    lowered = html.lower()
    if "<title>" in lowered and ("court.gov.cn" in lowered or "最高人民法院" in html):
        return "ok"
    return "unknown"


def _is_not_found(html: str) -> bool:
    return "抱歉，找不到您要的页面" in html or "哎呀,出错了" in html


def _clean_text_fragment(raw: str | None) -> str:
    return re.sub(r"\s+", " ", _html_to_text(raw or "")).strip()


def _normalize_detail_id(raw: str | None) -> str | None:
    """Normalize URL / path / numeric id into ``channel/xiangqing/id``."""

    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    if "://" in text:
        parsed = urlparse(text)
        text = parsed.path or ""
    text = text.split("#", 1)[0].split("?", 1)[0]
    text = text.strip()
    match = DETAIL_ID_RE.search(text)
    if match:
        return match.group("path").strip("/")
    if text.endswith(".html"):
        text = text[: -len(".html")]
    text = text.strip("/")
    if text.isdigit():
        return f"zixun/xiangqing/{text}"
    if DETAIL_ID_FULLMATCH_RE.match(text):
        return text
    return None


def _numeric_detail_id(detail_id: str) -> str | None:
    match = re.search(r"/(\d+)$", detail_id)
    return match.group(1) if match else (detail_id if detail_id.isdigit() else None)


def _detail_id_candidates(detail_id: str) -> list[str]:
    normalized = _normalize_detail_id(detail_id)
    if not normalized:
        return []
    number = _numeric_detail_id(normalized)
    candidates = [normalized]
    if number:
        for channel in DETAIL_URL_FALLBACK_CHANNELS:
            candidate = f"{channel}/xiangqing/{number}"
            if candidate not in candidates:
                candidates.append(candidate)
    return candidates


def _parse_rows(html: str, *, base_url: str, query: str | None = None) -> list[dict]:
    rows: list[dict] = []
    seen: set[str] = set()
    for match in SEARCH_ITEM_RE.finditer(html):
        href = unescape(match.group("href")).strip()
        detail_id = _normalize_detail_id(href)
        if not detail_id or detail_id in seen:
            continue
        title = _clean_text_fragment(match.group("title"))
        if not title:
            title_attr = re.search(r'title=["\'](?P<title>[^"\']+)["\']', match.group(0))
            title = _clean_text_fragment(title_attr.group("title") if title_attr else "")
        if query and query.strip() and query.strip() not in title:
            # /search.html normally filters server-side; keep this defensive
            # filter only for callers that feed pre-fetched list HTML.
            tail_text = _clean_text_fragment(match.group("tail"))
            if query.strip() not in tail_text:
                continue
        date_match = DATE_RE.search(match.group("tail") or "")
        date_text = _clean_text_fragment(date_match.group("date") if date_match else "")
        rows.append(
            {
                "detail_id": detail_id,
                "title": title,
                "released_at": _date_part(date_text),
                "published_at": date_text or None,
                "url": urljoin(base_url, f"/{detail_id}.html"),
                "status": "current",
            }
        )
        seen.add(detail_id)
    return rows


def _parse_total_count(html: str) -> int | None:
    for pattern in (SEARCH_TOTAL_RE, LIST_TOTAL_RE):
        match = pattern.search(html)
        if match:
            return int(match.group("count"))
    return None


def _parse_total_pages(html: str) -> int | None:
    pages = [int(match.group("page")) for match in PAGE_LINK_RE.finditer(html)]
    return max(pages) if pages else None


def _extract_detail_title(html: str) -> str | None:
    match = DETAIL_TITLE_RE.search(html)
    if not match:
        return None
    return _clean_text_fragment(match.group("title"))


def _extract_source_name(html: str) -> str | None:
    match = SOURCE_RE.search(html)
    return _clean_text_fragment(match.group("source")) if match else None


def _extract_published_at(html: str) -> str | None:
    match = PUBLISHED_RE.search(html)
    return _clean_text_fragment(match.group("date")) if match else None


def _extract_content_html(html: str) -> str:
    match = TXT_OPEN_RE.search(html)
    if not match:
        return ""
    body = match.group("body")
    for sentinel in (
        '<div class="txt_etr"',
        "<!--分享-->",
        '<div class="share"',
        "<!--法院相关链接结束-->",
        "</body>",
    ):
        idx = body.find(sentinel)
        if idx >= 0:
            body = body[:idx]
            break
    body = RELATED_LINK_RE.sub("", body)
    return body.strip()


def _date_part(raw: str | None) -> str | None:
    if not raw:
        return None
    match = re.search(r"\d{4}-\d{1,2}-\d{1,2}", raw)
    if not match:
        return None
    year, month, day = match.group(0).split("-")
    return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"


def _infer_effective_at(text: str) -> str | None:
    match = EFFECTIVE_DATE_RE.search(text or "")
    if not match:
        return None
    return (
        f"{int(match.group('year')):04d}-"
        f"{int(match.group('month')):02d}-"
        f"{int(match.group('day')):02d}"
    )


def _infer_issuing_body(title: str, text: str) -> str:
    marker = f"{title}\n{text}"
    compact = re.sub(r"\s+", "", marker)
    if (
        "最高人民法院、最高人民检察院、公安部" in marker
        or "最高人民法院最高人民检察院公安部" in compact
        or "两高一部" in marker
    ):
        return "最高人民法院、最高人民检察院、公安部"
    if (
        "最高人民法院、最高人民检察院" in marker
        or "最高人民法院最高人民检察院" in compact
        or "两高" in marker
    ):
        return "最高人民法院、最高人民检察院"
    return "最高人民法院"


def _infer_level(title: str, text: str) -> str:
    if "纪要" in title:
        return "judicial_meeting_minutes"
    if "解释" in title or "批复" in title:
        return "judicial_interpretation"
    if "规定" in title and ("适用法律" in title or "案件" in title):
        return "judicial_interpretation"
    if any(token in title for token in ("指导意见", "意见", "通知", "工作指引", "实施方案")):
        return "judicial_policy"
    if "指导性案例" in title or "典型案例" in title:
        return "guiding_case"

    # Detail pages often start with press-release context. Use the body only as
    # a fallback after title-level signals, otherwise phrases like "典型案例" in
    # the news intro can misclassify a judicial interpretation.
    marker = text[:500]
    if "纪要" in marker:
        return "judicial_meeting_minutes"
    if "解释" in marker or "批复" in marker:
        return "judicial_interpretation"
    if "规定" in marker and ("适用法律" in marker or "案件" in marker):
        return "judicial_interpretation"
    if any(token in marker for token in ("指导意见", "意见", "通知", "工作指引", "实施方案")):
        return "judicial_policy"
    if "指导性案例" in marker or "典型案例" in marker:
        return "guiding_case"
    return "other"


def _infer_short_title(title: str) -> str | None:
    return _adapter_html.infer_short_title(title, site_prefixes=ISSUER_PREFIXES)


def _book_titles(text: str) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for match in BOOK_TITLE_RE.finditer(text or ""):
        title = match.group("title").strip()
        if title and title not in seen:
            seen.add(title)
            result.append(title)
    return result


def _compact_title(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def _looks_like_normative_title(title: str) -> bool:
    """Return true when the page title is itself a normative document title.

    Supreme Court pages mix two shapes:

    - normative title: ``最高人民法院关于适用《X》的解释``;
    - press title: ``最高法发布《最高人民法院关于...的解释》``.

    Only the latter should be collapsed to the quoted title.
    """

    compact = _compact_title(title)
    if not compact or not any(token in compact for token in NORMATIVE_TITLE_TOKENS):
        return False

    book_start = compact.find("《")
    if book_start > 0:
        prefix = compact[:book_start]
        if any(hint in prefix for hint in PRESS_TITLE_HINTS):
            return False

    if compact.startswith("关于"):
        return True
    if "会议纪要" in compact and not any(hint in compact[:6] for hint in PRESS_TITLE_HINTS):
        return True

    for issuer in ISSUER_PREFIXES:
        issuer_compact = _compact_title(issuer)
        if compact.startswith(f"{issuer_compact}关于"):
            return True
        if compact.startswith(issuer_compact) and compact[len(issuer_compact):].startswith("关于"):
            return True

    return False


def _infer_document_title(page_title: str, text: str) -> str:
    """Convert press-release titles into the embedded normative title."""

    title = _strip_title_suffix(page_title).strip()
    if _looks_like_normative_title(title):
        return title

    books = _book_titles(title)
    if not books:
        books = _book_titles(text[:1200])
    if not books:
        return title

    # Prefer book titles that already carry the issuing body or look normative.
    chosen = next((b for b in books if b.startswith("最高人民法院")), None)
    if chosen:
        return chosen
    chosen = next(
        (
            b
            for b in books
            if any(token in b for token in ("解释", "规定", "批复", "通知", "意见", "方案"))
        ),
        books[0],
    )
    if chosen.startswith("关于"):
        if "两高一部" in title:
            return f"最高人民法院、最高人民检察院、公安部{chosen}"
        if "两高" in title:
            return f"最高人民法院、最高人民检察院{chosen}"
        if "最高法" in title or "最高人民法院" in title:
            return f"最高人民法院{chosen}"
    return chosen


def _should_emit_single_body_article(title: str, text: str, articles: list[dict]) -> bool:
    """Detect unnumbered normative documents whose title references another law article.

    Some Supreme Court replies are not organized as "第一条、第二条"; their title may
    reference another statute's "第N条". If the HTML line breaks put that fragment at
    line start, the generic article parser can fabricate an Article N for this reply.
    Treat these documents as one synthetic body item unless a real multi-article
    sequence is present.
    """

    compact_title = _compact_title(title)
    if not compact_title or not any(
        token in compact_title for token in UNNUMBERED_BODY_TITLE_TOKENS
    ):
        return False

    if not articles:
        return True
    if len(articles) > 1:
        return False

    number_display = _compact_title(str(articles[0].get("number_display") or ""))
    if not number_display:
        return False
    if number_display not in compact_title:
        return False

    first_text = _compact_title(str(articles[0].get("text") or ""))
    return bool(first_text) and first_text[:30] in _compact_title(text)


def _should_emit_policy_body_article(level: str, articles: list[dict]) -> bool:
    """Avoid treating embedded statute excerpts as policy-document articles.

    Judicial policy / meeting-minutes pages often quote Criminal Law or other
    statutes verbatim. If the parser's first "article" is not Article 1, the
    sequence is almost certainly an embedded excerpt rather than the document's
    own article structure.
    """

    if level not in {"judicial_policy", "judicial_meeting_minutes"}:
        return False
    if not articles:
        return True
    first_number = str(articles[0].get("number") or "").strip()
    return first_number != "1"


def _single_body_article(text: str) -> list[dict]:
    return cleaning.single_body_article(text)


def _policy_item_articles(level: str, text: str) -> list[dict]:
    if level not in {"judicial_policy", "judicial_meeting_minutes"}:
        return []
    return cleaning.parse_numbered_items_from_text(text)


@dataclass
class CourtMainAdapter:
    """最高人民法院主站 adapter。"""

    base_url: str = DEFAULT_BASE_URL
    timeout: int = DEFAULT_TIMEOUT
    request_interval: float = DEFAULT_REQUEST_INTERVAL
    _last_request_at: float = field(default=0.0, repr=False)

    def _throttle(self) -> None:
        interval = max(float(self.request_interval or 0), MIN_REQUEST_INTERVAL)
        now = time.monotonic()
        wait = interval - (now - self._last_request_at)
        if wait > 0:
            time.sleep(wait)
        self._last_request_at = time.monotonic()

    def probe(self) -> dict:
        self._throttle()
        checked_at = datetime.now(timezone.utc).isoformat()
        homepage_url = urljoin(self.base_url.rstrip("/") + "/", "index.html")
        try:
            homepage = _fetch_text(homepage_url, timeout=self.timeout)
        except HTTPError as exc:
            return {
                "source": "court_main",
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
                "source": "court_main",
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
            "source": "court_main",
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

    def search_url(self, query: str | None = None) -> str:
        params = {"content": query or ""}
        return urljoin(self.base_url.rstrip("/") + "/", f"{SEARCH_PATH}?{urlencode(params)}")

    def detail_url(self, detail_id: str) -> str:
        normalized = _normalize_detail_id(detail_id)
        if not normalized:
            raise ValueError(f"invalid court_main detail_id: {detail_id!r}")
        return urljoin(self.base_url.rstrip("/") + "/", f"{normalized}.html")

    def search_list(self, query: str | None = None, *, page_size: int = 20) -> dict:
        """Search ``/search.html`` and parse result rows.

        The site provides a server-side search page. This adapter consumes the
        first result page only; callers should use ``--prefer-id`` when they
        already have a detail URL.
        """

        needle = (query or "").strip()
        self._throttle()
        result = _fetch_text(self.search_url(needle), timeout=self.timeout)
        rows = _parse_rows(result.text, base_url=self.base_url, query=None)
        rows = rows[: max(int(page_size), 1)]
        return {
            "source": "court_main",
            "query": needle,
            "page": 1,
            "page_size": page_size,
            "total_pages": _parse_total_pages(result.text),
            "total_count": _parse_total_count(result.text),
            "rows": rows,
            "url": result.url,
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }

    def fetch_detail(self, detail_id: str) -> dict:
        candidates = _detail_id_candidates(detail_id)
        if not candidates:
            raise ValueError(f"invalid court_main detail_id: {detail_id!r}")

        last_detail: dict | None = None
        for candidate in candidates:
            url = urljoin(self.base_url.rstrip("/") + "/", f"{candidate}.html")
            self._throttle()
            result = _fetch_text(url, timeout=self.timeout)
            raw_title = _extract_title(result.text)
            page_title = _extract_detail_title(result.text) or _strip_title_suffix(
                raw_title or ""
            )
            content_html = _extract_content_html(result.text)
            detail = {
                "source": "court_main",
                "detail_id": candidate,
                "url": result.url,
                "raw_title": raw_title,
                "title": page_title or candidate,
                "content_html": content_html,
                "source_name_text": _extract_source_name(result.text),
                "published_at": _extract_published_at(result.text),
                "source_last_modified": result.headers.get("Last-Modified"),
                "source_etag": result.headers.get("ETag"),
                "checked_at": datetime.now(timezone.utc).isoformat(),
            }
            last_detail = detail
            if content_html and not _is_not_found(result.text):
                return detail

        raise ValueError(
            f"court_main detail {detail_id!r} produced empty article text"
            if last_detail
            else f"invalid court_main detail_id: {detail_id!r}"
        )

    def build_law_payload(
        self,
        detail_id: str,
        *,
        search_row: dict | None = None,
        detail: dict | None = None,
    ) -> dict:
        detail = detail or self.fetch_detail(detail_id)
        raw_text = _html_to_text(detail.get("content_html") or "")
        if not raw_text.strip():
            raise ValueError(f"court_main detail {detail_id} produced empty article text")

        title = _infer_document_title(detail.get("title") or "", raw_text)
        level = _infer_level(title, raw_text)
        issuing_body = _infer_issuing_body(title, raw_text)
        document_number = cleaning.extract_document_number_from_preamble(raw_text)
        released_at = _date_part(detail.get("published_at")) or (search_row or {}).get(
            "released_at"
        )
        effective_at = _infer_effective_at(raw_text)
        text = raw_text
        articles = cleaning.parse_articles_from_text(text)
        item_articles = _policy_item_articles(level, text)

        if item_articles:
            payload = cleaning.canonicalize(
                {
                    "id": f"court_main:{detail['detail_id']}",
                    "title": title,
                    "short_title": _infer_short_title(title),
                    "aliases": [],
                    "level": level,
                    "status": "current",
                    "issuing_body": issuing_body,
                    "document_number": document_number,
                    "released_at": released_at,
                    "effective_at": effective_at,
                    "repealed_at": None,
                    "source_url": detail.get("url"),
                    "source_name": "www.court.gov.cn",
                    "source_checked_at": detail.get("checked_at"),
                    "source_hash": self._hash_text(text),
                    "articles": item_articles,
                },
                source_kind="external_json",
            )
        elif _should_emit_single_body_article(
            title, text, articles
        ) or _should_emit_policy_body_article(level, articles):
            payload = cleaning.canonicalize(
                {
                    "id": f"court_main:{detail['detail_id']}",
                    "title": title,
                    "short_title": _infer_short_title(title),
                    "aliases": [],
                    "level": level,
                    "status": "current",
                    "issuing_body": issuing_body,
                    "document_number": document_number,
                    "released_at": released_at,
                    "effective_at": effective_at,
                    "repealed_at": None,
                    "source_url": detail.get("url"),
                    "source_name": "www.court.gov.cn",
                    "source_checked_at": detail.get("checked_at"),
                    "source_hash": self._hash_text(text),
                    "articles": _single_body_article(text),
                },
                source_kind="external_json",
            )
        else:
            payload = cleaning.canonicalize(
                text,
                source_kind="markdown",
                id=f"court_main:{detail['detail_id']}",
                title=title,
                short_title=_infer_short_title(title),
                level=level,
                status="current",
                issuing_body=issuing_body,
                document_number=document_number,
                released_at=released_at,
                effective_at=effective_at,
                source_url=detail.get("url"),
                source_name="www.court.gov.cn",
                source_checked_at=detail.get("checked_at"),
                source_hash=self._hash_text(text),
            )
        return payload

    @staticmethod
    def _hash_text(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def source_hash(self, detail_id: str) -> str:
        detail = self.fetch_detail(detail_id)
        return self._hash_text(_html_to_text(detail.get("content_html") or ""))


default_adapter = CourtMainAdapter()


def probe(timeout: int = DEFAULT_TIMEOUT) -> dict:
    adapter = CourtMainAdapter(timeout=timeout)
    return adapter.probe()


def search_list(query: str | None = None, **kwargs) -> dict:
    return default_adapter.search_list(query, **kwargs)


def fetch_detail(detail_id: str) -> dict:
    return default_adapter.fetch_detail(detail_id)
