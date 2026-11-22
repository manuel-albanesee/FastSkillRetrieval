"""Manual maintenance findings, bounded export and concurrency safeguards."""

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier

import test_registry as shared

from fastskillretrieval.contracts import cost
from fastskillretrieval.registry import Registry

registry = shared.registry
publish = shared.publish


def test_manual_findings_show_denominator_without_claiming_utility(registry):
    store, _ = registry
    identity = publish(store)[0]["id"]
    for index in range(20):
        assert (
            store.call("search", f"empty-{index}", query="unrelated", request_id="request")[
                "status"
            ]
            == "no_match"
        )
    assert store.call("reconcile", "maintenance")["status"] == "ok"
    report = store.call("export", "operator", offset=0, limit=10)
    assert report["status"] == "ok", report
    item = next(item for item in report["items"] if item["capability_id"] == identity)
    assert item["retrieved"] == 0
    assert item["search_responses_since_admission"] == 20
    assert item["admitted_at"] > 0
    assert "low_retrieval_review_only" in item["findings"]
    assert "utility" not in item
    assert store._cap(identity)["status"] == "enabled"
    assert "unrelated" not in json.dumps(report)


def test_two_connections_cannot_emit_more_than_shared_load_budget(registry):
    store, source = registry
    publish(store)
    reference = store.call("search", "reader", query="markdown", request_id="reference")[
        "candidates"
    ][0]["reference"]
    barrier = Barrier(2)
    limits = replace(store.limits, load=1700)

    def load():
        connection = Registry(store.state, source, limits)
        try:
            barrier.wait(timeout=10)
            return connection.call("load", "reader", reference=reference)
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: load(), range(2)))
    successful = [item for item in responses if item["status"] == "ok"]
    assert all(
        item["status"] in {"ok", "budget_exceeded", "catalog_unavailable"} for item in responses
    ), responses
    # SQLite may refuse both deferred lock upgrades. A serial retry must still
    # work; lock refusals cannot emit content or consume a successful load slot.
    if not successful:
        store.limits = limits
        retry = store.call("load", "reader", reference=reference)
        assert retry["status"] == "ok", retry
        successful.append(retry)
    # Admission is a byte cap, not a one-emission quota. Once writer upgrades are
    # serialized, both calls may legitimately fit; exercise the remaining capacity
    # and require the first payload that does not fit to be rejected.
    store.limits = limits
    for _ in range(10):
        response = store.call("load", "reader", reference=reference)
        if response["status"] != "ok":
            assert response["status"] == "budget_exceeded", response
            break
        successful.append(response)
    else:
        raise AssertionError("repeated emissions did not exhaust the shared byte cap")
    assert sum(cost(item) for item in successful) <= limits.load, responses


def test_unknown_schema_shape_is_rejected_even_with_known_version(registry):
    import pytest

    from fastskillretrieval.contracts import Failure

    store, source = registry
    store.db.execute("DROP TABLE content_charges")
    store.db.commit()
    with pytest.raises(Failure, match="schema"):
        Registry(store.state, source)


def test_retention_bounds_utf8_payloads_and_plans_together(registry, monkeypatch):
    from fastskillretrieval import registry as module

    store, _ = registry
    publish(store)
    for index in range(3):
        found = store.call("search", f"reader-{index}", query="markdown", request_id="one")
        reference = found["candidates"][0]["reference"]
        assert store.call("plan", f"reader-{index}", reference=reference)["status"] == "ok"
    assert store._detail_bytes() > 500
    monkeypatch.setattr(module, "DETAIL_LIMIT", 500)
    before = store._counter_total("retrieved")
    assert store.call("reconcile", "maintenance")["status"] == "ok"
    assert store._detail_bytes() <= 500
    assert store._counter_total("retrieved") == before
    assert (
        store.call("search", "reader-0", query="markdown", request_id="one")["status"]
        == "request_expired"
    )
