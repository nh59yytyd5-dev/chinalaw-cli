"""证券交易所 / 登记结算 / 协会规则站点通用 adapter。

这些站点的共同形态是：详情页通常是通知正文，真正可引用规则在 PDF/DOCX
附件中。adapter 的边界是有界检索 + 附件抽取 + 复用 cleaning 层，不做无界
全站爬取。
"""

from __future__ import annotations

import hashlib
import http.cookiejar
import json
import re
import shutil
import subprocess
import tempfile
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html import unescape
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urljoin, urlparse, urlsplit, urlunsplit
from urllib.request import HTTPCookieProcessor, Request, build_opener

from chinalaw import cleaning
from chinalaw.adapters import _html as _adapter_html
from chinalaw.document_numbers import extract_document_number

DEFAULT_TIMEOUT = 15
DEFAULT_REQUEST_INTERVAL = 0.5
MIN_REQUEST_INTERVAL = 0.1
TOOL_UA_TOKEN = "chinalaw-cli/0.1.1 (+https://github.com/chinalaw-cli/chinalaw-cli)"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36 "
    f"{TOOL_UA_TOKEN}"
)

TITLE_RE = re.compile(r"<title>(?P<title>.*?)</title>", re.IGNORECASE | re.DOTALL)
ANCHOR_RE = re.compile(
    r"<a\b(?P<attrs>[^>]*\bhref=[\"'](?P<href>[^\"']+)[\"'][^>]*)>"
    r"(?P<text>.*?)</a>",
    re.IGNORECASE | re.DOTALL,
)
HREF_TITLE_RE = re.compile(r"\btitle=[\"'](?P<title>[^\"']+)[\"']", re.IGNORECASE)
DOWNLOAD_TITLE_RE = re.compile(r"\bdownload=[\"'](?P<title>[^\"']+)[\"']", re.IGNORECASE)
FILE_ATTR_RE = re.compile(r"\bfile=[\"'](?P<href>[^\"']+)[\"']", re.IGNORECASE)
DATE_RE = re.compile(r"(?P<date>(?:19|20)\d{2}[-年]\d{1,2}[-月]\d{1,2}日?)")
CHINESE_DATE_RE = re.compile(
    r"(?P<year>(?:19|20)\d{2})年(?P<month>\d{1,2})月(?P<day>\d{1,2})日"
)
EFFECTIVE_DATE_RE = re.compile(
    r"自\s*(?P<year>(?:19|20)\d{2})\s*年\s*"
    r"(?P<month>\d{1,2})\s*月\s*(?P<day>\d{1,2})\s*日起施行"
)
BOOK_TITLE_RE = re.compile(r"《(?P<title>[^》]{4,120})》")
FILE_EXT_RE = re.compile(r"\.(?:pdf|docx?|txt)(?:$|[?#])", re.IGNORECASE)
ARTICLE_HINT_RE = re.compile(
    r"(?:第[一二三四五六七八九十百千万零〇两\d]+条|\d+(?:\.\d+)+\s*[\u4e00-\u9fff])"
)
PDF_PAGE_MARKER_RE = re.compile(r"^-\s*\d{1,4}\s*-")
PDF_TOC_LINE_RE = re.compile(r"(?:目\s*录|\.{3,}|…{2,})")
PDF_STRUCTURAL_HEADING_RE = re.compile(
    r"^(?:第[一二三四五六七八九十百千万零〇两\d]+[章节编]|附则)"
)
CHINESE_ARTICLE_REFERENCE_RE = re.compile(
    r"^第[一二三四五六七八九十百千万零〇两\d]+条"
    r"(?:的规定|规定|所称|所列|第|至|到|、|和|及|等)"
)


@dataclass(frozen=True)
class SeedCandidate:
    title: str
    detail_id: str
    released_at: str | None = None


@dataclass(frozen=True)
class SiteConfig:
    source: str
    source_name: str
    base_url: str
    homepage_path: str
    issuing_body: str
    title_suffixes: tuple[str, ...]
    content_markers: tuple[str, ...]
    search_pages: tuple[str, ...] = ()
    seed_candidates: tuple[SeedCandidate, ...] = ()
    search_api: str | None = None
    search_api_channel: str | None = None
    search_api_nodes: tuple[str, ...] = ()
    paginated_search_roots: tuple[str, ...] = ()
    paginated_search_max_pages: int = 0
    search_deadline_seconds: float = 0.0


@dataclass
class FetchResult:
    url: str
    status_code: int
    headers: Mapping[str, str]
    text: str


def _quote_url(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit(
        (
            parts.scheme,
            parts.netloc,
            quote(parts.path, safe="/:%"),
            quote(parts.query, safe="=&;%:+,/?"),
            quote(parts.fragment, safe=""),
        )
    )


def _build_request(url: str, *, data: bytes | None = None, referer: str | None = None) -> Request:
    headers = {
        "User-Agent": DEFAULT_USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/json,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.5",
    }
    if referer:
        headers["Referer"] = referer
    if data is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    return Request(_quote_url(url), headers=headers, data=data, method="POST" if data else "GET")


def _fetch_text(
    url: str,
    *,
    timeout: int = DEFAULT_TIMEOUT,
    data: bytes | None = None,
    referer: str | None = None,
) -> FetchResult:
    req = _build_request(url, data=data, referer=referer)
    with _open_with_cookies(req, timeout=timeout) as resp:
        charset = resp.headers.get_content_charset() or "utf-8"
        body = resp.read().decode(charset, errors="replace")
        return FetchResult(
            url=resp.geturl(),
            status_code=resp.getcode(),
            headers=dict(resp.headers.items()),
            text=body,
        )


def _fetch_bytes(
    url: str,
    *,
    timeout: int = DEFAULT_TIMEOUT,
    referer: str | None = None,
) -> tuple[str, Mapping[str, str], bytes]:
    req = _build_request(url, referer=referer)
    with _open_with_cookies(req, timeout=timeout) as resp:
        return resp.geturl(), dict(resp.headers.items()), resp.read()


def _open_with_cookies(req: Request, *, timeout: int):
    """Open one request with an ephemeral cookie jar.

    BSE currently responds to first-time GETs with a same-URL 302 and a short
    cookie. Plain ``urlopen`` follows the redirect without persisting the
    cookie, which loops until the redirect cap. An isolated opener keeps the
    challenge cookie for this request while preserving adapter statelessness.
    """

    opener = build_opener(HTTPCookieProcessor(http.cookiejar.CookieJar()))
    return opener.open(req, timeout=timeout)


def _html_to_text(content_html: str) -> str:
    return _adapter_html.html_to_text(content_html)


def _clean_text(raw: str | None) -> str:
    return re.sub(r"\s+", " ", _html_to_text(raw or "")).strip()


def _clean_title(raw: str | None) -> str:
    text = _clean_text(raw)
    text = re.sub(r"^[0-9一二三四五六七八九十]+[．.、]\s*", "", text)
    text = re.sub(r"\.(?:pdf|docx?|txt)$", "", text, flags=re.IGNORECASE)
    return text.strip(" 《》")


def _generic_file_title(title: str | None) -> bool:
    text = _clean_title(title)
    if not text:
        return True
    if text.lower() in {"pdf", "doc", "docx", "txt"}:
        return True
    return bool(re.fullmatch(r"[\dA-Za-z_. -]{1,80}", text))


def _compact(value: str | None) -> str:
    return re.sub(r"[\s《》【】\[\]（）()：:、，,。._—-]+", "", value or "")


def _date_part(raw: str | None) -> str | None:
    if not raw:
        return None
    match = CHINESE_DATE_RE.search(raw)
    if match:
        return (
            f"{int(match.group('year')):04d}-"
            f"{int(match.group('month')):02d}-"
            f"{int(match.group('day')):02d}"
        )
    match = re.search(r"(?:19|20)\d{2}-\d{1,2}-\d{1,2}", raw)
    if not match:
        return None
    year, month, day = match.group(0).split("-")
    return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"


def _latest_date(text: str | None) -> str | None:
    dates = [_date_part(match.group(0)) for match in DATE_RE.finditer(text or "")]
    dates = [date for date in dates if date]
    return dates[-1] if dates else None


def _infer_effective_at(text: str | None) -> str | None:
    match = EFFECTIVE_DATE_RE.search(text or "")
    if not match:
        return None
    return (
        f"{int(match.group('year')):04d}-"
        f"{int(match.group('month')):02d}-"
        f"{int(match.group('day')):02d}"
    )


def _title_from_html(html: str, suffixes: tuple[str, ...]) -> str | None:
    match = TITLE_RE.search(html or "")
    title = unescape(match.group("title")).strip() if match else None
    if not title:
        return None
    return _adapter_html.strip_known_title_suffix(title, suffixes)


def _book_title(raw_title: str | None) -> str | None:
    for match in BOOK_TITLE_RE.finditer(raw_title or ""):
        title = _clean_title(match.group("title"))
        if title and not any(token in title for token in ("修订说明", "起草说明", "问答")):
            return title
    return None


def _extract_content_html(html: str, markers: tuple[str, ...]) -> str:
    for marker in markers:
        idx = html.find(marker)
        if idx < 0:
            continue
        start = html.rfind("<div", 0, idx)
        if start < 0:
            start = idx
        tail = html[start:]
        end = len(tail)
        for sentinel in (
            '<div class="share"',
            "<footer",
            '<div class="footer"',
            '<div class="page-con-table"',
            '<div class="gg_bottom"',
            "</body>",
        ):
            pos = tail.find(sentinel)
            if pos >= 0:
                end = min(end, pos)
        return tail[:end].strip()
    return ""


def _extract_attachments(html: str, *, base_url: str, referer_url: str) -> list[dict]:
    items: list[dict] = []
    seen: set[str] = set()

    def add(href: str, title: str | None) -> None:
        href = unescape(href).strip()
        if not href or not FILE_EXT_RE.search(href):
            return
        url = urljoin(referer_url, href)
        if url in seen:
            return
        cleaned_title = _clean_title(title)
        if not cleaned_title:
            cleaned_title = Path(urlparse(url).path).name
        items.append({"url": url, "title": cleaned_title})
        seen.add(url)

    for match in ANCHOR_RE.finditer(html or ""):
        title_attr = HREF_TITLE_RE.search(match.group("attrs") or "")
        add(match.group("href"), title_attr.group("title") if title_attr else match.group("text"))
    for match in FILE_ATTR_RE.finditer(html or ""):
        context = html[max(0, match.start() - 300): match.end() + 300]
        title_attr = DOWNLOAD_TITLE_RE.search(context) or HREF_TITLE_RE.search(context)
        add(match.group("href"), title_attr.group("title") if title_attr else None)
    return items


def _parse_static_rows(
    html: str,
    *,
    page_url: str,
    source: str,
    query: str,
    page_size: int,
) -> list[dict]:
    rows: list[dict] = []
    seen: set[str] = set()
    for match in ANCHOR_RE.finditer(html or ""):
        href = unescape(match.group("href")).strip()
        if not href or href.startswith("javascript:"):
            continue
        attrs = match.group("attrs") or ""
        title_attr = HREF_TITLE_RE.search(attrs)
        title = _clean_title(title_attr.group("title") if title_attr else match.group("text"))
        if not title or not _matches_query(title, query):
            continue
        url = urljoin(page_url, href)
        detail_id = _url_to_detail_id(url)
        if not detail_id or detail_id in seen:
            continue
        context = html[max(0, match.start() - 400): match.end() + 400]
        rows.append(
            {
                "detail_id": detail_id,
                "title": title,
                "released_at": _latest_date(context),
                "published_at": _latest_date(context),
                "url": url,
                "status": "current",
                "source": source,
            }
        )
        seen.add(detail_id)
        if len(rows) >= max(page_size, 1):
            break
    return rows


def _parse_count_page(html: str) -> int | None:
    match = re.search(r"var\s+countPage\s*=\s*(?P<count>\d+)", html or "")
    if not match:
        return None
    return int(match.group("count"))


def _paginated_index_path(page: int) -> str:
    return "index.html" if page <= 0 else f"index_{page}.html"


def _matches_query(title: str, query: str | None) -> bool:
    needle = _compact(query)
    if not needle:
        return True
    haystack = _compact(title)
    if needle in haystack:
        return True
    return all(char in haystack for char in needle if "\u4e00" <= char <= "\u9fff")


def _is_precise_query(query: str | None) -> bool:
    return len(_compact(query)) >= 8


def _has_precise_title_match(rows: list[dict], query: str | None) -> bool:
    needle = _compact(query)
    if not needle:
        return bool(rows)
    return any(needle in _compact(row.get("title")) for row in rows)


def _url_to_detail_id(url: str) -> str | None:
    parsed = urlparse(url)
    path = (parsed.path or "").lstrip("/")
    if not path:
        return None
    if path.endswith((".html", ".shtml", ".pdf", ".doc", ".docx", ".txt")):
        return path
    return None


def _detail_url(base_url: str, detail_id: str) -> str:
    if "://" in detail_id:
        return detail_id
    return urljoin(base_url.rstrip("/") + "/", detail_id.lstrip("/"))


def _choose_attachment(attachments: list[dict], *, detail_title: str) -> dict | None:
    if not attachments:
        return None
    detail_compact = _compact(_book_title(detail_title) or detail_title)
    scored: list[tuple[int, dict]] = []
    for item in attachments:
        title = item.get("title") or ""
        compact = _compact(title)
        score = 0
        if any(token in compact for token in ("修订说明", "起草说明", "问答", "反馈意见")):
            score -= 20
        if detail_compact and (detail_compact in compact or compact in detail_compact):
            score += 20
        if any(token in compact for token in ("规则", "办法", "指引", "细则", "指南")):
            score += 5
        if compact.startswith(("1", "附件1")):
            score += 2
        scored.append((score, item))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return scored[0][1] if scored and scored[0][0] >= 0 else None


def _pdf_bytes_to_text(pdf_bytes: bytes) -> str:
    pdftotext = shutil.which("pdftotext")
    if not pdftotext:
        raise ValueError("PDF attachment extraction requires pdftotext")
    with tempfile.TemporaryDirectory() as td:
        pdf_path = Path(td) / "source.pdf"
        pdf_path.write_bytes(pdf_bytes)
        result = subprocess.run(
            [pdftotext, "-raw", str(pdf_path), "-"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    if result.returncode != 0:
        message = (result.stderr or "").strip() or "pdftotext failed"
        raise ValueError(f"PDF text extraction failed: {message}")
    return _clean_pdf_text(result.stdout)


def _clean_pdf_text(text: str) -> str:
    lines: list[str] = []
    normalized = (text or "").replace("\r\n", "\n").replace("\r", "\n").replace("\f", "\n")
    for raw_line in normalized.splitlines():
        stripped = re.sub(r"\s+", " ", raw_line).strip()
        if PDF_TOC_LINE_RE.search(stripped):
            continue
        stripped = PDF_PAGE_MARKER_RE.sub("", stripped).strip()
        if not stripped or re.fullmatch(r"\d{1,4}", stripped):
            continue
        if lines and _pdf_line_continues(lines[-1], stripped):
            lines[-1] = lines[-1] + stripped
        else:
            lines.append(stripped)
    return "\n".join(lines)


def _pdf_line_continues(previous: str, current: str) -> bool:
    if PDF_STRUCTURAL_HEADING_RE.match(current):
        return False
    if CHINESE_ARTICLE_REFERENCE_RE.match(current):
        return True
    if re.match(r"^(?:第[一二三四五六七八九十百千万零〇两\d]+条|\d+(?:\.\d+)+\s*)", current):
        return False
    return not previous.endswith(("。", "；", "：", "！", "？", ";", ":"))


def _has_article_text(text: str | None) -> bool:
    return bool(ARTICLE_HINT_RE.search(text or ""))


@dataclass
class SecuritiesRulesAdapter:
    config: SiteConfig
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
        homepage_url = urljoin(
            self.config.base_url.rstrip("/") + "/",
            self.config.homepage_path.lstrip("/"),
        )
        checked_at = datetime.now(timezone.utc).isoformat()
        self._throttle()
        try:
            result = _fetch_text(homepage_url, timeout=self.timeout)
        except HTTPError as exc:
            return self._probe_error(
                homepage_url,
                checked_at,
                f"HTTPError {exc.code}: {exc.reason}",
                exc.code,
            )
        except URLError as exc:
            return self._probe_error(homepage_url, checked_at, f"URLError: {exc.reason}", None)
        except (OSError, TimeoutError) as exc:
            return self._probe_error(homepage_url, checked_at, str(exc), None)
        title = _title_from_html(result.text, self.config.title_suffixes)
        return {
            "source": self.config.source,
            "homepage_url": homepage_url,
            "final_url": result.url,
            "status_code": result.status_code,
            "title": title,
            "page_shape": "ok" if title or self.config.issuing_body in result.text else "unknown",
            "detected_sections": [
                token
                for token in ("规则", "业务规则", "自律规则", "法律规则")
                if token in result.text
            ],
            "bundle_contains_known_sections": "规则" in result.text,
            "source_last_modified": result.headers.get("Last-Modified"),
            "source_etag": result.headers.get("ETag"),
            "checked_at": checked_at,
        }

    def _probe_error(
        self, homepage_url: str, checked_at: str, message: str, status_code: int | None
    ) -> dict:
        return {
            "source": self.config.source,
            "homepage_url": homepage_url,
            "final_url": homepage_url,
            "status_code": status_code,
            "title": None,
            "page_shape": "error",
            "detected_sections": [],
            "bundle_contains_known_sections": False,
            "source_last_modified": None,
            "source_etag": None,
            "checked_at": checked_at,
            "error": message,
        }

    def search_list(self, query: str | None = None, *, page_size: int = 20) -> dict:
        needle = (query or "").strip()
        rows: list[dict] = []
        warnings: list[dict] = []
        visited_urls: set[str] = set()
        deadline = (
            time.monotonic() + float(self.config.search_deadline_seconds)
            if self.config.search_deadline_seconds
            else None
        )
        if self.config.search_api == "szse":
            rows.extend(self._search_szse_api(needle, page_size=page_size))
        if self.config.search_api == "bse":
            rows.extend(self._search_bse_api(needle, page_size=page_size))

        for seed in self.config.seed_candidates:
            if _matches_query(seed.title, needle):
                rows.append(
                    {
                        "detail_id": seed.detail_id,
                        "title": seed.title,
                        "released_at": seed.released_at,
                        "published_at": seed.released_at,
                        "url": self.detail_url(seed.detail_id),
                        "status": "current",
                        "source": self.config.source,
                    }
                )

        for page in self.config.search_pages:
            if len(rows) >= page_size:
                break
            page_url = urljoin(self.config.base_url.rstrip("/") + "/", page.lstrip("/"))
            visited_urls.add(page_url)
            self._throttle()
            result = self._safe_fetch_search_page(page_url, warnings, deadline=deadline)
            if result is None:
                continue
            rows.extend(
                _parse_static_rows(
                    result.text,
                    page_url=result.url,
                    source=self.config.source,
                    query=needle,
                    page_size=max(page_size - len(rows), 1),
                )
            )

        deduped = self._dedupe_rows(rows)
        if (
            self.config.paginated_search_roots
            and _is_precise_query(needle)
            and not _has_precise_title_match(deduped, needle)
        ):
            rows.extend(
                self._search_paginated_roots(
                    needle,
                    page_size=page_size,
                    visited_urls=visited_urls,
                    warnings=warnings,
                    deadline=deadline,
                )
            )

        deduped = self._dedupe_rows(rows)[: max(page_size, 1)]
        payload = {
            "source": self.config.source,
            "query": needle,
            "page": 1,
            "page_size": page_size,
            "total_pages": None,
            "total_count": len(deduped),
            "rows": deduped,
            "url": self.config.base_url,
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }
        if warnings:
            payload["warnings"] = warnings
        return payload

    def _safe_fetch_search_page(
        self,
        page_url: str,
        warnings: list[dict],
        *,
        deadline: float | None = None,
    ) -> FetchResult | None:
        timeout = self.timeout
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                warnings.append(
                    {
                        "code": "search_deadline_exceeded",
                        "url": page_url,
                        "message": "search deadline exceeded before requesting this page",
                    }
                )
                return None
            timeout = min(timeout, max(1, int(remaining)))
        try:
            return _fetch_text(page_url, timeout=timeout)
        except (HTTPError, URLError, OSError, TimeoutError) as exc:
            warnings.append(
                {
                    "code": "search_page_unavailable",
                    "url": page_url,
                    "error": exc.__class__.__name__,
                    "message": str(exc),
                }
            )
            return None

    def _search_paginated_roots(
        self,
        query: str,
        *,
        page_size: int,
        visited_urls: set[str],
        warnings: list[dict],
        deadline: float | None,
    ) -> list[dict]:
        rows: list[dict] = []
        max_pages = max(int(self.config.paginated_search_max_pages or 0), 0)
        if max_pages <= 0:
            return rows

        for root in self.config.paginated_search_roots:
            for page_index in range(max_pages):
                if len(rows) >= max(page_size, 1) or _has_precise_title_match(rows, query):
                    return rows
                page_url = urljoin(
                    self.config.base_url.rstrip("/") + "/",
                    root.lstrip("/").rstrip("/") + "/" + _paginated_index_path(page_index),
                )
                if page_url in visited_urls:
                    continue
                visited_urls.add(page_url)
                self._throttle()
                result = self._safe_fetch_search_page(page_url, warnings, deadline=deadline)
                if result is None:
                    continue
                if page_index == 0:
                    count_page = _parse_count_page(result.text)
                    if count_page is not None:
                        max_pages = min(max_pages, max(count_page, 1))
                rows.extend(
                    _parse_static_rows(
                        result.text,
                        page_url=result.url,
                        source=self.config.source,
                        query=query,
                        page_size=max(page_size - len(rows), 1),
                    )
                )
        return rows

    def _search_szse_api(self, query: str, *, page_size: int) -> list[dict]:
        data = urlencode(
            {
                "keyword": query,
                "range": "title",
                "time": "0",
                "channelCode": self.config.search_api_channel or "szserulesAllRulesBuss",
                "currentPage": "1",
                "pageSize": str(max(page_size, 1)),
                "scope": "0",
            }
        ).encode("utf-8")
        url = urljoin(self.config.base_url.rstrip("/") + "/", "api/search/content")
        referer = urljoin(self.config.base_url.rstrip("/") + "/", "lawrules/rule/new/index.html")
        self._throttle()
        result = _fetch_text(url, timeout=self.timeout, data=data, referer=referer)
        payload = json.loads(result.text)
        rows: list[dict] = []
        for item in payload.get("data") or []:
            title = _clean_title(item.get("doctitle"))
            detail_url = item.get("docpuburl") or ""
            if detail_url.startswith("http://"):
                detail_url = "https://" + detail_url[len("http://"):]
            detail_id = _url_to_detail_id(detail_url)
            if not title or not detail_id:
                continue
            released = None
            if item.get("docpubtime"):
                released = datetime.fromtimestamp(int(item["docpubtime"]) / 1000).date().isoformat()
            rows.append(
                {
                    "detail_id": detail_id,
                    "title": title,
                    "released_at": released,
                    "published_at": released,
                    "url": detail_url,
                    "status": "current",
                    "source": self.config.source,
                }
            )
        return rows

    def _search_bse_api(self, query: str, *, page_size: int) -> list[dict]:
        rows: list[dict] = []
        nodes = self.config.search_api_nodes or (
            self.config.search_api_channel or ""
        ).split(",")
        nodes = tuple(node.strip() for node in nodes if node.strip())
        if not nodes:
            return rows

        referer = urljoin(
            self.config.base_url.rstrip("/") + "/",
            self.config.homepage_path.lstrip("/"),
        )
        endpoint = urljoin(self.config.base_url.rstrip("/") + "/", "info/listseSub.do?t=0")
        fields = [
            "infoId",
            "title",
            "htmlUrl",
            "metaDescription",
            "subTitle",
            "fileUrl",
            "fileName",
            "linkUrl",
            "mlinkUrl",
            "picURL",
            "nodeId",
            "p1",
            "potenctLevel",
            "publishDate",
        ]

        for node_id in nodes:
            if len(rows) >= page_size:
                break
            data = urlencode(
                {
                    "keywords": "",
                    "startTime": "",
                    "endTime": "",
                    "nodeIds": [node_id],
                    "page": "0",
                    "pageSize": str(max(page_size, 1)),
                    "needFields": fields,
                },
                doseq=True,
            ).encode("utf-8")
            self._throttle()
            result = self._fetch_bse_jsonp(
                endpoint,
                data=data,
                referer=referer,
            )
            payload = _parse_jsonp_payload(result.text)
            rows.extend(
                self._bse_rows_from_payload(
                    payload,
                    query=query,
                    page_size=max(page_size - len(rows), 1),
                )
            )
        return rows

    def _fetch_bse_jsonp(
        self,
        url: str,
        *,
        data: bytes,
        referer: str,
    ) -> FetchResult:
        """Fetch BSE JSONP with the same cookie jar used for the referer page."""

        opener = build_opener(HTTPCookieProcessor(http.cookiejar.CookieJar()))
        opener.open(_build_request(referer), timeout=self.timeout).close()
        req = _build_request(url, data=data, referer=referer)
        with opener.open(req, timeout=self.timeout) as resp:
            charset = resp.headers.get_content_charset() or "utf-8"
            body = resp.read().decode(charset, errors="replace")
            return FetchResult(
                url=resp.geturl(),
                status_code=resp.getcode(),
                headers=dict(resp.headers.items()),
                text=body,
            )

    def _bse_rows_from_payload(
        self,
        payload: object,
        *,
        query: str,
        page_size: int,
    ) -> list[dict]:
        rows: list[dict] = []
        if not isinstance(payload, list) or not payload:
            return rows
        first = payload[0]
        if not isinstance(first, dict) or first.get("result") is not True:
            return rows
        data = first.get("data") or {}
        content = data.get("content") if isinstance(data, dict) else None
        if not isinstance(content, list):
            return rows

        for item in content:
            if not isinstance(item, dict):
                continue
            title = _clean_title(item.get("title"))
            if not title or not _matches_query(title, query):
                continue
            detail_url = item.get("linkUrl") or item.get("htmlUrl") or item.get("fileUrl")
            if not detail_url:
                continue
            detail_url = urljoin(
                self.config.base_url.rstrip("/") + "/",
                str(detail_url).lstrip("/"),
            )
            detail_id = _url_to_detail_id(detail_url)
            if not detail_id:
                continue
            released = _date_part(item.get("publishDate"))
            rows.append(
                {
                    "detail_id": detail_id,
                    "title": title,
                    "released_at": released,
                    "published_at": released,
                    "url": detail_url,
                    "status": "current",
                    "source": self.config.source,
                }
            )
            if len(rows) >= max(page_size, 1):
                break
        return rows

    @staticmethod
    def _dedupe_rows(rows: list[dict]) -> list[dict]:
        out: list[dict] = []
        seen: set[str] = set()
        for row in rows:
            key = str(row.get("detail_id") or row.get("url") or row.get("title"))
            if not key or key in seen:
                continue
            out.append(row)
            seen.add(key)
        out.sort(key=lambda row: row.get("released_at") or "", reverse=True)
        return out

    def detail_url(self, detail_id: str) -> str:
        return _detail_url(self.config.base_url, detail_id)

    def fetch_detail(self, detail_id: str) -> dict:
        url = self.detail_url(detail_id)
        self._throttle()
        if FILE_EXT_RE.search(url):
            final_url, headers, content = _fetch_bytes(
                url,
                timeout=self.timeout,
                referer=self.config.base_url,
            )
            title = _clean_title(Path(urlparse(final_url).path).name)
            attachment = {
                "url": final_url,
                "title": title,
                "bytes": content,
                "headers": headers,
            }
            return {
                "source": self.config.source,
                "detail_id": _url_to_detail_id(final_url) or detail_id,
                "url": final_url,
                "title": title,
                "content_html": "",
                "content_text": "",
                "attachments": [attachment],
                "selected_attachment": attachment,
                "published_at": None,
                "source_last_modified": headers.get("Last-Modified"),
                "source_etag": headers.get("ETag"),
                "checked_at": datetime.now(timezone.utc).isoformat(),
            }

        result = _fetch_text(url, timeout=self.timeout)
        title = _title_from_html(result.text, self.config.title_suffixes) or detail_id
        content_html = _extract_content_html(result.text, self.config.content_markers)
        if not content_html:
            content_html = result.text
        attachments = _extract_attachments(
            result.text,
            base_url=self.config.base_url,
            referer_url=result.url,
        )
        selected = _choose_attachment(attachments, detail_title=title)
        return {
            "source": self.config.source,
            "detail_id": _url_to_detail_id(result.url) or detail_id,
            "url": result.url,
            "raw_title": _title_from_html(result.text, ()),
            "title": title,
            "content_html": content_html,
            "content_text": _html_to_text(content_html),
            "attachments": attachments,
            "selected_attachment": selected,
            "published_at": _latest_date(content_html),
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
        selected = dict(detail.get("selected_attachment") or {})
        title = self._canonical_title(detail, selected, search_row)
        source_bytes: bytes | None = None
        raw_text = detail.get("content_text") or ""
        source_url = detail.get("url")

        if selected:
            source_url = selected.get("url") or source_url
            try:
                if "bytes" in selected:
                    source_bytes = selected["bytes"]
                else:
                    self._throttle()
                    final_url, _headers, source_bytes = _fetch_bytes(
                        str(selected["url"]),
                        timeout=self.timeout,
                        referer=str(detail.get("url") or self.config.base_url),
                    )
                    source_url = final_url
                raw_text = self._attachment_text(str(source_url), source_bytes)
                if not _has_article_text(raw_text):
                    raw_text = detail.get("content_text") or raw_text
            except (HTTPError, URLError, OSError, TimeoutError, ValueError) as exc:
                if not _has_article_text(detail.get("content_text")):
                    raise exc
                raw_text = detail.get("content_text") or raw_text
                source_bytes = raw_text.encode("utf-8")
                source_url = detail.get("url") or source_url
        else:
            source_bytes = raw_text.encode("utf-8")

        if not raw_text.strip():
            raise ValueError(f"{self.config.source} detail {detail_id} produced empty text")

        metadata = {
            "id": f"{self.config.source}:{detail['detail_id']}",
            "title": title,
            "short_title": cleaning.infer_short_title(title),
            "level": "self_regulatory_rule",
            "status": "current",
            "issuing_body": self.config.issuing_body,
            "document_number": extract_document_number(
                "\n".join(
                    part
                    for part in (
                        detail.get("content_text"),
                        raw_text[:1200],
                        (search_row or {}).get("title"),
                    )
                    if part
                )
            ),
            "released_at": (
                _latest_date(detail.get("content_text"))
                or (search_row or {}).get("released_at")
                or _date_part(detail.get("published_at"))
            ),
            "effective_at": _infer_effective_at(raw_text)
            or _infer_effective_at(detail.get("content_text")),
            "source_url": source_url,
            "source_name": self.config.source_name,
            "source_checked_at": detail.get("checked_at"),
            "source_hash": self._hash_bytes(source_bytes or raw_text.encode("utf-8")),
        }

        if source_url and str(source_url).lower().split("?", 1)[0].endswith(".docx"):
            payload = cleaning.canonicalize(source_bytes or b"", source_kind="docx", **metadata)
        else:
            payload = cleaning.canonicalize(raw_text, source_kind="markdown", **metadata)
        if not payload.get("articles"):
            raise ValueError(f"{self.config.source} detail {detail_id} produced no article clauses")
        return payload

    @staticmethod
    def _hash_bytes(content: bytes) -> str:
        return hashlib.sha256(content).hexdigest()

    @staticmethod
    def _attachment_text(source_url: str, source_bytes: bytes) -> str:
        lower = source_url.lower().split("?", 1)[0]
        if lower.endswith(".pdf"):
            return _pdf_bytes_to_text(source_bytes)
        if lower.endswith(".txt"):
            return source_bytes.decode("utf-8", errors="replace")
        if lower.endswith((".doc", ".docx")):
            articles = cleaning.parse_articles_from_word_bytes(source_bytes)
            return "\n".join(
                f"{item.get('number_display') or item.get('number')} {item.get('text') or ''}"
                for item in articles
            )
        return source_bytes.decode("utf-8", errors="replace")

    @staticmethod
    def _canonical_title(detail: dict, selected: dict, search_row: dict | None) -> str:
        for candidate in (
            selected.get("title"),
            _book_title(detail.get("title")),
            _book_title((search_row or {}).get("title")),
            detail.get("title"),
            (search_row or {}).get("title"),
        ):
            title = _clean_title(candidate)
            if (
                title
                and not _generic_file_title(title)
                and not any(
                    token in _compact(title)
                    for token in ("修订说明", "起草说明", "通知")
                )
            ):
                return title
        return _clean_title(detail.get("title")) or str(detail.get("detail_id"))

    def source_hash(self, detail_id: str) -> str:
        detail = self.fetch_detail(detail_id)
        selected = detail.get("selected_attachment") or {}
        if selected:
            if "bytes" in selected:
                return self._hash_bytes(selected["bytes"])
            _, _, content = _fetch_bytes(
                str(selected["url"]),
                timeout=self.timeout,
                referer=str(detail.get("url") or ""),
            )
            return self._hash_bytes(content)
        return self._hash_bytes((detail.get("content_text") or "").encode("utf-8"))


def _parse_jsonp_payload(text: str) -> object:
    stripped = (text or "").strip()
    if not stripped:
        return None
    match = re.match(r"^[\w$.]+\((?P<body>.*)\)\s*;?$", stripped, re.DOTALL)
    if match:
        stripped = match.group("body").strip()
    return json.loads(stripped)
