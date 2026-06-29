"""Canonical cleaning helpers for external legal sources.

The cleaning layer converts raw upstream data into the JSON payload accepted by
``loader.load_law_from_dict``. Adapters should fetch bytes / JSON; this module
handles parsing, normalization, source hashes, and agent-readable structure.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import tempfile
import zipfile
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from urllib.parse import urljoin
from xml.etree import ElementTree as ET

from chinalaw.aliases import display_short_title, merge_law_aliases
from chinalaw.document_numbers import extract_document_number
from chinalaw.service import normalize_article_number

CLEANING_SCHEMA_VERSION = 1

FLXZ_TO_LEVEL = {
    # 当前 LawLevel 无单独 constitution 枚举；在协议升级前按法律层级处理，
    # 避免宪法被归入 other 后被 agent 的 level=law 过滤漏掉。
    "宪法": "law",
    "法律": "law",
    # flk 把法律修正案的 flxz 标成"修正案"，按法律性质归一到 law。
    # 实测样本：刑法修正案系列、立法法修正案、反垄断法修订决定。
    "修正案": "law",
    "行政法规": "admin_regulation",
    "司法解释": "judicial_interpretation",
    # flk 实际返回的字面是"地方法规"（短形式），书面"地方性法规"作为兼容别名同时保留。
    "地方法规": "local_regulation",
    "地方性法规": "local_regulation",
    "自治条例和单行条例": "local_regulation",
    "经济特区法规": "local_regulation",
    "浦东新区法规": "local_regulation",
    "海南自由贸易港法规": "local_regulation",
    # 部门规章 / 地方政府规章 在 LawLevel 枚举里早就声明了，但 mapping 缺失，导致
    # flk 偶尔吐这两个 flxz 时被错分到 other。
    "部门规章": "department_rule",
    "地方政府规章": "local_government_rule",
    "监察法规": "supervisory_regulation",
}

SXX_TO_STATUS = {
    1: "repealed",
    2: "amended",
    3: "current",
    4: "pending_effective",
}

DOCX_NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
BOOKMARK_NAME_ATTR = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}name"
OLE_WORD_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"

ARTICLE_RE = re.compile(
    r"^(?P<number_display>第[〇零一二三四五六七八九十百千万两0-9]+条(?:之[〇零一二三四五六七八九十百千万两0-9]+)?)(?P<body>.*)$"
)
DECIMAL_ARTICLE_RE = re.compile(
    r"^(?P<number_display>\d{1,3}(?:\.\d{1,2})+)(?!\s*条)"
    r"(?:[　\s]+|(?=[\u4e00-\u9fff]))(?P<body>.*)$"
)
NUMBERED_ITEM_RE = re.compile(
    r"^(?P<number>\d{1,3})[.．]\s*(?P<body>[\u4e00-\u9fff].*)$"
)
STRUCTURAL_HEADING_RE = re.compile(
    r"^(第[〇零一二三四五六七八九十百千万两0-9]+(?:编|章|节|分编).+|附则)$"
)
ENUM_STRUCTURAL_HEADING_RE = re.compile(
    r"^(?P<ordinal>[〇零一二三四五六七八九十百千万两0-9]{1,3})、"
    r"(?P<body>[\u4e00-\u9fff（）()《》·]{2,24})$"
)
TOC_MARKER_RE = re.compile(r"目\s*录")
TOC_DOT_LEADER_RE = re.compile(r"(?:\.{3,}|…{2,})")
TOC_STRUCTURAL_PAGE_RE = re.compile(
    r"^(?:第[〇零一二三四五六七八九十百千万两0-9]+(?:编|章|节|分编).+|"
    r"附则|[〇零一二三四五六七八九十百千万两0-9]{1,3}、.+)\s+\d{1,4}$"
)
ARTICLE_REFERENCE_FRAGMENT_RE = re.compile(
    r"^(?:第?[〇零一二三四五六七八九十百千万两0-9]+(?:款|项|段))(?:$|[\u4e00-\u9fff].*)"
)


def canonicalize(raw: object, *, source_kind: str, **options) -> dict:
    """Normalize raw upstream data into a loader-compatible law payload."""
    normalized_kind = (source_kind or "").strip().lower()
    if normalized_kind == "flk_npc_detail":
        return canonicalize_flk_npc(raw, **options)
    if normalized_kind == "external_json":
        return canonicalize_external_json(raw)
    if normalized_kind == "markdown":
        return canonicalize_markdown(raw, **options)
    if normalized_kind in {"docx", "docx_bytes"}:
        return canonicalize_docx(raw, **options)
    raise ValueError(f"unsupported source_kind: {source_kind}")


def canonicalize_external_json(payload: dict) -> dict:
    """Validate and shallow-normalize an already canonical-looking payload."""
    required = [
        "id",
        "title",
        "level",
        "status",
        "source_url",
        "source_name",
        "source_checked_at",
        "articles",
    ]
    missing = [field for field in required if field not in payload]
    if missing:
        raise ValueError(f"canonical law payload missing fields: {', '.join(missing)}")

    normalized = dict(payload)
    normalized["articles"] = normalize_articles(list(payload.get("articles") or []))
    title = str(payload["title"])
    short_title = normalized.get("short_title") or infer_short_title(title)
    normalized["short_title"] = display_short_title(title, short_title)
    normalized["aliases"] = merge_law_aliases(
        title,
        normalized.get("short_title"),
        list(payload.get("aliases") or []),
    )
    normalized.setdefault("document_number", None)
    normalized.setdefault("issuing_body", None)
    normalized.setdefault("released_at", None)
    normalized.setdefault("effective_at", None)
    normalized.setdefault("repealed_at", None)
    normalized.setdefault("source_hash", build_articles_hash(normalized["articles"]))
    return normalized


def canonicalize_markdown(raw: object, **metadata) -> dict:
    """Build a canonical law payload from local Markdown/plain text."""
    text, embedded_metadata = _extract_text_and_metadata(raw)
    merged_metadata = {**embedded_metadata, **metadata}
    articles = parse_articles_from_text(text)
    return _canonicalize_local_text_payload(
        merged_metadata,
        articles,
        source_bytes=text.encode("utf-8"),
    )


def canonicalize_docx(raw: object, **metadata) -> dict:
    """Build a canonical law payload from local DOCX bytes."""
    docx_bytes, embedded_metadata = _extract_bytes_and_metadata(raw)
    merged_metadata = {**embedded_metadata, **metadata}
    articles = parse_articles_from_docx(docx_bytes)
    return _canonicalize_local_text_payload(
        merged_metadata,
        articles,
        source_bytes=docx_bytes,
    )


def single_body_article(text: str) -> list[dict]:
    """Represent an unnumbered normative document as one body item."""

    body = (text or "").strip()
    if not body:
        return []
    return [
        {
            "number": "正文",
            "number_display": "正文",
            "text": body,
            "part": None,
            "position": 1,
        }
    ]


def extract_document_number_from_preamble(text: str | None) -> str | None:
    """Extract a document number only from the heading / preamble region.

    HTML source pages often quote repealed or related documents inside article
    bodies. Scanning the whole body would index those references as this
    document's own number, so adapters should use this helper for metadata.
    """

    if not text:
        return None
    preamble = _metadata_preamble_text_from_lines(str(text).splitlines())
    return extract_document_number(preamble)


def canonicalize_flk_npc(
    detail_payload: dict,
    *,
    docx_bytes: bytes,
    bbbs: str | None = None,
    base_url: str = "https://flk.npc.gov.cn",
    checked_at: str,
    categories: list[dict] | None = None,
    category_ids: list[str] | None = None,
) -> dict:
    """Build the canonical law payload for one FLK detail + Word body."""
    detail_data = detail_payload.get("data") or {}
    law_id = bbbs or detail_data.get("bbbs") or detail_data.get("id")
    if not law_id:
        raise ValueError("flk_npc detail payload missing bbbs/id")

    title = detail_data.get("title") or law_id
    short_title = display_short_title(title, infer_short_title(title))
    return {
        "id": law_id,
        "title": title,
        "short_title": short_title,
        "aliases": merge_law_aliases(title, short_title, []),
        "level": infer_level(detail_data.get("flxz")),
        "status": infer_status(detail_data.get("sxx")),
        "issuing_body": detail_data.get("zdjgName"),
        # document_number / repealed_at 这两条曾被硬编码为 None，与同模块
        # 其他三条 canonicalize 路径（external_json / markdown / docx 经
        # _canonicalize_local_text_payload）形成同层不变量违反——其它三路
        # 都从输入读，只 flk_npc 路径无视输入。修复路径见
        # docs/CLEANING_FLK_NPC_RESTORE_METADATA_SPEC.md §3：
        #   document_number 优先 detail JSON 候选键 (wenhao / wh /
        #   documentNumber)，退到 docx 题注调 extract_document_number 首匹配
        #   （与 HTML adapter 同型）；
        #   repealed_at 防御性读 detail JSON fzrq 字段，未来 flk schema 加
        #   字段或 caller 合成 detail_payload 时即可使用，找不到回退 None
        #   与原硬编码语义兼容。
        "document_number": _flk_document_number(detail_data, docx_bytes),
        "released_at": detail_data.get("gbrq"),
        "effective_at": detail_data.get("sxrq"),
        "repealed_at": detail_data.get("fzrq"),
        "source_url": flk_source_detail_url(base_url, law_id),
        "source_name": "flk.npc.gov.cn",
        "source_checked_at": checked_at,
        "source_hash": build_flk_source_hash(detail_payload, docx_bytes),
        "categories": list(categories or []),
        "category_ids": list(category_ids or []),
        # normalize_articles 是 cleaning 层的 invariant 兜底（trailing heading
        # 剥离 + part / number 兜底）。其他 3 个 source_kind 都走过；本路径必须
        # 与之对称，否则 fetch --to-fixture 落盘时会漂移（详见
        # docs/CANONICALIZE_FLK_NPC_NORMALIZE_SPEC.md）。
        "articles": normalize_articles(parse_articles_from_word_bytes(docx_bytes)),
    }


def parse_articles_from_word_bytes(word_bytes: bytes) -> list[dict]:
    """Parse articles from FLK Word downloads.

    Newer FLK downloads are DOCX zip files. Older judicial interpretations may
    still be legacy OLE ``.doc`` files; when the host has ``textutil`` (macOS)
    or ``antiword`` available, convert those to plain text and reuse the same
    article parser. The conversion tools are optional so the package keeps a
    zero-runtime-dependency core.
    """

    if word_bytes.startswith(b"PK"):
        return parse_articles_from_docx(word_bytes)
    if word_bytes.startswith(OLE_WORD_MAGIC):
        return parse_articles_from_text(_convert_legacy_doc_to_text(word_bytes))
    raise ValueError("unsupported Word payload: expected DOCX zip or legacy OLE .doc")


def _flk_document_number(detail_data: dict, docx_bytes: bytes) -> str | None:
    """Recover ``document_number`` for the FLK canonicalize path.

    flk JSON 详情接口实测**没有**发文号字段（``docs/CLEANING_FLK_NPC_RESTORE_METADATA_SPEC.md``
    §1.1）。但仍优先在 ``detail_data`` 上做防御性读，覆盖未来 flk schema 升级
    或 caller 注入合成 payload 的情形；退到 docx 题注（preamble）调
    :func:`chinalaw.document_numbers.extract_document_number` 抽首匹配。

    与 ``court_gongbao`` / ``spp_gov_cn`` adapter 调用 ``extract_document_number(text)``
    抽 HTML 转出的正文文本同型；区别在于 flk 路径有 docx_bytes，所以本地完成。
    """

    for key in ("wenhao", "wh", "documentNumber"):
        candidate = detail_data.get(key)
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return _extract_document_number_from_docx_bytes(docx_bytes)


def _extract_document_number_from_docx_bytes(docx_bytes: bytes) -> str | None:
    """Extract document numbers only from the FLK metadata preamble.

    Both DOCX zip and legacy OLE ``.doc`` are supported (matches
    :func:`parse_articles_from_word_bytes`). Parse errors fall back to ``None``
    so this helper never breaks the canonicalize call—the original L171
    hardcode also never raised. Window of swallowed exceptions is narrow per
    PR5c / PR6 style: only zip / XML / legacy-doc tooling errors, programming
    errors propagate.

    Do not scan the article body: legal texts often cite other document numbers,
    and indexing those as this law's own ``document_number`` would corrupt
    document-number lookup.
    """

    try:
        if docx_bytes.startswith(b"PK"):
            paragraphs = _iter_docx_paragraphs(docx_bytes)
            text = _metadata_preamble_text(paragraphs)
        elif docx_bytes.startswith(OLE_WORD_MAGIC):
            text = _metadata_preamble_text_from_lines(
                _convert_legacy_doc_to_text(docx_bytes).splitlines()
            )
        else:
            return None
    except (KeyError, ValueError, ET.ParseError, zipfile.BadZipFile):
        return None
    return extract_document_number(text)


def _metadata_preamble_text(paragraphs: list[dict]) -> str:
    lines = [str(paragraph.get("text") or "") for paragraph in paragraphs]
    return _metadata_preamble_text_from_lines(lines)


def _metadata_preamble_text_from_lines(lines: list[str]) -> str:
    """Return text before the first structural heading or article."""

    context = _new_parse_context()
    preamble: list[str] = []
    for raw_line in lines:
        text = _clean_text(raw_line)
        if not text:
            continue
        if _is_toc_line(text):
            break
        if (
            _is_metadata_article_boundary(text)
            or DECIMAL_ARTICLE_RE.match(text)
            or _is_structural_heading(text, context)
        ):
            break
        preamble.append(text)
    return "\n".join(preamble)


def _is_metadata_article_boundary(text: str) -> bool:
    match = ARTICLE_RE.match(text)
    if not match:
        return False
    body = match.group("body").strip()
    # Title fragments such as "第八十八条第一款" cite an article/paragraph
    # but are not the document body's own Article 88.
    is_reference_fragment = bool(body and ARTICLE_REFERENCE_FRAGMENT_RE.match(body))
    return not is_reference_fragment


def parse_articles_from_docx(docx_bytes: bytes) -> list[dict]:
    paragraphs = _iter_docx_paragraphs(docx_bytes)
    articles: list[dict] = []
    current: dict | None = None
    context = _new_parse_context()
    position = 1

    for paragraph in paragraphs:
        text = _clean_text(paragraph["text"])
        if not text:
            continue
        if _is_toc_line(text):
            continue

        bookmark_name = next(
            (name for name in paragraph["bookmark_names"] if name and not name.startswith("_")),
            None,
        )
        heading_name = bookmark_name or text

        if heading_name in {"题注", "目录"}:
            continue

        structural_heading = (
            heading_name if _is_structural_heading(heading_name, context) else text
        )
        if _is_structural_heading(structural_heading, context):
            _update_context(context, structural_heading)
            continue

        article_match = ARTICLE_RE.match(text) or DECIMAL_ARTICLE_RE.match(text)
        if article_match:
            number_display = article_match.group("number_display")
            body = article_match.group("body").strip()

            if current is not None:
                articles.append(current)

            current = {
                "number": normalize_article_number(number_display),
                "number_display": number_display,
                "text": body,
                "part": _part_label(context),
                "position": position,
            }
            position += 1
            continue

        if current is not None:
            _append_article_text(current, text)

    if current is not None:
        articles.append(current)

    return [article for article in articles if article.get("number") and article.get("text")]


def _convert_legacy_doc_to_text(doc_bytes: bytes) -> str:
    with tempfile.TemporaryDirectory() as td:
        doc_path = Path(td) / "source.doc"
        doc_path.write_bytes(doc_bytes)

        textutil = shutil.which("textutil")
        if textutil:
            result = subprocess.run(
                [textutil, "-convert", "txt", "-stdout", str(doc_path)],
                check=False,
                capture_output=True,
                text=True,
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout

        antiword = shutil.which("antiword")
        if antiword:
            result = subprocess.run(
                [antiword, str(doc_path)],
                check=False,
                capture_output=True,
                text=True,
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout

    raise ValueError("legacy .doc conversion requires macOS textutil or antiword")


def parse_articles_from_text(text: str) -> list[dict]:
    """Parse Chinese law-style articles from Markdown/plain text lines."""
    articles: list[dict] = []
    current: dict | None = None
    context = _new_parse_context()
    position = 1

    for raw_line in text.splitlines():
        line = _clean_text(raw_line.strip().lstrip("#").strip())
        if not line:
            continue
        if _is_toc_line(line):
            continue

        if _is_structural_heading(line, context):
            _update_context(context, line)
            continue

        article_match = ARTICLE_RE.match(line) or DECIMAL_ARTICLE_RE.match(line)
        if article_match:
            number_display = article_match.group("number_display")
            body = article_match.group("body").strip()
            if current is not None:
                articles.append(current)
            current = {
                "number": normalize_article_number(number_display),
                "number_display": number_display,
                "text": body,
                "part": _part_label(context),
                "position": position,
            }
            position += 1
            continue

        if current is not None:
            _append_article_text(current, line)

    if current is not None:
        articles.append(current)

    return [article for article in articles if article.get("number") and article.get("text")]


def parse_numbered_items_from_text(text: str, *, min_items: int = 2) -> list[dict]:
    """Parse policy/guidance documents organized as ``1. ...`` items.

    This is intentionally separate from ``parse_articles_from_text`` so statute
    cleaning does not promote ordinary numbered lists into articles.
    """

    articles: list[dict] = []
    current: dict | None = None
    context = _new_parse_context()
    position = 1

    for raw_line in text.splitlines():
        line = _clean_text(raw_line.strip().lstrip("#").strip())
        if not line:
            continue
        if _is_toc_line(line):
            continue

        if _is_structural_heading(line, context):
            _update_context(context, line)
            continue

        item_match = NUMBERED_ITEM_RE.match(line)
        if item_match:
            number = str(int(item_match.group("number")))
            body = item_match.group("body").strip()
            if current is None and number != "1":
                continue
            if current is not None:
                articles.append(current)
            current = {
                "number": number,
                "number_display": f"第{number}项",
                "text": body,
                "part": _part_label(context),
                "position": position,
            }
            position += 1
            continue

        if current is not None:
            _append_article_text(current, line)

    if current is not None:
        articles.append(current)

    articles = [
        article for article in articles if article.get("number") and article.get("text")
    ]
    if len(articles) < min_items:
        return []
    return articles


def normalize_articles(articles: list[dict]) -> list[dict]:
    """Normalize article numbers and positions for canonical JSON input."""
    normalized: list[dict] = []
    context = _new_parse_context()
    for pos, article in enumerate(articles, start=1):
        item = dict(article)
        number_display = str(item.get("number_display") or "").strip()
        number = str(item.get("number") or "").strip()
        if not number and number_display:
            number = normalize_article_number(number_display)
        if not number:
            raise ValueError(f"article at position {pos} missing number")
        _sync_enum_context_from_part(context, item.get("part"))
        text, trailing_headings = _split_trailing_structural_headings(
            str(item.get("text") or "").strip(),
            context,
            allow_orphan_enum=True,
        )
        if not text:
            raise ValueError(f"article {number} missing text")
        item["number"] = number
        item["number_display"] = number_display or number
        item["text"] = text
        item["part"] = item.get("part") or _part_label(context)
        item.setdefault("position", pos)
        normalized.append(item)
        for heading in trailing_headings:
            _update_context(context, heading)
    return normalized


def infer_short_title(title: str) -> str | None:
    prefix = "中华人民共和国"
    if title.startswith(prefix):
        short = title[len(prefix):].strip()
        if 2 <= len(short) <= 24:
            return short
    return None


def infer_level(flxz: str | None) -> str:
    if not flxz:
        return "other"
    return FLXZ_TO_LEVEL.get(flxz, "other")


def infer_status(sxx: object) -> str:
    if isinstance(sxx, int):
        return SXX_TO_STATUS.get(sxx, "unknown")
    return "unknown"


def flk_source_detail_url(base_url: str, bbbs: str) -> str:
    return urljoin(base_url if base_url.endswith("/") else f"{base_url}/", f"detail?id={bbbs}")


def build_flk_source_hash(detail_payload: dict, docx_bytes: bytes) -> str:
    content = detail_payload.get("data") or detail_payload
    normalized = json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256()
    digest.update(normalized.encode("utf-8"))
    digest.update(docx_bytes)
    return digest.hexdigest()


def build_articles_hash(articles: list[dict]) -> str:
    digest = hashlib.sha256()
    for article in articles:
        digest.update(str(article.get("number", "")).encode("utf-8"))
        digest.update(b"\n")
        digest.update(str(article.get("text", "")).encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def build_local_source_hash(source_bytes: bytes, metadata: dict) -> str:
    normalized_metadata = {
        key: metadata.get(key)
        for key in (
            "id",
            "title",
            "level",
            "status",
            "released_at",
            "effective_at",
            "repealed_at",
            "source_url",
            "source_name",
        )
    }
    digest = hashlib.sha256()
    digest.update(
        json.dumps(
            normalized_metadata,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )
    digest.update(source_bytes)
    return digest.hexdigest()


def _canonicalize_local_text_payload(
    metadata: dict,
    articles: list[dict],
    *,
    source_bytes: bytes,
) -> dict:
    required = ["id", "title", "level", "status", "source_url", "source_name"]
    missing = [field for field in required if not metadata.get(field)]
    if missing:
        raise ValueError(f"local law payload missing metadata: {', '.join(missing)}")

    normalized_articles = normalize_articles(articles)
    title = str(metadata["title"])
    source_checked_at = (
        metadata.get("source_checked_at")
        or metadata.get("checked_at")
        or datetime.now(timezone.utc).isoformat()
    )
    short_title = display_short_title(
        title,
        metadata.get("short_title") or infer_short_title(title),
    )
    return {
        "id": metadata["id"],
        "title": title,
        "short_title": short_title,
        "aliases": merge_law_aliases(
            title,
            short_title,
            list(metadata.get("aliases") or []),
        ),
        "level": metadata["level"],
        "status": metadata["status"],
        "issuing_body": metadata.get("issuing_body"),
        "document_number": metadata.get("document_number"),
        "released_at": metadata.get("released_at"),
        "effective_at": metadata.get("effective_at"),
        "repealed_at": metadata.get("repealed_at"),
        "source_url": metadata["source_url"],
        "source_name": metadata["source_name"],
        "source_checked_at": source_checked_at,
        "source_hash": metadata.get("source_hash") or build_local_source_hash(
            source_bytes,
            {**metadata, "source_checked_at": source_checked_at},
        ),
        "articles": normalized_articles,
    }


def _extract_text_and_metadata(raw: object) -> tuple[str, dict]:
    if isinstance(raw, str):
        return raw, {}
    if isinstance(raw, dict):
        text = raw.get("text")
        if not isinstance(text, str):
            raise ValueError("markdown source requires text")
        metadata = raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}
        return text, dict(metadata)
    raise ValueError("markdown source must be str or dict")


def _extract_bytes_and_metadata(raw: object) -> tuple[bytes, dict]:
    if isinstance(raw, bytes):
        return raw, {}
    if isinstance(raw, dict):
        content = raw.get("bytes")
        if not isinstance(content, bytes):
            raise ValueError("docx source requires bytes")
        metadata = raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}
        return content, dict(metadata)
    raise ValueError("docx source must be bytes or dict")


def _iter_docx_paragraphs(docx_bytes: bytes) -> list[dict]:
    with zipfile.ZipFile(BytesIO(docx_bytes)) as zf:
        xml_bytes = zf.read("word/document.xml")

    root = ET.fromstring(xml_bytes)
    paragraphs: list[dict] = []
    for paragraph in root.findall(".//w:p", DOCX_NS):
        bookmark_names = [
            bookmark.attrib[BOOKMARK_NAME_ATTR]
            for bookmark in paragraph.findall("./w:bookmarkStart", DOCX_NS)
            if BOOKMARK_NAME_ATTR in bookmark.attrib
        ]
        parts: list[str] = []
        for node in paragraph.iter():
            tag = node.tag.split("}")[-1]
            if tag == "t" and node.text:
                parts.append(node.text)
            elif tag == "tab":
                parts.append(" ")
            elif tag in {"br", "cr"}:
                parts.append("\n")
        text = "".join(parts).replace("\xa0", " ").strip()
        paragraphs.append({"text": text, "bookmark_names": bookmark_names})
    return paragraphs


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _is_toc_line(text: str) -> bool:
    """Return whether a cleaned line is table-of-contents noise.

    Exchange rule PDFs/DOCX often contain a generated TOC before the actual
    clauses. Those lines look like structural headings, so if we keep them they
    pollute ``article.part`` until the real heading appears. This rule is
    intentionally shape-based: marker line, dot leaders, or structural heading
    followed by a page number.
    """

    if TOC_MARKER_RE.search(text):
        return True
    if TOC_DOT_LEADER_RE.search(text):
        return True
    return bool(TOC_STRUCTURAL_PAGE_RE.match(text))


def _new_parse_context() -> dict[str, str | int | None]:
    return {"book": None, "chapter": None, "section": None, "enum_ordinal": 0}


def _part_label(context: dict[str, str | int | None]) -> str | None:
    parts = [context["book"], context["chapter"], context["section"]]
    values = [value for value in parts if value]
    return " ".join(values) if values else None


def _is_structural_heading(
    text: str,
    context: dict[str, str | int | None] | None = None,
    *,
    allow_orphan_enum: bool = False,
) -> bool:
    if STRUCTURAL_HEADING_RE.match(text):
        return True
    enum_match = ENUM_STRUCTURAL_HEADING_RE.match(text)
    if not enum_match:
        return False
    if context is None:
        return True
    ordinal = _enum_heading_ordinal(text)
    previous = int(context.get("enum_ordinal") or 0)
    if previous == 0 and allow_orphan_enum:
        return ordinal is not None
    return ordinal == previous + 1


def _enum_heading_ordinal(text: str) -> int | None:
    match = ENUM_STRUCTURAL_HEADING_RE.match(text)
    if not match:
        return None
    normalized = normalize_article_number(f"第{match.group('ordinal')}条")
    if not normalized.isdigit():
        return None
    return int(normalized)


def _sync_enum_context_from_part(
    context: dict[str, str | int | None],
    part: object,
) -> None:
    for segment in str(part or "").split():
        if ENUM_STRUCTURAL_HEADING_RE.match(segment):
            context["section"] = segment
            context["enum_ordinal"] = _enum_heading_ordinal(segment) or context["enum_ordinal"]


def _update_context(context: dict[str, str | int | None], heading: str) -> None:
    if "分编" in heading or "编" in heading or heading == "附则":
        context["book"] = heading
        context["chapter"] = None
        context["section"] = None
        context["enum_ordinal"] = 0
    elif "章" in heading:
        context["chapter"] = heading
        context["section"] = None
        context["enum_ordinal"] = 0
    elif "节" in heading:
        context["section"] = heading
        context["enum_ordinal"] = 0
    elif ENUM_STRUCTURAL_HEADING_RE.match(heading):
        context["section"] = heading
        context["enum_ordinal"] = _enum_heading_ordinal(heading) or context["enum_ordinal"]


def _append_article_text(current: dict, text: str) -> None:
    if not text:
        return
    current["text"] = f"{current['text']}\n{text}" if current["text"] else text


def _strip_trailing_structural_headings(text: str) -> str:
    stripped, _ = _split_trailing_structural_headings(text)
    return stripped


def _split_trailing_structural_headings(
    text: str,
    context: dict[str, str | int | None] | None = None,
    *,
    allow_orphan_enum: bool = False,
) -> tuple[str, list[str]]:
    lines = text.splitlines()
    if not lines:
        return "", []
    base_context = dict(context or _new_parse_context())
    best_start: int | None = None
    for start in range(len(lines) - 1, -1, -1):
        local_context = dict(base_context)
        suffix = [_clean_text(line) for line in lines[start:]]
        if not suffix or not all(suffix):
            continue
        matched = True
        for heading in suffix:
            if not _is_structural_heading(
                heading,
                local_context,
                allow_orphan_enum=allow_orphan_enum,
            ):
                matched = False
                break
            _update_context(local_context, heading)
        if matched:
            best_start = start
    if best_start is None:
        return "\n".join(lines).strip(), []
    return "\n".join(lines[:best_start]).strip(), [
        _clean_text(line) for line in lines[best_start:]
    ]
