"""Documentation schemas stay aligned with the public capability payloads."""

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError
from referencing import Registry, Resource

from fastskillretrieval.capabilities import validate_manifest

ROOT = Path(__file__).resolve().parents[1]
SCHEMAS = ROOT / "schemas"


def validator(name: str) -> Draft202012Validator:
    schemas = {
        path.name: json.loads(path.read_text(encoding="utf-8")) for path in SCHEMAS.glob("*.json")
    }
    schema = schemas[name]
    Draft202012Validator.check_schema(schema)
    registry = Registry().with_resources(
        (contents["$id"], Resource.from_contents(contents)) for contents in schemas.values()
    )
    return Draft202012Validator(schema, registry=registry)


def valid_manifest(kind: str = "skill", **extra: object) -> dict:
    return {
        "kind": kind,
        "name": "review",
        "description_original": "Review source safely.",
        **extra,
    }


def test_all_documentation_schemas_are_valid_draft_2020_12():
    for path in SCHEMAS.glob("*.json"):
        Draft202012Validator.check_schema(json.loads(path.read_text(encoding="utf-8")))


REFERENCE = {
    "schema_version": 2,
    "capability_id": "3a6ad58f4fcf71168bcb9f1e",
    "source_revision": "a" * 64,
    "descriptor_revision": "b" * 64,
    "catalog_id": 1,
    "scope_id": "local-scope",
}


def test_operator_manifest_schema_accepts_runtime_defaults_and_workflow_steps():
    schema = validator("operator-manifest-v1.schema.json")
    schema.validate(validate_manifest(valid_manifest()))
    schema.validate(
        valid_manifest(
            "workflow",
            activation={"mode": "dry_run"},
            steps=[
                {
                    "id": "read",
                    "capability": "reader",
                    "action": "load",
                    "outputs": ["summary"],
                }
            ],
        )
    )


@pytest.mark.parametrize(
    "manifest",
    [
        valid_manifest(grants=["shell"]),
        valid_manifest(activation={"mode": "load", "approved": True}),
        valid_manifest("command"),
        valid_manifest("workflow", steps=[]),
        valid_manifest("skill", steps=[]),
    ],
)
def test_operator_manifest_schema_rejects_extra_authority_and_invalid_kind_shape(manifest):
    with pytest.raises(ValidationError):
        validator("operator-manifest-v1.schema.json").validate(manifest)


def test_reference_v2_schema_matches_registry_reference():
    validator("reference-v2.schema.json").validate(REFERENCE)

    with pytest.raises(ValidationError):
        validator("reference-v2.schema.json").validate({**REFERENCE, "extra": True})


def test_search_request_schema_matches_registry_arguments():
    validator("search-request-v1.schema.json").validate(
        {
            "query": "review a Python capability",
            "request_id": "search-1",
            "kinds": ["skill", "subagent"],
            "mode": "discovery",
            "task_revision": 1,
            "epoch": 42,
            "limit": 5,
            "candidate_scan_limit": 100,
            "explicit_id": REFERENCE["capability_id"],
            "technologies": ["python"],
            "exclude_ids": ["already-selected"],
        }
    )

    with pytest.raises(ValidationError):
        validator("search-request-v1.schema.json").validate({"limit": 6})
    for invalid in (0, True, "2"):
        with pytest.raises(ValidationError):
            validator("search-request-v1.schema.json").validate({"task_revision": invalid})


def test_plan_response_schema_matches_registry_success_response():
    validator("plan-response-v1.schema.json").validate(
        {
            "status": "ok",
            "plan_id": "a" * 32,
            "actions": [
                {
                    "reference": REFERENCE,
                    "action": "load",
                    "target": "main",
                    "activation_allowed": True,
                    "dry_run": False,
                    "binding": "assisted_body_read",
                    "requires": [],
                }
            ],
            "main_estimate": 1024,
            "delegated_cost": "unknown",
            "dry_run": False,
            "token_estimate": "utf8_bytes_conservative",
        }
    )
