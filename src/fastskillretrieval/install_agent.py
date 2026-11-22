"""Install FastSkillRetrieval into a coding agent: prompt hook + /fsr-init-index command.

Idempotent and non-destructive: existing settings are merged (never replaced), and every entry this
module adds is recognisable by the marker ``fsr-hook.cjs`` so ``uninstall`` removes only its own.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

from .agent_index import fsr_home

MARKER = "fsr-hook.cjs"
ASSETS = Path(__file__).parent / "assets"

COMMAND_BODY = """---
description: Build or refresh the FastSkillRetrieval index of installed skills, subagents, commands
---

Run this command, wait for it to finish and report its JSON summary
(counts found, published, skipped):

```
"{python}" -m fastskillretrieval init-index --agent {agent} --project "$PWD"
```

The index is rebuilt from scratch each time and is used by the prompt hook to suggest relevant tools
before a request reaches you. If the command fails, show the error and stop.
"""


def _targets(agent: str, home: Path) -> tuple[Path, Path]:
    """(hook config file, slash-command file) for the agent."""
    if agent == "claude":
        return (
            home / ".claude" / "settings.json",
            home / ".claude" / "commands" / "fsr-init-index.md",
        )
    if agent == "codex":
        return home / ".codex" / "hooks.json", home / ".codex" / "prompts" / "fsr-init-index.md"
    raise ValueError(f"unsupported agent: {agent}")


def _load(path: Path) -> dict:
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} is not a JSON object")
    return value


def _entries(config: dict) -> list:
    hooks = config.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise ValueError("'hooks' must be an object")
    entries = hooks.setdefault("UserPromptSubmit", [])
    if not isinstance(entries, list):
        raise ValueError("'hooks.UserPromptSubmit' must be a list")
    return entries


def _ours(entry) -> bool:
    return isinstance(entry, dict) and any(
        MARKER in str(hook.get("command", ""))
        for hook in entry.get("hooks", [])
        if isinstance(hook, dict)
    )


def _save(path: Path, config: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not path.with_name(path.name + ".fsr-backup").exists():
        shutil.copy2(path, path.with_name(path.name + ".fsr-backup"))
    path.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def install(agent: str, home: Path | None = None, env=None, python: str | None = None) -> dict:
    home = home or Path.home()
    config_file, command_file = _targets(agent, home)
    hook = fsr_home(env) / "hooks" / MARKER
    hook.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ASSETS / MARKER, hook)

    config = _load(config_file)
    entries = [entry for entry in _entries(config) if not _ours(entry)]
    entries.append(
        {"hooks": [{"type": "command", "command": f'node "{hook}" {agent}', "timeout": 5}]}
    )
    config["hooks"]["UserPromptSubmit"] = entries
    _save(config_file, config)

    command_file.parent.mkdir(parents=True, exist_ok=True)
    command_file.write_text(
        COMMAND_BODY.format(python=python or sys.executable, agent=agent), encoding="utf-8"
    )
    return {
        "status": "ok",
        "agent": agent,
        "hook": str(hook),
        "config": str(config_file),
        "command": str(command_file),
        "next": "Run /fsr-init-index in the agent (or `fastskillretrieval init-index --agent "
        f"{agent}`) to build the search index.",
    }


def uninstall(agent: str, home: Path | None = None, env=None) -> dict:
    home = home or Path.home()
    config_file, command_file = _targets(agent, home)
    removed = False
    if config_file.exists():
        config = _load(config_file)
        entries = _entries(config)
        kept = [entry for entry in entries if not _ours(entry)]
        removed = len(kept) != len(entries)
        if kept:
            config["hooks"]["UserPromptSubmit"] = kept
        else:
            config["hooks"].pop("UserPromptSubmit", None)
            if not config["hooks"]:
                config.pop("hooks")
        _save(config_file, config)
    if command_file.exists():
        command_file.unlink()
    (fsr_home(env) / "hooks" / MARKER).unlink(missing_ok=True)
    return {"status": "ok", "agent": agent, "hook_removed": removed}
