"""Single-owner consent and persistence behind the SDK's OAuth/PKCE endpoints."""

from __future__ import annotations

import json
import secrets
import time
import uuid
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    OAuthAuthorizationServerProvider,
    RefreshToken,
    RegistrationError,
    TokenError,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from chinalaw.admin.errors import LibraryError
from chinalaw.server.auth_store import (
    PUBLIC_SCOPE,
    READ_SCOPES,
    AuthStore,
    token_digest,
    validate_scopes,
)

MAX_CLIENTS = 256
MAX_PENDING_PER_CLIENT = 10
UNUSED_CLIENT_SECONDS = 24 * 3600


class StoredCode(AuthorizationCode):
    grant_id: str
    name: str


class StoredRefresh(RefreshToken):
    grant_id: str
    name: str


class StoredAccess(AccessToken):
    grant_id: str


class OwnerOAuth(OAuthAuthorizationServerProvider[StoredCode, StoredRefresh, StoredAccess]):
    def __init__(self, store: AuthStore, origin: str):
        self.store = store
        self.origin = origin

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        with self.store.transaction() as conn:
            row = conn.execute(
                "SELECT metadata_json FROM oauth_clients WHERE id = ?", (client_id,)
            ).fetchone()
        return OAuthClientInformationFull.model_validate_json(row[0]) if row else None

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        if not client_info.client_id or len(client_info.client_id) > 256:
            raise RegistrationError(error="invalid_client_metadata", error_description="Invalid ID")
        scopes = set((client_info.scope or PUBLIC_SCOPE).split())
        if not scopes <= READ_SCOPES:
            raise RegistrationError(
                error="invalid_client_metadata", error_description="Only read scopes are available"
            )
        for redirect in client_info.redirect_uris or []:
            parsed = urlsplit(str(redirect))
            local = parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
            if (parsed.scheme != "https" and not local) or parsed.fragment or parsed.username:
                raise RegistrationError(
                    error="invalid_redirect_uri",
                    error_description="Redirects require HTTPS or HTTP on a loopback address",
                )
        with self.store.transaction() as conn:
            conn.execute("BEGIN IMMEDIATE")
            # Reclaim dynamic registrations that never reached an approved consent.
            conn.execute(
                "DELETE FROM oauth_clients WHERE last_grant_at IS NULL AND created_at <= ?",
                (int(time.time()) - UNUSED_CLIENT_SECONDS,),
            )
            count = conn.execute("SELECT COUNT(*) FROM oauth_clients").fetchone()[0]
            if count >= MAX_CLIENTS:
                raise RegistrationError(
                    error="invalid_client_metadata",
                    error_description="Client registration limit reached",
                )
            conn.execute(
                "INSERT INTO oauth_clients(id, metadata_json, created_at) VALUES (?, ?, ?)",
                (client_info.client_id, client_info.model_dump_json(), int(time.time())),
            )

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        if params.resource not in {None, self.store.resource_url}:
            raise AuthorizeError(error="invalid_request", error_description="Unknown resource")
        scopes = params.scopes or [PUBLIC_SCOPE]
        if not set(scopes) <= READ_SCOPES or PUBLIC_SCOPE not in scopes:
            raise AuthorizeError(
                error="invalid_scope", error_description="Only read scopes are available"
            )
        request_id = uuid.uuid4().hex
        with self.store.transaction() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("DELETE FROM oauth_requests WHERE expires_at <= ?", (int(time.time()),))
            count = conn.execute(
                "SELECT COUNT(*) FROM oauth_requests WHERE consumed = 0 AND client_id = ?",
                (client.client_id,),
            ).fetchone()[0]
            if count >= MAX_PENDING_PER_CLIENT:
                raise AuthorizeError(
                    error="temporarily_unavailable", error_description="Try again later"
                )
            conn.execute(
                "INSERT INTO oauth_requests(id, client_id, params_json, expires_at) "
                "VALUES (?, ?, ?, ?)",
                (request_id, client.client_id, params.model_dump_json(), int(time.time()) + 600),
            )
        return self.origin + "/?consent=" + request_id

    def consent_details(self, request_id: str) -> dict:
        with self.store.transaction() as conn:
            row = self._pending_request(conn, request_id)
            client = conn.execute(
                "SELECT metadata_json FROM oauth_clients WHERE id = ?", (row["client_id"],)
            ).fetchone()
        metadata = json.loads(client["metadata_json"])
        params = AuthorizationParams.model_validate_json(row["params_json"])
        return {
            "id": request_id,
            "client_name": metadata.get("client_name") or "未命名应用",
            "redirect_uri": str(params.redirect_uri),
            "scopes": params.scopes or [PUBLIC_SCOPE],
            "expires_at": row["expires_at"],
            "resource": self.store.resource_url,
        }

    def complete_consent(self, request_id: str, *, approved: bool, scopes: list[str]) -> str:
        with self.store.transaction() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = self._pending_request(conn, request_id)
            params = AuthorizationParams.model_validate_json(row["params_json"])
            requested = params.scopes or [PUBLIC_SCOPE]
            if approved:
                scopes = validate_scopes(scopes)
                if not set(scopes) <= set(requested):
                    raise LibraryError("invalid_scope", "不能授予应用未申请的权限。")
            conn.execute("UPDATE oauth_requests SET consumed = 1 WHERE id = ?", (request_id,))
            if not approved:
                return _redirect(str(params.redirect_uri), {"error": "access_denied"}, params.state)
            client = conn.execute(
                "SELECT metadata_json FROM oauth_clients WHERE id = ?", (row["client_id"],)
            ).fetchone()
            client_name = json.loads(client[0]).get("client_name") or "MCP application"
            raw_code = "clc_" + secrets.token_urlsafe(32)
            code = StoredCode(
                code="",
                scopes=scopes,
                expires_at=time.time() + 120,
                client_id=row["client_id"],
                code_challenge=params.code_challenge,
                redirect_uri=params.redirect_uri,
                redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
                resource=self.store.resource_url,
                subject="owner",
                grant_id=uuid.uuid4().hex,
                name=client_name[:80],
            )
            conn.execute(
                "INSERT INTO oauth_codes(digest, metadata_json, expires_at) VALUES (?, ?, ?)",
                (token_digest(raw_code), code.model_dump_json(), int(code.expires_at)),
            )
            conn.execute(
                "UPDATE oauth_clients SET last_grant_at = ? WHERE id = ?",
                (int(time.time()), row["client_id"]),
            )
        return _redirect(str(params.redirect_uri), {"code": raw_code}, params.state)

    @staticmethod
    def _pending_request(conn, request_id: str):
        row = conn.execute(
            "SELECT * FROM oauth_requests WHERE id = ? AND consumed = 0 AND expires_at > ?",
            (request_id, int(time.time())),
        ).fetchone()
        if row is None:
            raise LibraryError(
                "consent_expired", "授权请求已过期或已处理，请从应用重新连接。", status=410
            )
        return row

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> StoredCode | None:
        with self.store.transaction() as conn:
            row = conn.execute(
                "SELECT metadata_json FROM oauth_codes WHERE digest = ? AND expires_at > ?",
                (token_digest(authorization_code), int(time.time())),
            ).fetchone()
        if row is None:
            return None
        code = StoredCode.model_validate_json(row[0])
        if code.client_id != client.client_id or code.resource != self.store.resource_url:
            return None
        return code.model_copy(update={"code": authorization_code})

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: StoredCode
    ) -> OAuthToken:
        with self.store.transaction() as conn:
            conn.execute("BEGIN IMMEDIATE")
            deleted = conn.execute(
                "DELETE FROM oauth_codes WHERE digest = ? AND expires_at > ?",
                (token_digest(authorization_code.code), int(time.time())),
            ).rowcount
            if deleted != 1 or authorization_code.client_id != client.client_id:
                raise TokenError(
                    error="invalid_grant", error_description="Code expired or already used"
                )
            return self._issue_pair(conn, authorization_code)

    def _issue_pair(self, conn, grant: StoredCode | StoredRefresh) -> OAuthToken:
        access = "clo_" + secrets.token_urlsafe(32)
        refresh = "clr_" + secrets.token_urlsafe(32)
        for token, kind, lifetime in (
            (access, "oauth_access", 3600),
            (refresh, "oauth_refresh", 30 * 86400),
        ):
            self.store.insert_token(
                conn,
                token,
                name=grant.name,
                kind=kind,
                client_id=grant.client_id,
                grant_id=grant.grant_id,
                scopes=grant.scopes,
                expires_at=int(time.time()) + lifetime,
            )
        return OAuthToken(
            access_token=access,
            token_type="Bearer",
            expires_in=3600,
            refresh_token=refresh,
            scope=" ".join(grant.scopes),
        )

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> StoredRefresh | None:
        with self.store.transaction() as conn:
            row = self.store.lookup_token(conn, refresh_token, kinds=("oauth_refresh",))
        if row is None or row["client_id"] != client.client_id:
            return None
        return StoredRefresh(
            token=refresh_token,
            client_id=row["client_id"],
            scopes=json.loads(row["scopes_json"]),
            expires_at=row["expires_at"],
            resource=row["resource"],
            subject="owner",
            grant_id=row["grant_id"],
            name=row["name"],
        )

    async def exchange_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: StoredRefresh, scopes: list[str]
    ) -> OAuthToken:
        if refresh_token.client_id != client.client_id or not set(scopes) <= set(
            refresh_token.scopes
        ):
            raise TokenError(
                error="invalid_scope", error_description="Refresh cannot expand access"
            )
        with self.store.transaction() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = self.store.lookup_token(conn, refresh_token.token, kinds=("oauth_refresh",))
            if row is None:
                raise TokenError(
                    error="invalid_grant", error_description="Refresh expired or already used"
                )
            conn.execute(
                "UPDATE credentials SET revoked_at = ? WHERE grant_id = ?",
                (int(time.time()), refresh_token.grant_id),
            )
            return self._issue_pair(conn, refresh_token.model_copy(update={"scopes": scopes}))

    async def load_access_token(self, token: str) -> StoredAccess | None:
        principal = self.store.bearer(token)
        if principal is None:
            return None
        with self.store.transaction() as conn:
            row = self.store.lookup_token(conn, token, kinds=("personal", "oauth_access"))
        if row is None:
            return None
        return StoredAccess(
            token=token,
            client_id=row["client_id"],
            scopes=sorted(principal.scopes),
            expires_at=row["expires_at"],
            resource=row["resource"],
            subject="owner",
            grant_id=row["grant_id"],
        )

    async def revoke_token(self, token: StoredAccess | StoredRefresh) -> None:
        with self.store.transaction() as conn:
            conn.execute(
                "UPDATE credentials SET revoked_at = ? WHERE grant_id = ?",
                (int(time.time()), token.grant_id),
            )


def _redirect(uri: str, parameters: dict[str, str], state: str | None) -> str:
    parsed = urlsplit(uri)
    query = [*parse_qsl(parsed.query, keep_blank_values=True), *parameters.items()]
    if state is not None:
        query.append(("state", state))
    return urlunsplit(parsed._replace(query=urlencode(query)))
