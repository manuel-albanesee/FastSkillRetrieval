"""Operator-only Claude native handoff and observation receipts.

This module deliberately does not launch Claude Code and is not exposed through
the MCP server.  A trusted operator harness materializes one handoff, owns the
Claude process, and feeds its own stream-json pipe to an ``InvocationCapture``.
Model messages, including tool results, cannot produce a receipt by themselves.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import re
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .packages import safe_path

_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_FILE_COMPONENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")
_HASH = re.compile(r"[a-f0-9]{64}\Z")
_STORE_FIELDS = {"issued", "verified", "withdrawn"}


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _identifier(value: object, field: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"invalid_{field}")
    return value


def _hash(value: object, field: str) -> str:
    if not isinstance(value, str) or not _HASH.fullmatch(value):
        raise ValueError(f"invalid_{field}")
    return value


def _file_component(value: object, field: str) -> str:
    reserved = {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{n}" for n in range(1, 10)),
        *(f"LPT{n}" for n in range(1, 10)),
    }
    if (
        not isinstance(value, str)
        or not _FILE_COMPONENT.fullmatch(value)
        or value.upper() in reserved
    ):
        raise ValueError(f"invalid_{field}")
    return value


def _number(value: object, field: str) -> float:
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError(f"invalid_{field}")
    return float(value)


@dataclass(frozen=True)
class NativeHandoff:
    """A single, preflighted native invocation; this is never an authority grant."""

    invocation_id: str
    capability_reference: Mapping[str, Any]
    closure_digest: str
    runtime: str
    version: str
    surface: str
    scope_id: str
    root_fingerprint: str
    config_fingerprint: str
    native_id: str
    agent_definition: Mapping[str, Any]
    preloads: Mapping[str, str]
    expires_at: float

    def validate(self, now: float) -> None:
        _identifier(self.invocation_id, "invocation_id")
        if self.runtime != "claude-code":
            raise ValueError("invalid_runtime")
        for field in ("version", "surface", "scope_id", "native_id"):
            _identifier(getattr(self, field), field)
        for field in ("closure_digest", "root_fingerprint", "config_fingerprint"):
            _hash(getattr(self, field), field)
        _number(now, "observation_time")
        _number(self.expires_at, "expiry")
        if self.expires_at <= now:
            raise ValueError("handoff_expired")
        reference_fields = {
            "capability_id",
            "source_revision",
            "descriptor_revision",
            "catalog_id",
        }
        if set(self.capability_reference) != reference_fields:
            raise ValueError("invalid_capability_reference")
        _identifier(self.capability_reference["capability_id"], "capability_id")
        catalog_id = self.capability_reference["catalog_id"]
        if not (
            (type(catalog_id) is int and catalog_id > 0)
            or (isinstance(catalog_id, str) and _IDENTIFIER.fullmatch(catalog_id))
        ):
            raise ValueError("invalid_catalog_id")
        _hash(self.capability_reference["source_revision"], "source_revision")
        _hash(self.capability_reference["descriptor_revision"], "descriptor_revision")
        if set(self.agent_definition) != {"description", "prompt", "tools"}:
            raise ValueError("invalid_agent_definition")
        for field in ("description", "prompt"):
            value = self.agent_definition[field]
            if not isinstance(value, str) or not value.strip() or len(value) > 16384:
                raise ValueError("invalid_agent_definition")
        tools = self.agent_definition["tools"]
        if not isinstance(tools, list) or not tools or len(tools) > 64:
            raise ValueError("invalid_agent_tools")
        if any(not isinstance(tool, str) or not _IDENTIFIER.fullmatch(tool) for tool in tools):
            raise ValueError("invalid_agent_tools")
        if not isinstance(self.preloads, Mapping) or len(self.preloads) > 32:
            raise ValueError("invalid_preloads")
        for name, content in self.preloads.items():
            _file_component(name, "preload_name")
            if (
                not isinstance(content, str)
                or not content.startswith("---")
                or len(content) > 262144
            ):
                raise ValueError("invalid_preload")

    def contract(self) -> dict[str, object]:
        """The complete immutable binding checked when a receipt is consumed."""
        return {
            "invocation_id": self.invocation_id,
            "capability_reference": dict(self.capability_reference),
            "closure_digest": self.closure_digest,
            "runtime": self.runtime,
            "version": self.version,
            "surface": self.surface,
            "scope_id": self.scope_id,
            "root_fingerprint": self.root_fingerprint,
            "config_fingerprint": self.config_fingerprint,
            "native_id": self.native_id,
            "agent_definition_digest": _digest(dict(self.agent_definition)),
            "preloads_digest": _digest(dict(self.preloads)),
            "expires_at": self.expires_at,
        }


class ProtectedReceiptStore:
    """Small operator-owned replay/withdrawal store; never made available to MCP."""

    def __init__(self, path: Path):
        self.path = safe_path(Path(path))
        if self.path.name in {"", ".", ".."}:
            raise ValueError("invalid_receipt_store")
        self._lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        self._data = self._load()

    def _load(self) -> dict[str, dict[str, object]]:
        if not self.path.exists():
            return {field: {} for field in _STORE_FIELDS}
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError("invalid_receipt_store") from error
        if not isinstance(loaded, dict) or set(loaded) != _STORE_FIELDS:
            raise ValueError("invalid_receipt_store")
        if any(not isinstance(loaded[field], dict) for field in _STORE_FIELDS):
            raise ValueError("invalid_receipt_store")
        return loaded

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        safe_path(self.path.parent)
        temporary = self.path.with_suffix(self.path.suffix + "." + secrets.token_hex(8) + ".tmp")
        temporary.write_bytes(_canonical(self._data))
        os.replace(temporary, self.path)

    def _locked(self):
        """A small cross-process lock for the nonce file, including Windows hosts."""
        parent = self.path.parent
        parent.mkdir(parents=True, exist_ok=True)
        safe_path(parent)
        for _ in range(100):
            try:
                descriptor = os.open(self._lock_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                os.close(descriptor)
                return
            except FileExistsError:
                time.sleep(0.01)
        raise ValueError("receipt_store_locked")

    def _unlock(self) -> None:
        try:
            self._lock_path.unlink()
        except FileNotFoundError:
            pass

    def issue(self, invocation_id: str, receipt_id: str) -> bool:
        self._locked()
        try:
            self._data = self._load()
            if invocation_id in self._data["issued"] or invocation_id in self._data["withdrawn"]:
                return False
            self._data["issued"][invocation_id] = receipt_id
            self._save()
            return True
        finally:
            self._unlock()

    def withdrawn(self, invocation_id: str) -> bool:
        self._data = self._load()
        return invocation_id in self._data["withdrawn"]

    def withdraw(self, invocation_id: str) -> None:
        self._locked()
        try:
            self._data = self._load()
            self._data["withdrawn"][invocation_id] = True
            self._save()
        finally:
            self._unlock()

    def verify_once(self, receipt_id: str) -> bool:
        self._locked()
        try:
            self._data = self._load()
            if receipt_id in self._data["verified"]:
                return False
            self._data["verified"][receipt_id] = True
            self._save()
            return True
        finally:
            self._unlock()

    def issued_for(self, invocation_id: str) -> str | None:
        self._data = self._load()
        receipt_id = self._data["issued"].get(invocation_id)
        return receipt_id if isinstance(receipt_id, str) else None


class InvocationCapture:
    """Consumes only events forwarded directly by the operator harness pipe."""

    def __init__(self, bridge: ClaudeNativeHostBridge, handoff: NativeHandoff, now: float):
        self._bridge = bridge
        self._handoff = handoff
        self._handoff.validate(now)
        self._contract = handoff.contract()
        self._initialized = False
        self._session_id: str | None = None
        self._tool_use_id: str | None = None
        self._task_id: str | None = None
        self._completed = False
        self._failed = False
        self._terminal_seen = False
        self._finished = False
        self._evidence: list[dict[str, str]] = []

    def consume(self, event: Mapping[str, Any]) -> None:
        """Record the minimum correlated event data; ignored content cannot authorize anything."""
        if self._finished or not isinstance(event, Mapping):
            return
        if event.get("type") == "system" and event.get("subtype") == "init":
            agents = event.get("agents")
            if (
                event.get("claude_code_version") == self._handoff.version
                and isinstance(agents, list)
                and self._handoff.native_id in agents
                and isinstance(event.get("session_id"), str)
            ):
                self._initialized = True
                self._session_id = event["session_id"]
                self._evidence.append({"event": "runtime_init", "version": self._handoff.version})
            return
        if not self._initialized:
            return
        if event.get("session_id") != self._session_id:
            return
        if event.get("type") == "assistant":
            message = event.get("message")
            content = message.get("content") if isinstance(message, Mapping) else None
            if isinstance(content, list) and self._tool_use_id is None:
                for part in content:
                    if not isinstance(part, Mapping) or part.get("type") != "tool_use":
                        continue
                    details = part.get("input")
                    tool_id = part.get("id")
                    if (
                        part.get("name") == "Agent"
                        and isinstance(details, Mapping)
                        and details.get("subagent_type") == self._handoff.native_id
                        and isinstance(tool_id, str)
                        and event.get("parent_tool_use_id") is None
                    ):
                        self._tool_use_id = tool_id
                        self._evidence.append({"event": "agent_tool_use", "tool_use_id": tool_id})
                        return
            return
        if event.get("type") != "system" or self._tool_use_id is None:
            return
        subtype = event.get("subtype")
        if subtype == "task_started":
            task_id = event.get("task_id")
            if (
                self._task_id is None
                and event.get("tool_use_id") == self._tool_use_id
                and event.get("subagent_type") == self._handoff.native_id
                and isinstance(task_id, str)
            ):
                self._task_id = task_id
                self._evidence.append({"event": "task_started", "task_id": task_id})
            return
        if self._task_id is None or event.get("task_id") != self._task_id:
            return
        status = event.get("status")
        if subtype in {"task_notification", "task_updated"} and status in {
            "completed",
            "failed",
            "cancelled",
            "canceled",
            "interrupted",
        }:
            if self._terminal_seen:
                self._failed = True
                return
            self._terminal_seen = True
        if subtype in {"task_notification", "task_updated"} and status in {
            "failed",
            "cancelled",
            "canceled",
            "interrupted",
        }:
            self._failed = True
            self._evidence.append({"event": "task_not_completed", "status": str(status)})
            return
        if (
            subtype == "task_notification"
            and status == "completed"
            and event.get("tool_use_id") == self._tool_use_id
            and not self._failed
        ):
            self._completed = True
            self._evidence.append({"event": "task_completed", "task_id": self._task_id})

    def finish(self, *, now: float) -> dict[str, object]:
        if self._finished or not self._initialized or not self._completed or self._failed:
            raise ValueError("native_execution_not_completed")
        self._handoff.validate(now)
        if self._handoff.contract() != self._contract:
            raise ValueError("handoff_mutated")
        self._finished = True
        return self._bridge._issue(self._handoff, self._contract, self._evidence, now)


class ClaudeNativeHostBridge:
    """Host-side handoff materializer and receipt verifier, never a Claude launcher."""

    def __init__(self, signing_key: bytes, receipt_store: ProtectedReceiptStore):
        if not isinstance(signing_key, bytes) or len(signing_key) < 32:
            raise ValueError("invalid_signing_key")
        self._key = signing_key
        self._store = receipt_store

    def materialize_handoff(self, handoff: NativeHandoff, destination: Path, *, now: float) -> dict:
        handoff.validate(now)
        if self._store.withdrawn(handoff.invocation_id):
            raise ValueError("invocation_withdrawn")
        root = safe_path(Path(destination))
        if root.exists():
            raise ValueError("handoff_destination_exists")
        root.mkdir(parents=True)
        safe_path(root)
        _file_component(handoff.native_id, "native_id")
        agent = {**dict(handoff.agent_definition), "skills": sorted(handoff.preloads)}
        (root / "agents.json").write_bytes(_canonical({handoff.native_id: agent}))
        preload_paths = []
        for name, content in sorted(handoff.preloads.items()):
            path = root / ".claude" / "skills" / name / "SKILL.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8", newline="\n")
            preload_paths.append(str(path))
        return {
            "handoff_available": True,
            "native_authorization": "host_managed",
            "activation_allowed": False,
            "permission_enforcement": "unknown",
            "agents_path": str(root / "agents.json"),
            "preload_paths": preload_paths,
        }

    def open_invocation(self, handoff: NativeHandoff, *, now: float) -> InvocationCapture:
        if self._store.withdrawn(handoff.invocation_id):
            raise ValueError("invocation_withdrawn")
        return InvocationCapture(self, handoff, now)

    def withdraw(self, invocation_id: str) -> None:
        """Retire an operation before launch or while its harness is being cancelled."""
        _identifier(invocation_id, "invocation_id")
        self._store.withdraw(invocation_id)

    def _issue(
        self,
        handoff: NativeHandoff,
        contract: dict[str, object],
        evidence: list[dict[str, str]],
        now: float,
    ) -> dict:
        receipt_id = secrets.token_hex(16)
        if not self._store.issue(handoff.invocation_id, receipt_id):
            raise ValueError("invocation_replayed_or_withdrawn")
        payload = {
            "schema_version": 1,
            "receipt_id": receipt_id,
            "contract": contract,
            "observed_at": now,
            "expires_at": handoff.expires_at,
            "observation": "execution_observed",
            "native_authorization": "host_managed",
            "permission_enforcement": "unknown",
            "event_digest": _digest(evidence),
        }
        signature = hmac.new(self._key, _canonical(payload), hashlib.sha256).hexdigest()
        return {"payload": payload, "signature": signature, **payload}

    def verify_receipt(
        self, receipt: Mapping[str, Any], handoff: NativeHandoff, *, now: float
    ) -> dict:
        try:
            _number(now, "observation_time")
            payload = receipt["payload"]
            signature = receipt["signature"]
            if not isinstance(payload, Mapping) or not isinstance(signature, str):
                return {"verified": False, "reason": "receipt_invalid_format"}
            required = {
                "schema_version",
                "receipt_id",
                "contract",
                "observed_at",
                "expires_at",
                "observation",
                "native_authorization",
                "permission_enforcement",
                "event_digest",
            }
            if set(payload) != required or payload.get("schema_version") != 1:
                return {"verified": False, "reason": "receipt_invalid_format"}
            expected = hmac.new(self._key, _canonical(dict(payload)), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(expected, signature):
                return {"verified": False, "reason": "receipt_invalid_signature"}
            expires_at = _number(payload.get("expires_at"), "receipt_expiry")
            observed_at = _number(payload.get("observed_at"), "receipt_observed_at")
            if observed_at > now:
                return {"verified": False, "reason": "receipt_invalid_time"}
            if (
                payload.get("contract") != handoff.contract()
                or payload.get("observation") != "execution_observed"
            ):
                return {"verified": False, "reason": "receipt_contract_mismatch"}
            if expires_at != handoff.expires_at or now >= expires_at:
                return {"verified": False, "reason": "receipt_expired"}
            receipt_id = payload.get("receipt_id")
            event_digest = payload.get("event_digest")
            if (
                not isinstance(receipt_id, str)
                or not re.fullmatch(r"[a-f0-9]{32}", receipt_id)
                or not isinstance(event_digest, str)
                or not _HASH.fullmatch(event_digest)
                or payload.get("native_authorization") != "host_managed"
                or payload.get("permission_enforcement") != "unknown"
            ):
                return {"verified": False, "reason": "receipt_invalid_format"}
            if self._store.withdrawn(handoff.invocation_id):
                return {"verified": False, "reason": "invocation_withdrawn"}
            if self._store.issued_for(handoff.invocation_id) != receipt_id:
                return {"verified": False, "reason": "receipt_not_issued"}
            if not self._store.verify_once(receipt_id):
                return {"verified": False, "reason": "receipt_replayed"}
        except ValueError:
            return {"verified": False, "reason": "receipt_contract_mismatch"}
        return {"verified": True, "reason": "native_execution_observed"}
