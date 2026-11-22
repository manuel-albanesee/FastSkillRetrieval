import json

import pytest
from test_experiment_contracts import protocol

from fastskillretrieval.experiment import ExperimentError, protocol_fingerprint
from fastskillretrieval.experiment_runner import RunStore, plan_run


def approved_protocol():
    p = protocol()
    p["budget"] = {
        "trial_usd": 1,
        "session_usd": 3,
        "experiment_usd": 4,
        "timeout_seconds": 10,
        "max_turns": 2,
        "inflight_overshoot_usd": 0.1,
    }
    p["cases"][0]["human_review"] = {"reviewer": "fixture-human", "reviewed_at": "fixture"}
    p["approval"] = {
        "fingerprint": protocol_fingerprint(p),
        "reviewer": "fixture-human",
        "authorized_by": "fixture-operator",
        "authorized_at": "fixture",
    }
    return p


def test_plan_is_offline_counterbalanced_and_immutable(tmp_path):
    out = tmp_path / "new path"
    result = plan_run(protocol(), out)
    schedule = json.loads((out / "schedule.json").read_text())
    assert [x["arm"] for x in schedule] == ["A_NATIVE", "C_FSR", "C_FSR", "A_NATIVE"]
    assert result["status"] == "planned"
    assert not list((out / "trials").glob("*"))
    with pytest.raises(ExperimentError, match="new"):
        plan_run(protocol(), out)


def test_unknown_crash_reservation_survives_resume_and_no_duplicate(tmp_path):
    plan_run(approved_protocol(), tmp_path / "run")
    with RunStore(tmp_path / "run") as store:
        row = store.reserve("trial-000001")
        assert row["attempt"] == 1
    with RunStore(tmp_path / "run") as store:
        store.recover_interrupted()
        assert store.attempts()[0]["state"] == "unknown"
        with pytest.raises(ExperimentError, match="retry"):
            store.reserve("trial-000001")
        assert store.committed_usd() == pytest.approx(1.1)
        retry = store.reserve("trial-000001", retry=True)
        assert retry["attempt"] == 2
        store.finish(retry, "unknown", cost_usd=None)
        with pytest.raises(ExperimentError, match="budget"):
            store.reserve("trial-000002")


def test_cost_missing_holds_reservation_timeout_and_failure_are_not_erased(tmp_path):
    plan_run(approved_protocol(), tmp_path / "run")
    with RunStore(tmp_path / "run") as store:
        row = store.reserve("trial-000001")
        store.finish(row, "timed_out", cost_usd=None, error="timeout")
        assert store.committed_usd() == pytest.approx(1.1)
        row2 = store.reserve("trial-000001", retry=True)
        store.finish(row2, "failed", cost_usd=0.2, error="429")
        assert len(store.attempts()) == 2
        assert store.committed_usd() == pytest.approx(1.3)


def test_protocol_or_schedule_mutation_prevents_resume(tmp_path):
    out = tmp_path / "run"
    plan_run(protocol(), out)
    p = json.loads((out / "protocol.json").read_text())
    p["repetitions"] = 5
    (out / "protocol.json").write_text(json.dumps(p))
    with pytest.raises(ExperimentError, match="frozen"):
        RunStore(out)


def test_reservation_requires_real_approval_even_if_plan_offline(tmp_path):
    plan_run(protocol(), tmp_path / "run")
    with RunStore(tmp_path / "run") as store:
        with pytest.raises(ExperimentError, match="budget"):
            store.reserve("trial-000001")


def test_fake_client_path_with_spaces_and_partial_output(tmp_path):
    plan_run(approved_protocol(), tmp_path / "run")
    with RunStore(tmp_path / "run") as store:
        observed = store.execute(
            "trial-000001",
            lambda trial, payload: {
                "state": "failed",
                "cost_usd": None,
                "error": "partial_output",
                "loaded_skill_ids": None,
                "task_success": None,
            },
        )
        assert observed["state"] == "failed"
        assert store.committed_usd() == pytest.approx(1.1)
        assert "essential" not in store.payload("trial-000001")
        assert "query" in store.payload("trial-000001")


def test_operator_review_is_bound_to_output_and_cannot_fabricate_loads(tmp_path):
    import hashlib

    from fastskillretrieval.experiment import fingerprint
    from fastskillretrieval.experiment_runner import write_new

    plan_run(approved_protocol(), tmp_path / "run")
    with RunStore(tmp_path / "run") as store:
        trial = store.reserve("trial-000001")
        observed = store.finish(trial, "succeeded", cost_usd=0.1, loaded_skill_ids=["ecc/a"])
        folder = store.path / "trials/trial-000001/attempt-0001"
        write_new(folder / "task-output.json", {"result": "fixture output"})
        review = {
            "trial_id": "trial-000001",
            "attempt": 1,
            "reviewer": "fixture-reviewer",
            "reviewed_at": "fixture-date",
            "observation_sha256": fingerprint(observed),
            "output_sha256": hashlib.sha256((folder / "task-output.json").read_bytes()).hexdigest(),
            "task_success": True,
            "applied_skill_ids": ["ecc/unknown"],
            "evidence": "fixture output has required marker",
        }
        with pytest.raises(ExperimentError, match="invent"):
            store.import_review(review)
        review["applied_skill_ids"] = ["ecc/a"]
        assert store.import_review(review)["status"] == "review_recorded"
        assert store.report()["cases"][0]["arms"]["A_NATIVE"]["task_success"] is True
        (folder / "task-output.json").write_text('{"result":"tampered"}')
        with pytest.raises(ExperimentError, match="changed"):
            store.report()
