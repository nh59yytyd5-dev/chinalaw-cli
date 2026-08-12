"""国家金融监督管理总局 / 原银保监会（nfra.gov.cn）adapter。

NFRA 详情页由 Angular 模板渲染，真实正文通过
``/cbircweb/DocInfo/SelectByDocId`` JSON 接口返回。本 adapter 只暴露
有稳定 ``docId`` 的按需 fetch / bounded discover，不做批量 sync。
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlencode, urljoin, urlparse
from urllib.request import Request

from chinalaw import USER_AGENT_TOKEN, cleaning, netio
from chinalaw.adapters import _html as _adapter_html

DEFAULT_BASE_URL = "https://www.nfra.gov.cn"
DEFAULT_TIMEOUT = 20
DEFAULT_REQUEST_INTERVAL = 0.5
MIN_REQUEST_INTERVAL = 0.1

TOOL_UA_TOKEN = USER_AGENT_TOKEN
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36 "
    f"{TOOL_UA_TOKEN}"
)

TITLE_SUFFIXES = (" - 国家金融监督管理总局", "- 国家金融监督管理总局")
ISSUER_PREFIXES = (
    "国家金融监督管理总局 ",
    "中国银行保险监督管理委员会 ",
    "中国银保监会 ",
)
DOCUMENT_NO_BRACKETS_RE = re.compile(r"[\[\]]")

KNOWN_NFRA_DOCS: tuple[dict[str, str], ...] = (
    {
        "detail_id": "989061",
        "title": "银行保险机构公司治理准则",
        "url": "https://www.nfra.gov.cn/cn/view/pages/ItemDetail.html?docId=989061&itemId=928",
        "document_number": "银保监发〔2021〕14号",
        "issuing_body": "中国银行保险监督管理委员会",
    },
)


@dataclass
class FetchResult:
    url: str
    status_code: int
    headers: Mapping[str, str]
    text: str


def _build_request(url: str, *, accept: str = "application/json") -> Request:
    return Request(
        url,
        headers={
            "User-Agent": DEFAULT_USER_AGENT,
            "Accept": accept,
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.5",
        },
        method="GET",
    )


def _fetch_text(
    url: str,
    timeout: int = DEFAULT_TIMEOUT,
    *,
    accept: str = "application/json",
) -> FetchResult:
    req = _build_request(url, accept=accept)
    response = netio.request_bytes(
        req,
        policy=netio.source_policy("nfra_gov_cn", timeout=timeout),
        max_bytes=netio.MAX_TEXT_BYTES,
    )
    return FetchResult(
        url=response.url,
        status_code=response.status_code,
        headers=response.headers,
        text=response.content.decode(
            netio.response_charset(response.headers),
            errors="replace",
        ),
    )


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
    doc_id = (query.get("docId") or query.get("docid") or [None])[0]
    if doc_id and str(doc_id).isdigit():
        return str(doc_id)
    match = re.search(r"docId=(\d+)", text, re.IGNORECASE)
    return match.group(1) if match else None


def _detail_url(base_url: str, detail_id: str) -> str:
    normalized = _normalize_detail_id(detail_id)
    if not normalized:
        raise ValueError(f"invalid nfra_gov_cn detail_id: {detail_id!r}")
    for item in KNOWN_NFRA_DOCS:
        if item["detail_id"] == normalized:
            return item["url"]
    return urljoin(
        base_url.rstrip("/") + "/",
        f"cn/view/pages/ItemDetail.html?docId={normalized}",
    )


def _api_url(base_url: str, detail_id: str) -> str:
    normalized = _normalize_detail_id(detail_id)
    if not normalized:
        raise ValueError(f"invalid nfra_gov_cn detail_id: {detail_id!r}")
    return urljoin(
        base_url.rstrip("/") + "/",
        f"cbircweb/DocInfo/SelectByDocId?{urlencode({'docId': normalized})}",
    )


def _clean_text(raw: str | None) -> str:
    return re.sub(r"\s+", " ", _adapter_html.html_to_text(raw or "")).strip()


def _strip_title_suffix(raw_title: str) -> str:
    return _adapter_html.strip_known_title_suffix(raw_title, TITLE_SUFFIXES)


def _html_to_text(content_html: str) -> str:
    return _adapter_html.html_to_text(content_html)


def _clean_doc_clob_html(html: str | None) -> str:
    text = html or ""
    text = re.sub(r"(?is)<head\b.*?</head>", "", text)
    text = re.sub(r"(?is)<!--.*?-->", "", text)
    text = re.sub(r"(?is)<style\b.*?</style>", "", text)
    text = re.sub(r"(?is)<script\b.*?</script>", "", text)
    return text


def _date_part(raw: str | None) -> str | None:
    if not raw:
        return None
    match = re.search(r"((?:19|20)\d{2})[-年](\d{1,2})[-月](\d{1,2})", str(raw))
    if not match:
        return None
    return f"{int(match.group(1)):04d}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"


def _normalize_document_number(raw: str | None) -> str | None:
    if not raw:
        return None
    value = _clean_text(raw)
    value = value.replace("[", "〔").replace("]", "〕")
    return re.sub(r"\s+", "", value) or None


def _known_rows(query: str, *, page_size: int) -> list[dict]:
    needle = re.sub(r"\s+", "", query or "")
    rows: list[dict] = []
    for item in KNOWN_NFRA_DOCS:
        title = item["title"]
        compact = re.sub(r"\s+", "", title)
        if needle and needle not in compact and compact not in needle:
            continue
        rows.append(
            {
                "detail_id": item["detail_id"],
                "title": title,
                "released_at": None,
                "effective_at": None,
                "url": item["url"],
                "status": "current",
                "document_number": item.get("document_number"),
                "issuing_body": item.get("issuing_body"),
            }
        )
        if len(rows) >= page_size:
            break
    return rows


def _title_from_detail(data: dict, search_row: dict | None = None) -> str:
    if search_row and search_row.get("title"):
        return str(search_row["title"]).strip()
    title = _clean_text(data.get("title"))
    if title:
        return title
    doc_subtitle = _clean_text(data.get("docSubtitle"))
    for item in KNOWN_NFRA_DOCS:
        if item["title"] in doc_subtitle:
            return item["title"]
    return _strip_title_suffix(doc_subtitle or _clean_text(data.get("docTitle")))


def _infer_short_title(title: str) -> str | None:
    short = _adapter_html.infer_short_title(title, site_prefixes=ISSUER_PREFIXES)
    if short:
        return short
    cleaned = re.sub(r"\s+", "", title or "")
    return cleaned if 2 <= len(cleaned) <= 30 else None


@dataclass
class NfraGovCnAdapter:
    """国家金融监督管理总局 adapter。"""

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
        checked_at = datetime.now(timezone.utc).isoformat()
        return {
            "source": "nfra_gov_cn",
            "homepage_url": self.base_url,
            "final_url": self.base_url,
            "status_code": None,
            "title": "国家金融监督管理总局",
            "page_shape": "json_detail_api",
            "detected_sections": ["DocInfo/SelectByDocId"],
            "bundle_contains_known_sections": True,
            "source_last_modified": None,
            "source_etag": None,
            "checked_at": checked_at,
        }

    def detail_url(self, detail_id: str) -> str:
        return _detail_url(self.base_url, detail_id)

    def api_url(self, detail_id: str) -> str:
        return _api_url(self.base_url, detail_id)

    def search_list(self, query: str | None = None, *, page_size: int = 20) -> dict:
        needle = (query or "").strip()
        rows = _known_rows(needle, page_size=page_size)
        return {
            "source": "nfra_gov_cn",
            "query": needle,
            "page": 1,
            "page_size": page_size,
            "total_pages": 1 if rows else 0,
            "total_count": len(rows),
            "rows": rows,
            "url": self.base_url,
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }

    def fetch_detail(self, detail_id: str) -> dict:
        normalized = _normalize_detail_id(detail_id)
        if not normalized:
            raise ValueError(f"invalid nfra_gov_cn detail_id: {detail_id!r}")
        self._throttle()
        result = _fetch_text(self.api_url(normalized), timeout=self.timeout)
        payload = json.loads(result.text)
        if payload.get("rptCode") != 200 or not payload.get("data"):
            raise ValueError(f"nfra_gov_cn detail {normalized} returned invalid payload")
        data = payload["data"]
        content_html = _clean_doc_clob_html(data.get("docClob"))
        content_text = _html_to_text(content_html)
        return {
            "source": "nfra_gov_cn",
            "detail_id": normalized,
            "url": self.detail_url(normalized),
            "api_url": result.url,
            "raw_title": data.get("docTitle"),
            "title": _title_from_detail(data),
            "content_html": content_html,
            "content_text": content_text,
            "document_number": _normalize_document_number(data.get("documentNo")),
            "issuing_body": "中国银行保险监督管理委员会",
            "published_at": data.get("builddate") or data.get("publishDate"),
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
            raise ValueError(f"nfra_gov_cn detail {detail_id} produced empty article text")
        title = _title_from_detail(detail, search_row=search_row)
        payload = cleaning.canonicalize(
            raw_text,
            source_kind="markdown",
            id=f"nfra_gov_cn:{detail['detail_id']}",
            title=title,
            short_title=_infer_short_title(title),
            level="department_rule",
            status=_adapter_html.status_from_current_listing(search_row),
            issuing_body=detail.get("issuing_body"),
            document_number=detail.get("document_number")
            or (search_row or {}).get("document_number"),
            released_at=(search_row or {}).get("released_at")
            or _date_part(detail.get("published_at")),
            effective_at=None,
            source_url=detail.get("url"),
            source_name="www.nfra.gov.cn",
            source_checked_at=detail.get("checked_at"),
            source_hash=self._hash_text(raw_text),
        )
        if not payload.get("articles"):
            raise ValueError(f"nfra_gov_cn detail {detail_id} produced no article clauses")
        return payload

    @staticmethod
    def _hash_text(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def source_hash(self, detail_id: str) -> str:
        detail = self.fetch_detail(detail_id)
        return self._hash_text(detail.get("content_text") or "")


default_adapter = NfraGovCnAdapter()


def probe(timeout: int = DEFAULT_TIMEOUT) -> dict:
    adapter = NfraGovCnAdapter(timeout=timeout)
    return adapter.probe()


def search_list(query: str | None = None, **kwargs) -> dict:
    return default_adapter.search_list(query, **kwargs)


def fetch_detail(detail_id: str) -> dict:
    return default_adapter.fetch_detail(detail_id)


def build_law_payload(detail_id: str, **kwargs) -> dict:
    return default_adapter.build_law_payload(detail_id, **kwargs)
