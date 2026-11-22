import json
import sys
from types import SimpleNamespace

import pytest
from test_experiment_contracts import protocol
from test_experiment_runner import approved_protocol

from fastskillretrieval.experiment import fingerprint, protocol_fingerprint
from fastskillretrieval.experiment_cli import _capture, main
from fastskillretrieval.experiment_runner import RunStore, plan_run


def test_offline_cli_never_needs_executable(tmp_path, capsys):
    p = tmp_path / "protocol.json"
    p.write_text(json.dumps(protocol()))
    assert main(["validate", "--protocol", str(p)]) == 0
    out = tmp_path / "run"
    assert main(["plan", "--protocol", str(p), "--output", str(out)]) == 0
    assert main(["report", "--run", str(out), "--output", str(tmp_path / "report.json")]) == 0
    report = json.loads((tmp_path / "report.json").read_text())
    assert report["verdict_cases"] == "inconclusive"
    assert (
        main(
            [
                "run",
                "--protocol",
                str(p),
                "--output",
                str(tmp_path / "paid"),
                "--source-root",
                str(tmp_path),
                "--config",
                str(p),
            ]
        )
        == 2
    )
    assert not (tmp_path / "paid").exists()
    assert "explicit_budget_required" in capsys.readouterr().out


def test_capture_fake_process_and_timeout_with_spaces(tmp_path):
    fake = tmp_path / "fake client.py"
    fake.write_text('print(\'{"type":"result","result":"fixture"}\')')
    result = _capture([sys.executable, str(fake)], tmp_path, 5, local_only=True)
    assert result["events"][0]["result"] == "fixture"
    fake.write_text('import time\nprint("partial",flush=True)\ntime.sleep(10)')
    result = _capture([sys.executable, str(fake)], tmp_path, 0.1, local_only=True)
    assert result["timed_out"] is True
    assert result["partial"] is True


@pytest.mark.parametrize("model,expected", [("fixture", "succeeded"), ("changed", "invalid")])
def test_budgeted_queue_fake_transport_checks_model_and_saves_replay(
    tmp_path, monkeypatch, model, expected
):
    from fastskillretrieval import experiment_cli as cli

    p = approved_protocol()
    config = cli.execution_config(None, None)
    p["config_sha256"] = fingerprint(config)
    p["approval"]["fingerprint"] = protocol_fingerprint(p)
    out = tmp_path / "run"
    plan_run(p, out)
    binary = tmp_path / "fake native.exe"
    binary.write_bytes(b"fixture binary identity")
    monkeypatch.setattr(cli, "_executable", lambda value: binary)
    monkeypatch.setattr(cli, "_version", lambda value: "fixture")
    monkeypatch.setattr(cli, "_probe", lambda *args: {"status": "verified"})
    monkeypatch.setattr(
        cli, "verify_preflight", lambda *args: {"status": "verified", "client_version": "fixture"}
    )
    monkeypatch.setattr(
        cli,
        "_profile",
        lambda protocol, arm, source, folder, config: SimpleNamespace(
            cwd=folder, profile_sha256="f" * 64
        ),
    )
    monkeypatch.setattr(
        cli, "build_command", lambda profile, prompt, budget, **kw: [str(binary), prompt]
    )
    monkeypatch.setattr(
        cli,
        "_capture",
        lambda *args, **kw: {
            "events": [
                {
                    "type": "system",
                    "subtype": "init",
                    "model": model,
                    "claude_code_version": "fixture",
                    "opaque_secret_metadata": "drop me",
                },
                {
                    "type": "result",
                    "result": "declarative fixture output",
                    "usage": {},
                    "total_cost_usd": 0.1,
                },
            ],
            "duration_seconds": 0.1,
            "returncode": 0,
            "timed_out": False,
            "partial": False,
            "stdout_sha256": "a" * 64,
            "stderr_sha256": "b" * 64,
        },
    )
    with RunStore(out) as store:
        report = cli.execute_queue(store, tmp_path, config, binary)
        attempts = store.attempts()
        assert attempts[0]["state"] == expected
        assert len(attempts) == (4 if expected == "succeeded" else 1)
        assert report["verdict_cases"] == ("failed" if expected == "succeeded" else "inconclusive")
        artifact = out / "trials/trial-000001/attempt-0001"
        assert (
            json.loads((artifact / "task-output.json").read_text())["result"]
            == "declarative fixture output"
        )
        assert "opaque_secret_metadata" not in (artifact / "events.jsonl").read_text()
        assert "essential" not in (artifact / "invocation.json").read_text()


def test_missing_native_binary_and_shell_wrapper_are_rejected(tmp_path, monkeypatch):
    from fastskillretrieval.experiment import ExperimentError
    from fastskillretrieval.experiment_cli import _executable

    monkeypatch.setattr("fastskillretrieval.experiment_cli.shutil.which", lambda value: None)
    with pytest.raises(ExperimentError, match="missing"):
        _executable("absent")
    monkeypatch.setattr(
        "fastskillretrieval.experiment_cli.shutil.which", lambda value: str(tmp_path / "client.cmd")
    )
    with pytest.raises(ExperimentError, match="wrapper"):
        _executable("client.cmd")
