"""An exhausted account must never launch another model container."""

import importlib.util
import json
from pathlib import Path

import pytest


def test_unavailable_balance_prevents_container_launch(tmp_path, monkeypatch):
    path = Path(__file__).resolve().parents[1] / "scripts/ux_eval/run.py"
    spec = importlib.util.spec_from_file_location("ux_eval_run", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    source = tmp_path / "source"
    source.mkdir()
    output = tmp_path / "output"
    monkeypatch.setattr(module, "balance", lambda _: {"is_available": False})
    monkeypatch.setattr("sys.argv", [
        str(path), "run", "--inputs", str(tmp_path), "--output", str(output),
        "--source", str(source), "--prompt", str(tmp_path / "prompt.txt"),
    ])

    def must_not_launch(*args, **kwargs):
        pytest.fail("Docker must not be called after unavailable balance")

    monkeypatch.setattr(module.subprocess, "run", must_not_launch)
    with pytest.raises(SystemExit) as stopped:
        module.main()
    assert stopped.value.code == 3
    assert json.loads((output / "run.json").read_text())["reason"] == "balance_unavailable"
