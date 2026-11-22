"""Complete manual delivery with passive annotations and bounded payloads."""

import json
import shutil
from dataclasses import replace
from pathlib import Path

import pytest

from fastskillretrieval.catalog import Catalog
from fastskillretrieval.contracts import cost
from fastskillretrieval.registry import Registry

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("registry", [False, True])
def test_large_annotated_manual_delivers_whole_and_small_budget_refuses(tmp_path, registry):
    source = tmp_path / "sources"
    shutil.copytree(ROOT / "examples/skills", source)
    path = source / "markdown-check/SKILL.md"
    content = (
        path.read_text(encoding="utf-8").replace(
            "\n---\n", "\nmetadata:\n  origin: ECC\nlicense: Apache-2.0\n---\n", 1
        )
        + "\n"
        + "Istruzione completa: verificare anche le sezioni finali.\n" * 500
    )
    path.write_text(content, encoding="utf-8", newline="")
    service = (Registry if registry else Catalog)(tmp_path / "state", source)
    candidate = json.loads((ROOT / "examples/markdown-candidate.json").read_text())
    try:
        discovered = service.call(
            "discover" if registry else "inventory",
            "setup",
            **({} if registry else {"refresh": True}),
        )
        assert discovered["status"] == "ok", discovered
        item = discovered["items"][0]
        identity = (
            {"capability_id": item["id"], "source_revision": item["source_revision"]}
            if registry
            else {"skill_id": item["id"], "version": item["version"]}
        )
        assert service.call("submit", "setup", **identity, candidate=candidate)["status"] == "ok"
        assert service.call("publish", "setup")["status"] == "ok"
        found = service.call("search", "reader", query="markdown")
        assert found["status"] == "ok", found
        reference = found["candidates"][0]["reference"]
        loaded = service.call("load", "reader", reference=reference)
        assert loaded["status"] == "ok", loaded
        assert loaded["content"].encode("utf-8") == path.read_bytes()
        assert 4000 < cost(loaded) <= service.limits.load
        service.limits = replace(service.limits, load=4000)
        refused = service.call("load", "too-small", reference=reference)
        assert refused["status"] == "budget_exceeded", refused
        assert "content" not in refused
        resource = service.call(
            "resource", "too-small", reference=reference, file="references/checklist.md"
        )
        assert resource["reason"] == "load_required_before_resource"
    finally:
        service.close()
