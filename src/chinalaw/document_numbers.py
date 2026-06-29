"""Document-number helpers shared by fetch, sync, and fixture loading."""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

# Covers common central / court / procuratorate forms such as:
# 法释〔2023〕13号, 法发〔2019〕254号, 中办发〔2020〕5号, 高检发释字〔2017〕7号.
DOCUMENT_NUMBER_RE = re.compile(r"^[一-鿿]{1,12}〔\d{4}〕\d+号$")
# 显式语义别名：``DOCUMENT_NUMBER_FULLMATCH_RE`` 用于"用户输入是不是单一文号"
# 与 ``document_number_index`` key 校验等需要严格 fullmatch 的场景（已 ``^...$``
# 锚定）。``DOCUMENT_NUMBER_INLINE_RE`` 不锚定 + ``\s*`` 容忍内部空白，用于从
# 正文中抽出第一个文号子串。两个常量是 adapter 共享的权威定义；court_gongbao /
# spp_gov_cn 不应再各自写本地正则，详见
# ``docs/UNIFY_DOCUMENT_NUMBER_REGEX_SPEC.md``。
DOCUMENT_NUMBER_FULLMATCH_RE = DOCUMENT_NUMBER_RE
DOCUMENT_NUMBER_INLINE_RE = re.compile(r"([一-鿿]{1,12}〔\d{4}〕\s*\d+\s*号)")
COURT_DETAIL_RE = re.compile(r"/Details/([0-9A-Fa-f]{20,40})\.html")
COURT_MAIN_DETAIL_RE = re.compile(r"/([a-z]+/xiangqing/\d+)\.html", re.IGNORECASE)
GOV_XZFGK_DETAIL_RE = re.compile(r"[?&]LawID=(\d+)", re.IGNORECASE)
GOV_CN_CONTENT_RE = re.compile(r"content_(\d+)\.htm", re.IGNORECASE)
NFRA_DOC_ID_RE = re.compile(r"[?&]docId=(\d+)", re.IGNORECASE)
FLK_BBBS_RE = re.compile(r"^[0-9A-Fa-f]{24,80}$")
# spp.gov.cn detail_id 是路径片段，不带前导 ``/`` 与 ``.shtml``，例：
# ``xwfbh/wsfbt/202501/t20250116_679579``。
SPP_DETAIL_PATH_RE = re.compile(r"^[\w/\-]+$")
CSRC_DETAIL_PATH_RE = re.compile(r"^(?:csrc|[\w-]+)/c\d+/c[\w-]+$")
GENERIC_DETAIL_PATH_RE = re.compile(r"^[\w/\-.]+(?:html|shtml|pdf|docx?|txt)$", re.IGNORECASE)


def looks_like_document_number(text: str | None) -> bool:
    """Return whether ``text`` looks like a formal document number."""

    if not text:
        return False
    return bool(DOCUMENT_NUMBER_RE.match(normalize_document_number(text)))


def normalize_document_number(text: str | None) -> str:
    """Fold whitespace so ``法释〔2023〕 13号`` equals ``法释〔2023〕13号``."""

    return re.sub(r"\s+", "", (text or "").strip())


def extract_document_number(text: str | None) -> str | None:
    """从 ``text`` 中抽出第一个看起来像文号的子串，并 normalize 内部空白。

    与 :func:`looks_like_document_number` 不同：

    - ``looks_like_document_number(text)`` 判断"整段 text 本身是不是单一文号"
      （fullmatch 语义，用于 fetch CLI 的用户输入识别 / index key）。
    - :func:`extract_document_number` 在正文（多段、含其它文字）里 ``re.search``
      第一个文号样式子串，命中后用 ``normalize_document_number`` 折叠空白。
      用于 adapter 的 ``build_law_payload`` 从正文抽取 ``document_number``。

    为什么不要每个 adapter 各写一份：见
    ``docs/UNIFY_DOCUMENT_NUMBER_REGEX_SPEC.md`` §1（修前 court_gongbao 强制
    ``法`` 前缀漏召高检 / 中办联合发文）。
    """

    if not text:
        return None
    match = DOCUMENT_NUMBER_INLINE_RE.search(text)
    if not match:
        return None
    return normalize_document_number(match.group(1))


def index_document_number(
    conn,
    payload: dict,
    source: str | None = None,
    source_id: str | None = None,
) -> None:
    """Upsert ``payload.document_number`` into ``document_number_index``.

    The index is auxiliary. Missing document numbers, unknown sources, or
    payloads without a recoverable upstream source id are skipped silently. A
    missing table is also skipped so older ad-hoc DBs do not break fixture
    loading before migration has run.
    """

    document_number = normalize_document_number(payload.get("document_number"))
    if not document_number:
        return

    source_key = _normalize_source(source) or infer_source(payload)
    if not source_key:
        return

    resolved_source_id = (source_id or "").strip() or infer_source_id(payload, source_key)
    if not resolved_source_id:
        return

    indexed_at = datetime.now(timezone.utc).isoformat()
    try:
        conn.execute(
            "INSERT INTO document_number_index "
            "(document_number, source, source_id, law_id, title, indexed_at) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(document_number, source) DO UPDATE SET "
            "source_id = excluded.source_id, "
            "law_id = excluded.law_id, "
            "title = excluded.title, "
            "indexed_at = excluded.indexed_at",
            (
                document_number,
                source_key,
                str(resolved_source_id),
                payload.get("id"),
                payload.get("title"),
                indexed_at,
            ),
        )
    except sqlite3.OperationalError as exc:
        if "document_number_index" in str(exc):
            return
        raise


def infer_source(payload: dict) -> str | None:
    """Infer the adapter source key from ``source_name`` / ``source_url``."""

    source_name = (payload.get("source_name") or "").strip().lower()
    source_url = (payload.get("source_url") or "").strip().lower()
    marker = f"{source_name} {source_url}"
    if "flk.npc.gov.cn" in marker:
        return "flk_npc"
    if "gongbao.court.gov.cn" in marker:
        return "court_gongbao"
    if "www.court.gov.cn" in marker or "court.gov.cn" in marker:
        return "court_main"
    if (
        "xzfg.moj.gov.cn" in marker
        or "www.gov.cn/zhengce/xzfgk" in marker
        or "www.gov.cn/zhengce/" in marker
    ):
        return "gov_xzfgk"
    if "nfra.gov.cn" in marker:
        return "nfra_gov_cn"
    if "spp.gov.cn" in marker:
        return "spp_gov_cn"
    if "csrc.gov.cn" in marker:
        return "csrc_gov_cn"
    if "bse.cn" in marker:
        return "bse_cn"
    if "sse.com.cn" in marker:
        return "sse_com_cn"
    if "szse.cn" in marker:
        return "szse_cn"
    if "chinaclear.cn" in marker:
        return "chinaclear_cn"
    if "sac.net.cn" in marker:
        return "sac_net_cn"
    return None


# C901: 已知复杂（McCabe 38），三源文号主键推断集中于此；列为待拆分技术债，见
# docs/decisions/ADR-0009-module-boundaries.md。
def infer_source_id(payload: dict, source: str | None = None) -> str | None:  # noqa: C901
    """Infer the upstream id needed for document-number lookup."""

    source_key = _normalize_source(source) or infer_source(payload)
    source_url = (payload.get("source_url") or "").strip()
    law_id = (payload.get("id") or "").strip()

    if source_key == "flk_npc":
        parsed = urlparse(source_url)
        candidate = (parse_qs(parsed.query).get("id") or [None])[0]
        if candidate and FLK_BBBS_RE.match(candidate):
            return candidate
        if FLK_BBBS_RE.match(law_id):
            return law_id
        return None

    if source_key == "court_gongbao":
        match = COURT_DETAIL_RE.search(source_url)
        if match:
            return match.group(1)
        if law_id.startswith("court_gongbao:"):
            candidate = law_id.split(":", 1)[1]
            if candidate:
                return candidate
        return None

    if source_key == "court_main":
        match = COURT_MAIN_DETAIL_RE.search(source_url)
        if match:
            return match.group(1)
        if law_id.startswith("court_main:"):
            candidate = law_id.split(":", 1)[1]
            if COURT_MAIN_DETAIL_RE.match(f"/{candidate}.html"):
                return candidate
        return None

    if source_key == "gov_xzfgk":
        match = GOV_XZFGK_DETAIL_RE.search(source_url)
        if match:
            return match.group(1)
        match = GOV_CN_CONTENT_RE.search(source_url)
        if match:
            return f"gov_cn:content_{match.group(1)}"
        if law_id.startswith("gov_xzfgk:"):
            candidate = law_id.split(":", 1)[1]
            if candidate.isdigit() or candidate.startswith("gov_cn:content_"):
                return candidate
        return None

    if source_key == "nfra_gov_cn":
        match = NFRA_DOC_ID_RE.search(source_url)
        if match:
            return match.group(1)
        if law_id.startswith("nfra_gov_cn:"):
            candidate = law_id.split(":", 1)[1]
            if candidate.isdigit():
                return candidate
        return None

    if source_key == "spp_gov_cn":
        # source_url 形如 ``https://www.spp.gov.cn/xwfbh/wsfbt/202501/t20250116_679579.shtml``；
        # 抽 path → 去 leading / + .shtml + fragment。
        if source_url:
            path = urlparse(source_url).path or ""
            if path.endswith(".shtml"):
                path = path[: -len(".shtml")]
            path = path.lstrip("/")
            if path and SPP_DETAIL_PATH_RE.match(path):
                return path
        if law_id.startswith("spp_gov_cn:"):
            candidate = law_id.split(":", 1)[1]
            if candidate and SPP_DETAIL_PATH_RE.match(candidate):
                return candidate
        return None

    if source_key == "csrc_gov_cn":
        if source_url:
            path = urlparse(source_url).path or ""
            if path.endswith("/content.shtml"):
                path = path[: -len("/content.shtml")]
            path = path.lstrip("/")
            if path and CSRC_DETAIL_PATH_RE.match(path):
                return path
        if law_id.startswith("csrc_gov_cn:"):
            candidate = law_id.split(":", 1)[1]
            if candidate and CSRC_DETAIL_PATH_RE.match(candidate):
                return candidate
        return None

    if source_key in {
        "bse_cn",
        "sse_com_cn",
        "szse_cn",
        "chinaclear_cn",
        "sac_net_cn",
    }:
        if source_url:
            path = (urlparse(source_url).path or "").lstrip("/")
            if path and GENERIC_DETAIL_PATH_RE.match(path):
                return path
        prefix = f"{source_key}:"
        if law_id.startswith(prefix):
            candidate = law_id.split(":", 1)[1]
            if candidate and GENERIC_DETAIL_PATH_RE.match(candidate):
                return candidate
        return None

    return None


def _normalize_source(source: str | None) -> str | None:
    if not source:
        return None
    return source.strip().lower().replace("-", "_") or None
