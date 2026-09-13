"""HTTP scope, session, origin and read-only behavior at the real route boundary."""

from __future__ import annotations

import json

from chinalaw.admin.backups import library_fingerprint
from chinalaw.server.auth_store import PRIVATE_SCOPE, PUBLIC_SCOPE


def test_unauthenticated_user_cannot_read_inventory_or_private_counts(api):
    for url in [
        "/api/v1/documents",
        "/api/v1/system",
        "/api/v1/document?kind=norm&id=private-test",
    ]:
        response = api.get(url)
        assert response.status_code == 401
        assert "私域独有关键词" not in response.text
    assert api.get("/healthz").json()["ok"]


def test_owner_sees_full_text_and_private_library(owner_api):
    response = owner_api.get("/api/v1/document", params={"kind": "law", "id": "public-test"})
    assert response.status_code == 200
    assert len(response.json()["document"]["articles"][0]["text"]) > 120
    assert owner_api.get("/api/v1/documents?kind=norm").json()["total"] == 1
    assert owner_api.get("/api/v1/system").json()["norm_sources"] == 1


def test_public_token_does_not_inherit_owner_cookie_or_private_scope(owner_api):
    created = owner_api.post("/api/v1/auth/credentials", json={"name": "public client"})
    assert created.status_code == 200
    token = created.json()["token"]
    headers = {"Authorization": "Bearer " + token}
    session = owner_api.get("/api/v1/auth/session", headers=headers).json()
    assert not session["authenticated"] and session["csrf_token"] is None
    assert owner_api.get("/api/v1/documents", headers=headers).status_code == 200
    for url in [
        "/api/v1/documents?kind=norm",
        "/api/v1/document?kind=norm&id=private-test",
        "/api/v1/revisions?kind=norm&id=private-test",
        "/api/v1/system",
        "/api/v1/auth/credentials",
    ]:
        assert owner_api.get(url, headers=headers).status_code == 403, url
    result = owner_api.get("/api/v1/search?q=私域独有关键词", headers=headers)
    assert result.status_code == 200
    assert "私域独有关键词正文。" not in result.text
    assert (
        owner_api.post(
            "/api/v1/auth/credentials", json={"name": "forbidden"}, headers=headers
        ).status_code
        == 403
    )


def test_private_token_is_explicit_and_revocable(owner_api):
    created = owner_api.post(
        "/api/v1/auth/credentials",
        json={
            "name": "authorized client",
            "scopes": [PUBLIC_SCOPE, PRIVATE_SCOPE],
        },
    ).json()
    headers = {"Authorization": "Bearer " + created["token"]}
    assert owner_api.get("/api/v1/documents?kind=norm", headers=headers).status_code == 200
    listing = owner_api.get("/api/v1/auth/credentials").json()
    assert created["token"] not in json.dumps(listing)
    assert "digest" not in json.dumps(listing)
    assert owner_api.delete("/api/v1/auth/credentials/" + created["id"]).status_code == 200
    assert owner_api.get("/api/v1/documents", headers=headers).status_code == 401


def test_cookie_mutations_require_origin_and_csrf(owner_api):
    response = owner_api.post(
        "/api/v1/auth/credentials", json={"name": "bad csrf"}, headers={"X-CSRF-Token": "wrong"}
    )
    assert response.status_code == 403
    response = owner_api.post(
        "/api/v1/auth/credentials",
        json={"name": "bad origin"},
        headers={"Origin": "https://hostile.example"},
    )
    assert response.status_code == 403
    assert (
        owner_api.get("/api/v1/documents", headers={"Host": "hostile.example"}).status_code == 421
    )


def test_validation_never_echoes_password(api):
    secret = "synthetic-sensitive-input" * 100
    response = api.post(
        "/api/v1/auth/login",
        json={"password": secret},
        headers={"Origin": api.app.state.config.origin},
    )
    assert response.status_code == 422
    assert secret not in response.text
    assert "synthetic-sensitive-input" not in response.text


def test_pairing_link_is_single_use(api):
    token = api.app.state.auth.create_pairing()
    headers = {"Origin": api.app.state.config.origin}
    assert api.post("/api/v1/auth/pair", json={"token": token}, headers=headers).status_code == 200
    assert api.post("/api/v1/auth/pair", json={"token": token}, headers=headers).status_code == 401


def test_password_change_invalidates_unconsumed_pairing_link(api):
    token = api.app.state.auth.create_pairing()
    api.app.state.auth.set_password("different-synthetic-password")
    response = api.post(
        "/api/v1/auth/pair", json={"token": token}, headers={"Origin": api.app.state.config.origin}
    )
    assert response.status_code == 401


def test_wrong_resource_or_subject_token_is_rejected(owner_api):
    for column, value in (("resource", "https://wrong.example/mcp"), ("subject", "another-owner")):
        issued = owner_api.app.state.auth.issue_query_token("wrong binding", [PUBLIC_SCOPE])
        with owner_api.app.state.auth.transaction() as conn:
            conn.execute(f"UPDATE credentials SET {column} = ? WHERE id = ?", (value, issued["id"]))
        assert (
            owner_api.get(
                "/api/v1/documents", headers={"Authorization": "Bearer " + issued["token"]}
            ).status_code
            == 401
        )


def test_queries_do_not_change_portable_database(owner_api):
    db = owner_api.app.state.config.db_path
    before = library_fingerprint(db)
    assert owner_api.get("/api/v1/documents").status_code == 200
    assert owner_api.get("/api/v1/search?q=公开").status_code == 200
    assert owner_api.get("/api/v1/document?kind=law&id=public-test").status_code == 200
    assert library_fingerprint(db) == before


def test_password_and_query_token_are_not_stored_in_plaintext(owner_api):
    result = owner_api.post("/api/v1/auth/credentials", json={"name": "storage test"}).json()
    raw = owner_api.app.state.config.auth_path.read_bytes()
    assert result["token"].encode() not in raw
    assert b"synthetic-test-password-123" not in raw
