"""P3 ingestion guarantees for batched, immutable discovery."""

import shutil
from dataclasses import replace

import test_registry as shared

registry = shared.registry


def test_resume_rejects_a_changed_listing_before_writing_the_next_batch(registry):
    store, source = registry
    shutil.copytree(source / "markdown-check", source / "z-last")
    store.limits = replace(store.limits, batch=1)

    first = store.call("discover", "first")
    assert first["next_cursor"] == 1
    shutil.copytree(source / "markdown-check", source / "a-new")

    resumed = store.call("discover", "resume", cursor=first["next_cursor"])
    assert resumed == {"status": "stale_reference", "reason": "discovery_source_changed"}
    assert store.call("inventory", "reader")["total"] == 1


def test_completed_scan_marks_missing_sources_removed_but_keeps_snapshot_loadable(registry):
    store, source = registry
    item = shared.publish(store)[0]
    reference = store.call("search", "reader", query="markdown")["candidates"][0]["reference"]

    shutil.rmtree(source / "markdown-check")
    assert store.call("discover", "removed-scan")["status"] == "ok"

    removed = store.call("inventory", "inventory")["items"][0]
    assert (removed["id"], removed["state"], removed["reason"]) == (
        item["id"],
        "removed",
        "source_removed",
    )
    assert store.call("load", "reader", reference=reference)["status"] == "ok"
