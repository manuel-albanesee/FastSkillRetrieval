"""Strict parsing for Claude Code's local ``/context`` observation.

The result describes an estimated native context breakdown.  It is not model token
billing, a prompt-byte measurement, or evidence for an A/B/C context reduction.
"""

import hashlib
import json
import re
from decimal import Decimal, InvalidOperation


class ContextObservationError(ValueError):
    """The supplied result was not a zero-use local Claude context command."""


_TOKEN = re.compile(r"(?P<numeric>[0-9]+(?:\.[0-9]+)?)(?P<unit>[kKmM]?)\Z")
_HASH = re.compile(r"[a-f0-9]{64}\Z")


def _fail(reason: str) -> None:
    raise ContextObservationError(reason)


def _nonnegative_number(value: object) -> bool:
    return type(value) in {int, float} and value >= 0


def _zero(value: object) -> bool:
    return _nonnegative_number(value) and value == 0


def parse_estimated_tokens(value: object) -> dict:
    """Parse a displayed native estimate while retaining its original unit and spelling."""
    if not isinstance(value, str):
        _fail("invalid_native_token_estimate")
    raw = value.strip()
    match = _TOKEN.fullmatch(raw)
    if not match:
        _fail("invalid_native_token_estimate")
    numeric, unit = match.group("numeric"), match.group("unit").casefold()
    multiplier = {"": 1, "k": 1_000, "m": 1_000_000}[unit]
    try:
        estimated = Decimal(numeric) * multiplier
    except InvalidOperation as error:
        raise ContextObservationError("invalid_native_token_estimate") from error
    if estimated != estimated.to_integral_value():
        _fail("non_integral_native_token_estimate")
    return {
        "raw": raw,
        "numeric": numeric,
        "unit": unit,
        "estimated_tokens": int(estimated),
    }


def _parse_categories(result: object) -> list[dict]:
    if not isinstance(result, str):
        _fail("missing_context_result")
    lines = [line.strip() for line in result.splitlines()]
    try:
        start = lines.index("### Estimated usage by category")
    except ValueError as error:
        raise ContextObservationError("missing_category_table") from error
    start += 1
    while start < len(lines) and not lines[start]:
        start += 1
    if start >= len(lines) or lines[start] != "| Category | Tokens | Percentage |":
        _fail("invalid_category_table")
    start += 1
    if start >= len(lines) or not re.fullmatch(r"\|[-| ]+\|", lines[start]):
        _fail("invalid_category_table")
    categories = []
    for line in lines[start + 1 :]:
        if not line:
            break
        if not line.startswith("|"):
            break
        parts = [part.strip() for part in line.split("|")]
        if len(parts) != 5 or parts[0] or parts[-1]:
            _fail("invalid_category_row")
        category, tokens, percentage = parts[1:4]
        if not category or not percentage.endswith("%"):
            _fail("invalid_category_row")
        try:
            percent = float(percentage[:-1])
        except ValueError as error:
            raise ContextObservationError("invalid_category_row") from error
        if percent < 0:
            _fail("invalid_category_row")
        categories.append(
            {
                "category": category,
                "scope": "native_context",
                **parse_estimated_tokens(tokens),
                "percentage": percent,
            }
        )
    if not categories:
        _fail("missing_category_rows")
    return categories


def _require_zero_use(payload: dict) -> None:
    if payload.get("type") != "result" or payload.get("local_command") != "context":
        _fail("not_local_context")
    if not _zero(payload.get("duration_api_ms")) or payload.get("num_turns") != 0:
        _fail("model_turns_observed")
    if not _zero(payload.get("total_cost_usd")) or payload.get("modelUsage") != {}:
        _fail("model_usage_observed")
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        _fail("model_usage_observed")
    for key in (
        "input_tokens",
        "output_tokens",
        "cache_creation_input_tokens",
        "cache_read_input_tokens",
    ):
        if not _zero(usage.get(key)):
            _fail("model_usage_observed")
    details, server = usage.get("output_tokens_details"), usage.get("server_tool_use")
    if (
        usage.get("iterations") != []
        or not isinstance(details, dict)
        or not _zero(details.get("thinking_tokens"))
        or not isinstance(server, dict)
        or not _zero(server.get("web_search_requests"))
        or not _zero(server.get("web_fetch_requests"))
    ):
        _fail("model_usage_observed")


def parse_context_result(
    payload: str | bytes | dict,
    *,
    client_version: str,
    source_fixture_hash: str,
    source_manifest_hash: str,
) -> dict:
    """Validate a local context result and return a neutral evaluation observation."""
    if isinstance(payload, bytes):
        try:
            payload = payload.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ContextObservationError("invalid_context_json") from error
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError as error:
            raise ContextObservationError("invalid_context_json") from error
    if not isinstance(payload, dict):
        _fail("invalid_context_json")
    if (
        not isinstance(client_version, str)
        or not client_version.strip()
        or not isinstance(source_fixture_hash, str)
        or not _HASH.fullmatch(source_fixture_hash)
        or not isinstance(source_manifest_hash, str)
        or not _HASH.fullmatch(source_manifest_hash)
    ):
        _fail("invalid_observation_provenance")
    _require_zero_use(payload)
    categories = _parse_categories(payload.get("result"))
    observation_id = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()
    return {
        "kind": "native_client_observation",
        "observation_id": observation_id,
        "runtime": "claude-code",
        "client_version": client_version.strip(),
        "source_fixture_hash": source_fixture_hash,
        "source_manifest_hash": source_manifest_hash,
        "local_command": "context",
        "execution": {"duration_api_ms": 0, "num_turns": 0, "model_usage": "zero"},
        "measurements": {"unit": "estimated_tokens", "categories": categories},
        "context_bytes": None,
        "comparability": {
            "with_response_bytes": False,
            "reduction_asserted": False,
            "reason": "native_estimated_tokens_are_not_response_bytes",
        },
    }
