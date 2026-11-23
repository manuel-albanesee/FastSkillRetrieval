import json
import os
import sqlite3
from pathlib import Path

from fastskillretrieval.agent_index import collect, index_dir, init_index
from fastskillretrieval.install_agent import LISTING_MOD, MARKER, PLUGIN_DIRS, install, uninstall

SKILL = "---\nname: {name}\ndescription: {text}\n---\n\n# {name}\n\nUse this to {text}.\n"
AGENT = (
    "---\nname: reviewer\ndescription: review TypeScript diffs for regressions\n---\n"
    "Review the diff carefully.\n"
)
COMMAND = "---\ndescription: deploy the service to production safely\n---\nDeploy now.\n"


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _claude_home(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    plugin = tmp_path / "plugins" / "demo"
    _write(
        home / ".claude" / "plugins" / "installed_plugins.json",
        json.dumps({"plugins": {"demo@market": [{"installPath": str(plugin)}]}}),
    )
    _write(
        plugin / "skills" / "gradle-helper" / "SKILL.md",
        SKILL.format(name="gradle-helper", text="fix Gradle build failures in Java projects"),
    )
    _write(plugin / "agents" / "reviewer.md", AGENT)
    _write(plugin / "commands" / "deploy.md", COMMAND)
    _write(
        home / ".claude" / "skills" / "sql-tuner" / "SKILL.md",
        SKILL.format(name="sql-tuner", text="optimise slow PostgreSQL queries and indexes"),
    )
    return home


def test_collect_finds_skills_agents_commands_and_plugin_origin(tmp_path):
    found = collect("claude", _claude_home(tmp_path))
    names = {(s.kind, s.display) for s in found}
    assert ("skill", "sql-tuner") in names
    assert ("skill", "demo:gradle-helper") in names
    assert ("agent", "demo:reviewer") in names
    assert ("command", "demo:deploy") in names


def test_disabled_plugin_is_skipped(tmp_path):
    home = _claude_home(tmp_path)
    _write(
        home / ".claude" / "settings.json",
        json.dumps({"enabledPlugins": {"demo@market": False}}),
    )
    assert {s.display for s in collect("claude", home)} == {"sql-tuner"}


def test_init_index_builds_searchable_registry(tmp_path):
    home = _claude_home(tmp_path)
    env = {"FSR_HOME": str(tmp_path / "fsr")}
    summary = init_index("claude", home=home, env=env)
    assert summary["status"] == "ok"
    assert summary["found"] == {"skill": 2, "agent": 1, "command": 1}
    assert summary["published"] == 4 and summary["skipped_count"] == 0
    with sqlite3.connect(index_dir("claude", env) / "registry.sqlite3") as db:
        row = db.execute(
            "SELECT c.name FROM capability_fts f JOIN capabilities c ON c.id = f.capability_id "
            "WHERE capability_fts MATCH ? ORDER BY bm25(capability_fts) LIMIT 1",
            ('"gradle"',),
        ).fetchone()
    assert row == ("gradle-helper",)
    mapping = json.loads((index_dir("claude", env) / "sources.json").read_text("utf-8"))
    assert mapping["agent--reviewer"]["name"] == "demo:reviewer"


def test_if_stale_skips_unchanged_and_rebuilds_on_change(tmp_path):
    home = _claude_home(tmp_path)
    env = {"FSR_HOME": str(tmp_path / "fsr")}
    first = init_index("claude", home=home, env=env, if_stale=True)
    assert first["rebuilt"] is True and first["status"] == "ok"
    again = init_index("claude", home=home, env=env, if_stale=True)
    assert again["status"] == "current" and again["rebuilt"] is False
    _write(
        home / ".claude" / "skills" / "new-one" / "SKILL.md",
        SKILL.format(name="new-one", text="draft release notes from commits"),
    )
    third = init_index("claude", home=home, env=env, if_stale=True)
    assert third["rebuilt"] is True and third["published"] == 5


def test_install_is_idempotent_and_uninstall_keeps_foreign_entries(tmp_path):
    home = tmp_path / "home"
    env = {"FSR_HOME": str(tmp_path / "fsr")}
    settings = home / ".claude" / "settings.json"
    foreign = {"hooks": [{"type": "command", "command": "echo other"}]}
    _write(settings, json.dumps({"model": "x", "hooks": {"UserPromptSubmit": [foreign]}}))

    install("claude", home=home, env=env)
    install("claude", home=home, env=env)
    config = json.loads(settings.read_text("utf-8"))
    entries = config["hooks"]["UserPromptSubmit"]
    assert config["model"] == "x" and foreign in entries
    assert sum(MARKER in json.dumps(e) for e in entries) == 1
    assert (home / ".claude" / "commands" / "fsr-init-index.md").is_file()
    assert (tmp_path / "fsr" / "hooks" / MARKER).is_file()

    uninstall("claude", home=home, env=env)
    after = json.loads(settings.read_text("utf-8"))
    assert after["hooks"]["UserPromptSubmit"] == [foreign]
    assert not (home / ".claude" / "commands" / "fsr-init-index.md").exists()


def test_install_enables_agent_listing_mod_and_uninstall_removes_it(tmp_path):
    home = tmp_path / "home"
    env = {"FSR_HOME": str(tmp_path / "fsr")}
    settings = home / ".claude" / "settings.json"
    other = str(tmp_path / "other-mod")
    _write(settings, json.dumps({"env": {"KEEP_ME": "1", PLUGIN_DIRS: other}}))
    mod = tmp_path / "fsr" / "mods" / LISTING_MOD

    install("claude", home=home, env=env)
    (mod / "config.json").write_text('{"mode": "names"}', encoding="utf-8")
    install("claude", home=home, env=env)
    block = json.loads(settings.read_text("utf-8"))["env"]
    assert block["KEEP_ME"] == "1"
    assert block[PLUGIN_DIRS].split(os.pathsep) == [other, str(mod)]
    assert (mod / "hooks" / "register.ts").is_file() and (mod / ".claude-plugin" / "plugin.json").is_file()
    assert not (mod / "hooks" / "register.test.ts").exists()
    assert (mod / "config.json").read_text(encoding="utf-8") == '{"mode": "names"}'  # user settings survive

    uninstall("claude", home=home, env=env)
    assert json.loads(settings.read_text("utf-8"))["env"] == {"KEEP_ME": "1", PLUGIN_DIRS: other}
    assert not mod.exists()


def test_install_without_agent_listing_and_codex_leave_env_alone(tmp_path):
    home = tmp_path / "home"
    env = {"FSR_HOME": str(tmp_path / "fsr")}
    install("claude", home=home, env=env)
    install("claude", home=home, env=env, agent_listing=False)
    assert "env" not in json.loads((home / ".claude" / "settings.json").read_text("utf-8"))
    install("codex", home=home, env=env)
    assert "env" not in json.loads((home / ".codex" / "hooks.json").read_text("utf-8"))
