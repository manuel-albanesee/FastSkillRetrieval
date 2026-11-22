"""Diagnostic runtime adapter with a closed native execution gate.

No host attestation channel is implemented. Serializable binding metadata, model
self-report, and test fixtures consequently cannot authorize activation. There is
no subprocess launcher or shell fallback in this adapter.
"""

import hashlib
import math
import re

from .contracts import Failure, serialized, text

BINDING_STATES = {
    "unknown",
    "not_supported",
    "supported_not_installed",
    "installed_unavailable",
    "available_unapproved",
    "available_approved",
    "disabled_by_policy",
}
PUBLIC_CONFIG_FIELDS = {
    "runtime",
    "version",
    "surface",
    "native_ids",
    "tools",
    "enabled",
    "disabled",
    "scope_id",
}


def configuration_fingerprint(config: dict) -> str:
    """Hash explicitly public configuration; never include env or credential values."""
    if not isinstance(config, dict):
        raise Failure("needs_revision", "invalid_runtime_config")
    public = {}
    for key in sorted(PUBLIC_CONFIG_FIELDS & config.keys()):
        value = config[key]
        if isinstance(value, str):
            public[key] = text(value, 256)
        elif type(value) is bool:
            public[key] = value
        elif isinstance(value, list) and len(value) <= 256:
            public[key] = sorted({text(item, 256) for item in value})
        else:
            raise Failure("needs_revision", "invalid_public_runtime_config")
    return hashlib.sha256(serialized(public).encode("utf-8")).hexdigest()


def assess_binding(binding: dict | None, now: float, runtime: str = "claude-code") -> dict:
    """Assess diagnostic data without treating it as a trusted host observation.

    Expiry and runtime mismatches are useful even before an attestation channel is
    available. Positive claims always remain unknown; reported states are separate.
    """
    if type(now) not in (int, float) or not math.isfinite(now):
        raise Failure("needs_revision", "invalid_observation_time")
    result = {
        "status": "unknown",
        "activation_allowed": False,
        "native_execution_supported": False,
        "reason": "binding_unknown",
    }
    if binding is None or not isinstance(binding, dict) or not binding:
        return result
    reported = binding.get("status")
    if isinstance(reported, str) and reported in BINDING_STATES:
        result["reported_status"] = reported
    if binding.get("runtime", runtime) != runtime:
        result["reason"] = "runtime_mismatch"
        return result
    observed, expiry = binding.get("observed_at"), binding.get("expires_at")
    if expiry is not None:
        if type(expiry) not in (int, float) or not math.isfinite(expiry):
            result["reason"] = "invalid_binding_time"
            return result
        if expiry <= now:
            result["reason"] = "binding_expired"
            return result
    if observed is not None and (
        type(observed) not in (int, float) or not math.isfinite(observed) or observed > now
    ):
        result["reason"] = "invalid_binding_time"
        return result
    result["reason"] = "host_attestation_unavailable"
    return result


class ClaudeAdapter:
    """Claude Code configuration diagnostics; native activation is not implemented."""

    def report(self, version: str, surface: str) -> dict:
        return {
            "runtime": "claude-code",
            "version": text(version, 128),
            "surface": text(surface, 64),
            "observation_status": "unknown",
            "native_execution_supported": False,
            "mode": "discovery_and_dry_run",
            "reason": "host_attestation_unavailable",
        }

    def validate_binding(self, binding: dict) -> dict:
        fields = {"runtime", "version", "surface", "native_id", "config_fingerprint"}
        if not isinstance(binding, dict) or set(binding) != fields:
            raise Failure("needs_revision", "binding_fields")
        result = {key: text(value, 256) for key, value in binding.items()}
        if result["runtime"] != "claude-code":
            raise Failure("unsupported_integration", "runtime_mismatch")
        if not re.fullmatch(r"[a-f0-9]{64}", result["config_fingerprint"]):
            raise Failure("needs_revision", "invalid_config_fingerprint")
        return result
