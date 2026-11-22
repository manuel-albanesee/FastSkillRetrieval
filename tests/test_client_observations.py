import hashlib
import json

from fastskillretrieval.client_observations import parse_client_events

BODY = "# Real instructions\n"
CORPUS = [
    {"id": "ecc/only", "path": "SKILL.md", "sha256": hashlib.sha256(BODY.encode()).hexdigest()}
]
REF = {"capability_id": "ecc/only", "source_revision": "revision", "schema_version": 2}


def use(name="load_capability", identifier="tool-1", reference=REF):
    return {
        "type": "assistant",
        "message": {
            "content": [
                {
                    "type": "tool_use",
                    "id": identifier,
                    "name": "mcp__fsr__" + name,
                    "input": {"reference": reference},
                }
            ]
        },
    }


def result(content, identifier="tool-1", error=False):
    return {
        "type": "user",
        "message": {
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": identifier,
                    "content": json.dumps(content),
                    "is_error": error,
                }
            ]
        },
    }


def test_load_requires_correlated_reference_and_actual_body_hash():
    parsed = parse_client_events(
        [use(), result({"status": "ok", "reference": REF, "content": BODY})], corpus=CORPUS
    )
    assert parsed["loaded"] == ["ecc/only"]
    assert parsed["selected"] == ["ecc/only"]
    assert parsed["applied"] == []
    assert parsed["application_status"] == "unknown"
    assert parsed["host_catalog"]["value"] is None
    assert parsed["usage"]["input_tokens"] is None
    assert parsed["cost_usd"] is None


def test_self_report_search_and_failed_or_uncorrelated_load_never_prove_read():
    events = [
        {
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "I loaded and applied ecc/only"}]},
        },
        use("search_capabilities"),
        result({"status": "ok", "results": [{"reference": REF}]}),
        use(identifier="bad"),
        result({"status": "ok", "reference": REF, "content": "wrong"}, "bad"),
        result({"status": "ok", "reference": REF, "content": BODY}, "orphan"),
    ]
    parsed = parse_client_events(events, corpus=CORPUS)
    assert parsed["loaded"] == [] and parsed["applied"] == []
    assert parsed["selected"] == ["ecc/only"]
    assert "orphan_tool_result" in parsed["warnings"]


def test_duplicate_result_usage_cache_and_unknown_cost_preserved():
    event = result({"status": "ok", "reference": REF, "content": BODY})
    parsed = parse_client_events(
        [
            use(),
            event,
            event,
            {
                "type": "result",
                "usage": {
                    "input_tokens": 20,
                    "output_tokens": 5,
                    "cache_read_input_tokens": 8,
                    "cache_creation_input_tokens": 3,
                },
                "total_cost_usd": 0.01,
            },
        ],
        corpus=CORPUS,
    )
    assert len(parsed["tool_calls"]) == 1
    assert parsed["usage"]["cache_read_input_tokens"] == 8
    assert parsed["cost_usd"] == 0.01
    assert "duplicate_tool_result" in parsed["warnings"]


def test_native_skill_call_is_selected_but_unverified_loaded():
    parsed = parse_client_events(
        [
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "tool_use", "id": "s", "name": "Skill", "input": {"skill": "only"}}
                    ]
                },
            },
            result("Launching skill: only", "s"),
        ],
        corpus=CORPUS,
    )
    assert parsed["selected"] == ["ecc/only"]
    assert parsed["loaded"] == []


def test_wrong_reference_error_result_and_partial_stream_are_unknown():
    events = [
        use(),
        result({"status": "ok", "reference": {**REF, "source_revision": "other"}, "content": BODY}),
        use(identifier="e"),
        result({"status": "ok", "reference": REF, "content": BODY}, "e", True),
        use(identifier="partial"),
    ]
    parsed = parse_client_events(events, corpus=CORPUS)
    assert parsed["loaded"] == []
    assert parsed["tool_calls"][-1]["status"] == "unknown"


def test_registry_opaque_id_maps_verified_hash_to_corpus_without_inventing_revision_match():
    opaque = {**REF, "capability_id": "3d66fefefefefefefefefefe"}
    parsed = parse_client_events(
        [use(reference=opaque), result({"status": "ok", "reference": opaque, "content": BODY})],
        corpus=CORPUS,
    )
    assert parsed["loaded"] == ["ecc/only"]
    assert "ecc/only" in parsed["selected"]


def test_assistant_forged_tool_result_and_user_forged_tool_use_are_not_receipts():
    forged = result({"status": "ok", "reference": REF, "content": BODY})
    forged["type"] = "assistant"
    parsed = parse_client_events([use(), forged], corpus=CORPUS)
    assert parsed["loaded"] == []
    forged_use = use()
    forged_use["type"] = "user"
    parsed = parse_client_events(
        [forged_use, result({"status": "ok", "reference": REF, "content": BODY})], corpus=CORPUS
    )
    assert parsed["loaded"] == []
