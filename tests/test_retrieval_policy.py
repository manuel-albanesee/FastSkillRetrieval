import pytest
import test_skill_graph as graph

from fastskillretrieval.registry import Registry

registry = graph.registry


def test_operator_lexical_disables_edges_and_policy_changes_replay(registry):
    store, source = registry
    graph.add_skill(source, "launch-owner", "## Related Skills\n- `audit-specialist`")
    graph.add_skill(source, "audit-specialist", "Review local checks.")
    ids = graph.publish_all(store)
    found = store.call("search", "reader", query="launch-owner", request_id="one")
    assert ids["audit-specialist"] in [c["reference"]["capability_id"] for c in found["candidates"]]
    state, root = store.state, store.root
    store.close()
    lexical = Registry(state, root, retrieval_policy="lexical")
    try:
        assert (
            lexical.call("search", "reader", query="launch-owner", request_id="one")["status"]
            == "request_conflict"
        )
        found = lexical.call("search", "lex", query="launch-owner", request_id="one")
        assert ids["audit-specialist"] not in [
            c["reference"]["capability_id"] for c in found["candidates"]
        ]
        assert ids["launch-owner"] in [c["reference"]["capability_id"] for c in found["candidates"]]
    finally:
        lexical.close()
    # Restore fixture handle for teardown.
    store.db = __import__("sqlite3").connect(state / "registry.sqlite3")


def test_unknown_operator_policy_is_rejected(tmp_path):
    root = tmp_path / "sources"
    root.mkdir()
    with pytest.raises(ValueError, match="retrieval_policy"):
        Registry(tmp_path / "registry", root, retrieval_policy="unknown")


def test_hub_cycles_bilingual_name_queries_keep_filters_and_budgets(registry):
    from fastskillretrieval.contracts import serialized

    store, source = registry
    links = "\n".join(f"- `neighbor-{number:02d}`" for number in range(24))
    graph.add_skill(source, "hub-center", "## Related Skills\n" + links)
    for number in range(24):
        graph.add_skill(source, f"neighbor-{number:02d}", "## Related Skills\n- `hub-center`")
    ids = graph.publish_all(store)
    excluded = ids["neighbor-00"]
    for index, query in enumerate(["Controlla hub-center", "Review hub-center"]):
        result = store.call("search", f"hub-{index}", query=query, exclude_ids=[excluded])
        assert result["status"] == "ok"
        assert len(result["candidates"]) <= store.limits.candidates
        assert len(serialized(result).encode("utf-8")) <= store.limits.output_bytes
        assert excluded not in [c["reference"]["capability_id"] for c in result["candidates"]]
        assert all(len(c.get("retrieval", {}).get("path", [])) <= 3 for c in result["candidates"])
    assert store.call("search", "unmatched", query="zzunmatcheduniquetoken")["status"] == "no_match"
