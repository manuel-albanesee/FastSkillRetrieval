from dataclasses import replace

import test_skill_graph as shared

from fastskillretrieval.registry import Registry

registry = shared.registry


def test_graph_rebuild_is_bounded_resumable_and_keeps_published_graph(registry):
    store, source = registry
    for number in range(4):
        shared.add_skill(source, f"bounded-{number}", "## Related Skills\n- `markdown-check`")
    shared.publish_all(store)
    before = store.call("graph", "before")
    store.limits = replace(store.limits, batch=1)
    first = store.call("graph", "builder", rebuild=True)
    assert first["status"] == "in_progress"
    assert first["signature"] == before["signature"]
    assert store.call("graph", "reader")["node_count"] == 5
    reopened = Registry(store.state, source, store.limits)
    try:
        for _ in range(15):
            result = reopened.call("graph", "resumed", rebuild=True)
            if result["status"] == "ok":
                break
            assert result["status"] == "in_progress", result
            assert reopened.call("graph", "reader")["signature"] == before["signature"]
        else:
            raise AssertionError("bounded graph rebuild did not finish")
        assert result["signature"] == before["signature"]
        assert result["node_count"] == 5
        assert result["edge_count"] == before["edge_count"]
    finally:
        reopened.close()


def test_partial_graph_publication_does_not_activate_new_catalog(registry):
    from test_publication_batches import add_skills, discover_and_submit

    store, source = registry
    discover_and_submit(store)
    assert store.call("publish", "baseline")["status"] == "ok"
    baseline = store.active()
    signature = store.call("graph", "before")["signature"]
    add_skills(source, 1)
    discover_and_submit(store)
    store.limits = replace(store.limits, batch=1)
    observed_partial = False
    for number in range(10):
        result = store.call("publish", f"bounded-{number}", batch_size=1)
        if result["status"] == "ok":
            break
        assert result["status"] == "in_progress", result
        observed_partial = True
        assert store.active() == baseline
        assert store.call("graph", "reader")["signature"] == signature
        assert store.call("search", f"reader-{number}", query="markdown")["status"] == "ok"
    else:
        raise AssertionError("incremental publication did not complete")
    assert observed_partial
    assert store.active() != baseline
    assert store.call("graph", "after")["node_count"] == 2
