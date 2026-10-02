import json
import tempfile
import unittest
from pathlib import Path

from chinalaw.remote_check import CheckError, failure, settings


class RemoteCheckTests(unittest.TestCase):
    def test_credentials_are_parsed_without_shell_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "remote.env"
            path.write_text(
                'CHINALAW_REMOTE_URL=https://example.com\nCHINALAW_QUERY_TOKEN="$(not-a-command)"\n'
            )
            url, token = settings(path, {})
            self.assertEqual(url, "https://example.com/mcp")
            self.assertEqual(token, "$(not-a-command)")
            self.assertEqual(settings(path, {"CHINALAW_QUERY_TOKEN": "override"})[1], "override")

    def test_missing_credentials_and_unknown_errors_are_explicit(self):
        with tempfile.TemporaryDirectory() as directory, self.assertRaises(CheckError) as caught:
            settings(Path(directory) / "missing", {})
        self.assertEqual(failure(caught.exception)["error"], "credentials_missing")
        result = failure(RuntimeError("secret-token"))
        self.assertFalse(result["ok"])
        self.assertNotIn("secret-token", json.dumps(result))
