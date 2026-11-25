"""Install FastSkillRetrieval into a coding agent: prompt hook + /fsr-init-index command.

Idempotent and non-destructive: existing settings are merged (never replaced), and every entry this
module adds is recognisable by the marker ``fsr-hook.cjs`` so ``uninstall`` removes only its own.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

from .agent_index import fsr_home

MARKER = "fsr-hook.cjs"
ASSETS = Path(__file__).parent / "assets"
# Claude Code mod that shrinks the agent listing to the built-in agents (the prompt hook names the relevant
# others). Loaded through CLAUDE_CODE_PLUGIN_DIRS in the `env` block of ~/.claude/settings.json.
LISTING_MOD = "fsr-agent-listing"
PLUGIN_DIRS = "CLAUDE_CODE_PLUGIN_DIRS"
_MOD_LOCAL = shutil.ignore_patterns("listing.jsonl", "config.json", "*.test.ts", "types")


def _plugin_dirs(config: dict, add: Path | None) -> None:
    """Drop every fsr-agent-listing entry from the env var, then append `add` (if given); other paths stay."""
    block = config.get("env")
    if block is None and add is None:
        return
    if block is not None and not isinstance(block, dict):
        raise ValueError("'env' must be an object")
    block = config.setdefault("env", {}) if block is None else block
    paths = [p for p in str(block.get(PLUGIN_DIRS, "")).split(os.pathsep) if p and Path(p).name != LISTING_MOD]
    if add is not None:
        paths.append(str(add))
    if paths:
        block[PLUGIN_DIRS] = os.pathsep.join(paths)
    else:
        block.pop(PLUGIN_DIRS, None)
        if not block:
            config.pop("env")

COMMAND_BODY = """---
description: Build or refresh the FastSkillRetrieval index of installed skills, subagents, commands
---

Run this command, wait for it to finish and report its JSON summary
(counts found, published, skipped):

```
"{python}" -m fastskillretrieval init-index --agent {agent} --project "$PWD" --if-stale
```

The index is rebuilt from scratch only when the installed skills/agents/commands changed since the last
build (status "current" means the existing index was reused). It is used by the prompt hook to suggest
relevant tools before a request reaches you. To force a rebuild, run the command without `--if-stale`. If the command fails, show the error and stop.
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


def _entries(config: dict, event: str = "UserPromptSubmit") -> list:
    hooks = config.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise ValueError("'hooks' must be an object")
    entries = hooks.setdefault(event, [])
    if not isinstance(entries, list):
        raise ValueError(f"'hooks.{event}' must be a list")
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


def install(agent: str, home: Path | None = None, env=None, python: str | None = None,
            agent_listing: bool = True) -> dict:
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
    # The session banner (user-facing, costs the model no tokens) is registered for Claude Code only.
    starts = [entry for entry in _entries(config, "SessionStart") if not _ours(entry)]
    if agent == "claude":
        starts.append({"hooks": [{"type": "command", "command": f'node "{hook}" {agent}', "timeout": 5}]})
    if starts:
        config["hooks"]["SessionStart"] = starts
    else:
        config["hooks"].pop("SessionStart", None)
    mod = None
    if agent == "claude":
        mod_dir = fsr_home(env) / "mods" / LISTING_MOD
        if agent_listing:
            # The user's config.json (mode, keep) in the installed copy survives a reinstall.
            shutil.copytree(ASSETS / LISTING_MOD, mod_dir, dirs_exist_ok=True, ignore=_MOD_LOCAL)
            mod = str(mod_dir)
        _plugin_dirs(config, mod_dir if agent_listing else None)
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
        "agent_listing_mod": mod,
        "next": "Run /fsr-init-index in the agent (or `fastskillretrieval init-index --agent "
        f"{agent}`) to build the search index.",
    }


def uninstall(agent: str, home: Path | None = None, env=None) -> dict:
    home = home or Path.home()
    config_file, command_file = _targets(agent, home)
    removed = False
    if config_file.exists():
        config = _load(config_file)
        removed = False
        for event in ("UserPromptSubmit", "SessionStart"):
            entries = _entries(config, event)
            kept = [entry for entry in entries if not _ours(entry)]
            removed = removed or len(kept) != len(entries)
            if kept:
                config["hooks"][event] = kept
            else:
                config["hooks"].pop(event, None)
        if not config["hooks"]:
            config.pop("hooks")
        if agent == "claude":
            _plugin_dirs(config, None)
        _save(config_file, config)
    if command_file.exists():
        command_file.unlink()
    (fsr_home(env) / "hooks" / MARKER).unlink(missing_ok=True)
    shutil.rmtree(fsr_home(env) / "mods" / LISTING_MOD, ignore_errors=True)
    return {"status": "ok", "agent": agent, "hook_removed": removed}
