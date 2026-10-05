"""Evidence retention must detect corruption and preserve links without following them."""

import importlib.util
import json
import tarfile
from pathlib import Path

import pytest


@pytest.fixture
def archiver():
    path = Path(__file__).resolve().parents[1] / "scripts/ux_eval/archive_evidence.py"
    spec = importlib.util.spec_from_file_location("ux_evidence_archive", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_archive_preserves_hashes_empty_directories_and_symlinks(tmp_path, archiver):
    source = tmp_path / "source"
    source.mkdir()
    (source / "text.txt").write_text("测试证据")
    (source / "empty").mkdir()
    (source / "link").symlink_to("text.txt")
    destination = tmp_path / "archives"
    destination.mkdir()
    item = archiver.archive_dataset(source, destination)
    assert item["verified"]
    assert item["files"] == 1
    assert item["source_bytes"] == len("测试证据".encode())
    assert (source / "text.txt").read_text() == "测试证据"
    with tarfile.open(destination / item["archive"]) as archive:
        manifest = json.load(archive.extractfile("MANIFEST.json"))
        assert archive.getmember("data/link").issym()
    archiver.verify_archive(destination / item["archive"], manifest)


def test_verification_rejects_tampered_manifest_and_data(tmp_path, archiver):
    source = tmp_path / "source"
    source.mkdir()
    (source / "test.txt").write_bytes(b"original")
    output = tmp_path / "archives"
    output.mkdir()
    item = archiver.archive_dataset(source, output)
    expected = archiver.inventory(source)
    expected[0]["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="manifest differs"):
        archiver.verify_archive(output / item["archive"], expected)
    # An internally consistent manifest must still match the archived bytes.
    damaged = output / "damaged.tar.gz"
    import io

    with tarfile.open(damaged, "w:gz") as archive:
        payload = json.dumps(expected).encode()
        header = tarfile.TarInfo("MANIFEST.json")
        header.size = len(payload)
        archive.addfile(header, io.BytesIO(payload))
        archive.add(source / "test.txt", arcname="data/test.txt")
    with pytest.raises(ValueError, match="content hash differs"):
        archiver.verify_archive(damaged, expected)
