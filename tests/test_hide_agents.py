"""hide-agents: plugin agents become Agent(<plugin>:<agent>) deny rules, and restore is byte-exact."""
from __future__ import annotations

import json
from pathlib import Path

from fastskillretrieval.hide_agents import hide, restore


def _setup(tmp_path: Path):
    home, project = tmp_path / "home", tmp_path / "proj"
    project.mkdir()
    for plugin, names in {"ecc": ["zeta", "alpha", "sub/deep"], "other": ["x"]}.items():
        install = tmp_path / plugin
        for name in names:
            file = install / "agents" / f"{name}.md"
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text("---\nname: n\n---\nBody\n", encoding="utf-8")
    config = home / ".claude" / "plugins"
    config.mkdir(parents=True)
    (config / "installed_plugins.json").write_text(json.dumps({"plugins": {
        "ecc@m": [{"installPath": str(tmp_path / "ecc")}],
        "other@m": [{"installPath": str(tmp_path / "other")}],
    }}), encoding="utf-8")
    return home, project, project / ".claude" / "settings.json"


def _deny(file: Path) -> list:
    return json.loads(file.read_text(encoding="utf-8"))["permissions"]["deny"]


def test_c1_hide_writes_sorted_rules_for_selected_plugin_only(tmp_path: Path) -> None:
    home, project, settings = _setup(tmp_path)
    result = hide(["ecc"], settings, home=home, project=project)
    assert _deny(settings) == ["Agent(ecc:alpha)", "Agent(ecc:sub:deep)", "Agent(ecc:zeta)"]
    assert result["status"] == "ok" and result["added"] == 3
    text = settings.read_text(encoding="utf-8")
    assert text.endswith("\n") and '\n  "permissions"' in text


def test_c2_idempotent_keeps_existing_content_and_honours_keep(tmp_path: Path) -> None:
    home, project, settings = _setup(tmp_path)
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps({
        "model": "è", "permissions": {"allow": ["Bash"], "deny": ["Read(x)", "Agent(ecc:zeta)"]},
    }, ensure_ascii=False), encoding="utf-8")
    hide(["ecc"], settings, keep=["ecc:sub:deep"], home=home, project=project)
    once = settings.read_bytes()
    result = hide(["ecc"], settings, keep=["ecc:sub:deep"], home=home, project=project)
    assert settings.read_bytes() == once and result["added"] == 0
    data = json.loads(once)
    assert data["model"] == "è" and data["permissions"]["allow"] == ["Bash"]
    assert data["permissions"]["deny"] == ["Read(x)", "Agent(ecc:zeta)", "Agent(ecc:alpha)"]


def test_c3_dry_run_does_not_write(tmp_path: Path) -> None:
    home, project, settings = _setup(tmp_path)
    result = hide(["ecc"], settings, dry_run=True, home=home, project=project)
    assert result["rules"] == ["Agent(ecc:alpha)", "Agent(ecc:sub:deep)", "Agent(ecc:zeta)"]
    assert not settings.exists() and not settings.parent.exists()


def test_c4_restore_is_byte_exact_or_removes_created_file(tmp_path: Path) -> None:
    home, project, settings = _setup(tmp_path)
    original = b'{"permissions":{"deny":["Read(x)"]},\r\n "a": 1}'
    settings.parent.mkdir(parents=True)
    settings.write_bytes(original)
    hide(["ecc"], settings, home=home, project=project)
    hide(["ecc"], settings, home=home, project=project)  # backup is kept from the first run
    assert restore(settings)["restored"] is True
    assert settings.read_bytes() == original
    assert not settings.with_name("settings.json.fsr-backup").exists()

    missing = tmp_path / "new" / "settings.json"
    hide(["ecc"], missing, home=home, project=project)
    assert missing.exists()
    restore(missing)
    assert not missing.exists()
    assert restore(missing)["restored"] is False
