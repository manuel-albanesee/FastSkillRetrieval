import json
import os
import shutil
import time
from dataclasses import replace

import pytest
import test_registry as shared

from fastskillretrieval.contracts import cost

registry = shared.registry
metadata = shared.metadata
publish = shared.publish


def test_cached_result_is_not_usable_after_quarantine(registry):
    store, _ = registry
    item = publish(store)[0]
    assert store.call("search", "user", request_id="cached", query="markdown")["status"] == "ok"
    store.call("set_status", "operator", capability_id=item["id"], status="quarantined")
    assert (
        store.call("search", "user", request_id="cached", query="markdown")["status"]
        == "policy_denied"
    )


def test_quarantine_cannot_be_laundered_via_other_status(registry):
    store, _ = registry
    item = publish(store)[0]
    store.call("set_status", "operator", capability_id=item["id"], status="quarantined")
    transition = store.call("set_status", "operator", capability_id=item["id"], status="disabled")
    assert transition["status"] == "policy_denied"


def test_retention_cannot_recount_expired_requests(registry):
    store, _ = registry
    publish(store)
    store.call("search", "user", request_id="expired", query="markdown")
    store.db.execute("UPDATE request_ledger SET created=?", (time.time() - 31 * 86400,))
    store.db.commit()
    assert store.call("reconcile", "maintenance")["status"] == "ok"
    assert (
        store.call("search", "user", request_id="expired", query="markdown")["status"]
        == "request_expired"
    )


def test_kind_filter_applies_to_canonical_alias(registry):
    store, source = registry
    target = publish(store)[0]["id"]
    directory = source / "command-alias"
    shutil.copytree(source / "markdown-check", directory)
    (directory / "capability.json").write_text(
        json.dumps(
            {
                "kind": "command",
                "activation": {"mode": "alias"},
                "alias_of": target,
            }
        )
    )
    publish(store, "prepare-alias")
    result = store.call(
        "search", "user", request_id="kind-filter", query="markdown", kinds=["command"]
    )
    assert result["status"] == "no_match", result


def test_oversized_card_does_not_hide_next_valid_candidate(registry):
    store, source = registry
    shutil.copytree(source / "markdown-check", source / "long-description")
    items = store.call("discover")["items"]
    for item in items:
        candidate = metadata()
        if item["name"] == "long-description":
            candidate["description"] = "markdown " * 100
        assert (
            store.call(
                "submit",
                "setup",
                capability_id=item["id"],
                source_revision=item["source_revision"],
                candidate=candidate,
            )["status"]
            == "ok"
        )
    store.call("publish", "setup")
    # The default envelope now fits both cards; pin the former one to keep the oversize case.
    store.limits = replace(store.limits, cards=1200)
    result = store.call("search", "user", request_id="card-budget", query="markdown")
    assert result["status"] == "ok", result
    assert len(result["candidates"]) == 1


def test_legacy_kind_filter_precedes_pagination(registry):
    store, _ = registry
    publish(store)
    result = store.call("inventory", kinds=["subagent"], offset=0, limit=1)
    assert result["status"] == "ok", result
    assert result["total"] == 0
    assert result["items"] == []


def test_dependency_resource_cannot_escape_its_load_group_budget(registry):
    store, source = registry
    store.limits = replace(store.limits, load=4000)
    dependency = publish(store)[0]["id"]
    shutil.copytree(source / "markdown-check", source / "dependent")
    (source / "dependent/capability.json").write_text(
        json.dumps(
            {
                "kind": "skill",
                "requires": [dependency],
            }
        )
    )
    items = publish(store, "prepare-dependent")
    root = next(item for item in items if item["name"] == "dependent")
    root_ref = store.call("search", "user", request_id="root", explicit_id=root["id"])[
        "candidates"
    ][0]["reference"]
    result = store.call("load", "user", reference=root_ref)
    assert result["status"] == "ok", result
    emitted = cost(result)
    dependency_ref = result["dependencies"][0]["reference"]
    for _ in range(20):
        result = store.call(
            "resource", "user", reference=dependency_ref, file="references/checklist.md"
        )
        if result["status"] != "ok":
            assert result["status"] == "budget_exceeded"
            break
        emitted += cost(result)
    assert emitted <= store.limits.load


def test_transient_windows_snapshot_rename_can_recover(registry, monkeypatch):
    from fastskillretrieval import registry as module

    store, _ = registry
    original = module.os.replace
    attempted = 0

    def locked_once(source, destination):
        nonlocal attempted
        attempted += 1
        if attempted == 1:
            error = PermissionError("temporary Windows sharing violation")
            error.winerror = 32
            raise error
        return original(source, destination)

    monkeypatch.setattr(module.os, "replace", locked_once)
    publish(store)
    assert attempted >= 2


def test_new_parent_group_includes_previously_loaded_dependency(registry):
    store, source = registry
    store.limits = replace(store.limits, load=4000)
    dependency = publish(store)[0]["id"]
    shutil.copytree(source / "markdown-check", source / "parent")
    (source / "parent/capability.json").write_text(
        json.dumps({"kind": "skill", "requires": [dependency]})
    )
    items = publish(store, "prepare-parent")
    parent = next(item["id"] for item in items if item["name"] == "parent")

    def reference(identity, request):
        return store.call("search", "user", request_id=request, explicit_id=identity)["candidates"][
            0
        ]["reference"]

    dependency_ref = reference(dependency, "child")
    parent_ref = reference(parent, "parent")
    first = store.call("load", "user", reference=dependency_ref)
    assert first["status"] == "ok", first
    second = store.call("load", "user", reference=parent_ref)
    assert second["status"] == "ok", second
    emitted = cost(first) + cost(second)
    for _ in range(20):
        response = store.call(
            "resource", "user", reference=parent_ref, file="references/checklist.md"
        )
        if response["status"] != "ok":
            assert response["status"] == "budget_exceeded", response
            break
        emitted += cost(response)
    assert emitted <= store.limits.load


@pytest.mark.skipif(os.name != "nt", reason="MAX_PATH applies to Windows only")
def test_snapshot_fails_explicitly_when_state_path_is_too_long(tmp_path):
    from fastskillretrieval.registry import Registry

    source = tmp_path / "sources"
    shutil.copytree(shared.ROOT / "examples/skills", source)
    store = Registry(tmp_path / ("d" * 110) / "registry", source)
    try:
        inventory = store.call("discover", "setup")
        for item in inventory["items"]:
            store.call(
                "submit",
                "setup",
                capability_id=item["id"],
                source_revision=item["source_revision"],
                candidate=metadata(),
            )
        result = store.call("publish", "setup")
        assert result["status"] == "policy_denied"
        assert result["reason"] == "state_path_too_long"
    finally:
        store.close()
