"""Graph-era synthetic scale benchmark. No native-client or semantic-quality claims."""

import argparse
import ctypes
import hashlib
import importlib.metadata
import importlib.util
import json
import os
import platform
import random
import sqlite3
import statistics
import threading
import time
import tracemalloc
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path

from fastskillretrieval.contracts import serialized
from fastskillretrieval.registry import Registry

spec = importlib.util.spec_from_file_location(
    "fsr_benchmark_fixture", Path(__file__).with_name("benchmark_registry.py")
)
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)


def seed_public_fixture(base, size, *, resume=False):
    spec = importlib.util.spec_from_file_location(
        "fsr_ingestion_fixture", Path(__file__).with_name("benchmark_ingestion.py")
    )
    ingestion = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ingestion)
    base = ingestion.require_benchmark_root(base, resume=resume)
    source = base / "sources"
    if not resume:
        ingestion.prepare_sources(source, size)
        for index in range(size):
            path = source / f"ingestion-{index:05d}" / "SKILL.md"
            with path.open("a", encoding="utf-8") as stream:
                stream.write(f"\n## Related Skills\n- `ingestion-{(index + 1) % size:05d}`\n")
    store = Registry(base / "registry", source)
    started = time.perf_counter()
    if not resume:
        ingestion.discover_all(store)
    discover_ms = (time.perf_counter() - started) * 1000
    items = ingestion.inventory_all(store)
    if len(items) != size:
        store.close()
        raise ValueError("resume requires complete discovery and declared fixture size")
    pending = [item for item in items if item["state"] not in {"validated", "published"}]
    inspect_ms, submit_ms = ingestion.inspect_and_submit_all(store, pending)
    started = time.perf_counter()
    batch, publication_attempts = 100, []
    while True:
        result = store.call("publish", f"graph-build-{len(publication_attempts)}", batch_size=batch)
        publication_attempts.append(
            {"batch_size": batch, "status": result["status"], "reason": result.get("reason")}
        )
        if result["status"] == "ok":
            break
        if result["status"] == "timeout" and batch > 1:
            batch = max(1, batch // 2)
            continue
        if result["status"] != "in_progress":
            store.close()
            raise RuntimeError(f"publication failed: {result}; attempts={publication_attempts}")
    publish_ms = (time.perf_counter() - started) * 1000
    return (
        store,
        [ingestion.candidate(i)[0] for i in range(20)],
        {
            "method": (
                "public discovery, inspect, submit, bounded snapshot publication and graph build"
            ),
            "discover_ms": discover_ms,
            "inspect_ms": inspect_ms,
            "submit_ms": submit_ms,
            "publish_ms": publish_ms,
            "resumed": resume,
            "previous_validated_skipped": len(items) - len(pending),
            "publication_batch_size": batch,
            "publication_attempts": publication_attempts,
            "prior_preparation_ms": None if resume else 0,
        },
    )


def run_benchmark(base, size, *, resume=False):
    started = time.perf_counter()
    store, queries, preparation = seed_public_fixture(base, size, resume=resume)
    build_ms = (time.perf_counter() - started) * 1000
    tracemalloc.start()
    try:
        graph = store.call("graph", "graph-inspect", limit=1)
        expanded = store.call(
            "search", "verify-expand", query=queries[0], request_id="one", limit=5
        )
        cards = expanded.get("candidates", [])
        neighbors = [c for c in cards if c.get("retrieval", {}).get("method") == "graph"]
        if not neighbors:
            raise RuntimeError("graph expansion not observed")
        excluded = neighbors[0]["reference"]["capability_id"]
        filtered = store.call("search", "verify-filter", query=queries[0], exclude_ids=[excluded])
        wrong_technology = store.call(
            "search", "verify-tech", query=queries[0], technologies=["python"]
        )
        replay = store.call("search", "verify-expand", query=queries[0], request_id="one", limit=5)
        reference = cards[0]["reference"]
        loaded = store.call("load", "verify-load", reference=reference)
        resource = store.call("resource", "verify-load", reference=reference, file="resource.md")
        snapshot = (
            store.state
            / "packages"
            / reference["capability_id"]
            / reference["source_revision"]
            / "resource.md"
        )
        original = snapshot.read_bytes()
        try:
            snapshot.write_bytes(b"tampered")
            rejected = store.call("load", "verify-tamper", reference=reference)
        finally:
            snapshot.write_bytes(original)
        verification = {
            "resource_marker": "FSR-INGESTION-BENCHMARK-RESOURCE" in resource.get("content", ""),
            "load_ok": loaded["status"] == "ok",
            "snapshot_tamper_rejected": rejected["status"] == "stale_reference",
        }
        verification.update(
            {
                "graph_expansion": True,
                "excluded_neighbor": all(
                    c["reference"]["capability_id"] != excluded
                    for c in filtered.get("candidates", [])
                ),
                "technology_filter": not wrong_technology.get("candidates"),
                "replay_equal": replay == expanded,
                "advisory_edges_not_load_dependencies": all(
                    not c.get("dependencies") for c in cards
                ),
            }
        )
        state, root = store.state, store.root
        store.close()
        store = Registry(state, root)
        resumed = store.call("search", "verify-expand", query=queries[0], request_id="one", limit=5)
        verification["restart_replay_equal"] = resumed == expanded
        schedule = list(range(20)) * 5
        random.Random(fixture.SEED).shuffle(schedule)
        samples = []
        for n in range(110):
            q = n % 20 if n < 10 else schedule[n - 10]
            tick = time.perf_counter_ns()
            response = store.call(
                "search", f"bench-{n}", query=queries[q], request_id="one", limit=5
            )
            encoded = serialized(response).encode("utf-8")
            elapsed = (time.perf_counter_ns() - tick) / 1e6
            if response["status"] != "ok" or not response["candidates"]:
                raise RuntimeError(f"search failed: {response}")
            if n >= 10:
                samples.append(elapsed)
            if len(encoded) > store.limits.output_bytes:
                raise RuntimeError("output budget exceeded")
        verification["output_budget"] = True
        memory_current, memory_peak = tracemalloc.get_traced_memory()
        sizes = {p.name: p.stat().st_size for p in store.state.glob("*.sqlite3")}
        return {
            "benchmark": "synthetic_skill_graph_search_v1",
            "implementation": implementation_identity(),
            "size": size,
            "synthetic": True,
            "graph": {k: graph[k] for k in ("node_count", "edge_count", "signature", "ready")},
            "workload": {
                "warmup": 10,
                "queries": 20,
                "repetitions": 5,
                "samples": 100,
                "limit": 5,
                "seed": fixture.SEED,
            },
            "timings": {
                "scope": "Registry.call(search) and JSON serialization; tracemalloc enabled",
                "build_ms_excluded": build_ms,
                "samples_ms": samples,
                "p50_ms": statistics.median(samples),
                "p95_ms": fixture.nearest_rank(samples, 0.95),
                "p99_ms": fixture.nearest_rank(samples, 0.99),
                "max_ms": max(samples),
                "percentile": "sorted[ceil(.95*N)-1]",
            },
            "verification": verification,
            "limits": asdict(store.limits),
            "storage_bytes": sizes,
            "memory": {
                "python_current_bytes": memory_current,
                "python_peak_bytes": memory_peak,
                "os": windows_memory(),
                "method": (
                    "tracemalloc after preparation; excludes build, native SQLite "
                    "allocations and OS RSS"
                ),
            },
            "runtime": {"python": platform.python_version(), "platform": platform.platform()},
            "preparation": preparation,
            "model_turns": 0,
            "semantic_quality": "not_evaluated",
            "native_latency": "not_evaluated",
        }
    finally:
        store.close()
        tracemalloc.stop()


def windows_memory():
    """OS RSS for this process, keeping its lifetime peak distinct from Python tracing."""
    if os.name != "nt":
        return {"working_set_bytes": None, "reason": "Windows API unavailable"}
    from ctypes import wintypes

    class Counters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
            (name, ctypes.c_size_t)
            for name in (
                "PeakWorkingSetSize",
                "WorkingSetSize",
                "QuotaPeakPagedPoolUsage",
                "QuotaPagedPoolUsage",
                "QuotaPeakNonPagedPoolUsage",
                "QuotaNonPagedPoolUsage",
                "PagefileUsage",
                "PeakPagefileUsage",
            )
        ]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    psapi.GetProcessMemoryInfo.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(Counters),
        wintypes.DWORD,
    ]
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
    values = Counters()
    values.cb = ctypes.sizeof(values)
    if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(values), values.cb):
        return {"working_set_bytes": None, "reason": f"Windows error {ctypes.get_last_error()}"}
    return {
        "working_set_bytes": values.WorkingSetSize,
        "process_lifetime_peak_working_set_bytes": values.PeakWorkingSetSize,
        "pagefile_bytes": values.PagefileUsage,
        "method": (
            "Windows GetProcessMemoryInfo; process peak includes imports and all supplement phases"
        ),
    }


def supplement_benchmark(base):
    """Measure separate workloads against an already publicly ingested synthetic fixture."""
    base = Path(base)
    state, source = base / "registry", base / "sources"
    if not (state / "registry.sqlite3").is_file() or not source.is_dir():
        return {"status": "invalid_fixture", "reason": "existing public fixture required"}
    namespace = uuid.uuid4().hex[:12]
    queries = [f"fsringestgroup{i % 20:02d}case{i:05d}" for i in range(20)]
    schedule = list(range(20)) * 5
    random.Random(fixture.SEED).shuffle(schedule)
    memory = [{"phase": "before", **windows_memory()}]

    def measured(store, query, session, request_id="one"):
        started = time.perf_counter_ns()
        response = store.call("search", session, query=query, request_id=request_id, limit=5)
        size = len(serialized(response).encode("utf-8"))
        return response, {
            "ms": (time.perf_counter_ns() - started) / 1e6,
            "status": response["status"],
            "response_bytes": size,
            "reason": response.get("reason"),
            "candidates": len(response.get("candidates", [])),
        }

    cold = []
    for index, query in enumerate(queries):
        started = time.perf_counter_ns()
        store = Registry(state, source)
        opened_ms = (time.perf_counter_ns() - started) / 1e6
        try:
            _, row = measured(store, query, f"cold-{namespace}-{index}")
            cold.append({"query_index": index, "connection_init_ms_excluded": opened_ms, **row})
        finally:
            store.close()
    memory.append({"phase": "after_connection_cold", **windows_memory()})
    store = Registry(state, source)
    try:
        graph = store.call("graph", f"sup-graph-{namespace}", limit=1)
        entries = [
            dict(r)
            for r in store.db.execute(
                "SELECT * FROM entries WHERE catalog_id=? ORDER BY capability_id", (store.active(),)
            )
        ]
        from fastskillretrieval.experiment import fingerprint

        identity = {
            "source_root": str(source),
            "state": str(state),
            "catalog_id": store.active(),
            "catalog_fingerprint": fingerprint(entries),
            "graph": {k: graph[k] for k in ("node_count", "edge_count", "signature")},
            "limits": asdict(store.limits),
            "retrieval_policy": store.retrieval_policy,
            "registry_schema": store.db.execute("PRAGMA user_version").fetchone()[0],
            "graph_schema": store.db.execute("PRAGMA skill_graph.user_version").fetchone()[0],
        }
        warm = []
        for index, query in enumerate(queries):
            _, row = measured(store, query, f"warm-{namespace}-{index}")
            warm.append({"query_index": index, **row})
        original, first = measured(store, queries[0], f"cache-{namespace}")
        replays, equal = [], True
        for _ in range(20):
            response, row = measured(store, queries[0], f"cache-{namespace}")
            replays.append(row)
            equal = equal and response == original
    finally:
        store.close()
    memory.append({"phase": "after_warm_and_replay", **windows_memory()})
    barrier = threading.Barrier(2, timeout=15)

    def concurrent(index):
        local = Registry(state, source)
        try:
            barrier.wait()
            _, row = measured(local, queries[index], f"parallel-{namespace}-{index}")
            return {"query_index": index, **row}
        finally:
            local.close()

    started = time.perf_counter_ns()
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = [pool.submit(concurrent, index) for index in range(2)]
        parallel = [future.result(timeout=30) for future in pending]
    concurrent_ms = (time.perf_counter_ns() - started) / 1e6
    memory.append({"phase": "after_concurrent", **windows_memory()})
    storage = {p.name: p.stat().st_size for p in state.glob("*.sqlite3*") if p.is_file()}
    versions = {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "sqlite": sqlite3.sqlite_version,
    }
    for package in ("mcp", "fastskillretrieval"):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = None
    return {
        "status": "ok",
        "benchmark": "existing_public_graph_supplement_v1",
        "model_turns": 0,
        "identity": identity,
        "versions": versions,
        "implementation": implementation_identity(),
        "hardware": {
            "machine": platform.machine(),
            "processor": platform.processor(),
            "logical_cpu_count": os.cpu_count(),
        },
        "main_schedule": {
            "seed": fixture.SEED,
            "warmup_indices": list(range(10)),
            "indices": schedule,
            "queries": queries,
            "note": "schedule of main100 samples; supplement samples stay separate",
        },
        "connection_cold": {
            "samples": cold,
            "method": "new Registry connection per query; initialization excluded and recorded; "
            "constructor primes schema pages; OS filesystem cache uncontrolled",
        },
        "database_warm": {
            "samples": warm,
            "method": "one Registry connection; fresh sessions/request IDs; result cache bypassed",
        },
        "result_cache": {
            "first_call": first,
            "replays": replays,
            "all_replays_equal": equal,
            "method": "same session/request ID; integrity checks remain active",
        },
        "concurrency": {
            "queries": parallel,
            "wall_ms_including_connections": concurrent_ms,
            "all_succeeded": all(r["status"] == "ok" for r in parallel),
            "method": "two concurrent Registry connections and distinct sessions; "
            "separate from main100 latency distribution",
        },
        "memory": memory,
        "storage_bytes": storage,
        "storage_method": "registry/graph SQLite files plus any existing WAL/SHM/journal files",
        "unreached": ["OS disk-cache-cold measurement", "dense5000 adversarial latency"],
    }


def implementation_identity():
    from fastskillretrieval import graph, registry

    paths = {
        "registry": Path(registry.__file__),
        "graph": Path(graph.__file__),
        "benchmark": Path(__file__),
    }
    return {
        name: {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        for name, path in paths.items()
    }


def remeasure_benchmark(base):
    """Repeat the original10/100 warm search workload without preparing sources again."""
    base = Path(base)
    state, source = base / "registry", base / "sources"
    queries = [f"fsringestgroup{i % 20:02d}case{i:05d}" for i in range(20)]
    schedule = list(range(20)) * 5
    random.Random(fixture.SEED).shuffle(schedule)
    namespace = uuid.uuid4().hex[:12]
    identity = implementation_identity()
    store = Registry(state, source)
    tracemalloc.start()
    try:
        graph = store.call("graph", f"remeasure-graph-{namespace}", limit=1)
        samples = []
        for number in range(110):
            index = number % 20 if number < 10 else schedule[number - 10]
            started = time.perf_counter_ns()
            response = store.call(
                "search",
                f"remeasure-{namespace}-{number}",
                query=queries[index],
                request_id="one",
                limit=5,
            )
            encoded = serialized(response).encode("utf-8")
            elapsed = (time.perf_counter_ns() - started) / 1e6
            if number >= 10:
                samples.append(
                    {
                        "query_index": index,
                        "ms": elapsed,
                        "status": response["status"],
                        "reason": response.get("reason"),
                        "response_bytes": len(encoded),
                        "candidates": len(response.get("candidates", [])),
                    }
                )
        values = sorted(row["ms"] for row in samples)
        current, peak = tracemalloc.get_traced_memory()
        return {
            "benchmark": "existing_public_graph_warm_remeasure_v1",
            "model_turns": 0,
            "implementation": identity,
            "limits": asdict(store.limits),
            "graph": {k: graph[k] for k in ("node_count", "edge_count", "signature")},
            "workload": {
                "warmup": 10,
                "queries": queries,
                "repetitions": 5,
                "samples": 100,
                "seed": fixture.SEED,
                "indices": schedule,
                "limit": 5,
                "fresh_session_per_request": True,
            },
            "samples": samples,
            "p50_ms": statistics.median(values),
            "p95_ms": values[94],
            "p99_ms": values[98],
            "all_succeeded": all(row["status"] == "ok" for row in samples),
            "scope": "Registry.call(search) through JSON serialization; DB warm; "
            "result cache bypassed; existing public fixture, no reseed or build timing",
            "memory": {
                "python_current_bytes": current,
                "python_peak_bytes": peak,
                "os": windows_memory(),
            },
            "storage_bytes": {
                p.name: p.stat().st_size for p in state.glob("*.sqlite3*") if p.is_file()
            },
        }
    finally:
        store.close()
        tracemalloc.stop()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--size", type=int, choices=[20, 100, 5000])
    p.add_argument("--supplement", action="store_true")
    p.add_argument("--remeasure", action="store_true")
    p.add_argument("--resume-fixture", action="store_true")
    p.add_argument("--state", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    if args.supplement and args.remeasure:
        p.error("choose either --supplement or --remeasure")
    if args.remeasure:
        report = remeasure_benchmark(args.state)
    elif args.supplement:
        report = supplement_benchmark(args.state)
    else:
        if args.size is None:
            p.error("--size required for a new fixture")
        report = run_benchmark(args.state, args.size, resume=args.resume_fixture)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
