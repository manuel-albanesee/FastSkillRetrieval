"""Operator-only, crash-safe experiment queue; no provider is imported by the registry."""

import json
import math
import os
import sqlite3
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from .experiment import (
    TRIAL_STATES,
    ExperimentError,
    canonical,
    fingerprint,
    protocol_fingerprint,
    summarize,
    validate_protocol,
)


def _now():
    return datetime.now(UTC).isoformat()


def write_new(path, value):
    with Path(path).open("xb") as stream:
        stream.write(canonical(value) + b"\n")
        stream.flush()
        os.fsync(stream.fileno())


def _micros(value):
    return int((Decimal(str(value)) * 1_000_000).to_integral_value(rounding="ROUND_CEILING"))


def code_fingerprint():
    root = Path(__file__).parent
    return fingerprint(
        {p.name: fingerprint(p.read_text(encoding="utf-8")) for p in sorted(root.glob("*.py"))}
    )


def make_schedule(protocol):
    schedule = []
    for case_number, case in enumerate(protocol["cases"]):
        for repetition in range(1, protocol["repetitions"] + 1):
            arms = list(protocol["arms"])
            if (case_number + repetition) % 2 == 0:
                arms.reverse()
            for arm in arms:
                schedule.append(
                    {
                        "trial_id": f"trial-{len(schedule) + 1:06d}",
                        "case_id": case["id"],
                        "arm": arm,
                        "repetition": repetition,
                    }
                )
    return schedule


def plan_run(protocol, output):
    validate_protocol(protocol)
    output = Path(output)
    if output.exists():
        raise ExperimentError("run_directory_must_be_new")
    output.mkdir(parents=True)
    schedule = make_schedule(protocol)
    write_new(output / "protocol.json", protocol)
    write_new(output / "schedule.json", schedule)
    manifest = {
        "schema_version": 1,
        "created_at": _now(),
        "protocol_sha256": fingerprint(protocol),
        "review_fingerprint": protocol_fingerprint(protocol),
        "schedule_sha256": fingerprint(schedule),
        "code_sha256": code_fingerprint(),
        "status": "planned",
        "trial_count": len(schedule),
        "preparation_cost_usd": None,
        "preparation_duration_seconds": None,
    }
    write_new(output / "manifest.json", manifest)
    (output / "trials").mkdir()
    with sqlite3.connect(output / "attempts.sqlite3") as db:
        db.execute("""CREATE TABLE attempts (
            trial_id TEXT NOT NULL, attempt INTEGER NOT NULL, state TEXT NOT NULL,
            reserved_micros INTEGER NOT NULL, cost_micros INTEGER, started_at TEXT NOT NULL,
            finished_at TEXT, observation TEXT, PRIMARY KEY(trial_id,attempt))""")
    return manifest


class RunStore:
    """Single operator lock plus durable per-attempt reservations in integer microdollars.

    Unknown/crashed/partial attempts retain their entire reservation. Opening a run does
    not silently reset a budget, retry a trial, or convert interrupted work into failure.
    """

    def __init__(self, path):
        self.path = Path(path).resolve()
        self.protocol = json.loads((self.path / "protocol.json").read_text(encoding="utf-8"))
        self.schedule = json.loads((self.path / "schedule.json").read_text(encoding="utf-8"))
        manifest = json.loads((self.path / "manifest.json").read_text(encoding="utf-8"))
        if (
            fingerprint(self.protocol) != manifest["protocol_sha256"]
            or fingerprint(self.schedule) != manifest["schedule_sha256"]
            or code_fingerprint() != manifest["code_sha256"]
        ):
            raise ExperimentError("frozen_run_changed")
        validate_protocol(self.protocol)
        if self.schedule != make_schedule(self.protocol):
            raise ExperimentError("frozen_schedule_invalid")
        self._lock = (self.path / "operator.lock").open("a+b")
        try:
            self._lock.seek(0)
            if os.name == "nt":
                import msvcrt

                if self._lock.read(1) == b"":
                    self._lock.write(b"0")
                    self._lock.flush()
                self._lock.seek(0)
                msvcrt.locking(self._lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            self._lock.close()
            raise ExperimentError("run_already_open") from error
        self.db = sqlite3.connect(self.path / "attempts.sqlite3", timeout=5)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA synchronous=FULL")

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def close(self):
        self.db.close()
        self._lock.close()

    def _trial(self, trial_id):
        for row in self.schedule:
            if row["trial_id"] == trial_id:
                return row
        raise ExperimentError("unknown_trial")

    def payload(self, trial_id):
        trial = self._trial(trial_id)
        case = next(c for c in self.protocol["cases"] if c["id"] == trial["case_id"])
        # Only this payload may cross the client boundary. No expectations, rubric or peers.
        return {"query": case["query"], "task_kind": case["task_kind"]}

    def attempts(self):
        return [dict(r) for r in self.db.execute("SELECT * FROM attempts ORDER BY rowid")]

    def committed_usd(self):
        return (
            self.db.execute(
                "SELECT coalesce(sum(coalesce(cost_micros,reserved_micros)),0) FROM attempts"
            ).fetchone()[0]
            / 1_000_000
        )

    def _journal(self, event):
        # SQLite is authoritative if a crash occurs between transaction and journal append.
        with (self.path / "journal.jsonl").open("ab") as stream:
            stream.write(canonical({"at": _now(), **event}) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())

    def reserve(self, trial_id, *, retry=False):
        validate_protocol(self.protocol, for_execution=True)
        trial = self._trial(trial_id)
        budget = self.protocol["budget"]
        reserve = _micros(budget["trial_usd"]) + _micros(budget["inflight_overshoot_usd"])
        try:
            self.db.execute("BEGIN IMMEDIATE")
            last = self.db.execute(
                "SELECT * FROM attempts WHERE trial_id=? ORDER BY attempt DESC", (trial_id,)
            ).fetchone()
            if last and (not retry or last["state"] in ("running", "succeeded")):
                raise ExperimentError("explicit_retry_required_or_trial_not_retryable")
            if self.db.execute("SELECT 1 FROM attempts WHERE state='running'").fetchone():
                raise ExperimentError("serial_execution_required")
            committed = self.db.execute(
                "SELECT coalesce(sum(coalesce(cost_micros,reserved_micros)),0) FROM attempts"
            ).fetchone()[0]
            cap = min(_micros(budget["session_usd"]), _micros(budget["experiment_usd"]))
            if committed + reserve > cap:
                raise ExperimentError("budget_exhausted_or_unknown_cost_reserved")
            attempt = 1 if last is None else last["attempt"] + 1
            self.db.execute(
                "INSERT INTO attempts VALUES(?,?,?,?,?,?,NULL,NULL)",
                (trial_id, attempt, "running", reserve, None, _now()),
            )
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise
        row = {**trial, "attempt": attempt, "state": "running"}
        self._journal(row)
        return row

    def finish(self, trial, state, *, cost_usd=None, error=None, **fields):
        if state not in TRIAL_STATES or state in ("planned", "running"):
            raise ExperimentError("invalid_terminal_state")
        if cost_usd is not None and (
            type(cost_usd) not in (int, float) or cost_usd < 0 or not math.isfinite(cost_usd)
        ):
            raise ExperimentError("invalid_observed_cost")
        row = self.db.execute(
            "SELECT * FROM attempts WHERE trial_id=? AND attempt=?",
            (trial["trial_id"], trial["attempt"]),
        ).fetchone()
        if row is None or row["state"] != "running":
            raise ExperimentError("attempt_not_running")
        micros = None if cost_usd is None else _micros(cost_usd)
        if micros is not None and micros > row["reserved_micros"]:
            state, error = "invalid", "observed_cost_exceeded_reservation"
        observation = {
            "schema_version": 1,
            **fields,
            **trial,
            "state": state,
            "cost_usd": cost_usd,
            "error": error,
        }
        observation.setdefault("loaded_skill_ids", None)
        observation.setdefault("selected_skill_ids", None)
        observation.setdefault("applied_skill_ids", None)
        observation.setdefault("task_success", None)
        observation.setdefault("catalog_measurement", None)
        observation.setdefault("usage", None)
        observation.setdefault("duration_seconds", None)
        encoded = canonical(observation).decode("utf-8")
        with self.db:
            self.db.execute(
                "UPDATE attempts SET state=?,cost_micros=?,finished_at=?,observation=? "
                "WHERE trial_id=? AND attempt=?",
                (state, micros, _now(), encoded, trial["trial_id"], trial["attempt"]),
            )
        target = self.path / "trials" / trial["trial_id"] / f"attempt-{trial['attempt']:04d}"
        target.mkdir(parents=True, exist_ok=True)
        write_new(target / "observation.json", observation)
        self._journal(
            {
                "trial_id": trial["trial_id"],
                "attempt": trial["attempt"],
                "state": state,
                "cost_usd": cost_usd,
                "error": error,
            }
        )
        return observation

    def recover_interrupted(self):
        for row in self.attempts():
            if row["state"] == "running":
                trial = {**self._trial(row["trial_id"]), "attempt": row["attempt"]}
                self.finish(
                    trial, "unknown", error="interrupted_after_reservation_may_have_spawned"
                )

    def execute(self, trial_id, client, *, retry=False):
        trial = self.reserve(trial_id, retry=retry)
        try:
            result = client(trial, self.payload(trial_id))
        except TimeoutError:
            result = {"state": "timed_out", "error": "supervisor_timeout"}
        except Exception:
            result = {"state": "unknown", "error": "client_exception_cost_unknown"}
        result = dict(result)
        state = result.pop("state", "unknown")
        return self.finish(trial, state, **result)

    def import_review(self, review):
        """Import an explicit operator review bound to an immutable attempt and output.

        The tool records the named reviewer; it does not authenticate a human identity.
        Reviews cannot manufacture native delivery receipts or release cost reservations.
        """
        required = {
            "trial_id",
            "attempt",
            "reviewer",
            "reviewed_at",
            "observation_sha256",
            "output_sha256",
            "task_success",
            "applied_skill_ids",
            "evidence",
        }
        if not isinstance(review, dict) or set(review) != required:
            raise ExperimentError("invalid_review_shape")
        trial = self._trial(review["trial_id"])
        if type(review["attempt"]) is not int or review["attempt"] < 1:
            raise ExperimentError("invalid_review_attempt")
        if not all(
            isinstance(review[k], str) and review[k].strip()
            for k in ("reviewer", "reviewed_at", "evidence")
        ):
            raise ExperimentError("named_review_and_evidence_required")
        if type(review["task_success"]) is not bool:
            raise ExperimentError("explicit_task_judgment_required")
        directory = self.path / "trials" / trial["trial_id"] / f"attempt-{review['attempt']:04d}"
        observation = json.loads((directory / "observation.json").read_text(encoding="utf-8"))
        output = (directory / "task-output.json").read_bytes()
        import hashlib

        if (
            fingerprint(observation) != review["observation_sha256"]
            or hashlib.sha256(output).hexdigest() != review["output_sha256"]
        ):
            raise ExperimentError("review_evidence_changed")
        applied = review["applied_skill_ids"]
        if (
            not isinstance(applied, list)
            or any(not isinstance(x, str) for x in applied)
            or len(applied) != len(set(applied))
            or not set(applied) <= set(observation.get("loaded_skill_ids") or [])
        ):
            raise ExperimentError("review_cannot_invent_manual_delivery")
        write_new(directory / "operator-review.json", review)
        self._journal(
            {
                "event": "operator_review_imported",
                "trial_id": trial["trial_id"],
                "attempt": review["attempt"],
                "review_sha256": fingerprint(review),
            }
        )
        return {"status": "review_recorded", "review_sha256": fingerprint(review)}

    def report(self):
        attempts, latest = self.attempts(), {}
        for row in attempts:
            if row["observation"]:
                observed = json.loads(row["observation"])
                directory = self.path / "trials" / row["trial_id"] / f"attempt-{row['attempt']:04d}"
                review_file = directory / "operator-review.json"
                if review_file.exists():
                    review = json.loads(review_file.read_text(encoding="utf-8"))
                    import hashlib

                    if (
                        review["observation_sha256"] != fingerprint(observed)
                        or review["output_sha256"]
                        != hashlib.sha256((directory / "task-output.json").read_bytes()).hexdigest()
                    ):
                        raise ExperimentError("review_evidence_changed")
                    observed["task_success"] = review["task_success"]
                    observed["applied_skill_ids"] = review["applied_skill_ids"]
                latest[row["trial_id"]] = observed
        result = summarize(self.protocol, list(latest.values()))
        result["attempts"] = attempts
        complete = len(latest) == len(self.schedule) and all(
            a["cost_micros"] is not None for a in attempts
        )
        subtotal = sum(a["cost_micros"] or 0 for a in attempts) / 1_000_000
        result["observed_cost_subtotal_usd"] = subtotal if attempts else None
        result["cost_accounting_complete"] = complete
        result["cost_usd"] = subtotal if complete else None
        result["committed_or_reserved_usd"] = self.committed_usd()
        result["budget_accounting"] = "unknown_cost_retains_full_trial_plus_overshoot_reservation"
        return result
