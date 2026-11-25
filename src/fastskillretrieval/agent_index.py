"""Discover what is installed in a coding agent and build the shared search index.

Layout (one convention shared by every client, including Caronte):

    <FSR_HOME>/sources/<agent>/   staged copies, one folder per capability (read by the registry)
    <FSR_HOME>/index/<agent>/     registry state: registry.sqlite3, sources.json, index.json

``FSR_HOME`` defaults to ``~/.fsr``. Originals are only read, never modified.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from .capabilities import parse_definition
from .registry import Registry

AGENTS = ("claude", "codex")
PREFIX = {"skill": "", "agent": "agent--", "command": "cmd--"}
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")
_IGNORED = shutil.ignore_patterns(".git", "node_modules", "__pycache__", ".venv")


def fsr_home(env=None) -> Path:
    env = os.environ if env is None else env
    return Path(env.get("FSR_HOME") or Path.home() / ".fsr")


def index_dir(agent: str, env=None) -> Path:
    return fsr_home(env) / "index" / agent


def sources_dir(agent: str, env=None) -> Path:
    return fsr_home(env) / "sources" / agent


@dataclass(frozen=True)
class Source:
    kind: str  # skill | agent | command
    name: str
    origin: str  # user | project | system | plugin:<name>
    path: Path  # skill: directory with SKILL.md; agent/command: the .md file

    @property
    def display(self) -> str:
        """Name as the user types it: plugin:name for plugin items, plain name otherwise."""
        return f"{self.origin[7:]}:{self.name}" if self.origin.startswith("plugin:") else self.name


def _skills(root: Path, origin: str):
    if root.is_dir():
        for directory in sorted(root.iterdir()):
            if directory.is_dir() and (directory / "SKILL.md").is_file():
                yield Source("skill", directory.name, origin, directory)


def _files(root: Path, kind: str, origin: str):
    if root.is_dir():
        for file in sorted(root.rglob("*.md")):
            if file.name.lower() != "readme.md" and file.is_file():
                relative = file.relative_to(root).with_suffix("")
                yield Source(kind, ":".join(relative.parts), origin, file)


def _json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _enabled_plugins(home: Path, project: Path | None) -> dict:
    merged: dict = {}
    files = [home / ".claude" / "settings.json", home / ".claude" / "settings.local.json"]
    if project:
        files += [
            project / ".claude" / "settings.json",
            project / ".claude" / "settings.local.json",
        ]
    for file in files:
        enabled = _json(file).get("enabledPlugins")
        if isinstance(enabled, dict):
            merged.update(enabled)
    return merged


def collect_claude(home: Path, project: Path | None = None) -> list[Source]:
    base = home / ".claude"
    found = [
        *_skills(base / "skills", "user"),
        *_files(base / "agents", "agent", "user"),
        *_files(base / "commands", "command", "user"),
    ]
    if project:
        local = project / ".claude"
        found += [
            *_skills(local / "skills", "project"),
            *_files(local / "agents", "agent", "project"),
            *_files(local / "commands", "command", "project"),
        ]
    enabled = _enabled_plugins(home, project)
    plugins = _json(base / "plugins" / "installed_plugins.json").get("plugins", {})
    seen: set[str] = set()
    for key, installs in plugins.items() if isinstance(plugins, dict) else []:
        if enabled.get(key) is False or not isinstance(installs, list):
            continue
        origin = f"plugin:{key.split('@')[0]}"
        for install in installs:
            path = install.get("installPath") if isinstance(install, dict) else None
            if not path or path in seen:
                continue
            seen.add(path)
            root = Path(path)
            found += [
                *_skills(root / "skills", origin),
                *_files(root / "agents", "agent", origin),
                *_files(root / "commands", "command", origin),
            ]
    return found


def collect_codex(home: Path, project: Path | None = None) -> list[Source]:
    base = home / ".codex"
    found = [
        *_skills(base / "skills", "user"),
        *_skills(base / "skills" / ".system", "system"),
        *_skills(home / ".agents" / "skills", "user"),
        *_files(base / "prompts", "command", "user"),
    ]
    if project:
        found += [
            *_skills(project / ".agents" / "skills", "project"),
            *_skills(project / ".codex" / "skills", "project"),
        ]
    cache = base / "plugins" / "cache"
    if cache.is_dir():
        for skill in sorted(cache.glob("**/skills/*/SKILL.md")):
            relative = skill.relative_to(cache).parts
            found.append(Source("skill", skill.parent.name, f"plugin:{relative[0]}", skill.parent))
    return found


def collect(agent: str, home: Path, project: Path | None = None) -> list[Source]:
    if agent == "claude":
        return collect_claude(home, project)
    if agent == "codex":
        return collect_codex(home, project)
    raise ValueError(f"unsupported agent: {agent}")


def stage(sources: list[Source], root: Path, progress=None) -> dict[str, dict]:
    """Copy every source into <root>/<folder>/ and return folder -> descriptor.

    ``progress(position, total, source)`` is called before each copy, to show what is being read."""
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    staged: dict[str, dict] = {}
    for position, source in enumerate(sources, 1):
        if progress:
            progress(position, len(sources), source)
        base = PREFIX[source.kind] + _UNSAFE.sub("-", source.name).strip("-")
        folder, count = base, 1
        if folder in staged:
            folder = f"{base}--{_UNSAFE.sub('-', source.origin).strip('-')}"
        while folder in staged:
            count += 1
            folder = f"{base}--{count}"
        target = root / folder
        if source.kind == "skill":
            shutil.copytree(source.path, target, ignore=_IGNORED)
        else:
            target.mkdir()
            shutil.copy2(source.path, target / "SKILL.md")
        staged[folder] = {"kind": source.kind, "name": source.display, "origin": source.origin}
    return staged


def focus(folder: str) -> str:
    """What a staged definition is, read from its name: 'agent--typescript-reviewer' ->
    'typescript reviewer (review reviewe)'. Agent-noun words also get their verb forms, because requests
    say "review"/"resolve" while names say "reviewer"/"resolver"; a form nobody types is harmless."""
    name = folder.removeprefix(PREFIX["agent"]).removeprefix(PREFIX["command"]).split("--")[0]
    words = [w.lower() for w in re.split(r"[^A-Za-z0-9]+", name) if w]
    verbs: list[str] = []
    for word in words:
        if len(word) >= 6 and word.endswith(("er", "or")):
            verbs += [form for form in (word[:-2], word[:-1]) if form not in words + verbs]
    return " ".join(words) + (f" ({' '.join(verbs)})" if verbs else "")


def _candidate(root: Path, item: dict) -> dict:
    """Bounded candidate metadata derived from the definition itself, without a model.

    The description is prefixed with the name's own words (see ``focus``): a definition whose name says
    what it is ranks above one that merely mentions the same technology in a long description, and the
    prefix stays inside the first indexed tokens of every column."""
    source = root / item["name"] / "SKILL.md"
    parsed = parse_definition(source, "skill")
    body = parsed["definition"]["body"]
    quote = next((line.strip() for line in body.splitlines() if line.strip()), "SKILL.md")
    description = parsed["description_original"].strip()
    if len(description) < 40:
        description = (description + " " + quote).strip()
    if len(description) < 40:
        description = source.read_text(encoding="utf-8").replace("\n", " ").strip()[:1000]
    if len(description) < 40:
        raise ValueError("source_description_too_short")
    label = focus(item["name"])
    if label:
        description = f"{label}: {description}"
    return {
        "description": description[:1000],
        "aliases": [item["name"]],
        "capabilities": [
            {
                "action": "read",
                "object": item["name"],
                "scope": description[:1000],
                "evidence": {"file": "SKILL.md", "quote": quote[:4000]},
            }
        ],
        "use_when": [description[:1000]],
        "exclusions": [],
        "technologies": [],
    }


def _inventory(store: Registry) -> list[dict]:
    offset, items = 0, []
    while True:
        page = store.call("inventory", f"fsr-inventory-{offset}", offset=offset, limit=20)
        if page["status"] != "ok":
            raise RuntimeError(f"inventory_failed:{page.get('reason')}")
        items.extend(page["items"])
        offset += len(page["items"])
        if offset >= page["total"] or not page["items"]:
            return items


def build_registry(
    state: Path, root: Path, log=lambda message: None, progress=None
) -> list[dict]:
    """Discover, submit and publish every staged folder. Returns the skipped items.

    ``progress(position, total, item, outcome)`` is called per definition; outcome is ok or skipped."""
    skipped: list[dict] = []
    store = Registry(state, root)
    try:
        cursor = None
        for round_number in range(200):
            arguments = {} if cursor is None else {"cursor": cursor}
            found = store.call("discover", f"fsr-discover-{round_number}", **arguments)
            if found["status"] != "ok":
                raise RuntimeError(f"discover_failed:{found.get('reason')}")
            todo = [i for i in _inventory(store) if i["state"] in ("discovered", "needs_revision")]
            for position, item in enumerate(todo, 1):
                try:
                    candidate = _candidate(root, item)
                except Exception as error:  # noqa: BLE001 - one bad definition must not stop the index
                    skipped.append({"name": item["name"], "reason": str(error)[:80]})
                    if progress:
                        progress(position, len(todo), item, "skipped")
                    continue
                result = store.call(
                    "submit",
                    f"fsr-submit-{item['id']}",
                    capability_id=item["id"],
                    source_revision=item["source_revision"],
                    candidate=candidate,
                )
                if result["status"] != "ok":
                    skipped.append({"name": item["name"], "reason": str(result.get("reason"))})
                if progress:
                    progress(position, len(todo), item, "ok" if result["status"] == "ok" else "skipped")
            while True:
                published = store.call("publish", "fsr-publish", batch_size=100, deadline_ms=30000)
                if published["status"] == "ok":
                    break
                if published["status"] != "in_progress":
                    raise RuntimeError(f"publish_failed:{published.get('reason')}")
            log(f"round {round_number}: {len(todo)} submitted")
            cursor = found.get("next_cursor")
            if cursor is None:
                break
    finally:
        store.close()
    return skipped


def signature(sources: list[Source]) -> str:
    """Cheap fingerprint of what is installed: kind, name and definition file stat."""
    digest = hashlib.sha256()
    for source in sorted(sources, key=lambda s: (s.kind, s.display, str(s.path))):
        file = source.path / "SKILL.md" if source.kind == "skill" else source.path
        try:
            stat = file.stat()
            mark = f"{stat.st_mtime_ns}:{stat.st_size}"
        except OSError:
            mark = "missing"
        digest.update(f"{source.kind}|{source.display}|{source.origin}|{mark}\n".encode())
    return digest.hexdigest()


def init_index(
    agent: str,
    home: Path | None = None,
    project: Path | None = None,
    env=None,
    log=lambda message: None,
    if_stale: bool = False,
    on_stage=None,
    on_item=None,
) -> dict:
    """Rebuild the index for ``agent`` from scratch and return a summary.

    With ``if_stale`` the rebuild is skipped when the installed set is unchanged since the last one.
    """
    started = time.time()
    home = home or Path.home()
    sources = collect(agent, home, project)
    fingerprint = signature(sources)
    root, state = sources_dir(agent, env), index_dir(agent, env)
    if if_stale and (state / "registry.sqlite3").is_file():
        try:
            previous = json.loads((state / "index.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            previous = {}
        if previous.get("signature") == fingerprint:
            return {**previous, "status": "current", "rebuilt": False}
    log(f"Found {len(sources)} items: " + ", ".join(
        f"{sum(1 for s in sources if s.kind == kind)} {kind}s" for kind in PREFIX
    ))
    staged = stage(sources, root, on_stage)
    if state.exists():
        shutil.rmtree(state)
    state.parent.mkdir(parents=True, exist_ok=True)
    skipped = build_registry(state, root, log, on_item)
    db = sqlite3.connect(state / "registry.sqlite3")
    try:
        published = db.execute("SELECT COUNT(*) FROM capability_fts").fetchone()[0]
    finally:
        db.close()
    (state / "sources.json").write_text(json.dumps(staged, ensure_ascii=False), encoding="utf-8")
    kinds = {kind: sum(1 for s in sources if s.kind == kind) for kind in PREFIX}
    summary = {
        "status": "ok",
        "agent": agent,
        "signature": fingerprint,
        "rebuilt": True,
        "index": str(state),
        "found": kinds,
        "published": published,
        "skipped": skipped[:20],
        "skipped_count": len(skipped),
        "seconds": round(time.time() - started, 1),
    }
    (state / "index.json").write_text(json.dumps(summary, ensure_ascii=False), encoding="utf-8")
    return summary
