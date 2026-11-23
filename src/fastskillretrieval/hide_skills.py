"""Hide installed plugin skills from Claude Code's resident skill list so FSR is the only way they reach the model.

Claude Code ignores ``skillOverrides`` for plugin skills; the documented per-skill switch is the
``disable-model-invocation: true`` frontmatter key. ``hide`` adds it to every ``skills/*/SKILL.md`` of the active
plugin installs and records the original bytes under ``<FSR_HOME>/hidden-skills/`` so ``restore`` is exact.
Plugin updates install fresh files: run ``hide`` again afterwards (it is idempotent).
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from .agent_index import fsr_home

KEY = "disable-model-invocation"
_FRONT = re.compile(r"\A(﻿?---)(\r?\n)(.*?)(\r?\n---)", re.S)
_LINE = re.compile(rf"^{KEY}:.*$", re.M)
_TRUE = re.compile(rf"^{KEY}:\s*true\s*$", re.M)


def _installs(home: Path) -> list[Path]:
    file = home / ".claude" / "plugins" / "installed_plugins.json"
    data = json.loads(file.read_text(encoding="utf-8-sig"))
    found = []
    for entries in data.get("plugins", data).values():
        for entry in entries if isinstance(entries, list) else [entries]:
            if entry.get("installPath"):
                found.append(Path(entry["installPath"]))
    return found


def _patched(text: str) -> str | None:
    """Return the text with the key set, or None when it is already true or has no frontmatter."""
    match = _FRONT.match(text)
    if not match:
        return None
    head, newline, body, tail = match.groups()
    if _TRUE.search(body):
        return None
    body = _LINE.sub(f"{KEY}: true", body) if _LINE.search(body) else f"{body}{newline}{KEY}: true"
    return head + newline + body + tail + text[match.end():]


def hide(home: Path | None = None, env=None) -> dict:
    home = home or Path.home()
    store = fsr_home(env) / "hidden-skills"
    manifest_file = store / "manifest.json"
    manifest = json.loads(manifest_file.read_text(encoding="utf-8")) if manifest_file.exists() else {}
    changed = untouched = 0
    store.mkdir(parents=True, exist_ok=True)
    for root in _installs(home):
        for file in sorted(root.glob("**/skills/*/SKILL.md")):
            raw = file.read_bytes()
            new = _patched(raw.decode("utf-8", errors="replace"))
            if new is None:
                untouched += 1
                continue
            key = hashlib.sha256(str(file).encode()).hexdigest()[:24]
            if key not in manifest:  # keep the very first original across repeated runs
                (store / f"{key}.orig").write_bytes(raw)
                manifest[key] = str(file)
            file.write_bytes(new.encode("utf-8"))
            changed += 1
    manifest_file.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return {"status": "ok", "hidden": changed, "already_hidden_or_without_frontmatter": untouched}


def restore(home: Path | None = None, env=None) -> dict:
    store = fsr_home(env) / "hidden-skills"
    manifest_file = store / "manifest.json"
    if not manifest_file.exists():
        return {"status": "ok", "restored": 0}
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    restored = 0
    for key, name in manifest.items():
        original, file = store / f"{key}.orig", Path(name)
        if original.exists() and file.exists():
            file.write_bytes(original.read_bytes())
            restored += 1
        original.unlink(missing_ok=True)
    manifest_file.unlink()
    return {"status": "ok", "restored": restored}
