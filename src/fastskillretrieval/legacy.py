"""Skill-only compatibility facade over the opt-in capability registry.

All storage and authorization checks remain in Registry. Projections replace
verbose v2 fields with shorter legacy fields, preserving conservative accounting.
"""

from .registry import Registry


def _legacy_reference(reference: dict) -> dict:
    if reference.get("schema_version") == 2:
        return {
            "catalog": reference["catalog_id"],
            "skill_id": reference["capability_id"],
            "version": reference["source_revision"],
        }
    return reference


def _project(value):
    if isinstance(value, list):
        return [_project(item) for item in value]
    if not isinstance(value, dict):
        return value
    if value.get("schema_version") == 2 and "capability_id" in value:
        return _legacy_reference(value)
    result = {key: _project(item) for key, item in value.items()}
    if "catalog_id" in result:
        result["catalog"] = result.pop("catalog_id")
    return result


class LegacyFacade:
    def __init__(self, registry: Registry):
        self.registry = registry

    def call(self, operation: str, session_id: str = "default", **arguments) -> dict:
        if operation == "inventory":
            if arguments.pop("refresh", False):
                discovered = self.registry.call("discover", session_id)
                if discovered["status"] != "ok":
                    return discovered
            result = self.registry.call("inventory", session_id, kinds=["skill"], **arguments)
            if result["status"] == "ok":
                items = []
                for item in result["items"]:
                    if item["kind"] == "skill":
                        projected = dict(item)
                        projected["version"] = projected.pop("source_revision", None)
                        items.append(projected)
                result = {**result, "items": items}
            return result
        if operation in {"inspect", "submit"}:
            arguments["capability_id"] = arguments.pop("skill_id")
            arguments["source_revision"] = arguments.pop("version")
            arguments["expected_kind"] = "skill"
        elif operation == "search":
            arguments["kinds"] = ["skill"]
        if operation not in {
            "inspect",
            "submit",
            "publish",
            "search",
            "load",
            "resource",
            "diagnostics",
        }:
            return {"status": "policy_denied", "reason": "legacy_operation_not_supported"}
        return _project(self.registry.call(operation, session_id, **arguments))
