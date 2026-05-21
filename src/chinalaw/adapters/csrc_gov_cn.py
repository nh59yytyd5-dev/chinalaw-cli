"""中国证监会主站（www.csrc.gov.cn）adapter。

本 adapter 覆盖证监会官网公开的规章正文页，目标是让证券法工作流能用
``fetch`` / ``verify-source`` 直接补全证监会令类部门规章，而不是让 agent
各自 WebFetch 后手工清洗。

当前官方站点存在两类常见页面：

- ``/csrc/c106256/.../content.shtml``：规章正文页，正文在 ``.content-body``；
- ``/csrc/c101864/.../content.shtml``：政府信息公开详情页，正文在
  ``.Custom_UnionStyle``，有些只提供附件。

搜索使用站内 ``/guestweb4/s`` 表单 POST。结果页会把更权威的"规章"正文页
作为相似文章嵌入主结果，本 adapter 会同时抽取主结果和相似文章，并让 fetch
按 exact-title + current + released_at 选择正文页。
"""

from __future__ import annotations

import hashlib
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
from urllib.request import Request, urlopen

from chinalaw import cleaning
from chinalaw.adapters import _html as _adapter_html

DEFAULT_BASE_URL = "https://www.csrc.gov.cn"
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

SEARCH_PATH = "/guestweb4/s"
DETAIL_RE = re.compile(
    r"/?(?P<path>(?:csrc|[\w-]+)/c\d+/c[\w-]+)/content\.shtml",
    re.IGNORECASE,
)
META_RE_TEMPLATE = r'<meta\s+name=["\']{name}["\']\s+content=["\'](?P<value>.*?)["\']\s*/?>'
TITLE_ATTR_RE = re.compile(r'\btitle=["\'](?P<title>[^"\']+)["\']', re.IGNORECASE)
HREF_RE = re.compile(r'\bhref=["\'](?P<href>[^"\']*content\.shtml\s*)["\']', re.IGNORECASE)
PDF_HREF_RE = re.compile(
    r'<a\b(?P<attrs>[^>]*\bhref=["\'](?P<href>[^"\']+\.pdf)["\'][^>]*)>'
    r"(?P<text>.*?)</a>",
    re.IGNORECASE | re.DOTALL,
)
COLUMN_LABEL_RE = re.compile(
    r'<span[^>]*class=["\'][^"\']*\bcolumnLabel\b[^"\']*["\'][^>]*>(?P<label>.*?)</span>',
    re.IGNORECASE | re.DOTALL,
)
DATE_RE = re.compile(r"(?P<date>(?:19|20)\d{2}[-年]\d{1,2}[-月]\d{1,2}日?)")
CHINESE_DATE_RE = re.compile(
    r"(?P<year>(?:19|20)\d{2})年(?P<month>\d{1,2})月(?P<day>\d{1,2})日"
)
CONTENT_BODY_OPEN_RE = re.compile(
    r'<div[^>]*class=["\'][^"\']*\bcontent-body\b[^"\']*["\'][^>]*>',
    re.IGNORECASE,
)
CUSTOM_BODY_OPEN_RE = re.compile(
    r'<div[^>]*class=["\'][^"\']*\bCustom_UnionStyle\b[^"\']*["\'][^>]*>',
    re.IGNORECASE,
)
DETAIL_NEWS_OPEN_RE = re.compile(
    r'<div[^>]*class=["\'][^"\']*\bdetail-news\b[^"\']*["\'][^>]*>',
    re.IGNORECASE,
)
H3_RE = re.compile(r"<h3[^>]*>(?P<title>.*?)</h3>", re.IGNORECASE | re.DOTALL)
H2_RE = re.compile(r"<h2[^>]*>(?P<title>.*?)</h2>", re.IGNORECASE | re.DOTALL)
SUB_TITLE_RE = re.compile(
    r'<p[^>]*class=["\'][^"\']*\bsub-title\b[^"\']*["\'][^>]*>(?P<text>.*?)</p>',
    re.IGNORECASE | re.DOTALL,
)
DOC_NUMBER_PATTERNS = (
    re.compile(r"证监会令\s*(?:第)?\s*(?P<num>\d+)\s*号"),
    re.compile(r"证监会令[【〔\[]\s*第?\s*(?P<num>\d+)\s*号[】〕\]]"),
    re.compile(r"中国证券监督管理委员会令\s*(?:第)?\s*(?P<num>\d+)\s*号"),
)
EFFECTIVE_DATE_RE = re.compile(
    r"自\s*(?P<year>(?:19|20)\d{2})\s*年\s*"
    r"(?P<month>\d{1,2})\s*月\s*(?P<day>\d{1,2})\s*日起施行"
)
ARTICLE_HEADING_RE = re.compile(r"第[一二三四五六七八九十百千万零〇两\d]+条")
PDF_STRUCTURAL_LINE_RE = re.compile(
    r"^(?:第[一二三四五六七八九十百千万零〇两\d]+[章节条]|[（(][一二三四五六七八九十\d]+[）)]|[一二三四五六七八九十]+、)"
)
ARTICLE_REFERENCE_CONTINUATION_RE = re.compile(
    r"^第[一二三四五六七八九十百千万零〇两\d]+条"
    r"(?:规定|所称|所列|第|至|到|、|和|及|的规定|等)"
)

TITLE_SUFFIXES = (
    "_中国证券监督管理委员会",
    " - 中国证券监督管理委员会",
    "- 中国证券监督管理委员会",
)
ISSUER_PREFIXES = ("中国证券监督管理委员会 ", "中国证券监督管理委员会", "证监会 ")
NORMATIVE_COLUMNS = {
    "证监会令",
    "规章",
    "行政规范性文件",
    "证券基金经营机构监管规则",
}


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
    if data is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    return Request(url, headers=headers, data=data, method="POST" if data else "GET")


def _quote_url(url: str) -> str:
    """Quote non-ASCII URL path/query bytes while keeping URL separators."""

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


def _fetch_text(
    url: str, *, timeout: int = DEFAULT_TIMEOUT, data: bytes | None = None
) -> FetchResult:
    req = _build_request(_quote_url(url), data=data)
    with urlopen(req, timeout=timeout) as resp:
        charset = resp.headers.get_content_charset() or "utf-8"
        body = resp.read().decode(charset, errors="replace")
        return FetchResult(
            url=resp.geturl(),
            status_code=resp.getcode(),
            headers=dict(resp.headers.items()),
            text=body,
        )


def _fetch_bytes(url: str, *, timeout: int = DEFAULT_TIMEOUT) -> FetchResult:
    req = _build_request(_quote_url(url))
    with urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("latin1")
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


def _clean_text_fragment(raw: str | None) -> str:
    return re.sub(r"\s+", " ", _html_to_text(raw or "")).strip()


def _clean_title(raw: str | None) -> str:
    text = _clean_text_fragment(raw)
    text = re.sub(r"^【第\d+号令】", "", text).strip()
    return text.strip("《》 ")


def _normalize_detail_id(raw: str | None) -> str | None:
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    if "://" in text or text.startswith("//"):
        if text.startswith("//"):
            text = "https:" + text
        text = urlparse(text).path or ""
    text = text.split("#", 1)[0].split("?", 1)[0].strip()
    match = DETAIL_RE.search(text)
    if match:
        return match.group("path").strip("/")
    if text.endswith("/content.shtml"):
        text = text[: -len("/content.shtml")]
    text = text.strip("/")
    if re.fullmatch(r"(?:csrc|[\w-]+)/c\d+/c[\w-]+", text):
        return text
    return None


def _detail_url(base_url: str, detail_id: str) -> str:
    normalized = _normalize_detail_id(detail_id)
    if not normalized:
        raise ValueError(f"invalid csrc_gov_cn detail_id: {detail_id!r}")
    return urljoin(base_url.rstrip("/") + "/", f"{normalized}/content.shtml")


def _parse_search_rows(html: str, *, base_url: str, page_size: int) -> list[dict]:
    rows: list[dict] = []
    seen: set[str] = set()
    for match in HREF_RE.finditer(html or ""):
        href = unescape(match.group("href")).strip()
        detail_id = _normalize_detail_id(href)
        if not detail_id or detail_id in seen:
            continue
        context_start = max(0, match.start() - 1200)
        context_end = min(len(html), match.end() + 1400)
        context = html[context_start:context_end]
        row_context = _extract_row_context(html, match.start(), match.end())
        # Use only the preceding local context for column classification; a
        # search-result block can contain "similar article" anchors after the
        # main anchor, and using the whole block would misclassify the main
        # news row as a normative "规章" row.
        before_anchor = html[context_start:match.start()]
        column = _extract_column_label(before_anchor)
        if not column:
            continue
        anchor = _extract_anchor_html(html, match.start(), match.end())
        title = _extract_link_title(anchor) or _clean_title(anchor)
        if not title:
            continue
        released_at = _extract_nearest_date(row_context) or _extract_nearest_date(context)
        rows.append(
            {
                "detail_id": detail_id,
                "title": title,
                "released_at": released_at,
                "published_at": released_at,
                "url": _detail_url(base_url, detail_id),
                "status": "current",
                "column": column,
                "source": "csrc_gov_cn",
            }
        )
        seen.add(detail_id)
        if len(rows) >= max(page_size, 1):
            break
    rows.sort(key=_row_sort_key)
    return rows[: max(page_size, 1)]


def _extract_anchor_html(html: str, href_start: int, href_end: int) -> str:
    start = html.rfind("<a", 0, href_start)
    if start < 0:
        start = href_start
    end = html.find("</a>", href_end)
    if end < 0:
        end = href_end
    else:
        end += len("</a>")
    return html[start:end]


def _extract_row_context(html: str, href_start: int, href_end: int) -> str:
    """Return the closest li/div fragment around a result anchor."""

    starts = [html.rfind(tag, 0, href_start) for tag in ("<li", "<div")]
    starts = [idx for idx in starts if idx >= 0]
    start = max(starts) if starts else max(0, href_start - 500)
    ends: list[int] = []
    for tag in ("</li>", "</div>"):
        idx = html.find(tag, href_end)
        if idx >= 0:
            ends.append(idx + len(tag))
    end = min(ends) if ends else min(len(html), href_end + 500)
    return html[start:end]


def _extract_link_title(snippet: str) -> str | None:
    match = TITLE_ATTR_RE.search(snippet)
    if match:
        title = _clean_title(match.group("title"))
        if title:
            return title
    anchor_texts = re.findall(
        r"<a\b[^>]*>(?P<text>.*?)</a>",
        snippet,
        flags=re.IGNORECASE | re.DOTALL,
    )
    for raw in anchor_texts:
        title = _clean_title(raw)
        if title and len(title) <= 120:
            return title
    return None


def _extract_column_label(snippet: str) -> str | None:
    matches = [_clean_text_fragment(m.group("label")) for m in COLUMN_LABEL_RE.finditer(snippet)]
    return matches[-1] if matches else None


def _extract_nearest_date(snippet: str) -> str | None:
    matches = [m.group("date") for m in DATE_RE.finditer(snippet or "")]
    if not matches:
        return None
    return _date_part(matches[-1])


def _row_sort_key(row: dict) -> tuple[int, str]:
    column = row.get("column") or ""
    normative_rank = 0 if column in NORMATIVE_COLUMNS else 1
    date_token = (row.get("released_at") or "0000-00-00").replace("-", "")
    return (normative_rank, f"{99999999 - int(date_token):08d}")


def _meta(html: str, name: str) -> str | None:
    pattern = re.compile(META_RE_TEMPLATE.format(name=re.escape(name)), re.IGNORECASE | re.DOTALL)
    matches = [unescape(m.group("value")).strip() for m in pattern.finditer(html or "")]
    matches = [m for m in matches if m]
    return matches[-1] if matches else None


def _extract_detail_title(html: str) -> str | None:
    for pattern in (H3_RE, H2_RE):
        match = pattern.search(html or "")
        if match:
            title = _clean_title(match.group("title"))
            if title:
                return title
    title = _meta(html, "ArticleTitle") or _strip_title_suffix(_extract_title(html) or "")
    return _clean_title(title)


def _extract_content_html(html: str) -> str:
    for pattern, sentinels in (
        (
            CONTENT_BODY_OPEN_RE,
            ('<div class="fg-foot"', "</body>"),
        ),
        (
            CUSTOM_BODY_OPEN_RE,
            ('<div  id="files"', '<div id="files"', '<div class="xxgk-down-box"', "</body>"),
        ),
        (
            DETAIL_NEWS_OPEN_RE,
            ('<div  id="files"', '<div id="files"', '<div class="xxgk-down-box"', "</body>"),
        ),
    ):
        match = pattern.search(html or "")
        if not match:
            continue
        body = html[match.end():]
        end = len(body)
        for sentinel in sentinels:
            idx = body.find(sentinel)
            if idx >= 0:
                end = min(end, idx)
        return body[:end].strip()
    return ""


def _extract_pdf_attachments(html: str, *, base_url: str) -> list[dict]:
    attachments: list[dict] = []
    seen: set[str] = set()
    for match in PDF_HREF_RE.finditer(html or ""):
        href = unescape(match.group("href")).strip()
        if not href:
            continue
        url = urljoin(base_url, href)
        if url in seen:
            continue
        title = _clean_text_fragment(match.group("text")) or Path(urlparse(url).path).name
        attachments.append({"url": url, "title": title})
        seen.add(url)
    return attachments


def _compact_title(value: str | None) -> str:
    return re.sub(r"[\s《》【】\[\]（）()：:、，,。._-]+", "", value or "")


def _looks_like_rule_pdf(attachment: dict, *, law_title: str) -> bool:
    title = attachment.get("title") or ""
    compact = _compact_title(title)
    law_compact = _compact_title(law_title)
    if any(token in compact for token in ("修订说明", "起草说明", "征求意见", "反馈情况")):
        return False
    return bool(law_compact and law_compact in compact)


def _pdf_bytes_to_text(pdf_bytes: bytes) -> str:
    pdftotext = shutil.which("pdftotext")
    if not pdftotext:
        raise ValueError("csrc_gov_cn PDF attachment extraction requires pdftotext")
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
        raise ValueError(f"csrc_gov_cn PDF text extraction failed: {message}")
    return _clean_pdf_text(result.stdout)


def _clean_pdf_text(text: str) -> str:
    lines: list[str] = []
    normalized = (text or "").replace("\r\n", "\n").replace("\r", "\n").replace("\f", "\n")
    for raw_line in normalized.splitlines():
        stripped = re.sub(r"\s+", " ", raw_line).strip()
        if not stripped:
            continue
        if re.fullmatch(r"\d{1,4}", stripped):
            continue
        if lines and _pdf_line_continues(lines[-1], stripped):
            lines[-1] = lines[-1] + stripped
        else:
            lines.append(stripped)
    return "\n".join(lines)


def _pdf_line_continues(previous: str, current: str) -> bool:
    if ARTICLE_REFERENCE_CONTINUATION_RE.match(current):
        return True
    if PDF_STRUCTURAL_LINE_RE.match(current):
        return False
    return not previous.endswith(("。", "；", "：", "！", "？", "，", "、", "："))


def _has_article_text(text: str | None) -> bool:
    return bool(ARTICLE_HEADING_RE.search(text or ""))


def _extract_subtitle(html: str) -> str | None:
    match = SUB_TITLE_RE.search(html or "")
    return _clean_text_fragment(match.group("text")) if match else None


def _date_part(raw: str | None) -> str | None:
    if not raw:
        return None
    raw = raw.strip()
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


def _latest_chinese_date(text: str) -> str | None:
    dates = [_date_part(match.group(0)) for match in CHINESE_DATE_RE.finditer(text or "")]
    dates = [date for date in dates if date]
    return dates[-1] if dates else None


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
    for pattern in DOC_NUMBER_PATTERNS:
        matches = list(pattern.finditer(text or ""))
        if matches:
            return f"证监会令第{int(matches[-1].group('num'))}号"
    return None


def _infer_short_title(title: str) -> str | None:
    return _adapter_html.infer_short_title(title, site_prefixes=ISSUER_PREFIXES)


def _detect_page_shape(html: str, *, status_code: int) -> str:
    if not (200 <= status_code < 300):
        return "error"
    if "中国证券监督管理委员会" in (html or ""):
        return "ok"
    return "unknown"


@dataclass
class CsrcGovCnAdapter:
    """中国证监会官网 adapter。"""

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
        homepage_url = urljoin(self.base_url.rstrip("/") + "/", "csrc/index.shtml")
        try:
            homepage = _fetch_text(homepage_url, timeout=self.timeout)
        except HTTPError as exc:
            return {
                "source": "csrc_gov_cn",
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
                "source": "csrc_gov_cn",
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
        sections = [name for name in ("规章", "政府信息公开", "政策解读") if name in homepage.text]
        return {
            "source": "csrc_gov_cn",
            "homepage_url": homepage_url,
            "final_url": homepage.url,
            "status_code": homepage.status_code,
            "title": _extract_title(homepage.text),
            "page_shape": _detect_page_shape(homepage.text, status_code=homepage.status_code),
            "detected_sections": sections,
            "bundle_contains_known_sections": bool(sections),
            "source_last_modified": homepage.headers.get("Last-Modified"),
            "source_etag": homepage.headers.get("ETag"),
            "checked_at": checked_at,
        }

    def search_url(self) -> str:
        return urljoin(self.base_url.rstrip("/") + "/", SEARCH_PATH.lstrip("/"))

    def detail_url(self, detail_id: str) -> str:
        return _detail_url(self.base_url, detail_id)

    def search_list(self, query: str | None = None, *, page_size: int = 20) -> dict:
        needle = (query or "").strip()
        data = urlencode(
            {
                "searchWord": needle,
                "uc": "1",
                "siteCode": "bm56000001",
                "column": "全部",
            }
        ).encode("utf-8")
        self._throttle()
        result = _fetch_text(self.search_url(), timeout=self.timeout, data=data)
        rows = _parse_search_rows(result.text, base_url=self.base_url, page_size=page_size)
        return {
            "source": "csrc_gov_cn",
            "query": needle,
            "page": 1,
            "page_size": page_size,
            "total_pages": None,
            "total_count": None,
            "rows": rows,
            "url": result.url,
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }

    def fetch_detail(self, detail_id: str) -> dict:
        normalized = _normalize_detail_id(detail_id)
        if not normalized:
            raise ValueError(f"invalid csrc_gov_cn detail_id: {detail_id!r}")
        url = self.detail_url(normalized)
        self._throttle()
        result = _fetch_text(url, timeout=self.timeout)
        content_html = _extract_content_html(result.text)
        title = _extract_detail_title(result.text) or normalized
        content_text = _html_to_text(content_html)
        attachments = _extract_pdf_attachments(result.text, base_url=result.url)
        selected_attachment = next(
            (
                item
                for item in attachments
                if _looks_like_rule_pdf(item, law_title=title)
            ),
            None,
        )
        attachment_error = None
        if selected_attachment is not None:
            try:
                self._throttle()
                pdf_result = _fetch_bytes(selected_attachment["url"], timeout=self.timeout)
                pdf_bytes = pdf_result.text.encode("latin1")
                pdf_text = _pdf_bytes_to_text(pdf_bytes)
                if _has_article_text(pdf_text):
                    content_text = pdf_text
                    selected_attachment = {
                        **selected_attachment,
                        "final_url": pdf_result.url,
                        "content_type": pdf_result.headers.get("Content-Type"),
                    }
            except (HTTPError, URLError, OSError, TimeoutError, ValueError) as exc:
                attachment_error = str(exc)
        return {
            "source": "csrc_gov_cn",
            "detail_id": normalized,
            "url": result.url,
            "raw_title": _extract_title(result.text),
            "title": title,
            "subtitle": _extract_subtitle(result.text),
            "content_html": content_html,
            "content_text": content_text,
            "attachments": attachments,
            "selected_attachment": selected_attachment,
            "attachment_error": attachment_error,
            "column_name": _meta(result.text, "ColumnName"),
            "source_name_text": _meta(result.text, "ContentSource") or "中国证监会",
            "published_at": _meta(result.text, "PubDate"),
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
            raise ValueError(f"csrc_gov_cn detail {detail_id} produced empty article text")

        title = _clean_title(detail.get("title")) or (search_row or {}).get("title") or detail_id
        subtitle = detail.get("subtitle") or ""
        metadata_text = "\n".join(
            part
            for part in (
                subtitle,
                _html_to_text(detail.get("content_html") or "")[:1500],
                raw_text[:1500],
            )
            if part
        )
        release_metadata = subtitle or _html_to_text(detail.get("content_html") or "")[:1500]
        released_at = (
            _latest_chinese_date(release_metadata)
            or (search_row or {}).get("released_at")
            or _date_part(detail.get("published_at"))
        )
        effective_at = _infer_effective_at(raw_text)
        document_number = _infer_document_number(metadata_text)
        payload = cleaning.canonicalize(
            raw_text,
            source_kind="markdown",
            id=f"csrc_gov_cn:{detail['detail_id']}",
            title=title,
            short_title=_infer_short_title(title),
            level="departmental_rule",
            status="current",
            issuing_body="中国证券监督管理委员会",
            document_number=document_number,
            released_at=released_at,
            effective_at=effective_at,
            source_url=detail.get("url"),
            source_name="www.csrc.gov.cn",
            source_checked_at=detail.get("checked_at"),
            source_hash=self._hash_text(raw_text),
        )
        if not payload.get("articles"):
            message = f"csrc_gov_cn detail {detail_id} produced no article clauses"
            if detail.get("attachment_error"):
                message += f"; PDF attachment extraction failed: {detail['attachment_error']}"
            raise ValueError(message)
        return payload

    @staticmethod
    def _hash_text(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def source_hash(self, detail_id: str) -> str:
        detail = self.fetch_detail(detail_id)
        return self._hash_text(_html_to_text(detail.get("content_html") or ""))


default_adapter = CsrcGovCnAdapter()


def probe(timeout: int = DEFAULT_TIMEOUT) -> dict:
    return CsrcGovCnAdapter(timeout=timeout).probe()


def search_list(query: str | None = None, **kwargs) -> dict:
    return default_adapter.search_list(query, **kwargs)


def fetch_detail(detail_id: str) -> dict:
    return default_adapter.fetch_detail(detail_id)
