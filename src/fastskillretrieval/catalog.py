"""Transactional local catalog, with one explicit authorized source root."""

import json
import os
import re
import shutil
import sqlite3
import time
import uuid
from pathlib import Path

from .contracts import (
    Failure,
    Limits,
    cost,
    serialized,
    technology,
    terms,
    validate_candidate,
)
from .packages import (
    digest,
    read_package,
    relative_file,
    replace_snapshot,
    safe_path,
    supported_instructions,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS config(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sources(
 id TEXT PRIMARY KEY, name TEXT NOT NULL, version TEXT, state TEXT NOT NULL,
 reason TEXT NOT NULL DEFAULT '', enabled INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS versions(
 skill_id TEXT NOT NULL, version TEXT NOT NULL, manifest TEXT NOT NULL,
 candidate TEXT, state TEXT NOT NULL DEFAULT 'discovered', attempts INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(skill_id,version));
CREATE TABLE IF NOT EXISTS catalogs(id INTEGER PRIMARY KEY, signature TEXT UNIQUE NOT NULL);
CREATE TABLE IF NOT EXISTS entries(
 catalog INTEGER NOT NULL, skill_id TEXT NOT NULL, version TEXT NOT NULL, metadata TEXT NOT NULL,
 PRIMARY KEY(catalog,skill_id));
CREATE TABLE IF NOT EXISTS sessions(
 id TEXT PRIMARY KEY, spent INTEGER NOT NULL DEFAULT 0, pins TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS emissions(
 session_id TEXT NOT NULL, skill_id TEXT NOT NULL, version TEXT NOT NULL,
 spent INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(session_id,skill_id,version));
CREATE TABLE IF NOT EXISTS counters(name TEXT PRIMARY KEY, value INTEGER NOT NULL DEFAULT 0);
CREATE VIRTUAL TABLE IF NOT EXISTS skill_fts USING fts5(
 skill_id UNINDEXED, version UNINDEXED, description, capabilities, use_when, aliases, technologies);
"""


class Catalog:
    def __init__(self, state: Path, source_root: Path, limits: Limits | None = None):
        self.limits = limits or Limits()
        self.state = safe_path(state)
        self.root = safe_path(source_root)
        if not self.root.is_dir():
            raise Failure("policy_denied", "source_root_missing")
        if self.state.is_relative_to(self.root) or self.root.is_relative_to(self.state):
            raise Failure("policy_denied", "state_and_sources_must_be_separate")
        self.state.mkdir(parents=True, exist_ok=True)
        if safe_path(self.state / "registry.sqlite3").exists():
            raise Failure("migration_required", "registry_state_requires_registry_entrypoint")
        safe_path(self.state / "catalog.sqlite3")
        self.db = sqlite3.connect(self.state / "catalog.sqlite3", timeout=1)
        self.db.row_factory = sqlite3.Row
        try:
            self.db.executescript(SCHEMA)
            identity = os.path.normcase(str(self.root))
            row = self.db.execute("SELECT value FROM config WHERE key='root'").fetchone()
            if row and row[0] != identity:
                raise Failure("policy_denied", "state_belongs_to_another_root")
            with self.db:
                self.db.execute("INSERT OR IGNORE INTO config VALUES('root',?)", (identity,))
        except BaseException:
            self.db.close()
            raise
        self.deadline = float("inf")

    def close(self):
        self.db.close()

    def check_time(self):
        if time.monotonic() > self.deadline:
            raise Failure("timeout", "operation_deadline")

    def call(self, operation: str, session_id: str = "default", **arguments) -> dict:
        """Actual tool boundary: transactions, typed failures, budgets and output limits."""
        if not isinstance(session_id, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", session_id):
            return {"status": "policy_denied", "reason": "invalid_session_id"}
        handlers = {
            "inventory": self._inventory,
            "inspect": self._inspect,
            "submit": self._submit,
            "publish": self._publish,
            "search": self._search,
            "load": self._load,
            "resource": self._resource,
            "set_enabled": self._set_enabled,
            "diagnostics": self._diagnostics,
        }
        seconds = 2 if operation == "search" else 30 if operation in {"inventory", "publish"} else 5
        self.deadline = time.monotonic() + seconds
        self.db.set_progress_handler(lambda: int(time.monotonic() > self.deadline), 1000)
        try:
            self.db.execute("BEGIN IMMEDIATE")
            self.db.execute("INSERT OR IGNORE INTO sessions(id) VALUES(?)", (session_id,))
            self.session = session_id
            if operation not in handlers:
                raise Failure("policy_denied", "unknown_operation")
            spent = self.db.execute(
                "SELECT spent FROM sessions WHERE id=?", (session_id,)
            ).fetchone()[0]
            if spent >= self.limits.period:
                raise Failure("budget_exceeded", "session_period_exhausted")
            result = handlers[operation](**arguments)
            self.check_time()
            result = self._envelope(result)
            if cost(result) > self.limits.output_bytes:
                raise Failure("limit_exceeded", "response_too_large_use_pagination")
            cap = (
                self.limits.cards
                if operation == "search"
                else self.limits.load
                if operation in {"load", "resource"}
                else self.limits.period
            )
            if operation in {"load", "resource"}:
                reference = result["reference"]
                plan = self.db.execute(
                    "SELECT spent FROM emissions WHERE session_id=? AND skill_id=? AND version=?",
                    (session_id, reference["skill_id"], reference["version"]),
                ).fetchone()
                cap -= plan[0] if plan else 0
            if cost(result) > min(cap, self.limits.period - spent):
                raise Failure("budget_exceeded", "response_does_not_fit")
            if operation in {"load", "resource"}:
                self.db.execute(
                    "INSERT INTO emissions VALUES(?,?,?,?) "
                    "ON CONFLICT(session_id,skill_id,version) "
                    "DO UPDATE SET spent=spent+excluded.spent",
                    (session_id, reference["skill_id"], reference["version"], cost(result)),
                )
            self.db.execute(
                "UPDATE sessions SET spent=spent+? WHERE id=?", (cost(result), session_id)
            )
            self.db.execute(
                "INSERT INTO counters VALUES(?,1) ON CONFLICT(name) DO UPDATE SET value=value+1",
                (f"{operation}:{result['status']}",),
            )
            self.db.commit()
            return result
        except (Failure, sqlite3.Error, OSError, ValueError, TypeError, KeyError) as error:
            self.db.rollback()
            if isinstance(error, Failure):
                result = {"status": error.status, "reason": error.reason}
            elif isinstance(error, sqlite3.Error):
                status = "timeout" if time.monotonic() > self.deadline else "catalog_unavailable"
                result = {"status": status, "reason": "database_operation_failed"}
            elif isinstance(error, OSError):
                result = {"status": "catalog_unavailable", "reason": "filesystem_operation_failed"}
            else:
                result = {"status": "policy_denied", "reason": "invalid_arguments"}
            # A refusal emits no source/metadata. Protocol overhead is still measured,
            # even when repeated refusals themselves cross the estimated ceiling.
            self.db.set_progress_handler(None, 0)
            try:
                with self.db:
                    self.db.execute("INSERT OR IGNORE INTO sessions(id) VALUES(?)", (session_id,))
                    self.db.execute(
                        "UPDATE sessions SET spent=spent+? WHERE id=?", (cost(result), session_id)
                    )
                    self.db.execute(
                        "INSERT INTO counters VALUES(?,1) "
                        "ON CONFLICT(name) DO UPDATE SET value=value+1",
                        (f"{operation}:{result['status']}",),
                    )
            except sqlite3.Error:
                pass
            return result
        finally:
            self.db.set_progress_handler(None, 0)

    @staticmethod
    def _envelope(result: dict) -> dict:
        return {**result, "token_estimate": "utf8_bytes_conservative", "rules": "rules-v1"}

    def _source(self, skill_id: str):
        row = self.db.execute("SELECT * FROM sources WHERE id=?", (skill_id,)).fetchone()
        if not row:
            raise Failure("stale_reference", "unknown_skill")
        return row

    def _version(self, skill_id: str, version: str):
        row = self.db.execute(
            "SELECT * FROM versions WHERE skill_id=? AND version=?", (skill_id, version)
        ).fetchone()
        if not row:
            raise Failure("stale_reference", "unknown_version")
        return row

    def _files(self, skill_id: str, version: str, snapshot=False):
        source = self._source(skill_id)
        root = (
            self.state / "packages" / skill_id / version if snapshot else self.root / source["name"]
        )
        try:
            files, manifest, current = read_package(root, self.limits)
        except Failure as error:
            if snapshot:
                raise Failure("stale_reference", "snapshot_missing_or_changed") from error
            raise
        record = self._version(skill_id, version)
        if current != version or serialized(manifest) != record["manifest"]:
            raise Failure("stale_reference", "package_changed")
        return files

    def _inventory(self, offset: int = 0, limit: int = 20, refresh: bool = False):
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
            raise Failure("policy_denied", "invalid_page")
        if refresh:
            # Immediate children only: the operator supplies a dedicated skill root.
            children = []
            for child in self.root.iterdir():
                self.check_time()
                if (child / "SKILL.md").exists():
                    children.append(child)
                    if len(children) > self.limits.inventory_skills:
                        raise Failure("limit_exceeded", "inventory_skill_count")
            changed = 0
            seen = set()
            for child in sorted(children):
                self.check_time()
                skill_id = digest(f"{os.path.normcase(str(self.root))}/{child.name}".encode())[:24]
                seen.add(skill_id)
                previous = self.db.execute(
                    "SELECT * FROM sources WHERE id=?", (skill_id,)
                ).fetchone()
                try:
                    files, manifest, version = read_package(child, self.limits)
                    supported_instructions(files["SKILL.md"])
                    record = self.db.execute(
                        "SELECT state FROM versions WHERE skill_id=? AND version=?",
                        (skill_id, version),
                    ).fetchone()
                    if not previous or previous["version"] != version:
                        changed += 1
                    if changed > self.limits.batch:
                        raise Failure("limit_exceeded", "preparation_batch")
                    self.db.execute(
                        "INSERT OR IGNORE INTO versions(skill_id,version,manifest) VALUES(?,?,?)",
                        (skill_id, version, serialized(manifest)),
                    )
                    status, reason = (record["state"] if record else "discovered"), ""
                except Failure as error:
                    if error.reason == "preparation_batch":
                        raise
                    version, status, reason = None, "blocked", error.reason
                self.db.execute(
                    "INSERT INTO sources(id,name,version,state,reason) VALUES(?,?,?,?,?) "
                    "ON CONFLICT(id) DO UPDATE SET version=excluded.version, "
                    "state=excluded.state,reason=excluded.reason",
                    (skill_id, child.name, version, status, reason),
                )
            for row in self.db.execute("SELECT id FROM sources").fetchall():
                if row[0] not in seen:
                    self.db.execute(
                        "UPDATE sources SET state='missing',reason='source_missing' WHERE id=?",
                        (row[0],),
                    )
        rows = self.db.execute(
            "SELECT s.*,COALESCE(v.attempts,0) attempts FROM sources s LEFT JOIN versions v "
            "ON s.id=v.skill_id AND s.version=v.version ORDER BY s.id LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        total = self.db.execute("SELECT count(*) FROM sources").fetchone()[0]
        return {
            "status": "ok",
            "items": [dict(row) for row in rows],
            "total": total,
            "next_offset": offset + len(rows) if offset + len(rows) < total else None,
        }

    def _inspect(self, skill_id: str, version: str, file: str = "SKILL.md"):
        files = self._files(skill_id, version)
        relative_file(self.root / self._source(skill_id)["name"], file)
        if file not in files:
            raise Failure("stale_reference", "resource_missing")
        try:
            content = files[file].decode("utf-8")
        except UnicodeDecodeError as error:
            raise Failure("unsupported_integration", "binary_resource") from error
        return {
            "status": "ok",
            "skill_id": skill_id,
            "version": version,
            "file": file,
            "content": content,
            "source_is_untrusted_data": True,
        }

    def _submit(self, skill_id: str, version: str, candidate: dict):
        source = self._source(skill_id)
        if version != source["version"]:
            raise Failure("stale_reference", "not_current_source")
        record = self._version(skill_id, version)
        if record["attempts"] >= 2:
            raise Failure("limit_exceeded", "two_attempts_exhausted")
        files = self._files(skill_id, version)
        self.db.execute(
            "UPDATE versions SET attempts=attempts+1 WHERE skill_id=? AND version=?",
            (skill_id, version),
        )
        try:
            metadata = validate_candidate(candidate, files, self.limits)
        except Failure as error:
            # A failed rewrite does not revoke already validated metadata for this source.
            if record["state"] not in {"validated", "published"}:
                self.db.execute(
                    "UPDATE versions SET state='needs_revision' WHERE skill_id=? AND version=?",
                    (skill_id, version),
                )
                self.db.execute(
                    "UPDATE sources SET state='needs_revision',reason=? WHERE id=?",
                    (error.reason, skill_id),
                )
            return {
                "status": error.status,
                "reason": error.reason,
                "attempts": record["attempts"] + 1,
            }
        self.db.execute(
            "UPDATE versions SET candidate=?,state='validated' WHERE skill_id=? AND version=?",
            (serialized(metadata), skill_id, version),
        )
        self.db.execute("UPDATE sources SET state='validated',reason='' WHERE id=?", (skill_id,))
        return {
            "status": "ok",
            "state": "validated",
            "quality": 100,
            "semantic_certification": False,
            "attempts": record["attempts"] + 1,
        }

    def _active(self):
        row = self.db.execute("SELECT value FROM config WHERE key='active'").fetchone()
        return int(row[0]) if row else None

    def _snapshot(self, skill_id: str, version: str):
        destination = safe_path(self.state / "packages" / skill_id / version)
        if destination.exists():
            self._files(skill_id, version, snapshot=True)
            return
        files = self._files(skill_id, version)
        temporary = safe_path(destination.parent / f".staging-{uuid.uuid4().hex}")
        temporary.mkdir(parents=True)
        try:
            for name, data in files.items():
                self.check_time()
                target = relative_file(temporary, name)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            # Verify the complete copy before making it addressable.
            if read_package(temporary, self.limits)[2] != version:
                raise Failure("stale_reference", "snapshot_copy_failed")
            replace_snapshot(temporary, destination)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)

    def _publish(self):
        active = self._active()
        previous = (
            dict(
                self.db.execute(
                    "SELECT skill_id,version FROM entries WHERE catalog=?", (active,)
                ).fetchall()
            )
            if active
            else {}
        )
        selection = {}
        for source in self.db.execute(
            "SELECT * FROM sources WHERE enabled=1 ORDER BY id"
        ).fetchall():
            self.check_time()
            skill_id = source["id"]
            if source["state"] in {"validated", "published"}:
                selection[skill_id] = source["version"]
                self._snapshot(skill_id, source["version"])
            elif skill_id in previous:
                # Only the already published immutable package may survive a bad update.
                self._files(skill_id, previous[skill_id], snapshot=True)
                selection[skill_id] = previous[skill_id]
        # Include validated metadata: a rewrite with the same package is a new catalog.
        content = [
            (sid, ver, self._version(sid, ver)["candidate"]) for sid, ver in selection.items()
        ]
        signature = digest(serialized(content).encode())
        existing = self.db.execute(
            "SELECT id FROM catalogs WHERE signature=?", (signature,)
        ).fetchone()
        if existing and existing[0] == active:
            return {
                "status": "ok",
                "catalog": active,
                "published": len(selection),
                "changed": False,
            }
        if existing:
            catalog_id = existing[0]
        else:
            catalog_id = self.db.execute(
                "INSERT INTO catalogs(signature) VALUES(?)", (signature,)
            ).lastrowid
            self.db.executemany(
                "INSERT INTO entries VALUES(?,?,?,?)",
                (
                    (catalog_id, sid, ver, self._version(sid, ver)["candidate"])
                    for sid, ver in selection.items()
                ),
            )
        self.db.execute("DELETE FROM skill_fts")
        for skill_id, version in selection.items():
            metadata = json.loads(self._version(skill_id, version)["candidate"])
            capabilities = " ".join(
                " ".join(cap[key] for key in ("action", "object", "scope"))
                for cap in metadata["capabilities"]
            )
            columns = [
                metadata["description"],
                capabilities,
                " ".join(metadata["use_when"]),
                " ".join(metadata["aliases"]),
                " ".join(metadata["technologies"]),
            ]
            self.db.execute(
                "INSERT INTO skill_fts VALUES(?,?,?,?,?,?,?)",
                (skill_id, version, *(" ".join(terms(column)) for column in columns)),
            )
            self.db.execute(
                "UPDATE versions SET state='published' WHERE skill_id=? AND version=?",
                (skill_id, version),
            )
            self.db.execute(
                "UPDATE sources SET state='published' WHERE id=? AND version=?", (skill_id, version)
            )
        self.db.execute(
            "INSERT INTO config VALUES('active',?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(catalog_id),),
        )
        return {"status": "ok", "catalog": catalog_id, "published": len(selection), "changed": True}

    def _search(
        self,
        query: str = "",
        explicit_id: str | None = None,
        technologies: list[str] | None = None,
        exclude_ids: list[str] | None = None,
        limit: int = 5,
    ):
        if (
            not isinstance(query, str)
            or len(query) > 4096
            or type(limit) is not int
            or not 1 <= limit <= 5
        ):
            raise Failure("policy_denied", "invalid_query_or_limit")
        if technologies is not None and (
            not isinstance(technologies, list) or len(technologies) > 32
        ):
            raise Failure("policy_denied", "invalid_technologies")
        if exclude_ids is not None and (
            not isinstance(exclude_ids, list) or len(exclude_ids) > 100
        ):
            raise Failure("policy_denied", "invalid_exclusions")
        required = {technology(t) for t in technologies or []}
        excluded = set(exclude_ids or [])
        active = self._active()
        if active is None:
            raise Failure("catalog_not_ready", "publish_required")
        if explicit_id:
            rows = self.db.execute(
                "SELECT skill_id,version,0 AS score FROM entries WHERE catalog=? AND skill_id=?",
                (active, explicit_id),
            ).fetchall()
        else:
            tokens = terms(query)
            expression = " OR ".join(f'"{token}"' for token in tokens)
            rows = (
                self.db.execute(
                    "SELECT skill_id,version,bm25(skill_fts,0,0,5,4,4,3,2) AS score FROM skill_fts "
                    "WHERE skill_fts MATCH ? ORDER BY score,skill_id",
                    (expression,),
                ).fetchall()
                if tokens
                else []
            )
        candidates, omitted = [], 0
        for row in rows:
            self.check_time()
            source = self._source(row["skill_id"])
            if not source["enabled"] or row["skill_id"] in excluded:
                continue
            metadata = json.loads(
                self.db.execute(
                    "SELECT metadata FROM entries WHERE catalog=? AND skill_id=?",
                    (active, row["skill_id"]),
                ).fetchone()[0]
            )
            if not required.issubset(metadata["technologies"]):
                continue
            ref = {"skill_id": row["skill_id"], "version": row["version"], "catalog": active}
            card = {"reference": ref, "description": metadata["description"]}
            tentative = self._envelope(
                {"status": "ok", "candidates": candidates + [card], "omitted_for_budget": omitted}
            )
            if cost(tentative) > self.limits.cards:
                omitted += 1
                continue
            candidates.append(card)
            if len(candidates) >= min(limit, self.limits.candidates):
                break
        status = "ok" if candidates else "budget_exceeded" if omitted else "no_match"
        return {"status": status, "candidates": candidates, "omitted_for_budget": omitted}

    def _reference(self, reference: dict):
        if not isinstance(reference, dict) or set(reference) != {"skill_id", "version", "catalog"}:
            raise Failure("stale_reference", "invalid_reference")
        skill_id, version = reference["skill_id"], reference["version"]
        source = self._source(skill_id)
        if not source["enabled"]:
            raise Failure("policy_denied", "skill_disabled")
        row = self.db.execute(
            "SELECT 1 FROM entries WHERE catalog=? AND skill_id=? AND version=?",
            (reference["catalog"], skill_id, version),
        ).fetchone()
        if not row:
            raise Failure("stale_reference", "reference_not_published")
        pins = json.loads(
            self.db.execute("SELECT pins FROM sessions WHERE id=?", (self.session,)).fetchone()[0]
        )
        if skill_id in pins and pins[skill_id] != version:
            raise Failure("stale_reference", "session_pinned_to_other_version_use_new_session")
        pins[skill_id] = version
        self.db.execute("UPDATE sessions SET pins=? WHERE id=?", (serialized(pins), self.session))
        return self._files(skill_id, version, snapshot=True)

    def _load(self, reference: dict):
        files = self._reference(reference)
        supported_instructions(files["SKILL.md"])
        result = {
            "status": "ok",
            "reference": reference,
            "content": files["SKILL.md"].decode("utf-8"),
            "resources": sorted(name for name in files if name != "SKILL.md"),
            "observation": "content_emitted_not_application_confirmed",
        }
        # Preflight all UTF-8 resources in this simple package. This intentionally
        # overestimates optional resources instead of splitting a plan to evade limits.
        planned = cost(self._envelope(result))
        for name, data in files.items():
            self.check_time()
            if name == "SKILL.md":
                continue
            try:
                content = data.decode("utf-8")
            except UnicodeDecodeError:
                continue  # Binary resources are never emitted by this adapter.
            planned += cost(
                self._envelope(
                    {
                        "status": "ok",
                        "reference": reference,
                        "file": name,
                        "content": content,
                    }
                )
            )
        if planned > self.limits.load:
            raise Failure("budget_exceeded", "whole_package_plan_does_not_fit")
        remaining = (
            self.limits.period
            - self.db.execute("SELECT spent FROM sessions WHERE id=?", (self.session,)).fetchone()[
                0
            ]
        )
        if planned > remaining:
            raise Failure("budget_exceeded", "whole_package_exceeds_session_remaining")
        return result

    def _resource(self, reference: dict, file: str):
        # Validate the logical path even if no file with that spelling is in the manifest.
        relative_file(self.state, file)
        files = self._reference(reference)
        loaded = self.db.execute(
            "SELECT 1 FROM emissions WHERE session_id=? AND skill_id=? AND version=?",
            (self.session, reference["skill_id"], reference["version"]),
        ).fetchone()
        if not loaded:
            raise Failure("policy_denied", "load_required_before_resource")
        if file not in files:
            raise Failure("stale_reference", "resource_missing")
        try:
            content = files[file].decode("utf-8")
        except UnicodeDecodeError as error:
            raise Failure("unsupported_integration", "binary_resource") from error
        return {"status": "ok", "reference": reference, "file": file, "content": content}

    def _set_enabled(self, skill_id: str, enabled: bool):
        if type(enabled) is not bool:
            raise Failure("policy_denied", "enabled_must_be_boolean")
        self._source(skill_id)
        self.db.execute("UPDATE sources SET enabled=? WHERE id=?", (int(enabled), skill_id))
        return {"status": "ok", "skill_id": skill_id, "enabled": enabled}

    def _diagnostics(self):
        return {
            "status": "ok",
            "mode": "assistance",
            "managed_catalog": "unsupported_integration",
            "native_invocation": "not_preserved_by_body_read",
            "claude_code_test": "pending",
            "active_catalog": self._active(),
            "session_spent_estimate": self.db.execute(
                "SELECT spent FROM sessions WHERE id=?", (self.session,)
            ).fetchone()[0],
            "limits": vars(self.limits),
            "counters": dict(self.db.execute("SELECT name,value FROM counters")),
            "deferred": [
                "dependency_graph",
                "catalog_rollback",
                "request_idempotency",
                "semantic_review",
                "native_catalog_control",
                "utility_experiment",
            ],
        }
