"""Verify pinned real manuals through registry search/load without a model.

Creates fresh state: old published manifests retain their original activation decisions.
This is a delivery check, not a relevance or Claude Code quality benchmark.
"""

import argparse
import hashlib
import json
from pathlib import Path

from prepare_real_eval import _prepare_registry

from fastskillretrieval.capabilities import parse_definition
from fastskillretrieval.contracts import cost
from fastskillretrieval.registry import Registry


def verify(source: Path, manifest: Path, state: Path) -> dict:
    fixture = json.loads(manifest.read_text(encoding="utf-8"))
    items = fixture["skills"]
    if len(items) != 100 or len({item["id"] for item in items}) != 100:
        raise ValueError("expected_100_distinct_pinned_skills")
    expected = {}
    for item in items:
        name = item["id"].removeprefix("ecc/")
        if (
            not name
            or name in {".", ".."}
            or Path(name).name != name
            or "\\" in name
            or ":" in name
        ):
            raise ValueError("invalid_fixture_name")
        file = source / name / "SKILL.md"
        data = file.read_bytes()
        if hashlib.sha256(data).hexdigest() != item["sha256"]:
            raise ValueError("source_hash_mismatch")
        expected[name] = data
    receipt = _prepare_registry(state, source, fixture["proposal_fingerprint"])
    store = Registry(state, source)
    observations = []
    try:
        for item in receipt["capabilities"]:
            name = item["name"]
            parsed = parse_definition(source / name / "SKILL.md", "skill")
            context = "delivery-" + item["capability_id"]
            found = store.call("search", context, explicit_id=item["capability_id"], limit=1)
            if found["status"] != "ok" or len(found["candidates"]) != 1:
                raise ValueError("explicit_lookup_failed")
            loaded = store.call("load", context, reference=found["candidates"][0]["reference"])
            observation = {
                "name": name,
                "bytes": len(expected[name]),
                "activation": parsed["activation"]["mode"],
                "status": loaded["status"],
                "reason": loaded.get("reason"),
            }
            if parsed["activation"]["mode"] == "load":
                if loaded["status"] != "ok":
                    raise ValueError(f"plain_manual_delivery_failed:{name}:{loaded}")
                if loaded["content"].encode("utf-8") != expected[name]:
                    raise ValueError("delivered_content_mismatch")
                observation["response_bytes"] = cost(loaded)
                observation["sha256_verified"] = True
            elif loaded["status"] != "unsupported_integration" or "content" in loaded:
                raise ValueError("native_skill_was_not_refused")
            observations.append(observation)
        largest = sorted(
            (item for item in observations if item["status"] == "ok"),
            key=lambda item: item["bytes"],
            reverse=True,
        )[:5]
        identities = {item["name"]: item["capability_id"] for item in receipt["capabilities"]}
        for item in largest:
            found = store.call("search", "five-manuals", explicit_id=identities[item["name"]])
            loaded = store.call(
                "load", "five-manuals", reference=found["candidates"][0]["reference"]
            )
            if loaded.get("content", "").encode("utf-8") != expected[item["name"]]:
                raise ValueError("five_manual_session_failed")
        return {
            "status": "ok",
            "scope": "manual_delivery_not_relevance_or_model_quality",
            "model_turns": 0,
            "catalog_id": receipt["catalog_id"],
            "corpus_count": len(items),
            "delivered": sum(item["status"] == "ok" for item in observations),
            "native_refused": sum(
                item["status"] == "unsupported_integration" for item in observations
            ),
            "budget_refused": sum(item["status"] == "budget_exceeded" for item in observations),
            "limits": vars(store.limits),
            "five_largest_in_one_session": [item["name"] for item in largest],
            "observations": observations,
        }
    finally:
        store.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("source-root", "manifest", "state", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or args.state.exists():
        parser.error("output and state must be new paths")
    result = verify(args.source_root.resolve(), args.manifest, args.state.resolve())
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    print(json.dumps({key: value for key, value in result.items() if key != "observations"}))
