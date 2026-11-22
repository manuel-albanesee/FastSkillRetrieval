import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_registry_cli_vertical_path(tmp_path):
    entry = ["-m", "fastskillretrieval"]
    if os.environ.get("FSR_COVERAGE") == "1":
        entry = ["-m", "coverage", "run", "--parallel-mode", *entry]
    base = [
        sys.executable,
        *entry,
        "--registry",
        "--state",
        str(tmp_path / "state"),
        "--source-root",
        str(ROOT / "examples/skills"),
    ]

    def run(*args):
        completed = subprocess.run(
            [*base, *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
            timeout=30,
        )
        return completed.returncode, json.loads(completed.stdout)

    code, discovered = run("discover")
    assert code == 0, discovered
    item = discovered["items"][0]
    assert (
        run(
            "submit",
            item["id"],
            item["source_revision"],
            str(ROOT / "examples/markdown-candidate.json"),
        )[0]
        == 0
    )
    assert run("publish")[0] == 0
    code, found = run("search", "Markdown", "--request-id", "cli-search")
    assert code == 0, found
    reference = json.dumps(found["candidates"][0]["reference"])
    assert run("plan", reference)[1]["actions"][0]["action"] == "load"
    assert run("load", reference)[1]["status"] == "ok"
    assert (
        "FSR-MARKDOWN-CHECK-01"
        in run("resource", reference, "references/checklist.md")[1]["content"]
    )


def test_registry_legacy_facade_isolated_skill_contract(tmp_path):
    from fastskillretrieval.legacy import LegacyFacade
    from fastskillretrieval.registry import Registry

    registry = Registry(tmp_path / "state", ROOT / "examples/skills")
    try:
        facade = LegacyFacade(registry)
        item = facade.call("inventory", refresh=True)["items"][0]
        candidate = json.loads((ROOT / "examples/markdown-candidate.json").read_text())
        assert (
            facade.call(
                "submit", skill_id=item["id"], version=item["version"], candidate=candidate
            )["status"]
            == "ok"
        )
        facade.call("publish")
        result = facade.call("search", query="Markdown")
        reference = result["candidates"][0]["reference"]
        assert set(reference) == {"catalog", "skill_id", "version"}
        assert facade.call("load", reference=reference)["status"] == "ok"
    finally:
        registry.close()
