"""The ingestion scale benchmark exercises the public registry path without SQL seeding."""

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "benchmark_ingestion.py"


def test_ingestion_benchmark_smoke_uses_public_pipeline(tmp_path):
    spec = importlib.util.spec_from_file_location("benchmark_ingestion", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    report = module.run_benchmark(tmp_path / "ingestion", 20)

    assert report["status"] == "ok"
    assert report["corpus"]["size"] == 20
    assert report["corpus"]["preparation"].endswith("public discovery/inspect/submit/publish calls")
    assert report["publication"]["published"] == 20
    assert report["search"]["snapshot_load_and_resource_verified"] is True
    assert report["search"]["samples"] == 100
    assert report["search"]["isolated_session_per_sample"] is True
    assert report["search"]["scope"] == "Registry.call(search) plus JSON serialization"
    assert report["claims"]["human_100_skills_20_requests"] == "not_evaluated"
