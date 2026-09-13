"""Owner login, local pairing, read-only credentials and OAuth consent."""

from __future__ import annotations

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field

from chinalaw.admin.errors import LibraryError
from chinalaw.server.auth_store import PUBLIC_SCOPE, SESSION_SECONDS
from chinalaw.server.dependencies import SESSION_COOKIE, owner, same_origin

router = APIRouter(prefix="/api/v1/auth", tags=["owner authentication"])


class Login(BaseModel):
    password: str = Field(min_length=1, max_length=1024)


class Pairing(BaseModel):
    token: str = Field(min_length=1, max_length=256)


class NewCredential(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    scopes: list[str] = Field(default_factory=lambda: [PUBLIC_SCOPE], max_length=2)
    days: int = Field(default=90, ge=1, le=365)


class Consent(BaseModel):
    approved: bool
    scopes: list[str] = Field(default_factory=lambda: [PUBLIC_SCOPE], max_length=2)


def _set_session(response: Response, request: Request, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=SESSION_SECONDS,
        httponly=True,
        secure=request.app.state.config.cookie_secure,
        samesite="strict",
        path="/",
    )


@router.get("/session")
def session_info(request: Request) -> dict:
    value = (
        None
        if request.headers.get("authorization")
        else request.app.state.auth.session(request.cookies.get(SESSION_COOKIE))
    )
    return {
        "authenticated": value is not None,
        "csrf_token": value.csrf if value else None,
        "local_mode": request.app.state.config.local_mode,
        "password_configured": request.app.state.auth.has_password(),
    }


@router.post("/login")
def login(body: Login, request: Request, response: Response) -> dict:
    same_origin(request)
    address = request.client.host if request.client else "unknown"
    token, csrf = request.app.state.auth.login(body.password, address)
    _set_session(response, request, token)
    return {"authenticated": True, "csrf_token": csrf}


@router.post("/pair")
def pair(body: Pairing, request: Request, response: Response) -> dict:
    same_origin(request)
    if not request.app.state.config.local_mode:
        raise LibraryError("pairing_disabled", "服务器模式不允许本地配对。", status=403)
    token, csrf = request.app.state.auth.consume_pairing(body.token)
    _set_session(response, request, token)
    return {"authenticated": True, "csrf_token": csrf}


@router.post("/logout")
def logout(request: Request, response: Response) -> dict:
    owner(request)
    request.app.state.auth.logout(request.cookies.get(SESSION_COOKIE))
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"authenticated": False}


@router.post("/password")
def password(body: Login, request: Request, response: Response) -> dict:
    owner(request)
    request.app.state.auth.set_password(body.password)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"authenticated": False, "password_configured": True}


@router.get("/credentials")
def credentials(request: Request) -> dict:
    owner(request)
    return {
        "items": request.app.state.auth.list_credentials(),
        "mcp_url": request.app.state.config.resource_url,
    }


@router.post("/credentials")
def new_credential(body: NewCredential, request: Request) -> dict:
    owner(request)
    return request.app.state.auth.issue_query_token(body.name, body.scopes, days=body.days)


@router.delete("/credentials/{identifier}")
def revoke_credential(identifier: str, request: Request) -> dict:
    owner(request)
    request.app.state.auth.revoke_credential(identifier)
    return {"revoked": True}


@router.get("/consent/{request_id}")
def consent_details(request_id: str, request: Request) -> dict:
    owner(request)
    return request.app.state.oauth.consent_details(request_id)


@router.post("/consent/{request_id}")
def consent(body: Consent, request_id: str, request: Request) -> dict:
    owner(request)
    redirect = request.app.state.oauth.complete_consent(
        request_id,
        approved=body.approved,
        scopes=body.scopes,
    )
    return {"redirect_url": redirect}
