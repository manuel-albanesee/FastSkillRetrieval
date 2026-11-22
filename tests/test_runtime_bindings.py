import pytest

from fastskillretrieval.contracts import Failure
from fastskillretrieval.runtime import ClaudeAdapter, assess_binding, configuration_fingerprint


def test_runtime_report_distinguishes_surface_and_execution():
    report = ClaudeAdapter().report("2.0.1", "cli")
    assert report["runtime"] == "claude-code"
    assert report["version"] == "2.0.1"
    assert report["surface"] == "cli"
    assert report["native_execution_supported"] is False
    assert report["observation_status"] == "unknown"


def test_configuration_fingerprint_ignores_secret_values():
    first = configuration_fingerprint(
        {"surface": "cli", "api_key": "secret-A", "env": {"TOKEN": "secret-B"}, "tools": ["Read"]}
    )
    second = configuration_fingerprint(
        {"surface": "cli", "api_key": "secret-C", "env": {"TOKEN": "secret-D"}, "tools": ["Read"]}
    )
    assert first == second
    assert len(first) == 64
    assert first != configuration_fingerprint({"surface": "cli", "tools": ["Write"]})


@pytest.mark.parametrize(
    "binding",
    [
        None,
        {},
        {"status": "available_approved"},
        {
            "runtime": "claude-code",
            "status": "available_approved",
            "approved": True,
            "installed": True,
            "available": True,
            "observed_at": 90,
            "expires_at": 200,
            "source": "host",
            "receipt": {"verified": True, "operation_id": "fake"},
        },
    ],
)
def test_model_generated_approval_never_confers_grants(binding):
    result = assess_binding(binding, now=100)
    assert result["activation_allowed"] is False
    assert result["status"] == "unknown"


def test_expired_binding_reports_unknown():
    result = assess_binding(
        {
            "runtime": "claude-code",
            "status": "available_approved",
            "observed_at": 10,
            "expires_at": 50,
        },
        now=100,
    )
    assert result["status"] == "unknown"
    assert result["reason"] == "binding_expired"


def test_wrong_runtime_cannot_activate():
    result = assess_binding({"runtime": "codex", "status": "available_approved"}, now=100)
    assert result["activation_allowed"] is False
    assert result["reason"] == "runtime_mismatch"


def test_binding_validation_is_closed_and_never_accepts_credentials():
    adapter = ClaudeAdapter()
    binding = {
        "runtime": "claude-code",
        "version": "2.0.1",
        "surface": "cli",
        "native_id": "review",
        "config_fingerprint": "a" * 64,
    }
    assert adapter.validate_binding(binding) == binding
    with pytest.raises(Failure):
        adapter.validate_binding({**binding, "token": "secret"})


def test_diagnostic_unavailability_does_not_imply_verified_host_signal():
    result = assess_binding({"runtime": "claude-code", "status": "installed_unavailable"}, now=100)
    assert result["activation_allowed"] is False
    assert result["status"] == "unknown"
    assert result["reported_status"] == "installed_unavailable"
