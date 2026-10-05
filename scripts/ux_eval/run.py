"""Host launcher: explicit read-only inputs, no production credentials or Docker socket."""

import argparse
import hashlib
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path


def balance(secret):
    key = json.loads(secret.read_text())["DEEPSEEK_API_KEY"]
    request = urllib.request.Request(
        "https://api.deepseek.com/user/balance", headers={"Authorization": "Bearer " + key}
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def source_digest(root):
    digest = hashlib.sha256()
    for file in sorted(root.rglob("*")):
        if file.is_file() and "__pycache__" not in file.parts:
            digest.update(str(file.relative_to(root)).encode() + b"\0")
            digest.update(file.read_bytes())
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["replay", "run", "balance"])
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--prompt", type=Path)
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[2] / "src")
    parser.add_argument("--context", default="colima-chinalaw-eval")
    parser.add_argument("--image", default="chinalaw-dsh-eval:20261005-aligned")
    parser.add_argument("--model", default="deepseek-v4-pro")
    parser.add_argument("--max-tokens", type=int, default=16384)
    parser.add_argument("--effort", choices=["off", "high", "max"], default="high")
    parser.add_argument("--timeout", type=int, default=900)
    args = parser.parse_args()
    if not 128 <= args.max_tokens <= 65536:
        parser.error("--max-tokens must be between 128 and 65536")
    inputs = args.inputs.resolve()
    secret = inputs / "deepseek-secret.yaml"
    if args.mode == "balance":
        print(json.dumps(balance(secret)))
        return
    if args.output is None or (args.mode == "run" and args.prompt is None):
        parser.error("--output is required; run also requires --prompt")
    output = args.output.resolve()
    output.mkdir(parents=True, mode=0o700, exist_ok=False)
    uid, gid = os.getuid(), os.getgid()
    name = "chinalaw-ux-" + output.name.lower().replace("_", "-")
    command = [
        "docker",
        "--context",
        args.context,
        "run",
        "--rm",
        "--name",
        name,
        "--read-only",
        "--user",
        f"{uid}:{gid}",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges:true",
        "--cpus",
        "2",
        "--memory",
        "2g",
        "--pids-limit",
        "128",
        "--tmpfs",
        "/tmp:rw,nosuid,nodev,size=256m",
        "--tmpfs",
        f"/work:rw,nosuid,nodev,size=1g,uid={uid},gid={gid},mode=0700",
        "-e",
        "PYTHONPATH=/project/src",
        "-e",
        "HOME=/work",
        "-e",
        "EVAL_MODEL=" + args.model,
        "-e",
        f"EVAL_MAX_TOKENS={args.max_tokens}",
        "-e",
        "EVAL_EFFORT=" + args.effort,
        "-e",
        f"EVAL_TIMEOUT={args.timeout}",
    ]

    def mount(source, target, readonly=True):
        command.extend(
            [
                "--mount",
                f"type=bind,source={source},target={target}" + (",readonly" if readonly else ""),
            ]
        )

    mount(args.source.resolve(), "/project/src")
    mount(Path(__file__).resolve().parent, "/runner")
    mount(inputs / "library.db", "/inputs/library.db")
    mount(output, "/out", False)
    if args.mode == "replay":
        mount(inputs / "history-anonymized.json", "/inputs/history.json")
    else:
        mount(secret, "/run/secrets/deepseek.yaml")
        mount(args.prompt.resolve(), "/inputs/prompt.txt")
    command.extend([args.image, "python", "/runner/container.py", args.mode])
    metadata = {
        "model": args.model,
        "mode": args.mode,
        "started": time.time(),
        "source": str(args.source.resolve()),
        "container": name,
        "source_sha256": source_digest(args.source.resolve()),
        "image": args.image,
        "max_tokens": args.max_tokens,
        "reasoning_effort": args.effort,
    }
    if args.mode == "run":
        metadata["balance_before"] = balance(secret)
        if not metadata["balance_before"].get("is_available"):
            metadata.update(exit_code=3, reason="balance_unavailable", ended=time.time())
            (output / "run.json").write_text(json.dumps(metadata, indent=2))
            print(json.dumps(metadata))
            raise SystemExit(3)
    try:
        with (output / "container.log").open("w") as log:
            result = subprocess.run(
                command, stdout=log, stderr=subprocess.STDOUT, timeout=args.timeout + 120
            )
        metadata["exit_code"] = result.returncode
    except subprocess.TimeoutExpired:
        subprocess.run(
            ["docker", "--context", args.context, "stop", "--time", "10", name],
            capture_output=True,
            timeout=30,
        )
        metadata["exit_code"] = 124
    finally:
        metadata["ended"] = time.time()
        if args.mode == "run":
            try:
                metadata["balance_after"] = balance(secret)
            except (OSError, ValueError) as exc:
                metadata["balance_error"] = type(exc).__name__
        (output / "run.json").write_text(json.dumps(metadata, indent=2))
    print(json.dumps(metadata))
    raise SystemExit(metadata["exit_code"])


if __name__ == "__main__":
    main()
