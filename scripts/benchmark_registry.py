"""Synthetic registry benchmark; never substitutes for the agreed human corpus.

Preparation seeds validated descriptors/catalog rows directly to avoid benchmarking
thousands of maintenance responses. Source files and package snapshots are real;
the measured public search boundary retains every normal integrity/budget check.
"""

import argparse
import importlib.metadata
import math
import os
import platform
import random
import shutil
import sqlite3
import statistics
import tempfile
import time
from collections import Counter
from dataclasses import asdict
from pathlib import Path

from fastskillretrieval.capabilities import validate_manifest
from fastskillretrieval.contracts import Limits, serialized, validate_candidate
from fastskillretrieval.packages import digest, read_package, safe_path
from fastskillretrieval.registry import Registry

SEED = 20261018
MARKER = "FSR-SYNTHETIC-BENCHMARK-RESOURCE"


def nearest_rank(values, fraction):
    """Nearest-rank percentile: sorted[ceil(p * N) - 1], without interpolation."""
    if not values or not 0 < fraction <= 1:
        raise ValueError("non-empty samples and 0 < percentile <= 1 required")
    return sorted(values)[math.ceil(fraction * len(values)) - 1]


def seed_fixture(base, size):
    """Build an explicitly disposable synthetic catalog without production shortcuts."""
    base = safe_path(base)
    if base.exists() and any(base.iterdir()):
        raise ValueError("benchmark state must be a new or empty directory")
    source = base / "sources"
    source.mkdir(parents=True, exist_ok=True)
    store = Registry(base / "registry", source)
    selection = []
    queries = []
    try:
        for index in range(size):
            group = index % 20
            tag = f"fsrgroup{group:02d}case{index:05d}"
            if index < 20:
                queries.append(tag)
            name = f"synthetic-{index:05d}"
            identity = digest(f"synthetic/{name}".encode())[:24]
            folder = source / name
            folder.mkdir()
            quote = f"Review the synthetic fixture {tag} using its bundled resource."
            (folder / "SKILL.md").write_text(f"# Synthetic fixture\n\n{quote}\n")
            (folder / "resource.md").write_text(f"{MARKER}\n{tag}\n")
            files, hashes, revision = read_package(folder, store.limits)
            manifest = validate_manifest(
                {
                    "kind": "skill",
                    "name": name,
                    "description_original": quote,
                }
            )
            metadata = validate_candidate(
                {
                    "description": f"Synthetic retrieval fixture {tag}.",
                    "aliases": [],
                    "capabilities": [
                        {
                            "action": "Review",
                            "object": tag,
                            "scope": "synthetic fixtures",
                            "evidence": {"file": "SKILL.md", "quote": quote},
                        }
                    ],
                    "use_when": ["Synthetic performance measurement"],
                    "exclusions": ["Human acceptance evaluation"],
                    "technologies": ["markdown"],
                },
                files,
                store.limits,
            )
            descriptor = digest(serialized([identity, revision, metadata, "rules-v1"]).encode())
            snapshot = store.state / "packages" / identity / revision
            shutil.copytree(folder, snapshot)
            if read_package(snapshot, store.limits)[2] != revision:
                raise RuntimeError("synthetic snapshot integrity failure")
            store.db.execute(
                "INSERT INTO capabilities(id,name,kind,scope,source_revision,state,manifest,"
                "entry_file) VALUES(?,?,?,?,?,?,?,?)",
                (
                    identity,
                    name,
                    "skill",
                    store.scope,
                    revision,
                    "published",
                    serialized(manifest),
                    "SKILL.md",
                ),
            )
            store.db.execute(
                "INSERT INTO source_revisions VALUES(?,?,?,?,?)",
                (identity, revision, serialized(hashes), "SKILL.md", serialized(manifest)),
            )
            store.db.execute(
                "INSERT INTO descriptor_revisions VALUES(?,?,?,?,?)",
                (descriptor, identity, revision, serialized(metadata), "rules-v1"),
            )
            selection.append((identity, revision, descriptor, serialized(manifest)))
        signature = digest(serialized(sorted(selection)).encode())
        catalog = store.db.execute(
            "INSERT INTO catalogs(signature) VALUES(?)",
            (signature,),
        ).lastrowid
        store.db.executemany(
            "INSERT INTO entries VALUES(?,?,?,?,?)",
            [(catalog, *item) for item in selection],
        )
        store._activate(catalog)
        store.db.commit()
        return store, queries
    except BaseException:
        store.close()
        raise


def verify_fixture(store, query):
    found = store.call("search", "bench-verify", query=query, request_id="verify", limit=1)
    if found["status"] != "ok" or len(found["candidates"]) != 1:
        raise RuntimeError(f"synthetic fixture search failed: {found['status']}")
    reference = found["candidates"][0]["reference"]
    loaded = store.call("load", "bench-verify", reference=reference)
    resource = store.call("resource", "bench-verify", reference=reference, file="resource.md")
    if loaded["status"] != "ok" or MARKER not in resource.get("content", ""):
        raise RuntimeError("synthetic fixture load/resource verification failed")
    snapshot = (
        store.state
        / "packages"
        / reference["capability_id"]
        / reference["source_revision"]
        / "resource.md"
    )
    original = snapshot.read_bytes()
    try:
        snapshot.write_bytes(b"tampered synthetic resource")
        rejected = store.call("search", "bench-tamper", query=query, request_id="tamper", limit=1)
        if rejected["status"] != "stale_reference":
            raise RuntimeError("search did not reject tampered synthetic snapshot")
    finally:
        snapshot.write_bytes(original)
    return {"resource_marker": True, "snapshot_tamper_rejected": True}


def run_benchmark(base, size):
    build_start = time.perf_counter()
    store, queries = seed_fixture(base, size)
    build_ms = (time.perf_counter() - build_start) * 1000
    try:
        verification = verify_fixture(store, queries[0])
        schedule = list(range(20)) * 5
        random.Random(SEED).shuffle(schedule)
        samples, statuses = [], Counter()
        for number in range(10 + len(schedule)):
            query_index = number % 20 if number < 10 else schedule[number - 10]
            # Synthetic traffic uses a fresh context per request; normal production
            # limits remain unchanged, rather than raising the budget for this tool.
            session = f"bench-warmup-{number}" if number < 10 else f"bench-sample-{number - 10}"
            started = time.perf_counter_ns()
            result = store.call(
                "search",
                session,
                query=queries[query_index],
                request_id=f"sample-{number}",
                limit=1,
            )
            encoded = serialized(result).encode("utf-8")
            elapsed = (time.perf_counter_ns() - started) / 1_000_000
            if number >= 10:
                samples.append(elapsed)
                statuses[result["status"]] += 1
            if result["status"] != "ok" or len(result["candidates"]) != 1 or not encoded:
                raise RuntimeError(f"benchmark search failed: {result['status']}")
        return {
            "status": "ok",
            "benchmark": "synthetic_registry_search_v1",
            "corpus": {
                "synthetic": True,
                "size": size,
                "groups": 20,
                "matches_per_query": 1,
                "preparation": "validated SQL seed and real snapshots",
            },
            "workload": {
                "warmup": 10,
                "query_count": 20,
                "repetitions": 5,
                "samples": len(samples),
                "shuffle_seed": SEED,
                "limit": 1,
                "isolated_session_per_sample": True,
                "traffic": "artificial independent sessions; no LLM calls",
            },
            "timings": {
                "scope": "Registry.call(search) plus JSON serialization",
                "build_ms_excluded": build_ms,
                "samples_ms": samples,
                "p50_ms": statistics.median(samples),
                "p95_ms": nearest_rank(samples, 0.95),
                "max_ms": max(samples),
                "percentile_definition": "nearest rank: sorted[ceil(0.95*N)-1]",
                "statuses": dict(statuses),
            },
            "verification": verification,
            "hardware": {
                "machine": platform.machine(),
                "processor": platform.processor(),
                "logical_cpu_count": os.cpu_count(),
                "storage": "local fixture filesystem; device model not measured",
            },
            "runtime": {
                "python": platform.python_version(),
                "os": platform.platform(),
                "sqlite": sqlite3.sqlite_version,
                "mcp": importlib.metadata.version("mcp"),
            },
            "configuration": {
                "limits": asdict(Limits()),
                "registry_schema": 2,
                "state": str(store.state),
                "source": str(store.root),
            },
            "claims": {
                "human_100_skills_20_requests": "not_evaluated",
                "context_reduction_30_percent": "not_evaluated",
                "semantic_quality": "not_evaluated",
                "native_client_latency": "not_evaluated",
            },
        }
    finally:
        store.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--size",
        type=int,
        choices=(20, 100, 5000),
        default=100,
        help="Synthetic entries: 100/5000 for scale, 20 for a fast smoke test",
    )
    parser.add_argument(
        "--state", type=Path, help="New or empty disposable fixture directory; default: temporary"
    )
    args = parser.parse_args()
    try:
        if args.state:
            report = run_benchmark(args.state, args.size)
        else:
            with tempfile.TemporaryDirectory(prefix="fsr-synthetic-benchmark-") as temporary:
                report = run_benchmark(Path(temporary), args.size)
        print(serialized(report))
    except (OSError, ValueError, RuntimeError) as error:
        print(serialized({"status": "benchmark_failed", "reason": str(error)}))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
