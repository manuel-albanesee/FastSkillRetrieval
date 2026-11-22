"""Acceptance tests for the capability registry (plan CR01–CR15)."""

import json
import shutil
from dataclasses import replace
from pathlib import Path

import pytest

from fastskillretrieval.contracts import Failure, Limits

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def registry(tmp_path):
    from fastskillretrieval.registry import Registry

    source = tmp_path / "sources"
    shutil.copytree(ROOT / "examples/skills", source)
    result = Registry(tmp_path / "registry", source)
    yield result, source
    result.close()


def metadata():
    return json.loads((ROOT / "examples/markdown-candidate.json").read_text())


def publish(registry, session="setup"):
    inventory = registry.call("discover", session)
    assert inventory["status"] == "ok", inventory
    for item in inventory["items"]:
        if item["state"] == "discovered":
            result = registry.call(
                "submit",
                session,
                capability_id=item["id"],
                source_revision=item["source_revision"],
                candidate=metadata(),
            )
            assert result["status"] == "ok", result
    result = registry.call("publish", session)
    assert result["status"] == "ok", result
    return inventory["items"]


def test_registry_versioned_reference_and_whole_resource_plan(registry):
    store, _ = registry
    item = publish(store)[0]
    result = store.call("search", "user", request_id="one", query="markdown")
    assert result["status"] == "ok", result
    reference = result["candidates"][0]["reference"]
    assert reference["schema_version"] == 2
    assert reference["capability_id"] == item["id"]
    assert reference["descriptor_revision"] != ""
    assert store.call("load", "user", reference=reference)["status"] == "ok"
    resource = store.call("resource", "user", reference=reference, file="references/checklist.md")
    assert "FSR-MARKDOWN-CHECK-01" in resource["content"]
    assert store.db.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert store.db.execute("PRAGMA user_version").fetchone()[0] == 2


def test_request_idempotency_and_no_query_persistence(registry):
    store, _ = registry
    publish(store)
    query = "markdown headings query-secret-unique-123"
    first = store.call("search", "user", request_id="same", query=query)
    spent = store.db.execute("SELECT spent FROM sessions WHERE id='user'").fetchone()[0]
    second = store.call("search", "user", request_id="same", query=query)
    assert second == first
    assert (
        store.db.execute("SELECT SUM(value) FROM daily_counters WHERE name='retrieved'").fetchone()[
            0
        ]
        == 1
    )
    assert store.db.execute("SELECT spent FROM sessions WHERE id='user'").fetchone()[0] > spent
    conflict = store.call("search", "user", request_id="same", query="different")
    assert conflict["status"] == "request_conflict"
    assert query not in "\n".join(store.db.iterdump())


def test_blocked_candidates_do_not_exclude_sixth_valid(registry):
    store, source = registry
    # Isolate dependency selection from the separate card-envelope budget test.
    store.limits = replace(store.limits, cards=4000)
    for index in range(6):
        directory = source / f"clone-{index}"
        shutil.copytree(source / "markdown-check", directory)
        if index < 5:
            (directory / "capability.json").write_text(
                json.dumps(
                    {
                        "kind": "skill",
                        "requires": ["missing-required"],
                    }
                )
            )
    publish(store)
    result = store.call("search", "user", request_id="closure", query="markdown")
    assert result["status"] == "ok", result
    assert len(result["candidates"]) == 2
    assert len(result["blocked"]) == 5


def test_snapshot_tamper_and_quarantine_block_old_references(registry):
    store, _ = registry
    item = publish(store)[0]
    reference = store.call("search", "user", request_id="one", query="markdown")["candidates"][0][
        "reference"
    ]
    assert (
        store.call("set_status", "operator", capability_id=item["id"], status="quarantined")[
            "status"
        ]
        == "ok"
    )
    assert store.call("load", "user", reference=reference)["status"] == "policy_denied"
    assert store.call("rollback", "operator", catalog_id=reference["catalog_id"])["status"] == "ok"
    assert store.call("load", "user", reference=reference)["status"] == "policy_denied"


def test_attempts_are_persistent_and_scoped_to_rules(registry):
    store, _ = registry
    item = store.call("discover")["items"][0]
    args = {
        "capability_id": item["id"],
        "source_revision": item["source_revision"],
        "candidate": {},
    }
    assert store.call("submit", "a", **args)["attempts"] == 1
    assert store.call("submit", "b", **args)["attempts"] == 2
    assert store.call("submit", "c", **args)["status"] == "limit_exceeded"
    assert store.call("inventory")["items"][0]["state"] == "needs_revision"


def test_discovery_paginates_new_versions_without_losing_prior_items(registry):
    store, source = registry
    for index in range(3):
        shutil.copytree(source / "markdown-check", source / f"copy-{index}")
    store.limits = replace(Limits(), batch=2)
    first = store.call("discover", "first")
    assert first["next_cursor"] is not None
    second = store.call("discover", "second", cursor=first["next_cursor"])
    assert second["next_cursor"] is None
    assert store.call("inventory")["total"] == 4


def test_unknown_schema_is_not_silently_upgraded(registry):
    from fastskillretrieval.registry import Registry

    store, source = registry
    store.db.execute("PRAGMA user_version=99")
    store.db.commit()
    with pytest.raises(Failure, match="schema"):
        Registry(store.state, source)


def test_reconcile_is_explicit_and_idempotent(registry):
    store, _ = registry
    publish(store)
    first = store.call("reconcile", "maintenance")
    second = store.call("reconcile", "maintenance")
    assert first["status"] == second["status"] == "ok"
    assert not second["changed"]
    assert second["automatic_inference"] is False
