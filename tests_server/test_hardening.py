"""Regression tests for server hardening: rate limits, gate, exports and OAuth hygiene."""

from __future__ import annotations

import sqlite3
import threading
import time
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from chinalaw.admin import backups, drafts
from chinalaw.admin.errors import LibraryError
from chinalaw.admin.gate import MaintenanceGate
from chinalaw.server import auth_store as auth_module
from chinalaw.server import cli
from chinalaw.server.app import create_app
from chinalaw.server.auth_store import AuthStore
from chinalaw.server.oauth import MAX_PENDING_PER_CLIENT
from tests_server.conftest import PASSWORD
from tests_server.test_oauth_mcp import authorization, consent_code


def test_login_limit_is_per_address_with_backoff(api):
    auth = api.app.state.auth
    with patch.object(auth_module.time, "sleep") as sleep:
        for attempt in range(1, 11):
            with pytest.raises(LibraryError, match="密码不正确"):
                auth.login("wrong-synthetic-password", "203.0.113.10")
            assert sleep.call_args.args[0] == pytest.approx(min(0.25 * attempt, 2.0))
        with pytest.raises(LibraryError, match="登录尝试过多"):
            auth.login(PASSWORD, "203.0.113.10")
        # Another address is unaffected: no global counter can lock the owner out.
        assert auth.login(PASSWORD, "198.51.100.7")
        # 120 failures spread over many addresses exceed the old global cap of 100.
        for index in range(15):
            for _ in range(8):
                with pytest.raises(LibraryError, match="密码不正确"):
                    auth.login("wrong-synthetic-password", f"198.51.100.{20 + index}")
        assert auth.login(PASSWORD, "198.51.100.9")


def test_server_mode_trusts_forwarded_client_only_from_loopback(api, tmp_path):
    config = api.app.state.config
    state = tmp_path / "proxied-state"
    AuthStore(state / "auth.db", "unused").set_password(PASSWORD)
    calls = []
    with patch("uvicorn.run", side_effect=lambda app, **kwargs: calls.append(kwargs)):
        assert (
            cli.main(
                [
                    "serve",
                    "--db",
                    str(config.db_path),
                    "--state-dir",
                    str(state),
                    "--server",
                    "--host",
                    "0.0.0.0",
                    "--public-url",
                    "https://library.example",
                ]
            )
            == 0
        )
        assert cli.main(["serve", "--db", str(config.db_path), "--state-dir", str(state)]) == 0
    assert calls[0]["proxy_headers"] is True
    assert calls[0]["forwarded_allow_ips"] == "127.0.0.1,::1"
    assert calls[1]["proxy_headers"] is False


def test_exclusive_waits_for_activity_to_finish():
    gate = MaintenanceGate()
    release = threading.Event()
    entered = threading.Event()

    def hold():
        with gate.activity():
            entered.set()
            release.wait(5)

    thread = threading.Thread(target=hold)
    thread.start()
    assert entered.wait(5)
    with pytest.raises(LibraryError, match="仍有维护操作"), gate.exclusive(timeout=0.05):
        pass
    # A rejected exclusive attempt must not leave the gate closed.
    with gate.activity():
        pass
    threading.Timer(0.1, release.set).start()
    started = time.monotonic()
    with gate.exclusive(timeout=5):
        assert time.monotonic() - started < 4
        with pytest.raises(LibraryError, match="正在恢复"), gate.activity():
            pass
    thread.join(5)
    with gate.activity():
        pass


def test_read_requests_do_not_occupy_maintenance_gate(owner_api):
    with owner_api.app.state.gate.exclusive():
        assert owner_api.get("/api/v1/jobs").status_code == 200
        assert owner_api.get("/api/v1/drafts").status_code == 200
        assert (
            owner_api.post("/api/v1/jobs", json={"action": "import", "arguments": {}}).status_code
            == 409
        )


def test_export_streams_from_state_dir_and_cleans_up(owner_api):
    config = owner_api.app.state.config
    response = owner_api.post("/api/v1/backups")
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/zip"
    assert "attachment" in response.headers["content-disposition"]
    assert int(response.headers["content-length"]) == len(response.content)
    exports = config.state_dir / "exports"
    assert exports.is_dir() and not any(exports.iterdir())


def test_startup_clears_exports_and_sweeps_restores_and_drafts(api, tmp_path, monkeypatch):
    config = api.app.state.config
    stale = config.state_dir / "exports" / "export-stale"
    stale.mkdir(parents=True)
    (stale / "library.zip").write_bytes(b"stale")
    swept = []
    monkeypatch.setattr(backups, "sweep_restores", swept.append)
    monkeypatch.setattr(drafts, "sweep_drafts", swept.append)
    with TestClient(create_app(config, auth_store=api.app.state.auth), base_url=config.origin):
        pass
    assert not stale.exists()
    assert swept == [config.restores_dir, config.db_path]


def test_unused_dynamic_clients_are_reclaimed_after_a_day(owner_api):
    auth = owner_api.app.state.auth
    old = int(time.time()) - 25 * 3600
    granted_id, _, request_id = authorization(owner_api)
    consent_code(owner_api, request_id)
    unused_id, _, _ = authorization(owner_api)
    with auth.transaction() as conn:
        conn.execute("UPDATE oauth_clients SET created_at = ?", (old,))
        assert conn.execute(
            "SELECT last_grant_at FROM oauth_clients WHERE id = ?", (granted_id,)
        ).fetchone()[0]
    authorization(owner_api)
    with auth.transaction() as conn:
        remaining = {row[0] for row in conn.execute("SELECT id FROM oauth_clients")}
    assert granted_id in remaining
    assert unused_id not in remaining


def test_pending_authorizations_are_limited_per_client(owner_api):
    auth = owner_api.app.state.auth
    client_id, _, _ = authorization(owner_api)
    with auth.transaction() as conn:
        for index in range(MAX_PENDING_PER_CLIENT):
            conn.execute(
                "INSERT INTO oauth_requests(id, client_id, params_json, expires_at) "
                "VALUES (?, ?, '{}', ?)",
                (f"pending-{index}", client_id, int(time.time()) + 600),
            )
    response = owner_api.get(
        "/authorize",
        params={
            "client_id": client_id,
            "response_type": "code",
            "redirect_uri": "http://127.0.0.1:43111/callback",
            "code_challenge": "A" * 43,
            "code_challenge_method": "S256",
        },
        follow_redirects=False,
    )
    assert "temporarily_unavailable" in response.headers.get("location", "") + response.text
    # Other clients keep working.
    authorization(owner_api)


def test_authentication_database_migration_adds_last_grant_column(tmp_path):
    path = tmp_path / "auth.db"
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE oauth_clients (id TEXT PRIMARY KEY, metadata_json TEXT NOT NULL, "
            "created_at INTEGER NOT NULL)"
        )
        conn.execute("INSERT INTO oauth_clients VALUES ('legacy', '{}', 1)")
    store = AuthStore(path, "https://library.example/mcp")
    with store.transaction() as conn:
        assert conn.execute("SELECT last_grant_at FROM oauth_clients").fetchone()[0] is None
    AuthStore(path, "https://library.example/mcp")


def test_password_change_requires_current_password_under_login_limit(owner_api):
    for _ in range(3):
        response = owner_api.post(
            "/api/v1/auth/password",
            json={"password": "replacement-password-1", "current_password": "wrong-guess"},
        )
        assert response.status_code == 401, response.text
        assert response.json()["error"] == "invalid_credentials"
    missing = owner_api.post("/api/v1/auth/password", json={"password": "replacement-password-1"})
    assert missing.status_code == 401
    # Failures share the login counter for this address.
    with owner_api.app.state.auth.transaction() as conn:
        assert conn.execute("SELECT attempts FROM login_attempts").fetchone()[0] == 4
    assert owner_api.get("/api/v1/auth/session").json()["authenticated"]
    assert owner_api.get("/api/v1/auth/session").json()["password_configured"]


def test_first_local_password_may_omit_current_password(api, tmp_path):
    config = api.app.state.config
    auth = AuthStore(tmp_path / "fresh" / "auth.db", config.resource_url)
    with TestClient(create_app(config, auth_store=auth), base_url=config.origin) as fresh:
        token = auth.create_pairing()
        paired = fresh.post(
            "/api/v1/auth/pair", json={"token": token}, headers={"Origin": config.origin}
        )
        fresh.headers.update({"Origin": config.origin, "X-CSRF-Token": paired.json()["csrf_token"]})
        assert fresh.get("/api/v1/auth/session").json()["password_configured"] is False
        created = fresh.post("/api/v1/auth/password", json={"password": "first-local-password"})
        assert created.status_code == 200, created.text
    assert auth.has_password()


def test_revoking_missing_or_revoked_credential_is_404(owner_api):
    created = owner_api.post("/api/v1/auth/credentials", json={"name": "revoke twice"}).json()
    assert owner_api.delete("/api/v1/auth/credentials/" + created["id"]).status_code == 200
    again = owner_api.delete("/api/v1/auth/credentials/" + created["id"])
    assert again.status_code == 404 and again.json()["error"] == "credential_not_found"
    assert owner_api.delete("/api/v1/auth/credentials/does-not-exist").status_code == 404


def test_password_change_invalidates_existing_sessions(owner_api):
    assert owner_api.get("/api/v1/auth/session").json()["authenticated"]
    response = owner_api.post(
        "/api/v1/auth/password",
        json={"password": "replacement-password-1", "current_password": PASSWORD},
    )
    assert response.status_code == 200, response.text
    session = owner_api.get("/api/v1/auth/session").json()
    assert session["authenticated"] is False and session["csrf_token"] is None
    assert owner_api.get("/api/v1/system").status_code == 401
