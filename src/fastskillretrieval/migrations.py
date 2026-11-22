"""Explicit, offline v0.1 import; normal registry startup never upgrades a database.

The default only prepares a sibling state. Operators must stop legacy processes
before requesting cutover. A failed protection check leaves the old path intact.
Interrupted imports keep their staging directory for inspection and can be retried.
"""

import json
import os
import sqlite3
import stat
import uuid
from contextlib import closing
from pathlib import Path

from .capabilities import parse_definition, validate_manifest
from .contracts import Failure, Limits, serialized, validate_candidate
from .packages import digest, read_package, relative_file, safe_path
from .registry import Registry

LEGACY_COLUMNS = {
    "config": {"key", "value"},
    "sources": {"id", "name", "version", "state", "reason", "enabled"},
    "versions": {"skill_id", "version", "manifest", "candidate", "state", "attempts"},
    "catalogs": {"id", "signature"},
    "entries": {"catalog", "skill_id", "version", "metadata"},
    "sessions": {"id", "spent", "pins"},
    "emissions": {"session_id", "skill_id", "version", "spent"},
    "counters": {"name", "value"},
    "skill_fts": {
        "skill_id",
        "version",
        "description",
        "capabilities",
        "use_when",
        "aliases",
        "technologies",
    },
}


def _readonly(path):
    return sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=1)


def _identify(connection, root):
    if connection.execute("PRAGMA user_version").fetchone()[0] != 0:
        raise Failure("migration_required", "unknown_legacy_schema")
    for table, expected in LEGACY_COLUMNS.items():
        columns = {row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')}
        if columns != expected:
            raise Failure("migration_required", "unknown_legacy_schema")
    if not connection.execute(
        "SELECT sql FROM sqlite_master WHERE name='skill_fts' AND sql LIKE '%fts5%'"
    ).fetchone():
        raise Failure("migration_required", "unknown_legacy_schema")
    identity = connection.execute("SELECT value FROM config WHERE key='root'").fetchone()
    if not identity or identity[0] != os.path.normcase(str(root)):
        raise Failure("policy_denied", "state_belongs_to_another_root")
    if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        raise Failure("catalog_unavailable", "legacy_integrity_check_failed")


def _fingerprint(connection):
    # A logical fingerprint includes uncheckpointed WAL changes without writing v1.
    memory = sqlite3.connect(":memory:")
    try:
        connection.backup(memory)
        return digest("\n".join(memory.iterdump()).encode())
    finally:
        memory.close()


def _write_json(path, data):
    temporary = path.with_name(path.name + ".tmp-" + uuid.uuid4().hex)
    temporary.write_text(serialized(data), encoding="utf-8")
    os.replace(temporary, path)


def _copy_package(origin, destination, expected, revision):
    files, hashes, actual = read_package(origin, Limits())
    if actual != revision or serialized(hashes) != expected:
        raise Failure("stale_reference", "legacy_snapshot_changed")
    destination.mkdir(parents=True)
    for name, data in files.items():
        target = relative_file(destination, name)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    _, copied, actual = read_package(destination, Limits())
    if actual != revision or copied != hashes:
        raise Failure("stale_reference", "snapshot_copy_failed")
    return files


def _manifest(name, package, candidate):
    if package is not None:
        try:
            return parse_definition(package / "SKILL.md", "skill")
        except Failure:
            # Legacy accepted more Markdown than the new declarative parser. The
            # old content remains immutable; load still checks native semantics.
            pass
    metadata = json.loads(candidate) if candidate else {}
    return validate_manifest(
        {
            "kind": "skill",
            "name": name,
            "description_original": metadata.get("description", f"Legacy skill {name}"),
            "activation": {"mode": "load"},
        }
    )


def _import(connection, registry, legacy):
    sources = connection.execute("SELECT * FROM sources ORDER BY id").fetchall()
    versions = connection.execute("SELECT * FROM versions ORDER BY skill_id,version").fetchall()
    entries = connection.execute("SELECT * FROM entries ORDER BY catalog,skill_id").fetchall()
    by_id = {row["id"]: row for row in sources}
    manifests, packages = {}, {}
    published = {(row["skill_id"], row["version"]) for row in entries}
    for row in versions:
        identity, revision = row["skill_id"], row["version"]
        if identity not in by_id:
            raise Failure("migration_required", "legacy_orphan_version")
        # SQL identifiers are never accepted as filesystem paths.
        if (
            not identity
            or any(char not in "0123456789abcdef" for char in identity)
            or len(identity) != 24
        ):
            raise Failure("policy_denied", "invalid_legacy_identity")
        if (
            not revision
            or any(char not in "0123456789abcdef" for char in revision)
            or len(revision) != 64
        ):
            raise Failure("policy_denied", "invalid_legacy_revision")
        source = by_id[identity]
        snapshot = safe_path(legacy / "packages" / identity / revision)
        current = relative_file(registry.root, source["name"])
        package, files = None, None
        if snapshot.exists():
            package = snapshot
        elif source["version"] == revision and current.is_dir():
            try:
                if read_package(current, registry.limits)[2] == revision:
                    package = current
            except Failure:
                pass
        if (identity, revision) in published and not snapshot.is_dir():
            raise Failure("stale_reference", "published_legacy_snapshot_missing")
        if package:
            destination = safe_path(registry.state / "packages" / identity / revision)
            files = _copy_package(package, destination, row["manifest"], revision)
            packages[identity, revision] = files
        hashes = json.loads(row["manifest"])
        if (
            not isinstance(hashes, dict)
            or "SKILL.md" not in hashes
            or digest(serialized(hashes).encode()) != revision
        ):
            raise Failure("migration_required", "invalid_legacy_file_manifest")
        manifests[identity, revision] = _manifest(source["name"], package, row["candidate"])

    for source in sources:
        key = (source["id"], source["version"])
        manifest = manifests.get(key) or _manifest(source["name"], None, None)
        registry.db.execute(
            "INSERT INTO capabilities VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                source["id"],
                source["name"],
                "skill",
                registry.scope,
                source["version"],
                source["state"],
                "enabled" if source["enabled"] else "disabled",
                source["reason"],
                serialized(manifest),
                "SKILL.md",
            ),
        )
    for row in versions:
        key = row["skill_id"], row["version"]
        registry.db.execute(
            "INSERT INTO source_revisions VALUES(?,?,?,?,?)",
            (*key, row["manifest"], "SKILL.md", serialized(manifests[key])),
        )
        if row["attempts"] < 0:
            raise Failure("migration_required", "invalid_legacy_attempts")
        registry.db.execute(
            "INSERT INTO attempts VALUES(?,?,?,?)", (*key, "rules-v1", row["attempts"])
        )

    def descriptor(identity, revision, metadata):
        candidate = json.loads(metadata)
        # Validation is repeated when bytes exist; inaccessible historical drafts
        # are retained as immutable metadata, never implicitly published.
        if (identity, revision) in packages:
            candidate = validate_candidate(candidate, packages[identity, revision], registry.limits)
        encoded = serialized(candidate)
        descriptor_id = digest(serialized([identity, revision, candidate, "rules-v1"]).encode())
        registry.db.execute(
            "INSERT OR IGNORE INTO descriptor_revisions VALUES(?,?,?,?,?)",
            (descriptor_id, identity, revision, encoded, "rules-v1"),
        )
        return descriptor_id

    for row in connection.execute("SELECT * FROM catalogs ORDER BY id"):
        # IDs are stable. Prefixing the legacy signature prevents collision with
        # signatures produced by the v2 publication algorithm.
        registry.db.execute(
            "INSERT INTO catalogs VALUES(?,?)", (row["id"], "legacy:" + row["signature"])
        )
    for row in entries:
        identity, revision = row["skill_id"], row["version"]
        descriptor_id = descriptor(identity, revision, row["metadata"])
        registry.db.execute(
            "INSERT INTO entries VALUES(?,?,?,?,?)",
            (
                row["catalog"],
                identity,
                revision,
                descriptor_id,
                serialized(manifests[identity, revision]),
            ),
        )
        reference = {
            "schema_version": 2,
            "capability_id": identity,
            "source_revision": revision,
            "descriptor_revision": descriptor_id,
            "catalog_id": row["catalog"],
            "scope_id": registry.scope,
        }
        registry.db.execute(
            "INSERT INTO legacy_refs VALUES(?,?,?,?)",
            (row["catalog"], identity, revision, serialized(reference)),
        )
    # The v2 publisher uses insertion order to select the latest descriptor.
    # Import the current validated draft after immutable published history so a
    # correction accepted by v1 but not yet published remains the next candidate.
    for row in versions:
        if row["candidate"]:
            descriptor_id = descriptor(row["skill_id"], row["version"], row["candidate"])
            registry.db.execute(
                "UPDATE capabilities SET reason=? WHERE id=? AND source_revision=? "
                "AND state IN ('validated','published')",
                (descriptor_id, row["skill_id"], row["version"]),
            )
    for row in connection.execute("SELECT * FROM sessions"):
        if row["spent"] < 0 or not isinstance(json.loads(row["pins"]), dict):
            raise Failure("migration_required", "invalid_legacy_session")
        registry.db.execute("INSERT INTO sessions VALUES(?,?,?)", tuple(row))
    for row in connection.execute("SELECT * FROM emissions"):
        if row["spent"] < 0:
            raise Failure("migration_required", "invalid_legacy_emission")
        registry.db.execute("INSERT INTO emissions VALUES(?,?,?,?)", tuple(row))
        registry.db.execute(
            "INSERT INTO content_charges(session_id,members,spent) VALUES(?,?,?) "
            "ON CONFLICT(session_id,members) DO UPDATE SET spent=spent+excluded.spent",
            (row["session_id"], serialized([row["skill_id"]]), row["spent"]),
        )
    active_row = connection.execute("SELECT value FROM config WHERE key='active'").fetchone()
    active = int(active_row[0]) if active_row else 0
    if (
        active
        and not registry.db.execute("SELECT 1 FROM catalogs WHERE id=?", (active,)).fetchone()
    ):
        raise Failure("migration_required", "legacy_active_catalog_missing")
    registry._activate(active)
    if (
        registry.db.execute("PRAGMA integrity_check").fetchone()[0] != "ok"
        or registry.db.execute("PRAGMA foreign_key_check").fetchall()
    ):
        raise Failure("catalog_unavailable", "migrated_integrity_check_failed")
    return {
        "capabilities": len(sources),
        "source_revisions": len(versions),
        "legacy_references": len(entries),
        "active_catalog": active,
    }


def _deny_writes(path):
    """Verify actual SQLite denial, not just mode bits (root/ACLs may bypass them)."""
    path.chmod(stat.S_IREAD | stat.S_IRGRP | stat.S_IROTH)
    connection = None
    try:
        connection = sqlite3.connect(path, timeout=1)
        connection.execute("BEGIN IMMEDIATE")
        # A zero-row UPDATE still asks SQLite for write permission, without
        # changing data even on a filesystem where protection is ineffective.
        connection.execute("UPDATE config SET value=value WHERE 0")
        connection.rollback()
        return False
    except sqlite3.OperationalError as error:
        return "readonly" in str(error).casefold() or "read-only" in str(error).casefold()
    finally:
        if connection:
            connection.close()


def _mark_activated(target):
    database = safe_path(target / "registry.sqlite3")
    if not database.is_file():
        raise Failure("catalog_unavailable", "migration_target_missing")
    with closing(sqlite3.connect(database, timeout=1)) as connection:
        if connection.execute("PRAGMA user_version").fetchone()[0] != 2:
            raise Failure("migration_required", "unknown_registry_schema")
        with connection:
            connection.execute(
                "INSERT INTO config VALUES('migration_phase','activated') "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value"
            )


def _cutover(legacy, target, root, marker):
    original_db = safe_path(legacy / "catalog.sqlite3")
    original_mode = original_db.stat().st_mode
    # Protection is checked before renaming so refusal preserves the old path.
    if not _deny_writes(original_db):
        original_db.chmod(original_mode)
        raise Failure("policy_denied", "backup_protection_unavailable")
    retired = safe_path(legacy.with_name("legacy-retired-" + uuid.uuid4().hex))
    parent = safe_path(legacy.parent)
    paths = (legacy, target, retired)
    if any(path.resolve().parent != parent.resolve() for path in paths) or len(set(paths)) != 3:
        original_db.chmod(original_mode)
        raise Failure("policy_denied", "migration_paths_must_be_separate_siblings")
    try:
        _write_json(
            marker,
            {
                "phase": "cutover_pending",
                "legacy": str(legacy),
                "target": str(target),
                "retired": str(retired),
            },
        )
        os.replace(legacy, retired)
        try:
            legacy.write_text(
                "FastSkillRetrieval legacy state retired. Use the explicit v2 state.\n",
                encoding="utf-8",
            )
        except BaseException:
            os.replace(retired, legacy)
            original_db.chmod(original_mode)
            raise
        # Exercise the old constructor. It must fail at mkdir, before opening DB.
        from .catalog import Catalog

        try:
            unexpected = Catalog(legacy, root)
        except (OSError, Failure):
            pass
        else:
            unexpected.close()
            raise Failure("policy_denied", "legacy_path_not_blocked")
        if not _deny_writes(retired / "catalog.sqlite3"):
            raise Failure("policy_denied", "backup_protection_unavailable")
        _mark_activated(target)
        result = {"activation": "activated", "retired_state": str(retired)}
        _write_json(
            marker, {"phase": "complete", "legacy": str(legacy), "target": str(target), **result}
        )
        return result
    except BaseException:
        # If the rename failed (for example a running Windows process holds the
        # file), keep v1 usable. After a tombstone exists, retain it for recovery.
        if legacy.is_dir():
            original_db.chmod(original_mode)
        raise


def _resume_cutover(legacy, target, root, marker, receipt):
    """Complete the narrow crash window between directory rename and tombstone."""
    if not marker.is_file():
        return None
    pending = json.loads(marker.read_text(encoding="utf-8"))
    if pending.get("legacy") != str(legacy) or pending.get("target") != str(target):
        raise Failure("policy_denied", "migration_marker_scope_mismatch")
    phase = pending.get("phase")
    if phase not in {"cutover_pending", "complete"}:
        return None
    retired = safe_path(Path(pending.get("retired_state", pending.get("retired", ""))))
    if retired.parent != legacy.parent or retired in {legacy, target}:
        raise Failure("policy_denied", "migration_marker_scope_mismatch")
    if legacy.is_dir():
        return None  # Rename never happened; normal activation can be retried.
    with closing(_readonly(retired / "catalog.sqlite3")) as previous:
        _identify(previous, root)
        if _fingerprint(previous) != receipt["legacy_fingerprint"]:
            raise Failure("stale_reference", "retired_state_changed")
    if not _deny_writes(retired / "catalog.sqlite3"):
        raise Failure("policy_denied", "backup_protection_unavailable")
    if not legacy.exists():
        legacy.write_text("FastSkillRetrieval legacy state retired. Use v2.\n", encoding="utf-8")
    if not legacy.is_file():
        raise Failure("policy_denied", "legacy_path_not_blocked")
    _mark_activated(target)
    result = {"activation": "activated", "retired_state": str(retired), "already_activated": True}
    _write_json(
        marker, {"phase": "complete", "legacy": str(legacy), "target": str(target), **result}
    )
    return result


def migrate_legacy(
    legacy_state: Path, target_state: Path, source_root: Path, activate: bool = False
) -> dict:
    """Import v0.1 into a new sibling state and optionally retire the old path.

    Typed refusal dictionaries are suitable for CLI/MCP output. Existing targets
    are never overwritten. A failed import can be retried; its staging directory
    is retained and named in the pending marker for operator inspection.
    """
    registry = connection = None
    try:
        legacy, target, root = (
            safe_path(Path(path)) for path in (legacy_state, target_state, source_root)
        )
        if type(activate) is not bool:
            raise Failure("policy_denied", "activate_must_be_boolean")
        if (
            target.parent != legacy.parent
            or target == legacy
            or root.is_relative_to(legacy)
            or legacy.is_relative_to(root)
        ):
            raise Failure("policy_denied", "migration_paths_must_be_separate_siblings")
        marker = safe_path(target.with_name(target.name + ".migration-pending.json"))
        if target.exists():
            # Activation can explicitly resume a successful default import.
            receipt = safe_path(target / "migration.json")
            if activate and receipt.is_file():
                data = json.loads(receipt.read_text(encoding="utf-8"))
                if data.get("legacy") == str(legacy) and data.get("target") == str(target):
                    resumed = _resume_cutover(legacy, target, root, marker, data)
                    if resumed:
                        return {"status": "ok", "target_state": str(target), **resumed}
                    with closing(_readonly(legacy / "catalog.sqlite3")) as old:
                        _identify(old, root)
                        if _fingerprint(old) != data["legacy_fingerprint"]:
                            raise Failure("stale_reference", "legacy_changed_since_import")
                    return {
                        "status": "ok",
                        "target_state": str(target),
                        **_cutover(legacy, target, root, marker),
                    }
            raise Failure("migration_required", "target_already_exists")
        if not legacy.is_dir() or not root.is_dir() or not (legacy / "catalog.sqlite3").is_file():
            raise Failure("migration_required", "legacy_state_missing")
        connection = _readonly(safe_path(legacy / "catalog.sqlite3"))
        connection.row_factory = sqlite3.Row
        _identify(connection, root)
        # Keep temporary names short: legacy Windows installations may still
        # enforce MAX_PATH on the immutable 64-character revision directories.
        staging = safe_path(target.with_name(".m-" + uuid.uuid4().hex[:8]))
        _write_json(
            marker,
            {
                "phase": "preparing",
                "legacy": str(legacy),
                "target": str(target),
                "staging": str(staging),
            },
        )
        staging.mkdir()
        backup_folder = safe_path(staging / "legacy-backup")
        backup_folder.mkdir()
        backup = backup_folder / "catalog.sqlite3"
        with closing(sqlite3.connect(backup)) as destination:
            connection.backup(destination)
        connection.close()
        connection = _readonly(backup)
        connection.row_factory = sqlite3.Row
        _identify(connection, root)
        fingerprint = _fingerprint(connection)
        registry = Registry(staging, root)
        with registry.db:
            counts = _import(connection, registry, legacy)
            registry.db.execute("INSERT INTO config VALUES('migration_phase','pending_activation')")
        registry.close()
        registry = None
        connection.close()
        connection = None
        # Make the archival DB unusable for ordinary legacy writes; a separate
        # copy of each package is already hash-verified in the v2 package store.
        protected = _deny_writes(backup)
        if not protected:
            raise Failure("policy_denied", "backup_protection_unavailable")
        receipt = {
            "legacy": str(legacy),
            "target": str(target),
            "legacy_fingerprint": fingerprint,
            "activation": "pending_activation",
            "backup_write_protection_verified": protected,
            **counts,
        }
        _write_json(staging / "migration.json", receipt)
        with closing(_readonly(legacy / "catalog.sqlite3")) as current:
            if _fingerprint(current) != fingerprint:
                raise Failure("stale_reference", "legacy_changed_during_import")
        os.replace(staging, target)
        _write_json(marker, {"phase": "prepared", **receipt})
        activation = (
            _cutover(legacy, target, root, marker)
            if activate
            else {"activation": "pending_activation"}
        )
        return {"status": "ok", "target_state": str(target), **counts, **activation}
    except Failure as error:
        return {"status": error.status, "reason": error.reason}
    except (OSError, sqlite3.Error, ValueError, TypeError, KeyError):
        return {"status": "catalog_unavailable", "reason": "migration_failed"}
    finally:
        if registry:
            registry.close()
        if connection:
            connection.close()
