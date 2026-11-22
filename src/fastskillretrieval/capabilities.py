"""Closed operator manifests and bounded, data-only capability definition parsers.

These contracts never grant runtime authority. Generated descriptors continue to use
``contracts.validate_candidate`` and cannot change the kind, relations, or activation.
"""

import json
import math
import re
import tomllib
from pathlib import Path

import yaml
from yaml.events import AliasEvent, CollectionEndEvent, CollectionStartEvent, ScalarEvent
from yaml.nodes import MappingNode, ScalarNode, SequenceNode

from .contracts import Failure, text
from .packages import safe_path
from .skill_syntax import has_native_substitutions

KINDS = {"skill", "subagent", "workflow", "command"}
ACTIONS = {
    "skill": {"load", "dry_run", "invoke_native"},
    "subagent": {"dry_run", "delegate"},
    "workflow": {"dry_run", "orchestrate"},
    "command": {"alias"},
}
DEFAULT_ACTION = {"skill": "load", "subagent": "dry_run", "workflow": "dry_run", "command": "alias"}
RELATIONS = ("requires", "preloads", "uses", "conflicts_with")
MANIFEST_FIELDS = {
    "schema_version",
    "kind",
    "name",
    "description_original",
    "activation",
    *RELATIONS,
    "alias_of",
    "steps",
    "definition",
}
STEP_FIELDS = {"id", "capability", "action", "depends_on", "inputs", "outputs", "on_error"}
SECRET_FIELDS = {
    "credentials",
    "credential",
    "api_key",
    "apikey",
    "token",
    "access_token",
    "secret",
    "password",
    "authorization",
    "environment",
    "env",
    "grants",
    "permissions",
    "approved",
}
MAX_DEFINITION_BYTES = 262144
PASSIVE_SKILL_FIELDS = {
    "name",
    "description",
    "metadata",
    "license",
    "homepage",
    "origin",
    "author",
    "argument-hint",
    "tools",
    "aliases",
    "related",
    "related_to",
    "requires",
    "preloads",
    "uses",
    "conflicts_with",
    "alias_of",
}


def skill_requires_native(metadata: dict, body: str) -> bool:
    """ECC tool hints are data, distinct from the native allowed-tools grant."""
    if "metadata" in metadata and not isinstance(metadata["metadata"], dict):
        _fail("invalid_skill_metadata")
    if "argument-hint" in metadata and (
        not isinstance(metadata["argument-hint"], str) or len(metadata["argument-hint"]) > 1000
    ):
        _fail("invalid_skill_annotation")
    if "tools" in metadata:
        value = metadata["tools"]
        values = value if isinstance(value, list) else [value]
        if len(values) > 32 or any(not isinstance(v, str) or len(v) > 4000 for v in values):
            _fail("invalid_skill_annotation")
    for key in (
        "aliases",
        "related",
        "related_to",
        "requires",
        "preloads",
        "uses",
        "conflicts_with",
        "alias_of",
    ):
        if key in metadata:
            value = metadata[key]
            values = value if isinstance(value, list) else [value]
            if len(values) > 32 or any(not isinstance(v, str) or len(v) > 256 for v in values):
                _fail("invalid_skill_annotation")
    return bool(set(metadata) - PASSIVE_SKILL_FIELDS) or has_native_substitutions(body)


def _fail(reason: str) -> None:
    raise Failure("needs_revision", reason)


def _strings(value: object, field: str, maximum: int = 32) -> list[str]:
    if not isinstance(value, list) or len(value) > maximum:
        _fail(f"invalid_{field}")
    values = [text(item, 256) for item in value]
    if len(values) != len(set(values)):
        _fail(f"duplicate_{field}")
    return values


def _without_secrets(value: object, depth: int = 0) -> None:
    if depth > 16:
        _fail("definition_too_deep")
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = key.casefold().replace("-", "_") if isinstance(key, str) else ""
            secret_suffixes = ("_api_key", "_token", "_secret", "_password", "_credentials")
            if (
                not isinstance(key, str)
                or normalized in SECRET_FIELDS
                or normalized.endswith(secret_suffixes)
            ):
                raise Failure("policy_denied", "authority_or_credentials_in_definition")
            _without_secrets(item, depth + 1)
    elif isinstance(value, list):
        for item in value:
            _without_secrets(item, depth + 1)
    elif value is not None and not isinstance(value, (str, int, float, bool)):
        _fail("invalid_definition_value")


def _workflow_steps(value: object) -> list[dict]:
    if not isinstance(value, list) or not 1 <= len(value) <= 32:
        _fail("invalid_workflow_steps")
    steps = []
    for item in value:
        if not isinstance(item, dict) or set(item) - STEP_FIELDS:
            _fail("workflow_step_fields")
        if not {"id", "capability", "action"} <= set(item):
            _fail("workflow_step_fields")
        step = {key: text(item[key], 256) for key in ("id", "capability", "action")}
        if step["action"] not in {"load", "delegate", "orchestrate", "dry_run"}:
            _fail("invalid_step_action")
        for key in ("depends_on", "inputs", "outputs"):
            step[key] = _strings(item.get(key, []), key)
        step["on_error"] = item.get("on_error", "stop")
        if step["on_error"] != "stop":
            _fail("invalid_error_condition")
        steps.append(step)
    ids = {item["id"] for item in steps}
    if len(ids) != len(steps):
        _fail("duplicate_step_id")
    graph = {item["id"]: item["depends_on"] for item in steps}
    if any(dependency not in ids for edges in graph.values() for dependency in edges):
        _fail("unknown_step_dependency")
    visiting, visited = set(), set()

    def visit(node: str) -> None:
        if node in visiting:
            _fail("workflow_cycle")
        if node in visited:
            return
        visiting.add(node)
        for child in graph[node]:
            visit(child)
        visiting.remove(node)
        visited.add(node)

    for node in graph:
        visit(node)
    return steps


def validate_manifest(value: dict) -> dict:
    """Validate an operator-controlled v1 manifest; reject all extra fields.

    Relations are capability IDs resolved by the registry, never filesystem paths.
    Native activation modes describe a proposal, not permission to execute it.
    """
    if not isinstance(value, dict) or set(value) - MANIFEST_FIELDS:
        _fail("manifest_fields")
    if not {"kind", "name", "description_original"} <= set(value):
        _fail("manifest_fields")
    if type(value.get("schema_version", 1)) is not int or value.get("schema_version", 1) != 1:
        _fail("unsupported_manifest_version")
    kind = value["kind"]
    if not isinstance(kind, str) or kind not in KINDS:
        raise Failure("unsupported_integration", "unsupported_capability_kind")
    activation = value.get("activation", {"mode": DEFAULT_ACTION[kind]})
    if not isinstance(activation, dict) or set(activation) != {"mode"}:
        _fail("activation_fields")
    mode = activation["mode"]
    if not isinstance(mode, str) or mode not in ACTIONS[kind]:
        _fail("invalid_kind_action")
    result = {
        "schema_version": 1,
        "kind": kind,
        "name": text(value["name"], 128),
        "description_original": text(value["description_original"], 8000),
        "activation": {"mode": mode},
    }
    for key in RELATIONS:
        result[key] = _strings(value.get(key, []), key)
    if "alias_of" in value:
        result["alias_of"] = text(value["alias_of"], 256)
    if kind == "command" and "alias_of" not in result:
        _fail("command_requires_alias")
    if kind == "workflow":
        result["steps"] = _workflow_steps(value.get("steps"))
    elif "steps" in value:
        _fail("steps_require_workflow")
    if "definition" in value:
        definition = value["definition"]
        definition_fields = {"body", "frontmatter", "metadata"}
        if not isinstance(definition, dict) or set(definition) - definition_fields:
            _fail("definition_fields")
        if "body" in definition and not isinstance(definition["body"], str):
            _fail("invalid_definition_body")
        for key in ("frontmatter", "metadata"):
            if key in definition and not isinstance(definition[key], dict):
                _fail("invalid_definition_metadata")
        _without_secrets(definition)
        try:
            encoded = json.dumps(definition, ensure_ascii=False, allow_nan=False).encode("utf-8")
        except (ValueError, TypeError, UnicodeEncodeError) as error:
            raise Failure("needs_revision", "invalid_definition_value") from error
        if len(encoded) > MAX_DEFINITION_BYTES:
            _fail("definition_too_large")
        if kind == "skill" and mode == "load":
            metadata = definition.get("frontmatter", definition.get("metadata", {}))
            body = definition.get("body", "")
            if skill_requires_native(metadata, body):
                raise Failure("unsupported_integration", "native_skill_requires_invocation")
        # Own the nested data; callers cannot mutate the validated source through aliases.
        result["definition"] = json.loads(encoded)
    return result


MAX_FRONTMATTER_BYTES = 65536
MAX_FRONTMATTER_DEPTH = 16
MAX_FRONTMATTER_NODES = 1024
MAX_FRONTMATTER_EVENTS = MAX_FRONTMATTER_NODES * 3 + 8
_SAFE_YAML_TAGS = {
    "tag:yaml.org,2002:map",
    "tag:yaml.org,2002:seq",
    "tag:yaml.org,2002:str",
    "tag:yaml.org,2002:null",
    "tag:yaml.org,2002:bool",
    "tag:yaml.org,2002:int",
    "tag:yaml.org,2002:float",
}
_JSON_INT = re.compile(r"[-+]?(?:0|[1-9][0-9_]*)$")
_JSON_FLOAT = re.compile(
    r"[-+]?(?:(?:0|[1-9][0-9_]*)\.[0-9_]*|\.[0-9_]+|(?:0|[1-9][0-9_]*)[eE][-+]?[0-9_]+|(?:0|[1-9][0-9_]*)\.[0-9_]*[eE][-+]?[0-9_]+)$"
)


def _frontmatter_failure(reason: str = "unsafe_frontmatter") -> None:
    raise Failure("unsupported_integration", reason)


def _yaml_scalar(node: ScalarNode) -> str | int | float | bool | None:
    value = node.value
    if node.tag == "tag:yaml.org,2002:str":
        return value
    if node.tag == "tag:yaml.org,2002:null" and value.casefold() in {"", "~", "null"}:
        return None
    if node.tag == "tag:yaml.org,2002:bool":
        normalized = value.casefold()
        if normalized in {"true", "yes", "y", "on"}:
            return True
        if normalized in {"false", "no", "n", "off"}:
            return False
    if node.tag == "tag:yaml.org,2002:int" and _JSON_INT.fullmatch(value):
        return int(value.replace("_", ""), 10)
    if node.tag == "tag:yaml.org,2002:float" and _JSON_FLOAT.fullmatch(value):
        result = float(value.replace("_", ""))
        if math.isfinite(result):
            return result
    _frontmatter_failure("unsupported_frontmatter_scalar")


def _yaml_value(node, depth: int, seen: set[str]) -> object:
    if depth > MAX_FRONTMATTER_DEPTH:
        _frontmatter_failure("frontmatter_too_deep")
    if len(seen) >= MAX_FRONTMATTER_NODES:
        _frontmatter_failure("frontmatter_too_large")
    seen.add(str(id(node)))
    if node.tag not in _SAFE_YAML_TAGS:
        _frontmatter_failure("unsupported_frontmatter_tag")
    if isinstance(node, ScalarNode):
        return _yaml_scalar(node)
    if isinstance(node, SequenceNode):
        return [_yaml_value(item, depth + 1, seen) for item in node.value]
    if isinstance(node, MappingNode):
        result = {}
        for key, value in node.value:
            if not isinstance(key, ScalarNode) or key.tag != "tag:yaml.org,2002:str":
                _frontmatter_failure("frontmatter_key_not_text")
            if key.value == "<<" or key.value in result:
                _frontmatter_failure("frontmatter_merge_or_duplicate_key")
            result[key.value] = _yaml_value(value, depth + 1, seen)
        return result
    _frontmatter_failure("unsupported_frontmatter_node")


def _safe_frontmatter(value: str) -> dict:
    if len(value.encode("utf-8")) > MAX_FRONTMATTER_BYTES:
        _frontmatter_failure("frontmatter_too_large")
    try:
        depth = 0
        events = 0
        for event in yaml.parse(value, Loader=yaml.SafeLoader):
            events += 1
            if events > MAX_FRONTMATTER_EVENTS:
                _frontmatter_failure("frontmatter_too_large")
            if isinstance(event, AliasEvent) or getattr(event, "anchor", None) is not None:
                _frontmatter_failure("frontmatter_alias_or_anchor")
            tag = getattr(event, "tag", None)
            if tag is not None and tag not in _SAFE_YAML_TAGS:
                _frontmatter_failure("unsupported_frontmatter_tag")
            if isinstance(event, CollectionStartEvent):
                depth += 1
                if depth > MAX_FRONTMATTER_DEPTH:
                    _frontmatter_failure("frontmatter_too_deep")
            elif isinstance(event, CollectionEndEvent):
                depth -= 1
                if depth < 0:
                    _frontmatter_failure("malformed_frontmatter")
            elif isinstance(event, ScalarEvent) and events > MAX_FRONTMATTER_EVENTS:
                _frontmatter_failure("frontmatter_too_large")
        documents = list(yaml.compose_all(value, Loader=yaml.SafeLoader))
    except (yaml.YAMLError, RecursionError) as error:
        raise Failure("unsupported_integration", "malformed_frontmatter") from error
    if len(documents) != 1 or not isinstance(documents[0], MappingNode):
        _frontmatter_failure("frontmatter_requires_mapping")
    parsed = _yaml_value(documents[0], 0, set())
    if not isinstance(parsed, dict):
        _frontmatter_failure("frontmatter_requires_mapping")
    _without_secrets(parsed)
    return parsed


def _simple_frontmatter(source: str) -> tuple[dict, str]:
    lines = source.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return {}, source
    end = next((index for index, line in enumerate(lines[1:], 1) if line.strip() == "---"), None)
    if end is None:
        raise Failure("unsupported_integration", "malformed_frontmatter")
    return _safe_frontmatter("".join(lines[1:end])), "".join(lines[end + 1 :])


def _json_unique(pairs: list[tuple]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            _fail("duplicate_json_key")
        result[key] = value
    return result


def parse_definition(path: Path, kind: str) -> dict:
    """Read one authorized definition as data, without imports or shell evaluation.

    The caller selects the authorized root; this parser rejects linked paths, limits
    the individual file, and does not follow paths mentioned in its content.
    """
    if kind not in {"skill", "subagent", "workflow"}:
        raise Failure("unsupported_integration", "unsupported_definition_kind")
    path = safe_path(Path(path))
    if not path.is_file():
        raise Failure("stale_reference", "definition_missing")
    with path.open("rb") as stream:
        data = stream.read(MAX_DEFINITION_BYTES + 1)
    if len(data) > MAX_DEFINITION_BYTES:
        raise Failure("limit_exceeded", "definition_too_large")
    try:
        source = data.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise Failure("needs_revision", "definition_not_utf8") from error
    if kind == "workflow":
        if path.suffix.casefold() != ".json":
            raise Failure("unsupported_integration", "workflow_requires_json")
        try:
            value = json.loads(source, object_pairs_hook=_json_unique)
        except (ValueError, RecursionError) as error:
            raise Failure("needs_revision", "invalid_workflow_json") from error
        if not isinstance(value, dict) or value.get("kind") != "workflow":
            _fail("workflow_kind_mismatch")
        return validate_manifest(value)
    if kind == "subagent" and path.suffix.casefold() == ".toml":
        try:
            metadata = tomllib.loads(source)
        except (ValueError, RecursionError) as error:
            raise Failure("needs_revision", "invalid_agent_toml") from error
        _without_secrets(metadata)
        definition = {"metadata": metadata}
        body = ""
    elif path.suffix.casefold() == ".md":
        metadata, body = _simple_frontmatter(source)
        definition = {"frontmatter": metadata, "body": body}
    else:
        raise Failure("unsupported_integration", "unsupported_definition_format")
    description = metadata.get("description")
    if description is None:
        description = next(
            (line.strip().lstrip("# ") for line in body.splitlines() if line.strip()), None
        )
    name = metadata.get("name", path.parent.name if kind == "skill" else path.stem)
    mode = "load" if kind == "skill" else "dry_run"
    if kind == "skill" and skill_requires_native(metadata, body):
        mode = "dry_run"
    return validate_manifest(
        {
            "kind": kind,
            "name": name,
            "description_original": description,
            "activation": {"mode": mode},
            "definition": definition,
        }
    )
