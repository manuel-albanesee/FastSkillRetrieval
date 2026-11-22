"""Acceptance scenarios for dependency plans and snapshot revalidation."""

import json
import os
import shutil
from dataclasses import replace

import pytest
import test_registry as shared

from fastskillretrieval.packages import digest
from fastskillretrieval.registry import Registry

registry = shared.registry
metadata = shared.metadata
publish = shared.publish


def identity(source, name):
    return digest(f"{os.path.normcase(str(source))}/{name}".encode())[:24]


def overlay(folder, **relations):
    (folder / "capability.json").write_text(json.dumps(relations), encoding="utf-8")


def prepare_all(store):
    discovered = store.call("discover", "scan")
    assert discovered["status"] == "ok", discovered
    offset = 0
    while True:
        page = store.call("inventory", f"page-{offset}", offset=offset)
        assert page["status"] == "ok", page
        for item in page["items"]:
            candidate = metadata()
            if item["kind"] == "subagent":
                for capability in candidate["capabilities"]:
                    capability["evidence"]["file"] = "agent.md"
            result = store.call(
                "submit",
                f"prep-{item['id']}",
                capability_id=item["id"],
                source_revision=item["source_revision"],
                candidate=candidate,
            )
            assert result["status"] == "ok", result
        offset += len(page["items"])
        if offset >= page["total"]:
            break
    published = store.call("publish", "publication")
    assert published["status"] == "ok", published


def test_required_cycle_is_blocked_before_retrieved(registry):
    store, source = registry
    first, second = source / "markdown-check", source / "second"
    shutil.copytree(first, second)
    overlay(first, requires=[identity(source, second.name)])
    overlay(second, requires=[identity(source, first.name)])
    prepare_all(store)
    result = store.call(
        "search", "reader", explicit_id=identity(source, first.name), request_id="cycle"
    )
    assert result["status"] == "dependency_unavailable", result
    assert result["candidates"] == []
    assert result["blocked"][0]["reason"] == "required_cycle"
    assert store.db.execute("SELECT count(*) FROM events WHERE name='retrieved'").fetchone()[0] == 0


@pytest.mark.parametrize("count,expected", [(32, "ok"), (33, "dependency_unavailable")])
def test_dependency_limit_keeps_boundary_explicit(registry, count, expected):
    store, source = registry
    store.limits = replace(store.limits, load=200000, period=300000)
    template = source / "markdown-check"
    names = [template.name, *(f"step-{index}" for index in range(1, count))]
    for name in names[1:]:
        shutil.copytree(template, source / name)
    for current, following in zip(names, names[1:], strict=False):
        overlay(source / current, requires=[identity(source, following)])
    prepare_all(store)
    result = store.call(
        "search", "reader", explicit_id=identity(source, names[0]), request_id="boundary"
    )
    assert result["status"] == expected, result
    if count == 33:
        assert result["blocked"][0]["reason"] == "dependency_limit_32"


def test_combined_plan_rejects_conflicts_between_individually_valid_roots(registry):
    store, source = registry
    first, second = source / "markdown-check", source / "second"
    shutil.copytree(first, second)
    overlay(first, conflicts_with=[identity(source, second.name)])
    prepare_all(store)
    references = []
    for name in (first.name, second.name):
        result = store.call("search", f"read-{name}", explicit_id=identity(source, name))
        assert result["status"] == "ok", result
        references.append(result["candidates"][0]["reference"])
    result = store.call("plan", "planner", references=references)
    assert result["status"] == "conflict_detected", result


def test_new_source_keeps_old_snapshot_but_does_not_repin_session(registry):
    store, source = registry
    publish(store)
    old = store.call("search", "reader", query="markdown")["candidates"][0]["reference"]
    loaded = store.call("load", "reader", reference=old)
    assert loaded["status"] == "ok"
    path = source / "markdown-check" / "SKILL.md"
    path.write_text(path.read_text(encoding="utf-8") + "\nNew source revision.\n", encoding="utf-8")
    publish(store, "new-publication")
    new = store.call("search", "other-reader", query="markdown")["candidates"][0]["reference"]
    assert old["source_revision"] != new["source_revision"]
    result = store.call("load", "reader", reference=new)
    assert result["status"] == "stale_reference", result
    assert store.call("load", "historical-reader", reference=old)["content"] == loaded["content"]


def test_snapshot_tampering_after_plan_blocks_prepare(registry):
    store, _ = registry
    publish(store)
    reference = store.call("search", "reader", query="markdown")["candidates"][0]["reference"]
    planned = store.call("plan", "reader", reference=reference)
    assert planned["status"] == "ok", planned
    snapshot = (
        store.state
        / "packages"
        / reference["capability_id"]
        / reference["source_revision"]
        / "SKILL.md"
    )
    snapshot.write_text("Tampered instructions", encoding="utf-8")
    result = store.call("prepare", "reader", plan_id=planned["plan_id"])
    assert result["status"] == "stale_reference", result


def test_subagent_is_discoverable_but_cannot_be_loaded_as_skill(registry):
    store, source = registry
    agent = source / "reviewer"
    agent.mkdir()
    body = (source / "markdown-check" / "SKILL.md").read_text(encoding="utf-8")
    (agent / "agent.md").write_text(body, encoding="utf-8")
    prepare_all(store)
    found = store.call(
        "search", "reader", explicit_id=identity(source, agent.name), kinds=["subagent"]
    )
    assert found["status"] == "ok", found
    reference = found["candidates"][0]["reference"]
    planned = store.call("plan", "reader", reference=reference)
    assert planned["dry_run"] is True
    assert planned["actions"][0]["activation_allowed"] is False
    assert planned["actions"][0]["action"] == "delegate"
    assert store.call("load", "reader", reference=reference)["status"] == "unsupported_integration"
    assert (
        store.call("prepare", "reader", plan_id=planned["plan_id"])["status"]
        == "unsupported_integration"
    )


def test_cross_root_reference_cannot_address_matching_catalog_numbers(registry, tmp_path):
    store, source = registry
    publish(store)
    reference = store.call("search", "reader", query="markdown")["candidates"][0]["reference"]
    other_source = tmp_path / "other-sources"
    shutil.copytree(source, other_source)
    other = Registry(tmp_path / "other-registry", other_source)
    try:
        publish(other)
        refused = other.call("load", "reader", reference=reference)
        assert refused["status"] == "stale_reference", refused
        assert refused["reason"] == "invalid_reference_scope"
    finally:
        other.close()
