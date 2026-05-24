"""国家法律法规数据库（flk.npc.gov.cn）适配器。"""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from html import unescape
from urllib.parse import urlencode, urljoin
from urllib.request import Request, urlopen

from chinalaw import USER_AGENT_TOKEN, cleaning

DEFAULT_TIMEOUT = 10
DEFAULT_REQUEST_INTERVAL = 0.2
# 节流硬下限。任何调用方传入 ``<= 0`` 或低于此值的间隔都会被 clamp 到
# ``MIN_REQUEST_INTERVAL``，无法在 adapter 层关闭节流。详见 docs/COMPLIANCE.md §3。
MIN_REQUEST_INTERVAL = 0.1

# 完整 chinalaw-cli 标识，含仓库链接以便上游识别 / 联系；详见 docs/COMPLIANCE.md §4。
DEFAULT_USER_AGENT = USER_AGENT_TOKEN
BASE_URL = "https://flk.npc.gov.cn"
HOMEPAGE_URL = f"{BASE_URL}/"

KNOWN_SECTION_LABELS = (
    "宪法",
    "法律",
    "行政法规",
    "监察法规",
    "司法解释",
    "地方性法规",
)

SEARCH_LIST_PATH = "/law-search/search/list"
LAW_DETAIL_PATH = "/law-search/search/flfgDetails"
HIT_DISPLAY_PATH = "/law-search/search/hitDisplay"
RELATED_RESOURCES_PATH = "/law-search/search/xgzl"
RELATED_FILE_PATH = "/law-search/search/xgwjDetails"
RECOMMENDATIONS_PATH = "/law-search/search/recommend"
DOWNLOAD_INFO_PATH = "/law-search/download/pc"
DOWNLOAD_MOBILE_PATH = "/law-search/download/mobile"
ENUM_DATA_PATH = "/law-search/search/enumData"

WAF_MARKERS = (
    "Please enable JavaScript",
    "请开启JavaScript",
    "wzws-waf-cgi",
    "jsjiami.com",
)

DEFAULT_SEARCH_PAYLOAD = {
    "searchContent": "",
    "searchRange": 1,
    "searchType": 2,
    "sxrq": [],
    "gbrq": [],
    "sxx": [],
    "gbrqYear": [],
    "flfgCodeId": [],
    "zdjgCodeId": [],
    "orderByParam": {"order": "-1", "sort": ""},
    "pageNum": 1,
    "pageSize": 20,
}

FLXZ_TO_LEVEL = cleaning.FLXZ_TO_LEVEL
SXX_TO_STATUS = cleaning.SXX_TO_STATUS


@dataclass
class FetchResult:
    url: str
    status_code: int
    headers: Mapping[str, str]
    text: str


@dataclass
class BinaryFetchResult:
    url: str
    status_code: int
    headers: Mapping[str, str]
    content: bytes


def _build_request(
    url: str,
    *,
    method: str = "GET",
    accept: str,
    data: object | None = None,
) -> Request:
    headers = {
        "User-Agent": DEFAULT_USER_AGENT,
        "Accept": accept,
    }
    body = None
    if data is not None:
        headers["Content-Type"] = "application/json;charset=utf-8"
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    return Request(url, data=body, headers=headers, method=method.upper())


def _fetch_text(url: str, timeout: int = DEFAULT_TIMEOUT) -> FetchResult:
    req = _build_request(url, accept="text/html,application/javascript;q=0.9,*/*;q=0.8")
    with urlopen(req, timeout=timeout) as resp:
        charset = resp.headers.get_content_charset() or "utf-8"
        body = resp.read().decode(charset, errors="replace")
        return FetchResult(
            url=resp.geturl(),
            status_code=resp.getcode(),
            headers=dict(resp.headers.items()),
            text=body,
        )


def _fetch_bytes(url: str, timeout: int = DEFAULT_TIMEOUT) -> BinaryFetchResult:
    req = _build_request(url, accept="application/octet-stream,*/*;q=0.8")
    with urlopen(req, timeout=timeout) as resp:
        return BinaryFetchResult(
            url=resp.geturl(),
            status_code=resp.getcode(),
            headers=dict(resp.headers.items()),
            content=resp.read(),
        )


def _looks_like_waf_challenge(text: str) -> bool:
    return any(marker in text for marker in WAF_MARKERS)


def _snippet(text: str, limit: int = 120) -> str:
    return re.sub(r"\s+", " ", text).strip()[:limit]


def _unexpected_text_response_message(
    *,
    url: str,
    status_code: int,
    content_type: str | None,
    text: str,
    expected: str,
) -> str:
    if _looks_like_waf_challenge(text):
        reason = "FLK returned anti-bot JavaScript challenge"
    elif text.lstrip().startswith("<"):
        reason = "FLK returned HTML"
    else:
        reason = "FLK returned unexpected non-Word/non-JSON response"
    return (
        f"{reason}; expected {expected}; status={status_code}; "
        f"content_type={content_type or 'unknown'}; url={url}; "
        f"snippet={_snippet(text)!r}"
    )


def _ensure_word_bytes(result: BinaryFetchResult) -> bytes:
    if result.content.startswith((b"PK", cleaning.OLE_WORD_MAGIC)):
        return result.content
    text = result.content[:2048].decode("utf-8", errors="ignore")
    raise ValueError(
        _unexpected_text_response_message(
            url=result.url,
            status_code=result.status_code,
            content_type=result.headers.get("Content-Type"),
            text=text,
            expected="DOCX zip or legacy OLE Word bytes",
        )
    )


def _extract_title(html: str) -> str | None:
    match = re.search(r"<title>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    if not match:
        return None
    return unescape(match.group(1).strip())


def _extract_asset_url(html: str, tag: str, extension: str, base_url: str) -> str | None:
    pattern = (
        rf"<{tag}\b[^>]*\b(?:src|href)=[\"'](?P<path>[^\"']+\.{extension}[^\"']*)[\"'][^>]*>"
    )
    match = re.search(pattern, html, re.IGNORECASE)
    if not match:
        return None
    return urljoin(base_url, match.group("path"))


def _detect_page_shape(html: str) -> str:
    if 'id="app"' in html and 'type="module"' in html:
        return "spa"
    return "html"


def _detect_labels(bundle_text: str) -> list[str]:
    return [label for label in KNOWN_SECTION_LABELS if label in bundle_text]


def parse_articles_from_docx(docx_bytes: bytes) -> list[dict]:
    return cleaning.parse_articles_from_docx(docx_bytes)


def _flatten_category_tree(node: dict, parent_id: str | None = None) -> list[dict]:
    current_id = f"flk:{node['id']}"
    categories = [
        {
            "id": current_id,
            "name": node.get("name") or "未命名分类",
            "parent_id": parent_id,
            "description": f"flk codeId={node.get('codeId')}",
        }
    ]
    for child in node.get("children", []):
        categories.extend(_flatten_category_tree(child, current_id))
    return categories


def _category_ids_from_code(tree: dict, code_id: int | None) -> list[str]:
    if code_id is None:
        return []
    if tree.get("codeId") == code_id:
        return [f"flk:{tree['id']}"]
    for child in tree.get("children", []):
        chain = _category_ids_from_code(child, code_id)
        if chain:
            return [f"flk:{tree['id']}", *chain]
    return []


@dataclass
class FlkNpcAdapter:
    base_url: str = BASE_URL
    timeout: int = DEFAULT_TIMEOUT
    request_interval: float = DEFAULT_REQUEST_INTERVAL
    _last_request_at: float = 0.0

    def _throttle(self) -> None:
        """Keep real FLK requests modest during batch fetches.

        The public site may return a JavaScript challenge when detail/download
        endpoints are hit in bursts. This is intentionally adapter-local so
        tests and higher-level code do not need to duplicate pacing logic.

        节流硬下限：传入 ``<= 0`` 或低于 ``MIN_REQUEST_INTERVAL`` 的值都会
        被 clamp 到下限。详见 docs/COMPLIANCE.md §3。
        """

        interval = max(float(self.request_interval or 0), MIN_REQUEST_INTERVAL)
        now = time.monotonic()
        elapsed = now - self._last_request_at
        if 0 <= elapsed < interval:
            time.sleep(interval - elapsed)
        self._last_request_at = time.monotonic()

    def probe(self) -> dict:
        checked_at = datetime.now(timezone.utc).isoformat()
        base = self.base_url if self.base_url.endswith("/") else f"{self.base_url}/"
        homepage_url = urljoin(base, "/")
        homepage = _fetch_text(homepage_url, timeout=self.timeout)
        main_script_url = _extract_asset_url(homepage.text, "script", "js", homepage.url)
        stylesheet_url = _extract_asset_url(homepage.text, "link", "css", homepage.url)

        bundle_labels: list[str] = []
        if main_script_url:
            bundle = _fetch_text(main_script_url, timeout=self.timeout)
            bundle_labels = _detect_labels(bundle.text)

        return {
            "source": "flk_npc",
            "homepage_url": homepage_url,
            "final_url": homepage.url,
            "status_code": homepage.status_code,
            "title": _extract_title(homepage.text),
            "page_shape": _detect_page_shape(homepage.text),
            "main_script_url": main_script_url,
            "stylesheet_url": stylesheet_url,
            "detected_sections": bundle_labels,
            "bundle_contains_known_sections": bool(bundle_labels),
            "source_last_modified": homepage.headers.get("Last-Modified"),
            "source_etag": homepage.headers.get("ETag"),
            "checked_at": checked_at,
        }

    def build_search_payload(
        self,
        search_content: str,
        *,
        page_num: int = 1,
        page_size: int = 20,
        search_range: int = 1,
        search_type: int = 2,
        order: str = "-1",
        sort: str = "",
        **overrides,
    ) -> dict:
        payload = deepcopy(DEFAULT_SEARCH_PAYLOAD)
        payload.update(
            {
                "searchContent": search_content,
                "searchRange": search_range,
                "searchType": search_type,
                "pageNum": page_num,
                "pageSize": page_size,
                "orderByParam": {"order": order, "sort": sort},
            }
        )
        payload.update(overrides)
        return payload

    def search_list(
        self,
        search_content: str,
        *,
        page_num: int = 1,
        page_size: int = 20,
        search_range: int = 1,
        search_type: int = 2,
        **overrides,
    ) -> dict:
        payload = self.build_search_payload(
            search_content,
            page_num=page_num,
            page_size=page_size,
            search_range=search_range,
            search_type=search_type,
            **overrides,
        )
        return self._request_json(SEARCH_LIST_PATH, method="POST", data=payload)

    def list_laws(
        self,
        *,
        since: str | None = None,
        until: str | None = None,
        page_num: int = 1,
        page_size: int = 20,
    ) -> dict:
        gbrq = []
        if since or until:
            gbrq = [since or "", until or ""]
        return self.search_list(
            "",
            page_num=page_num,
            page_size=page_size,
            gbrq=gbrq,
            order="gbrq",
            sort="DESC",
        )

    def fetch_law_detail(self, bbbs: str) -> dict:
        return self._request_json(LAW_DETAIL_PATH, params={"bbbs": bbbs})

    def fetch_hit_display(
        self,
        bbbs: str,
        *,
        search_content: str,
        search_type: int = 2,
        search_range: int = 1,
    ) -> dict:
        return self._request_json(
            HIT_DISPLAY_PATH,
            method="POST",
            data={
                "bbbs": bbbs,
                "searchContent": search_content,
                "searchType": search_type,
                "searchRange": search_range,
            },
        )

    def fetch_related_resources(
        self,
        bbbs: str,
        *,
        search_content: str,
        search_type: int = 2,
        search_range: int = 1,
    ) -> dict:
        return self._request_json(
            RELATED_RESOURCES_PATH,
            method="POST",
            data={
                "bbbs": bbbs,
                "searchContent": search_content,
                "searchType": search_type,
                "searchRange": search_range,
            },
        )

    def fetch_related_file_detail(self, bbbs: str) -> dict:
        return self._request_json(RELATED_FILE_PATH, params={"bbbs": bbbs})

    def fetch_recommendations(self, bbbs: str) -> dict:
        return self._request_json(RECOMMENDATIONS_PATH, params={"bbbs": bbbs})

    def fetch_enum_data(self) -> dict:
        return self._request_json(ENUM_DATA_PATH)

    def build_category_tree_payload(self) -> dict:
        enum_data = self.fetch_enum_data()
        flfg_tree = ((enum_data.get("data") or {}).get("flfgfl")) or {}
        categories = _flatten_category_tree(flfg_tree)
        return {
            "source": "flk_npc",
            "categories": categories,
            "root": flfg_tree,
        }

    def get_download_info(
        self,
        bbbs: str,
        *,
        file_id: str = "",
        format_name: str = "docx",
    ) -> dict:
        return self._request_json(
            DOWNLOAD_INFO_PATH,
            params={"format": format_name, "bbbs": bbbs, "fileId": file_id},
        )

    def download_docx_bytes(self, bbbs: str, *, file_id: str = "") -> bytes:
        download_info = self.get_download_info(bbbs, file_id=file_id, format_name="docx")
        url = (((download_info.get("data") or {}).get("url")) or "").strip()
        if not url:
            query = urlencode({"format": "docx", "bbbs": bbbs, "fileId": file_id})
            direct_url = urljoin(
                self.base_url if self.base_url.endswith("/") else f"{self.base_url}/",
                f"{DOWNLOAD_MOBILE_PATH}?{query}",
            )
            self._throttle()
            return _ensure_word_bytes(_fetch_bytes(direct_url, timeout=self.timeout))
        self._throttle()
        return _ensure_word_bytes(_fetch_bytes(url, timeout=self.timeout))

    def build_law_payload(
        self,
        bbbs: str,
        *,
        search_row: dict | None = None,
        detail_payload: dict | None = None,
        docx_bytes: bytes | None = None,
    ) -> dict:
        detail_payload = detail_payload or self.fetch_law_detail(bbbs)
        detail_data = detail_payload.get("data") or {}
        docx_bytes = docx_bytes or self.download_docx_bytes(
            bbbs,
            file_id=(detail_data.get("fileId") or ""),
        )
        checked_at = datetime.now(timezone.utc).isoformat()
        enum_payload = self.build_category_tree_payload()
        category_code = (
            detail_data.get("flfgCodeId")
            if detail_data.get("flfgCodeId") is not None
            else (search_row or {}).get("flfgCodeId")
        )
        category_ids = _category_ids_from_code(enum_payload["root"], category_code)

        return cleaning.canonicalize(
            detail_payload,
            source_kind="flk_npc_detail",
            bbbs=bbbs,
            docx_bytes=docx_bytes,
            base_url=self.base_url,
            checked_at=checked_at,
            categories=enum_payload["categories"],
            category_ids=category_ids,
        )

    def source_hash(self, bbbs: str) -> str:
        payload = self.fetch_law_detail(bbbs)
        normalized = json.dumps(
            payload.get("data") or payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    def _request_json(
        self,
        path: str,
        *,
        method: str = "GET",
        params: Mapping[str, object] | None = None,
        data: object | None = None,
    ) -> dict:
        url = urljoin(self.base_url if self.base_url.endswith("/") else f"{self.base_url}/", path)
        if params:
            url = f"{url}?{urlencode(params, doseq=True)}"

        req = _build_request(
            url,
            method=method,
            accept="application/json,text/plain;q=0.9,*/*;q=0.8",
            data=data,
        )
        self._throttle()
        with urlopen(req, timeout=self.timeout) as resp:
            charset = resp.headers.get_content_charset() or "utf-8"
            text = resp.read().decode(charset, errors="replace")
            try:
                payload = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    _unexpected_text_response_message(
                        url=resp.geturl(),
                        status_code=resp.getcode(),
                        content_type=resp.headers.get("Content-Type"),
                        text=text,
                        expected="JSON object",
                    )
                ) from exc
            if not isinstance(payload, dict):
                raise ValueError("expected JSON object response")
            return payload


default_adapter = FlkNpcAdapter()


def probe(timeout: int = DEFAULT_TIMEOUT) -> dict:
    return FlkNpcAdapter(timeout=timeout).probe()


def search_list(search_content: str, **kwargs) -> dict:
    return default_adapter.search_list(search_content, **kwargs)


def fetch_law_detail(bbbs: str) -> dict:
    return default_adapter.fetch_law_detail(bbbs)


def fetch_hit_display(bbbs: str, **kwargs) -> dict:
    return default_adapter.fetch_hit_display(bbbs, **kwargs)


def fetch_related_resources(bbbs: str, **kwargs) -> dict:
    return default_adapter.fetch_related_resources(bbbs, **kwargs)


def fetch_related_file_detail(bbbs: str) -> dict:
    return default_adapter.fetch_related_file_detail(bbbs)


def fetch_recommendations(bbbs: str) -> dict:
    return default_adapter.fetch_recommendations(bbbs)
