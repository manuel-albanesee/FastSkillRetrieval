"""Hide plugin agents from Claude Code by writing ``Agent(<plugin>:<agent>)`` rules into ``permissions.deny``.

The agents stay in the FSR index. The first ``hide`` on a settings file keeps a byte-exact copy in
``<settings>.fsr-backup`` (or an ``.fsr-absent`` marker when the file did not exist) so ``restore`` is exact.
"""

from __future__ import annotations

import json
from pathlib import Path

from .agent_index import collect_claude

BACKUP = ".fsr-backup"
ABSENT = ".fsr-absent"


def _sibling(settings: Path, suffix: str) -> Path:
    return settings.with_name(settings.name + suffix)


def _rules(plugins, keep, home: Path, project: Path) -> list[str]:
    wanted, kept = {f"plugin:{p}" for p in plugins}, set(keep)
    names = {
        source.display
        for source in collect_claude(home, project)
        if source.kind == "agent" and source.origin in wanted
    }
    return [f"Agent({name})" for name in sorted(names - kept)]


def hide(
    plugins,
    settings: Path | None = None,
    keep=(),
    dry_run: bool = False,
    home: Path | None = None,
    project: Path | None = None,
) -> dict:
    home, project = home or Path.home(), project or Path.cwd()
    settings = settings or project / ".claude" / "settings.json"
    rules = _rules(plugins, keep, home, project)
    if dry_run:
        return {"status": "ok", "dry_run": True, "rules": rules}
    exists = settings.exists()
    raw = settings.read_bytes() if exists else b""
    data = json.loads(raw.decode("utf-8-sig")) if exists else {}
    if not isinstance(data, dict):
        raise ValueError("settings is not a JSON object")
    permissions = data.setdefault("permissions", {})
    deny = permissions.setdefault("deny", [])
    if not isinstance(permissions, dict) or not isinstance(deny, list):
        raise ValueError("permissions.deny is not a list")
    new = [rule for rule in rules if rule not in deny]
    if new or not exists:
        backup, marker = _sibling(settings, BACKUP), _sibling(settings, ABSENT)
        if not backup.exists() and not marker.exists():  # keep the very first original
            settings.parent.mkdir(parents=True, exist_ok=True)
            (backup if exists else marker).write_bytes(raw)
        deny.extend(new)
        settings.parent.mkdir(parents=True, exist_ok=True)
        settings.write_bytes((json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))
    return {"status": "ok", "added": len(new), "already_present": len(rules) - len(new), "rules": rules}


def restore(settings: Path | None = None, project: Path | None = None) -> dict:
    settings = settings or (project or Path.cwd()) / ".claude" / "settings.json"
    backup, marker = _sibling(settings, BACKUP), _sibling(settings, ABSENT)
    restored = False
    if backup.exists():
        settings.write_bytes(backup.read_bytes())
        restored = True
    elif marker.exists():
        settings.unlink(missing_ok=True)
        restored = True
    backup.unlink(missing_ok=True)
    marker.unlink(missing_ok=True)
    return {"status": "ok", "restored": restored}
