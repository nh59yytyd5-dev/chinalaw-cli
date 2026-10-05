"""Run the real DSH against a disposable, public-only MCP server in Docker."""

import asyncio
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import httpx
import uvicorn
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from chinalaw.server.app import create_app
from chinalaw.server.auth_store import PUBLIC_SCOPE, AuthStore
from chinalaw.server.config import ServerConfig

OUT = Path("/out")
WORK = Path("/work")


def server():
    config = ServerConfig(WORK / "library.db", OUT / "server-state", start_worker=False)
    uvicorn.run(
        create_app(config), host="127.0.0.1", port=8765, log_level="error", access_log=False
    )


async def replay(token):
    rows = json.loads(Path("/inputs/history.json").read_text())
    async with (
        httpx.AsyncClient(headers={"Authorization": "Bearer " + token}) as http,
        streamable_http_client("http://127.0.0.1:8765/mcp", http_client=http) as transport,
        ClientSession(transport[0], transport[1]) as session,
    ):
        await session.initialize()
        tools = await session.list_tools()
        (OUT / "tools.json").write_text(tools.model_dump_json(indent=2))
        with (OUT / "replay.jsonl").open("w") as log:
            for row in rows:
                started = time.monotonic()
                result = await session.call_tool("chinalaw_" + row["tool"], row["params"])
                log.write(
                    json.dumps(
                        {
                            "id": row["id"],
                            "tool": row["tool"],
                            "params": row["params"],
                            "duration_ms": round((time.monotonic() - started) * 1000),
                            "result": result.model_dump(mode="json"),
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
                log.flush()
    print("Replayed", len(rows), "historical requests through MCP", flush=True)


def profile(token):
    root = OUT / "dsh"
    root.mkdir(mode=0o700, exist_ok=True)
    os.environ["DSH_HOME"] = str(root)
    os.environ["HOME"] = str(WORK)
    disabled = [
        "tool-bash",
        "tool-pwsh",
        "tool-fs",
        "tool-fs-search",
        "tool-str-replace-editor",
        "tool-web",
        "tool-subagent",
        "tool-subagent-fork",
        "tool-subagent-control",
        "tool-subagent-list-agents",
        "tool-subagent-report",
        "tool-workflow",
        "tool-ralph",
        "tool-goal",
        "tool-jobs",
        "tool-skill",
        "session-title-llm",
        # Head/tail clipping can join fields from separate JSON documents and
        # accidentally leave parseable but false provenance. Retain complete
        # tool results; the normal context-window compactor remains enabled.
        "spill-policy",
        "tool-result-pruner",
    ]
    patches = [{"id": name, "disabled": True} for name in disabled]
    patches.extend(
        [
            {"id": "credentials", "config": {"path": "/run/secrets/deepseek.yaml", "watch": False}},
            {
                "id": "agent-default-model",
                "config": {
                    "provider": "deepseek-official",
                    "model": os.environ.get("EVAL_MODEL", "deepseek-v4-pro"),
                },
            },
            {
                "id": "llm-deepseek",
                "config": {
                    "apiKeyEnv": "DEEPSEEK_API_KEY",
                    "maxTokens": 16384,
                    "reasoningEffort": "high",
                },
            },
            {
                "insert": [
                    {
                        "id": "mcp-chinalaw",
                        "name": "@deepseek-ai/dsh-mcp-client",
                        "config": {
                            "serverName": "chinalaw",
                            "transport": "streamable-http",
                            "url": "http://127.0.0.1:8765/mcp",
                            "headers": {"Authorization": "Bearer " + token},
                            "failOnStartupError": True,
                        },
                    }
                ]
            },
        ]
    )
    # JSON is valid YAML. Only a disposable local MCP credential appears here.
    (root / "cordis.patch.yml").write_text(json.dumps(patches))
    (root / "cordis.patch.yml").chmod(0o600)
    packages = "/usr/local/lib/node_modules/@deepseek-ai/dsh"
    versions = {
        "harness": json.loads(Path(packages, "package.json").read_text())["version"],
        **{
            name: json.loads(
                Path(packages, "node_modules/@deepseek-ai", name, "package.json").read_text()
            )["version"]
            for name in ("cordis-plugin-hmr", "cordis-plugin-loader")
        },
    }
    (OUT / "profile-facts.json").write_text(
        json.dumps(
            {
                **versions,
                "spill_policy": "disabled",
                "tool_result_pruner": "disabled",
                "tools": "MCP plus planning",
                "production_credentials": False,
            },
            indent=2,
        )
    )


def main():
    OUT.mkdir(exist_ok=True)
    shutil.copyfile("/inputs/library.db", WORK / "library.db")
    config = ServerConfig(WORK / "library.db", OUT / "server-state", start_worker=False)
    auth = AuthStore(config.auth_path, config.resource_url)
    token = auth.issue_query_token("isolated-eval", [PUBLIC_SCOPE])["token"]
    process = subprocess.Popen([sys.executable, __file__, "serve"])
    try:
        for _ in range(100):
            if process.poll() is not None:
                raise RuntimeError("isolated server exited")
            try:
                if httpx.get(config.origin + "/healthz", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.1)
        else:
            raise RuntimeError("isolated server did not become ready")
        if sys.argv[1] == "replay":
            asyncio.run(replay(token))
            return
        profile(token)
        prompt = Path("/inputs/prompt.txt").read_text()
        with (OUT / "answer.txt").open("w") as output, (OUT / "dsh-stderr.log").open("w") as err:
            result = subprocess.run(
                [
                    "node",
                    "--expose-internals",
                    "/usr/local/lib/node_modules/@deepseek-ai/dsh/lib/bin.js",
                    "--profile",
                    "headless",
                    prompt,
                ],
                stdout=output,
                stderr=err,
                timeout=int(os.environ.get("EVAL_TIMEOUT", "900")),
            )
        (OUT / "exit.json").write_text(json.dumps({"exit_code": result.returncode}))
        raise SystemExit(result.returncode)
    finally:
        process.terminate()
        process.wait(timeout=10)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "serve":
        server()
    else:
        main()
