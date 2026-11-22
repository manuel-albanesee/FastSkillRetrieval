"""Regression cases for descriptive hints and literal examples in skill manuals."""

import pytest

from fastskillretrieval.capabilities import parse_definition, validate_manifest
from fastskillretrieval.contracts import Failure
from fastskillretrieval.packages import supported_instructions


def skill(tmp_path, body, extra=""):
    path = tmp_path / "SKILL.md"
    source = f"---\nname: review\ndescription: Review source safely.\n{extra}---\n{body}"
    path.write_text(source, encoding="utf-8", newline="")
    return path


@pytest.mark.parametrize(
    "extra,body",
    [
        ("tools: Read, Write, Edit, Bash, Grep, Glob\n", "Review the diff."),
        ("tools: [Read, Bash]\n", "Review the diff."),
        ("argument-hint: '<path/to/*.plan.md>'\n", "Review the plan supplied by the user."),
        ("", "Prefer `assert_eq!` over `assert!` for better error messages."),
        ("", "Use `!` after the type/scope (`feat(api)!: remove endpoint`)."),
        (
            "",
            "Variables such as `${CLAUDE_SKILL_DIR}` and `$ARGUMENTS` are host-specific.\n"
            "## Anti-Patterns\n"
            "This is a forbidden pattern in a portable copy:\n"
            '```bash\nnode "${CLAUDE_PLUGIN_ROOT}/scripts/setup.js" $ARGUMENTS\n```\n'
            "## Best Practices\nRewrite `${CLAUDE_PROJECT_DIR}` to an explicit path.\n",
        ),
    ],
    ids=["ecc-tools", "tool-list", "autocomplete-hint", "rust-macro", "commit-bang", "host-guide"],
)
def test_hints_and_literal_examples_are_loadable_data(tmp_path, extra, body):
    path = skill(tmp_path, body, extra)
    parsed = parse_definition(path, "skill")
    assert parsed["activation"]["mode"] == "load"
    assert parsed["definition"]["body"] == body
    assert validate_manifest(parsed)["activation"]["mode"] == "load"
    supported_instructions(path.read_bytes())


@pytest.mark.parametrize(
    "body",
    [
        "Changes: !`git diff HEAD`",
        "!`git status`",
        "```!\ngit status\n```",
        "Review issue $ARGUMENTS following the coding standards.",
        "Write logs to ${CLAUDE_SESSION_ID}.",
        "Run `${CLAUDE_SKILL_DIR}/scripts/check.sh`.",
        "```bash\nnode ${CLAUDE_SKILL_DIR}/scripts/check.js $ARGUMENTS\n```",
        "## Anti-Patterns\nDo not run these.\n## Instructions\nReview $ARGUMENTS.",
        "## Anti-Patterns\nChanges: !`git diff HEAD`",
        "## Anti-Patterns\n```!\ngit status\n```",
        "## Anti-Patterns\n### Details\nExample `$ARGUMENTS`.\n# Instructions\nUse $ARGUMENTS.",
        "```bash ${CLAUDE_SKILL_DIR}\ncommand\n```",
    ],
)
def test_real_native_features_remain_closed(tmp_path, body):
    path = skill(tmp_path, body)
    parsed = parse_definition(path, "skill")
    assert parsed["activation"]["mode"] == "dry_run"
    with pytest.raises(Failure, match="native_skill_requires_invocation"):
        validate_manifest({**parsed, "activation": {"mode": "load"}})
    with pytest.raises(Failure, match="native_substitution_requires_invocation"):
        supported_instructions(path.read_bytes())


@pytest.mark.parametrize(
    "field",
    ["context: fork", "model: sonnet", "allowed-tools: Bash", "disallowed-tools: Bash"],
)
def test_tool_hint_cannot_override_native_frontmatter(tmp_path, field):
    path = skill(tmp_path, "Review the diff.", f"tools: Read\nargument-hint: filename\n{field}\n")
    assert parse_definition(path, "skill")["activation"]["mode"] == "dry_run"
    with pytest.raises(Failure, match="native_frontmatter_requires_invocation"):
        supported_instructions(path.read_bytes())


@pytest.mark.parametrize("extra", ["tools: {Bash: true}\n", "argument-hint: [file]\n"])
def test_malformed_annotations_are_not_silently_accepted(tmp_path, extra):
    path = skill(tmp_path, "Review the diff.", extra)
    with pytest.raises(Failure, match="invalid_skill_annotation"):
        parse_definition(path, "skill")


def test_inline_injection_requires_a_whitespace_boundary(tmp_path):
    path = skill(tmp_path, "The assignment KEY=!`example` is literal syntax.")
    assert parse_definition(path, "skill")["activation"]["mode"] == "load"
    supported_instructions(path.read_bytes())
