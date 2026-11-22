"""Deterministic, human-gated reporting for the 100/20 capability evaluation.

This module does not invoke a model, inspect a native client, or create a registry.  It only
checks immutable inputs and summarizes operator-supplied observations without turning missing
observations into successful measurements.
"""

import copy
import hashlib
import json
import os
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path

from .packages import safe_path


class EvaluationError(ValueError):
    """An evaluation input is incomplete, changed, or inconsistent."""


_COST_FIELDS = (
    "preparation",
    "catalog",
    "new_instructions",
    "main_runtime",
    "delegated_runtime",
)
_ARMS = ("A", "B", "C")
_SKILL_ID = re.compile(r"ecc/[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")


def _canonical(value: object) -> bytes:
    try:
        return json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise EvaluationError("non_json_value") from error


def _fail(reason: str) -> None:
    raise EvaluationError(reason)


def _safe_relative(value: object) -> str:
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        _fail("invalid_relative_path")
    path = Path(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        _fail("invalid_relative_path")
    reserved = {"con", "prn", "aux", "nul"}
    reserved.update(f"com{number}" for number in range(1, 10))
    reserved.update(f"lpt{number}" for number in range(1, 10))
    if any(part.split(".", 1)[0].casefold() in reserved for part in path.parts):
        _fail("invalid_relative_path")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def _unique_strings(value: object, reason: str, *, nonempty: bool = True) -> list[str]:
    if (
        not isinstance(value, list)
        or (nonempty and not value)
        or any(not isinstance(item, str) or not item for item in value)
    ):
        _fail(reason)
    if len(value) != len(set(value)):
        _fail(reason)
    return value


def corpus_fingerprint(proposal: dict) -> str:
    """Hash the corpus independently from a later human relevance review."""
    if not isinstance(proposal, dict):
        _fail("proposal_not_object")
    frozen = copy.deepcopy(proposal)
    frozen.pop("approval", None)
    frozen.pop("results", None)
    frozen["status"] = "reviewable"
    for request in frozen.get("requests", []):
        if isinstance(request, dict):
            request.pop("expected_essential_capability_references", None)
            request.pop("human_review", None)
    return hashlib.sha256(_canonical(frozen)).hexdigest()


def proposal_fingerprint(proposal: dict) -> str:
    """Fingerprint the reviewable corpus and cases, excluding approval and outcomes."""
    if not isinstance(proposal, dict):
        _fail("proposal_not_object")
    frozen = copy.deepcopy(proposal)
    frozen.pop("approval", None)
    frozen.pop("results", None)
    frozen["status"] = "reviewable"
    return hashlib.sha256(_canonical(frozen)).hexdigest()


def validate_proposal(proposal: dict, *, require_standard_counts: bool = True) -> None:
    if not isinstance(proposal, dict) or proposal.get("schema_version") != 2:
        _fail("unsupported_proposal_schema")
    if proposal.get("status") not in {"proposed", "approved"}:
        _fail("invalid_proposal_status")
    skills = proposal.get("skill_references")
    requests = proposal.get("requests")
    if not isinstance(skills, list) or not isinstance(requests, list):
        _fail("missing_corpus_or_requests")
    expected_skills = 100 if require_standard_counts else proposal.get("required_skill_count")
    expected_requests = 20 if require_standard_counts else proposal.get("required_request_count")
    if (
        type(expected_skills) is not int
        or type(expected_requests) is not int
        or proposal.get("required_skill_count") != expected_skills
        or proposal.get("required_request_count") != expected_requests
        or len(skills) != expected_skills
        or len(requests) != expected_requests
    ):
        _fail("wrong_corpus_or_request_count")
    skill_ids = []
    for item in skills:
        if not isinstance(item, dict):
            _fail("invalid_skill_reference")
        identifier, path, digest = item.get("id"), item.get("path"), item.get("sha256")
        if not isinstance(identifier, str) or not _SKILL_ID.fullmatch(identifier):
            _fail("invalid_skill_reference")
        _safe_relative(path)
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(char not in "0123456789abcdef" for char in digest)
        ):
            _fail("invalid_skill_hash")
        skill_ids.append(identifier)
    if len(skill_ids) != len(set(skill_ids)):
        _fail("duplicate_skill_id")
    request_ids = []
    for item in requests:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"]:
            _fail("invalid_request")
        if not isinstance(item.get("query"), str) or not item["query"]:
            _fail("invalid_request")
        proposed = _unique_strings(
            item.get("proposed_essential_capability_references"), "invalid_proposed_essentials"
        )
        if not set(proposed).issubset(skill_ids):
            _fail("unknown_proposed_essential")
        expected = item.get("expected_essential_capability_references")
        if expected is not None:
            expected = _unique_strings(expected, "invalid_expected_essentials")
            if not set(expected).issubset(skill_ids):
                _fail("unknown_expected_essential")
        request_ids.append(item["id"])
    if len(request_ids) != len(set(request_ids)):
        _fail("duplicate_request_id")
    approval = proposal.get("approval")
    if proposal["status"] == "approved":
        if (
            not isinstance(approval, dict)
            or approval.get("review_fingerprint") != proposal_fingerprint(proposal)
            or approval.get("corpus_fingerprint") != corpus_fingerprint(proposal)
        ):
            _fail("invalid_approval_fingerprint")
        for item in requests:
            if item.get("expected_essential_capability_references") is None or not isinstance(
                item.get("human_review"), dict
            ):
                _fail("human_review_required")
    elif approval is not None:
        _fail("unapproved_proposal_has_approval")


def approve_proposal(proposal: dict, *, operator: str, note: str) -> dict:
    """Create an approved copy only after every expected essential is human-reviewed."""
    if (
        not isinstance(operator, str)
        or not operator.strip()
        or not isinstance(note, str)
        or not note.strip()
    ):
        _fail("invalid_operator_approval")
    candidate = copy.deepcopy(proposal)
    validate_proposal(candidate, require_standard_counts=False)
    if candidate["status"] != "proposed":
        _fail("proposal_not_pending_approval")
    for request in candidate["requests"]:
        if request.get("expected_essential_capability_references") is None or not isinstance(
            request.get("human_review"), dict
        ):
            _fail("human_review_required")
    corpus = corpus_fingerprint(candidate)
    candidate["status"] = "approved"
    candidate["approval"] = {
        "operator": operator.strip(),
        "note": note.strip(),
        "approved_at": datetime.now(UTC).isoformat(),
        "corpus_fingerprint": corpus,
        "review_fingerprint": proposal_fingerprint(candidate),
    }
    validate_proposal(candidate, require_standard_counts=False)
    return candidate


def prepare_fixture(
    proposal: dict, source_root: Path, output: Path, *, require_standard_counts: bool = True
) -> dict:
    """Copy exactly the hash-pinned source files to a fresh evaluation fixture."""
    validate_proposal(proposal, require_standard_counts=require_standard_counts)
    source_root, output = safe_path(Path(source_root)), safe_path(Path(output))
    if not source_root.is_dir() or output.exists():
        _fail("invalid_source_or_existing_output")
    prepared = []
    try:
        for item in proposal["skill_references"]:
            relative = _safe_relative(item["path"])
            source = safe_path(source_root / relative)
            if not source.is_file() or _sha256(source) != item["sha256"]:
                _fail("source_hash_mismatch")
            destination = safe_path(
                output / "sources" / item["id"].removeprefix("ecc/") / "SKILL.md"
            )
            destination.parent.mkdir(parents=True, exist_ok=False)
            shutil.copyfile(source, destination)
            if _sha256(destination) != item["sha256"]:
                _fail("copied_hash_mismatch")
            prepared.append({"id": item["id"], "sha256": item["sha256"]})
        manifest = {
            "status": "prepared",
            "proposal_fingerprint": proposal_fingerprint(proposal),
            "skills": prepared,
        }
        (output / "fixture-manifest.json").write_bytes(_canonical(manifest))
        return manifest
    except BaseException:
        # A partial fixture is never a valid input. Leave it inspectable for recovery, but mark it.
        if output.exists():
            (output / ".incomplete").write_text(
                "fixture preparation did not complete\n", encoding="utf-8"
            )
        raise


def _costs(value: object) -> dict[str, int | None]:
    if not isinstance(value, dict) or set(value) != set(_COST_FIELDS):
        _fail("invalid_cost_breakdown")
    result = {}
    for key in _COST_FIELDS:
        amount = value[key]
        if amount is not None and (type(amount) is not int or amount < 0):
            _fail("invalid_cost_breakdown")
        result[key] = amount
    return result


def _arm_summary(
    arm: str, value: object, essentials: dict[str, set[str]], skill_ids: set[str]
) -> dict:
    if not isinstance(value, dict) or set(value) != {
        "catalog_fingerprint",
        "context_bytes",
        "context_evidence",
        "queries",
    }:
        _fail("invalid_arm_result")
    context = value["context_bytes"]
    if context is not None and (type(context) is not int or context < 0):
        _fail("invalid_context_bytes")
    if arm == "A" and context is not None:
        _fail("native_context_bytes_unsupported")
    if arm == "A" and value["context_evidence"] is not None:
        evidence = value["context_evidence"]
        if (
            not isinstance(evidence, dict)
            or evidence.get("kind") != "native_client_observation"
            or evidence.get("context_bytes") not in {None}
        ):
            _fail("native_context_evidence_required")
    if arm != "A" and context is not None and value["catalog_fingerprint"] is None:
        _fail("curated_catalog_fingerprint_required")
    queries = value["queries"]
    if not isinstance(queries, list) or len(queries) != len(essentials):
        _fail("wrong_result_query_count")
    returned, costs, query_ids = 0, {key: [] for key in _COST_FIELDS}, set()
    all_essential = 0
    for query in queries:
        if not isinstance(query, dict) or set(query) != {
            "id",
            "selected_capability_references",
            "costs",
        }:
            _fail("invalid_query_result")
        query_id = query["id"]
        if not isinstance(query_id, str) or query_id not in essentials or query_id in query_ids:
            _fail("invalid_query_result")
        query_ids.add(query_id)
        selected = _unique_strings(
            query["selected_capability_references"], "invalid_selection", nonempty=False
        )
        if not set(selected).issubset(skill_ids):
            _fail("unknown_selected_capability")
        selected_set = set(selected)
        returned += len(selected_set & essentials[query_id])
        all_essential += essentials[query_id].issubset(selected_set)
        for key, amount in _costs(query["costs"]).items():
            if amount is not None:
                costs[key].append(amount)
    if query_ids != set(essentials):
        _fail("result_query_ids_mismatch")
    total = sum(len(value) for value in essentials.values())
    return {
        "essential_total": total,
        "essential_retrieved": returned,
        "essential_recall": returned / total if total else None,
        "requests_with_all_essential": all_essential,
        "context_bytes": context,
        "costs": {key: sum(value) if value else None for key, value in costs.items()},
    }


def evaluate_results(
    proposal: dict, results: dict, *, require_standard_counts: bool = True
) -> dict:
    """Validate frozen observations and calculate only deterministic set metrics."""
    validate_proposal(proposal, require_standard_counts=require_standard_counts)
    if proposal["status"] != "approved":
        _fail("proposal_not_approved")
    if not isinstance(results, dict) or set(results) != {
        "proposal_fingerprint",
        "registry_readiness",
        "arms",
    }:
        _fail("invalid_results")
    if results["proposal_fingerprint"] != proposal_fingerprint(proposal):
        _fail("results_fingerprint_mismatch")
    registry = results["registry_readiness"]
    if not isinstance(registry, dict) or registry.get("status") not in {
        "ready",
        "not_observed",
        "not_ready",
    }:
        _fail("invalid_registry_readiness")
    if registry["status"] == "ready" and not isinstance(registry.get("receipt"), dict):
        _fail("registry_receipt_required")
    arms = results["arms"]
    if not isinstance(arms, dict) or set(arms) != set(_ARMS):
        _fail("missing_evaluation_arm")
    essentials = {
        request["id"]: set(request["expected_essential_capability_references"])
        for request in proposal["requests"]
    }
    skill_ids = {item["id"] for item in proposal["skill_references"]}
    summaries = {arm: _arm_summary(arm, arms[arm], essentials, skill_ids) for arm in _ARMS}
    b_catalog, c_catalog = arms["B"]["catalog_fingerprint"], arms["C"]["catalog_fingerprint"]
    if not isinstance(b_catalog, str) or not b_catalog or b_catalog != c_catalog:
        _fail("curated_catalog_fingerprint_mismatch")
    native = arms["A"]["context_evidence"] is not None
    # Native /context reports estimated tokens, deliberately never response bytes.
    reduction = None
    return {
        "status": "measured",
        "proposal_fingerprint": proposal_fingerprint(proposal),
        "registry_readiness": registry,
        "native_catalog_observed": native,
        "context_reduction_A_to_C": reduction,
        "arms": summaries,
    }


def write_json_new(path: Path, value: object) -> None:
    """Write a result once; callers must choose a new, explicit output filename."""
    path = safe_path(Path(path))
    if path.exists() or not path.parent.is_dir():
        _fail("output_must_be_new_with_existing_parent")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(_canonical(value) + b"\n")
