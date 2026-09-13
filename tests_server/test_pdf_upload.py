"""Exercise the optional system PDF reader with a self-contained synthetic PDF."""

from __future__ import annotations

import shutil

import pytest

from chinalaw.admin import ingest


def synthetic_pdf() -> bytes:
    text = b"Synthetic PDF source text for a private test document. " * 5
    stream = b"BT /F1 8 Tf 20 800 Td (" + text + b") Tj ET"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 2000 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    result = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, 1):
        offsets.append(len(result))
        result.extend(str(number).encode() + b" 0 obj\n" + body + b"\nendobj\n")
    start = len(result)
    result.extend(b"xref\n0 6\n0000000000 65535 f \n")
    for offset in offsets:
        result.extend(f"{offset:010d} 00000 n \n".encode())
    result.extend(f"trailer\n<< /Root 1 0 R /Size 6 >>\nstartxref\n{start}\n%%EOF".encode())
    return bytes(result)


@pytest.mark.skipif(shutil.which("pdftotext") is None, reason="optional Poppler is not installed")
def test_text_pdf_upload_retains_original_and_complete_extracted_text(owner_api):
    uploaded = owner_api.post(
        "/api/v1/uploads", files={"file": ("synthetic.pdf", synthetic_pdf(), "application/pdf")}
    )
    assert uploaded.status_code == 201
    config = owner_api.app.state.config
    draft = ingest.import_artifact(
        config.db_path,
        config.artifacts_dir,
        uploaded.json()["id"],
        kind="norm",
        metadata={"id": "pdf-test", "source_type": "other"},
    )
    assert len(draft["document"]["clauses"][0]["text"]) > 120
    assert "Synthetic PDF source text" in draft["document"]["clauses"][0]["text"]
    assert len(draft["origin"]["artifact_ids"]) == 2
    assert owner_api.get("/api/v1/artifacts/" + uploaded.json()["id"]).content == synthetic_pdf()
