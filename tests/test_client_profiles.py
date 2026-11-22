import hashlib
from dataclasses import FrozenInstanceError

import pytest

from fastskillretrieval.client_profiles import (
    ProfileError,
    build_command,
    guarded_environment,
    materialize_profile,
    verify_preflight,
)


def fixture(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    body = b"---\nname: only\ndescription: One fixture\n---\n# Only\n"
    (source / "SKILL.md").write_bytes(body)
    return source, [
        {"id": "ecc/only", "path": "SKILL.md", "sha256": hashlib.sha256(body).hexdigest()}
    ]


def test_native_profile_materializes_project_skill_without_restricted(tmp_path):
    source, corpus = fixture(tmp_path)
    profile = materialize_profile(
        "A_NATIVE", corpus, source, tmp_path / "a", expected_skill_count=1
    )
    argv = build_command(profile, "/context", local_only=True)
    assert "--restricted" not in argv and "--add-dir" not in argv
    assert argv[argv.index("--setting-sources") + 1] == "project"
    assert argv[argv.index("--tools") + 1] == "Skill"
    assert (profile.cwd / ".claude/skills/only/SKILL.md").read_bytes() == (
        source / "SKILL.md"
    ).read_bytes()
    with pytest.raises(FrozenInstanceError):
        profile.arm = "C_FSR"


def test_curated_catalog_is_inserted_in_work_prompt_and_hashed(tmp_path):
    source, corpus = fixture(tmp_path)
    descriptors = [{"id": "ecc/only", "description": "Curated fixture"}]
    profile = materialize_profile(
        "B_CURATED", corpus, source, tmp_path / "b", descriptors=descriptors, expected_skill_count=1
    )
    argv = build_command(profile, "Task", {"max_turns": 2, "trial_usd": 1})
    assert "Curated fixture" in argv[-1]
    assert hashlib.sha256(profile.catalog_text.encode()).hexdigest() == profile.catalog_sha256
    assert "--disable-slash-commands" in argv
    assert "Read" not in argv and "--tools" in argv
    assert not (profile.cwd / ".claude/skills").exists()
    with pytest.raises(ProfileError, match="catalog"):
        materialize_profile(
            "B_CURATED", corpus, source, tmp_path / "bad", descriptors=[], expected_skill_count=1
        )


def test_fsr_has_explicit_ten_tools_and_no_native_catalog(tmp_path):
    source, corpus = fixture(tmp_path)
    profile = materialize_profile(
        "C_FSR",
        corpus,
        source,
        tmp_path / "c",
        mcp_config={"mcpServers": {"fsr": {"command": "python", "args": []}}},
        expected_skill_count=1,
    )
    assert len(profile.expected_tools) == 10
    argv = build_command(profile, "Task", {"max_turns": 2, "trial_usd": 1})
    assert "--disable-slash-commands" in argv
    assert "--allowedTools" in argv
    assert not (profile.cwd / ".claude/skills").exists()
    result = verify_preflight(
        profile,
        [
            {
                "type": "system",
                "subtype": "init",
                "skills": [],
                "tools": list(profile.expected_tools),
                "claude_code_version": "2.1.fixture",
                "mcp_servers": [{"name": "fsr", "status": "connected"}],
            }
        ],
    )
    assert result["status"] == "verified"
    assert verify_preflight(profile, [])["status"] == "blocked"


def test_guarded_provider_overrides_all_alternative_provider_switches():
    env = guarded_environment(
        {
            "ANTHROPIC_API_KEY": "secret",
            "CLAUDE_CODE_USE_BEDROCK": "1",
            "CLAUDE_CODE_USE_VERTEX": "1",
            "CLAUDE_CODE_USE_FOUNDRY": "1",
        }
    )
    assert env["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:1"
    assert env["ANTHROPIC_API_KEY"] != "secret"
    assert not any(
        key in env
        for key in ("CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY")
    )


def test_profiles_fail_closed_on_hash_mismatch_and_reuse(tmp_path):
    source, corpus = fixture(tmp_path)
    corpus[0]["sha256"] = "a" * 64
    with pytest.raises(ProfileError, match="hash"):
        materialize_profile("A_NATIVE", corpus, source, tmp_path / "bad", expected_skill_count=1)
    assert not (tmp_path / "bad").exists()


def test_curated_supports_only_loading_tools_and_fsr_limits_operational_allowlist(tmp_path):
    source, corpus = fixture(tmp_path)
    config = {"mcpServers": {"fsr": {"command": "python", "args": []}}}
    b = materialize_profile(
        "B_CURATED",
        corpus,
        source,
        tmp_path / "b-load",
        descriptors=[{"id": "ecc/only", "description": "fixture"}],
        mcp_config=config,
        expected_skill_count=1,
    )
    command = build_command(b, "Task", {"max_turns": 1, "trial_usd": 1})
    allowed = command[command.index("--allowedTools") + 1]
    assert "load_capability" in allowed and "search_capabilities" not in allowed
    c = materialize_profile(
        "C_FSR", corpus, source, tmp_path / "c-load", mcp_config=config, expected_skill_count=1
    )
    command = build_command(c, "Task", {"max_turns": 1, "trial_usd": 1})
    allowed = command[command.index("--allowedTools") + 1]
    assert "search_capabilities" in allowed and "publish_catalog" not in allowed


def test_preflight_blocks_extra_filesystem_tools_and_duplicate_init(tmp_path):
    source, corpus = fixture(tmp_path)
    p = materialize_profile(
        "B_CURATED",
        corpus,
        source,
        tmp_path / "pre",
        descriptors=[{"id": "ecc/only"}],
        expected_skill_count=1,
    )
    init = {
        "type": "system",
        "subtype": "init",
        "skills": [],
        "tools": ["Read"],
        "claude_code_version": "fixture",
    }
    assert verify_preflight(p, [init])["status"] == "blocked"
    init["tools"] = []
    assert verify_preflight(p, [init, init])["status"] == "blocked"
