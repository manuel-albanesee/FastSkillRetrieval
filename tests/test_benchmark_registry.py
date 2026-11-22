"""The synthetic benchmark reports measurements without claiming human evaluation."""

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/benchmark_registry.py"


def test_synthetic_benchmark_cli_and_verified_fixture(tmp_path):
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--size", "20", "--state", str(tmp_path / "bench")],
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=90,
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["status"] == "ok"
    assert report["corpus"]["synthetic"] is True
    assert report["corpus"]["size"] == 20
    assert report["workload"]["warmup"] == 10
    assert report["workload"]["query_count"] == 20
    assert report["workload"]["samples"] == 100
    assert report["workload"]["isolated_session_per_sample"] is True
    samples = report["timings"]["samples_ms"]
    assert len(samples) == 100
    assert report["timings"]["p95_ms"] == sorted(samples)[94]
    assert report["timings"]["scope"] == "Registry.call(search) plus JSON serialization"
    assert report["verification"]["resource_marker"] is True
    assert report["verification"]["snapshot_tamper_rejected"] is True
    assert report["claims"]["human_100_skills_20_requests"] == "not_evaluated"
    assert report["claims"]["context_reduction_30_percent"] == "not_evaluated"
    assert report["runtime"]["python"]
    assert report["hardware"]["logical_cpu_count"]


def test_nearest_rank_percentile_and_unapproved_template():
    spec = importlib.util.spec_from_file_location("benchmark_registry", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.nearest_rank(list(range(1, 101)), 0.95) == 95
    template = json.loads((ROOT / "examples/capability-eval-template.json").read_text())
    assert template["status"] == "unapproved"
    assert template["requests"] == []
    assert template["required_request_count"] == 20
