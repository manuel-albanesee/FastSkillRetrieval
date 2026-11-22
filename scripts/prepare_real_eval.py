"""Build the hash-checked 100-file evaluation fixture; never runs A/B/C measurements."""

import argparse
import json
from pathlib import Path

from fastskillretrieval.capabilities import parse_definition
from fastskillretrieval.evaluation import EvaluationError, prepare_fixture, write_json_new
from fastskillretrieval.registry import Registry


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Prepare the pinned real-skill evaluation fixture")
    result.add_argument("--proposal", type=Path, required=True)
    result.add_argument("--source-root", type=Path, required=True)
    result.add_argument("--output", type=Path, required=True)
    result.add_argument(
        "--registry-state",
        type=Path,
        help=(
            "Fresh disposable registry state; performs real discover/submit/publish without "
            "SQL seeding"
        ),
    )
    return result


def _source_candidate(source_root: Path, item: dict) -> dict:
    """Make bounded candidate metadata from the definition itself, without a model judgment."""
    source = source_root / item["name"] / "SKILL.md"
    parsed = parse_definition(source, "skill")
    raw = source.read_text(encoding="utf-8")
    body = parsed["definition"]["body"]
    quote = next((line.strip() for line in body.splitlines() if line.strip()), "SKILL.md")
    description = parsed["description_original"].strip()
    if len(description) < 40:
        description = (description + " " + quote).strip()
    if len(description) < 40:
        description = raw.replace("\n", " ").strip()[:1000]
    if len(description) < 40:
        raise EvaluationError("source_description_too_short")
    return {
        "description": description[:1000],
        "aliases": [item["name"]],
        "capabilities": [
            {
                "action": "read",
                "object": item["name"],
                "scope": description[:1000],
                "evidence": {"file": "SKILL.md", "quote": quote[:4000]},
            }
        ],
        "use_when": [description[:1000]],
        "exclusions": [],
        "technologies": [],
    }


def _inventory(store: Registry) -> list[dict]:
    offset, items = 0, []
    while True:
        # Registry responses are deliberately card-budgeted; page at the service's safe default.
        page = store.call("inventory", f"real-eval-inventory-{offset}", offset=offset, limit=20)
        if page["status"] != "ok":
            raise EvaluationError("registry_inventory_failed")
        items.extend(page["items"])
        offset += len(page["items"])
        if offset >= page["total"]:
            return items


def _prepare_registry(state: Path, sources: Path, proposal_fingerprint: str) -> dict:
    if state.exists():
        raise EvaluationError("registry_state_must_be_new")
    store = Registry(state, sources)
    try:
        discovered = store.call("discover", "real-eval-preparation")
        if discovered["status"] != "ok" or discovered["next_cursor"] is not None:
            raise EvaluationError("registry_discovery_incomplete")
        items = _inventory(store)
        if len(items) != 100 or any(item["state"] == "blocked" for item in items):
            raise EvaluationError("registry_discovery_not_ready")
        for item in items:
            submitted = store.call(
                "submit",
                f"real-eval-submit-{item['id']}",
                capability_id=item["id"],
                source_revision=item["source_revision"],
                candidate=_source_candidate(sources, item),
            )
            if submitted["status"] != "ok":
                raise EvaluationError("registry_descriptor_rejected")
        while True:
            published = store.call(
                "publish", "real-eval-publish", batch_size=100, deadline_ms=30000
            )
            if published["status"] == "ok":
                break
            if published["status"] != "in_progress":
                raise EvaluationError("registry_publish_failed")
        return {
            "status": "ready",
            "kind": "registry_inventory",
            "proposal_fingerprint": proposal_fingerprint,
            "catalog_id": published["catalog_id"],
            "capability_count": published["published"],
            "capabilities": [
                {
                    "name": item["name"],
                    "capability_id": item["id"],
                    "source_revision": item["source_revision"],
                }
                for item in items
            ],
        }
    finally:
        store.close()


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    try:
        with args.proposal.open("rb") as stream:
            proposal = json.load(stream)
        report = prepare_fixture(proposal, args.source_root, args.output)
        if args.registry_state:
            receipt = _prepare_registry(
                args.registry_state, args.output / "sources", report["proposal_fingerprint"]
            )
            write_json_new(args.output / "registry-receipt.json", receipt)
            report["registry_receipt"] = "registry-receipt.json"
    except (EvaluationError, OSError, ValueError, json.JSONDecodeError) as error:
        print(json.dumps({"status": "rejected", "reason": str(error)}, ensure_ascii=False))
        return 2
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
