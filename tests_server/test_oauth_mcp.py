"""Exercise the SDK's actual HTTP OAuth/PKCE flow, not a mocked auth bypass."""

from __future__ import annotations

import base64
import hashlib
import secrets
from urllib.parse import parse_qs, urlsplit

from chinalaw.server.auth_store import PRIVATE_SCOPE, PUBLIC_SCOPE


def authorization(owner_api, scopes=None):
    scopes = scopes or [PUBLIC_SCOPE]
    registered = owner_api.post(
        "/register",
        json={
            "client_name": "Synthetic MCP client",
            "redirect_uris": ["http://127.0.0.1:43111/callback"],
            "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "scope": " ".join(scopes),
        },
    )
    assert registered.status_code == 201, registered.text
    client_id = registered.json()["client_id"]
    verifier = secrets.token_urlsafe(48)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    )
    response = owner_api.get(
        "/authorize",
        params={
            "client_id": client_id,
            "response_type": "code",
            "redirect_uri": "http://127.0.0.1:43111/callback",
            "scope": " ".join(scopes),
            "state": "literal-test-state",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "resource": owner_api.app.state.config.resource_url,
        },
        follow_redirects=False,
    )
    assert response.status_code in {302, 303, 307}, response.text
    request_id = parse_qs(urlsplit(response.headers["location"]).query)["consent"][0]
    return client_id, verifier, request_id


def consent_code(owner_api, request_id, scopes=None):
    response = owner_api.post(
        "/api/v1/auth/consent/" + request_id,
        json={"approved": True, "scopes": scopes or [PUBLIC_SCOPE]},
    )
    assert response.status_code == 200, response.text
    query = parse_qs(urlsplit(response.json()["redirect_url"]).query)
    assert query["state"] == ["literal-test-state"]
    return query["code"][0]


def exchange(owner_api, client_id, code, verifier):
    return owner_api.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "client_id": client_id,
            "code": code,
            "redirect_uri": "http://127.0.0.1:43111/callback",
            "code_verifier": verifier,
            "resource": owner_api.app.state.config.resource_url,
        },
    )


def test_oauth_discovery_is_real_and_scoped(api):
    resource = api.get("/.well-known/oauth-protected-resource/mcp")
    assert resource.status_code == 200
    assert resource.json()["resource"] == api.app.state.config.resource_url
    assert resource.json()["authorization_servers"]
    metadata = api.get("/.well-known/oauth-authorization-server").json()
    assert metadata["authorization_endpoint"].endswith("/authorize")
    assert "S256" in metadata["code_challenge_methods_supported"]
    assert metadata["registration_endpoint"].endswith("/register")


def test_registration_rejects_executable_or_nonlocal_http_redirects(api):
    for uri in (
        "javascript:alert(1)",
        "http://untrusted.example/callback",
        "https://example.com/#fragment",
    ):
        response = api.post(
            "/register",
            json={
                "client_name": "Synthetic invalid redirect",
                "redirect_uris": [uri],
                "token_endpoint_auth_method": "none",
                "grant_types": ["authorization_code"],
                "response_types": ["code"],
            },
        )
        assert response.status_code == 400, response.text


def test_authorization_rejects_wrong_audience(owner_api):
    registered = owner_api.post(
        "/register",
        json={
            "client_name": "Audience test",
            "redirect_uris": ["https://example.org/callback"],
            "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code"],
            "response_types": ["code"],
        },
    ).json()
    response = owner_api.get(
        "/authorize",
        params={
            "client_id": registered["client_id"],
            "response_type": "code",
            "redirect_uri": "https://example.org/callback",
            "scope": PUBLIC_SCOPE,
            "code_challenge": "A" * 43,
            "code_challenge_method": "S256",
            "resource": "https://another-library.example/mcp",
        },
        follow_redirects=False,
    )
    assert response.status_code in {302, 400}
    assert "invalid_request" in response.text or "invalid_request" in response.headers.get(
        "location", ""
    )


def test_pkce_single_use_code_and_private_scope(owner_api):
    client_id, verifier, request_id = authorization(owner_api, [PUBLIC_SCOPE, PRIVATE_SCOPE])
    details = owner_api.get("/api/v1/auth/consent/" + request_id).json()
    assert details["client_name"] == "Synthetic MCP client"
    code = consent_code(owner_api, request_id, [PUBLIC_SCOPE])
    assert exchange(owner_api, client_id, code, "wrong-verifier" * 4).status_code == 400
    response = exchange(owner_api, client_id, code, verifier)
    assert response.status_code == 200, response.text
    tokens = response.json()
    assert exchange(owner_api, client_id, code, verifier).status_code == 400
    headers = {"Authorization": "Bearer " + tokens["access_token"]}
    assert owner_api.get("/api/v1/documents", headers=headers).status_code == 200
    assert owner_api.get("/api/v1/documents?kind=norm", headers=headers).status_code == 403
    assert owner_api.get("/api/v1/system", headers=headers).status_code == 403


def test_refresh_rotates_and_revocation_revokes_the_grant(owner_api):
    client_id, verifier, request_id = authorization(owner_api)
    tokens = exchange(owner_api, client_id, consent_code(owner_api, request_id), verifier).json()
    form = {
        "grant_type": "refresh_token",
        "client_id": client_id,
        "refresh_token": tokens["refresh_token"],
        "scope": PUBLIC_SCOPE,
    }
    refreshed = owner_api.post("/token", data=form)
    assert refreshed.status_code == 200, refreshed.text
    fresh = refreshed.json()
    assert fresh["refresh_token"] != tokens["refresh_token"]
    assert owner_api.post("/token", data=form).status_code == 400
    assert (
        owner_api.get(
            "/api/v1/documents", headers={"Authorization": "Bearer " + tokens["access_token"]}
        ).status_code
        == 401
    )
    revoke = owner_api.post(
        "/revoke",
        data={
            "client_id": client_id,
            "token": fresh["access_token"],
            "token_type_hint": "access_token",
        },
    )
    assert revoke.status_code == 200, revoke.text
    assert (
        owner_api.get(
            "/api/v1/documents", headers={"Authorization": "Bearer " + fresh["access_token"]}
        ).status_code
        == 401
    )
    assert (
        owner_api.post("/token", data={**form, "refresh_token": fresh["refresh_token"]}).status_code
        == 400
    )


def test_mcp_http_rejects_anonymous_and_exposes_only_read_tools(owner_api):
    headers = {"Accept": "application/json, text/event-stream"}
    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-11-25",
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "1"},
        },
    }
    assert owner_api.post("/mcp", json=request, headers=headers).status_code == 401
    token = owner_api.app.state.auth.issue_query_token("SDK test", [PUBLIC_SCOPE])["token"]
    headers["Authorization"] = "Bearer " + token
    init = owner_api.post("/mcp", json=request, headers=headers)
    assert init.status_code == 200, init.text
    assert "result" in init.json()
    headers["Mcp-Session-Id"] = init.headers["mcp-session-id"]
    headers["MCP-Protocol-Version"] = "2025-11-25"
    owner_api.post(
        "/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"}, headers=headers
    )
    listing = owner_api.post(
        "/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, headers=headers
    )
    assert listing.status_code == 200, listing.text
    tools = listing.json()["result"]["tools"]
    assert {tool["name"] for tool in tools} == {
        "chinalaw_resolve",
        "chinalaw_search",
        "chinalaw_article",
        "chinalaw_list",
        "chinalaw_document",
    }
    assert all(tool["annotations"]["readOnlyHint"] for tool in tools)
    query = owner_api.post(
        "/mcp",
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "chinalaw_search", "arguments": {"query": "私域独有关键词"}},
        },
    )
    assert query.status_code == 200, query.text
    assert "私域独有关键词正文。" not in query.text
    assert not query.json()["result"].get("isError"), query.text


def test_mcp_session_cannot_keep_private_access_after_token_switch_or_revocation(owner_api):
    auth = owner_api.app.state.auth
    private = auth.issue_query_token("private session", [PUBLIC_SCOPE, PRIVATE_SCOPE])
    public = auth.issue_query_token("public session", [PUBLIC_SCOPE])
    headers = {"Accept": "application/json, text/event-stream", "Authorization": "Bearer " + private["token"]}
    initialized = owner_api.post("/mcp", headers=headers, json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "scope-switch", "version": "1"}}})
    assert initialized.status_code == 200
    headers.update({"Mcp-Session-Id": initialized.headers["mcp-session-id"], "MCP-Protocol-Version": "2025-11-25"})
    owner_api.post("/mcp", headers=headers, json={"jsonrpc": "2.0", "method": "notifications/initialized"})
    request = {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "chinalaw_document", "arguments": {"kind": "norm", "id": "private-test"}}}
    allowed = owner_api.post("/mcp", headers=headers, json=request)
    assert "私域独有关键词正文。" in allowed.text
    headers["Authorization"] = "Bearer " + public["token"]
    denied = owner_api.post("/mcp", headers=headers, json={**request, "id": 3})
    assert "私域独有关键词正文。" not in denied.text
    assert denied.status_code == 403 or denied.json()["result"].get("isError")
    auth.revoke_credential(private["id"])
    headers["Authorization"] = "Bearer " + private["token"]
    assert owner_api.post("/mcp", headers=headers, json={**request, "id": 4}).status_code == 401
