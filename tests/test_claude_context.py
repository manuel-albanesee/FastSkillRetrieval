"""Native Claude context observations are local measurements, never token billing data."""

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "measure_claude_catalog.py"
FIXTURE_HASH = "a" * 64
MANIFEST_HASH = "b" * 64


def context_result(*, command="context", turns=0, input_tokens=0, category="System prompt"):
    return {
        "type": "result",
        "local_command": command,
        "duration_api_ms": 0,
        "num_turns": turns,
        "total_cost_usd": 0,
        "modelUsage": {},
        "usage": {
            "input_tokens": input_tokens,
            "output_tokens": 0,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
            "iterations": [],
            "output_tokens_details": {"thinking_tokens": 0},
            "server_tool_use": {"web_search_requests": 0, "web_fetch_requests": 0},
        },
        "result": "\n".join(
            (
                "## Context Usage",
                "",
                "**Model:** claude-opus-5-5  ",
                "**Tokens:** 1.6k / 1m (0%)",
                "",
                "### Estimated usage by category",
                "",
                "| Category | Tokens | Percentage |",
                "|----------|--------|------------|",
                f"| {category} | 1.6k | 0.2% |",
                "| Messages | 10 | 0.0% |",
                "| Free space | 965.4k | 96.5% |",
            )
        ),
    }


def test_context_parser_preserves_native_estimate_categories_and_units():
    from fastskillretrieval.claude_context import parse_context_result

    observed = parse_context_result(
        json.dumps(context_result(category="Future native category")),
        client_version="2.1.302",
        source_fixture_hash=FIXTURE_HASH,
        source_manifest_hash=MANIFEST_HASH,
    )

    first = observed["measurements"]["categories"][0]
    assert observed["kind"] == "native_client_observation"
    assert observed["context_bytes"] is None
    assert observed["comparability"]["reduction_asserted"] is False
    assert observed["client_version"] == "2.1.302"
    assert observed["source_fixture_hash"] == FIXTURE_HASH
    assert first == {
        "category": "Future native category",
        "scope": "native_context",
        "raw": "1.6k",
        "numeric": "1.6",
        "unit": "k",
        "estimated_tokens": 1600,
        "percentage": 0.2,
    }
    assert observed["measurements"]["categories"][1]["unit"] == ""
    assert observed["measurements"]["categories"][1]["estimated_tokens"] == 10


@pytest.mark.parametrize(
    ("changed", "reason"),
    (
        ({"local_command": "help"}, "not_local_context"),
        ({"num_turns": 1}, "model_turns_observed"),
        ({"usage": {"input_tokens": 1}}, "model_usage_observed"),
        ({"modelUsage": {"claude": {}}}, "model_usage_observed"),
    ),
)
def test_context_parser_refuses_any_nonlocal_or_model_run(changed, reason):
    from fastskillretrieval.claude_context import ContextObservationError, parse_context_result

    payload = context_result()
    payload.update(changed)
    with pytest.raises(ContextObservationError, match=reason):
        parse_context_result(
            payload,
            client_version="2.1.302",
            source_fixture_hash=FIXTURE_HASH,
            source_manifest_hash=MANIFEST_HASH,
        )


def test_measurement_script_materializes_a_hash_verified_fake_skill_and_observes_context(tmp_path):
    spec = importlib.util.spec_from_file_location("measure_claude_catalog", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    source = tmp_path / "source"
    skill = source / "skills" / "only" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("# Fixture skill\n", encoding="utf-8")
    manifest = {
        "skill_references": [
            {
                "id": "ecc/only",
                "path": "skills/only/SKILL.md",
                "sha256": hashlib.sha256(skill.read_bytes()).hexdigest(),
            }
        ]
    }
    commands = []

    def fake_run(command, *, cwd, env):
        assert cwd == tmp_path / "project"
        assert env["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:1"
        commands.append(command)
        return json.dumps(context_result(category="Skills"))

    report = module.measure(
        manifest,
        source,
        tmp_path / "project",
        client_version="2.1.302",
        expected_skill_count=1,
        run_command=fake_run,
    )

    assert (tmp_path / "project" / ".claude" / "skills" / "only" / "SKILL.md").read_text(
        encoding="utf-8"
    ) == "# Fixture skill\n"
    assert "--bare" not in commands[0]
    assert "--add-dir" not in commands[0]
    assert "--mcp-config" in commands[0]
    assert "--restricted" not in commands[0]
    assert commands[0][commands[0].index("--tools")+1] == "Skill"
    assert commands[0][-1] == "/context"
    first = report["observation"]["measurements"]["categories"][0]
    assert first["category"] == "Skills"
    assert first["estimated_tokens"] == 1600
    assert report["evaluation"] == {
        "status": "not_run_requires_human_approval",
        "context_reduction_A_to_C": None,
    }


def test_measurement_script_rejects_an_id_that_could_escape_the_skill_directory(tmp_path):
    spec = importlib.util.spec_from_file_location("measure_claude_catalog", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    manifest = {
        "skill_references": [
            {"id": "ecc/../../escape", "path": "skills/only/SKILL.md", "sha256": "a" * 64}
        ]
    }

    with pytest.raises(module.MeasurementError, match="invalid_skill_reference"):
        module.materialize_fixture(manifest, tmp_path, tmp_path / "project", expected_skill_count=1)

