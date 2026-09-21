from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from chinalaw.db import connect, current_version
from chinalaw.schema import SCHEMA_VERSION
from chinalaw.server.cli import initialize


class ServerCLITests(unittest.TestCase):
    def test_missing_library_serve_fails_without_creating_it(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "missing.db"
            process = subprocess.run(
                [sys.executable, "-m", "chinalaw.server", "serve", "--db", str(db)],
                text=True,
                capture_output=True,
                timeout=15,
            )
            self.assertEqual(process.returncode, 1)
            self.assertFalse(db.exists())

    def test_explicit_init_returns_json_and_preserves_upgrade_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "library.db"
            process = subprocess.run(
                [sys.executable, "-m", "chinalaw.server", "init", "--db", str(db), "--json"],
                text=True,
                capture_output=True,
                timeout=15,
            )
            self.assertEqual(process.returncode, 0, process.stderr)
            self.assertEqual(json.loads(process.stdout)["schema_version"], SCHEMA_VERSION)
            # The summary is UTF-8 regardless of the console code page (Windows CI
            # runs with cp1252), so decode explicitly instead of via locale.
            process = subprocess.run(
                [sys.executable, "-m", "chinalaw.server", "init", "--db", str(db)],
                capture_output=True,
                timeout=15,
            )
            self.assertEqual(process.returncode, 0, process.stderr)
            summary = process.stdout.decode("utf-8", errors="replace")
            self.assertIn("资料库已就绪", summary)
            self.assertIn("chinalaw-server serve", summary)
            with connect(db) as conn:
                conn.execute("UPDATE meta SET value = '13' WHERE key = 'schema_version'")
            result = initialize(db)
            self.assertTrue(Path(result["upgrade_backup"]).is_file())
            with connect(db) as conn:
                self.assertEqual(current_version(conn), SCHEMA_VERSION)

    def test_invalid_environment_port_has_argument_error(self):
        process = subprocess.run(
            [sys.executable, "-m", "chinalaw.server", "serve"],
            env={**os.environ, "CHINALAW_PORT": "invalid"},
            text=True,
            capture_output=True,
            timeout=15,
        )
        self.assertEqual(process.returncode, 2)
        self.assertNotIn("Traceback", process.stderr)
