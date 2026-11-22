"""Opt-in, bounded Claude Code native-agent probe.

This is an operator harness, not a library or MCP entry point.  It launches
Claude only with ``--run`` and passes its direct stdout pipe to the protected
host bridge.  The receipt proves one correlated native Agent lifecycle, never
that Claude's general permission policy was enforced.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from fastskillretrieval.host_bridge import (  # noqa: E402
    ClaudeNativeHostBridge,
    NativeHandoff,
    ProtectedReceiptStore,
)
from fastskillretrieval.native_integration import NativeRegistryCoordinator  # noqa: E402
from fastskillretrieval.packages import digest  # noqa: E402
from fastskillretrieval.registry import Registry  # noqa: E402

MARKER = "FSR-NATIVE-PRELOAD-MARKER-01"


def fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _candidate(name: str, evidence_file: str, quote: str) -> dict:
    """Data-only metadata accepted by the real registry validator."""
    description = f"Harmless native probe definition for {name}; no external side effects."
    return {
        "description": description,
        "aliases": [name],
        "capabilities": [
            {
                "action": "read",
                "object": name,
                "scope": description,
                "evidence": {"file": evidence_file, "quote": quote},
            }
        ],
        "use_when": [description],
        "exclusions": [],
        "technologies": [],
    }


def _operator_handoff(
    bridge: ClaudeNativeHostBridge, root: Path, run_id: str, now: float
) -> NativeHandoff:
    """Exercise discover → submit → publish → plan → coordinator with no SQL seeding."""
    workspace = root / run_id
    source = workspace / "sources"
    skill = source / "probe-skill"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: probe-skill\ndescription: harmless declared preload\n---\n"
        f"Return exactly {MARKER}. Do not call tools.\n",
        encoding="utf-8",
    )
    agent = source / "probe-agent"
    agent.mkdir()
    (agent / "agent.md").write_text(
        "# FSR Native Agent\nFollow the declared preload.\n", encoding="utf-8"
    )
    preload_id = digest(f"{os.path.normcase(str(source))}/probe-skill".encode())[:24]
    (agent / "capability.json").write_text(
        json.dumps({"kind": "subagent", "preloads": [preload_id]}), encoding="utf-8"
    )
    session = f"native-{run_id[:12]}"
    store = Registry(workspace / "registry", source)
    try:
        discovered = store.call("discover", session)
        if discovered["status"] != "ok" or discovered["next_cursor"] is not None:
            raise RuntimeError("native_probe_discovery_failed")
        agent_item = None
        for item in discovered["items"]:
            if item["kind"] == "subagent":
                agent_item = item
                candidate = _candidate(item["name"], "agent.md", "# FSR Native Agent")
            else:
                candidate = _candidate(item["name"], "SKILL.md", MARKER)
            submitted = store.call(
                "submit",
                session,
                capability_id=item["id"],
                source_revision=item["source_revision"],
                candidate=candidate,
            )
            if submitted["status"] != "ok":
                raise RuntimeError("native_probe_submission_failed")
        if agent_item is None or store.call("publish", session)["status"] != "ok":
            raise RuntimeError("native_probe_publication_failed")
        entry = store.db.execute(
            "SELECT * FROM entries WHERE catalog_id=? AND capability_id=?",
            (store.active(), agent_item["id"]),
        ).fetchone()
        plan = store.call("plan", session, reference=store._ref(entry))
        if plan["status"] != "ok":
            raise RuntimeError("native_probe_plan_failed")
        policy = {
            "runtime": "claude-code",
            "version": "2.1.302",
            "surface": "cli",
            "root_fingerprint": digest(os.path.normcase(str(source)).encode()),
            "config_fingerprint": fingerprint("restricted-read-agent-v1"),
            "expires_at": now + 150,
            "bindings": {
                agent_item["id"]: {
                    "native_id": "fsr_probe",
                    "source_body_digest": digest((agent / "agent.md").read_bytes()),
                    "agent_definition": {
                        "description": (
                            "A harmless native agent that follows its declared preload."
                        ),
                        "prompt": (
                            "Use the declared preloaded skill as your entire task. "
                            "Do not call tools."
                        ),
                        "tools": ["Read"],
                    },
                }
            },
        }
        return NativeRegistryCoordinator(store, bridge).prepare(
            plan["plan_id"], session, policy, now=now
        )
    finally:
        store.close()


def _child_evidence(event: dict) -> tuple[bool, int]:
    if event.get("type") != "assistant" or not event.get("parent_tool_use_id"):
        return False, 0
    message = event.get("message")
    content = message.get("content") if isinstance(message, dict) else []
    if not isinstance(content, list):
        return False, 0
    marker = any(part.get("text") == MARKER for part in content if isinstance(part, dict))
    reads = sum(
        part.get("name") == "Read"
        for part in content
        if isinstance(part, dict) and part.get("type") == "tool_use"
    )
    return marker, reads


def run_once(bridge: ClaudeNativeHostBridge, root: Path, timeout: int) -> dict:
    now = time.time()
    run_id = secrets.token_hex(16)
    selected = _operator_handoff(bridge, root, run_id, now)
    fixture = root / run_id / "host"
    materialized = bridge.materialize_handoff(selected, fixture, now=now)
    (fixture / "no-mcp.json").write_text('{"mcpServers":{}}', encoding="utf-8")
    command = [
        shutil.which("claude") or "claude",
        "--print",
        "--restricted",
        "--setting-sources",
        "",
        "--strict-mcp-config",
        "--mcp-config",
        str(fixture / "no-mcp.json"),
        "--agents",
        materialized["agents_path"],
        "--tools",
        "Agent,Read",
        "--allowedTools",
        "Agent,Read",
        "--permission-mode",
        "dontAsk",
        "--permission-prompts",
        "none",
        "--disable-slash-commands",
        "--no-session-persistence",
        "--output-format",
        "stream-json",
        "--verbose",
        "--forward-subagent-text",
        "Delegate exactly once to fsr_probe and ask it to follow its declared preloaded skill. "
        "Do not imitate its answer yourself.",
    ]
    capture = bridge.open_invocation(selected, now=now)
    process = subprocess.Popen(
        command,
        cwd=fixture,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        bridge.withdraw(selected.invocation_id)
        process.kill()
        process.communicate()
        return {"status": "timeout", "receipt": "not_issued"}
    marker_seen, child_reads, malformed = False, 0, 0
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            malformed += 1
            continue
        if not isinstance(event, dict):
            malformed += 1
            continue
        capture.consume(event)
        marker, reads = _child_evidence(event)
        marker_seen = marker_seen or marker
        child_reads += reads
    try:
        receipt = capture.finish(now=time.time())
        verified = bridge.verify_receipt(receipt, selected, now=time.time())
    except ValueError as error:
        return {
            "status": "native_observation_unavailable",
            "reason": str(error),
            "return_code": process.returncode,
            "stderr_bytes": len(stderr),
            "malformed_lines": malformed,
        }
    return {
        "status": "ok" if verified["verified"] else "receipt_rejected",
        "return_code": process.returncode,
        "observation": receipt["observation"],
        "native_authorization": receipt["native_authorization"],
        "permission_enforcement": receipt["permission_enforcement"],
        "preload_marker_observed": marker_seen and child_reads == 0,
        "child_read_calls": child_reads,
        "malformed_lines": malformed,
        "limitation": "marker evidence does not certify general preload or permission enforcement",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", action="store_true", help="Authorize bounded Claude calls.")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--work-dir", type=Path, default=ROOT / ".work" / "verify-claude-native")
    args = parser.parse_args()
    if not args.run:
        print(json.dumps({"status": "not_run", "hint": "pass --run to authorize the probe"}))
        return 0
    if not 1 <= args.runs <= 3 or not 1 <= args.timeout <= 120:
        raise SystemExit("--runs must be 1..3 and --timeout must be 1..120")
    if not shutil.which("claude"):
        raise SystemExit("Claude Code executable not found")
    root = args.work_dir / secrets.token_hex(8)
    root.mkdir(parents=True)
    bridge = ClaudeNativeHostBridge(
        secrets.token_bytes(32), ProtectedReceiptStore(root / "operator-receipts.json")
    )
    results = [run_once(bridge, root, args.timeout) for _ in range(args.runs)]
    print(json.dumps({"runs": results, "requested_runs": args.runs}, sort_keys=True))
    return 0 if all(result["status"] == "ok" for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
