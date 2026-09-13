from __future__ import annotations

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from chinalaw.server.app import create_app
from chinalaw.server.auth_store import AuthStore
from chinalaw.server.config import ServerConfig


def test_remote_mode_requires_https_password_and_secure_cookie(api, tmp_path):
    with pytest.raises(ValueError, match="HTTPS"):
        ServerConfig(
            api.app.state.config.db_path, tmp_path / "remote", host="0.0.0.0", local_mode=False
        )
    config = replace(
        api.app.state.config,
        host="0.0.0.0",
        local_mode=False,
        state_dir=tmp_path / "remote",
        public_url="https://library.example",
    )
    auth = AuthStore(config.auth_path, config.resource_url)
    with pytest.raises(ValueError, match="owner password"):
        create_app(config, auth_store=auth)
    auth.set_password("synthetic-server-mode-password")
    with TestClient(create_app(config, auth_store=auth), base_url=config.origin) as remote:
        login = remote.post(
            "/api/v1/auth/login",
            json={"password": "synthetic-server-mode-password"},
            headers={"Origin": config.origin},
        )
        assert login.status_code == 200
        assert "Secure" in login.headers["set-cookie"]
        assert "HttpOnly" in login.headers["set-cookie"]
        assert remote.get("/api/v1/documents").status_code == 200
        assert (
            remote.post(
                "/api/v1/auth/pair", json={"token": "unused"}, headers={"Origin": config.origin}
            ).status_code
            == 403
        )


def test_authentication_storage_cannot_overlap_library(api):
    db = api.app.state.config.db_path.with_name("auth.db")
    with pytest.raises(ValueError, match="different files"):
        ServerConfig(db, db.parent)
