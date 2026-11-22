"""Batch discovery must not lock the writer while preparing untrusted files."""

import json
import shutil
import sqlite3
from dataclasses import replace

import test_registry as shared

from fastskillretrieval import registry as registry_module

registry = shared.registry


def write_override(folder, **values):
    (folder / "capability.json").write_text(json.dumps(values), encoding="utf-8")


def test_file_preparation_leaves_writer_available_for_entire_batch(registry, monkeypatch):
    store, source = registry
    shutil.copytree(source / "markdown-check", source / "second")
    original = registry_module.read_package
    observed = []

    def probe(*args, **kwargs):
        other = sqlite3.connect(store.state / "registry.sqlite3", timeout=0)
        try:
            other.execute("BEGIN IMMEDIATE")
            other.rollback()
            observed.append(args[0].name)
        finally:
            other.close()
        return original(*args, **kwargs)

    monkeypatch.setattr(registry_module, "read_package", probe)
    result = store.call("discover")
    assert result["status"] == "ok", result
    assert len(observed) == 2


def test_resume_rejects_changed_directory_listing_without_skipping_source(registry):
    store, source = registry
    shutil.copytree(source / "markdown-check", source / "z-last")
    store.limits = replace(store.limits, batch=1)
    first = store.call("discover", "first")
    assert first["next_cursor"] == 1
    shutil.copytree(source / "markdown-check", source / "a-new")
    resumed = store.call("discover", "resume", cursor=first["next_cursor"])
    assert resumed["status"] == "stale_reference", resumed
    assert resumed["reason"] == "discovery_source_changed"
    assert store.call("inventory", "reader")["total"] == 1
    restarted = store.call("discover", "restart", cursor=0)
    assert restarted["status"] == "ok", restarted
    assert restarted["items"][0]["name"] == "a-new"


def test_change_during_preparation_does_not_commit_half_batch(registry, monkeypatch):
    store, source = registry
    original = registry_module.read_package
    changed = False

    def change(*args, **kwargs):
        nonlocal changed
        result = original(*args, **kwargs)
        if not changed:
            changed = True
            shutil.copytree(source / "markdown-check", source / "added-mid-scan")
        return result

    monkeypatch.setattr(registry_module, "read_package", change)
    result = store.call("discover")
    assert result["status"] == "stale_reference", result
    assert store.call("inventory", "reader")["total"] == 0


def test_completed_scan_marks_removed_sources_without_revoking_verified_snapshot(registry):
    store, source = registry
    item = shared.publish(store)[0]
    found = store.call("search", "reader", query="markdown")
    reference = found["candidates"][0]["reference"]
    shutil.rmtree(source / "markdown-check")
    assert store.call("discover", "removed-scan")["status"] == "ok"
    removed = store.call("inventory", "inventory")["items"][0]
    assert removed["id"] == item["id"]
    assert removed["state"] == "removed"
    assert removed["reason"] == "source_removed"
    assert removed["status"] == "enabled"
    assert store.call("load", "reader", reference=reference)["status"] == "ok"


def test_new_kind_does_not_reinterpret_active_published_skill(registry):
    store, source = registry
    item = shared.publish(store)[0]
    write_override(source / "markdown-check", kind="subagent", activation={"mode": "dry_run"})
    assert store.call("discover", "new-source")["status"] == "ok"
    current = store.call("inventory", "inventory")["items"][0]
    assert current["kind"] == "subagent"
    found = store.call("search", "reader", explicit_id=item["id"], kinds=["skill"])
    assert found["status"] == "ok", found
    assert found["candidates"][0]["kind"] == "skill"
    assert (
        store.call("load", "reader", reference=found["candidates"][0]["reference"])["status"]
        == "ok"
    )


def test_rollback_subagent_never_creates_legacy_skill_binding_from_current_kind(registry):
    store, source = registry
    write_override(source / "markdown-check", kind="subagent", activation={"mode": "dry_run"})
    shared.publish(store)
    first = store.active()
    (source / "markdown-check" / "capability.json").unlink()
    shared.publish(store, "new-publication")
    assert store.call("inventory", "inventory")["items"][0]["kind"] == "skill"
    assert store.call("rollback", "operator", catalog_id=first)["status"] == "ok"
    assert (
        store.db.execute("SELECT count(*) FROM legacy_refs WHERE catalog=?", (first,)).fetchone()[0]
        == 0
    )


def test_historical_workflow_keeps_step_dependencies_after_definition_kind_changes(registry):
    store, source = registry
    workflow = source / "pipeline"
    workflow.mkdir()
    candidate = shared.metadata()
    payload = {
        "kind": "workflow",
        "name": "pipeline",
        "description_original": candidate["capabilities"][0]["evidence"]["quote"],
        "steps": [{"id": "missing", "capability": "not-installed", "action": "delegate"}],
    }
    (workflow / "workflow.json").write_text(json.dumps(payload), encoding="utf-8")
    discovered = store.call("discover", "scan")
    item = next(item for item in discovered["items"] if item["name"] == "pipeline")
    candidate["capabilities"][0]["evidence"]["file"] = "workflow.json"
    assert (
        store.call(
            "submit",
            "prepare",
            capability_id=item["id"],
            source_revision=item["source_revision"],
            candidate=candidate,
        )["status"]
        == "ok"
    )
    assert store.call("publish", "publication")["status"] == "ok"
    shutil.copyfile(source / "markdown-check" / "SKILL.md", workflow / "agent.md")
    assert store.call("discover", "changed-source")["status"] == "ok"
    result = store.call("search", "reader", explicit_id=item["id"], kinds=["workflow"])
    assert result["status"] == "dependency_unavailable", result
    assert result["blocked"][0]["reason"] == "required_capability_unavailable"


def test_alias_cannot_drop_its_own_required_capability(registry):
    store, source = registry
    target = shared.publish(store)[0]
    alias = source / "alias"
    shutil.copytree(source / "markdown-check", alias)
    write_override(
        alias,
        kind="command",
        activation={"mode": "alias"},
        alias_of=target["id"],
        requires=["missing-required"],
    )
    published = shared.publish(store, "alias-publication")
    alias_id = next(item["id"] for item in published if item["name"] == "alias")
    found = store.call("search", "reader", explicit_id=alias_id, mode="prepare_use")
    assert found["status"] == "unsupported_integration", found
    assert found["candidates"] == []
