"""Conservative stream-json observation parser. Tool results are reads, never application."""

import hashlib
import json
import math

USAGE_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
)


def _payload(value):
    if isinstance(value, dict):
        return value
    if isinstance(value, list):
        texts = [
            v.get("text", "") for v in value if isinstance(v, dict) and v.get("type") == "text"
        ]
        value = "\n".join(texts)
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {}
    except (ValueError, TypeError):
        return {}


def _identifier(reference):
    return (
        reference.get("capability_id", reference.get("skill_id"))
        if isinstance(reference, dict)
        else None
    )


def parse_client_events(events, *, corpus=(), client_version=None, run_id=None, turn_id=None):
    """Correlate calls/results by tool ID and verify returned manual bytes against corpus.

    Input is trusted operator capture, not model-supplied evidence. Persist raw capture
    separately. Billing usage and host context remain distinct and missing data stays null.
    """
    if isinstance(events, str):
        events = [json.loads(line) for line in events.splitlines() if line.strip()]
    expected = {i["id"]: i["sha256"] for i in corpus}
    hash_ids = {i["sha256"]: i["id"] for i in corpus}
    calls, selected, loaded, warnings = {}, set(), set(), []
    usage = {k: None for k in USAGE_FIELDS}
    cost = duration = None
    init = None
    for event in events:
        if not isinstance(event, dict):
            warnings.append("invalid_event")
            continue
        if event.get("type") == "system" and event.get("subtype") == "init":
            init = event
            client_version = event.get("claude_code_version", client_version)
        if event.get("type") == "result":
            for key in USAGE_FIELDS:
                value = event.get("usage", {}).get(key)
                if type(value) in (int, float) and math.isfinite(value) and value >= 0:
                    usage[key] = value
            value = event.get("total_cost_usd")
            if type(value) in (int, float) and math.isfinite(value) and value >= 0:
                cost = value
            value = event.get("duration_ms")
            if type(value) in (int, float) and math.isfinite(value) and value >= 0:
                duration = value
        blocks = event.get("message", {}).get("content", [])
        if not isinstance(blocks, list):
            continue
        for block in blocks:
            if not isinstance(block, dict):
                continue
            kind = block.get("type")
            if kind == "tool_use" and event.get("type") == "assistant":
                tool_id = block.get("id")
                if not isinstance(tool_id, str) or tool_id in calls:
                    warnings.append("duplicate_or_invalid_tool_use")
                    continue
                name = block.get("name", "")
                arguments = block.get("input", {})
                if not isinstance(arguments, dict):
                    arguments = {}
                reference = arguments.get("reference")
                identifier = _identifier(reference)
                if name == "Skill":
                    identifier = arguments.get("skill")
                    if isinstance(identifier, str) and "/" not in identifier:
                        identifier = "ecc/" + identifier
                operation = name.split("__")[-1]
                if identifier and (
                    operation in ("load_capability", "load_skill", "plan_capabilities")
                    or name == "Skill"
                ):
                    selected.add(identifier)
                calls[tool_id] = {
                    "id": tool_id,
                    "name": name,
                    "operation": operation,
                    "reference": reference,
                    "capability_id": identifier,
                    "status": "unknown",
                    "loaded": False,
                    "result_observed": False,
                }
            elif kind == "tool_result" and event.get("type") == "user":
                tool_id = block.get("tool_use_id")
                if tool_id not in calls:
                    warnings.append("orphan_tool_result")
                    continue
                call = calls[tool_id]
                if call["result_observed"]:
                    warnings.append("duplicate_tool_result")
                    continue
                call["result_observed"] = True
                payload = _payload(block.get("content"))
                call["status"] = (
                    "failed"
                    if block.get("is_error") or payload.get("status") not in ("ok", None)
                    else "succeeded"
                )
                # Selection does not imply a read; native Skill's "Launching" message
                # is not a verifiable body delivery.
                if call["status"] == "failed" or call["operation"] not in (
                    "load_capability",
                    "load_skill",
                ):
                    continue
                candidates = (
                    [payload] + payload.get("dependencies", [])
                    if isinstance(payload.get("dependencies", []), list)
                    else [payload]
                )
                for candidate in candidates:
                    if not isinstance(candidate, dict):
                        continue
                    reference = candidate.get("reference")
                    identifier = _identifier(reference)
                    body = candidate.get("content")
                    if candidate is payload and reference != call["reference"]:
                        warnings.append("load_reference_mismatch")
                        continue
                    digest = (
                        hashlib.sha256(body.encode("utf-8")).hexdigest()
                        if isinstance(body, str)
                        else None
                    )
                    matched = hash_ids.get(digest)
                    if matched and (identifier not in expected or expected[identifier] == digest):
                        loaded.add(matched)
                        selected.add(matched)
                        call["loaded"] = True
                    else:
                        warnings.append("unverified_manual_body")
    return {
        "parser_version": 1,
        "run_id": run_id,
        "turn_id": turn_id,
        "client": {
            "name": "claude-code",
            "version": client_version,
            "model": init.get("model") if init else None,
        },
        "selected": sorted(selected),
        "loaded": sorted(loaded),
        "applied": [],
        "application_status": "unknown",
        "tool_calls": list(calls.values()),
        "usage": usage,
        "cost_usd": cost,
        "duration_ms": duration,
        "host_catalog": {
            "value": None,
            "unit": None,
            "method": None,
            "reason": "homogeneous_host_catalog_measurement_unavailable",
        },
        "warnings": list(dict.fromkeys(warnings)),
    }
