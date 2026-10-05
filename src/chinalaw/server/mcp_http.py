"""Authenticated MCP HTTP queries using the official SDK, without mutation tools."""

from __future__ import annotations

import json

from mcp.server import MCPServer
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from pydantic import AnyHttpUrl

from chinalaw import __version__, service
from chinalaw.admin import catalog
from chinalaw.admin.errors import LibraryError, private_access_denied
from chinalaw.article_views import ArticleDetail, article_view
from chinalaw.db import read_only_operation
from chinalaw.search_views import SearchView, search_view
from chinalaw.server.auth_store import PRIVATE_SCOPE, PUBLIC_SCOPE, READ_SCOPES
from chinalaw.server.config import ServerConfig
from chinalaw.server.oauth import OwnerOAuth
from chinalaw.server.query_log import QueryLog
from chinalaw.server.search_executor import SearchExecutor

READ_ONLY = ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
)


def _private_allowed() -> bool:
    token = get_access_token()
    if token is None or token.subject != "owner" or PUBLIC_SCOPE not in token.scopes:
        raise LibraryError("authentication_required", "需要有效的只读凭据。", status=401)
    return PRIVATE_SCOPE in token.scopes


def _document_payload(db_path, kind: str, id: str) -> dict:
    try:
        result = catalog.get_document(db_path, kind, id, include_management=False)
    except LibraryError as exc:
        if kind != "law" or exc.code != "document_not_found":
            raise
        with read_only_operation():
            resolved = service.resolve(db_path, id)
        if not resolved["matched"]:
            raise LibraryError(
                "document_not_found",
                "未找到法规；请核对名称或候选名单。",
                status=404,
                details=resolved,
            ) from exc
        result = catalog.get_document(db_path, kind, resolved["id"], include_management=False)
    return result


def _logged(query_log, tool: str, params: dict, call):
    try:
        if query_log is None:
            return call()
        token = get_access_token()
        return query_log.run(
            call,
            channel="mcp",
            client=_log_label(token),
            tool=tool,
            params=params,
        )
    except LibraryError as exc:
        # Adapt after logging so expected failures remain failures in the audit log.
        payload = {**exc.payload(), "status": exc.status}
        return CallToolResult(
            is_error=True,
            content=[TextContent(type="text", text=json.dumps(payload, ensure_ascii=False))],
            structured_content=payload,
        )


def _article_payload(db_path, law: str, number: str, as_of: str | None,
                     *, include_private: bool) -> dict:
    if not 1 <= len(law) <= 200 or not 1 <= len(number) <= 60:
        raise LibraryError("invalid_arguments", "law or article number is too long")
    with read_only_operation():
        if as_of:
            result = service.get_article_as_of(
                db_path, law, number, as_of, include_norm=include_private
            )
        else:
            result = service.get_article(
                db_path, law, number, include_norm=include_private
            )
        if result and result.get("article") is not None:
            return result
        if result and result.get("error"):
            return {**result, "found": False}
        if result and isinstance(result.get("law"), dict):
            return {
                **result, "kind": "article_missing", "found": False,
                "error": "article_not_found",
                "reason": "article_null_as_of" if as_of else "article_null",
                "hint": (
                    "法规或规范已定位，但该条号在所请求的版本中未找到。请核对条号与版本；"
                    "条文总数不等于最大条号，历史条文不能用当前文本替代。"
                ),
            }
        diagnosis = service.diagnose_article_miss(db_path, law, number, as_of=as_of)
        return {
            "kind": "article_missing",
            "found": False,
            "error": "invalid_as_of" if diagnosis["reason"] == "invalid_as_of"
            else "article_not_found",
            "law": law,
            **diagnosis,
        }


def make_mcp(
    config: ServerConfig, oauth: OwnerOAuth, query_log: QueryLog | None = None,
    search_executor: SearchExecutor | None = None,
):
    executor = search_executor or SearchExecutor(config.search_concurrency, config.search_queue)
    server = MCPServer(
        "chinalaw",
        version=__version__,
        instructions=(
            "Read this user's Chinese normative-source library. Results preserve provenance. "
            "Private sources require explicit permission. Missing records are not fetched. "
            "Use the human management panel for import, correction and review."
        ),
        auth_server_provider=oauth,
        auth=AuthSettings(
            issuer_url=AnyHttpUrl(config.origin),
            resource_server_url=AnyHttpUrl(config.resource_url),
            required_scopes=[PUBLIC_SCOPE],
            validate_token_resource=True,
            client_registration_options=ClientRegistrationOptions(
                enabled=True,
                valid_scopes=sorted(READ_SCOPES),
                default_scopes=[PUBLIC_SCOPE],
            ),
            revocation_options=RevocationOptions(enabled=True),
        ),
    )

    def logged(tool: str, params: dict, call):
        return _logged(query_log, tool, params, call)

    def resolve(name: str) -> dict:
        _private_allowed()
        if not 1 <= len(name) <= 200:
            raise LibraryError("invalid_arguments", "name must contain 1–200 characters")
        with read_only_operation():
            return service.resolve(config.db_path, name)

    @server.tool(annotations=READ_ONLY)
    def chinalaw_resolve(name: str) -> dict:
        """Resolve a public law name/alias or document number to source metadata.

        Check official_title: via=like_fallback is only a title substring match,
        which may name an amendment decision rather than the requested full law.
        A shared document number returns candidates for explicit ID selection.
        """
        return logged("resolve", {"name": name}, lambda: resolve(name))

    @server.tool(annotations=READ_ONLY)
    async def chinalaw_search(
        query: str,
        kind: str = "all",
        limit: int = 10,
        as_of: str | None = None,
        status: str | None = None,
        level: str | None = None,
        region: str | None = None,
        versions: str = "folded",
        in_laws: list[str] | str | None = None,
        view: SearchView = "full",
    ) -> dict:
        """Exact-first search; sparse article hits add literal fragments marked fuzzy.

        Fuzzy fragments must all occur in one article. Empty results suggest
        retrying with statutory wording. Bare article numbers are content searches;
        use chinalaw_article(law, number) for a particular article.

        kind=law searches PUBLIC document titles, including judicial interpretations;
        kind=article searches public provisions. kind=norm means PRIVATE imported
        documents, not all normative sources. kind=all searches authorized content.
        kind=law/all also matches exact document-number metadata (e.g. 法释〔2024〕10号).
        A title plus document number must match both; verify the returned metadata.
        A zero-hit result may include guidance.next_steps with explicit arguments;
        suggested searches retain the original filters and are not already executed.

        Private hits appear only with private-read authorization. Public hits
        are judged on ``as_of`` (YYYY-MM-DD, default today in Beijing): laws in
        force and national levels come first, and each law shows one version
        (``versions="all"`` shows every version). ``status`` (current,
        amended, repealed, pending_effective, unknown), ``level`` (e.g.
        law,judicial_interpretation) and ``region`` (e.g. 上海市) filter. A
        citation such as 民法典第五百零四条 returns that article first.
        in_laws limits public search to law names/IDs (string or list, up to 20).
        Unresolved scopes are reported and never fall back to global search.

        view=brief preserves candidates/metadata, replacing provision text with
        literal excerpts (max 240 Unicode characters). excerpt.truncated marks
        partial text; use each hit's read tool/arguments for full text and check
        text_version.sha256. view.full repeats this search with all text.
        """
        options = {
            "as_of": as_of,
            "status": status,
            "level": level,
            "region": region,
            "versions": versions,
            "in_laws": in_laws,
        }
        given = {key: value for key, value in options.items() if value and value != "folded"}
        params = {"query": query, "kind": kind, "limit": limit, **given}
        if view != "full":
            params["view"] = view

        return await executor.run_logged(
            lambda: search_view(
                catalog.search_library(
                    config.db_path, query, kind=kind, limit=limit,
                    include_private=_private_allowed(), **options,
                ), arguments=params, view=view,
            ),
            lambda call: logged("search", params, call),
        )

    def applicable(date: str, topic: str | None, law: str | None, domain: str | None) -> dict:
        _private_allowed()
        if any(len(value or "") > 200 for value in (date, topic, law, domain)):
            raise LibraryError("invalid_arguments", "arguments must be at most 200 characters")
        with read_only_operation():
            return service.applicable(
                config.db_path, as_of=date, topic=topic, law=law, domain=domain
            )

    @server.tool(annotations=READ_ONLY)
    def chinalaw_applicable(
        date: str, topic: str | None = None, law: str | None = None, domain: str | None = None
    ) -> dict:
        """Rules on which law applies to facts of a given date (YYYY-MM-DD).

        Curated transition rules, e.g. 民法典 and 公司法 2023 time-effect
        provisions and 刑法 retroactivity. Filter by topic, law or domain.
        Domain labels are listed in coverage.domains. A specific domain also
        includes rows tagged all; domain=all selects only that tag. Omit domain
        to query every domain at the date.
        The top-level law is reference metadata, not the law version at that date.
        """
        return logged(
            "applicable",
            {"date": date, "topic": topic, "law": law, "domain": domain},
            lambda: applicable(date, topic, law, domain),
        )

    def article(law: str, number: str, as_of: str | None, detail: ArticleDetail) -> dict:
        payload = _article_payload(config.db_path, law, number, as_of,
                                   include_private=_private_allowed())
        return article_view(payload, law=law, number=number, as_of=as_of, detail=detail)

    @server.tool(annotations=READ_ONLY)
    def chinalaw_article(
        law: str, number: str, as_of: str | None = None, detail: ArticleDetail = "full",
    ) -> dict:
        """Read a complete article with provenance, optionally at a historical date.

        detail=compact omits the duplicate item and full version lists, retaining
        complete text, selected/current versions and diagnostics. view.full gives
        the same lookup with full history. Default full preserves all fields.
        """
        params = {"law": law, "number": number, "as_of": as_of}
        if detail != "full":
            params["detail"] = detail
        return logged(
            "article", params, lambda: article(law, number, as_of, detail),
        )

    def list_documents(kind: str, query: str, page: int, page_size: int) -> dict:
        if kind == "norm" and not _private_allowed():
            raise private_access_denied()
        _private_allowed()
        return catalog.list_documents(
            config.db_path, kind=kind, query=query, page=page, page_size=page_size
        )

    @server.tool(annotations=READ_ONLY)
    def chinalaw_list(
        kind: str = "law", query: str = "", page: int = 1, page_size: int = 20
    ) -> dict:
        """List documents in this library, with stable pagination and provenance.

        kind=law lists all PUBLIC document types, including judicial interpretations.
        kind=norm lists PRIVATE imported documents and requires private permission.
        query filters titles, not article text; use search for content keywords.
        """
        return logged(
            "list",
            {"kind": kind, "query": query, "page": page, "page_size": page_size},
            lambda: list_documents(kind, query, page, page_size),
        )

    def document(kind: str, id: str, offset: int, limit: int) -> dict:
        private = _private_allowed()
        if kind == "norm" and not private:
            raise private_access_denied()
        if offset < 0 or not 1 <= limit <= 100:
            raise LibraryError(
                "invalid_arguments", "offset must be non-negative and limit must be 1–100"
            )
        if not 1 <= len(id) <= 200:
            raise LibraryError("invalid_arguments", "id must contain 1–200 characters")
        result = _document_payload(config.db_path, kind, id)
        member = "articles" if kind == "law" else "clauses"
        clauses = result["document"].pop(member)
        result["document"][member] = clauses[offset : offset + limit]
        result.update(
            total=len(clauses), offset=offset, limit=limit, has_more=offset + limit < len(clauses),
            returned=len(result["document"][member]),
            next_offset=offset + limit if offset + limit < len(clauses) else None,
        )
        return result

    @server.tool(annotations=READ_ONLY)
    def chinalaw_document(kind: str, id: str, offset: int = 0, limit: int = 50) -> dict:
        """Paginated full text. Law id accepts ID/name/alias; norm requires ID.

        kind=law covers PUBLIC documents; norm is PRIVATE imported material.
        offset is zero-based and limit counts entries, not characters. Continue
        at next_offset; null means finished. Do not restart at offset=0 to read more.
        """
        return logged(
            "document",
            {"kind": kind, "id": id, "offset": offset, "limit": limit},
            lambda: document(kind, id, offset, limit),
        )

    app = server.streamable_http_app(
        json_response=True,
        host=config.host,
        max_request_body_size=256 * 1024,
        max_sessions=100,
        session_idle_timeout=600,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=config.allowed_hosts,
            allowed_origins=[config.origin],
        ),
    )
    return server, app


def _log_label(token) -> str | None:
    if token is None:
        return None
    return getattr(token, "log_label", None) or token.client_id
