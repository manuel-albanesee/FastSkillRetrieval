"""Bounded publication keeps the current catalog usable until activation."""

import json
import shutil
from pathlib import Path

import pytest

from fastskillretrieval.registry import Registry

ROOT = Path(__file__).resolve().parents[1]


def candidate():
    return json.loads((ROOT / "examples" / "markdown-candidate.json").read_text())


def add_skills(source, count):
    template = source / "markdown-check"
    for index in range(count):
        shutil.copytree(template, source / f"batch-{index:03d}")


def discover_and_submit(store):
    discovered = store.call("discover", "discovery")
    assert discovered["status"] == "ok", discovered
    for item in discovered["items"]:
        if item["state"] == "discovered":
            submitted = store.call(
                "submit",
                "submission",
                capability_id=item["id"],
                source_revision=item["source_revision"],
                candidate=candidate(),
            )
            assert submitted["status"] == "ok", submitted


@pytest.fixture
def registry(tmp_path):
    source = tmp_path / "sources"
    shutil.copytree(ROOT / "examples" / "skills", source)
    store = Registry(tmp_path / "registry", source)
    yield store, source
    store.close()


def test_publication_activates_only_after_all_bounded_snapshot_batches(registry):
    store, source = registry
    add_skills(source, 2)
    discover_and_submit(store)

    first = store.call("publish", "operator-one", batch_size=1)
    assert first["status"] == "in_progress", first
    assert first["remaining"] == 2
    assert store.active() == 0
    assert store.call("search", "reader", query="markdown")["status"] == "catalog_not_ready"

    second = store.call("publish", "operator-two", batch_size=1)
    assert second["status"] == "in_progress", second
    assert second["remaining"] == 1
    assert store.active() == 0

    completed = store.call("publish", "operator-three", batch_size=1)
    assert completed["status"] == "ok", completed
    assert completed["published"] == 3
    assert store.active() == completed["catalog_id"]


def test_interrupted_publication_keeps_previous_catalog_and_resumes(registry, monkeypatch):
    store, source = registry
    discover_and_submit(store)
    baseline = store.call("publish", "baseline")
    assert baseline["status"] == "ok", baseline
    active = store.active()

    add_skills(source, 1)
    discover_and_submit(store)
    original = store._snapshot
    calls = 0

    def interrupted(identity, revision):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("simulated interrupted snapshot")
        return original(identity, revision)

    monkeypatch.setattr(store, "_snapshot", interrupted)
    failed = store.call("publish", "operator-failure", batch_size=1)
    assert failed["status"] == "catalog_unavailable", failed
    assert store.active() == active

    monkeypatch.setattr(store, "_snapshot", original)
    resumed = store.call("publish", "operator-resume", batch_size=1)
    assert resumed["status"] == "ok", resumed
    assert resumed["catalog_id"] != active
    assert resumed["published"] == 2


def test_publication_deadline_never_activates_a_partial_catalog(registry, monkeypatch):
    store, source = registry
    discover_and_submit(store)
    baseline = store.call("publish", "baseline")
    assert baseline["status"] == "ok", baseline
    active = store.active()

    add_skills(source, 1)
    discover_and_submit(store)
    ticks = iter((0.0, 0.0, 0.002))
    monkeypatch.setattr("fastskillretrieval.registry.time.monotonic", lambda: next(ticks))
    timed_out = store.call("publish", "operator-timeout", batch_size=1, deadline_ms=1)
    assert timed_out == {"status": "timeout", "reason": "operation_deadline"}
    assert store.active() == active


def test_publication_restarts_stage_when_descriptor_changes(registry):
    store, source = registry
    add_skills(source, 2)
    discover_and_submit(store)
    first = store.call("publish", "operator-first", batch_size=1)
    assert first["status"] == "in_progress", first
    first_cursor = first["cursor"]

    selected = store.call("inventory", "operator-inventory", limit=100)["items"][0]
    revised = candidate()
    revised["description"] += " Revised before the staged catalog could activate."
    submitted = store.call(
        "submit",
        "operator-revision",
        capability_id=selected["id"],
        source_revision=selected["source_revision"],
        candidate=revised,
    )
    assert submitted["status"] == "ok", submitted

    restarted = store.call("publish", "operator-restart", batch_size=1)
    assert restarted["status"] == "in_progress", restarted
    assert restarted["cursor"] != first_cursor
    assert store.active() == 0

    completed = store.call("publish", "operator-complete", batch_size=100)
    assert completed["status"] == "ok", completed
    descriptor = store.db.execute(
        "SELECT descriptor_revision FROM entries WHERE catalog_id=? AND capability_id=?",
        (completed["catalog_id"], selected["id"]),
    ).fetchone()[0]
    assert descriptor == submitted["descriptor_revision"]


def test_unchanged_publication_does_not_rehash_existing_snapshots(registry, monkeypatch):
    store, _ = registry
    discover_and_submit(store)
    assert store.call("publish", "initial")["status"] == "ok"

    def must_not_snapshot(*_args):
        raise AssertionError(
            "unchanged snapshot should not be copied or rehashed during publication"
        )

    monkeypatch.setattr(store, "_snapshot", must_not_snapshot)
    unchanged = store.call("publish", "unchanged", batch_size=1)
    assert unchanged["status"] == "ok", unchanged
    assert unchanged["changed"] is False
