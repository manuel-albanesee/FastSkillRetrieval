import copy

import pytest

from fastskillretrieval.experiment import (
    ExperimentError,
    protocol_fingerprint,
    summarize,
    validate_protocol,
)


def protocol():
    return {
        "schema_version": 1,
        "id": "fixture-v1",
        "revision": 1,
        "purpose": "development",
        "corpus": [{"id": "ecc/a", "path": "skills/a/SKILL.md", "sha256": "a" * 64}],
        "config_sha256": "b" * 64,
        "client": {"name": "claude-code", "version": "fixture", "model": "fixture"},
        "arms": ["A_NATIVE", "C_FSR"],
        "repetitions": 2,
        "cases": [
            {
                "id": "case-it",
                "group": "g1",
                "language": "it",
                "split": "development",
                "query": "Controlla il documento.",
                "essential": ["ecc/a"],
                "acceptable": [],
                "rationale": "Manuale a",
                "task_kind": "declarative",
                "resources": [],
                "human_review": None,
            }
        ],
        "metrics": {
            "catalog_threshold": 0.3,
            "essential_loss_tolerance": 0,
            "latency_p95_ms": 300,
            "aggregation": "ratio_of_means",
            "catalog_scope": "initial_and_loaded",
            "cache_policy": "report_separately",
            "generalization": "descriptive_only",
        },
        "retention": {"policy": "operator_review_before_sharing"},
        "budget": None,
        "approval": None,
    }


def test_contract_is_separate_from_legacy_and_hashes_all_decisions():
    p = protocol()
    validate_protocol(p)
    changed = copy.deepcopy(p)
    changed["cases"][0]["query"] += " Extra."
    assert protocol_fingerprint(p) != protocol_fingerprint(changed)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda p: p["cases"].append({**p["cases"][0], "id": "en", "split": "holdout"}),
        lambda p: p["cases"][0].update(essential=["ecc/unknown"]),
        lambda p: p.update(repetitions=True),
        lambda p: p["metrics"].update(catalog_threshold=0.05),
        lambda p: p["corpus"][0].update(path="../outside/SKILL.md"),
        lambda p: p["cases"][0].update(essential=["ecc/a"] * 6),
    ],
)
def test_invalid_protocol(mutation):
    p = protocol()
    mutation(p)
    with pytest.raises(ExperimentError):
        validate_protocol(p)


def test_paid_execution_needs_review_and_explicit_budget():
    with pytest.raises(ExperimentError, match="budget"):
        validate_protocol(protocol(), for_execution=True)


def test_missing_measurement_is_inconclusive_and_no_loss_is_absolute():
    p = protocol()
    report = summarize(p, [])
    assert report["catalog_A_to_C"]["value"] is None
    assert report["verdict_cases"] == "inconclusive"
    assert report["generalization_status"] == "not_established"


def test_host_ratio_uses_ratio_of_means_and_keeps_all_cases():
    from fastskillretrieval.experiment import catalog_reduction

    rows = [
        {"case_id": "a", "baseline": 100, "treatment": 50},
        {"case_id": "b", "baseline": 900, "treatment": 900},
    ]
    assert catalog_reduction(rows)["value"] == pytest.approx(0.05)
    rows.append({"case_id": "c", "baseline": None, "treatment": 0})
    assert catalog_reduction(rows)["value"] is None


def test_partial_cost_cannot_be_presented_as_experiment_total():
    p = protocol()
    row = {"case_id": "case-it", "arm": "A_NATIVE", "repetition": 1, "cost_usd": 0.2}
    assert summarize(p, [row])["cost_usd"] is None


def test_execution_cannot_silently_include_holdout_in_development():
    p = protocol()
    p["budget"] = {
        "trial_usd": 1,
        "session_usd": 2,
        "experiment_usd": 3,
        "timeout_seconds": 10,
        "max_turns": 2,
        "inflight_overshoot_usd": 0.1,
    }
    p["cases"][0]["split"] = "holdout"
    with pytest.raises(ExperimentError, match="one_frozen_split"):
        validate_protocol(p, for_execution=True)
