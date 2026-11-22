"""Offline reviewer reproductions: desired safety invariants, no real client invocation."""

import subprocess
from unittest.mock import Mock, patch

from test_experiment_runner import approved_protocol

from fastskillretrieval.experiment_cli import _capture
from fastskillretrieval.experiment_runner import RunStore, plan_run


def test_post_spawn_file_not_found_must_retain_unknown_cost(tmp_path):
    plan_run(approved_protocol(), tmp_path / "run")
    with RunStore(tmp_path / "run") as store:

        def client(trial, payload):
            # Simulates an exception in output persistence/cleanup after client activity.
            raise FileNotFoundError("post-spawn artifact or taskkill missing")

        result = store.execute("trial-000001", client)
        assert result["cost_usd"] is None
        assert store.committed_usd() >= 1.1


def test_timeout_kills_client_even_if_taskkill_fails(tmp_path):
    process = Mock(pid=12345)
    process.poll.return_value = None
    process.communicate.side_effect = [subprocess.TimeoutExpired("fake-client", 1), ("", "")]
    with (
        patch("fastskillretrieval.experiment_cli.subprocess.Popen", return_value=process),
        patch(
            "fastskillretrieval.experiment_cli.subprocess.run",
            side_effect=OSError("taskkill failed"),
        ),
    ):
        try:
            _capture(["fake-client"], tmp_path, 1, local_only=False)
        except OSError:
            pass
    process.kill.assert_called()


def test_report_cost_is_unknown_when_planned_trials_are_unobserved(tmp_path):
    plan_run(approved_protocol(), tmp_path / "run")
    with RunStore(tmp_path / "run") as store:
        trial = store.reserve("trial-000001")
        store.finish(trial, "succeeded", cost_usd=0.2)
        # Three of the four planned trials have no observed cost yet.
        assert store.report()["cost_usd"] is None


def test_report_observed_cost_includes_paid_retries(tmp_path):
    plan_run(approved_protocol(), tmp_path / "run")
    with RunStore(tmp_path / "run") as store:
        first = store.reserve("trial-000001")
        store.finish(first, "failed", cost_usd=0.2)
        second = store.reserve("trial-000001", retry=True)
        store.finish(second, "succeeded", cost_usd=0.3)
        for trial_id in ("trial-000002", "trial-000003", "trial-000004"):
            trial = store.reserve(trial_id)
            store.finish(trial, "succeeded", cost_usd=0)
        assert store.committed_usd() == 0.5
        assert store.report()["cost_usd"] == 0.5
