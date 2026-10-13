"""An exhausted account must never launch another model container."""

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class UxEvalRunTests(unittest.TestCase):
    def test_unavailable_balance_prevents_container_launch(self):
        path = Path(__file__).resolve().parents[1] / "scripts/ux_eval/run.py"
        spec = importlib.util.spec_from_file_location("ux_eval_run", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as td:
            tmp_path = Path(td)
            source = tmp_path / "source"
            source.mkdir()
            output = tmp_path / "output"
            argv = [
                str(path), "run", "--inputs", str(tmp_path), "--output", str(output),
                "--source", str(source), "--prompt", str(tmp_path / "prompt.txt"),
            ]

            def must_not_launch(*args, **kwargs):
                self.fail("Docker must not be called after unavailable balance")

            with patch.object(module, "balance", lambda _: {"is_available": False}), \
                    patch("sys.argv", argv), \
                    patch.object(module.subprocess, "run", must_not_launch), \
                    self.assertRaises(SystemExit) as stopped:
                module.main()
            self.assertEqual(stopped.exception.code, 3)
            run = json.loads((output / "run.json").read_text(encoding="utf-8"))
            self.assertEqual(run["reason"], "balance_unavailable")


if __name__ == "__main__":
    unittest.main()
