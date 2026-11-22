"""Versioned capability registry. Retrieval never grants execution authority."""

import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import sqlite3
import time
import uuid
from pathlib import Path

from .contracts import (
    Failure,
    Limits,
    cost,
    query_terms,
    serialized,
    technology,
    terms,
    validate_candidate,
)
from .graph import SkillGraph
from .packages import (
    digest,
    read_package,
    relative_file,
    replace_snapshot,
    safe_path,
    supported_instructions,
)

SCHEMA_VERSION = 2
DETAIL_LIMIT = 100_000_000
SCHEMA = """
CREATE TABLE config(key TEXT PRIMARY KEY,value TEXT NOT NULL) STRICT;
CREATE TABLE capabilities(
 id TEXT PRIMARY KEY,name TEXT NOT NULL,kind TEXT NOT NULL,scope TEXT NOT NULL,
 source_revision TEXT,state TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'enabled',
 reason TEXT NOT NULL DEFAULT '',manifest TEXT NOT NULL,entry_file TEXT NOT NULL) STRICT;
CREATE TABLE source_revisions(
 capability_id TEXT NOT NULL REFERENCES capabilities(id),revision TEXT NOT NULL,
 files TEXT NOT NULL,entry_file TEXT NOT NULL,manifest TEXT NOT NULL,
 PRIMARY KEY(capability_id,revision)) STRICT;
CREATE TABLE descriptor_revisions(
 id TEXT PRIMARY KEY,capability_id TEXT NOT NULL,source_revision TEXT NOT NULL,
 metadata TEXT NOT NULL,rules TEXT NOT NULL,
 FOREIGN KEY(capability_id,source_revision)
 REFERENCES source_revisions(capability_id,revision)) STRICT;
CREATE INDEX descriptor_source_lookup ON descriptor_revisions(capability_id,source_revision);
CREATE TABLE attempts(
 capability_id TEXT NOT NULL,source_revision TEXT NOT NULL,rules TEXT NOT NULL,
 count INTEGER NOT NULL,PRIMARY KEY(capability_id,source_revision,rules)) STRICT;
CREATE TABLE catalogs(id INTEGER PRIMARY KEY,signature TEXT NOT NULL UNIQUE) STRICT;
CREATE TABLE entries(
 catalog_id INTEGER NOT NULL REFERENCES catalogs(id),
 capability_id TEXT NOT NULL REFERENCES capabilities(id),
 source_revision TEXT NOT NULL,
 descriptor_revision TEXT NOT NULL REFERENCES descriptor_revisions(id),
 manifest TEXT NOT NULL,PRIMARY KEY(catalog_id,capability_id)) STRICT;
CREATE VIRTUAL TABLE capability_fts USING fts5(
 capability_id UNINDEXED,description,capabilities,use_when,aliases,technologies);
CREATE TABLE sessions(id TEXT PRIMARY KEY,spent INTEGER NOT NULL DEFAULT 0,
 pins TEXT NOT NULL DEFAULT '{}') STRICT;
CREATE TABLE emissions(session_id TEXT NOT NULL,capability_id TEXT NOT NULL,
 source_revision TEXT NOT NULL,
 spent INTEGER NOT NULL,PRIMARY KEY(session_id,capability_id,source_revision)) STRICT;
CREATE TABLE content_charges(session_id TEXT NOT NULL,members TEXT NOT NULL,spent INTEGER NOT NULL,
 PRIMARY KEY(session_id,members)) STRICT;
CREATE TABLE load_groups(session_id TEXT NOT NULL,root_id TEXT NOT NULL,root_revision TEXT NOT NULL,
 members TEXT NOT NULL,spent INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(session_id,root_id,root_revision)) STRICT;
CREATE TABLE request_ledger(
 session_id TEXT NOT NULL,request_id TEXT NOT NULL,fingerprint TEXT NOT NULL,response TEXT NOT NULL,
 created REAL NOT NULL,epoch INTEGER NOT NULL,delivery TEXT NOT NULL DEFAULT 'delivery_unconfirmed',
 PRIMARY KEY(session_id,request_id)) STRICT;
CREATE TABLE events(
 id INTEGER PRIMARY KEY,at REAL NOT NULL,request_id TEXT NOT NULL,capability_id TEXT NOT NULL,
 name TEXT NOT NULL,phase TEXT NOT NULL,UNIQUE(request_id,capability_id,name,phase)) STRICT;
CREATE TABLE daily_counters(
 day TEXT NOT NULL,capability_id TEXT NOT NULL,name TEXT NOT NULL,value INTEGER NOT NULL,
 PRIMARY KEY(day,capability_id,name)) STRICT;
CREATE TABLE plans(id TEXT PRIMARY KEY,session_id TEXT NOT NULL,
 data TEXT NOT NULL,created REAL NOT NULL) STRICT;
CREATE TABLE legacy_refs(
 catalog INTEGER NOT NULL,skill_id TEXT NOT NULL,version TEXT NOT NULL,reference TEXT NOT NULL,
 PRIMARY KEY(catalog,skill_id,version)) STRICT;
CREATE TABLE maintenance_runs(id TEXT PRIMARY KEY,at REAL NOT NULL,status TEXT NOT NULL) STRICT;
CREATE TABLE findings(capability_id TEXT NOT NULL,reason TEXT NOT NULL,
 PRIMARY KEY(capability_id,reason)) STRICT;
CREATE TABLE admissions(capability_id TEXT PRIMARY KEY,at REAL NOT NULL,
 search_baseline INTEGER NOT NULL,retrieval_baseline INTEGER NOT NULL) STRICT;
PRAGMA user_version=2;
"""


class Registry:
    def __init__(
        self,
        state: Path,
        source_root: Path,
        limits: Limits | None = None,
        *,
        retrieval_policy: str = "graph",
    ):
        if retrieval_policy not in {"graph", "lexical"}:
            raise ValueError("retrieval_policy must be graph or lexical")
        self.retrieval_policy = retrieval_policy
        self.state, self.root = safe_path(state), safe_path(source_root)
        self.limits = limits or Limits()
        if (
            not self.root.is_dir()
            or self.state.is_relative_to(self.root)
            or self.root.is_relative_to(self.state)
        ):
            raise Failure("policy_denied", "invalid_source_or_state_scope")
        self.scope = digest(os.path.normcase(str(self.root)).encode())[:24]
        self.state.mkdir(parents=True, exist_ok=True)
        if safe_path(self.state / "catalog.sqlite3").exists():
            raise Failure("migration_required", "explicit_sibling_migration_required")
        path = safe_path(self.state / "registry.sqlite3")
        self.db = sqlite3.connect(path, timeout=1)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        try:
            version = self.db.execute("PRAGMA user_version").fetchone()[0]
            tables = self.db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            if version == 0 and not tables:
                self.db.executescript(SCHEMA)
                with self.db:
                    self.db.execute(
                        "INSERT INTO config VALUES('root',?)", (os.path.normcase(str(self.root)),)
                    )
                    self.db.execute("INSERT INTO config VALUES('active','0')")
            elif version != SCHEMA_VERSION:
                raise Failure("migration_required", "unknown_registry_schema")
            with sqlite3.connect(":memory:") as expected:
                expected.executescript(SCHEMA)
                for (table,) in expected.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ):
                    actual_columns = self.db.execute(f'PRAGMA table_info("{table}")').fetchall()
                    expected_columns = expected.execute(f'PRAGMA table_info("{table}")').fetchall()
                    if [tuple(row) for row in actual_columns] != expected_columns:
                        raise Failure("migration_required", "unknown_registry_schema_shape")
            if self.db.execute("SELECT value FROM config WHERE key='root'").fetchone()[
                0
            ] != os.path.normcase(str(self.root)):
                raise Failure("policy_denied", "state_belongs_to_another_root")
            # Derived lookup index; existing v2 tables and references stay unchanged.
            self.db.execute(
                "CREATE INDEX IF NOT EXISTS descriptor_source_lookup "
                "ON descriptor_revisions(capability_id,source_revision)"
            )
            # The HMAC key is never stored in metadata, events, exports or tool output.
            key_path = safe_path(self.state / ".request-key")
            if not key_path.exists():
                if self.db.execute("SELECT count(*) FROM request_ledger").fetchone()[0]:
                    raise Failure("catalog_unavailable", "request_key_missing")
                descriptor = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(secrets.token_bytes(32))
            self.key = key_path.read_bytes()
            if len(self.key) != 32:
                raise Failure("catalog_unavailable", "invalid_request_key")
            self.graph = SkillGraph(self.db, self.state, self.check)
        except BaseException:
            self.db.close()
            raise
        self.deadline = float("inf")

    def close(self):
        self.db.close()

    def active(self):
        return int(self.db.execute("SELECT value FROM config WHERE key='active'").fetchone()[0])

    def check(self):
        if time.monotonic() > self.deadline:
            raise Failure("timeout", "operation_deadline")

    def call(self, operation: str, session_id: str = "default", **arguments) -> dict:
        if not isinstance(session_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", session_id):
            return {"status": "policy_denied", "reason": "invalid_session_id"}
        handlers = {
            name: getattr(self, "_" + name)
            for name in (
                "discover",
                "inventory",
                "inspect",
                "submit",
                "publish",
                "search",
                "plan",
                "prepare",
                "load",
                "resource",
                "set_status",
                "rollback",
                "reconcile",
                "diagnostics",
                "graph",
                "export",
                "delivery",
            )
        }
        self.session = session_id
        self.deadline = time.monotonic() + (
            2
            if operation == "search"
            else 30
            if operation in {"discover", "publish", "reconcile", "graph"}
            else 5
        )
        try:
            if operation not in handlers:
                raise Failure("policy_denied", "unknown_operation")
            phase = self.db.execute(
                "SELECT value FROM config WHERE key='migration_phase'"
            ).fetchone()
            if phase and phase[0] != "activated" and operation != "diagnostics":
                raise Failure("migration_required", "migration_pending_activation")
            with self.db:
                self.db.execute("INSERT OR IGNORE INTO sessions(id) VALUES(?)", (session_id,))
            # Every operation charges a durable budget. Acquire the writer reservation
            # before reading its snapshot so parallel readers cannot deadlock while
            # upgrading their deferred transactions to writers.
            # Discovery prepares untrusted files before its first write, keeping
            # the writer available for the whole preparation batch.
            if operation != "discover":
                self.db.execute("BEGIN IMMEDIATE")
            self.db.execute("SAVEPOINT request")
            spent = self.db.execute(
                "SELECT spent FROM sessions WHERE id=?", (session_id,)
            ).fetchone()[0]
            if spent >= self.limits.period:
                raise Failure("budget_exceeded", "session_period_exhausted")
            response = handlers[operation](**arguments)
            response = {**response, "token_estimate": "utf8_bytes_conservative"}
            amount = cost(response)
            maximum = (
                self.limits.cards
                if operation == "search"
                else self.limits.load
                if operation in {"load", "resource"}
                else self.limits.period
            )
            if amount > min(maximum, self.limits.period - spent):
                raise Failure("budget_exceeded", "response_does_not_fit")
            if amount > self.limits.output_bytes:
                raise Failure("limit_exceeded", "response_too_large")
            if operation in {"load", "resource"}:
                ref = response["reference"]
                emitted_ids = {ref["capability_id"]}
                emitted_ids.update(
                    item["reference"]["capability_id"] for item in response.get("dependencies", [])
                )
                self.db.execute(
                    "INSERT INTO content_charges VALUES(?,?,?) "
                    "ON CONFLICT DO UPDATE SET spent=spent+excluded.spent",
                    (session_id, serialized(sorted(emitted_ids)), amount),
                )
                groups = self.db.execute(
                    "SELECT * FROM load_groups WHERE session_id=?", (session_id,)
                ).fetchall()
                for group in groups:
                    if emitted_ids.intersection(json.loads(group["members"])):
                        if group["spent"] + amount > self.limits.load:
                            raise Failure("budget_exceeded", "dependency_group_budget")
                        self.db.execute(
                            "UPDATE load_groups SET spent=spent+? "
                            "WHERE session_id=? AND root_id=? AND root_revision=?",
                            (amount, session_id, group["root_id"], group["root_revision"]),
                        )
                row = self.db.execute(
                    "SELECT spent FROM emissions "
                    "WHERE session_id=? AND capability_id=? AND source_revision=?",
                    (session_id, ref["capability_id"], ref["source_revision"]),
                ).fetchone()
                if amount + (row[0] if row else 0) > self.limits.load:
                    raise Failure("budget_exceeded", "cumulative_plan_budget")
                self.db.execute(
                    "INSERT INTO emissions VALUES(?,?,?,?) "
                    "ON CONFLICT DO UPDATE SET spent=spent+excluded.spent",
                    (session_id, ref["capability_id"], ref["source_revision"], amount),
                )
            self.check()
            updated = self.db.execute(
                "UPDATE sessions SET spent=spent+? WHERE id=? AND spent+?<=?",
                (amount, session_id, amount, self.limits.period),
            )
            if updated.rowcount != 1:
                raise Failure("budget_exceeded", "concurrent_budget_exhausted")
            self.db.execute("RELEASE request")
            self.db.commit()
            return response
        except (Failure, sqlite3.Error, OSError, ValueError, TypeError, KeyError) as error:
            self.db.rollback()
            if isinstance(error, Failure):
                result = {"status": error.status, "reason": error.reason}
            else:
                result = {
                    "status": "catalog_unavailable"
                    if isinstance(error, (OSError, sqlite3.Error))
                    else "policy_denied",
                    "reason": "operation_failed",
                }
            # Refusal envelopes are measured but cannot themselves enforce a hard prompt cap.
            try:
                with self.db:
                    self.db.execute(
                        "UPDATE sessions SET spent=spent+? WHERE id=?", (cost(result), session_id)
                    )
            except sqlite3.Error:
                pass
            return result

    def _cap(self, capability_id):
        row = self.db.execute("SELECT * FROM capabilities WHERE id=?", (capability_id,)).fetchone()
        if not row:
            raise Failure("stale_reference", "unknown_capability")
        return row

    @staticmethod
    def _entry(folder):
        for name in ("SKILL.md", "agent.md", "agent.toml", "workflow.json"):
            if (folder / name).is_file():
                return name
        raise Failure("unsupported_integration", "unknown_definition_format")

    def _discover(self, cursor: int = 0):
        if type(cursor) is not int or cursor < 0:
            raise Failure("policy_denied", "invalid_cursor")
        directories = sorted(path for path in self.root.iterdir() if path.is_dir())
        if len(directories) > self.limits.inventory_skills:
            raise Failure("limit_exceeded", "inventory_skill_count")
        if cursor > len(directories):
            raise Failure("policy_denied", "invalid_cursor")
        fingerprint = digest(
            serialized([os.path.normcase(folder.name) for folder in directories]).encode()
        )
        fingerprint_key = "discovery_listing_fingerprint"
        cursor_key = "discovery_next_cursor"
        if cursor:
            prior = self.db.execute(
                "SELECT value FROM config WHERE key=?", (fingerprint_key,)
            ).fetchone()
            expected = self.db.execute(
                "SELECT value FROM config WHERE key=?", (cursor_key,)
            ).fetchone()
            if not prior or prior[0] != fingerprint:
                raise Failure("stale_reference", "discovery_source_changed")
            if not expected or expected[0] != str(cursor):
                raise Failure("policy_denied", "invalid_cursor")
        from .capabilities import parse_definition, validate_manifest

        prepared, next_cursor = [], None
        for index in range(cursor, len(directories)):
            self.check()
            if len(prepared) >= self.limits.batch:
                next_cursor = index
                break
            folder = safe_path(directories[index])
            identity = digest(f"{os.path.normcase(str(self.root))}/{folder.name}".encode())[:24]
            try:
                entry = self._entry(folder)
                kind = (
                    "skill"
                    if entry == "SKILL.md"
                    else "workflow"
                    if entry == "workflow.json"
                    else "subagent"
                )
                parsed = parse_definition(folder / entry, kind)
                override_file = safe_path(folder / "capability.json")
                override = {}
                if override_file.exists():
                    with override_file.open("rb") as stream:
                        raw = stream.read(self.limits.candidate_bytes + 1)
                    if len(raw) > self.limits.candidate_bytes:
                        raise Failure("limit_exceeded", "operator_manifest_too_large")
                    override = json.loads(raw)
                manifest = validate_manifest({**parsed, **override})
                _, hashes, revision = read_package(folder, self.limits, entry)
                prepared.append((identity, folder.name, manifest, entry, hashes, revision, None))
            except (Failure, ValueError) as error:
                reason = error.reason if isinstance(error, Failure) else "invalid_manifest_json"
                prepared.append((identity, folder.name, None, None, None, None, reason))

        current_directories = sorted(path for path in self.root.iterdir() if path.is_dir())
        current_fingerprint = digest(
            serialized([os.path.normcase(folder.name) for folder in current_directories]).encode()
        )
        if current_fingerprint != fingerprint:
            raise Failure("stale_reference", "discovery_source_changed")

        processed = []
        for identity, name, manifest, entry, hashes, revision, reason in prepared:
            if reason is None:
                old = self.db.execute(
                    "SELECT source_revision,state FROM capabilities WHERE id=?", (identity,)
                ).fetchone()
                state = old["state"] if old and old["source_revision"] == revision else "discovered"
                self.db.execute(
                    "INSERT INTO "
                    "capabilities(id,name,kind,scope,source_revision,state,manifest,entry_file) "
                    "VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                    "kind=excluded.kind,source_revision=excluded.source_revision,state=excluded."
                    "state,manifest=excluded.manifest,entry_file=excluded.entry_file,reason=''",
                    (
                        identity,
                        name,
                        manifest["kind"],
                        self.scope,
                        revision,
                        state,
                        serialized(manifest),
                        entry,
                    ),
                )
                self.db.execute(
                    "INSERT OR IGNORE INTO source_revisions VALUES(?,?,?,?,?)",
                    (identity, revision, serialized(hashes), entry, serialized(manifest)),
                )
            else:
                self.db.execute(
                    "INSERT INTO "
                    "capabilities(id,name,kind,scope,state,reason,manifest,entry_file) "
                    "VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                    "state='blocked',reason=excluded.reason",
                    (identity, name, "skill", self.scope, "blocked", reason, "{}", "SKILL.md"),
                )
            processed.append(identity)
        if next_cursor is not None:
            self.db.execute(
                "INSERT INTO config VALUES(?,?) ON CONFLICT(key) DO UPDATE SET "
                "value=excluded.value",
                (fingerprint_key, fingerprint),
            )
            self.db.execute(
                "INSERT INTO config VALUES(?,?) ON CONFLICT(key) DO UPDATE SET "
                "value=excluded.value",
                (cursor_key, str(next_cursor)),
            )
        if next_cursor is None:
            self.db.execute("DELETE FROM config WHERE key IN (?,?)", (fingerprint_key, cursor_key))
            listed = [
                digest(f"{os.path.normcase(str(self.root))}/{folder.name}".encode())[:24]
                for folder in current_directories
            ]
            if listed:
                placeholders = ",".join("?" for _ in listed)
                self.db.execute(
                    "UPDATE capabilities SET state='removed',reason='source_removed' "
                    f"WHERE scope=? AND id NOT IN ({placeholders})",
                    (self.scope, *listed),
                )
            else:
                self.db.execute(
                    "UPDATE capabilities SET state='removed',reason='source_removed' WHERE scope=?",
                    (self.scope,),
                )
        items = [dict(self._cap(identity)) for identity in processed]
        for item in items:
            item.pop("manifest")
        return {
            "status": "ok",
            "items": items[:20],
            "processed": len(items),
            "next_cursor": next_cursor,
        }

    def _inventory(self, offset: int = 0, limit: int = 20, kinds: list[str] | None = None):
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
            raise Failure("policy_denied", "invalid_page")
        if kinds is not None and (
            not isinstance(kinds, list)
            or any(k not in {"skill", "subagent", "workflow", "command"} for k in kinds)
        ):
            raise Failure("policy_denied", "invalid_kind_filter")
        clause = " WHERE kind IN (" + ",".join("?" for _ in kinds) + ")" if kinds else ""
        params = kinds or []
        rows = self.db.execute(
            "SELECT id,name,kind,source_revision,state,status,reason FROM capabilities"
            + clause
            + " ORDER BY id LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()
        return {
            "status": "ok",
            "items": [dict(row) for row in rows],
            "total": self.db.execute(
                "SELECT count(*) FROM capabilities" + clause, params
            ).fetchone()[0],
        }

    def _files(self, identity, revision, snapshot=False):
        cap = self._cap(identity)
        record = self.db.execute(
            "SELECT * FROM source_revisions WHERE capability_id=? AND revision=?",
            (identity, revision),
        ).fetchone()
        if not record:
            raise Failure("stale_reference", "source_revision_missing")
        directory = (
            self.state / "packages" / identity / revision if snapshot else self.root / cap["name"]
        )
        try:
            files, hashes, current = read_package(directory, self.limits, record["entry_file"])
        except (OSError, Failure) as error:
            raise Failure("stale_reference", "package_missing_or_changed") from error
        if current != revision or serialized(hashes) != record["files"]:
            raise Failure("stale_reference", "package_changed")
        return files

    def _inspect(
        self,
        capability_id: str,
        source_revision: str,
        file: str | None = None,
        expected_kind: str | None = None,
    ):
        cap = self._cap(capability_id)
        if expected_kind and cap["kind"] != expected_kind:
            raise Failure("policy_denied", "capability_kind_mismatch")
        file = file or cap["entry_file"]
        relative_file(self.root / cap["name"], file)
        files = self._files(capability_id, source_revision)
        if file not in files:
            raise Failure("stale_reference", "resource_missing")
        return {
            "status": "ok",
            "content": files[file].decode("utf-8"),
            "source_is_untrusted_data": True,
        }

    def _submit(
        self,
        capability_id: str,
        source_revision: str,
        candidate: dict,
        expected_kind: str | None = None,
    ):
        cap = self._cap(capability_id)
        if expected_kind and cap["kind"] != expected_kind:
            raise Failure("policy_denied", "capability_kind_mismatch")
        if cap["source_revision"] != source_revision:
            raise Failure("stale_reference", "not_current_source")
        rule = "rules-v1"
        row = self.db.execute(
            "SELECT count FROM attempts WHERE capability_id=? AND source_revision=? AND rules=?",
            (capability_id, source_revision, rule),
        ).fetchone()
        count = row[0] if row else 0
        if count >= 2:
            raise Failure("limit_exceeded", "two_attempts_exhausted")
        files = self._files(capability_id, source_revision)
        self.db.execute(
            "INSERT INTO attempts VALUES(?,?,?,1) ON CONFLICT DO UPDATE SET count=count+1",
            (capability_id, source_revision, rule),
        )
        try:
            metadata = validate_candidate(candidate, files, self.limits)
        except Failure as error:
            if cap["state"] not in {"validated", "published"}:
                self.db.execute(
                    "UPDATE capabilities SET state='needs_revision',reason=? WHERE id=?",
                    (error.reason, capability_id),
                )
            return {"status": error.status, "reason": error.reason, "attempts": count + 1}
        revision = digest(serialized([capability_id, source_revision, metadata, rule]).encode())
        self.db.execute(
            "INSERT OR IGNORE INTO descriptor_revisions VALUES(?,?,?,?,?)",
            (revision, capability_id, source_revision, serialized(metadata), rule),
        )
        self.db.execute(
            "UPDATE capabilities SET state='validated',reason='' WHERE id=?", (capability_id,)
        )
        return {
            "status": "ok",
            "descriptor_revision": revision,
            "attempts": count + 1,
            "quality": 100,
            "semantic_certification": False,
        }

    def _snapshot(self, identity, revision):
        destination = safe_path(self.state / "packages" / identity / revision)
        if destination.exists():
            self._files(identity, revision, True)
            return
        files = self._files(identity, revision)
        # Windows senza percorsi lunghi (MAX_PATH 260): meglio un errore esplicito che un
        # stale_reference a metà pubblicazione, difficile da ricondurre alla causa.
        staged = self.state / "staging" / ("0" * 32)
        if os.name == "nt" and any(
            len(str(destination / name)) >= 259 or len(str(staged / name)) >= 259 for name in files
        ):
            raise Failure("policy_denied", "state_path_too_long")
        staging = safe_path(self.state / "staging" / uuid.uuid4().hex)
        staging.mkdir(parents=True)
        try:
            for name, data in files.items():
                self.check()
                target = relative_file(staging, name)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            destination.parent.mkdir(parents=True, exist_ok=True)
            replace_snapshot(staging, destination)
            self._files(identity, revision, True)
        finally:
            if staging.exists():
                shutil.rmtree(staging)

    def _publication_selection(self):
        active = self.active()
        selection = {
            row["capability_id"]: dict(row)
            for row in self.db.execute("SELECT * FROM entries WHERE catalog_id=?", (active,))
        }
        for cap in self.db.execute("SELECT * FROM capabilities ORDER BY id").fetchall():
            self.check()
            if cap["status"] != "enabled":
                selection.pop(cap["id"], None)
                continue
            if cap["state"] in {"validated", "published"}:
                descriptor = self.db.execute(
                    "SELECT id FROM descriptor_revisions "
                    "WHERE capability_id=? AND source_revision=? "
                    "ORDER BY rowid DESC LIMIT 1",
                    (cap["id"], cap["source_revision"]),
                ).fetchone()
                if descriptor:
                    selection[cap["id"]] = {
                        "capability_id": cap["id"],
                        "source_revision": cap["source_revision"],
                        "descriptor_revision": descriptor[0],
                        "manifest": cap["manifest"],
                    }
        return selection

    @staticmethod
    def _publication_signature(selection):
        return digest(
            serialized(
                [
                    [key, item["source_revision"], item["descriptor_revision"], item["manifest"]]
                    for key, item in sorted(selection.items())
                ]
            ).encode()
        )

    def _publication_stage_current(self, stage, desired):
        selection = stage.get("selection")
        pending = stage.get("pending")
        if not isinstance(selection, dict) or not isinstance(pending, list):
            return False
        if selection != desired:
            return False
        seen = set()
        for item in pending:
            if (
                not isinstance(item, list)
                or len(item) != 2
                or not isinstance(item[0], str)
                or not isinstance(item[1], str)
                or item[0] in seen
                or item[0] not in selection
                or selection[item[0]]["source_revision"] != item[1]
            ):
                return False
            seen.add(item[0])
        return stage.get("fingerprint") == self._publication_signature(desired)

    def _snapshot_exists(self, identity, revision):
        return safe_path(self.state / "packages" / identity / revision).is_dir()

    def _publish(self, batch_size: int | None = None, deadline_ms: int | None = None):
        if batch_size is None:
            batch_size = self.limits.batch
        if type(batch_size) is not int or not 1 <= batch_size <= min(self.limits.batch, 100):
            raise Failure("policy_denied", "invalid_publish_batch_size")
        if deadline_ms is not None:
            if type(deadline_ms) is not int or not 1 <= deadline_ms <= 30_000:
                raise Failure("policy_denied", "invalid_publish_deadline")
            self.deadline = min(self.deadline, time.monotonic() + deadline_ms / 1000)

        stage_row = self.db.execute(
            "SELECT value FROM config WHERE key='publication_stage'"
        ).fetchone()
        stage = json.loads(stage_row[0]) if stage_row else None
        desired = self._publication_selection() if stage is not None else None
        if stage is not None and not self._publication_stage_current(stage, desired):
            self.db.execute("DELETE FROM config WHERE key='publication_stage'")
            stage = None

        active = self.active()
        if stage is None:
            selection = desired if desired is not None else self._publication_selection()
            signature = self._publication_signature(selection)
            existing = self.db.execute(
                "SELECT id FROM catalogs WHERE signature=?", (signature,)
            ).fetchone()
            if existing and existing[0] == active:
                if not self.graph.signature(active):
                    if not self.graph.build(
                        active, self._files, batch_size=min(self.limits.batch, 100)
                    ):
                        return {
                            "status": "in_progress",
                            "active_catalog": active,
                            "published": len(selection),
                            "remaining": 0,
                            "graph_building": True,
                        }
                return {
                    "status": "ok",
                    "catalog_id": active,
                    "published": len(selection),
                    "changed": False,
                }
            pending = [
                [identity, entry["source_revision"]]
                for identity, entry in sorted(selection.items())
                if not self._snapshot_exists(identity, entry["source_revision"])
            ]
            stage = {
                "fingerprint": signature,
                "selection": selection,
                "pending": pending,
                "cursor": uuid.uuid4().hex,
            }

        pending = stage["pending"]
        completed = 0
        for identity, revision in pending[:batch_size]:
            self.check()
            if not self._snapshot_exists(identity, revision):
                self._snapshot(identity, revision)
            completed += 1
        if completed:
            stage["pending"] = pending[completed:]
        if stage["pending"]:
            self.db.execute(
                "INSERT INTO config VALUES('publication_stage',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (serialized(stage),),
            )
            return {
                "status": "in_progress",
                "cursor": stage["cursor"],
                "remaining": len(stage["pending"]),
                "active_catalog": active,
                "published": len(stage["selection"]),
            }

        selection = stage["selection"]
        signature = stage["fingerprint"]
        existing = self.db.execute(
            "SELECT id FROM catalogs WHERE signature=?", (signature,)
        ).fetchone()
        if existing:
            catalog = existing[0]
        else:
            catalog = self.db.execute(
                "INSERT INTO catalogs(signature) VALUES(?)", (signature,)
            ).lastrowid
            self.db.executemany(
                "INSERT INTO entries VALUES(?,?,?,?,?)",
                [
                    (
                        catalog,
                        key,
                        row["source_revision"],
                        row["descriptor_revision"],
                        row["manifest"],
                    )
                    for key, row in selection.items()
                ],
            )
        if not self.graph.signature(catalog):
            if not self.graph.build(catalog, self._files, batch_size=min(self.limits.batch, 100)):
                self.db.execute(
                    "INSERT INTO config VALUES('publication_stage',?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (serialized(stage),),
                )
                return {
                    "status": "in_progress",
                    "active_catalog": active,
                    "published": len(selection),
                    "remaining": 0,
                    "cursor": stage["cursor"],
                    "graph_building": True,
                }
        self._activate(catalog)
        self.db.execute("DELETE FROM config WHERE key='publication_stage'")
        return {
            "status": "ok",
            "catalog_id": catalog,
            "published": len(selection),
            "changed": catalog != active,
        }

    def _activate(self, catalog):
        if not self.graph.signature(catalog):
            self.graph.build(catalog, self._files)
        self.db.execute("DELETE FROM capability_fts")
        for row in self.db.execute(
            "SELECT e.*,d.metadata FROM entries e JOIN descriptor_revisions d ON "
            "d.id=e.descriptor_revision WHERE catalog_id=?",
            (catalog,),
        ).fetchall():
            metadata = json.loads(row["metadata"])
            positive = [
                metadata["description"],
                " ".join(
                    " ".join(cap[k] for k in ("action", "object", "scope"))
                    for cap in metadata["capabilities"]
                ),
                " ".join(metadata["use_when"]),
                " ".join(metadata["aliases"]),
                " ".join(metadata["technologies"]),
            ]
            self.db.execute(
                "INSERT INTO capability_fts VALUES(?,?,?,?,?,?)",
                (row["capability_id"], *(" ".join(terms(v)) for v in positive)),
            )
            if json.loads(row["manifest"])["kind"] == "skill":
                self.db.execute(
                    "INSERT OR REPLACE INTO legacy_refs VALUES(?,?,?,?)",
                    (
                        catalog,
                        row["capability_id"],
                        row["source_revision"],
                        serialized(self._ref(row)),
                    ),
                )
            self.db.execute(
                "INSERT OR IGNORE INTO admissions VALUES(?,?,?,?)",
                (
                    row["capability_id"],
                    time.time(),
                    self._counter_total("search_responses_committed"),
                    self._counter_total("retrieved", row["capability_id"]),
                ),
            )
        self.db.execute("UPDATE config SET value=? WHERE key='active'", (str(catalog),))

    def _ref(self, row):
        return {
            "schema_version": 2,
            "capability_id": row["capability_id"],
            "source_revision": row["source_revision"],
            "descriptor_revision": row["descriptor_revision"],
            "catalog_id": row["catalog_id"],
            "scope_id": self.scope,
        }

    def _entry_ref(self, reference):
        if isinstance(reference, dict) and set(reference) == {"skill_id", "version", "catalog"}:
            mapped = self.db.execute(
                "SELECT reference FROM legacy_refs WHERE catalog=? AND skill_id=? AND version=?",
                (reference["catalog"], reference["skill_id"], reference["version"]),
            ).fetchone()
            if not mapped:
                raise Failure("stale_reference", "legacy_reference_missing")
            reference = json.loads(mapped[0])
        if (
            not isinstance(reference, dict)
            or reference.get("schema_version") != 2
            or reference.get("scope_id") != self.scope
        ):
            raise Failure("stale_reference", "invalid_reference_scope")
        row = self.db.execute(
            "SELECT * FROM entries WHERE catalog_id=? AND capability_id=? "
            "AND source_revision=? AND descriptor_revision=?",
            (
                reference["catalog_id"],
                reference["capability_id"],
                reference["source_revision"],
                reference["descriptor_revision"],
            ),
        ).fetchone()
        if not row:
            raise Failure("stale_reference", "reference_not_published")
        if self._cap(row["capability_id"])["status"] != "enabled":
            raise Failure("policy_denied", "capability_not_enabled")
        return row, self._ref(row)

    def _closure(self, identity, catalog=None, visiting=None):
        catalog = catalog or self.active()
        visiting = visiting or set()
        ordered, walk = [], set()

        def visit(current):
            self.check()
            if current in visiting:
                raise Failure("dependency_unavailable", "required_cycle")
            if current in walk:
                return
            if len(walk) >= 32:
                raise Failure("dependency_unavailable", "dependency_limit_32")
            row = self.db.execute(
                "SELECT * FROM entries WHERE catalog_id=? AND capability_id=?", (catalog, current)
            ).fetchone()
            if not row or self._cap(current)["status"] != "enabled":
                raise Failure("dependency_unavailable", "required_capability_unavailable")
            walk.add(current)
            visiting.add(current)
            manifest = json.loads(row["manifest"])
            dependencies = manifest.get("requires", []) + manifest.get("preloads", [])
            if manifest["kind"] == "workflow":
                dependencies += [step["capability"] for step in manifest.get("steps", [])]
            for dependency in dependencies:
                visit(dependency)
            visiting.remove(current)
            ordered.append(row)

        visit(identity)
        identities = {row["capability_id"] for row in ordered}
        for row in ordered:
            if identities.intersection(json.loads(row["manifest"]).get("conflicts_with", [])):
                raise Failure("conflict_detected", "incompatible_capabilities")
        return ordered

    def _search(
        self,
        query: str = "",
        request_id: str | None = None,
        kinds: list | None = None,
        mode: str = "discovery",
        task_revision: int = 1,
        epoch: int | None = None,
        limit: int = 5,
        candidate_scan_limit: int = 100,
        explicit_id: str | None = None,
        technologies: list[str] | None = None,
        exclude_ids: list[str] | None = None,
    ):
        if (
            not isinstance(query, str)
            or len(query) > 4096
            or mode not in {"discovery", "prepare_use"}
            or type(task_revision) is not int
            or task_revision < 1
            or type(limit) is not int
            or not 1 <= limit <= 5
        ):
            raise Failure("policy_denied", "invalid_search")
        if type(candidate_scan_limit) is not int or not 1 <= candidate_scan_limit <= 5000:
            raise Failure("policy_denied", "invalid_scan_limit")
        if kinds is not None and (
            not isinstance(kinds, list)
            or any(k not in {"skill", "subagent", "workflow", "command"} for k in kinds)
        ):
            raise Failure("policy_denied", "invalid_kind_filter")
        if technologies is not None and (
            not isinstance(technologies, list) or len(technologies) > 32
        ):
            raise Failure("policy_denied", "invalid_technologies")
        required_technologies = {technology(item) for item in technologies or []}
        if exclude_ids is not None and (
            not isinstance(exclude_ids, list)
            or len(exclude_ids) > 100
            or any(not isinstance(item, str) for item in exclude_ids)
        ):
            raise Failure("policy_denied", "invalid_exclusions")
        active = self.active()
        if not active:
            raise Failure("catalog_not_ready", "publish_required")
        epoch_now = int(time.time() // (30 * 86400))
        namespace_key = f"request_epoch:{self.session}"
        namespace = self.db.execute(
            "SELECT value FROM config WHERE key=?", (namespace_key,)
        ).fetchone()
        epoch = (int(namespace[0]) if namespace else epoch_now) if epoch is None else epoch
        if epoch != epoch_now:
            raise Failure("request_expired", "request_namespace_expired")
        if self.db.execute(
            "SELECT 1 FROM config WHERE key=?", (f"closed_requests:{self.session}:{epoch}",)
        ).fetchone():
            raise Failure("request_expired", "request_namespace_closed")
        request_id = request_id or uuid.uuid4().hex
        if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", request_id):
            raise Failure("policy_denied", "invalid_request_id")
        fingerprint = hmac.new(
            self.key,
            serialized(
                [
                    query,
                    kinds,
                    mode,
                    task_revision,
                    epoch,
                    limit,
                    candidate_scan_limit,
                    self.scope,
                    explicit_id,
                    technologies,
                    exclude_ids,
                    active,
                    vars(self.limits),
                    "rules-v1",
                    "ranking-v4-operator-policy",
                    self.retrieval_policy,
                    self.graph.signature(active),
                    "utf8_bytes_conservative",
                ]
            ).encode(),
            hashlib.sha256,
        ).hexdigest()
        prior = self.db.execute(
            "SELECT * FROM request_ledger WHERE session_id=? AND request_id=?",
            (self.session, request_id),
        ).fetchone()
        if prior:
            if prior["epoch"] != epoch_now or not prior["response"]:
                raise Failure("request_expired", "request_namespace_expired")
            if prior["fingerprint"] != fingerprint:
                raise Failure("request_conflict", "request_input_changed")
            cached = json.loads(prior["response"])
            for card in cached["candidates"]:
                for edge in card.get("retrieval", {}).get("path", []):
                    for identity in (edge["source"], edge["target"]):
                        if self._cap(identity)["status"] != "enabled":
                            raise Failure("policy_denied", "graph_path_unavailable")
                entry, _ = self._entry_ref(card["reference"])
                self._plan_rows(self._closure(entry["capability_id"], entry["catalog_id"]), mode)
            return cached
        tokens = query_terms(query)
        expression = " OR ".join(f'"{token}"' for token in tokens)
        hits = (
            self.db.execute(
                "SELECT capability_id,bm25(capability_fts,0,5,4,4,3,2) score FROM "
                "capability_fts WHERE capability_fts MATCH ? ORDER BY score,capability_id LIMIT "
                "?",
                (expression, candidate_scan_limit + 1),
            ).fetchall()
            if tokens
            else []
        )
        overflow = len(hits) > candidate_scan_limit
        hits = hits[:candidate_scan_limit]
        if explicit_id:
            hits = self.db.execute(
                "SELECT capability_id FROM entries WHERE catalog_id=? AND capability_id=?",
                (active, explicit_id),
            ).fetchall()
            overflow = False
        traces = {}
        if not explicit_id and tokens and self.retrieval_policy == "graph":
            eligibility = {}

            def eligible(identity):
                if identity not in eligibility:
                    cap = self._cap(identity)
                    entry = self.db.execute(
                        "SELECT e.manifest,d.metadata FROM entries e JOIN descriptor_revisions d "
                        "ON d.id=e.descriptor_revision WHERE e.catalog_id=? AND e.capability_id=?",
                        (active, identity),
                    ).fetchone()
                    eligibility[identity] = bool(
                        entry
                        and cap["status"] == "enabled"
                        and identity not in (exclude_ids or [])
                        and (not kinds or json.loads(entry[0])["kind"] in kinds)
                        and required_technologies.issubset(json.loads(entry[1])["technologies"])
                    )
                return eligibility[identity]

            hits, traces = self.graph.expand(active, query, hits, eligible)
        candidates, blocked, canonical = [], [], set()
        # Canonical candidates that passed every filter but did not fit the card budget.
        budget_omitted = set()
        for hit in hits:
            self.check()
            identity = hit["capability_id"]
            cap = self._cap(identity)
            if cap["status"] != "enabled":
                continue
            row = self.db.execute(
                "SELECT * FROM entries WHERE catalog_id=? AND capability_id=?", (active, identity)
            ).fetchone()
            alias_relations = {key: set() for key in ("requires", "preloads", "conflicts_with")}
            alias_seen = set()
            while row and json.loads(row["manifest"]).get("alias_of"):
                if row["capability_id"] in alias_seen:
                    row = None
                    break
                alias_seen.add(row["capability_id"])
                alias_manifest = json.loads(row["manifest"])
                for key in alias_relations:
                    alias_relations[key].update(alias_manifest.get(key, []))
                target = alias_manifest["alias_of"]
                row = self.db.execute(
                    "SELECT * FROM entries WHERE catalog_id=? AND capability_id=?", (active, target)
                ).fetchone()
                if not row:
                    break
            if not row:
                continue
            manifest = json.loads(row["manifest"])
            alias_relations_not_preserved = any(
                not values.issubset(set(manifest.get(key, [])))
                for key, values in alias_relations.items()
            )
            if row["capability_id"] in canonical:
                continue
            if row["capability_id"] in (exclude_ids or []) or (
                kinds and manifest["kind"] not in kinds
            ):
                continue
            try:
                if alias_relations_not_preserved:
                    raise Failure("unsupported_integration", "alias_relations_not_preserved")
                closure = self._closure(row["capability_id"])
                self._plan_rows(closure, mode)
            except Failure as error:
                blocked.append(
                    {"capability_id": identity, "status": error.status, "reason": error.reason}
                )
                continue
            metadata = json.loads(
                self.db.execute(
                    "SELECT metadata FROM descriptor_revisions WHERE id=?",
                    (row["descriptor_revision"],),
                ).fetchone()[0]
            )
            if not required_technologies.issubset(metadata["technologies"]):
                continue
            card = {
                "reference": self._ref(row),
                "kind": manifest["kind"],
                "description": metadata["description"],
            }
            if identity in traces:
                card["retrieval"] = traces[identity]
            tentative = {
                "status": "ok",
                "candidates": candidates + [card],
                "blocked": [],
                "blocked_count": len(blocked),
                "omitted_for_budget": len(budget_omitted),
                "request_id": request_id,
                "epoch": epoch,
                "partial": False,
                "token_estimate": "utf8_bytes_conservative",
            }
            if cost(tentative) > self.limits.cards:
                budget_omitted.add(row["capability_id"])
                continue
            canonical.add(row["capability_id"])
            candidates.append(card)
            if len(candidates) >= min(limit, self.limits.candidates):
                break
        partial = overflow and len(candidates) < limit
        status = (
            "ok"
            if candidates
            else "limit_exceeded"
            if partial
            else blocked[0]["status"]
            if blocked
            else "budget_exceeded"
            if budget_omitted
            else "no_match"
        )
        result = {
            "status": status,
            "candidates": candidates,
            "blocked": [],
            "blocked_count": len(blocked),
            "omitted_for_budget": len(budget_omitted),
            "request_id": request_id,
            "epoch": epoch,
            "partial": partial,
        }
        # Keep diagnostic details within the same card budget; never let blocked
        # candidates consume slots before a usable result. Total remains visible.
        for detail in blocked:
            tentative = {
                **result,
                "blocked": result["blocked"] + [detail],
                "token_estimate": "utf8_bytes_conservative",
            }
            if cost(tentative) > self.limits.cards:
                break
            result["blocked"].append(detail)
        now = time.time()
        self.db.execute(
            "INSERT INTO config VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (namespace_key, str(epoch)),
        )
        self.db.execute(
            "INSERT INTO "
            "request_ledger(session_id,request_id,fingerprint,response,created,epoch) "
            "VALUES(?,?,?,?,?,?)",
            (self.session, request_id, fingerprint, serialized(result), now, epoch),
        )
        self._event(request_id, "", "search_responses_committed")
        for card in candidates:
            self._event(request_id, card["reference"]["capability_id"], "retrieved")
        return result

    def _event(self, request_id, identity, name, phase="final"):
        request_key = f"{self.session}:{request_id}"
        inserted = self.db.execute(
            "INSERT OR IGNORE INTO events(at,request_id,capability_id,name,phase) "
            "VALUES(?,?,?,?,?)",
            (time.time(), request_key, identity, name, phase),
        )
        if inserted.rowcount:
            day = time.strftime("%Y-%m-%d", time.gmtime())
            self.db.execute(
                "INSERT INTO daily_counters VALUES(?,?,?,1) ON CONFLICT DO UPDATE SET "
                "value=value+1",
                (day, identity, name),
            )

    def _plan_rows(self, rows, mode="discovery"):
        if mode not in {"discovery", "prepare_use"}:
            raise Failure("policy_denied", "invalid_plan_mode")
        actions, total = [], 0
        for row in rows:
            manifest = json.loads(row["manifest"])
            kind = manifest["kind"]
            assisted = kind == "skill" and manifest.get("activation", {}).get("mode") == "load"
            action = (
                "load"
                if assisted
                else "delegate"
                if kind == "subagent"
                else "orchestrate"
                if kind == "workflow"
                else "invoke_native"
            )
            if mode == "prepare_use" and not assisted:
                raise Failure("unsupported_integration", "native_activation_not_verified")
            files = self._files(row["capability_id"], row["source_revision"], True)
            if assisted:
                entry_file = self.db.execute(
                    "SELECT entry_file FROM source_revisions WHERE capability_id=? AND revision=?",
                    (row["capability_id"], row["source_revision"]),
                ).fetchone()[0]
                supported_instructions(files[entry_file])
                for name, data in files.items():
                    try:
                        content = data.decode("utf-8")
                    except UnicodeDecodeError:
                        continue
                    total += cost(
                        {
                            "status": "ok",
                            "reference": self._ref(row),
                            "file": name,
                            "content": content,
                            "token_estimate": "utf8_bytes_conservative",
                        }
                    )
            actions.append(
                {
                    "reference": self._ref(row),
                    "action": action,
                    "target": "main" if kind == "skill" else "native_host",
                    "activation_allowed": assisted,
                    "dry_run": not assisted,
                    "binding": "assisted_body_read" if assisted else "unknown",
                    "requires": manifest.get("requires", []),
                }
            )
        if total > self.limits.load:
            raise Failure("budget_exceeded", "whole_dependency_plan_does_not_fit")
        return {
            "status": "ok",
            "actions": actions,
            "main_estimate": total,
            "delegated_cost": "unknown",
            "dry_run": any(a["dry_run"] for a in actions),
        }

    def _plan(
        self,
        reference: dict | None = None,
        mode: str = "discovery",
        references: list[dict] | None = None,
    ):
        references = references if references is not None else [reference]
        if not isinstance(references, list) or not 1 <= len(references) <= self.limits.candidates:
            raise Failure("policy_denied", "invalid_plan_references")
        selected = {}
        for item in references:
            row, _ = self._entry_ref(item)
            for entry in self._closure(row["capability_id"], row["catalog_id"]):
                previous = selected.get(entry["capability_id"])
                if previous and previous["source_revision"] != entry["source_revision"]:
                    raise Failure("conflict_detected", "incompatible_source_versions")
                selected[entry["capability_id"]] = entry
        for entry in selected.values():
            if set(selected).intersection(json.loads(entry["manifest"]).get("conflicts_with", [])):
                raise Failure("conflict_detected", "incompatible_capabilities")
        result = self._plan_rows(list(selected.values()), mode)
        plan_id = uuid.uuid4().hex
        self.db.execute(
            "INSERT INTO plans VALUES(?,?,?,?)",
            (plan_id, self.session, serialized(result), time.time()),
        )
        return {**result, "plan_id": plan_id}

    def _prepare(self, plan_id: str):
        plan = self.db.execute(
            "SELECT * FROM plans WHERE id=? AND session_id=?", (plan_id, self.session)
        ).fetchone()
        if not plan:
            raise Failure("stale_reference", "plan_missing")
        data = json.loads(plan["data"])
        for action in data["actions"]:
            self._entry_ref(action["reference"])
        return self._plan_rows(
            [self._entry_ref(a["reference"])[0] for a in data["actions"]], "prepare_use"
        )

    def _load(self, reference: dict):
        row, reference = self._entry_ref(reference)
        rows = self._closure(row["capability_id"], row["catalog_id"])
        self._plan_rows(rows, "prepare_use")
        pins = json.loads(
            self.db.execute("SELECT pins FROM sessions WHERE id=?", (self.session,)).fetchone()[0]
        )
        contents = []
        for entry in rows:
            identity, version = entry["capability_id"], entry["source_revision"]
            if identity in pins and pins[identity] != version:
                raise Failure("stale_reference", "session_pinned_to_other_version")
            pins[identity] = version
            files = self._files(identity, version, True)
            entry_file = self.db.execute(
                "SELECT entry_file FROM source_revisions WHERE capability_id=? AND revision=?",
                (identity, version),
            ).fetchone()[0]
            contents.append(
                {
                    "reference": self._ref(entry),
                    "content": files[entry_file].decode("utf-8"),
                    "resources": sorted(f for f in files if f != entry_file),
                }
            )
        self.db.execute("UPDATE sessions SET pins=? WHERE id=?", (serialized(pins), self.session))
        result = {
            "status": "ok",
            "reference": reference,
            "content": contents[-1]["content"],
            "dependencies": contents[:-1],
            "resources": contents[-1]["resources"],
            "observation": "content_emitted_not_application_confirmed",
        }
        planned = cost({**result, "token_estimate": "utf8_bytes_conservative"})
        for content in contents:
            for file in content["resources"]:
                package = self._files(
                    content["reference"]["capability_id"],
                    content["reference"]["source_revision"],
                    True,
                )
                try:
                    payload = package[file].decode("utf-8")
                except UnicodeDecodeError:
                    continue
                planned += cost(
                    {
                        "status": "ok",
                        "reference": content["reference"],
                        "file": file,
                        "content": payload,
                        "token_estimate": "utf8_bytes_conservative",
                    }
                )
        remaining = (
            self.limits.period
            - self.db.execute("SELECT spent FROM sessions WHERE id=?", (self.session,)).fetchone()[
                0
            ]
        )
        if planned > min(self.limits.load, remaining):
            raise Failure("budget_exceeded", "whole_load_group_does_not_fit")
        member_ids = {item["capability_id"] for item in rows}
        historical = sum(
            charge["spent"]
            for charge in self.db.execute(
                "SELECT members,spent FROM content_charges WHERE session_id=?", (self.session,)
            )
            if member_ids.intersection(json.loads(charge["members"]))
        )
        members = serialized(sorted(member_ids))
        self.db.execute(
            "INSERT OR IGNORE INTO load_groups "
            "(session_id,root_id,root_revision,members,spent) VALUES(?,?,?,?,?)",
            (self.session, row["capability_id"], row["source_revision"], members, historical),
        )
        return result

    def _resource(self, reference: dict, file: str):
        relative_file(self.state, file)
        row, reference = self._entry_ref(reference)
        pin = json.loads(
            self.db.execute("SELECT pins FROM sessions WHERE id=?", (self.session,)).fetchone()[0]
        )
        if pin.get(row["capability_id"]) != row["source_revision"]:
            raise Failure("policy_denied", "load_required_before_resource")
        files = self._files(row["capability_id"], row["source_revision"], True)
        if file not in files:
            raise Failure("stale_reference", "resource_missing")
        return {
            "status": "ok",
            "reference": reference,
            "file": file,
            "content": files[file].decode("utf-8"),
        }

    def _set_status(self, capability_id: str, status: str):
        if status not in {"enabled", "disabled", "archived", "quarantined"}:
            raise Failure("policy_denied", "invalid_capability_status")
        cap = self._cap(capability_id)
        if cap["status"] == "quarantined" and status != "quarantined":
            raise Failure("policy_denied", "quarantine_requires_separate_review")
        self.db.execute("UPDATE capabilities SET status=? WHERE id=?", (status, capability_id))
        return {"status": "ok", "capability_id": capability_id, "capability_status": status}

    def _rollback(self, catalog_id: int):
        if not self.db.execute("SELECT 1 FROM catalogs WHERE id=?", (catalog_id,)).fetchone():
            raise Failure("stale_reference", "catalog_missing")
        for row in self.db.execute("SELECT * FROM entries WHERE catalog_id=?", (catalog_id,)):
            if self._cap(row["capability_id"])["status"] == "enabled":
                self._files(row["capability_id"], row["source_revision"], True)
        self._activate(catalog_id)
        return {"status": "ok", "catalog_id": catalog_id, "retirements_preserved": True}

    def _counter_total(self, name, identity=None):
        clause = " AND capability_id=?" if identity is not None else ""
        return self.db.execute(
            "SELECT COALESCE(SUM(value),0) FROM daily_counters WHERE name=?" + clause,
            (name, identity) if identity is not None else (name,),
        ).fetchone()[0]

    def _admission_metrics(self, identity):
        admission = self.db.execute(
            "SELECT * FROM admissions WHERE capability_id=?", (identity,)
        ).fetchone()
        return {
            "admitted_at": admission["at"] if admission else None,
            "search_responses_since_admission": (
                self._counter_total("search_responses_committed") - admission["search_baseline"]
                if admission
                else 0
            ),
            "retrieved": (
                self._counter_total("retrieved", identity) - admission["retrieval_baseline"]
                if admission
                else 0
            ),
        }

    def _export(self, offset: int = 0, limit: int = 10, kind=None, status=None):
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 20:
            raise Failure("policy_denied", "invalid_page")
        if kind not in {None, "skill", "subagent", "workflow", "command"} or status not in {
            None,
            "enabled",
            "disabled",
            "archived",
            "quarantined",
        }:
            raise Failure("policy_denied", "invalid_export_filter")
        clauses, parameters = [], []
        for key, value in (("kind", kind), ("status", status)):
            if value is not None:
                clauses.append(key + "=?")
                parameters.append(value)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        rows = self.db.execute(
            "SELECT id,kind,state,status FROM capabilities"
            + where
            + " ORDER BY id LIMIT ? OFFSET ?",
            [*parameters, limit, offset],
        ).fetchall()
        items = []
        for row in rows:
            windows = {}
            for days in (7, 30, 90):
                cutoff = time.strftime("%Y-%m-%d", time.gmtime(time.time() - (days - 1) * 86400))
                windows[str(days)] = {
                    "retrieved": self.db.execute(
                        "SELECT COALESCE(SUM(value),0) FROM daily_counters "
                        "WHERE capability_id=? AND name='retrieved' AND day>=?",
                        (row["id"], cutoff),
                    ).fetchone()[0],
                    "server_search_responses": self.db.execute(
                        "SELECT COALESCE(SUM(value),0) FROM daily_counters "
                        "WHERE name='search_responses_committed' AND day>=?",
                        (cutoff,),
                    ).fetchone()[0],
                }
            items.append(
                {
                    "capability_id": row["id"],
                    "kind": row["kind"],
                    "state": row["state"],
                    "status": row["status"],
                    **self._admission_metrics(row["id"]),
                    "windows_days": windows,
                    "findings": [
                        item[0]
                        for item in self.db.execute(
                            "SELECT reason FROM findings WHERE capability_id=? ORDER BY reason",
                            (row["id"],),
                        )
                    ],
                }
            )
        total = self.db.execute("SELECT count(*) FROM capabilities" + where, parameters).fetchone()[
            0
        ]
        return {
            "status": "ok",
            "items": items,
            "next_offset": offset + len(items) if offset + len(items) < total else None,
            "denominator_semantics": "server_searches_not_observed_or_eligible_exposure",
            "delivery": "unknown",
            "utility_assessment": "not_inferred_from_counts",
        }

    def _detail_bytes(self):
        total = 0
        for table, columns in (
            ("request_ledger", ("session_id", "request_id", "fingerprint", "response", "delivery")),
            ("events", ("request_id", "capability_id", "name", "phase")),
            ("plans", ("id", "session_id", "data")),
        ):
            size = "+".join(f"length(CAST({column} AS BLOB))" for column in columns)
            total += self.db.execute(f"SELECT COALESCE(SUM({size}+64),0) FROM {table}").fetchone()[
                0
            ]
        return total

    def _reconcile(self, cursor: int = 0):
        run_id = uuid.uuid4().hex
        discovered = self._discover(cursor)
        self.db.execute(
            "INSERT INTO maintenance_runs VALUES(?,?,?)", (run_id, time.time(), "started")
        )
        self.db.execute("DELETE FROM findings")
        duplicates = {}
        for cap in self.db.execute("SELECT * FROM capabilities").fetchall():
            self.check()
            reasons = set()
            if cap["state"] in {"blocked", "needs_revision", "removed"}:
                reasons.add(cap["reason"])
            manifest = json.loads(cap["manifest"])
            target = manifest.get("alias_of")
            if (
                target
                and not self.db.execute(
                    "SELECT 1 FROM capabilities WHERE id=? AND status='enabled'", (target,)
                ).fetchone()
            ):
                reasons.add("unresolved_alias")
            metrics = self._admission_metrics(cap["id"])
            if metrics["search_responses_since_admission"] >= 20 and metrics["retrieved"] == 0:
                reasons.add("low_retrieval_review_only")
            latest = self.db.execute(
                "SELECT metadata FROM descriptor_revisions WHERE capability_id=? "
                "ORDER BY rowid DESC LIMIT 1",
                (cap["id"],),
            ).fetchone()
            if latest:
                duplicates.setdefault(latest[0], []).append(cap["id"])
            for reason in reasons:
                self.db.execute("INSERT OR IGNORE INTO findings VALUES(?,?)", (cap["id"], reason))
        for identities in duplicates.values():
            if len(identities) > 1:
                self.db.executemany(
                    "INSERT OR IGNORE INTO findings VALUES(?, 'descriptor_overlap_review_only')",
                    [(identity,) for identity in identities],
                )
        result = self._publish()
        cutoff = time.time() - 30 * 86400
        self.db.execute("DELETE FROM events WHERE at<?", (cutoff,))
        for old in self.db.execute(
            "SELECT DISTINCT session_id,epoch FROM request_ledger WHERE created<?", (cutoff,)
        ).fetchall():
            self._expire_namespace(old["session_id"], old["epoch"])
        self.db.execute("DELETE FROM plans WHERE created<?", (cutoff,))
        # Bound all detailed records together; aggregates contain no query or payload.
        while self._detail_bytes() > DETAIL_LIMIT:
            self.check()
            self.db.execute(
                "DELETE FROM events WHERE id IN (SELECT id FROM events ORDER BY at LIMIT 1000)"
            )
            old = self.db.execute(
                "SELECT session_id,epoch FROM request_ledger ORDER BY created LIMIT 1"
            ).fetchone()
            if old:
                self._expire_namespace(old["session_id"], old["epoch"])
            self.db.execute(
                "DELETE FROM plans WHERE id IN (SELECT id FROM plans ORDER BY created LIMIT 1000)"
            )
        self.db.execute("UPDATE maintenance_runs SET status='completed' WHERE id=?", (run_id,))
        return {**result, "automatic_inference": False, "next_cursor": discovered["next_cursor"]}

    def _expire_namespace(self, session_id, epoch):
        self.db.execute(
            "INSERT OR IGNORE INTO config VALUES(?,?)",
            (f"closed_requests:{session_id}:{epoch}", "1"),
        )
        self.db.execute(
            "DELETE FROM request_ledger WHERE session_id=? AND epoch=?", (session_id, epoch)
        )

    def _delivery(self, request_id: str, evidence: dict | None = None):
        # Model-supplied JSON cannot authenticate a runtime receipt.
        return {
            "status": "unsupported_integration",
            "reason": "trusted_host_receipt_channel_unavailable",
            "delivery": "delivery_unconfirmed",
        }

    def _graph(self, rebuild=False, capability_id=None, limit=20, offset=0):
        if (
            type(rebuild) is not bool
            or type(limit) is not int
            or not 1 <= limit <= 100
            or type(offset) is not int
            or offset < 0
            or (capability_id is not None and not isinstance(capability_id, str))
        ):
            raise Failure("policy_denied", "invalid_graph_request")
        active = self.active()
        if not active:
            raise Failure("catalog_not_ready", "publish_required")
        if rebuild:
            if not self.graph.build(active, self._files, batch_size=min(self.limits.batch, 100)):
                return {
                    "status": "in_progress",
                    "graph_building": True,
                    **self.graph.inspect(active, capability_id, limit, offset),
                }
        return {"status": "ok", **self.graph.inspect(active, capability_id, limit, offset)}

    def _diagnostics(self):
        return {
            "status": "ok",
            "schema_version": SCHEMA_VERSION,
            "scope_id": self.scope,
            "mode": "assistance",
            "active_catalog": self.active(),
            "graph": {
                key: value
                for key, value in self.graph.inspect(self.active(), limit=1).items()
                if key not in {"nodes", "edges", "findings"}
            },
            "native_activation": "unverified",
            "retrieved_semantics": "server_committed_final_response",
            "delivery": "unconfirmed_without_host_receipt",
            "request_epoch": int(time.time() // (30 * 86400)),
            "limits": vars(self.limits),
            "maintenance": "manual_only",
            "migration_phase": (
                self.db.execute("SELECT value FROM config WHERE key='migration_phase'").fetchone()
                or ["not_required"]
            )[0],
            "session_spent_estimate": self.db.execute(
                "SELECT spent FROM sessions WHERE id=?", (self.session,)
            ).fetchone()[0],
        }
