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
    text = f"---\nname: {staged}\ndescription: x\n{extra}---\n{body}"
    (folder / "SKILL.md").write_bytes(text.encode("utf-8"))  # bytes: no platform newline translation


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
    proc = subprocess.run(["node", str(HOOK), "claude"], input=json.dumps(event), text=True, encoding="utf-8", capture_output=True,
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


def _home_fwd(home: Path) -> str:
    return str(home).replace("\\", "/")


def test_ties_are_ordered_by_name(tmp_path: Path) -> None:
    # Inserted in reverse order: identical scores must still come out sorted by name.
    _registry(tmp_path, {"zeta-skill": "skill", "mid-skill": "skill", "alpha-skill": "skill"})
    ctx = _context(_run(tmp_path, FSR_INJECT_BODY="off"))
    assert ctx.index("alpha-skill") < ctx.index("mid-skill") < ctx.index("zeta-skill")


def test_total_output_is_capped_by_inject_limit(tmp_path: Path) -> None:
    _registry(tmp_path, {"k8s-review": "skill", "k8s-audit": "skill"})
    _skill(tmp_path, "k8s-review", "line of text\n" * 600)
    _skill(tmp_path, "k8s-audit", "other line\n" * 600)
    ctx = _context(_run(tmp_path, FSR_INJECT_LIMIT="3000"))
    assert len(ctx.encode("utf-8")) <= 3000
    assert "truncated at 3000 bytes" in ctx
    # Minimum is 1000 even when a smaller value is requested.
    small = _context(_run(tmp_path, {"prompt": PROMPT, "session_id": "sess-2"}, FSR_INJECT_LIMIT="10"))
    assert 500 < len(small.encode("utf-8")) <= 1000
    # Default (12000) is applied when the variable is absent.
    big = _context(_run(tmp_path, {"prompt": PROMPT, "session_id": "sess-3"}, FSR_BODY_LIMIT="8000"))
    assert len(big.encode("utf-8")) <= 12000


def test_inject_limit_cuts_on_utf8_boundary(tmp_path: Path) -> None:
    _registry(tmp_path, {"k8s-review": "skill"})
    _skill(tmp_path, "k8s-review", "è" * 3000)
    ctx = _context(_run(tmp_path, FSR_INJECT_LIMIT="1001"))
    assert "�" not in ctx
    assert len(ctx.encode("utf-8")) <= 1001


def test_normalizes_crlf_posix_paths_and_index_signature(tmp_path: Path) -> None:
    _registry(tmp_path, {"k8s-review": "skill"})
    _skill(tmp_path, "k8s-review", "first\r\nsecond\r\n" + "y" * 3000)
    sig = "ab12cd34ef56" + "0" * 52
    (tmp_path / "index" / "claude" / "index.json").write_text(json.dumps({"signature": sig}), encoding="utf-8")
    ctx = _context(_run(tmp_path, FSR_BODY_LIMIT="1000"))
    assert "\r" not in ctx and "first\nsecond\n" in ctx
    assert "\\" not in ctx
    assert f"files in {_home_fwd(tmp_path)}/sources/claude/k8s-review" in ctx
    assert f"read {_home_fwd(tmp_path)}/sources/claude/k8s-review/SKILL.md" in ctx
    assert ctx.startswith("FastSkillRetrieval [index ab12cd34ef56]:")
    assert sig not in ctx


def test_header_has_no_variable_value_without_index_json(tmp_path: Path) -> None:
    _registry(tmp_path, {"k8s-review": "skill"})
    assert "index " not in _context(_run(tmp_path, FSR_INJECT_BODY="off")).split("\n")[0]


GOLDEN = Path(__file__).parent / "golden" / "prompt_hook" / "expected.json"
GOLDEN_PROMPTS = [
    PROMPT,
    "review the kubernetes ingress manifest",
    "kubernetes ingress staging cluster review manifest",
    "please review the kubernetes ingress manifest for the staging cluster with extra care",
]


def _golden_home(home: Path) -> None:
    _registry(home, {"zeta-cmd": "command", "k8s-review": "skill", "alpha-skill": "skill", "agent--k8s-reviewer": "agent"})
    _skill(home, "alpha-skill", "# Alpha\r\nUse probes.\r\nAvoid latest tags.\r\n")
    _skill(home, "k8s-review", "# K8s review\nCheck limits.\n" + "detail line\n" * 900, "allowed-tools: Read\n")
    _skill(home, "zeta-cmd", "Zeta command body.")
    (home / "index" / "claude" / "index.json").write_text(json.dumps({"signature": "0123456789abcdef" * 4}), encoding="utf-8")


def _golden_run(home: Path, tag: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for i, prompt in enumerate(GOLDEN_PROMPTS):
        res = _run(home, {"prompt": prompt, "session_id": f"{tag}-{i}"})
        out[prompt] = _context(res).replace(_home_fwd(home), "<FSR_HOME>")
    return out


def test_golden_output_is_stable_across_sessions(tmp_path: Path) -> None:
    _golden_home(tmp_path)
    first = _golden_run(tmp_path, "run-a")
    second = _golden_run(tmp_path, "run-b")
    assert first == second
    assert all(first.values())
    if os.environ.get("FSR_UPDATE_GOLDEN") == "1":
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN.write_text(json.dumps(first, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    expected = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert first == expected
