import json

import pytest

from fastskillretrieval.capabilities import parse_definition, validate_manifest
from fastskillretrieval.contracts import Failure, Limits, validate_candidate


def manifest(kind="skill", **extra):
    return {
        "kind": kind,
        "name": "review",
        "description_original": "Review source safely.",
        **extra,
    }


@pytest.mark.parametrize(
    "kind,mode", [("skill", "load"), ("subagent", "dry_run"), ("workflow", "dry_run")]
)
def test_default_action_is_typed(kind, mode):
    extra = (
        {"steps": [{"id": "review", "capability": "review-agent", "action": "delegate"}]}
        if kind == "workflow"
        else {}
    )
    result = validate_manifest(manifest(kind, **extra))
    assert result["schema_version"] == 1
    assert result["activation"] == {"mode": mode}
    assert result["requires"] == []


@pytest.mark.parametrize(
    "value",
    [
        manifest("tool"),
        manifest(activation={"mode": "shell"}),
        manifest("subagent", activation={"mode": "load"}),
        manifest(grants=["shell"]),
        manifest(credentials={"token": "SECRET"}),
        manifest(activation={"mode": "load", "approved": True}),
        manifest(requires=[{"id": "other", "approved": True}]),
        manifest(requires=["other", "other"]),
        manifest(description_original=""),
        manifest("command"),
        manifest(schema_version=2),
    ],
)
def test_closed_operator_contract_rejects_invalid_authority(value):
    with pytest.raises(Failure):
        validate_manifest(value)


def test_command_requires_explicit_alias():
    result = validate_manifest(manifest("command", alias_of="skills:review"))
    assert result["activation"]["mode"] == "alias"


def test_workflow_steps_are_data_with_checked_dependencies():
    result = validate_manifest(
        manifest(
            "workflow",
            steps=[
                {
                    "id": "read",
                    "capability": "read-skill",
                    "action": "load",
                    "outputs": ["summary"],
                },
                {
                    "id": "review",
                    "capability": "review-agent",
                    "action": "delegate",
                    "depends_on": ["read"],
                    "inputs": ["summary"],
                    "on_error": "stop",
                },
            ],
        )
    )
    assert result["steps"][1]["depends_on"] == ["read"]


@pytest.mark.parametrize(
    "steps",
    [
        [{"id": "a", "capability": "x", "action": "shell", "command": "echo fail"}],
        [{"id": "a", "capability": "x", "action": "load", "depends_on": ["a"]}],
        [
            {"id": "a", "capability": "x", "action": "load", "depends_on": ["b"]},
            {"id": "b", "capability": "x", "action": "load", "depends_on": ["a"]},
        ],
        [{"id": "a", "capability": "x", "action": "load", "depends_on": ["missing"]}],
    ],
)
def test_workflow_execution_and_cycles_are_rejected(steps):
    with pytest.raises(Failure):
        validate_manifest(manifest("workflow", steps=steps))


def test_skill_metadata_preserves_original_body(tmp_path):
    path = tmp_path / "SKILL.md"
    body = (
        "---\nname: review\ndescription: Review source safely.\n---\n"
        "# Review\nUse static analysis.\n"
    )
    path.write_text(body, encoding="utf-8", newline="")
    result = parse_definition(path, "skill")
    assert result["name"] == "review"
    assert result["definition"]["body"] == "# Review\nUse static analysis.\n"
    assert result["activation"]["mode"] == "load"
    assert path.read_text(encoding="utf-8") == body


def test_operational_skill_metadata_forces_dry_run(tmp_path):
    path = tmp_path / "SKILL.md"
    path.write_text("---\nname: review\ndescription: Review code.\ncontext: fork\n---\nBody")
    result = parse_definition(path, "skill")
    assert result["activation"]["mode"] == "dry_run"
    assert result["definition"]["frontmatter"]["context"] == "fork"


def test_subagent_toml_is_parsed_as_data(tmp_path):
    path = tmp_path / "agent.toml"
    path.write_text('name = "review"\ndescription = "Review code."\nmodel = "sonnet"\n')
    result = parse_definition(path, "subagent")
    assert result["definition"]["metadata"]["model"] == "sonnet"
    assert result["activation"]["mode"] == "dry_run"


def test_safe_yaml_frontmatter_preserves_nested_origin_and_allows_body_load(tmp_path):
    path = tmp_path / "agent.md"
    body = (
        "---\nname: review\ndescription: |\n  Review source safely.\nmetadata:\n"
        "  origin: ECC\n---\nBody\n"
    )
    path.write_text(body, encoding="utf-8", newline="")

    result = parse_definition(path, "skill")

    assert result["description_original"] == "Review source safely."
    assert result["definition"]["frontmatter"]["metadata"] == {"origin": "ECC"}
    assert result["definition"]["body"] == "Body\n"
    assert result["activation"]["mode"] == "load"
    assert path.read_text(encoding="utf-8") == body


@pytest.mark.parametrize("extra", ["context: fork", "allowed-tools: Bash", "model: sonnet"])
def test_descriptive_metadata_does_not_override_operational_fields(tmp_path, extra):
    path = tmp_path / "SKILL.md"
    path.write_text(
        "---\nname: review\ndescription: Review source safely.\n"
        f"metadata: {{origin: ECC}}\n{extra}\n---\nBody\n",
        encoding="utf-8",
    )
    result = parse_definition(path, "skill")
    assert result["activation"]["mode"] == "dry_run"
    with pytest.raises(Failure, match="native_skill_requires_invocation"):
        validate_manifest({**result, "activation": {"mode": "load"}})


@pytest.mark.parametrize("metadata", ["ECC", "null", "[ECC]"])
def test_descriptive_metadata_must_be_a_mapping(tmp_path, metadata):
    path = tmp_path / "SKILL.md"
    path.write_text(
        f"---\nname: review\ndescription: Review source.\nmetadata: {metadata}\n---\nBody",
        encoding="utf-8",
    )
    with pytest.raises(Failure, match="invalid_skill_metadata"):
        parse_definition(path, "skill")


@pytest.mark.parametrize(
    "metadata",
    [
        "name: review\ndescription: review\ntools: &anchor [Bash]\n",
        "name: review\ndescription: review\ntools: *anchor\n",
        "name: !!python/object/apply:os.system ['calc']\ndescription: review\n",
        "name: review\nname: duplicate\ndescription: review\n",
        "name: review\ndescription: review\n<<: {origin: ECC}\n",
        "name: review\ndescription: review\nmetadata: {api_key: secret}\n",
    ],
)
def test_unsafe_or_ambiguous_yaml_frontmatter_is_rejected(tmp_path, metadata):
    path = tmp_path / "agent.md"
    path.write_text(f"---\n{metadata}---\nBody", encoding="utf-8")
    with pytest.raises(Failure) as error:
        parse_definition(path, "subagent")
    assert error.value.status in {"unsupported_integration", "policy_denied", "needs_revision"}


def test_deep_frontmatter_is_rejected_before_yaml_composition_recurses(tmp_path):
    path = tmp_path / "SKILL.md"
    nested = "[" * 1500 + "x" + "]" * 1500
    source = f"---\nname: review\ndescription: review\nmetadata: {nested}\n---\nBody"
    path.write_text(source)

    with pytest.raises(Failure) as error:
        parse_definition(path, "skill")

    assert error.value.reason == "frontmatter_too_deep"


def test_workflow_json_only(tmp_path):
    path = tmp_path / "workflow.json"
    path.write_text(
        json.dumps(
            manifest(
                "workflow",
                steps=[
                    {"id": "read", "capability": "read-skill", "action": "load"},
                ],
            )
        )
    )
    assert parse_definition(path, "workflow")["steps"][0]["action"] == "load"


def test_generated_candidate_cannot_change_manifest_authority():
    with pytest.raises(Failure) as error:
        validate_candidate({"kind": "subagent", "activation": {"mode": "delegate"}}, {}, Limits())
    assert error.value.reason == "candidate_fields"


def test_operator_overlay_cannot_turn_native_skill_into_plain_body_load():
    with pytest.raises(Failure) as error:
        validate_manifest(manifest(definition={"body": "Body", "frontmatter": {"context": "fork"}}))
    assert error.value.status == "unsupported_integration"


def test_credentials_under_provider_specific_names_are_rejected(tmp_path):
    path = tmp_path / "agent.toml"
    path.write_text('name = "review"\ndescription = "Review."\nanthropic_api_key = "SECRET"\n')
    with pytest.raises(Failure) as error:
        parse_definition(path, "subagent")
    assert error.value.status == "policy_denied"
