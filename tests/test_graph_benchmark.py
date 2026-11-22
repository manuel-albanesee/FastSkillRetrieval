import importlib.util
from pathlib import Path


def test_graph_benchmark_measures_new_graph(tmp_path):
    path = Path(__file__).parents[1] / "scripts/benchmark_skill_graph.py"
    spec = importlib.util.spec_from_file_location("benchmark_skill_graph", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result = module.run_benchmark(tmp_path / "bench", 20)
    assert result["graph"]["node_count"] == 20
    assert result["graph"]["edge_count"] == 20
    assert len(result["timings"]["samples_ms"]) == 100
    assert result["verification"]["graph_expansion"]
    assert result["verification"]["excluded_neighbor"]
    assert result["verification"]["replay_equal"]


def test_graph_supplement_keeps_connection_cache_and_concurrency_separate(tmp_path):
    path = Path(__file__).parents[1] / "scripts/benchmark_skill_graph.py"
    spec = importlib.util.spec_from_file_location("benchmark_graph_supplement", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    report = module.supplement_benchmark(tmp_path / "missing")
    assert report["status"] == "invalid_fixture"
    base = tmp_path / "fixture"
    store, _, _ = module.seed_public_fixture(base, 20)
    store.close()
    report = module.supplement_benchmark(base)
    assert report["status"] == "ok"
    assert len(report["connection_cold"]["samples"]) == 20
    assert len(report["database_warm"]["samples"]) == 20
    assert report["result_cache"]["all_replays_equal"]
    assert len(report["concurrency"]["queries"]) == 2
    assert report["concurrency"]["all_succeeded"]
    assert all("reason" in row for row in report["concurrency"]["queries"])
    assert report["identity"]["graph"]["node_count"] == 20
    assert len(report["main_schedule"]["indices"]) == 100
    assert report["storage_bytes"]["graph.sqlite3"] > 0
    measured = module.remeasure_benchmark(base)
    assert measured["all_succeeded"]
    assert len(measured["samples"]) == 100
    assert measured["p95_ms"] == sorted(row["ms"] for row in measured["samples"])[94]
    assert measured["graph"]["signature"] == report["identity"]["graph"]["signature"]
    resumed, _, preparation = module.seed_public_fixture(base, 20, resume=True)
    try:
        assert preparation["previous_validated_skipped"] == 20
        assert preparation["resumed"]
    finally:
        resumed.close()
