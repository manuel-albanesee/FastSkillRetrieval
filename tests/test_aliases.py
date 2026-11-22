"""Aliases may only canonicalize when their constraints survive the mapping."""

import json
import shutil

import pytest
import test_registry as shared

registry = shared.registry


@pytest.mark.parametrize("relation", ["requires", "preloads", "conflicts_with"])
def test_alias_relation_must_be_declared_by_its_canonical_target(registry, relation):
    store, source = registry
    target = shared.publish(store)[0]
    alias = source / "alias"
    shutil.copytree(source / "markdown-check", alias)
    (alias / "capability.json").write_text(
        json.dumps(
            {
                "kind": "command",
                "activation": {"mode": "alias"},
                "alias_of": target["id"],
                relation: ["missing-relation"],
            }
        ),
        encoding="utf-8",
    )

    alias_id = next(
        item["id"] for item in shared.publish(store, "alias-publication") if item["name"] == "alias"
    )
    result = store.call("search", "reader", explicit_id=alias_id, mode="prepare_use")
    assert result["status"] == "unsupported_integration"
    assert result["candidates"] == []
    assert result["blocked"] == [
        {
            "capability_id": alias_id,
            "status": "unsupported_integration",
            "reason": "alias_relations_not_preserved",
        }
    ]
