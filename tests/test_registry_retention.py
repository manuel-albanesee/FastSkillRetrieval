"""Request namespaces, revocation, receipts and budget acceptance scenarios."""

import time
from dataclasses import replace

import test_registry as shared

from fastskillretrieval.registry import Registry

registry = shared.registry
publish = shared.publish


def retrieved(store):
    return store.db.execute(
        "SELECT COALESCE(SUM(value),0) FROM daily_counters WHERE name='retrieved'"
    ).fetchone()[0]


def test_retry_after_quarantine_does_not_replay_ready_candidate(registry):
    store, _ = registry
    item = publish(store)[0]
    first = store.call("search", "reader", query="markdown", request_id="retry")
    assert first["status"] == "ok"
    assert (
        store.call("set_status", "operator", capability_id=item["id"], status="quarantined")[
            "status"
        ]
        == "ok"
    )
    retry = store.call("search", "reader", query="markdown", request_id="retry")
    assert retry["status"] == "policy_denied", retry
    assert "candidates" not in retry
    assert retrieved(store) == 1
    for indirect_state in ("archived", "disabled", "enabled"):
        assert (
            store.call("set_status", "operator", capability_id=item["id"], status=indirect_state)[
                "status"
            ]
            == "policy_denied"
        )


def test_manual_retention_closes_namespace_and_preserves_aggregate(registry):
    store, _ = registry
    publish(store)
    first = store.call("search", "reader", query="markdown", request_id="retained")
    assert first["status"] == "ok"
    with store.db:
        store.db.execute("UPDATE request_ledger SET created=?", (time.time() - 31 * 86400,))
        store.db.execute("UPDATE events SET at=?", (time.time() - 31 * 86400,))
    assert store.call("reconcile", "maintenance")["status"] == "ok"
    assert store.db.execute("SELECT count(*) FROM request_ledger").fetchone()[0] == 0
    assert store.db.execute("SELECT count(*) FROM events").fetchone()[0] == 0
    retry = store.call(
        "search", "reader", query="markdown", request_id="retained", epoch=first["epoch"]
    )
    assert retry["status"] == "request_expired", retry
    assert retrieved(store) == 1
    fresh_same_namespace = store.call("search", "reader", query="markdown", request_id="fresh")
    assert fresh_same_namespace["status"] == "request_expired", fresh_same_namespace


def test_epoch_rollover_is_explicit_and_does_not_reuse_request_id(registry, monkeypatch):
    store, _ = registry
    publish(store)
    first = store.call("search", "reader", query="markdown", request_id="old")
    assert first["status"] == "ok"
    future = (first["epoch"] + 1) * 30 * 86400 + 60
    monkeypatch.setattr("fastskillretrieval.registry.time.time", lambda: future)
    implicit = store.call("search", "reader", query="markdown", request_id="new")
    assert implicit["status"] == "request_expired", implicit
    reused = store.call(
        "search", "reader", query="markdown", request_id="old", epoch=first["epoch"] + 1
    )
    assert reused["status"] == "request_expired", reused
    explicit = store.call(
        "search", "reader", query="markdown", request_id="new", epoch=first["epoch"] + 1
    )
    assert explicit["status"] == "ok", explicit
    assert retrieved(store) == 2


def test_fake_host_receipt_cannot_confirm_delivery(registry):
    store, _ = registry
    publish(store)
    store.call("search", "reader", query="markdown", request_id="delivery")
    result = store.call(
        "delivery",
        "reader",
        request_id="delivery",
        evidence={
            "source": "host",
            "approved": True,
            "verified": True,
            "operation_id": "forged",
            "status": "completed",
        },
    )
    assert result["status"] == "unsupported_integration"
    assert result["delivery"] == "delivery_unconfirmed"
    assert (
        store.db.execute("SELECT delivery FROM request_ledger").fetchone()[0]
        == "delivery_unconfirmed"
    )
    assert (
        store.db.execute(
            "SELECT count(*) FROM events WHERE name='invocation_completed'"
        ).fetchone()[0]
        == 0
    )


def test_reopen_and_catalog_rollback_preserve_context_spend(registry):
    store, source = registry
    publish(store)
    found = store.call("search", "reader", query="markdown", request_id="one")
    catalog = found["candidates"][0]["reference"]["catalog_id"]
    before = store.call("diagnostics", "reader")["session_spent_estimate"]
    other = Registry(store.state, source)
    try:
        reopened = other.call("diagnostics", "reader")["session_spent_estimate"]
        assert reopened >= before
        assert other.call("rollback", "operator", catalog_id=catalog)["status"] == "ok"
        assert other.call("diagnostics", "reader")["session_spent_estimate"] > reopened
        other.limits = replace(other.limits, cards=1)
        count_before = retrieved(other)
        refused = other.call("search", "reader", query="markdown", request_id="too-big")
        assert refused["status"] == "budget_exceeded", refused
        assert retrieved(other) == count_before
        assert other.call("diagnostics", "reader")["session_spent_estimate"] > reopened
    finally:
        other.close()
