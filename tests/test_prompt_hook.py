"""Behaviour of the UserPromptSubmit hook asset (fsr-hook.cjs): names plus SKILL.md body injection."""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[1] / "src" / "fastskillretrieval" / "assets" / "fsr-hook.cjs"
PROMPT = "please review the kubernetes ingress manifest for the staging cluster"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is required for the hook")


def _skill(home: Path, staged: str, body: str, extra: str = "") -> None:
    folder = home / "sources" / "claude" / staged
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(f"---\nname: {staged}\ndescription: x\n{extra}---\n{body}", encoding="utf-8")


def _registry(home: Path, entries: dict[str, str]) -> None:
    """entries: staged name -> kind. Filler rows give bm25 enough corpus for a strong score."""
    index = home / "index" / "claude"
    index.mkdir(parents=True)
    db = sqlite3.connect(index / "registry.sqlite3")
    db.executescript(
        "CREATE TABLE capabilities(id TEXT PRIMARY KEY,name TEXT,kind TEXT,status TEXT);"
        "CREATE VIRTUAL TABLE capability_fts USING fts5("
        "capability_id UNINDEXED,description,capabilities,use_when,aliases,technologies);")
    for i in range(300):
        db.execute("INSERT INTO capabilities VALUES(?,?,?,?)", (f"f{i}", f"filler{i}", "skill", "enabled"))
        db.execute("INSERT INTO capability_fts VALUES(?,?,?,?,?,?)", (f"f{i}", f"unrelated topic number{i}", "", "", "", ""))
    for name, kind in entries.items():
        db.execute("INSERT INTO capabilities VALUES(?,?,?,?)", (name, name, kind, "enabled"))
        db.execute("INSERT INTO capability_fts VALUES(?,?,?,?,?,?)",
                   (name, "review kubernetes ingress manifest staging cluster", "kubernetes ingress", "", "", ""))
    db.commit()
    db.close()
    sources = {n: {"kind": k, "name": n.removeprefix("agent--"), "origin": "user"} for n, k in entries.items()}
    (index / "sources.json").write_text(json.dumps(sources), encoding="utf-8")


def _run(home: Path, event: dict | None = None, **env: str) -> dict | None:
    event = {"prompt": PROMPT, "session_id": "sess-1"} if event is None else event
    proc = subprocess.run(["node", str(HOOK), "claude"], input=json.dumps(event), text=True, capture_output=True,
                          env={**os.environ, "FSR_HOME": str(home), **env}, check=True)
    return json.loads(proc.stdout) if proc.stdout.strip() else None


def _context(out: dict | None) -> str:
    assert out is not None
    return out["hookSpecificOutput"]["additionalContext"]


def test_injects_skill_body_without_frontmatter(tmp_path: Path) -> None:
    _registry(tmp_path, {"k8s-review": "skill"})
    _skill(tmp_path, "k8s-review", "# K8s review\nCheck probes and limits.")
    ctx = _context(_run(tmp_path))
    assert "k8s-review (skill)" in ctx
    assert "Check probes and limits." in ctx
    assert "description: x" not in ctx


def test_body_injected_once_per_session(tmp_path: Path) -> None:
    _registry(tmp_path, {"k8s-review": "skill"})
    _skill(tmp_path, "k8s-review", "Check probes and limits.")
    assert "Check probes and limits." in _context(_run(tmp_path))
    second = _context(_run(tmp_path))
    assert "k8s-review (skill)" in second and "Check probes and limits." not in second
    other_session = _context(_run(tmp_path, {"prompt": PROMPT, "session_id": "sess-2"}))
    assert "Check probes and limits." in other_session


def test_long_body_is_truncated_with_pointer(tmp_path: Path) -> None:
    _registry(tmp_path, {"k8s-review": "skill"})
    _skill(tmp_path, "k8s-review", "x" * 5000)
    ctx = _context(_run(tmp_path, FSR_BODY_LIMIT="1000"))
    assert "truncated at 1000 bytes" in ctx
    assert "x" * 1001 not in ctx


def test_switch_off_keeps_names_only(tmp_path: Path) -> None:
    _registry(tmp_path, {"k8s-review": "skill"})
    _skill(tmp_path, "k8s-review", "Check probes and limits.")
    ctx = _context(_run(tmp_path, FSR_INJECT_BODY="off"))
    assert "k8s-review (skill)" in ctx and "Check probes and limits." not in ctx


@pytest.mark.parametrize("extra", [
    "allowed-tools: Bash(git status *)\n",
    "allowed-tools:\n  - Read\n  - Grep\n",
    "context: fork\n",
    "model: haiku\n",
])
def test_runtime_frontmatter_skill_is_injected_with_declared_limits(tmp_path: Path, extra: str) -> None:
    _registry(tmp_path, {"k8s-review": "skill"})
    _skill(tmp_path, "k8s-review", "Check probes and limits.", extra)
    ctx = _context(_run(tmp_path))
    assert "Check probes and limits." in ctx
    assert "declared but not enforced: " + extra.split(":")[0] in ctx
    assert "invoke with the Skill tool" not in ctx


def test_agents_stay_names_only(tmp_path: Path) -> None:
    _registry(tmp_path, {"agent--k8s-reviewer": "agent"})
    _skill(tmp_path, "agent--k8s-reviewer", "Agent system prompt text.")
    ctx = _context(_run(tmp_path))
    assert "k8s-reviewer (agent)" in ctx and "Agent system prompt text." not in ctx


def test_missing_skill_file_falls_back_to_names(tmp_path: Path) -> None:
    _registry(tmp_path, {"k8s-review": "skill"})
    assert "k8s-review (skill)" in _context(_run(tmp_path))


def test_unsafe_session_id_does_not_write_outside_home(tmp_path: Path) -> None:
    _registry(tmp_path, {"k8s-review": "skill"})
    _skill(tmp_path, "k8s-review", "Check probes and limits.")
    _run(tmp_path, {"prompt": PROMPT, "session_id": "../../evil"})
    assert not (tmp_path / "sessions").exists()
    assert not (tmp_path.parent / "evil.json").exists()
