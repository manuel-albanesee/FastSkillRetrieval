import json
import sqlite3

import pytest
from conftest import prepared

import fastskillretrieval.migrations as migrations
from fastskillretrieval.catalog import Catalog
from fastskillretrieval.contracts import cost, serialized
from fastskillretrieval.migrations import migrate_legacy
from fastskillretrieval.registry import Registry


def test_migration_preserves_legacy_references_and_budgets(workspace):
    legacy, root, metadata = workspace
    item, reference = prepared(legacy, metadata)
    assert legacy.call("load", "existing", reference=reference)["status"] == "ok"
    spent, pins = legacy.db.execute(
        "SELECT spent,pins FROM sessions WHERE id='existing'"
    ).fetchone()
    emissions = tuple(
        legacy.db.execute("SELECT * FROM emissions WHERE session_id='existing'").fetchone()
    )
    legacy.db.close()
    before = (legacy.state / "catalog.sqlite3").read_bytes()
    target = legacy.state.with_name("state-v2")
    result = migrate_legacy(legacy.state, target, root)
    assert result["status"] == "ok"
    assert result["activation"] == "pending_activation"
    assert (legacy.state / "catalog.sqlite3").read_bytes() == before
    registry = Registry(target, root)
    try:
        assert tuple(
            registry.db.execute("SELECT spent,pins FROM sessions WHERE id='existing'").fetchone()
        ) == (spent, pins)
        assert (
            tuple(
                registry.db.execute(
                    "SELECT * FROM emissions WHERE session_id='existing'"
                ).fetchone()
            )
            == emissions
        )
        assert (
            registry.db.execute(
                "SELECT count FROM attempts WHERE capability_id=?", (item["id"],)
            ).fetchone()[0]
            == 1
        )
        assert not registry.db.execute("SELECT * FROM daily_counters").fetchall()
        assert (
            registry.db.execute(
                "SELECT spent FROM content_charges WHERE session_id=? AND members=?",
                ("existing", serialized([item["id"]])),
            ).fetchone()[0]
            == emissions[3]
        )
        assert not registry.db.execute("PRAGMA foreign_key_check").fetchall()
        assert (
            registry.db.execute("SELECT value FROM config WHERE key='migration_phase'").fetchone()[
                0
            ]
            == "pending_activation"
        )
    finally:
        registry.close()
    assert migrate_legacy(legacy.state, target, root)["reason"] == "target_already_exists"
    assert migrate_legacy(legacy.state, target, root, activate=True)["status"] == "ok"
    registry = Registry(target, root)
    try:
        existing_load = registry.call("load", "existing", reference=reference)
        assert existing_load["status"] == "ok"
        assert registry.db.execute(
            "SELECT spent FROM load_groups WHERE session_id='existing'"
        ).fetchone()[0] == emissions[3] + cost(existing_load)
        assert registry.call("load", "new", reference=reference)["status"] == "ok"
        assert registry.call("search", "fresh", query="markdown")["status"] == "ok"
        assert not registry.db.execute("PRAGMA foreign_key_check").fetchall()
    finally:
        registry.close()


def test_reject_unknown_schema_and_scope(tmp_path):
    root, legacy = tmp_path / "skills", tmp_path / "old"
    root.mkdir()
    legacy.mkdir()
    connection = sqlite3.connect(legacy / "catalog.sqlite3")
    connection.execute("CREATE TABLE unrelated(value TEXT)")
    connection.close()
    result = migrate_legacy(legacy, tmp_path / "new", root)
    assert result == {"status": "migration_required", "reason": "unknown_legacy_schema"}
    assert not (tmp_path / "new").exists()


def test_immutable_published_descriptor_history(workspace):
    legacy, root, metadata = workspace
    item, first_ref = prepared(legacy, metadata)
    revised = {**metadata, "description": metadata["description"] + " Updated wording."}
    assert (
        legacy.call(
            "submit", "prepare2", skill_id=item["id"], version=item["version"], candidate=revised
        )["status"]
        == "ok"
    )
    assert legacy.call("publish", "prepare2")["status"] == "ok"
    legacy.close()
    target = legacy.state.with_name("state-v2")
    assert migrate_legacy(legacy.state, target, root, activate=True)["status"] == "ok"
    registry = Registry(target, root)
    try:
        descriptors = registry.db.execute(
            "SELECT metadata FROM descriptor_revisions WHERE capability_id=?", (item["id"],)
        ).fetchall()
        assert {json.loads(row[0])["description"] for row in descriptors} == {
            metadata["description"],
            revised["description"],
        }
        assert registry.call("load", "first", reference=first_ref)["status"] == "ok"
        assert (
            registry.db.execute(
                "SELECT count FROM attempts WHERE capability_id=?", (item["id"],)
            ).fetchone()[0]
            == 2
        )
    finally:
        registry.close()


def test_cutover_tombstone_blocks_old_runner(workspace):
    legacy, root, metadata = workspace
    prepared(legacy, metadata)
    legacy.close()
    target = legacy.state.with_name("state-v2")
    result = migrate_legacy(legacy.state, target, root, activate=True)
    assert result["status"] == "ok"
    assert result["activation"] == "activated"
    assert legacy.state.is_file()
    with pytest.raises(OSError):
        Catalog(legacy.state, root)
    retired = result["retired_state"]
    with pytest.raises((OSError, sqlite3.Error)):
        Catalog(retired, root)


def test_wal_import_and_interrupted_retry(workspace, monkeypatch):
    legacy, root, metadata = workspace
    prepared(legacy, metadata)
    assert legacy.db.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
    with legacy.db:
        legacy.db.execute("INSERT INTO sessions VALUES('wal-session',77,'{}')")
    target = legacy.state.with_name("state-v2")
    original = migrations._copy_package

    def interrupted(*arguments):
        raise OSError("simulated interruption during package copy")

    monkeypatch.setattr(migrations, "_copy_package", interrupted)
    assert migrate_legacy(legacy.state, target, root)["status"] == "catalog_unavailable"
    assert legacy.call("diagnostics", "still-usable")["status"] == "ok"
    assert not target.exists()
    monkeypatch.setattr(migrations, "_copy_package", original)
    result = migrate_legacy(legacy.state, target, root)
    assert result["status"] == "ok"
    registry = Registry(target, root)
    try:
        assert (
            registry.db.execute("SELECT spent FROM sessions WHERE id='wal-session'").fetchone()[0]
            == 77
        )
    finally:
        registry.close()


def test_import_then_activation_refuses_changed_legacy(workspace):
    legacy, root, metadata = workspace
    prepared(legacy, metadata)
    target = legacy.state.with_name("state-v2")
    assert migrate_legacy(legacy.state, target, root)["status"] == "ok"
    assert legacy.call("diagnostics", "later")["status"] == "ok"
    assert migrate_legacy(legacy.state, target, root, activate=True) == {
        "status": "stale_reference",
        "reason": "legacy_changed_since_import",
    }
    assert legacy.state.is_dir()


def test_activation_recovers_rename_before_tombstone(workspace, monkeypatch):
    legacy, root, metadata = workspace
    prepared(legacy, metadata)
    legacy.close()
    target = legacy.state.with_name("state-v2")
    assert migrate_legacy(legacy.state, target, root)["status"] == "ok"
    marker = target.with_name(target.name + ".migration-pending.json")
    retired = legacy.state.with_name("legacy-retired-fixture")
    (legacy.state / "catalog.sqlite3").chmod(0o444)
    legacy.state.rename(retired)
    marker.write_text(
        json.dumps(
            {
                "phase": "cutover_pending",
                "legacy": str(legacy.state),
                "target": str(target),
                "retired": str(retired),
            }
        )
    )
    result = migrate_legacy(legacy.state, target, root, activate=True)
    assert result["status"] == "ok"
    assert result["activation"] == "activated"
    assert legacy.state.is_file()
    assert migrate_legacy(legacy.state, target, root, activate=True)["already_activated"]


def test_migration_refuses_tampered_snapshot(workspace):
    legacy, root, metadata = workspace
    item, _ = prepared(legacy, metadata)
    legacy.close()
    snapshot = legacy.state / "packages" / item["id"] / item["version"] / "SKILL.md"
    snapshot.write_text("Changed immutable package", encoding="utf-8")
    target = legacy.state.with_name("state-v2")
    assert migrate_legacy(legacy.state, target, root) == {
        "status": "stale_reference",
        "reason": "legacy_snapshot_changed",
    }
    assert legacy.state.is_dir()
    assert not target.exists()


def test_migration_preserves_disabled_state_and_attempt_exhaustion(workspace):
    legacy, root, metadata = workspace
    item, reference = prepared(legacy, metadata)
    assert (
        legacy.call(
            "submit", "retry", skill_id=item["id"], version=item["version"], candidate=metadata
        )["status"]
        == "ok"
    )
    assert (
        legacy.call("set_enabled", "disable", skill_id=item["id"], enabled=False)["status"] == "ok"
    )
    legacy.close()
    target = legacy.state.with_name("state-v2")
    assert migrate_legacy(legacy.state, target, root, activate=True)["status"] == "ok"
    registry = Registry(target, root)
    try:
        assert registry.call("load", "blocked", reference=reference)["status"] == "policy_denied"
        response = registry.call(
            "submit",
            "exhausted",
            capability_id=item["id"],
            source_revision=item["version"],
            candidate=metadata,
        )
        assert response == {"status": "limit_exceeded", "reason": "two_attempts_exhausted"}
        assert not registry.db.execute(
            "SELECT * FROM emissions WHERE session_id='blocked'"
        ).fetchall()
    finally:
        registry.close()


def test_backup_protection_failure_preserves_legacy(workspace, monkeypatch):
    legacy, root, metadata = workspace
    prepared(legacy, metadata)
    legacy.close()
    previous = (legacy.state / "catalog.sqlite3").read_bytes()
    monkeypatch.setattr(migrations, "_deny_writes", lambda path: False)
    target = legacy.state.with_name("state-v2")
    assert migrate_legacy(legacy.state, target, root, activate=True) == {
        "status": "policy_denied",
        "reason": "backup_protection_unavailable",
    }
    assert legacy.state.is_dir()
    assert (legacy.state / "catalog.sqlite3").read_bytes() == previous
    assert not target.exists()


def test_migration_rejects_other_source_root(workspace):
    legacy, root, metadata = workspace
    prepared(legacy, metadata)
    legacy.close()
    other = root.with_name("other-skills")
    other.mkdir()
    assert migrate_legacy(legacy.state, legacy.state.with_name("state-v2"), other) == {
        "status": "policy_denied",
        "reason": "state_belongs_to_another_root",
    }


def test_pending_migration_denies_emission_until_verified_cutover(workspace):
    legacy, root, metadata = workspace
    _, reference = prepared(legacy, metadata)
    legacy.close()
    target = legacy.state.with_name("state-v2")
    assert migrate_legacy(legacy.state, target, root)["activation"] == "pending_activation"
    registry = Registry(target, root)
    try:
        response = registry.call("load", "pending", reference=reference)
        assert response["status"] == "migration_required"
        assert "content" not in response
        diagnostics = registry.call("diagnostics", "operator")
        assert diagnostics["status"] == "ok"
        assert diagnostics["migration_phase"] == "pending_activation"
        assert not registry.db.execute(
            "SELECT * FROM emissions WHERE session_id='pending'"
        ).fetchall()
    finally:
        registry.close()
    assert migrate_legacy(legacy.state, target, root, activate=True)["activation"] == "activated"
    registry = Registry(target, root)
    try:
        assert registry.call("diagnostics", "operator")["migration_phase"] == "activated"
        assert registry.call("load", "pending", reference=reference)["status"] == "ok"
    finally:
        registry.close()


def test_migration_keeps_validated_unpublished_candidate_current(workspace):
    legacy, root, metadata = workspace
    item, old_reference = prepared(legacy, metadata)
    revised = {**metadata, "description": metadata["description"] + " Unpublished correction."}
    assert (
        legacy.call(
            "submit", "revision", skill_id=item["id"], version=item["version"], candidate=revised
        )["status"]
        == "ok"
    )
    legacy.close()
    target = legacy.state.with_name("state-v2")
    assert migrate_legacy(legacy.state, target, root, activate=True)["status"] == "ok"
    registry = Registry(target, root)
    try:
        assert registry.call("publish", "operator")["status"] == "ok"
        found = registry.call("search", "reader", explicit_id=item["id"])
        assert found["candidates"][0]["description"] == revised["description"]
        old, _ = registry._entry_ref(old_reference)
        historical = registry.db.execute(
            "SELECT metadata FROM descriptor_revisions WHERE id=?", (old["descriptor_revision"],)
        ).fetchone()[0]
        assert json.loads(historical)["description"] == metadata["description"]
    finally:
        registry.close()
