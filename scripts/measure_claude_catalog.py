"""Opt-in observation of Claude Code's native skill catalog context estimate.

This tool copies a hash-pinned, explicit corpus into a new disposable project and invokes only
the local ``/context`` command.  It neither runs a model turn nor evaluates A/B/C quality.
"""

import argparse
import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

from fastskillretrieval.claude_context import ContextObservationError, parse_context_result
from fastskillretrieval.client_profiles import guarded_environment

ROOT = Path(__file__).resolve().parents[1]
_SKILL_ID = re.compile(r"ecc/[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")


class MeasurementError(ValueError):
    """The operator fixture or local command could not be measured safely."""


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def _sha256_bytes(value):
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def _fail(reason):
    raise MeasurementError(reason)


def _references(manifest, expected_skill_count):
    if (
        not isinstance(manifest, dict)
        or not isinstance(manifest.get("skill_references"), list)
        or len(manifest["skill_references"]) != expected_skill_count
    ):
        _fail("wrong_explicit_skill_count")
    references, identifiers = [], set()
    for item in manifest["skill_references"]:
        if not isinstance(item, dict) or set(item) != {"id", "path", "sha256"}:
            _fail("invalid_skill_reference")
        identifier, relative, digest = item["id"], item["path"], item["sha256"]
        path = Path(relative) if isinstance(relative, str) else None
        if (
            not isinstance(identifier, str)
            or not _SKILL_ID.fullmatch(identifier)
            or not isinstance(relative, str)
            or path is None
            or path.is_absolute()
            or "\\" in relative
            or ":" in relative
            or any(part in {"", ".", ".."} for part in path.parts)
            or not isinstance(digest, str)
            or len(digest) != 64
            or any(char not in "0123456789abcdef" for char in digest)
            or identifier in identifiers
        ):
            _fail("invalid_skill_reference")
        identifiers.add(identifier)
        references.append(item)
    return references


def materialize_fixture(manifest, source_root, project, *, expected_skill_count=100):
    """Copy only listed, hash-verified skill files to a project that must not exist yet."""
    references = _references(manifest, expected_skill_count)
    source_root, project = Path(source_root).resolve(), Path(project).resolve()
    if not source_root.is_dir() or project.exists():
        _fail("invalid_source_or_existing_project")
    project.mkdir(parents=True)
    copied = []
    try:
        for item in references:
            source = (source_root / item["path"]).resolve()
            if not source.is_relative_to(source_root) or not source.is_file():
                _fail("source_missing")
            if _sha256_file(source) != item["sha256"]:
                _fail("source_hash_mismatch")
            name = item["id"].removeprefix("ecc/")
            destination = project / ".claude" / "skills" / name / "SKILL.md"
            destination.parent.mkdir(parents=True, exist_ok=False)
            shutil.copyfile(source, destination)
            if _sha256_file(destination) != item["sha256"]:
                _fail("copied_hash_mismatch")
            copied.append({"id": item["id"], "sha256": item["sha256"]})
        fixture_hash = _sha256_bytes(_canonical(copied))
        source_manifest_hash = _sha256_bytes(_canonical(manifest))
        fixture = {
            "status": "prepared",
            "skill_count": len(copied),
            "source_fixture_hash": fixture_hash,
            "source_manifest_hash": source_manifest_hash,
            "skills": copied,
        }
        (project / "fixture-manifest.json").write_bytes(_canonical(fixture) + b"\n")
        (project / "empty-mcp.json").write_text('{"mcpServers":{}}\n', encoding="utf-8")
        return fixture
    except BaseException:
        (project / ".incomplete").write_text(
            "fixture preparation did not complete\n", encoding="utf-8"
        )
        raise


def _context_command(project):
    return [
        "claude",
        "--print",
        "--setting-sources",
        "project",
        "--strict-mcp-config",
        "--mcp-config",
        str(project / "empty-mcp.json"),
        "--tools",
        "Skill",
        "--permission-mode",
        "dontAsk",
        "--permission-prompts",
        "none",
        "--no-session-persistence",
        "--output-format",
        "json",
        "--max-turns",
        "1",
        "/context",
    ]


def _run(command, *, cwd, env):
    completed = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        shell=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )
    if completed.returncode != 0:
        _fail("claude_context_command_failed")
    return completed.stdout


def measure(
    manifest,
    source_root,
    project,
    *,
    client_version,
    expected_skill_count=100,
    run_command=_run,
):
    """Materialize one fixed corpus and collect one zero-use native context observation."""
    fixture = materialize_fixture(
        manifest, source_root, project, expected_skill_count=expected_skill_count
    )
    try:
        observation = parse_context_result(
            run_command(
                _context_command(Path(project)), cwd=Path(project), env=guarded_environment()
            ),
            client_version=client_version,
            source_fixture_hash=fixture["source_fixture_hash"],
            source_manifest_hash=fixture["source_manifest_hash"],
        )
    except ContextObservationError as error:
        _fail(str(error))
    report = {
        "status": "observed",
        "method_revision": "project-cwd-provider-guard-v2",
        "fixture": fixture,
        "observation": observation,
        "evaluation": {
            "status": "not_run_requires_human_approval",
            "context_reduction_A_to_C": None,
        },
    }
    (Path(project) / "context-observation.json").write_bytes(_canonical(report) + b"\n")
    return report


def _under_work(path):
    work = (ROOT / ".work").resolve()
    return Path(path).resolve().is_relative_to(work)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest", type=Path, required=True, help="Explicit 100-skill input list"
    )
    parser.add_argument(
        "--source-root", type=Path, required=True, help="Read-only root for manifest paths"
    )
    parser.add_argument("--project", type=Path, required=True, help="New .work project directory")
    parser.add_argument("--client-version", required=True, help="Observed Claude Code version")
    parser.add_argument("--execute", action="store_true", help="Run the local /context command")
    args = parser.parse_args(argv)
    if not args.execute:
        parser.error("--execute is required; this command is an explicit operator measurement")
    if not _under_work(args.project):
        parser.error("--project must be a new directory below this repository's .work")
    try:
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        print(
            json.dumps(
                measure(
                    manifest,
                    args.source_root,
                    args.project,
                    client_version=args.client_version,
                ),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
    except (MeasurementError, OSError, json.JSONDecodeError) as error:
        print(json.dumps({"status": "not_observed", "reason": str(error)}, separators=(",", ":")))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
