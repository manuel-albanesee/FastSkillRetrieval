"""Schema and dataset integrity checks; these do not execute evaluation cases."""

import copy
import hashlib
import json
from collections import defaultdict
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, ValidationError

from fastskillretrieval.experiment import ExperimentError, validate_protocol

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples" / "experiments"


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def validator(name):
    schema = read(ROOT / "schemas" / name)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def test_protocol_templates_are_structurally_valid_and_execution_is_blocked():
    schema = validator("experiment-v1.schema.json")
    for name in ("completion-v1.json", "pilot-v1.json"):
        protocol = read(EXAMPLES / name)
        schema.validate(protocol)
        validate_protocol(protocol)
        assert protocol["approval"] is None and protocol["budget"] is None
        assert protocol["client"]["version"] == "REPLACE_WITH_OBSERVED_VERSION"
        with pytest.raises(ExperimentError, match="budget"):
            validate_protocol(protocol, for_execution=True)
        assert protocol["config_sha256"] == hashlib.sha256(
            (EXAMPLES / "config-v1.json").read_bytes()
        ).hexdigest()


def test_semantic_groups_languages_and_splits_are_frozen_before_tuning():
    main = read(EXAMPLES / "completion-v1.json")
    pilot = read(EXAMPLES / "pilot-v1.json")
    groups = defaultdict(list)
    for case in main["cases"] + pilot["cases"]:
        groups[case["group"]].append(case)
        assert case["task_kind"] == "declarative"
        assert case["human_review"] is None
        assert case["essential"] and len(case["essential"]) <= 5
        assert case["rationale"]
    assert len({case["group"] for case in main["cases"]}) == 24
    assert len({case["group"] for case in pilot["cases"]}) == 3
    assert not {c["group"] for c in main["cases"]} & {c["group"] for c in pilot["cases"]}
    for cases in groups.values():
        assert {c["language"] for c in cases} == {"it", "en"}
        assert len(cases) == 2 and len({c["split"] for c in cases}) == 1
    assert {c["split"] for c in main["cases"]} == {"development", "holdout"}
    assert {c["split"] for c in pilot["cases"]} == {"pilot"}
    assert len(main["corpus"]) == 100
    assert main["corpus"] == read(ROOT / "examples/capability-eval-proposal.json")[
        "skill_references"
    ]


def test_corpus_audit_matches_pinned_inventory_without_claiming_complete_resources():
    protocol = read(EXAMPLES / "completion-v1.json")
    audit = read(EXAMPLES / "corpus-audit-v1.json")
    expected = {item["id"]: item["sha256"] for item in protocol["corpus"]}
    assert len(audit["manuals"]) == 100
    assert {item["id"]: item["sha256"] for item in audit["manuals"]} == expected
    assert all(item["verified"] is True for item in audit["manuals"])
    assert sum(len(item["missing_relative_links"]) for item in audit["manuals"]) > 0
    assert "Does not" in audit["limitations"]


@pytest.mark.parametrize("mutation", [
    lambda p: p.update(unknown=True),
    lambda p: p.update(repetitions=True),
    lambda p: p["corpus"][0].update(sha256="x" * 64),
    lambda p: p["corpus"][0].update(path="../outside/SKILL.md"),
    lambda p: p["cases"][0].update(essential=["ecc/a"] * 6),
    lambda p: p["cases"][0].update(task_kind="execute_shell"),
    lambda p: p["metrics"].update(aggregation="mean_of_ratios"),
    lambda p: p.update(budget={"trial_usd": -1}),
])
def test_protocol_schema_rejects_malformed_fields(mutation):
    protocol = read(EXAMPLES / "completion-v1.json")
    mutation(protocol)
    with pytest.raises(ValidationError):
        validator("experiment-v1.schema.json").validate(protocol)


def test_observation_schema_distinguishes_unknown_from_zero():
    schema = validator("run-observation-v1.schema.json")
    row = read(EXAMPLES / "observation-unknown-v1.json")
    schema.validate(row)
    assert row["usage"] is None and row["cost_usd"] is None
    known = copy.deepcopy(row)
    known.update(cost_usd=0, duration_seconds=0, loaded_skill_ids=[], task_success=False)
    schema.validate(known)
    known["catalog_measurement"] = {
        "value": 0, "origin": "host_observed", "unit": "tokens",
        "method": "host_context", "scope": "initial_and_loaded",
    }
    schema.validate(known)
    known["catalog_measurement"]["origin"] = "server_estimate"
    with pytest.raises(ValidationError):
        schema.validate(known)


@pytest.mark.parametrize("field,value", [
    ("repetition", 0), ("attempt", True), ("state", "complete"),
    ("loaded_skill_ids", "unknown"), ("selected_skill_ids", [1]),
    ("cost_usd", -1), ("duration_seconds", -1), ("task_success", 0),
    ("catalog_measurement", {"value": 1}), ("extra", "not-versioned"),
])
def test_observation_schema_rejects_ambiguous_or_invalid_rows(field, value):
    row = read(EXAMPLES / "observation-unknown-v1.json")
    row[field] = value
    with pytest.raises(ValidationError):
        validator("run-observation-v1.schema.json").validate(row)
