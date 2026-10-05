"""Run an explicit finite evaluation queue, stopping on errors or unavailable balance."""

import argparse
import json
import subprocess
import sys
from pathlib import Path

from run import balance


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan", type=Path)
    parser.add_argument("--inputs", type=Path, required=True)
    args = parser.parse_args()
    inputs = args.inputs.resolve()
    for job in json.loads(args.plan.read_text()):
        available = balance(inputs / "deepseek-secret.yaml")
        if not available.get("is_available"):
            print(json.dumps({"stop": "balance_unavailable", "balance": available}), flush=True)
            return
        output = inputs / job["id"]
        if output.exists():
            raise SystemExit(f"Refusing to overwrite {output}")
        print(json.dumps({"starting": job["id"], "balance": available}), flush=True)
        command = [
            sys.executable,
            str(Path(__file__).with_name("run.py")),
            "run",
            "--inputs",
            str(inputs),
            "--output",
            str(output),
            "--source",
            str(Path(job["source"]).resolve()),
            "--prompt",
            str(Path(job["prompt"]).resolve()),
            "--timeout",
            str(job.get("timeout", 900)),
        ]
        result = subprocess.run(command, check=False)
        if result.returncode:
            print(
                json.dumps(
                    {"stop": "run_failed", "job": job["id"], "exit_code": result.returncode}
                ),
                flush=True,
            )
            raise SystemExit(result.returncode)
    print(json.dumps({"stop": "queue_completed"}), flush=True)


if __name__ == "__main__":
    main()
