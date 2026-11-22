"""Teseo-style source graph, retrieval traversal and catalog isolation."""

import pytest
import test_registry as shared

from fastskillretrieval.registry import Registry

registry = shared.registry


def add_skill(source, name, body, extra=""):
    folder = source / name
    folder.mkdir()
    (folder / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: Review documents safely using local checks.\n"
        f"{extra}---\n{body}\n",
        encoding="utf-8",
    )


def publish_all(store):
    scanned = store.call("discover", "graph-setup")
    assert scanned["status"] == "ok", scanned
    ids = {}
    for item in scanned["items"]:
        ids[item["name"]] = item["id"]
        files = store._files(item["id"], item["source_revision"])
        candidate = shared.metadata()
        quote = next(line for line in files["SKILL.md"].decode().splitlines() if line.strip())
        candidate["capabilities"][0]["evidence"] = {"file": "SKILL.md", "quote": quote}
        candidate["aliases"] = [item["name"]]
        candidate["description"] = f"Review documents safely with {item['name']} and local checks."
        assert (
            store.call(
                "submit",
                "graph-submit-" + item["id"],
                capability_id=item["id"],
                source_revision=item["source_revision"],
                candidate=candidate,
            )["status"]
            == "ok"
        )
    result = store.call("publish", "graph-publish")
    assert result["status"] == "ok", result
    return ids


def test_related_section_builds_edges_and_retrieves_a_neighbor_without_keyword_match(registry):
    store, source = registry
    add_skill(
        source, "release-orchestrator", "## Related Skills\n- `test-specialist` — verify tests"
    )
    add_skill(source, "test-specialist", "Check behavior and regressions.")
    ids = publish_all(store)
    graph = store.call("graph", "inspect", capability_id=ids["release-orchestrator"])
    assert graph["status"] == "ok", graph
    assert any(
        e["target"] == ids["test-specialist"] and e["relation"] == "related_to"
        for e in graph["edges"]
    )
    found = store.call("search", "reader", query="release-orchestrator")
    assert found["status"] in {"ok", "no_match"}, found
    cards = {c["reference"]["capability_id"]: c for c in found["candidates"]}
    assert ids["test-specialist"] in cards
    assert cards[ids["test-specialist"]]["retrieval"]["method"] == "graph"
    assert cards[ids["test-specialist"]]["retrieval"]["path"][0]["relation"] == "related_to"


def test_frontmatter_wikilinks_and_weak_mentions_have_source_provenance(registry):
    store, source = registry
    add_skill(source, "launch", "See [[audit-helper]].", 'uses: ["[[audit-helper]]"]\n')
    add_skill(source, "audit-helper", "Check release evidence.")
    ids = publish_all(store)
    graph = store.call("graph", "inspect", capability_id=ids["launch"])
    assert {e["relation"] for e in graph["edges"]} >= {"uses", "mentions"}
    assert all(e["file"] == "SKILL.md" and e["line"] > 0 for e in graph["edges"])
    result = store.call("search", "reader", query="launch", mode="prepare_use")
    assert result["status"] in {"ok", "no_match"}, result
    ref = next(
        c["reference"]
        for c in result["candidates"]
        if c["reference"]["capability_id"] == ids["launch"]
    )
    loaded = store.call("load", "reader", reference=ref)
    assert loaded["status"] == "ok" and loaded["dependencies"] == []


def test_missing_links_are_findings_and_code_examples_do_not_create_edges(registry):
    store, source = registry
    add_skill(
        source,
        "launch",
        "## Related Skills\n- `not-installed`\n"
        "```md\n- `audit-helper`\n```\n## Instructions\n`[[audit-helper]]` is syntax.",
    )
    add_skill(source, "audit-helper", "Check release evidence.")
    ids = publish_all(store)
    graph = store.call("graph", "inspect", capability_id=ids["launch"])
    assert graph["edges"] == []
    assert any(f["reason"] == "unresolved_reference" for f in graph["findings"])


def test_graph_does_not_expand_from_or_through_quarantined_or_excluded_nodes(registry):
    store, source = registry
    add_skill(source, "launch", "## Related Skills\n- `audit-helper`")
    add_skill(source, "audit-helper", "Check release evidence.")
    ids = publish_all(store)
    assert (
        store.call("set_status", "operator", capability_id=ids["launch"], status="quarantined")[
            "status"
        ]
        == "ok"
    )
    result = store.call("search", "reader", query="launch")
    assert result["status"] in {"ok", "no_match"}, result
    assert result["candidates"] == []
    result = store.call("search", "reader2", query="audit-helper", exclude_ids=[ids["launch"]])
    assert all(c["reference"]["capability_id"] != ids["launch"] for c in result["candidates"])


def test_catalog_rollback_restores_graph_and_removes_new_edges(registry):
    store, source = registry
    add_skill(source, "launch", "## Related Skills\n- `audit-helper`")
    add_skill(source, "audit-helper", "Check release evidence.")
    ids = publish_all(store)
    first = store.active()
    path = source / "launch/SKILL.md"
    path.write_text(path.read_text().replace("- `audit-helper`", "No related skills."))
    publish_all(store)
    assert store.call("graph", "new", capability_id=ids["launch"])["edges"] == []
    assert store.call("rollback", "operator", catalog_id=first)["status"] == "ok"
    assert store.call("graph", "old", capability_id=ids["launch"])["edges"]


def test_publication_failure_rolls_back_graph_and_catalog_together(registry, monkeypatch):
    store, source = registry
    publish_all(store)
    active = store.active()
    before = store.call("graph", "before")["signature"]
    add_skill(source, "launch", "## Related Skills\n- `markdown-check`")
    original = store.graph.build

    def interrupted(*args, **kwargs):
        original(*args, **kwargs)
        raise OSError("simulated graph publication interruption")

    monkeypatch.setattr(store.graph, "build", interrupted)
    with pytest.raises(AssertionError):
        publish_all(store)
    assert store.active() == active
    assert store.call("graph", "after")["signature"] == before


def test_existing_registry_can_rebuild_derived_graph_without_schema_migration(registry):
    store, source = registry
    add_skill(source, "launch", "## Related Skills\n- `markdown-check`")
    publish_all(store)
    with store.db:
        store.db.execute("DELETE FROM skill_graph.snapshots")
    assert store.call("graph", "missing")["ready"] is False
    assert store.call("graph", "rebuild", rebuild=True)["ready"] is True
    reopened = Registry(store.state, source)
    try:
        assert reopened.call("graph")["ready"] is True
        assert reopened.db.execute("PRAGMA user_version").fetchone()[0] == 2
    finally:
        reopened.close()


def test_typed_traversal_does_not_switch_direction_or_follow_weak_second_hops(registry):
    store, source = registry
    add_skill(source, "launch", "Start safely.", "uses: [audit-helper]\n")
    add_skill(
        source, "audit-helper", "## Related Skills\n- `weak-neighbor`", "uses: [final-review]\n"
    )
    add_skill(source, "final-review", "Inspect final evidence.")
    add_skill(source, "sibling", "Other process.", "uses: [audit-helper]\n")
    add_skill(source, "weak-neighbor", "Other knowledge.")
    ids = publish_all(store)
    result = store.call("search", "reader", query="launch")
    cards = {c["reference"]["capability_id"]: c for c in result["candidates"]}
    assert ids["final-review"] in cards
    assert len(cards[ids["final-review"]]["retrieval"]["path"]) == 2
    assert ids["sibling"] not in cards
    assert ids["weak-neighbor"] not in cards
    excluded = store.call("search", "blocked", query="launch", exclude_ids=[ids["audit-helper"]])
    assert ids["final-review"] not in {
        c["reference"]["capability_id"] for c in excluded["candidates"]
    }


def test_ambiguous_aliases_never_choose_an_arbitrary_node(registry):
    store, source = registry
    add_skill(source, "launch", "## Related Skills\n- `helper`")
    add_skill(source, "first", "Review first.", "aliases: [helper]\n")
    add_skill(source, "second", "Review second.", "aliases: [helper]\n")
    ids = publish_all(store)
    graph = store.call("graph", "inspect", capability_id=ids["launch"])
    assert graph["edges"] == []
    assert graph["findings"][0]["reason"] == "ambiguous_reference"


def test_cached_graph_result_rechecks_the_entire_path(registry):
    store, source = registry
    add_skill(source, "launch", "## Related Skills\n- `audit-helper`")
    add_skill(source, "audit-helper", "Check release evidence.")
    ids = publish_all(store)
    first = store.call("search", "reader", request_id="stable", query="launch")
    assert len(first["candidates"]) == 2
    assert (
        store.call("set_status", "operator", capability_id=ids["launch"], status="quarantined")[
            "status"
        ]
        == "ok"
    )
    repeated = store.call("search", "reader", request_id="stable", query="launch")
    assert repeated["status"] != "ok"
