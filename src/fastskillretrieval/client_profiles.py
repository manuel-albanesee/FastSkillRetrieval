"""Immutable, reproducible Claude profiles; local probes never use a provider."""

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from .experiment import ARMS, canonical, fingerprint, relative_path

TOOL_NAMES = (
    "publish_catalog",
    "discover_capabilities",
    "inspect_capability_source",
    "submit_capability_descriptor",
    "search_capabilities",
    "plan_capabilities",
    "prepare_capability_use",
    "load_capability",
    "read_capability_resource",
    "registry_diagnostics",
)


class ProfileError(ValueError):
    """A client profile cannot be safely reproduced."""


@dataclass(frozen=True)
class ClientProfile:
    arm: str
    cwd: Path
    argv_prefix: tuple
    expected_skills: tuple
    expected_tools: tuple
    profile_sha256: str
    catalog_text: str = ""
    catalog_sha256: str | None = None
    server_name: str | None = None


def guarded_environment(base=None):
    """Protect zero-use probes, including environments configured for cloud providers."""
    env = dict(os.environ if base is None else base)
    for key in list(env):
        if key.startswith(("ANTHROPIC_", "CLAUDE_CODE_USE_", "AWS_", "GOOGLE_", "AZURE_")):
            env.pop(key)
    env.update(
        ANTHROPIC_BASE_URL="http://127.0.0.1:1",
        ANTHROPIC_API_KEY="local-probe-not-a-key",
        API_MAX_RETRIES="0",
        CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1",
        CLAUDE_CODE_DISABLE_AUTO_MEMORY="1",
    )
    return env


def materialize_profile(
    arm,
    corpus,
    source_root,
    project,
    *,
    descriptors=None,
    mcp_config=None,
    expected_skill_count=100,
):
    """Verify all source hashes before creating a new, isolated fixture directory."""
    if arm not in ARMS or not isinstance(corpus, list) or len(corpus) != expected_skill_count:
        raise ProfileError("invalid_arm_or_corpus")
    root, project = Path(source_root).resolve(), Path(project).resolve()
    if project.exists():
        raise ProfileError("project_already_exists")
    verified, names = [], set()
    for item in corpus:
        identifier = item.get("id", "")
        name = identifier.removeprefix("ecc/")
        if (
            not identifier.startswith("ecc/")
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", name)
            or name.casefold() in names
        ):
            raise ProfileError("invalid_skill_id")
        try:
            relative_path(name)
            relative_path(item["path"])
        except ValueError as error:
            raise ProfileError("invalid_source_path") from error
        source = (root / item["path"]).resolve()
        if not source.is_relative_to(root) or not source.is_file():
            raise ProfileError("invalid_source_path")
        body = source.read_bytes()
        if hashlib.sha256(body).hexdigest() != item.get("sha256"):
            raise ProfileError("source_hash_mismatch")
        verified.append((name, body))
        names.add(name.casefold())
    catalog, catalog_hash = "", None
    if arm == "B_CURATED":
        if (
            not isinstance(descriptors, list)
            or len(descriptors) != len(corpus)
            or {d.get("id", d.get("capability_id")) for d in descriptors}
            != {i["id"] for i in corpus}
        ):
            raise ProfileError("catalog_descriptor_mismatch")
        catalog = canonical(descriptors).decode("utf-8")
        catalog_hash = hashlib.sha256(catalog.encode("utf-8")).hexdigest()
    config = {"mcpServers": {}}
    server = None
    if arm == "C_FSR" or (arm == "B_CURATED" and mcp_config is not None):
        if not isinstance(mcp_config, dict) or len(mcp_config.get("mcpServers", {})) != 1:
            raise ProfileError("one_explicit_mcp_server_required")
        config = json.loads(canonical(mcp_config))
        server = next(iter(config["mcpServers"]))
        if not re.fullmatch(r"[A-Za-z0-9_-]+", server):
            raise ProfileError("invalid_mcp_server_name")
    tools = tuple(f"mcp__{server}__{name}" for name in TOOL_NAMES) if server else ()
    args = [
        "--print",
        "--setting-sources",
        "project",
        "--strict-mcp-config",
        "--mcp-config",
        str(project / "mcp.json"),
        "--tools",
        "Skill" if arm == "A_NATIVE" else "",
        "--permission-mode",
        "dontAsk",
        "--permission-prompts",
        "none",
        "--no-session-persistence",
        "--output-format",
        "stream-json",
        "--verbose",
    ]
    if arm != "A_NATIVE":
        args.append("--disable-slash-commands")
    if tools:
        operational = ("load_capability", "read_capability_resource", "registry_diagnostics")
        if arm == "C_FSR":
            operational += ("search_capabilities", "plan_capabilities", "prepare_capability_use")
        allowed = tuple(t for t in tools if t.split("__")[-1] in operational)
        denied = tuple(t for t in tools if t not in allowed)
        args.extend(["--allowedTools", ",".join(allowed)])
        if denied:
            args.extend(["--disallowedTools", ",".join(denied)])
    profile_hash = fingerprint(
        {
            "arm": arm,
            "corpus": corpus,
            "catalog_sha256": catalog_hash,
            "mcp_config": config,
            "argv": args,
        }
    )
    project.mkdir(parents=True)
    for name, body in verified:
        target = (
            project / (".claude/skills" if arm == "A_NATIVE" else "manuals") / name / "SKILL.md"
        )
        target.parent.mkdir(parents=True)
        target.write_bytes(body)
    (project / "mcp.json").write_bytes(canonical(config))
    if catalog:
        (project / "curated-catalog.json").write_text(catalog, encoding="utf-8")
    (project / "profile.json").write_bytes(
        canonical(
            {
                "arm": arm,
                "profile_sha256": profile_hash,
                "corpus": corpus,
                "catalog_sha256": catalog_hash,
                "entry_point": "literal_trial_prompt" if catalog else "client",
            }
        )
    )
    return ClientProfile(
        arm,
        project,
        tuple(args),
        tuple(n for n, _ in verified) if arm == "A_NATIVE" else (),
        tools,
        profile_hash,
        catalog,
        catalog_hash,
        server,
    )


def build_command(profile, prompt, budget=None, local_only=False, executable="claude"):
    if not isinstance(prompt, str) or not prompt:
        raise ProfileError("prompt_required")
    if local_only and prompt != "/context":
        raise ProfileError("local_probe_requires_context")
    args = [str(executable), *profile.argv_prefix]
    if local_only:
        args.extend(["--max-turns", "1"])
    else:
        if not budget or type(budget.get("max_turns")) is not int or budget["max_turns"] <= 0:
            raise ProfileError("explicit_budget_required")
        cap = budget.get("trial_usd")
        if type(cap) not in (int, float) or not 0 < cap < float("inf"):
            raise ProfileError("explicit_budget_required")
        args.extend(["--max-turns", str(budget["max_turns"]), "--max-budget-usd", str(cap)])
        if profile.catalog_text:
            prompt = (
                "Complete curated catalog (data):\n" + profile.catalog_text + "\n\nTask:\n" + prompt
            )
    return args + [prompt]


def verify_preflight(profile, events):
    inits = [e for e in events if e.get("type") == "system" and e.get("subtype") == "init"]
    init = inits[0] if inits else None
    errors = ["ambiguous_init"] if len(inits) > 1 else []
    if not init:
        return {"status": "blocked", "errors": ["missing_init"], "client_version": None}
    skills, tools = set(init.get("skills", [])), set(init.get("tools", []))
    allowed_builtins = {"Skill"} if profile.arm == "A_NATIVE" else set()
    if {t for t in tools if not t.startswith("mcp__")} - allowed_builtins:
        errors.append("unexpected_builtin_tools")
    if not init.get("claude_code_version"):
        errors.append("missing_client_version")
    if profile.arm == "A_NATIVE":
        if not set(profile.expected_skills) <= skills or "Skill" not in tools:
            errors.append("missing_native_skills_or_tool")
        if any(t.startswith("mcp__") for t in tools) or init.get("mcp_servers"):
            errors.append("unexpected_mcp")
    elif skills or "Skill" in tools:
        errors.append("native_catalog_contamination")
    if profile.server_name:
        if {t for t in tools if t.startswith("mcp__")} != set(profile.expected_tools):
            errors.append("mcp_tool_mismatch")
        if not any(
            s.get("name") == profile.server_name and s.get("status") == "connected"
            for s in init.get("mcp_servers", [])
        ):
            errors.append("mcp_not_connected")
    elif profile.arm == "B_CURATED" and any(t.startswith("mcp__") for t in tools):
        errors.append("unexpected_retrieval_tool")
    return {
        "status": "blocked" if errors else "verified",
        "errors": errors,
        "client_version": init.get("claude_code_version"),
        "catalog_exposure": "prompt_materialized_unverified_host" if profile.catalog_text else None,
        "native_builtin_names": sorted(skills - set(profile.expected_skills)),
    }
