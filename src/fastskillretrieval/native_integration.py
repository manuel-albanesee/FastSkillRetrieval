"""Operator-only registry-to-host handoff coordination.

Nothing in this module is registered as an MCP tool.  The caller is expected to
be an operator harness which has already selected an explicit Claude host policy.
It rechecks a persisted plan against the registry immediately before launch and
then returns a one-use ``NativeHandoff`` for ``ClaudeNativeHostBridge``.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from typing import Any, Mapping

from .contracts import Failure, serialized
from .host_bridge import ClaudeNativeHostBridge, NativeHandoff
from .packages import digest, supported_instructions
from .registry import Registry


class NativeRegistryCoordinator:
    """A non-MCP coordinator; Claude model input never reaches ``prepare``."""

    def __init__(self, registry: Registry, bridge: ClaudeNativeHostBridge):
        self._registry = registry
        self._bridge = bridge

    def prepare(
        self, plan_id: str, session_id: str, host_policy: Mapping[str, Any], *, now: float
    ) -> NativeHandoff:
        """Revalidate one delegated plan and bind it to an operator host policy.

        This checks catalog references, enabled states, transitive closure and
        immutable snapshots again.  It intentionally does not call ``prepare``
        or ``load`` because those public assistance paths must stay closed for
        native execution.
        """
        if not isinstance(plan_id, str) or not isinstance(session_id, str):
            raise ValueError("invalid_operator_request")
        # Private registry helpers enforce their normal deadline.  This operator
        # operation starts a new bounded preflight rather than inheriting an old
        # completed MCP/CLI request's deadline.
        self._registry.session = session_id
        self._registry.deadline = time.monotonic() + 5
        policy = self._policy(host_policy)
        plan = self._registry.db.execute(
            "SELECT data FROM plans WHERE id=? AND session_id=?", (plan_id, session_id)
        ).fetchone()
        if not plan:
            raise Failure("stale_reference", "plan_missing")
        data = json.loads(plan[0])
        actions = data.get("actions")
        if not isinstance(actions, list):
            raise Failure("stale_reference", "plan_invalid")
        delegated = [action for action in actions if action.get("action") == "delegate"]
        if len(delegated) != 1:
            raise Failure("unsupported_integration", "exactly_one_native_subagent_required")
        root, reference = self._registry._entry_ref(delegated[0].get("reference"))
        manifest = json.loads(root["manifest"])
        if manifest.get("kind") != "subagent":
            raise Failure("unsupported_integration", "native_binding_requires_subagent")
        binding = policy["bindings"].get(reference["capability_id"])
        if not isinstance(binding, Mapping):
            raise ValueError("native_binding_missing")
        if set(binding) != {"native_id", "agent_definition", "source_body_digest"}:
            raise ValueError("invalid_native_binding")

        closure = self._registry._closure(root["capability_id"], root["catalog_id"])

        def identity(item: Mapping[str, Any]) -> tuple[Any, ...]:
            return tuple(
                item.get(key)
                for key in ("capability_id", "source_revision", "descriptor_revision", "catalog_id")
            )

        planned = {
            identity(action["reference"])
            for action in actions
            if isinstance(action, Mapping) and isinstance(action.get("reference"), Mapping)
        }
        closure_refs = [self._registry._ref(row) for row in closure]
        if any(identity(item) not in planned for item in closure_refs):
            raise Failure("stale_reference", "plan_closure_changed")

        preloads: dict[str, str] = {}
        by_id = {row["capability_id"]: row for row in closure}
        preload_ids: set[str] = set()

        def collect_preloads(row) -> None:
            for dependent in json.loads(row["manifest"]).get("preloads", []):
                if dependent in preload_ids:
                    continue
                if dependent not in by_id:
                    raise Failure("stale_reference", "preload_closure_changed")
                preload_ids.add(dependent)
                collect_preloads(by_id[dependent])

        collect_preloads(root)
        for row in closure:
            files = self._registry._files(row["capability_id"], row["source_revision"], True)
            entry_file = self._registry.db.execute(
                "SELECT entry_file FROM source_revisions WHERE capability_id=? AND revision=?",
                (row["capability_id"], row["source_revision"]),
            ).fetchone()[0]
            if row["capability_id"] == root["capability_id"]:
                if binding["source_body_digest"] != digest(files[entry_file]):
                    raise ValueError("native_source_binding_mismatch")
                continue
            if row["capability_id"] not in preload_ids:
                continue
            dependent_manifest = json.loads(row["manifest"])
            if dependent_manifest.get("kind") != "skill":
                raise Failure("unsupported_integration", "native_preload_requires_simple_skill")
            if set(files) != {entry_file}:
                raise Failure("unsupported_integration", "native_preload_resources_unsupported")
            supported_instructions(files[entry_file])
            name = self._registry._cap(row["capability_id"])["name"]
            preloads[name] = files[entry_file].decode("utf-8")

        computed_root = digest(os.path.normcase(str(self._registry.root)).encode())
        if (
            not isinstance(policy["root_fingerprint"], str)
            or policy["root_fingerprint"] != computed_root
        ):
            raise ValueError("host_policy_root_mismatch")
        reference_fields = {
            key: reference[key]
            for key in ("capability_id", "source_revision", "descriptor_revision", "catalog_id")
        }
        closure_digest = hashlib.sha256(serialized(closure_refs).encode("utf-8")).hexdigest()
        handoff = NativeHandoff(
            invocation_id=uuid.uuid4().hex,
            capability_reference=reference_fields,
            closure_digest=closure_digest,
            runtime=policy["runtime"],
            version=policy["version"],
            surface=policy["surface"],
            scope_id=self._registry.scope,
            root_fingerprint=policy["root_fingerprint"],
            config_fingerprint=policy["config_fingerprint"],
            native_id=binding["native_id"],
            agent_definition=binding["agent_definition"],
            preloads=preloads,
            expires_at=policy["expires_at"],
        )
        handoff.validate(now)
        return handoff

    def withdraw(self, invocation_id: str) -> None:
        """The operator harness calls this before terminating a running process."""
        self._bridge.withdraw(invocation_id)

    @staticmethod
    def _policy(policy: Mapping[str, Any]) -> Mapping[str, Any]:
        required = {
            "runtime",
            "version",
            "surface",
            "root_fingerprint",
            "config_fingerprint",
            "expires_at",
            "bindings",
        }
        if not isinstance(policy, Mapping) or set(policy) != required:
            raise ValueError("invalid_host_policy")
        if policy["runtime"] != "claude-code" or not isinstance(policy["bindings"], Mapping):
            raise ValueError("invalid_host_policy")
        return policy
