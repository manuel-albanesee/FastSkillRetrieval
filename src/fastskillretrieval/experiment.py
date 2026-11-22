"""Offline experiment contracts. Never invokes a provider or writes registry events."""

import copy
import hashlib
import json
import math
import re
from pathlib import PurePosixPath


class ExperimentError(ValueError):
    """An experiment cannot be reproduced or safely interpreted."""


ARMS = ("A_NATIVE", "B_CURATED", "C_FSR")
TRIAL_STATES = ("planned", "running", "succeeded", "failed", "timed_out", "invalid", "unknown")
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_HASH = re.compile(r"[a-f0-9]{64}\Z")


def canonical(value):
    try:
        return json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    except (ValueError, TypeError) as error:
        raise ExperimentError("non_json_value") from error


def fingerprint(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def protocol_fingerprint(protocol):
    frozen = copy.deepcopy(protocol)
    frozen.pop("approval", None)
    return fingerprint(frozen)


def _require(condition, reason):
    if not condition:
        raise ExperimentError(reason)


def _number(value, *, positive=False):
    return (
        type(value) in (float, int)
        and math.isfinite(value)
        and (value > 0 if positive else value >= 0)
    )


def _strings(value, *, nonempty=False):
    return (
        isinstance(value, list)
        and (bool(value) or not nonempty)
        and all(isinstance(s, str) and s for s in value)
        and len(value) == len(set(value))
    )


def _identifier(value):
    return isinstance(value, str) and _ID.fullmatch(value)


def relative_path(value):
    _require(
        isinstance(value, str) and value and "\\" not in value and ":" not in value,
        "invalid_relative_path",
    )
    path = PurePosixPath(value)
    reserved = {"con", "prn", "aux", "nul"}
    reserved.update(f"{prefix}{n}" for prefix in ("com", "lpt") for n in range(1, 10))
    _require(
        not path.is_absolute()
        and all(
            s not in ("", ".", "..")
            and not s.endswith((".", " "))
            and s.split(".")[0].casefold() not in reserved
            for s in value.split("/")
        ),
        "invalid_relative_path",
    )
    return value


def validate_budget(budget):
    _require(isinstance(budget, dict), "explicit_budget_required")
    for field in ("trial_usd", "session_usd", "experiment_usd", "timeout_seconds"):
        _require(_number(budget.get(field), positive=True), "invalid_budget_" + field)
    _require(_number(budget.get("inflight_overshoot_usd")), "explicit_overshoot_required")
    _require(type(budget.get("max_turns")) is int and budget["max_turns"] > 0, "invalid_max_turns")
    _require(
        budget["trial_usd"] <= budget["session_usd"] <= budget["experiment_usd"],
        "inconsistent_budget_caps",
    )


def validate_protocol(protocol, *, for_execution=False):
    _require(
        isinstance(protocol, dict) and protocol.get("schema_version") == 1,
        "unsupported_experiment_schema",
    )
    canonical(protocol)
    _require(_identifier(protocol.get("id")), "invalid_experiment_id")
    _require(type(protocol.get("revision")) is int and protocol["revision"] > 0, "invalid_revision")
    _require(
        protocol.get("purpose") in ("development", "pilot", "historical", "holdout"),
        "invalid_purpose",
    )
    arms = protocol.get("arms")
    _require(_strings(arms, nonempty=True) and set(arms) <= set(ARMS), "invalid_arms")
    _require({"A_NATIVE", "C_FSR"} <= set(arms), "missing_comparison_arms")
    if protocol["purpose"] == "historical":
        _require(set(arms) == set(ARMS), "historical_requires_ABC")
    _require(
        type(protocol.get("repetitions")) is int and protocol["repetitions"] > 0,
        "invalid_repetitions",
    )
    corpus = protocol.get("corpus")
    _require(isinstance(corpus, list) and corpus, "missing_corpus")
    skill_ids, paths = set(), set()
    for item in corpus:
        _require(
            isinstance(item, dict) and set(item) == {"id", "path", "sha256"}, "invalid_corpus_item"
        )
        identifier = item["id"]
        _require(
            isinstance(identifier, str)
            and identifier.startswith("ecc/")
            and _identifier(identifier[4:])
            and identifier not in skill_ids,
            "invalid_or_duplicate_skill_id",
        )
        relative_path(item["path"])
        _require(item["path"].casefold() not in paths, "duplicate_corpus_path")
        _require(
            isinstance(item["sha256"], str) and _HASH.fullmatch(item["sha256"]),
            "invalid_corpus_hash",
        )
        skill_ids.add(identifier)
        paths.add(item["path"].casefold())
    _require(
        isinstance(protocol.get("config_sha256"), str)
        and _HASH.fullmatch(protocol["config_sha256"]),
        "invalid_config_hash",
    )
    client = protocol.get("client")
    _require(
        isinstance(client, dict)
        and client.get("name") == "claude-code"
        and all(isinstance(client.get(k), str) and client[k] for k in ("version", "model")),
        "client_pin_required",
    )
    cases = protocol.get("cases")
    _require(isinstance(cases, list) and cases, "missing_cases")
    ids, groups, translations = set(), {}, set()
    for case in cases:
        _require(
            isinstance(case, dict)
            and _identifier(case.get("id"))
            and case["id"] not in ids
            and _identifier(case.get("group")),
            "invalid_case_id",
        )
        ids.add(case["id"])
        _require(case.get("language") in ("it", "en"), "unsupported_case_language")
        split = case.get("split")
        _require(split in ("development", "pilot", "holdout", "historical"), "invalid_split")
        _require(
            case["group"] not in groups or groups[case["group"]] == split,
            "semantic_group_split_leakage",
        )
        groups[case["group"]] = split
        translation = case["group"], case["language"]
        _require(translation not in translations, "duplicate_group_language")
        translations.add(translation)
        _require(isinstance(case.get("query"), str) and case["query"].strip(), "missing_query")
        for key in ("essential", "acceptable"):
            _require(
                _strings(case.get(key)) and set(case[key]) <= skill_ids,
                "unknown_or_duplicate_" + key,
            )
        _require(len(case["essential"]) <= 5, "essentials_exceed_product_limit")
        _require(isinstance(case.get("rationale"), str) and case["rationale"], "missing_rationale")
        _require(case.get("task_kind") == "declarative", "unverified_filesystem_boundary")
        _require(isinstance(case.get("resources"), list), "missing_resource_inventory")
        for resource in case["resources"]:
            _require(
                isinstance(resource, dict)
                and isinstance(resource.get("sha256"), str)
                and _HASH.fullmatch(resource["sha256"]),
                "invalid_resource",
            )
            relative_path(resource.get("path"))
    if protocol["purpose"] == "historical":
        _require(len(corpus) == 100 and len(cases) == 20, "historical_requires_100_20")
    metrics = protocol.get("metrics")
    _require(
        isinstance(metrics, dict)
        and metrics.get("catalog_threshold") == 0.3
        and type(metrics.get("essential_loss_tolerance")) is int
        and metrics["essential_loss_tolerance"] == 0
        and metrics.get("latency_p95_ms") == 300
        and metrics.get("aggregation") == "ratio_of_means"
        and metrics.get("catalog_scope") in ("initial", "initial_and_loaded")
        and metrics.get("cache_policy") == "report_separately"
        and metrics.get("generalization") == "descriptive_only",
        "unfrozen_metrics",
    )
    retention = protocol.get("retention")
    _require(
        isinstance(retention, dict)
        and isinstance(retention.get("policy"), str)
        and retention["policy"],
        "retention_policy_required",
    )
    if protocol.get("budget") is not None or for_execution:
        validate_budget(protocol.get("budget"))
    if for_execution:
        _require(
            all(case["split"] == protocol["purpose"] for case in cases),
            "execution_requires_one_frozen_split",
        )
    approval = protocol.get("approval")
    if approval is not None or for_execution:
        _require(
            isinstance(approval, dict)
            and approval.get("fingerprint") == protocol_fingerprint(protocol),
            "human_approval_required",
        )
        _require(
            all(
                isinstance(approval.get(k), str) and approval[k].strip()
                for k in ("reviewer", "authorized_by", "authorized_at")
            ),
            "execution_authorization_required",
        )
        for case in cases:
            review = case.get("human_review")
            _require(
                isinstance(review, dict) and review.get("reviewer") and review.get("reviewed_at"),
                "case_human_review_required",
            )


def catalog_reduction(rows):
    """All planned pairs must be present and comparable; no complete-case filtering."""
    if not rows or any(
        not _number(r.get("baseline"), positive=True) or not _number(r.get("treatment"))
        for r in rows
    ):
        return {
            "value": None,
            "reason": "missing_or_zero_denominator",
            "pairs": rows,
            "method": "ratio_of_means",
        }
    return {
        "value": 1 - sum(r["treatment"] for r in rows) / sum(r["baseline"] for r in rows),
        "reason": None,
        "pairs": rows,
        "method": "ratio_of_means",
    }


def summarize(protocol, observations):
    """Summarize observed cases without converting usage or server bytes into context."""
    validate_protocol(protocol)
    lookup = {}
    planned = {
        (case["id"], arm, repetition)
        for case in protocol["cases"]
        for arm in protocol["arms"]
        for repetition in range(1, protocol["repetitions"] + 1)
    }
    for row in observations:
        key = row.get("case_id"), row.get("arm"), row.get("repetition")
        _require(key in planned, "unplanned_observation")
        _require(key not in lookup, "duplicate_observation")
        lookup[key] = row
    ratios, missing, loss, task_failed = {}, False, False, False
    for baseline, field in (("A_NATIVE", "catalog_A_to_C"), ("B_CURATED", "catalog_B_to_C")):
        rows = []
        for case in protocol["cases"]:
            for repetition in range(1, protocol["repetitions"] + 1):
                a = lookup.get((case["id"], baseline, repetition), {})
                c = lookup.get((case["id"], "C_FSR", repetition), {})
                am, cm = a.get("catalog_measurement"), c.get("catalog_measurement")
                comparable = (
                    isinstance(am, dict)
                    and isinstance(cm, dict)
                    and all(
                        am.get(k) is not None and am.get(k) == cm.get(k)
                        for k in ("unit", "method", "scope")
                    )
                    and am.get("origin") == cm.get("origin") == "host_observed"
                    and am.get("unit") == "tokens"
                    and am.get("method") == "host_context"
                    and am.get("scope") == protocol["metrics"]["catalog_scope"]
                )
                rows.append(
                    {
                        "case_id": case["id"],
                        "repetition": repetition,
                        "baseline": am.get("value") if comparable else None,
                        "treatment": cm.get("value") if comparable else None,
                    }
                )
        ratios[field] = catalog_reduction(rows)
    cases = []
    for case in protocol["cases"]:
        for repetition in range(1, protocol["repetitions"] + 1):
            pair = {}
            for arm in protocol["arms"]:
                row = lookup.get((case["id"], arm, repetition), {})
                delivered = row.get("loaded_skill_ids")
                known = row.get("state") == "succeeded" and isinstance(delivered, list)
                missing |= not known or row.get("task_success") is None
                task_failed |= row.get("task_success") is False
                omissions = sorted(set(case["essential"]) - set(delivered or [])) if known else None
                pair[arm] = {
                    "essential_omissions": omissions,
                    "task_success": row.get("task_success"),
                }
                if arm == "C_FSR" and omissions:
                    loss = True
            cases.append(
                {
                    "case_id": case["id"],
                    "group": case["group"],
                    "language": case["language"],
                    "repetition": repetition,
                    "arms": pair,
                }
            )
    required = [ratios["catalog_A_to_C"]]
    if "B_CURATED" in protocol["arms"]:
        required.append(ratios["catalog_B_to_C"])
    failed = (
        loss or task_failed or any(r["value"] is not None and r["value"] < 0.3 for r in required)
    )
    unknown = missing or any(r["value"] is None for r in required)
    return {
        "protocol_sha256": protocol_fingerprint(protocol),
        **ratios,
        "cases": cases,
        "essential_loss": loss,
        "verdict_cases": "failed" if failed else "inconclusive" if unknown else "passed",
        "generalization_status": "not_established",
        "runtime_usage": [r.get("usage") for r in observations],
        "cost_usd": (
            sum(r["cost_usd"] for r in observations)
            if len(lookup) == len(planned) and all(_number(r.get("cost_usd")) for r in observations)
            else None
        ),
    }
