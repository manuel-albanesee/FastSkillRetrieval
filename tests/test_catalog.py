import copy
import json
import sqlite3
from dataclasses import replace

import pytest
from conftest import prepared

from fastskillretrieval.catalog import Catalog
from fastskillretrieval.contracts import Failure, Limits, cost, terms
from fastskillretrieval.packages import read_package, safe_path


def test_vertical_path_and_idempotent_publication(workspace):
    service, _, metadata = workspace
    assert service.call("search", query="markdown")["status"] == "catalog_not_ready"
    item, reference = prepared(service, metadata)
    result = service.call("load", "user", reference=reference)
    assert result["status"] == "ok"
    assert result["observation"] == "content_emitted_not_application_confirmed"
    resource = service.call("resource", "user", reference=reference, file="references/checklist.md")
    assert "FSR-MARKDOWN-CHECK-01" in resource["content"]
    again = service.call("publish", "publish-again")
    assert not again["changed"]
    assert again["catalog"] == reference["catalog"]
    inventory = service.call("inventory", "inventory")
    assert inventory["items"][0]["state"] == "published"
    assert inventory["items"][0]["id"] == item["id"]


@pytest.mark.parametrize(
    "mutation",
    [
        lambda candidate: candidate.update(enabled=True),
        lambda candidate: candidate.update(description="short"),
        lambda candidate: candidate.update(aliases=["same", "SAME"]),
        lambda candidate: candidate.update(technologies=["unknown-platform"]),
        lambda candidate: candidate["capabilities"][0]["evidence"].update(
            quote="invented capability"
        ),
        lambda candidate: candidate["capabilities"][0]["evidence"].update(file="../secret"),
        lambda candidate: candidate.update(use_when=[]),
        lambda candidate: candidate.update(capabilities=[]),
        lambda candidate: candidate.update(exclusions="not a list"),
    ],
)
def test_bad_candidates_are_visible_but_not_searchable(workspace, mutation):
    service, _, metadata = workspace
    item = service.call("inventory", refresh=True)["items"][0]
    mutation(metadata)
    result = service.call(
        "submit", skill_id=item["id"], version=item["version"], candidate=metadata
    )
    assert result["status"] == "needs_revision"
    assert service.call("inventory")["items"][0]["state"] == "needs_revision"
    assert service.call("publish")["published"] == 0
    assert service.call("search", query="markdown")["status"] == "no_match"


def test_attempts_survive_new_session_and_restart(workspace):
    service, source, _ = workspace
    item = service.call("inventory", refresh=True)["items"][0]
    args = {"skill_id": item["id"], "version": item["version"], "candidate": {}}
    assert service.call("submit", "first", **args)["attempts"] == 1
    with_restart = Catalog(service.state, source)
    try:
        assert with_restart.call("submit", "second", **args)["attempts"] == 2
        assert with_restart.call("submit", "third", **args)["reason"] == "two_attempts_exhausted"
    finally:
        with_restart.close()


def test_bad_updated_source_keeps_old_snapshot_and_metadata(workspace):
    service, source, metadata = workspace
    item, reference = prepared(service, metadata)
    (source / "markdown-check" / "references" / "checklist.md").write_text("new resource")
    fresh = service.call("inventory", "fresh", refresh=True)["items"][0]
    assert fresh["version"] != item["version"]
    assert (
        service.call(
            "submit", "fresh", skill_id=item["id"], version=fresh["version"], candidate={}
        )["status"]
        == "needs_revision"
    )
    assert service.call("publish", "fresh")["published"] == 1
    assert service.call("load", "user", reference=reference)["status"] == "ok"
    result = service.call("resource", "user", reference=reference, file="references/checklist.md")
    assert "FSR-MARKDOWN-CHECK-01" in result["content"]
    found = service.call("search", "another", query="markdown")
    assert found["candidates"][0]["reference"]["version"] == item["version"]


def test_snapshot_tampering_is_reported_and_does_not_load(workspace):
    service, _, metadata = workspace
    item, reference = prepared(service, metadata)
    file = service.state / "packages" / item["id"] / item["version"] / "references/checklist.md"
    file.write_text("tampered")
    assert service.call("load", "user", reference=reference)["status"] == "stale_reference"


def test_source_changed_before_validation_is_stale(workspace):
    service, source, metadata = workspace
    item = service.call("inventory", refresh=True)["items"][0]
    (source / "markdown-check" / "references/checklist.md").write_text("changed")
    result = service.call(
        "submit", skill_id=item["id"], version=item["version"], candidate=metadata
    )
    assert result["status"] == "stale_reference"


def test_version_pinning_and_explicit_disable(workspace):
    service, source, metadata = workspace
    item, first = prepared(service, metadata)
    assert service.call("load", "pinned", reference=first)["status"] == "ok"
    (source / "markdown-check" / "references/checklist.md").write_text("version two")
    second = service.call("inventory", "maintenance", refresh=True)["items"][0]
    assert (
        service.call(
            "submit",
            "maintenance",
            skill_id=item["id"],
            version=second["version"],
            candidate=metadata,
        )["status"]
        == "ok"
    )
    service.call("publish", "maintenance")
    new_ref = service.call("search", "new", query="markdown")["candidates"][0]["reference"]
    assert service.call("load", "pinned", reference=new_ref)["status"] == "stale_reference"
    assert service.call("load", "new", reference=new_ref)["status"] == "ok"
    assert service.call("load", "pinned", reference=first)["status"] == "ok"
    assert (
        service.call("set_enabled", "operator", skill_id=item["id"], enabled=False)["status"]
        == "ok"
    )
    assert service.call("load", "new", reference=new_ref)["status"] == "policy_denied"
    assert service.call("search", "new", query="markdown")["status"] == "no_match"
    assert service.call("publish", "operator")["published"] == 0
    service.call("set_enabled", "operator", skill_id=item["id"], enabled=True)
    assert service.call("publish", "operator")["published"] == 1


@pytest.mark.parametrize("path", ["../secret", "/etc/passwd", "C:/secret", "..\\secret", "a/./b"])
def test_resource_traversal_rejected(workspace, path):
    service, _, metadata = workspace
    _, reference = prepared(service, metadata)
    assert service.call("resource", reference=reference, file=path)["status"] == "policy_denied"


def test_exclusions_are_not_positive_search_signals_and_fts_is_literal(workspace):
    service, _, metadata = workspace
    item, _ = prepared(service, metadata)
    assert service.call("search", query="deployment")["status"] == "no_match"
    for query in ['" OR * NOT (', "NEAR(markdown,*)", "'; DROP TABLE sources; --"]:
        assert service.call("search", "syntax", query=query)["status"] in {"ok", "no_match"}
    assert (
        service.call("search", explicit_id=item["id"], exclude_ids=[item["id"]])["status"]
        == "no_match"
    )
    assert service.call("search", query="markdown", technologies=["cpp"])["status"] == "no_match"
    assert service.call("search", query="markdown", technologies=[42])["status"] == "policy_denied"


def test_technical_tokens_distinct():
    assert terms("C C++ C#") == ["c", "cpp", "csharp"]


def test_budget_denies_instructions_without_truncating_or_pinning(workspace):
    service, _, metadata = workspace
    _, reference = prepared(service, metadata)
    service.limits = replace(service.limits, load=50)
    refused = service.call("load", "small", reference=reference)
    assert refused["status"] == "budget_exceeded"
    assert "content" not in refused
    pins = service.db.execute("SELECT pins FROM sessions WHERE id='small'").fetchone()[0]
    assert json.loads(pins) == {}
    service.limits = replace(service.limits, load=4000, period=200)
    assert (
        service.call(
            "inspect", "small", skill_id=reference["skill_id"], version=reference["version"]
        )["status"]
        == "budget_exceeded"
    )


def test_cards_and_serialized_envelope_counted(workspace):
    service, _, metadata = workspace
    prepared(service, metadata)
    service.limits = replace(service.limits, cards=50)
    assert service.call("search", "cards", query="markdown")["status"] == "budget_exceeded"
    service.limits = replace(service.limits, cards=1200)
    response = service.call("search", "cards2", query="markdown")
    spent = service.db.execute("SELECT spent FROM sessions WHERE id='cards2'").fetchone()[0]
    assert spent == cost(response)


def test_no_sensitive_query_in_database(workspace):
    service, _, metadata = workspace
    prepared(service, metadata)
    secret = "query-only-sensitive-unique-string-123"
    service.call("search", query=secret)
    dump = "\n".join(service.db.iterdump())
    assert secret not in dump
    assert not list(service.state.glob("*.log"))


def test_unsupported_native_semantics_stay_excluded(workspace):
    service, source, _ = workspace
    file = source / "markdown-check" / "SKILL.md"
    file.write_text("---\nname: forked\ncontext: fork\n---\nDo work")
    item = service.call("inventory", refresh=True)["items"][0]
    assert item["state"] == "blocked"
    assert item["reason"] == "native_frontmatter_requires_invocation"
    assert service.call("publish")["published"] == 0


def test_identity_is_bound_to_root_and_shared_state_cannot_leak(workspace, tmp_path):
    service, _, _ = workspace
    other = tmp_path / "other"
    other.mkdir()
    with pytest.raises(Failure, match="state_belongs_to_another_root"):
        Catalog(service.state, other)
    with pytest.raises(Failure, match="state_and_sources_must_be_separate"):
        Catalog(other / "state", other)


def test_symlink_is_rejected_if_platform_allows_it(workspace, tmp_path):
    service, source, _ = workspace
    linked = source / "markdown-check" / "escape"
    try:
        linked.symlink_to(tmp_path, target_is_directory=True)
    except OSError:
        pytest.skip("Creating symlinks requires Windows developer mode or privileges")
    try:
        assert service.call("inventory", refresh=True)["items"][0]["reason"] == "linked_path"
    finally:
        linked.unlink()


def test_windows_reparse_attribute_is_rejected(monkeypatch, tmp_path):
    original = type(tmp_path).lstat

    def attributes(path):
        info = original(path)
        if path == tmp_path:

            class Reparse:
                st_mode = info.st_mode
                st_file_attributes = 0x400

            return Reparse()
        return info

    monkeypatch.setattr(type(tmp_path), "lstat", attributes)
    with pytest.raises(Failure, match="linked_path"):
        safe_path(tmp_path)


def test_package_limits_and_sensitive_files(workspace):
    service, source, _ = workspace
    root = source / "markdown-check"
    with pytest.raises(Failure, match="package_size_or_files"):
        read_package(root, replace(Limits(), skill_bytes=10))
    (root / ".env").write_text("DO_NOT_COPY=anything")
    item = service.call("inventory", refresh=True)["items"][0]
    assert item["state"] == "blocked" and item["reason"] == "sensitive_file"


def test_failed_rewrite_does_not_revoke_valid_metadata(workspace):
    service, _, metadata = workspace
    item, reference = prepared(service, metadata)
    assert (
        service.call(
            "submit", "rewrite", skill_id=item["id"], version=item["version"], candidate={}
        )["status"]
        == "needs_revision"
    )
    assert not service.call("publish", "rewrite")["changed"]
    assert service.call("load", "reader", reference=reference)["status"] == "ok"


def test_publish_failure_preserves_active_catalog(workspace, monkeypatch):
    service, source, metadata = workspace
    _, reference = prepared(service, metadata)
    (source / "markdown-check" / "references/checklist.md").write_text("update")
    item = service.call("inventory", "update", refresh=True)["items"][0]
    service.call(
        "submit", "update", skill_id=item["id"], version=item["version"], candidate=metadata
    )

    def disk_failure(*args):
        raise OSError("simulated disk full")

    monkeypatch.setattr(service, "_snapshot", disk_failure)
    assert service.call("publish", "update")["status"] == "catalog_unavailable"
    assert service._active() == reference["catalog"]
    assert service.call("load", "reader", reference=reference)["status"] == "ok"


def test_timeout_rolls_back_inventory(workspace, monkeypatch):
    service, _, _ = workspace

    def timeout():
        raise Failure("timeout", "operation_deadline")

    monkeypatch.setattr(service, "check_time", timeout)
    assert service.call("inventory", refresh=True)["status"] == "timeout"
    assert service.db.execute("SELECT count(*) FROM sources").fetchone()[0] == 0


def test_catalog_lock_returns_typed_failure(workspace):
    service, _, _ = workspace
    other = sqlite3.connect(service.state / "catalog.sqlite3")
    try:
        other.execute("BEGIN IMMEDIATE")
        assert service.call("search", query="anything")["status"] == "catalog_unavailable"
    finally:
        other.rollback()
        other.close()


def test_metadata_change_creates_catalog_and_snapshot_remains(workspace):
    service, _, metadata = workspace
    item, reference = prepared(service, metadata)
    updated = copy.deepcopy(metadata)
    updated["description"] += " Additional wording."
    assert (
        service.call(
            "submit", "rewrite", skill_id=item["id"], version=item["version"], candidate=updated
        )["status"]
        == "ok"
    )
    assert service.call("publish", "rewrite")["catalog"] != reference["catalog"]
    old = service.db.execute(
        "SELECT metadata FROM entries WHERE catalog=?", (reference["catalog"],)
    ).fetchone()[0]
    assert json.loads(old) == metadata
    assert service.call("load", "reader", reference=reference)["status"] == "ok"


def test_whole_resource_plan_must_fit_before_loading(workspace):
    service, source, metadata = workspace
    service.limits = replace(service.limits, load=4000)
    (source / "markdown-check" / "references/checklist.md").write_text("x" * 5000)
    _, reference = prepared(service, metadata)
    result = service.call("load", "reader", reference=reference)
    assert result["status"] == "budget_exceeded"
    assert result["reason"] == "whole_package_plan_does_not_fit"
    assert "content" not in result
    assert (
        service.call("resource", "reader", reference=reference, file="SKILL.md")["reason"]
        == "load_required_before_resource"
    )


def test_repeated_resources_cannot_bypass_load_budget(workspace):
    service, _, metadata = workspace
    service.limits = replace(service.limits, load=4000)
    _, reference = prepared(service, metadata)
    assert service.call("load", "reader", reference=reference)["status"] == "ok"
    for _ in range(20):
        result = service.call(
            "resource", "reader", reference=reference, file="references/checklist.md"
        )
        if result["status"] == "budget_exceeded":
            break
    else:
        pytest.fail("Repeated resource reads bypassed the cumulative plan budget")
    emitted = service.db.execute(
        "SELECT spent FROM emissions WHERE session_id='reader'"
    ).fetchone()[0]
    assert emitted <= 4000


def test_deterministic_ties_and_explicit_request(workspace):
    import shutil

    service, source, metadata = workspace
    shutil.copytree(source / "markdown-check", source / "same-skill")
    items = service.call("inventory", "prepare-two", refresh=True)["items"]
    for item in items:
        assert (
            service.call(
                "submit",
                "prepare-two",
                skill_id=item["id"],
                version=item["version"],
                candidate=metadata,
            )["status"]
            == "ok"
        )
    service.call("publish", "prepare-two")
    first = service.call("search", "one", query="markdown")
    second = service.call("search", "two", query="markdown")
    assert first == second
    ids = [card["reference"]["skill_id"] for card in first["candidates"]]
    assert ids == sorted(ids)
    explicit = service.call("search", "explicit", query="does not matter", explicit_id=ids[-1])
    assert explicit["candidates"][0]["reference"]["skill_id"] == ids[-1]
