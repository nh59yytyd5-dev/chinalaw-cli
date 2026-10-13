"""Evidence retention must detect corruption and preserve links without following them."""

import importlib.util
import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path


def load_archiver():
    path = Path(__file__).resolve().parents[1] / "scripts/ux_eval/archive_evidence.py"
    spec = importlib.util.spec_from_file_location("ux_evidence_archive", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class EvidenceArchiveTests(unittest.TestCase):
    def setUp(self):
        self.archiver = load_archiver()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.tmp_path = Path(temporary.name)

    def test_archive_preserves_hashes_empty_directories_and_symlinks(self):
        source = self.tmp_path / "source"
        source.mkdir()
        (source / "text.txt").write_text("测试证据", encoding="utf-8")
        (source / "empty").mkdir()
        (source / "link").symlink_to("text.txt")
        destination = self.tmp_path / "archives"
        destination.mkdir()
        item = self.archiver.archive_dataset(source, destination)
        self.assertTrue(item["verified"])
        self.assertEqual(item["files"], 1)
        self.assertEqual(item["source_bytes"], len("测试证据".encode()))
        self.assertEqual((source / "text.txt").read_text(encoding="utf-8"), "测试证据")
        with tarfile.open(destination / item["archive"]) as archive:
            manifest = json.load(archive.extractfile("MANIFEST.json"))
            self.assertTrue(archive.getmember("data/link").issym())
        self.archiver.verify_archive(destination / item["archive"], manifest)

    def test_verification_rejects_tampered_manifest_and_data(self):
        source = self.tmp_path / "source"
        source.mkdir()
        (source / "test.txt").write_bytes(b"original")
        output = self.tmp_path / "archives"
        output.mkdir()
        item = self.archiver.archive_dataset(source, output)
        expected = self.archiver.inventory(source)
        expected[0]["sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "manifest differs"):
            self.archiver.verify_archive(output / item["archive"], expected)
        # An internally consistent manifest must still match the archived bytes.
        damaged = output / "damaged.tar.gz"
        with tarfile.open(damaged, "w:gz") as archive:
            payload = json.dumps(expected).encode()
            header = tarfile.TarInfo("MANIFEST.json")
            header.size = len(payload)
            archive.addfile(header, io.BytesIO(payload))
            archive.add(source / "test.txt", arcname="data/test.txt")
        with self.assertRaisesRegex(ValueError, "content hash differs"):
            self.archiver.verify_archive(damaged, expected)


if __name__ == "__main__":
    unittest.main()
