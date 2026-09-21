"""Authenticated MCP HTTP queries using the official SDK, without mutation tools."""

from __future__ import annotations

from mcp.server import MCPServer
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import AnyHttpUrl

from chinalaw import __version__, service
from chinalaw.admin import catalog
from chinalaw.admin.errors import LibraryError
from chinalaw.db import read_only_operation
from chinalaw.server.auth_store import PRIVATE_SCOPE, PUBLIC_SCOPE, READ_SCOPES
from chinalaw.server.config import ServerConfig
from chinalaw.server.oauth import OwnerOAuth

READ_ONLY = ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
)


def _private_allowed() -> bool:
    token = get_access_token()
    if token is None or token.subject != "owner" or PUBLIC_SCOPE not in token.scopes:
        raise LibraryError("authentication_required", "需要有效的只读凭据。", status=401)
    return PRIVATE_SCOPE in token.scopes


def make_mcp(config: ServerConfig, oauth: OwnerOAuth):
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

    @server.tool(annotations=READ_ONLY)
    def chinalaw_resolve(name: str) -> dict:
        """Resolve a public law name/alias to the local record and source metadata."""
        _private_allowed()
        if not 1 <= len(name) <= 200:
            raise ValueError("name must contain 1–200 characters")
        with read_only_operation():
            return service.resolve(config.db_path, name)

    @server.tool(annotations=READ_ONLY)
    def chinalaw_search(query: str, kind: str = "all", limit: int = 10) -> dict:
        """Search grounded text. Private hits appear only with private-read authorization."""
        return catalog.search_library(
            config.db_path,
            query,
            kind=kind,
            limit=limit,
            include_private=_private_allowed(),
        )

    @server.tool(annotations=READ_ONLY)
    def chinalaw_article(law: str, number: str, as_of: str | None = None) -> dict:
        """Read one complete article with provenance, optionally at a historical date."""
        include_private = _private_allowed()
        if not 1 <= len(law) <= 200 or not 1 <= len(number) <= 60:
            raise ValueError("law or article number is too long")
        with read_only_operation():
            if as_of:
                result = service.get_article_as_of(
                    config.db_path, law, number, as_of, include_norm=include_private
                )
            else:
                result = service.get_article(
                    config.db_path, law, number, include_norm=include_private
                )
        return result or {"kind": "article_missing", "error": "article_not_found", "law": law}

    @server.tool(annotations=READ_ONLY)
    def chinalaw_list(
        kind: str = "law", query: str = "", page: int = 1, page_size: int = 20
    ) -> dict:
        """List documents in this library, with stable pagination and provenance."""
        if kind == "norm" and not _private_allowed():
            raise LibraryError("private_access_denied", "未获私域规范访问权限。", status=403)
        _private_allowed()
        return catalog.list_documents(
            config.db_path, kind=kind, query=query, page=page, page_size=page_size
        )

    @server.tool(annotations=READ_ONLY)
    def chinalaw_document(kind: str, id: str, offset: int = 0, limit: int = 50) -> dict:
        """Read complete clauses from a document; use offset to continue through long documents."""
        private = _private_allowed()
        if kind == "norm" and not private:
            raise LibraryError("private_access_denied", "未获私域规范访问权限。", status=403)
        if offset < 0 or not 1 <= limit <= 100:
            raise ValueError("offset must be non-negative and limit must be 1–100")
        result = catalog.get_document(config.db_path, kind, id, include_management=False)
        member = "articles" if kind == "law" else "clauses"
        clauses = result["document"].pop(member)
        result["document"][member] = clauses[offset : offset + limit]
        result.update(
            total=len(clauses), offset=offset, limit=limit, has_more=offset + limit < len(clauses)
        )
        return result

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
