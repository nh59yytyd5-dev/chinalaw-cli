"""Small interoperability fixes around SDK-provided OAuth endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from mcp.server.auth.middleware.client_auth import AuthenticationError, ClientAuthenticator

router = APIRouter()


@router.post("/revoke", include_in_schema=False)
async def revoke(request: Request):
    """RFC 7009 revocation also works for public PKCE clients without a secret.

    SDK 2.2.0's RevocationRequest makes client_secret required even when the
    registered client uses auth method 'none'. Reuse its client authenticator,
    but validate only the fields the revocation request actually requires.
    """
    provider = request.app.state.oauth
    headers = _cors_headers(request)
    try:
        client = await ClientAuthenticator(provider).authenticate_request(request)
    except AuthenticationError:
        return JSONResponse({"error": "invalid_client"}, status_code=401, headers=headers)
    form = await request.form()
    raw = form.get("token")
    hint = form.get("token_type_hint")
    if not isinstance(raw, str) or not raw or len(raw) > 256:
        return JSONResponse({"error": "invalid_request"}, status_code=400, headers=headers)
    if hint not in {None, "access_token", "refresh_token"}:
        return JSONResponse({"error": "unsupported_token_type"}, status_code=400, headers=headers)
    token = await provider.load_access_token(raw)
    if token is None:
        token = await provider.load_refresh_token(client, raw)
    if token is not None and token.client_id == client.client_id:
        await provider.revoke_token(token)
    return Response(
        status_code=200, headers={**headers, "Cache-Control": "no-store", "Pragma": "no-cache"}
    )


def _cors_headers(request: Request) -> dict[str, str]:
    """Match the SDK's permissive CORS on OAuth endpoints.

    The preflight for this path is answered by the SDK's own ``/revoke`` route
    (mounted after this router); the actual response must carry the same
    allow-origin answer or a browser client cannot read it.
    """
    return {"Access-Control-Allow-Origin": "*"} if request.headers.get("origin") else {}
