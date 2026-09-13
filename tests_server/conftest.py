"""Optional server tests: run with chinalaw[dev,server] installed."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from chinalaw import loader, normsources
from chinalaw.db import connect, migrate
from chinalaw.server.app import create_app
from chinalaw.server.auth_store import AuthStore
from chinalaw.server.config import ServerConfig

PASSWORD = "synthetic-test-password-123"


@pytest.fixture
def api(tmp_path):
    db = tmp_path / "library.db"
    with connect(db) as conn:
        migrate(conn)
        loader.load_law_from_dict(
            conn,
            {
                "id": "public-test",
                "title": "公开查询测试资料",
                "level": "other",
                "status": "unknown",
                "source_url": "https://example.com/synthetic",
                "source_name": "synthetic-test",
                "articles": [{"number": "1", "text": "公开全文测试。" * 30}],
            },
        )
        normsources.import_source_from_dict(
            conn,
            {
                "id": "private-test",
                "name": "私域查询测试",
                "source_type": "other",
                "clauses": [{"number": "1", "text": "私域独有关键词正文。"}],
            },
        )
    config = ServerConfig(db, tmp_path / "state", start_worker=False)
    auth = AuthStore(config.auth_path, config.resource_url)
    auth.set_password(PASSWORD)
    app = create_app(config, auth_store=auth)
    with TestClient(app, base_url=config.origin) as client:
        yield client


@pytest.fixture
def owner_api(api):
    result = api.post(
        "/api/v1/auth/login",
        json={"password": PASSWORD},
        headers={"Origin": api.app.state.config.origin},
    )
    assert result.status_code == 200, result.text
    api.headers.update(
        {"Origin": api.app.state.config.origin, "X-CSRF-Token": result.json()["csrf_token"]}
    )
    return api
