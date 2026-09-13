"""Read-only HTTP adapter. Authorization applies to every result kind."""

from __future__ import annotations

import shutil

from fastapi import APIRouter, Request

from chinalaw import models, sources
from chinalaw.admin import catalog
from chinalaw.server.dependencies import owner, principal, require_document_scope

router = APIRouter(prefix="/api/v1", tags=["library queries"])


@router.get("/documents")
def documents(
    request: Request,
    kind: str = "law",
    q: str = "",
    page: int = 1,
    page_size: int = 24,
    status: str | None = None,
    category: str | None = None,
    source: str | None = None,
) -> dict:
    value = principal(request)
    require_document_scope(value, kind)
    return catalog.list_documents(
        request.app.state.config.db_path,
        kind=kind,
        query=q,
        page=page,
        page_size=page_size,
        status=status,
        category=category,
        source=source,
    )


@router.get("/document")
def document(request: Request, kind: str, id: str) -> dict:
    value = principal(request)
    require_document_scope(value, kind)
    return catalog.get_document(
        request.app.state.config.db_path, kind, id, include_management=value.is_owner
    )


@router.get("/revision")
def revision(request: Request, kind: str, id: str, revision: str) -> dict:
    require_document_scope(principal(request), kind)
    return catalog.revision_document(request.app.state.config.db_path, kind, id, revision)


@router.get("/revisions")
def revisions(request: Request, kind: str, id: str) -> dict:
    require_document_scope(principal(request), kind)
    return catalog.revisions(request.app.state.config.db_path, kind, id)


@router.get("/search")
def search(request: Request, q: str, kind: str = "all", limit: int = 20) -> dict:
    value = principal(request)
    return catalog.search_library(
        request.app.state.config.db_path,
        q,
        kind=kind,
        include_private=value.can_read_private,
        limit=limit,
    )


@router.get("/system")
def system(request: Request) -> dict:
    owner(request)
    return catalog.dashboard(request.app.state.config.db_path)


@router.get("/options")
def options(request: Request) -> dict:
    principal(request)
    return {
        "law_levels": [item.value for item in models.LawLevel],
        "law_statuses": [item.value for item in models.LawStatus],
        "norm_types": [item.value for item in models.NormSourceType],
        "binding_notes": models.NORM_SOURCE_TYPE_BINDING_NOTES,
        "fetch_sources": list(sources.VERIFIABLE_SOURCES),
        "pdf_text_available": shutil.which("pdftotext") is not None,
    }
