"""Regression tests for data paths, CLI boundaries, and platform scripts."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import stat
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock

from chinalaw import cli, doctor, loader, service
from chinalaw.datapaths import builtin_data_file, builtin_data_search_message

REPO_ROOT = Path(__file__).resolve().parents[1]


def _write_executable(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _run_cli(arguments: list[str]) -> tuple[int, dict]:
    stdout = StringIO()
    with redirect_stdout(stdout):
        code = cli.app(arguments)
    return code, json.loads(stdout.getvalue())


class DataPathBoundaryTests(unittest.TestCase):
    def test_user_scheme_shared_data_is_discovered(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            user_root = root / "userbase"
            system_root = root / "system"
            target = user_root / "chinalaw" / "data" / "user-only.json"
            target.parent.mkdir(parents=True)
            target.write_text("{}", encoding="utf-8")
            preferred = "nt_user" if os.name == "nt" else "posix_user"

            def fake_get_path(_name: str, scheme: str | None = None) -> str:
                return str(user_root if scheme == preferred else system_root)

            with (
                mock.patch("chinalaw.datapaths.sysconfig.get_path", side_effect=fake_get_path),
                mock.patch(
                    "chinalaw.datapaths.sysconfig.get_scheme_names",
                    return_value=(preferred,),
                ),
                mock.patch("chinalaw.datapaths.site.USER_BASE", str(root / "fallback")),
            ):
                resolved = builtin_data_file("user-only.json")
                search_message = builtin_data_search_message("user-only.json")

        self.assertEqual(target, resolved)
        self.assertIn(str(target), search_message)

    def test_empty_load_files_has_no_database_side_effect(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "missing.db"

            result = loader.load_files(db_path, [])

            self.assertEqual(0, result["laws_loaded"])
            self.assertFalse(db_path.exists())


class ReadOnlyHealthTests(unittest.TestCase):
    def _legacy_db(self, directory: str) -> Path:
        db_path = Path(directory) / "legacy.db"
        conn = sqlite3.connect(db_path)
        conn.executescript(
            """
            CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
            INSERT INTO meta(key, value) VALUES ('schema_version', '1');
            CREATE TABLE laws (
                id TEXT PRIMARY KEY,
                title TEXT,
                short_title TEXT,
                status TEXT,
                level TEXT,
                source_checked_at TEXT
            );
            CREATE TABLE articles (law_id TEXT);
            INSERT INTO laws(id, title, short_title, status, level)
            VALUES ('legacy-law', '旧版示例法', '示例法', 'current', 'law');
            """
        )
        conn.commit()
        conn.close()
        return db_path

    def test_status_and_doctor_do_not_migrate_legacy_database(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db_path = self._legacy_db(td)
            before = db_path.read_bytes()

            status = service.status(db_path)
            report = doctor.run_doctor(db_path)

            after = db_path.read_bytes()
            conn = sqlite3.connect(db_path)
            version = conn.execute(
                "SELECT value FROM meta WHERE key = 'schema_version'"
            ).fetchone()[0]
            conn.close()

        self.assertEqual(before, after)
        self.assertEqual("1", version)
        self.assertEqual(1, status["schema_version"])
        self.assertTrue(status["read_only"])
        checks = {item["name"]: item for item in report["checks"]}
        self.assertEqual("fail", checks["schema_version"]["status"])

    def test_cli_status_does_not_create_missing_database(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "missing.db"

            code, payload = _run_cli(
                ["--no-notice", "--db", str(db_path), "status", "--format", "json"]
            )

            self.assertEqual(2, code)
            self.assertEqual("cli_command_error", payload["kind"])
            self.assertEqual("FileNotFoundError", payload["error"])
            self.assertFalse(db_path.exists())


class CliBoundaryTests(unittest.TestCase):
    def test_cli_honors_chinalaw_db_environment(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "from-env.db"
            with mock.patch.dict(os.environ, {"CHINALAW_DB": str(db_path)}):
                code, payload = _run_cli(["--no-notice", "doctor", "--format", "json"])

        self.assertEqual(0, code)
        self.assertEqual(str(db_path), payload["db_path"])

    def test_init_fails_when_no_bundled_law_or_article_was_loaded(self) -> None:
        doctor_report = {
            "kind": "doctor_report",
            "ok": True,
            "error_count": 0,
            "warning_count": 0,
            "checks": [],
        }
        with (
            tempfile.TemporaryDirectory() as td,
            mock.patch(
                "chinalaw.cli.loader.load_fixtures",
                return_value={
                    "laws_loaded": 0,
                    "articles_loaded": 0,
                    "titles": [],
                    "note": "fixtures missing; searched bundled data paths: example",
                },
            ),
            mock.patch("chinalaw.cli.doctor.run_doctor", return_value=doctor_report),
        ):
            code, payload = _run_cli(
                [
                    "--no-notice",
                    "--db",
                    str(Path(td) / "init.db"),
                    "init",
                    "--format",
                    "json",
                ]
            )

        self.assertEqual(1, code)
        self.assertFalse(payload["ok"])
        self.assertEqual("bundled_data_unavailable", payload["error"]["code"])

    def test_sync_from_empty_directory_fails_without_touching_database(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            directory = Path(td) / "empty"
            directory.mkdir()
            db_path = Path(td) / "sync.db"

            code, payload = _run_cli(
                [
                    "--no-notice",
                    "--db",
                    str(db_path),
                    "sync",
                    "--from-dir",
                    str(directory),
                    "--format",
                    "json",
                ]
            )

            self.assertEqual(2, code)
            self.assertEqual("cli_command_error", payload["kind"])
            self.assertIn("contains no JSON files", payload["message"])
            self.assertFalse(db_path.exists())

    def test_missing_file_uses_machine_readable_cli_error(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            missing = Path(td) / "missing.md"
            code, payload = _run_cli(
                [
                    "--no-notice",
                    "audit",
                    "file",
                    str(missing),
                    "--format",
                    "json",
                ]
            )

        self.assertEqual(2, code)
        self.assertEqual("cli_command_error", payload["kind"])
        self.assertEqual("audit", payload["command"])
        self.assertEqual("FileNotFoundError", payload["error"])

    def test_negative_limits_are_rejected_by_shared_parser_type(self) -> None:
        parser = cli.build_parser()
        stderr = StringIO()
        with redirect_stderr(stderr), self.assertRaises(SystemExit) as caught:
            parser.parse_args(["search", "合同", "--limit", "-1"])

        self.assertEqual(2, caught.exception.code)
        self.assertIn("must be >= 1", stderr.getvalue())


class PlatformScriptTests(unittest.TestCase):
    def test_update_local_executes_with_empty_skill_args(self) -> None:
        bash = shutil.which("bash")
        if bash is None:
            self.skipTest("bash is unavailable")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "checkout"
            scripts = root / "scripts"
            bin_dir = Path(td) / "bin"
            scripts.mkdir(parents=True)
            bin_dir.mkdir()
            shutil.copy2(REPO_ROOT / "scripts" / "update-local", scripts / "update-local")
            (scripts / "update-local").chmod(0o755)
            log = Path(td) / "skill-args.txt"
            _write_executable(scripts / "install-local", "#!/usr/bin/env bash\nexit 0\n")
            _write_executable(
                scripts / "install-skills",
                '#!/usr/bin/env bash\nprintf "%s" "$#" > "$TEST_SKILL_LOG"\n',
            )
            _write_executable(
                bin_dir / "git",
                "#!/usr/bin/env bash\n"
                "if [ \"${1:-}\" = rev-parse ]; then exit 1; fi\n"
                "exit 0\n",
            )
            env = os.environ.copy()
            env["PATH"] = os.pathsep.join((str(bin_dir), env.get("PATH", "")))
            env["TEST_SKILL_LOG"] = str(log)

            completed = subprocess.run(
                [bash, str(scripts / "update-local"), "--no-doctor"],
                cwd=root,
                env=env,
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            logged_args = log.read_text(encoding="utf-8") if log.exists() else None

        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertEqual("0", logged_args)

    def test_windows_cmd_shim_delegates_without_embedded_absolute_paths(self) -> None:
        script = (REPO_ROOT / "scripts" / "install-local.ps1").read_text(encoding="utf-8")

        self.assertIn('"%~dp0$Name.ps1" %*', script)
        self.assertNotIn('set "PYTHON=$PythonBin"', script)
        self.assertNotIn('set "PYTHONPATH=$srcPath;%PYTHONPATH%"', script)
        self.assertIn("Set-Content -Path $ps1Path -Value $ps1Content -Encoding UTF8", script)


if __name__ == "__main__":
    unittest.main()
