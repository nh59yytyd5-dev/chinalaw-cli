"""引用审查：从文本 / 规范包 / 私域规范中核验法规引用。"""

from __future__ import annotations

import re
import shlex
from difflib import SequenceMatcher
from pathlib import Path

from chinalaw import normpacks, normsources, service, snapshots

_CN_NUM = "一二三四五六七八九十百千万零〇两"
_NUMBER_BASE_RE = rf"[{_CN_NUM}\d]+"
_NUMBER_CORE_RE = rf"{_NUMBER_BASE_RE}(?:[-－—][{_CN_NUM}\d]+|之[{_CN_NUM}\d]+)?"
_ARTICLE_NUMBER_RE = (
    rf"(?:第?\s*{_NUMBER_BASE_RE}\s*条(?:之[{_CN_NUM}\d]+)?|"
    rf"第?\s*{_NUMBER_BASE_RE}\s*[-－—]\s*{_NUMBER_BASE_RE}\s*条?|"
    rf"第?\s*{_NUMBER_BASE_RE}\s*项)"
)
_CITATION_RE = re.compile(
    rf"《(?P<law>[^》]{{1,80}})》\s*(?P<number>{_ARTICLE_NUMBER_RE})"
)
_SHORT_CITATION_RE = re.compile(
    rf"(?<![A-Za-z0-9_])"
    rf"(?P<law>九民|[民公证])\s*§\s*(?P<number>{_NUMBER_CORE_RE})\s*(?:条|项)?"
)
_DATE_RE = re.compile(r"(?:19|20)\d{2}[年/-]\d{1,2}(?:[月/-]\d{1,2}日?)?")
_PUNCT_RE = re.compile(r"[\s　，,。；;：:、（）()【】\[\]《》“”\"'‘’`]+")
_SHORT_LAW_ALIASES = {
    "九民": "九民纪要",
    "民": "民法典",
    "公": "公司法",
    "证": "证券法",
}
_SCHOLARLY_SOURCE_TITLES = {
    "证券法苑",
    "清华法学",
    "法律科学",
    "交大法学",
    "法学评论",
    "法律适用",
}
_COMPARATIVE_LAW_TITLES = {
    "公司条例",
    "公司更生法",
    "有限责任公司法",
}


def extract_citations(text: str) -> list[dict]:
    """Extract book-title style legal citations from free text.

    The parser is deliberately conservative. It only treats ``《...》第N条`` as a
    law citation, avoiding broad guesses such as any ``第N条`` in private rules.
    """

    citations: list[dict] = []
    for match in _CITATION_RE.finditer(text or ""):
        raw = match.group(0)
        start = match.start()
        end = match.end()
        number_input = match.group("number").strip()
        law_input = match.group("law").strip()
        if _should_ignore_book_title_citation(law_input, number_input):
            continue
        citations.append(
            {
                "raw": raw,
                "law_input": law_input,
                "number_input": number_input,
                "number": service.normalize_article_number(number_input),
                "position": {
                    "start": start,
                    "end": end,
                    "line": (text or "").count("\n", 0, start) + 1,
                },
                "context": _context(text, start, end),
                "quoted_text": _extract_quoted_text(text, end),
            }
        )
    for match in _SHORT_CITATION_RE.finditer(text or ""):
        raw = match.group(0)
        start = match.start()
        end = match.end()
        law_input = _SHORT_LAW_ALIASES[match.group("law")]
        number_input = match.group("number").strip()
        citations.append(
            {
                "raw": raw,
                "law_input": law_input,
                "number_input": number_input,
                "number": service.normalize_article_number(number_input),
                "position": {
                    "start": start,
                    "end": end,
                    "line": (text or "").count("\n", 0, start) + 1,
                },
                "context": _context(text, start, end),
                "quoted_text": _extract_quoted_text(text, end),
            }
        )
    citations.sort(key=lambda item: item.get("position", {}).get("start", 0))
    return citations


def _should_ignore_book_title_citation(law_input: str, number_input: str) -> bool:
    title = (law_input or "").strip()
    number = (number_input or "").strip()
    if not title:
        return True
    if title in _SCHOLARLY_SOURCE_TITLES or title in _COMPARATIVE_LAW_TITLES:
        return True
    if title.startswith("论") and len(title) >= 8:
        return True
    if any(mark in title for mark in ("？", "?", "，", ",", "：", ":")):
        return True
    return _looks_like_source_year(number)


def _looks_like_source_year(number_input: str) -> bool:
    compact = re.sub(r"\s+", "", number_input or "")
    compact = compact.removeprefix("第").removesuffix("条").removesuffix("项")
    return bool(re.fullmatch(r"(?:19|20)\d{2}(?:[-/]\d{1,2})?", compact))


def audit_file(
    db_path: str | Path,
    path: str | Path,
    *,
    as_of: str | None = None,
    strict: bool = False,
) -> dict:
    source_path = Path(path)
    text = normsources.read_source_text(source_path)
    report = audit_text(
        db_path,
        text,
        source_kind="file",
        target=str(source_path),
        as_of=as_of,
        strict=strict,
    )
    report["path"] = str(source_path)
    return report


def audit_grounding_file(
    db_path: str | Path,
    path: str | Path,
    *,
    snapshot_path: str | Path | None = None,
    as_of: str | None = None,
    strict: bool = False,
) -> dict:
    """Audit whether final text is backed by a project retrieval snapshot."""

    source_path = Path(path)
    text = normsources.read_source_text(source_path)
    resolved_snapshot = snapshots.resolve_snapshot_in(
        snapshot_path,
        anchor=source_path.parent,
    )
    records = snapshots.load_records(resolved_snapshot)
    citations: list[dict] = []
    issues: list[dict] = []
    claims: list[dict] = []

    for citation in extract_citations(text):
        # Grounding audit is about traceability, not quote-text verification.
        citation = {**citation, "quoted_text": None}
        audited = _audit_citation(db_path, citation, as_of=as_of, strict=strict)
        grounding = _classify_grounding(audited, records)
        audited["grounding"] = grounding
        claims.append(
            {
                "text": citation.get("context"),
                "citation": citation.get("raw"),
                "grounding": grounding,
            }
        )
        if grounding["status"] == "ungrounded":
            audited["issues"].append(
                _issue(
                    "error",
                    "ungrounded_citation",
                    "最终文本引用了该条，但项目检索快照中没有对应证据。",
                    strict=strict,
                    details=grounding,
                )
            )
        elif grounding["status"] == "retrieved_only":
            audited["issues"].append(
                _issue(
                    "warning",
                    "retrieved_but_unverified",
                    "项目快照只显示检索/预览过该依据，未出现 article 级精确核验。",
                    strict=strict,
                    details=grounding,
                )
            )
        citations.append(_finalize_citation(audited))

    if not records:
        issues.append(
            _issue(
                "warning",
                "snapshot_empty_or_missing",
                "未读取到检索快照；所有引用都无法证明经过本项目检索。",
                strict=strict,
                details={"snapshot_path": str(resolved_snapshot) if resolved_snapshot else None},
            )
        )
    if not citations:
        issues.append(
            _issue(
                "warning",
                "no_citations",
                "未识别到《法规》第N条格式的引用；无法做引用级 grounding 审计。",
                strict=strict,
            )
        )
    if (
        as_of is None
        and _DATE_RE.search(text or "")
        and citations
        and not _has_time_effect_evidence(records)
    ):
        issues.append(
            _issue(
                "warning",
                "date_found_without_time_effect_evidence",
                "文本中出现日期，但快照中没有 applicable / relation / history / trace 记录。",
                strict=strict,
            )
        )

    report = _finalize_report(
        {
            "kind": "grounding_audit",
            "target": str(source_path),
            "path": str(source_path),
            "snapshot_path": str(resolved_snapshot) if resolved_snapshot else None,
            "snapshot_record_count": len(records),
            "as_of": as_of,
            "strict": strict,
            "claims": claims,
            "citations": citations,
            "issues": issues,
        }
    )
    report["grounding_counts"] = _grounding_counts(citations)
    return report


def audit_norm(
    db_path: str | Path,
    identifier: str,
    *,
    as_of: str | None = None,
    strict: bool = False,
) -> dict:
    source = normsources.get_source(db_path, identifier)
    if source is None:
        return _missing_target_report(
            kind="norm_audit",
            target=identifier,
            code="norm_source_not_found",
            message=f"未找到私域规范：{identifier}",
            strict=strict,
        )

    citations: list[dict] = []
    issues: list[dict] = []
    for clause in source.get("clauses") or []:
        text = clause.get("text") or ""
        sub = audit_text(
            db_path,
            text,
            source_kind="norm_clause",
            target=source.get("name") or identifier,
            as_of=as_of,
            strict=strict,
            add_no_citation_warning=False,
        )
        container = {
            "norm_source_id": source.get("id"),
            "norm_source_name": source.get("name"),
            "clause_number": clause.get("number"),
            "clause_number_display": clause.get("number_display"),
            "clause_title": clause.get("title"),
        }
        for citation in sub.get("citations") or []:
            citation["container"] = container
            citations.append(citation)
        for issue in sub.get("issues") or []:
            issue["container"] = container
            issues.append(issue)

    if not citations:
        issues.append(
            _issue(
                "warning",
                "no_citations",
                "该私域规范未识别到《法规》第N条格式的公开法引用。",
                strict=strict,
            )
        )

    return _finalize_report(
        {
            "kind": "norm_audit",
            "target": identifier,
            "source": {
                "id": source.get("id"),
                "name": source.get("name"),
                "short_name": source.get("short_name"),
                "source_type": source.get("source_type"),
                "source_url": source.get("source_url"),
                "source_checked_at": source.get("source_checked_at"),
            },
            "as_of": as_of,
            "strict": strict,
            "citations": citations,
            "issues": issues,
        }
    )


def audit_pack(
    db_path: str | Path,
    identifier: str,
    *,
    as_of: str | None = None,
    strict: bool = False,
) -> dict:
    pack = normpacks.get_pack(db_path, identifier, resolve=True)
    if pack is None:
        return _missing_target_report(
            kind="pack_audit",
            target=identifier,
            code="pack_not_found",
            message=f"未找到规范包：{identifier}",
            strict=strict,
        )

    validation = normpacks.validate_pack(db_path, identifier)
    issues: list[dict] = []
    for validation_issue in (validation or {}).get("issues") or []:
        issues.append(
            _issue(
                validation_issue.get("severity") or "warning",
                f"pack_validate:{validation_issue.get('code')}",
                validation_issue.get("message") or "规范包校验问题。",
                strict=strict,
                details=validation_issue,
            )
        )

    citations: list[dict] = []
    for item in pack.get("items") or []:
        item_type = item.get("item_type")
        if item_type == "article":
            citation = {
                "raw": _pack_item_label(item),
                "law_input": item.get("law_id") or item.get("law_title"),
                "number_input": item.get("article_number_display")
                or item.get("article_number"),
                "number": item.get("article_number"),
                "position": {"pack_item_position": item.get("position")},
                "context": item.get("reason") or item.get("note") or "",
                "quoted_text": None,
            }
            audited = _audit_citation(
                db_path,
                citation,
                as_of=as_of,
                strict=strict,
            )
            audited["container"] = _pack_item_container(item)
            citations.append(audited)
            continue

        if item_type == "reference":
            reference_text = "\n".join(
                part
                for part in (
                    item.get("reference_text"),
                    item.get("reason"),
                    item.get("note"),
                )
                if part
            )
            if (item.get("note") or "").strip().startswith("pending:"):
                issues.append(
                    _issue(
                        "warning",
                        "pending_reference_in_pack",
                        "规范包含 pending reference，不能当作已核验条文依据。",
                        strict=strict,
                        details=_pack_item_container(item),
                    )
                )
            sub = audit_text(
                db_path,
                reference_text,
                source_kind="pack_reference",
                target=pack.get("name") or identifier,
                as_of=as_of,
                strict=strict,
                add_no_citation_warning=False,
            )
            for citation in sub.get("citations") or []:
                citation["container"] = _pack_item_container(item)
                citations.append(citation)
            for issue in sub.get("issues") or []:
                issue["details"] = {
                    **(issue.get("details") or {}),
                    "container": _pack_item_container(item),
                }
                issues.append(issue)
            continue

        if item_type == "norm_clause":
            resolved = item.get("resolved") or {}
            clause = resolved.get("clause") or {}
            if clause.get("text"):
                sub = audit_text(
                    db_path,
                    clause["text"],
                    source_kind="pack_norm_clause",
                    target=pack.get("name") or identifier,
                    as_of=as_of,
                    strict=strict,
                    add_no_citation_warning=False,
                )
                for citation in sub.get("citations") or []:
                    citation["container"] = _pack_item_container(item)
                    citations.append(citation)
                for issue in sub.get("issues") or []:
                    issue["details"] = {
                        **(issue.get("details") or {}),
                        "container": _pack_item_container(item),
                    }
                    issues.append(issue)

    if not citations and not issues:
        issues.append(
            _issue(
                "warning",
                "no_auditable_citations",
                "规范包中未发现可审查的 article 成员或《法规》第N条格式 reference。",
                strict=strict,
            )
        )

    return _finalize_report(
        {
            "kind": "pack_audit",
            "target": identifier,
            "pack": {
                "id": pack.get("id"),
                "name": pack.get("name"),
                "item_count": pack.get("item_count"),
                "resolved_item_count": pack.get("resolved_item_count"),
            },
            "pack_validation": validation,
            "as_of": as_of,
            "strict": strict,
            "citations": citations,
            "issues": issues,
        }
    )


def audit_text(
    db_path: str | Path,
    text: str,
    *,
    source_kind: str = "text",
    target: str | None = None,
    as_of: str | None = None,
    strict: bool = False,
    add_no_citation_warning: bool = True,
) -> dict:
    citations = [
        _audit_citation(db_path, citation, as_of=as_of, strict=strict)
        for citation in extract_citations(text)
    ]
    issues: list[dict] = []
    if not citations and add_no_citation_warning:
        issues.append(
            _issue(
                "warning",
                "no_citations",
                "未识别到《法规》第N条格式的引用。",
                strict=strict,
            )
        )
    if as_of is None and _DATE_RE.search(text or "") and citations:
        issues.append(
            _issue(
                "warning",
                "date_found_without_as_of",
                "文本中出现日期，但审查未指定 --as-of；如涉及时间效力，请带事实日期重跑。",
                strict=strict,
            )
        )

    return _finalize_report(
        {
            "kind": "text_audit",
            "source_kind": source_kind,
            "target": target,
            "as_of": as_of,
            "strict": strict,
            "citations": citations,
            "issues": issues,
        }
    )


def _audit_citation(
    db_path: str | Path,
    citation: dict,
    *,
    as_of: str | None,
    strict: bool,
) -> dict:
    audited = {
        "raw": citation.get("raw"),
        "law_input": citation.get("law_input"),
        "number_input": citation.get("number_input"),
        "number": citation.get("number")
        or service.normalize_article_number(citation.get("number_input") or ""),
        "position": citation.get("position"),
        "context": citation.get("context"),
        "quoted_text": citation.get("quoted_text"),
        "resolved": False,
        "law": None,
        "article": None,
        "status": None,
        "text_match": {"checked": False, "kind": "not_checked"},
        "issues": [],
    }
    law_input = audited["law_input"]
    number_input = audited["number_input"]
    if not law_input or not number_input:
        audited["issues"].append(
            _issue(
                "error",
                "invalid_citation",
                "引用缺少法规名或条号。",
                strict=strict,
            )
        )
        return _finalize_citation(audited)

    payload = (
        service.get_article_as_of(db_path, law_input, number_input, as_of)
        if as_of
        else service.get_article(db_path, law_input, number_input)
    )
    if payload is None or payload.get("article") is None:
        diag = service.diagnose_article_miss(
            db_path,
            law_input,
            number_input,
            as_of=as_of,
        )
        audited["diagnosis"] = diag
        audited["suggested_command"] = _suggested_article_command(
            law_input, number_input, as_of=as_of
        )
        audited["issues"].append(
            _issue(
                "error",
                diag.get("reason") or "article_not_found",
                diag.get("hint") or "法规或条文未能在本地解析。",
                strict=strict,
            )
        )
        return _finalize_citation(audited)

    law = payload.get("law") or {}
    article = payload.get("article") or {}
    audited["resolved"] = True
    audited["law"] = _compact_law(law)
    audited["article"] = _compact_article(article)
    audited["status"] = law.get("status")
    audited["suggested_command"] = _suggested_article_command(
        law.get("short_title") or law.get("title") or law_input,
        article.get("number") or number_input,
        as_of=as_of,
    )

    status_issue = _status_issue(law, as_of=as_of, strict=strict)
    if status_issue:
        audited["issues"].append(status_issue)

    quoted_text = citation.get("quoted_text")
    if quoted_text:
        text_match = _compare_quoted_text(quoted_text, article.get("text") or "")
        audited["text_match"] = text_match
        if text_match["kind"] in {"cosmetic_drift", "wording_drift"}:
            audited["issues"].append(
                _issue(
                    "warning",
                    text_match["kind"],
                    "引用文字与本地条文存在轻微差异，请人工复核。",
                    strict=strict,
                    details={"similarity": text_match.get("similarity")},
                )
            )
        elif text_match["kind"] == "mismatch":
            audited["issues"].append(
                _issue(
                    "error",
                    "quoted_text_mismatch",
                    "引用文字与本地条文不匹配。",
                    strict=strict,
                    details={"similarity": text_match.get("similarity")},
                )
            )

    return _finalize_citation(audited)


def _context(text: str, start: int, end: int, radius: int = 80) -> str:
    left = max(0, start - radius)
    right = min(len(text or ""), end + radius)
    return (text or "")[left:right].replace("\n", " ").strip()


def _extract_quoted_text(text: str, citation_end: int) -> str | None:
    after = (text or "")[citation_end : citation_end + 800]
    stripped = after.lstrip()
    if not stripped:
        return None
    quote_intro = re.match(
        r"^(?:规定|明确|载明|指出|要求|约定|称)?[：:，,、\s]*[“\"'‘『「]",
        stripped,
    )
    explicit_intro = re.match(
        r"^(?:原文|条文|内容|摘录|引用|规定如下|明确如下|载明如下)"
        r"[：:，,、\s]*",
        stripped,
    )
    if not quote_intro and not explicit_intro:
        return None
    stripped = re.sub(
        r"^(?:规定如下|明确如下|载明如下|规定|明确|载明|指出|要求|约定|称|原文|条文|内容|摘录|引用)"
        r"[：:，,、\s]*",
        "",
        stripped,
    ).lstrip("：:，,、 \t“”\"'‘’『』「」")
    if not stripped:
        return None
    quote_match = re.match(r"[“\"'‘『「](.*?)[”\"'’』」]", stripped, flags=re.S)
    if quote_match:
        candidate = quote_match.group(1).strip()
    else:
        candidate = re.split(r"[\n。；;]", stripped, maxsplit=1)[0].strip()
    candidate = candidate.strip("：:，,、 \t“”\"'‘’『』「」")
    return candidate if len(_normalize_for_compare(candidate)) >= 8 else None


def _normalize_for_compare(text: str) -> str:
    return _PUNCT_RE.sub("", text or "")


def _compare_quoted_text(quoted: str, official: str) -> dict:
    quoted_norm = _normalize_for_compare(quoted)
    official_norm = _normalize_for_compare(official)
    if not quoted_norm or not official_norm:
        return {"checked": False, "kind": "not_checked"}
    if quoted_norm in official_norm:
        return {"checked": True, "kind": "exact_excerpt", "similarity": 1.0}
    similarity = _best_similarity(quoted_norm, official_norm)
    if similarity >= 0.95:
        kind = "cosmetic_drift"
    elif similarity >= 0.78:
        kind = "wording_drift"
    else:
        kind = "mismatch"
    return {
        "checked": True,
        "kind": kind,
        "similarity": round(similarity, 4),
        "quoted_text": quoted,
    }


def _best_similarity(needle: str, haystack: str) -> float:
    if not needle or not haystack:
        return 0.0
    if len(needle) >= len(haystack):
        return SequenceMatcher(None, needle, haystack).ratio()
    window_size = min(len(haystack), max(len(needle) + 20, len(needle)))
    step = max(1, len(needle) // 3)
    best = 0.0
    for start in range(0, max(1, len(haystack) - window_size + 1), step):
        window = haystack[start : start + window_size]
        best = max(best, SequenceMatcher(None, needle, window).ratio())
        if best >= 0.98:
            break
    return best


def _classify_grounding(citation: dict, records: list[dict]) -> dict:
    law = citation.get("law") or {}
    article = citation.get("article") or {}
    law_id = law.get("id")
    number = str(article.get("number") or citation.get("number") or "")
    if not law_id or not number:
        return {"status": "unresolved", "evidence_id": None}

    retrieved_candidate: dict | None = None
    law_candidate: dict | None = None
    for record in records:
        for item in record.get("articles") or []:
            if item.get("law_id") != law_id:
                continue
            if str(item.get("number") or "") == number:
                if item.get("evidence_level") == "article":
                    return {
                        "status": "verified",
                        "evidence_id": record.get("evidence_id"),
                        "command": record.get("command"),
                        "evidence_level": item.get("evidence_level"),
                    }
                retrieved_candidate = {
                    "evidence_id": record.get("evidence_id"),
                    "command": record.get("command"),
                    "evidence_level": item.get("evidence_level"),
                }
        for law_item in record.get("laws") or []:
            if law_item.get("law_id") == law_id:
                law_candidate = {
                    "evidence_id": record.get("evidence_id"),
                    "command": record.get("command"),
                    "evidence_level": law_item.get("evidence_level"),
                }
    if retrieved_candidate:
        return {"status": "retrieved_only", **retrieved_candidate}
    if law_candidate:
        return {"status": "retrieved_only", **law_candidate}
    return {"status": "ungrounded", "evidence_id": None}


def _has_time_effect_evidence(records: list[dict]) -> bool:
    return any(
        record.get("command") in {"applicable", "relation", "history", "trace"}
        or bool(record.get("time_effect"))
        for record in records
    )


def _grounding_counts(citations: list[dict]) -> dict:
    counts = {"verified": 0, "retrieved_only": 0, "ungrounded": 0, "unresolved": 0}
    for citation in citations:
        status = ((citation.get("grounding") or {}).get("status")) or "unresolved"
        counts[status] = counts.get(status, 0) + 1
    return counts


def _status_issue(law: dict, *, as_of: str | None, strict: bool) -> dict | None:
    if law.get("via") == "norm_fallback":
        return None
    status = law.get("status")
    if status in {"current", "active"}:
        return None
    if as_of:
        return None
    if status == "repealed":
        return _issue(
            "error",
            "repealed_law_without_as_of",
            "引用法规当前状态为 repealed；如审查历史事实，请指定 --as-of。",
            strict=strict,
        )
    return _issue(
        "warning",
        "non_current_law_status",
        f"引用法规当前状态为 {status or 'unknown'}，请确认是否可作为当前依据。",
        strict=strict,
    )


def _issue(
    severity: str,
    code: str,
    message: str,
    *,
    strict: bool,
    details: dict | None = None,
) -> dict:
    final_severity = "error" if strict and severity == "warning" else severity
    issue = {
        "severity": final_severity,
        "code": code,
        "message": message,
    }
    if strict and severity == "warning":
        issue["promoted_by_strict"] = True
    if details:
        issue["details"] = details
    return issue


def _finalize_citation(citation: dict) -> dict:
    issues = citation.get("issues") or []
    citation["ok"] = not any(issue.get("severity") == "error" for issue in issues)
    citation["error_count"] = sum(1 for issue in issues if issue.get("severity") == "error")
    citation["warning_count"] = sum(
        1 for issue in issues if issue.get("severity") == "warning"
    )
    return citation


def _finalize_report(report: dict) -> dict:
    citation_issues = [
        issue
        for citation in report.get("citations") or []
        for issue in citation.get("issues") or []
    ]
    issues = [*(report.get("issues") or []), *citation_issues]
    error_count = sum(1 for issue in issues if issue.get("severity") == "error")
    warning_count = sum(1 for issue in issues if issue.get("severity") == "warning")
    citations = report.get("citations") or []
    report.update(
        {
            "ok": error_count == 0,
            "citation_count": len(citations),
            "resolved_count": sum(1 for citation in citations if citation.get("resolved")),
            "checked_text_count": sum(
                1
                for citation in citations
                if (citation.get("text_match") or {}).get("checked")
            ),
            "error_count": error_count,
            "warning_count": warning_count,
        }
    )
    return report


def _missing_target_report(
    *,
    kind: str,
    target: str,
    code: str,
    message: str,
    strict: bool,
) -> dict:
    return _finalize_report(
        {
            "kind": kind,
            "target": target,
            "strict": strict,
            "citations": [],
            "issues": [
                _issue("error", code, message, strict=strict),
            ],
        }
    )


def _compact_law(law: dict) -> dict:
    return {
        "id": law.get("id"),
        "title": law.get("title"),
        "short_title": law.get("short_title"),
        "status": law.get("status"),
        "level": law.get("level"),
        "source_url": law.get("source_url"),
        "source_name": law.get("source_name"),
        "source_checked_at": law.get("source_checked_at"),
        "via": law.get("via"),
    }


def _compact_article(article: dict) -> dict:
    return {
        "id": article.get("id"),
        "law_id": article.get("law_id"),
        "number": article.get("number"),
        "number_display": article.get("number_display"),
        "title": article.get("title"),
        "text": article.get("text"),
        "via": article.get("via"),
    }


def _suggested_article_command(law: str, number: str, *, as_of: str | None) -> str:
    safe_law = shlex.quote(str(law))
    safe_number = shlex.quote(str(number))
    if as_of:
        return f"chinalaw article {safe_law} {safe_number} --as-of {as_of} --format card"
    return f"chinalaw article {safe_law} {safe_number} --format card"


def _pack_item_label(item: dict) -> str:
    law = item.get("law_title") or item.get("law_id") or "?"
    number = item.get("article_number_display") or item.get("article_number") or "?"
    return f"{law} {number}"


def _pack_item_container(item: dict) -> dict:
    return {
        "pack_item_id": item.get("id"),
        "pack_item_type": item.get("item_type"),
        "pack_item_position": item.get("position"),
        "role": item.get("role"),
        "reason": item.get("reason"),
    }
