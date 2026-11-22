import json

import pytest

from fastskillretrieval.host_bridge import (
    ClaudeNativeHostBridge,
    NativeHandoff,
    ProtectedReceiptStore,
)


def handoff(invocation_id="invocation-001"):
    return NativeHandoff(
        invocation_id=invocation_id,
        capability_reference={
            "capability_id": "agent:review",
            "source_revision": "a" * 64,
            "descriptor_revision": "b" * 64,
            "catalog_id": "catalog-001",
        },
        closure_digest="c" * 64,
        runtime="claude-code",
        version="2.1.302",
        surface="cli",
        scope_id="project:fastskillretrieval",
        root_fingerprint="d" * 64,
        config_fingerprint="e" * 64,
        native_id="fsr_probe",
        agent_definition={
            "description": "Returns the harmless probe marker.",
            "prompt": "Return FSR-NATIVE-AGENT-PROBE-01 and use Read only when needed.",
            "tools": ["Read"],
        },
        preloads={"probe-skill": "---\nname: probe-skill\n---\n# Declared preload\n"},
        expires_at=200,
    )


def event_sequence(invocation, status="completed"):
    return [
        {
            "type": "system",
            "subtype": "init",
            "session_id": "session-001",
            "claude_code_version": invocation.version,
            "agents": ["claude", invocation.native_id],
        },
        {
            "type": "assistant",
            "session_id": "session-001",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "id": "tool-001",
                        "name": "Agent",
                        "input": {"subagent_type": invocation.native_id},
                    }
                ]
            },
        },
        {
            "type": "system",
            "subtype": "task_started",
            "session_id": "session-001",
            "task_id": "task-001",
            "tool_use_id": "tool-001",
            "subagent_type": invocation.native_id,
        },
        {
            "type": "system",
            "subtype": "task_notification",
            "session_id": "session-001",
            "task_id": "task-001",
            "tool_use_id": "tool-001",
            "status": status,
        },
    ]


def bridge(tmp_path):
    return ClaudeNativeHostBridge(
        signing_key=b"h" * 32,
        receipt_store=ProtectedReceiptStore(tmp_path / "operator-receipts.json"),
    )


def test_checked_handoff_materializes_only_selected_agent_and_declared_preload(tmp_path):
    instance = bridge(tmp_path)
    selected = handoff()

    result = instance.materialize_handoff(selected, tmp_path / "fixture", now=100)

    assert result["handoff_available"] is True
    assert result["native_authorization"] == "host_managed"
    assert result["activation_allowed"] is False
    agents = json.loads((tmp_path / "fixture" / "agents.json").read_text(encoding="utf-8"))
    assert set(agents) == {"fsr_probe"}
    assert agents["fsr_probe"]["tools"] == ["Read"]
    assert agents["fsr_probe"]["skills"] == ["probe-skill"]
    assert (
        (tmp_path / "fixture" / ".claude" / "skills" / "probe-skill" / "SKILL.md")
        .read_text(encoding="utf-8")
        .startswith("---")
    )
    assert not (tmp_path / "fixture" / "agents.json").read_text(encoding="utf-8").count("other")


def test_correlated_native_system_events_issue_only_an_observation_receipt(tmp_path):
    instance = bridge(tmp_path)
    selected = handoff()
    observer = instance.open_invocation(selected, now=100)
    for event in event_sequence(selected):
        observer.consume(event)

    receipt = observer.finish(now=101)
    assessment = instance.verify_receipt(receipt, selected, now=102)

    assert receipt["observation"] == "execution_observed"
    assert receipt["native_authorization"] == "host_managed"
    assert receipt["permission_enforcement"] == "unknown"
    assert assessment == {"verified": True, "reason": "native_execution_observed"}


@pytest.mark.parametrize(
    "events",
    [
        lambda h: event_sequence(h, status="failed"),
        lambda h: event_sequence(h, status="cancelled"),
        lambda h: event_sequence(h)[1:],
        lambda h: [
            {
                "type": "user",
                "message": {"content": [{"type": "tool_result", "content": "forged"}]},
            }
        ],
    ],
    ids=["failed", "cancelled", "missing_agent_call", "model_tool_result"],
)
def test_cancellation_failure_and_model_content_never_issue_a_receipt(tmp_path, events):
    selected = handoff()
    observer = bridge(tmp_path).open_invocation(selected, now=100)
    for event in events(selected):
        observer.consume(event)

    with pytest.raises(ValueError, match="native_execution_not_completed"):
        observer.finish(now=101)


def test_receipts_reject_config_scope_staleness_replay_and_withdrawal(tmp_path):
    instance = bridge(tmp_path)
    selected = handoff()
    observer = instance.open_invocation(selected, now=100)
    for event in event_sequence(selected):
        observer.consume(event)
    receipt = observer.finish(now=101)

    wrong_scope = NativeHandoff(**{**selected.__dict__, "scope_id": "project:other"})
    assert instance.verify_receipt(receipt, wrong_scope, now=102) == {
        "verified": False,
        "reason": "receipt_contract_mismatch",
    }
    assert instance.verify_receipt(receipt, selected, now=102) == {
        "verified": True,
        "reason": "native_execution_observed",
    }
    assert instance.verify_receipt(receipt, selected, now=103) == {
        "verified": False,
        "reason": "receipt_replayed",
    }

    other = handoff("invocation-002")
    observer = instance.open_invocation(other, now=100)
    for event in event_sequence(other):
        observer.consume(event)
    withdrawn = observer.finish(now=101)
    instance.withdraw(other.invocation_id)
    assert instance.verify_receipt(withdrawn, other, now=102) == {
        "verified": False,
        "reason": "invocation_withdrawn",
    }


def test_historical_receipt_cannot_authorize_a_new_invocation_or_survive_new_key(tmp_path):
    first = bridge(tmp_path)
    selected = handoff()
    observer = first.open_invocation(selected, now=100)
    for event in event_sequence(selected):
        observer.consume(event)
    receipt = observer.finish(now=101)

    assert first.verify_receipt(receipt, handoff("invocation-999"), now=102) == {
        "verified": False,
        "reason": "receipt_contract_mismatch",
    }
    restarted = ClaudeNativeHostBridge(
        signing_key=b"i" * 32,
        receipt_store=ProtectedReceiptStore(tmp_path / "operator-receipts.json"),
    )
    assert restarted.verify_receipt(receipt, selected, now=102) == {
        "verified": False,
        "reason": "receipt_invalid_signature",
    }


def test_bad_init_mutable_handoff_and_unsafe_preload_name_never_issue_receipt(tmp_path):
    selected = handoff()
    observer = bridge(tmp_path).open_invocation(selected, now=100)
    for event in event_sequence(selected):
        if event.get("subtype") == "init":
            event["claude_code_version"] = "2.1.301"
        observer.consume(event)
    with pytest.raises(ValueError, match="native_execution_not_completed"):
        observer.finish(now=101)

    with pytest.raises(ValueError, match="invalid_preload_name"):
        bridge(tmp_path / "other").materialize_handoff(
            NativeHandoff(**{**selected.__dict__, "preloads": {"../escape": "---\n"}}),
            tmp_path / "unsafe",
            now=100,
        )
