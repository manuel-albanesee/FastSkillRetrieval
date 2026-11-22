import json
import os
import shutil
from contextlib import contextmanager
from pathlib import Path

import pytest

from fastskillretrieval.host_bridge import ClaudeNativeHostBridge, ProtectedReceiptStore
from fastskillretrieval.native_integration import NativeRegistryCoordinator
from fastskillretrieval.packages import digest
from fastskillretrieval.registry import Registry

ROOT = Path(__file__).resolve().parents[1]


def capability_id(source, name):
    return digest(f"{os.path.normcase(str(source))}/{name}".encode())[:24]


def candidate():
    return json.loads((ROOT / "examples" / "markdown-candidate.json").read_text(encoding="utf-8"))


@contextmanager
def published_subagent(tmp_path):
    source = tmp_path / "sources"
    shutil.copytree(ROOT / "examples" / "skills", source)
    preload = source / "plain-preload"
    preload.mkdir()
    (preload / "SKILL.md").write_text(
        "---\nname: plain-preload\ndescription: harmless native preload\n---\n"
        "Return the declared marker.\n",
        encoding="utf-8",
    )
    agent = source / "reviewer"
    agent.mkdir()
    (agent / "agent.md").write_text("# Reviewer\n", encoding="utf-8")
    (agent / "capability.json").write_text(
        json.dumps({"kind": "subagent", "preloads": [capability_id(source, "plain-preload")]}),
        encoding="utf-8",
    )
    store = Registry(tmp_path / "state", source)
    try:
        discovered = store.call("discover", "setup")
        assert discovered["status"] == "ok"
        for item in discovered["items"]:
            metadata = candidate()
            if item["kind"] == "subagent":
                metadata["capabilities"][0]["evidence"]["file"] = "agent.md"
                metadata["capabilities"][0]["evidence"]["quote"] = "# Reviewer"
            elif item["name"] == "plain-preload":
                metadata["capabilities"][0]["evidence"]["quote"] = "Return the declared marker."
            assert (
                store.call(
                    "submit",
                    "setup",
                    capability_id=item["id"],
                    source_revision=item["source_revision"],
                    candidate=metadata,
                )["status"]
                == "ok"
            )
        assert store.call("publish", "setup")["status"] == "ok"
        identity = capability_id(source, "reviewer")
        row = store.db.execute(
            "SELECT * FROM entries WHERE capability_id=?", (identity,)
        ).fetchone()
        assert row is not None
        planned = store.call("plan", "reader", reference=store._ref(row))
        assert planned["status"] == "ok", planned
        yield store, planned["plan_id"], identity
    finally:
        store.close()


def policy(capability, source):
    return {
        "runtime": "claude-code",
        "version": "2.1.302",
        "surface": "cli",
        "root_fingerprint": digest(os.path.normcase(str(source)).encode()),
        "config_fingerprint": "2" * 64,
        "expires_at": 200,
        "bindings": {
            capability: {
                "native_id": "fsr_probe",
                "source_body_digest": digest((source / "reviewer" / "agent.md").read_bytes()),
                "agent_definition": {
                    "description": "Harmless native probe.",
                    "prompt": "Return the marker.",
                    "tools": ["Read"],
                },
            }
        },
    }


def test_operator_coordinator_revalidates_plan_and_returns_only_a_host_managed_handoff(tmp_path):
    with published_subagent(tmp_path) as (store, plan_id, capability):
        bridge = ClaudeNativeHostBridge(
            b"b" * 32, ProtectedReceiptStore(tmp_path / "receipts.json")
        )
        coordinator = NativeRegistryCoordinator(store, bridge)

        handoff = coordinator.prepare(plan_id, "reader", policy(capability, store.root), now=100)

        assert handoff.native_id == "fsr_probe"
        assert handoff.capability_reference["capability_id"] == capability
        assert set(handoff.preloads) == {"plain-preload"}
        materialized = bridge.materialize_handoff(handoff, tmp_path / "host-fixture", now=100)
        assert materialized["handoff_available"] is True
        assert materialized["native_authorization"] == "host_managed"
        assert materialized["activation_allowed"] is False


def test_operator_coordinator_rejects_changed_status_or_unbound_agent(tmp_path):
    with published_subagent(tmp_path) as (store, plan_id, capability):
        bridge = ClaudeNativeHostBridge(
            b"b" * 32, ProtectedReceiptStore(tmp_path / "receipts.json")
        )
        coordinator = NativeRegistryCoordinator(store, bridge)
        with pytest.raises(ValueError, match="native_binding_missing"):
            coordinator.prepare(
                plan_id, "reader", {**policy(capability, store.root), "bindings": {}}, now=100
            )

        assert (
            store.call("set_status", "operator", capability_id=capability, status="quarantined")[
                "status"
            ]
            == "ok"
        )
        with pytest.raises(Exception, match="capability_not_enabled"):
            coordinator.prepare(plan_id, "reader", policy(capability, store.root), now=100)
