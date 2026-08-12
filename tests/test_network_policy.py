from __future__ import annotations

import io
import subprocess
import unittest
import zipfile

from chinalaw.netio import NetworkPolicyError, SourcePolicy, validate_url
from chinalaw.resource_limits import (
    ResourceLimitError,
    read_limited,
    run_limited,
    validate_zip_archive,
)


class NetworkPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = SourcePolicy(
            source="example",
            allowed_hosts=frozenset({"example.test"}),
            allow_subdomains=True,
            resolve_hosts=False,
        )

    def test_allows_https_allowlisted_host_and_subdomain(self) -> None:
        self.assertEqual(
            validate_url("https://example.test/rules", self.policy),
            "https://example.test/rules",
        )
        self.assertEqual(
            validate_url("https://files.example.test/rule.pdf", self.policy),
            "https://files.example.test/rule.pdf",
        )

    def test_rejects_non_https_userinfo_and_cross_domain(self) -> None:
        for url in (
            "http://example.test/rules",
            "file:///etc/passwd",
            "https://user:secret@example.test/rules",
            "https://evil.test/rules",
        ):
            with self.subTest(url=url), self.assertRaises(NetworkPolicyError):
                validate_url(url, self.policy)

    def test_rejects_local_and_private_literal_targets(self) -> None:
        policies = [
            SourcePolicy(
                source="local",
                allowed_hosts=frozenset({host}),
                resolve_hosts=False,
            )
            for host in ("localhost", "127.0.0.1", "169.254.169.254", "::1")
        ]
        urls = (
            "https://localhost/",
            "https://127.0.0.1/",
            "https://169.254.169.254/latest/meta-data/",
            "https://[::1]/",
        )
        for policy, url in zip(policies, urls, strict=True):
            with self.subTest(url=url), self.assertRaises(NetworkPolicyError):
                validate_url(url, policy)

    def test_rejects_dns_resolution_to_private_address(self) -> None:
        policy = SourcePolicy(
            source="dns-rebind",
            allowed_hosts=frozenset({"example.test"}),
            resolve_hosts=True,
        )

        def resolver(*_args, **_kwargs):
            return [(2, 1, 6, "", ("10.0.0.7", 443))]

        with self.assertRaises(NetworkPolicyError):
            validate_url("https://example.test/", policy, resolver=resolver)


class ResourceLimitTests(unittest.TestCase):
    def test_read_limited_rejects_content_length_before_reading(self) -> None:
        stream = io.BytesIO(b"small")
        with self.assertRaises(ResourceLimitError):
            read_limited(
                stream,
                headers={"Content-Length": "100"},
                max_bytes=10,
            )
        self.assertEqual(stream.tell(), 0)

    def test_read_limited_rejects_streaming_overflow(self) -> None:
        with self.assertRaises(ResourceLimitError):
            read_limited(io.BytesIO(b"01234567890"), max_bytes=10)

    def test_zip_compression_ratio_is_bounded(self) -> None:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("word/document.xml", b"A" * 100_000)
        buffer.seek(0)
        with zipfile.ZipFile(buffer) as archive, self.assertRaises(ResourceLimitError):
            validate_zip_archive(archive, max_ratio=10)

    def test_run_limited_maps_timeout_and_output_overflow(self) -> None:
        def timeout_runner(*_args, **_kwargs):
            raise subprocess.TimeoutExpired(cmd="helper", timeout=1)

        with self.assertRaises(ResourceLimitError):
            run_limited(["helper"], timeout=1, runner=timeout_runner)

        def noisy_runner(*_args, **_kwargs):
            return subprocess.CompletedProcess(
                args=["helper"],
                returncode=0,
                stdout="x" * 11,
                stderr="",
            )

        with self.assertRaises(ResourceLimitError):
            run_limited(["helper"], max_output_bytes=10, runner=noisy_runner)


if __name__ == "__main__":
    unittest.main()
