"""Reproducible offline retrieval diagnostics; no host-token or model claims."""

import json
import time
from dataclasses import asdict
from pathlib import Path

from .experiment import fingerprint
from .registry import Registry


def summarize_cases(rows):
    def metrics(items):
        expected = sum(len(set(r["essential"])) for r in items)
        recalled = sum(len(set(r["essential"]) & set(r["selected"])) for r in items)
        return {
            "essential_total": expected,
            "essential_recalled": recalled,
            "missing_essential": expected - recalled,
            "essential_recall": recalled / expected if expected else None,
            "precision_at_5": recalled / (5 * len(items)) if items else None,
            "precision_denominator": "5 slots per case, including unfilled slots",
            "complete_cases": sum(set(r["essential"]).issubset(r["selected"]) for r in items),
            "zero_match_cases": sum(not r["selected"] for r in items),
            "blocked_count": sum(r.get("blocked_count", 0) for r in items),
            "omitted_for_budget": sum(r.get("omitted_for_budget", 0) for r in items),
        }

    return {
        **metrics(rows),
        "semantic_groups": len({r["group"] for r in rows}),
        "by_language": {
            lang: metrics([r for r in rows if r["language"] == lang])
            for lang in sorted({r["language"] for r in rows})
        },
    }


def classify_findings(graph, installed_root):
    """Classify observable targets, without importing any additional skills."""
    nodes = {n["name"] for n in graph["nodes"]}
    results = []
    for finding in graph["findings"]:
        ref = finding["reference"]
        safe = ref and ref[0].isalnum() and all(c.isalnum() or c in "-_" for c in ref)
        target = Path(installed_root) / ref / "SKILL.md" if safe else None
        if not safe:
            classification, reason = "non_skill_syntax", "reference is not a plain skill slug"
        elif ref not in nodes and target.is_file():
            classification, reason = (
                "outside_frozen_corpus",
                "manual exists in ECC bundle but name is absent from frozen nodes",
            )
        else:
            classification, reason = (
                "unresolved_manual_review",
                "no exact frozen node or installed manual established",
            )
        results.append(
            {
                **finding,
                "classification": classification,
                "classification_reason": reason,
                "external_manual": str(target) if target and target.is_file() else None,
            }
        )
    return results


def evaluate_ablation(state, source, requests, *, limit=5, diagnostic=True):
    """Use identical catalog, limits and cases for both policies; isolated ledgers."""
    if limit != 5:
        raise ValueError("ablation precision_at_5 requires limit=5")
    arms = {}
    config = None
    corpus = None
    for policy, arm in [("lexical", "L_FTS"), ("graph", "C_FSR")]:
        store = Registry(Path(state), Path(source), retrieval_policy=policy)
        try:
            config = {"limits": asdict(store.limits), "limit": limit, "mode": "metadata_only"}
            entries = [
                dict(row)
                for row in store.db.execute(
                    "SELECT * FROM entries WHERE catalog_id=? ORDER BY capability_id",
                    (store.active(),),
                )
            ]
            current_corpus = fingerprint(entries)
            if corpus is not None and corpus != current_corpus:
                raise ValueError("corpus changed between ablation arms")
            corpus = current_corpus
            names = {
                r["id"]: "ecc/" + r["name"]
                for r in store.db.execute("SELECT id,name FROM capabilities")
            }
            rows = []
            import uuid

            namespace = uuid.uuid4().hex[:12]
            for index, case in enumerate(requests):
                started = time.perf_counter_ns()
                response = store.call(
                    "search",
                    f"eval-{policy}-{namespace}-{index}",
                    query=case["query"],
                    request_id="one",
                    limit=limit,
                )
                selected = [
                    names[c["reference"]["capability_id"]] for c in response.get("candidates", [])
                ]
                rows.append(
                    {
                        "id": case["id"],
                        "language": case.get("language", "it"),
                        "group": case.get("group", case["id"]),
                        "split": case.get("split", "development"),
                        "essential": case.get(
                            "essential", case.get("expected_essential_capability_references", [])
                        ),
                        "selected": selected,
                        "status": response["status"],
                        "latency_ms": (time.perf_counter_ns() - started) / 1e6,
                        "blocked_count": response.get("blocked_count", 0),
                        "omitted_for_budget": response.get("omitted_for_budget", 0),
                        "response_bytes": len(json.dumps(response, ensure_ascii=False).encode()),
                        "retrieval": [c.get("retrieval") for c in response.get("candidates", [])],
                    }
                )
            arms[arm] = {"policy": policy, "cases": rows, "metrics": summarize_cases(rows)}
        finally:
            store.close()
    return {
        "schema_version": 1,
        "experiment": "offline_retrieval_ablation",
        "diagnostic_only": diagnostic,
        "host_context_reduction": None,
        "host_context_reason": "offline registry responses are not host observations",
        "corpus_fingerprint": corpus,
        "configuration": config,
        "configuration_fingerprint": fingerprint(config),
        "arms": arms,
        "by_group": {
            arm: {
                group: summarize_cases([r for r in data["cases"] if r["group"] == group])
                for group in sorted({r["group"] for r in data["cases"]})
            }
            for arm, data in arms.items()
        },
        "name_recognition": "FTS aliases retained; source graph alias resolution belongs to C_FSR",
        "model_turns": 0,
    }
