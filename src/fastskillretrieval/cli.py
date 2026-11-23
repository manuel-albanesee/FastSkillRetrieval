"""Explicit operator commands. No background scans or provider calls."""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from .catalog import Catalog
from .contracts import Failure, Limits, serialized


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="FastSkillRetrieval - local capability retrieval")
    result.add_argument("--state", type=Path, default=Path(".fsr"))
    result.add_argument("--registry", action="store_true", help="Use the opt-in v2 registry")
    result.add_argument(
        "--source-root",
        type=Path,
        required=True,
        help="Authorized root; each immediate subdirectory is one package",
    )
    result.add_argument(
        "--session", default="default", help="Keep the same ID for the same context"
    )
    result.add_argument(
        "--limits", type=Path, help="JSON object overriding positive integer limits"
    )
    commands = result.add_subparsers(dest="command", required=True)
    inventory = commands.add_parser("inventory")
    inventory.add_argument("--refresh", action="store_true")
    inventory.add_argument("--offset", type=int, default=0)
    inventory.add_argument("--limit", type=int, default=20)
    inspect = commands.add_parser("inspect")
    inspect.add_argument("skill_id")
    inspect.add_argument("version")
    inspect.add_argument("--file")
    submit = commands.add_parser("submit")
    submit.add_argument("skill_id")
    submit.add_argument("version")
    submit.add_argument("candidate", type=Path)
    publish = commands.add_parser("publish")
    publish.add_argument("--batch-size", type=int)
    publish.add_argument("--deadline-ms", type=int)
    search = commands.add_parser("search")
    search.add_argument("query", nargs="?", default="")
    search.add_argument("--explicit-id")
    search.add_argument("--technology", dest="technologies", action="append")
    search.add_argument("--exclude-id", dest="exclude_ids", action="append")
    search.add_argument("--limit", type=int, default=5)
    search.add_argument("--request-id")
    search.add_argument("--kind", dest="kinds", action="append")
    search.add_argument("--mode", choices=["discovery", "prepare_use"], default="discovery")
    search.add_argument("--epoch", type=int)
    for name in ("load", "resource", "plan"):
        command = commands.add_parser(name)
        command.add_argument("reference", help="JSON reference returned by search")
        if name == "resource":
            command.add_argument("file")
        if name == "plan":
            command.add_argument(
                "--mode", choices=["discovery", "prepare_use"], default="discovery"
            )
    discover = commands.add_parser("discover")
    discover.add_argument("--cursor", type=int, default=0)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("plan_id")
    reconcile = commands.add_parser("reconcile")
    reconcile.add_argument("--cursor", type=int, default=0)
    export = commands.add_parser("export")
    export.add_argument("--offset", type=int, default=0)
    export.add_argument("--limit", type=int, default=10)
    export.add_argument("--kind")
    export.add_argument("--status")
    rollback = commands.add_parser("rollback")
    rollback.add_argument("catalog_id", type=int)
    status = commands.add_parser("set-status")
    status.add_argument("capability_id")
    status.add_argument("status", choices=["enabled", "disabled", "archived", "quarantined"])
    migration = commands.add_parser("migrate-state")
    migration.add_argument("legacy", type=Path)
    migration.add_argument("target", type=Path)
    migration.add_argument("--activate", action="store_true")
    enabled = commands.add_parser("set-enabled")
    enabled.add_argument("skill_id")
    enabled.add_argument("enabled", choices=["true", "false"])
    commands.add_parser("diagnostics")
    graph = commands.add_parser("graph", help="Inspect or rebuild the derived Teseo-style graph")
    graph.add_argument("--rebuild", action="store_true")
    graph.add_argument("--capability-id")
    graph.add_argument("--limit", type=int, default=20)
    graph.add_argument("--offset", type=int, default=0)
    commands.add_parser("serve")
    return result


def agent_parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        prog="fastskillretrieval",
        description="Install into a coding agent and build its search index",
    )
    commands = result.add_subparsers(dest="command", required=True)
    for name, text in (
        ("install", "Register the prompt hook and the /fsr-init-index command"),
        ("uninstall", "Remove only what install added"),
        ("init-index", "Index every skill, subagent and command installed in the agent"),
    ):
        command = commands.add_parser(name, help=text)
        command.add_argument("--agent", choices=["claude", "codex"], default="claude")
        if name == "init-index":
            command.add_argument(
                "--project", type=Path, help="Also index this project's .claude/.codex"
            )
            command.add_argument(
                "--if-stale",
                action="store_true",
                help="Skip the rebuild when installed skills/agents/commands are unchanged",
            )
    hide = commands.add_parser(
        "hide-skills", help="Hide plugin skills from Claude's resident list (FSR injects them instead)"
    )
    hide.add_argument("--restore", action="store_true", help="Put every SKILL.md back as it was")
    agents = commands.add_parser(
        "hide-agents", help="Hide plugin agents via Agent(...) deny rules in a settings file"
    )
    agents.add_argument("--plugin", dest="plugins", action="append", help="Required unless --restore")
    agents.add_argument("--keep", action="append", default=[], help="plugin:agent to leave visible")
    agents.add_argument("--settings", type=Path, help="Default: <cwd>/.claude/settings.json")
    agents.add_argument("--dry-run", action="store_true")
    agents.add_argument("--restore", action="store_true", help="Put the settings file back as it was")
    return result


def agent_main(argv) -> int:
    args = agent_parser().parse_args(argv)
    if args.command == "install":
        from .install_agent import install

        result = install(args.agent)
    elif args.command == "uninstall":
        from .install_agent import uninstall

        result = uninstall(args.agent)
    elif args.command == "hide-skills":
        from .hide_skills import hide, restore

        result = restore() if args.restore else hide()
    elif args.command == "hide-agents":
        from . import hide_agents

        if args.restore:
            result = hide_agents.restore(args.settings)
        else:
            if not args.plugins:
                agent_parser().error("hide-agents requires --plugin unless --restore")
            result = hide_agents.hide(
                args.plugins, args.settings, keep=args.keep, dry_run=args.dry_run
            )
    else:
        from .agent_index import init_index

        result = init_index(
            args.agent,
            project=args.project,
            log=lambda m: print(m, file=sys.stderr),
            if_stale=args.if_stale,
        )
    sys.stdout.write(serialized(result) + "\n")
    return 0


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv and argv[0] in ("install", "uninstall", "init-index", "hide-skills", "hide-agents"):
        return agent_main(argv)
    args = vars(parser().parse_args(argv))
    source, state = args.pop("source_root"), args.pop("state")
    session, config = args.pop("session"), args.pop("limits")
    registry = args.pop("registry")
    command = args.pop("command").replace("-", "_")
    service = None
    try:
        limits = Limits(**json.loads(config.read_text(encoding="utf-8"))) if config else Limits()
        if command == "serve":
            from .server import serve

            serve(state, source, limits, registry=registry)
            return 0
        if command == "migrate_state":
            from .migrations import migrate_legacy

            result = migrate_legacy(args["legacy"], args["target"], source, args["activate"])
            sys.stdout.write(serialized(result) + "\n")
            return 0 if result["status"] == "ok" else 2
        if registry:
            from .registry import Registry

            service = Registry(state, source, limits)
        else:
            service = Catalog(state, source, limits)
        if "candidate" in args:
            with args["candidate"].open("rb") as stream:
                data = stream.read(limits.candidate_bytes + 1)
            if len(data) > limits.candidate_bytes:
                raise Failure("limit_exceeded", "candidate_too_large")
            args["candidate"] = json.loads(data)
        if "reference" in args:
            args["reference"] = json.loads(args["reference"])
        if "enabled" in args:
            args["enabled"] = args["enabled"] == "true"
        if command == "publish":
            args = {key: value for key, value in args.items() if value is not None}
        if registry:
            if command in {"inspect", "submit"}:
                args["capability_id"] = args.pop("skill_id")
                args["source_revision"] = args.pop("version")
            if command == "inventory":
                refresh = args.pop("refresh")
                if refresh:
                    discovered = service.call("discover", session)
                    if discovered["status"] != "ok":
                        raise Failure(discovered["status"], discovered["reason"])
            if command == "set_enabled":
                command = "set_status"
                args = {
                    "capability_id": args["skill_id"],
                    "status": "enabled" if args["enabled"] else "disabled",
                }
        else:
            if command == "inspect" and args.get("file") is None:
                args.pop("file")
            if command == "search":
                for key, default in (
                    ("request_id", None),
                    ("kinds", None),
                    ("mode", "discovery"),
                    ("epoch", None),
                ):
                    if args.pop(key) != default:
                        raise Failure("unsupported_integration", "registry_flag_required")
        result = service.call(command, session, **args)
    except Failure as error:
        result = {"status": error.status, "reason": error.reason}
    except (OSError, sqlite3.Error):
        result = {"status": "catalog_unavailable", "reason": "startup_or_input_file_failed"}
    except (ValueError, TypeError):
        result = {"status": "policy_denied", "reason": "invalid_json_or_configuration"}
    finally:
        if service:
            service.close()
    # One canonical JSON response, no source contents or requests in diagnostic logs.
    sys.stdout.write(serialized(result) + "\n")
    return 0 if result["status"] in {"ok", "no_match", "in_progress"} else 2
