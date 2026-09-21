"""Owner workflows through the actual ASGI routes and managed worker."""

from __future__ import annotations

import io
import json

from chinalaw.admin import catalog, jobs
from chinalaw.server.auth_store import PRIVATE_SCOPE, PUBLIC_SCOPE


def prepare_upload(client, *, kind="norm", text="第一章 测试\n第一条 " + "完整预览测试。" * 50):
    response = client.post(
        "/api/v1/uploads", files={"file": ("synthetic.txt", text.encode(), "text/plain")}
    )
    assert response.status_code == 201, response.text
    artifact = response.json()
    config = client.app.state.config
    worker = jobs.JobWorker(config.db_path, config.artifacts_dir, gate=client.app.state.gate)
    worker.start()
    client.app.state.worker = worker
    try:
        submitted = client.post(
            "/api/v1/jobs",
            json={
                "action": "import",
                "arguments": {
                    "artifact_id": artifact["id"],
                    "kind": kind,
                    "metadata": {
                        "id": "managed-test",
                        "name": "虚构制度测试",
                        "source_type": "other",
                    },
                },
            },
        )
        assert submitted.status_code == 202, submitted.text
        # Use notification-driven worker completion rather than arbitrary sleeps.
        import time

        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            result = client.get("/api/v1/jobs/" + submitted.json()["id"]).json()
            if result["state"] not in {"queued", "running"}:
                break
            worker.wake.wait(0.01)
        assert result["state"] == "awaiting_confirmation", result
        return artifact, client.get("/api/v1/drafts/" + result["draft_id"]).json()
    finally:
        worker.stop()


def test_upload_preview_commit_source_and_review(owner_api):
    artifact, draft = prepare_upload(owner_api)
    assert len(draft["document"]["clauses"][0]["text"]) > 120
    before = owner_api.get("/api/v1/document?kind=norm&id=managed-test")
    assert before.status_code == 404
    original = owner_api.get("/api/v1/artifacts/" + artifact["id"])
    assert original.status_code == 200
    assert b"attachment" in original.headers["content-disposition"].encode()
    committed = owner_api.post(
        "/api/v1/drafts/" + draft["id"] + "/commit", json={"fingerprint": draft["fingerprint"]}
    )
    assert committed.status_code == 200, committed.text
    assert owner_api.post(
        "/api/v1/drafts/" + draft["id"] + "/commit", json={"fingerprint": draft["fingerprint"]}
    ).json()["repeated"]
    document = owner_api.get("/api/v1/document?kind=norm&id=managed-test").json()
    assert document["document"]["clauses"][0]["text"] == draft["document"]["clauses"][0]["text"]
    review = owner_api.post(
        "/api/v1/reviews",
        json={
            "kind": "norm",
            "id": "managed-test",
            "fingerprint": document["fingerprint"],
            "note": "人工核对测试",
        },
    )
    assert review.status_code == 200
    revisions = owner_api.get("/api/v1/revisions?kind=norm&id=managed-test").json()
    revision = owner_api.get("/api/v1/revision?kind=norm&id=managed-test&revision=1").json()
    assert revisions["revision_count"] == 1
    assert revision["document"]["clauses"][0]["text"] == draft["document"]["clauses"][0]["text"]
    assert (
        owner_api.post(
            "/api/v1/revisions/restore",
            json={"kind": "norm", "id": "managed-test", "revision": "1"},
        ).status_code
        == 201
    )


def test_backup_http_round_trip_keeps_owner_credentials_separate(owner_api):
    artifact, draft = prepare_upload(owner_api)
    owner_api.post(
        "/api/v1/drafts/" + draft["id"] + "/commit", json={"fingerprint": draft["fingerprint"]}
    )
    backup = owner_api.post("/api/v1/backups")
    assert backup.status_code == 200, backup.text
    assert backup.headers["content-type"] == "application/zip"
    import zipfile

    with zipfile.ZipFile(io.BytesIO(backup.content)) as archive:
        assert not any("auth" in name for name in archive.namelist())
        assert json.loads(archive.read("manifest.json"))["format_version"] == 1
    prepared = owner_api.post(
        "/api/v1/backups/restore/preview",
        files={"file": ("backup.zip", backup.content, "application/zip")},
    )
    assert prepared.status_code == 201, prepared.text
    preview = prepared.json()
    assert preview["artifact_count"] == 2
    restored = owner_api.post(
        f"/api/v1/backups/restore/{preview['id']}/commit",
        json={"fingerprint": preview["fingerprint"]},
    )
    assert restored.status_code == 200, restored.text
    assert owner_api.get("/api/v1/auth/session").json()["authenticated"]
    assert owner_api.get("/api/v1/artifacts/" + artifact["id"]).status_code == 200


def test_query_credentials_cannot_upload_commit_or_restore(owner_api):
    auth = owner_api.app.state.auth
    for scopes in ([PUBLIC_SCOPE], [PUBLIC_SCOPE, PRIVATE_SCOPE]):
        token = auth.issue_query_token("not-owner", list(scopes))["token"]
        headers = {"Authorization": "Bearer " + token}
        for path, method, data in (
            ("/api/v1/backups", "POST", {}),
            ("/api/v1/drafts", "POST", {"kind": "law", "payload": {}}),
            (
                "/api/v1/reviews",
                "POST",
                {"kind": "law", "id": "public-test", "fingerprint": "a" * 64},
            ),
            ("/api/v1/jobs", "POST", {"action": "import", "arguments": {}}),
        ):
            assert owner_api.request(method, path, headers=headers, json=data).status_code == 403
        assert (
            owner_api.post(
                "/api/v1/uploads", headers=headers, files={"file": ("test.txt", b"test")}
            ).status_code
            == 403
        )


def test_remote_public_document_omits_human_notes(owner_api):
    data = catalog.get_document(owner_api.app.state.config.db_path, "law", "public-test")
    owner_api.post(
        "/api/v1/reviews",
        json={
            "kind": "law",
            "id": "public-test",
            "fingerprint": data["fingerprint"],
            "note": "owner-only-note",
        },
    )
    token = owner_api.app.state.auth.issue_query_token("public", [PUBLIC_SCOPE])["token"]
    result = owner_api.get(
        "/api/v1/document?kind=law&id=public-test", headers={"Authorization": "Bearer " + token}
    )
    assert result.status_code == 200
    assert "owner-only-note" not in result.text


def test_origin_host_body_limits_and_static_assets(owner_api):
    assert owner_api.get("/api/v1/documents", headers={"Host": "rebind.example"}).status_code == 421
    assert (
        owner_api.get(
            "/api/v1/documents", headers={"Origin": "https://untrusted.example"}
        ).status_code
        == 403
    )
    assert (
        owner_api.post(
            "/api/v1/uploads", headers={"Content-Length": str(40 * 1024 * 1024)}
        ).status_code
        == 413
    )
    assert owner_api.get("/").status_code == 200
    assert "script-src 'self'" in owner_api.get("/").headers["content-security-policy"]


def test_invalid_restore_does_not_change_library(owner_api):
    before = owner_api.get("/api/v1/system").json()
    result = owner_api.post(
        "/api/v1/backups/restore/preview", files={"file": ("broken.zip", b"not a zip")}
    )
    assert result.status_code == 400, result.text
    assert owner_api.get("/api/v1/system").json() == before
