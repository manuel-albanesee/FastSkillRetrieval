import hashlib
import json
from pathlib import Path

import pytest

from fastskillretrieval.evaluation import (
    EvaluationError,
    approve_proposal,
    corpus_fingerprint,
    evaluate_results,
    prepare_fixture,
    proposal_fingerprint,
    validate_proposal,
)


def _skill(root: Path, name: str, content: bytes = b"# real skill\n") -> dict:
    path = root / "skills" / name / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return {
        "id": f"ecc/{name}",
        "path": f"skills/{name}/SKILL.md",
        "sha256": hashlib.sha256(content).hexdigest(),
    }


def _proposal(root: Path, *, approved: bool = False) -> dict:
    skills = [_skill(root, "one"), _skill(root, "two", b"# second skill\n")]
    requests = []
    for index, essential in enumerate((["ecc/one"], ["ecc/two"]), 1):
        requests.append(
            {
                "id": f"q{index}",
                "query": f"Richiesta autorizzata {index}",
                "proposed_essential_capability_references": essential,
                "expected_essential_capability_references": essential if approved else None,
                "human_review": {"reviewer": "reviewer", "decision": "approved"}
                if approved
                else None,
            }
        )
    value = {
        "schema_version": 2,
        "status": "proposed",
        "purpose": "test fixture",
        "source": {"provider": "test", "bundle_version": "1", "hash_algorithm": "sha256"},
        "required_skill_count": 2,
        "required_request_count": 2,
        "skill_references": skills,
        "requests": requests,
        "approval": None,
        "results": None,
    }
    if approved:
        return approve_proposal(value, operator="reviewer", note="explicit human review")
    return value


def _arm(selected: list[list[str]], *, context_bytes: int | None, fingerprint: str | None):
    return {
        "catalog_fingerprint": fingerprint,
        "context_bytes": context_bytes,
        "context_evidence": (
            {"kind": "native_client_observation", "observation_id": "host-run-1"}
            if context_bytes is not None
            else None
        ),
        "queries": [
            {
                "id": f"q{index}",
                "selected_capability_references": values,
                "costs": {
                    "preparation": 1,
                    "catalog": 2,
                    "new_instructions": 3,
                    "main_runtime": 4,
                    "delegated_runtime": None,
                },
            }
            for index, values in enumerate(selected, 1)
        ],
    }


def test_prepare_fixture_checks_hashes_and_writes_only_selected_files(tmp_path):
    source = tmp_path / "source"
    proposal = _proposal(source)
    fixture = tmp_path / "fixture"

    report = prepare_fixture(proposal, source, fixture, require_standard_counts=False)

    assert report["status"] == "prepared"
    assert (fixture / "sources" / "one" / "SKILL.md").read_bytes() == b"# real skill\n"
    assert not (fixture / "sources" / "unselected" / "SKILL.md").exists()


def test_prepare_fixture_rejects_changed_source(tmp_path):
    source = tmp_path / "source"
    proposal = _proposal(source)
    (source / "skills" / "one" / "SKILL.md").write_text("changed", encoding="utf-8")

    with pytest.raises(EvaluationError, match="source_hash_mismatch"):
        prepare_fixture(proposal, source, tmp_path / "fixture", require_standard_counts=False)


def test_approval_requires_completed_human_reviews(tmp_path):
    with pytest.raises(EvaluationError, match="human_review_required"):
        approve_proposal(_proposal(tmp_path / "source"), operator="reviewer", note="no")


def test_evaluation_keeps_native_and_unobserved_metrics_unknown(tmp_path):
    proposal = _proposal(tmp_path / "source", approved=True)
    shared_catalog = "curated-catalog-fingerprint"
    results = {
        "proposal_fingerprint": proposal_fingerprint(proposal),
        "registry_readiness": {"status": "not_observed"},
        "arms": {
            "A": _arm([["ecc/one"], ["ecc/two"]], context_bytes=None, fingerprint=None),
            "B": _arm([["ecc/one"], []], context_bytes=200, fingerprint=shared_catalog),
            "C": _arm([["ecc/one"], ["ecc/two"]], context_bytes=100, fingerprint=shared_catalog),
        },
    }

    report = evaluate_results(proposal, results, require_standard_counts=False)

    assert report["arms"]["A"]["essential_recall"] == 1.0
    assert report["context_reduction_A_to_C"] is None
    assert report["native_catalog_observed"] is False
    assert report["registry_readiness"]["status"] == "not_observed"
    assert report["arms"]["C"]["costs"]["delegated_runtime"] is None


def test_evaluation_requires_same_curated_catalog_for_b_and_c(tmp_path):
    proposal = _proposal(tmp_path / "source", approved=True)
    results = {
        "proposal_fingerprint": proposal_fingerprint(proposal),
        "registry_readiness": {"status": "not_observed"},
        "arms": {
            "A": _arm([[], []], context_bytes=None, fingerprint=None),
            "B": _arm([[], []], context_bytes=200, fingerprint="B"),
            "C": _arm([[], []], context_bytes=100, fingerprint="C"),
        },
    }

    with pytest.raises(EvaluationError, match="curated_catalog_fingerprint_mismatch"):
        evaluate_results(proposal, results, require_standard_counts=False)


def test_real_proposal_is_reviewed_unapproved_and_hashable():
    proposal = json.loads(
        Path("examples/capability-eval-proposal.json").read_text(encoding="utf-8")
    )

    validate_proposal(proposal)

    assert proposal["status"] == "proposed"
    assert proposal["approval"] is None
    assert len(proposal["skill_references"]) == 100
    assert len(proposal["requests"]) == 20
    assert all(
        item["expected_essential_capability_references"] and isinstance(item["human_review"], dict)
        for item in proposal["requests"]
    )


@pytest.mark.parametrize("path", ["C:relative", "name:stream", "aux/SKILL.md"])
def test_proposal_rejects_windows_ambiguous_source_paths(tmp_path, path):
    proposal = _proposal(tmp_path / "source")
    proposal["skill_references"][0]["path"] = path

    with pytest.raises(EvaluationError, match="invalid_relative_path"):
        validate_proposal(proposal, require_standard_counts=False)


def test_fingerprint_rejects_nonfinite_json_values(tmp_path):
    proposal = _proposal(tmp_path / "source")
    proposal["purpose"] = float("nan")

    with pytest.raises(EvaluationError, match="non_json_value"):
        proposal_fingerprint(proposal)


def test_essential_recall_counts_each_query_even_when_essential_repeats(tmp_path):
    pending = _proposal(tmp_path / "source")
    pending["requests"][0]["expected_essential_capability_references"] = ["ecc/one"]
    pending["requests"][1]["expected_essential_capability_references"] = ["ecc/one"]
    pending["requests"][0]["human_review"] = {"reviewer": "human", "decision": "approved"}
    pending["requests"][1]["human_review"] = {"reviewer": "human", "decision": "approved"}
    proposal = approve_proposal(pending, operator="operator", note="human review recorded")
    catalog = "same-curated-catalog"
    results = {
        "proposal_fingerprint": proposal_fingerprint(proposal),
        "registry_readiness": {"status": "not_observed"},
        "arms": {
            "A": _arm([["ecc/one"], ["ecc/one"]], context_bytes=None, fingerprint=None),
            "B": _arm([["ecc/one"], ["ecc/one"]], context_bytes=20, fingerprint=catalog),
            "C": _arm([["ecc/one"], ["ecc/one"]], context_bytes=10, fingerprint=catalog),
        },
    }

    report = evaluate_results(proposal, results, require_standard_counts=False)

    assert report["arms"]["C"]["essential_total"] == 2
    assert report["arms"]["C"]["essential_retrieved"] == 2
    assert report["arms"]["C"]["essential_recall"] == 1.0


def test_proposal_rejects_skill_id_that_escapes_fixture_destination(tmp_path):
    proposal = _proposal(tmp_path / "source")
    proposal["skill_references"][0]["id"] = "ecc/../../outside"

    with pytest.raises(EvaluationError, match="invalid_skill_reference"):
        validate_proposal(proposal, require_standard_counts=False)


def test_native_context_bytes_are_never_accepted_from_an_untrusted_observation(tmp_path):
    proposal = _proposal(tmp_path / "source", approved=True)
    catalog = "same-curated-catalog"
    results = {
        "proposal_fingerprint": proposal_fingerprint(proposal),
        "registry_readiness": {"status": "not_observed"},
        "arms": {
            "A": _arm([[], []], context_bytes=123, fingerprint=None),
            "B": _arm([[], []], context_bytes=20, fingerprint=catalog),
            "C": _arm([[], []], context_bytes=10, fingerprint=catalog),
        },
    }

    with pytest.raises(EvaluationError, match="native_context_bytes_unsupported"):
        evaluate_results(proposal, results, require_standard_counts=False)


def test_approval_records_stable_corpus_and_review_projection_fingerprints(tmp_path):
    pending = _proposal(tmp_path / "source")
    original_corpus = corpus_fingerprint(pending)
    original_review = proposal_fingerprint(pending)
    for request in pending["requests"]:
        request["expected_essential_capability_references"] = list(
            request["proposed_essential_capability_references"]
        )
        request["human_review"] = {"reviewer": "human", "decision": "approved"}

    approved = approve_proposal(pending, operator="operator", note="review complete")

    assert corpus_fingerprint(approved) == original_corpus
    assert proposal_fingerprint(approved) != original_review
    assert approved["approval"]["corpus_fingerprint"] == original_corpus
    assert approved["approval"]["review_fingerprint"] == proposal_fingerprint(approved)
