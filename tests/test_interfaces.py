import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_cli_actual_commands_and_exit_codes(tmp_path):
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    entry = [sys.executable]
    if os.environ.get("FSR_COVERAGE") == "1":
        entry.extend(["-m", "coverage", "run", "--parallel-mode"])
    base = [
        *entry,
        "-m",
        "fastskillretrieval",
        "--state",
        str(tmp_path / "state"),
        "--source-root",
        str(ROOT / "examples/skills"),
    ]

    def run(*arguments):
        result = subprocess.run(
            [*base, *arguments],
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=20,
        )
        return result.returncode, json.loads(result.stdout)

    assert run("search", "markdown")[0] == 2
    code, inventory = run("inventory", "--refresh")
    assert code == 0
    item = inventory["items"][0]
    assert (
        run("submit", item["id"], item["version"], str(ROOT / "examples/markdown-candidate.json"))[
            0
        ]
        == 0
    )
    assert run("publish")[0] == 0
    code, found = run("search", "Markdown")
    assert code == 0 and found["status"] == "ok"
    reference = json.dumps(found["candidates"][0]["reference"])
    assert run("load", reference)[1]["status"] == "ok"
    assert (
        "FSR-MARKDOWN-CHECK-01"
        in run("resource", reference, "references/checklist.md")[1]["content"]
    )
    assert run("load", "not-json")[1]["status"] == "policy_denied"
    assert run("search", "unique-no-match")[1]["status"] == "no_match"


def test_actual_stdio_mcp_server():
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/verify_mcp.py")],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["status"] == "ok"
    assert report["transport"] == "stdio"
    assert len(report["checks"]) >= 10
