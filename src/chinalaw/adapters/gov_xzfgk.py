"""国家行政法规库（www.gov.cn / xzfg.moj.gov.cn）adapter。

``www.gov.cn/zhengce/xzfgk/`` 是国务院入口页；实际应用由司法部
``xzfg.moj.gov.cn`` 承载。本 adapter 以实际应用的 ``LawID`` 作为源主键，
覆盖行政法规的搜索、详情清洗和历史沿革提示。
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
from urllib.parse import parse_qs, urlencode, urljoin, urlparse
from urllib.request import Request, urlopen

from chinalaw import cleaning
from chinalaw.adapters import _html as _adapter_html

GOV_WRAPPER_URL = "https://www.gov.cn/zhengce/xzfgk/"
DEFAULT_BASE_URL = "https://xzfg.moj.gov.cn"
DEFAULT_TIMEOUT = 15
DEFAULT_REQUEST_INTERVAL = 0.5
MIN_REQUEST_INTERVAL = 0.1

TOOL_UA_TOKEN = "chinalaw-cli/0.1.0 (+https://github.com/chinalaw-cli/chinalaw-cli)"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36 "
    f"{TOOL_UA_TOKEN}"
)

TITLE_SUFFIXES = (" - 国家行政法规库", "- 国家行政法规库")
ISSUER_PREFIXES = ("中华人民共和国国务院 ", "中华人民共和国国务院", "国务院 ")

LIST_ITEM_RE = re.compile(
    r'<li[^>]*class=["\'][^"\']*\blist-item\b[^"\']*["\'][^>]*>(?P<body>.*?)'
    r'(?=\n\s*<li[^>]*class=["\'][^"\']*\blist-item\b|</ul>\s*<div\s+id=["\']pagination)',
    re.IGNORECASE | re.DOTALL,
)
TITLE_LINK_RE = re.compile(
    r'<div[^>]*class=["\'][^"\']*\btitle\b[^"\']*["\'][^>]*>.*?'
    r'<a[^>]*href=["\'](?P<href>[^"\']*LawID=(?P<law_id>\d+)[^"\']*)["\'][^>]*>'
    r"(?P<title>.*?)</a>",
    re.IGNORECASE | re.DOTALL,
)
INCIDENT_RE = re.compile(
    r'<div[^>]*class=["\'][^"\']*\bincident-record\b(?P<class>[^"\']*)["\'][^>]*'
    r'data-time=["\'](?P<date>[^"\']+)["\'][^>]*>.*?'
    r'<a[^>]*class=["\'](?P<a_class>[^"\']*)["\'][^>]*'
    r'href=["\'](?P<href>[^"\']*LawID=(?P<law_id>\d+)[^"\']*)["\'][^>]*>'
    r"(?P<title>.*?)</a>",
    re.IGNORECASE | re.DOTALL,
)
PAGE_COUNT_RE = re.compile(r'id=["\']page-count["\']\s+value=["\'](?P<count>\d+)["\']')
LAW_TOTAL_RE = re.compile(r'id=["\']law-total["\']\s+value=["\'](?P<count>\d+)["\']')
DETAIL_TITLE_RE = re.compile(
    r'<div[^>]*class=["\'][^"\']*\btext-title\b[^"\']*["\'][^>]*>(?P<title>.*?)</div>',
    re.IGNORECASE | re.DOTALL,
)
LAW_CHAPTER_OPEN_RE = re.compile(
    r'<div[^>]*class=["\'][^"\']*\blaw-chapter\b[^"\']*["\'][^>]*>(?P<body>.*)',
    re.IGNORECASE | re.DOTALL,
)
DATE_RE = re.compile(r"(?P<date>(?:19|20)\d{2})[-年](?P<month>\d{1,2})[-月](?P<day>\d{1,2})")
CHINESE_DATE_RE = re.compile(r"(?P<year>(?:19|20)\d{2})年(?P<month>\d{1,2})月(?P<day>\d{1,2})日")
EFFECTIVE_DATE_RE = re.compile(
    r"自\s*(?P<year>(?:19|20)\d{2})年(?P<month>\d{1,2})月(?P<day>\d{1,2})日起施行"
)
DOCUMENT_NUMBER_RE = re.compile(
    r"(?:中华人民共和国)?国务院令\s*(?:第)?\s*(?P<num>\d+)\s*号"
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


def _html_to_text(content_html: str) -> str:
    return _adapter_html.html_to_text(content_html)


def _clean_text(raw: str | None) -> str:
    return re.sub(r"\s+", " ", _html_to_text(raw or "")).strip()


def _strip_title_suffix(raw_title: str) -> str:
    return _adapter_html.strip_known_title_suffix(raw_title, TITLE_SUFFIXES)


def _extract_title(html: str) -> str | None:
    return _adapter_html.html_extract_title(html)


def _normalize_detail_id(raw: str | None) -> str | None:
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    if text.isdigit():
        return text
    parsed = urlparse(text)
    query = parse_qs(parsed.query)
    law_id = (query.get("LawID") or query.get("lawid") or [None])[0]
    if law_id and str(law_id).isdigit():
        return str(law_id)
    match = re.search(r"LawID=(\d+)", text, re.IGNORECASE)
    return match.group(1) if match else None


def _detail_url(base_url: str, detail_id: str) -> str:
    normalized = _normalize_detail_id(detail_id)
    if not normalized:
        raise ValueError(f"invalid gov_xzfgk detail_id: {detail_id!r}")
    return urljoin(base_url.rstrip("/") + "/", f"front/law/detail?LawID={normalized}")


def _download_url(base_url: str, detail_id: str) -> str:
    normalized = _normalize_detail_id(detail_id)
    if not normalized:
        raise ValueError(f"invalid gov_xzfgk detail_id: {detail_id!r}")
    return urljoin(base_url.rstrip("/") + "/", f"law/download?LawID={normalized}")


def _parse_date(raw: str | None) -> str | None:
    if not raw:
        return None
    match = DATE_RE.search(raw)
    if not match:
        return None
    return (
        f"{int(match.group('date')):04d}-"
        f"{int(match.group('month')):02d}-"
        f"{int(match.group('day')):02d}"
    )


def _date_for_label(text: str, label: str) -> str | None:
    match = re.search(rf"((?:19|20)\d{{2}}[-年]\d{{1,2}}[-月]\d{{1,2}}日?)\s*{label}", text)
    return _parse_date(match.group(1)) if match else None


def _latest_chinese_date(text: str) -> str | None:
    dates = []
    for match in CHINESE_DATE_RE.finditer(text or ""):
        dates.append(
            f"{int(match.group('year')):04d}-"
            f"{int(match.group('month')):02d}-"
            f"{int(match.group('day')):02d}"
        )
    return max(dates) if dates else None


def _infer_effective_at(text: str) -> str | None:
    match = EFFECTIVE_DATE_RE.search(text or "")
    if not match:
        return None
    return (
        f"{int(match.group('year')):04d}-"
        f"{int(match.group('month')):02d}-"
        f"{int(match.group('day')):02d}"
    )


def _infer_document_number(text: str) -> str | None:
    matches = list(DOCUMENT_NUMBER_RE.finditer(text or ""))
    if not matches:
        return None
    return f"国务院令第{int(matches[-1].group('num'))}号"


def _extract_detail_title(html: str) -> str | None:
    match = DETAIL_TITLE_RE.search(html)
    if match:
        return _clean_text(match.group("title"))
    title = _extract_title(html)
    return _strip_title_suffix(title or "") or None


def _extract_content_html(html: str) -> str:
    match = LAW_CHAPTER_OPEN_RE.search(html)
    if not match:
        return ""
    body = match.group("body")
    for sentinel in ("</main>", "<!--** 底部", '<script src="https://xzfg.moj.gov.cn/law/js'):
        idx = body.find(sentinel)
        if idx >= 0:
            body = body[:idx]
            break
    return body.strip()


def _parse_versions(html: str, *, base_url: str) -> list[dict]:
    versions: list[dict] = []
    seen: set[str] = set()
    for match in INCIDENT_RE.finditer(html or ""):
        law_id = match.group("law_id")
        if law_id in seen:
            continue
        href = unescape(match.group("href"))
        a_class = match.group("a_class") or ""
        versions.append(
            {
                "detail_id": law_id,
                "title": _clean_text(match.group("title")),
                "date": _parse_date(match.group("date")),
                "current": "on" in a_class.split(),
                "url": urljoin(base_url.rstrip("/") + "/", href),
            }
        )
        seen.add(law_id)
    return versions


def _parse_search_rows(html: str, *, base_url: str, page_size: int = 20) -> list[dict]:
    rows: list[dict] = []
    seen: set[str] = set()
    for item_match in LIST_ITEM_RE.finditer(html or ""):
        chunk = item_match.group("body")
        link_match = TITLE_LINK_RE.search(chunk)
        if not link_match:
            continue
        law_id = link_match.group("law_id")
        if law_id in seen:
            continue
        title = _clean_text(link_match.group("title"))
        text = _clean_text(chunk)
        versions = _parse_versions(chunk, base_url=base_url)
        row = {
            "detail_id": law_id,
            "title": title,
            "released_at": _date_for_label(text, "公布"),
            "effective_at": _date_for_label(text, "施行"),
            "url": urljoin(base_url.rstrip("/") + "/", unescape(link_match.group("href"))),
            "status": "current",
            "related_versions": versions,
        }
        rows.append(row)
        seen.add(law_id)
        if len(rows) >= page_size:
            break
    return rows


def _page_count(html: str) -> int | None:
    match = PAGE_COUNT_RE.search(html or "")
    return int(match.group("count")) if match else None


def _total_count(html: str) -> int | None:
    match = LAW_TOTAL_RE.search(html or "")
    return int(match.group("count")) if match else None


@dataclass
class GovXzfgkAdapter:
    """国家行政法规库 adapter。"""

    base_url: str = DEFAULT_BASE_URL
    wrapper_url: str = GOV_WRAPPER_URL
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
        checked_at = datetime.now(timezone.utc).isoformat()
        try:
            self._throttle()
            wrapper = _fetch_text(self.wrapper_url, timeout=self.timeout)
            self._throttle()
            app = _fetch_text(self.search_url(None), timeout=self.timeout)
        except HTTPError as exc:
            return {
                "source": "gov_xzfgk",
                "homepage_url": self.wrapper_url,
                "final_url": self.wrapper_url,
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
                "source": "gov_xzfgk",
                "homepage_url": self.wrapper_url,
                "final_url": self.wrapper_url,
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
        sections = [name for name in ("国家行政法规库", "历史沿革", "现行有效") if name in app.text]
        return {
            "source": "gov_xzfgk",
            "homepage_url": self.wrapper_url,
            "final_url": app.url,
            "status_code": app.status_code,
            "title": _extract_title(app.text) or _extract_title(wrapper.text),
            "page_shape": "ok" if "国家行政法规库" in app.text else "unknown",
            "detected_sections": sections,
            "bundle_contains_known_sections": bool(sections),
            "source_last_modified": app.headers.get("Last-Modified"),
            "source_etag": app.headers.get("ETag"),
            "checked_at": checked_at,
            "wrapper_status_code": wrapper.status_code,
            "wrapper_final_url": wrapper.url,
        }

    def search_url(self, query: str | None = None) -> str:
        needle = (query or "").strip()
        if not needle:
            return urljoin(self.base_url.rstrip("/") + "/", "search2.html")
        params = urlencode(
            {
                "title": needle,
                "timeliness": "1",
                "sortField": "PublishTime",
                "ascOrDesc": "desc",
            }
        )
        return urljoin(self.base_url.rstrip("/") + "/", f"SearchAdvancedFront?{params}")

    def detail_url(self, detail_id: str) -> str:
        return _detail_url(self.base_url, detail_id)

    def download_url(self, detail_id: str) -> str:
        return _download_url(self.base_url, detail_id)

    def search_list(self, query: str | None = None, *, page_size: int = 20) -> dict:
        needle = (query or "").strip()
        self._throttle()
        result = _fetch_text(self.search_url(needle), timeout=self.timeout)
        rows = _parse_search_rows(result.text, base_url=self.base_url, page_size=page_size)
        return {
            "source": "gov_xzfgk",
            "query": needle,
            "page": 1,
            "page_size": page_size,
            "total_pages": _page_count(result.text),
            "total_count": _total_count(result.text),
            "rows": rows,
            "url": result.url,
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }

    def fetch_detail(self, detail_id: str) -> dict:
        normalized = _normalize_detail_id(detail_id)
        if not normalized:
            raise ValueError(f"invalid gov_xzfgk detail_id: {detail_id!r}")
        url = self.detail_url(normalized)
        self._throttle()
        result = _fetch_text(url, timeout=self.timeout)
        content_html = _extract_content_html(result.text)
        title = _extract_detail_title(result.text) or normalized
        return {
            "source": "gov_xzfgk",
            "detail_id": normalized,
            "url": result.url,
            "raw_title": _extract_title(result.text),
            "title": title,
            "content_html": content_html,
            "content_text": _html_to_text(content_html),
            "related_versions": _parse_versions(result.text, base_url=self.base_url),
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
        detail = detail or self.fetch_detail(detail_id)
        raw_text = detail.get("content_text") or _html_to_text(detail.get("content_html") or "")
        if not raw_text.strip():
            raise ValueError(f"gov_xzfgk detail {detail_id} produced empty article text")

        title = _clean_text(detail.get("title")) or (search_row or {}).get("title") or detail_id
        versions = (
            detail.get("related_versions")
            or (search_row or {}).get("related_versions")
            or []
        )
        current_version = next(
            (item for item in versions if item.get("detail_id") == detail.get("detail_id")),
            None,
        )
        status = (
            "current"
            if current_version is None or current_version.get("current")
            else "amended"
        )
        preamble = "\n".join(raw_text.splitlines()[:8])
        released_at = (search_row or {}).get("released_at") or _latest_chinese_date(preamble)
        effective_at = (search_row or {}).get("effective_at") or _infer_effective_at(raw_text)
        payload = cleaning.canonicalize(
            raw_text,
            source_kind="markdown",
            id=f"gov_xzfgk:{detail['detail_id']}",
            title=title,
            short_title=_infer_short_title(title),
            level="admin_regulation",
            status=status,
            issuing_body="国务院",
            document_number=_infer_document_number(preamble),
            released_at=released_at,
            effective_at=effective_at,
            source_url=detail.get("url"),
            source_name="xzfg.moj.gov.cn",
            source_checked_at=detail.get("checked_at"),
            source_hash=self._hash_text(raw_text),
        )
        payload["related_versions"] = versions
        return payload

    @staticmethod
    def _hash_text(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def source_hash(self, detail_id: str) -> str:
        detail = self.fetch_detail(detail_id)
        return self._hash_text(detail.get("content_text") or "")


def _infer_short_title(title: str) -> str | None:
    short = _adapter_html.infer_short_title(title, site_prefixes=ISSUER_PREFIXES)
    if short:
        return short
    cleaned = re.sub(r"\s+", "", title or "")
    return cleaned if 2 <= len(cleaned) <= 30 else None


default_adapter = GovXzfgkAdapter()


def probe(timeout: int = DEFAULT_TIMEOUT) -> dict:
    adapter = GovXzfgkAdapter(timeout=timeout)
    return adapter.probe()


def search_list(query: str | None = None, **kwargs) -> dict:
    return default_adapter.search_list(query, **kwargs)


def fetch_detail(detail_id: str) -> dict:
    return default_adapter.fetch_detail(detail_id)


def build_law_payload(detail_id: str, **kwargs) -> dict:
    return default_adapter.build_law_payload(detail_id, **kwargs)
