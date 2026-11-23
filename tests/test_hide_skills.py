"""hide-skills: plugin SKILL.md files get disable-model-invocation, and restore is byte-exact."""
from __future__ import annotations

import json
from pathlib import Path

from fastskillretrieval.hide_skills import hide, restore

FILES = {
    "plain": "---\nname: plain\ndescription: d\n---\nBody\n",
    "crlf": "---\r\nname: crlf\r\ndescription: d\r\n---\r\nBody\r\n",
    "false": "---\nname: f\ndisable-model-invocation: false\n---\nBody\n",
    "done": "---\nname: done\ndisable-model-invocation: true\n---\nBody\n",
    "bare": "No frontmatter\n",
}


def _setup(tmp_path: Path):
    home, env = tmp_path / "home", {"FSR_HOME": str(tmp_path / "fsr")}
    install = tmp_path / "plugin"
    for name, text in FILES.items():
        directory = install / "skills" / name
        directory.mkdir(parents=True)
        (directory / "SKILL.md").write_bytes(text.encode())
    config = home / ".claude" / "plugins"
    config.mkdir(parents=True)
    (config / "installed_plugins.json").write_text(
        json.dumps({"plugins": {"p@m": [{"installPath": str(install)}]}}), encoding="utf-8")
    return home, env, install


def test_hide_then_restore_is_exact_and_idempotent(tmp_path: Path) -> None:
    home, env, install = _setup(tmp_path)
    assert hide(home, env)["hidden"] == 3
    assert hide(home, env)["hidden"] == 0
    for name in ("plain", "crlf", "false", "done"):
        assert "disable-model-invocation: true" in (install / "skills" / name / "SKILL.md").read_text()
    assert (install / "skills" / "crlf" / "SKILL.md").read_bytes().count(b"\r\n") == 6
    assert (install / "skills" / "bare" / "SKILL.md").read_text() == FILES["bare"]
    assert restore(home, env)["restored"] == 3
    for name, text in FILES.items():
        assert (install / "skills" / name / "SKILL.md").read_bytes() == text.encode()
