"""Measure bounded, public ingestion and publication on disposable synthetic packages.

This is a scale and integrity benchmark, not a human-corpus quality evaluation.  It
uses Registry discovery, inspection, descriptor validation, snapshot publication,
and search APIs only; it never writes catalog rows directly.
"""

import argparse
import importlib.metadata
import math
import os
import platform
import random
import sqlite3
import statistics
import time
from dataclasses import asdict
from pathlib import Path

from fastskillretrieval.contracts import Limits, serialized
from fastskillretrieval.packages import safe_path
from fastskillretrieval.registry import Registry

ROOT = Path(__file__).resolve().parents[1]
SEED = 20261018
MARKER = "FSR-INGESTION-BENCHMARK-RESOURCE"
PUBLISH_BATCH_SIZE = 10


def nearest_rank(values, fraction):
    """Nearest-rank percentile: sorted[ceil(p * N) - 1]."""
    if not values or not 0 < fraction <= 1:
        raise ValueError("non-empty samples and 0 < percentile <= 1 required")
    return sorted(values)[math.ceil(fraction * len(values)) - 1]


def candidate(index):
    tag = f"fsringestgroup{index % 20:02d}case{index:05d}"
    quote = f"Review the synthetic ingestion fixture {tag} through its bundled resource."
    return (
        tag,
        quote,
        {
            "description": (
                f"Synthetic ingestion benchmark capability {tag} measures bounded descriptor "
                "validation and immutable publication through the public registry API."
            ),
            "aliases": [],
            "capabilities": [
                {
                    "action": "Review",
                    "object": tag,
                    "scope": "synthetic ingestion benchmark corpus",
                    "evidence": {"file": "SKILL.md", "quote": quote},
                }
            ],
            "use_when": ["Measuring a bounded local registry ingestion benchmark."],
            "exclusions": ["Human capability quality evaluation."],
            "technologies": ["markdown"],
        },
    )


def prepare_sources(source, size):
    started = time.perf_counter()
    source.mkdir(parents=True)
    for index in range(size):
        tag, quote, _ = candidate(index)
        folder = source / f"ingestion-{index:05d}"
        folder.mkdir()
        (folder / "SKILL.md").write_text(
            f"# Synthetic ingestion fixture {index:05d}\n\n{quote}\n", encoding="utf-8"
        )
        (folder / "resource.md").write_text(f"{MARKER}\n{tag}\n", encoding="utf-8")
    return (time.perf_counter() - started) * 1000


def require_benchmark_root(root, *, resume=False):
    root = safe_path(root)
    if root.name.casefold() == ".fsr":
        raise ValueError("benchmark state must not be a real .fsr directory")
    if root.exists() and any(root.iterdir()) and not resume:
        raise ValueError("benchmark state must be a new or empty directory")
    if resume and not (root / "sources").is_dir():
        raise ValueError("resume state must contain prepared sources")
    root.mkdir(parents=True, exist_ok=True)
    return root


def discover_all(store):
    started = time.perf_counter()
    cursor, batch = 0, 0
    while True:
        result = store.call("discover", f"benchmark-discover-{batch}", cursor=cursor)
        if result["status"] != "ok":
            raise RuntimeError(f"discovery failed: {result}")
        cursor = result["next_cursor"]
        batch += 1
        if cursor is None:
            break
    return (time.perf_counter() - started) * 1000, batch


def inventory_all(store):
    items, offset = [], 0
    while True:
        result = store.call("inventory", f"benchmark-inventory-{offset}", offset=offset, limit=20)
        if result["status"] != "ok":
            raise RuntimeError(f"inventory failed: {result}")
        items.extend(result["items"])
        offset += len(result["items"])
        if offset >= result["total"]:
            return items


def inspect_and_submit_all(store, items):
    inspect_ms = submit_ms = 0.0
    for item in items:
        index = int(item["name"].rsplit("-", 1)[1])
        tag, quote, descriptor = candidate(index)
        started = time.perf_counter()
        inspected = store.call(
            "inspect",
            f"benchmark-inspect-{index}",
            capability_id=item["id"],
            source_revision=item["source_revision"],
        )
        inspect_ms += (time.perf_counter() - started) * 1000
        if inspected["status"] != "ok" or quote not in inspected["content"]:
            raise RuntimeError(
                f"inspect did not expose expected source data for {tag}: {inspected}"
            )
        started = time.perf_counter()
        submitted = store.call(
            "submit",
            f"benchmark-submit-{index}",
            capability_id=item["id"],
            source_revision=item["source_revision"],
            candidate=descriptor,
        )
        submit_ms += (time.perf_counter() - started) * 1000
        if submitted["status"] != "ok":
            raise RuntimeError(f"descriptor validation failed for {tag}: {submitted}")
    return inspect_ms, submit_ms


def publish_all(store):
    snapshots_ms, calls = [], 0
    while True:
        started = time.perf_counter()
        result = store.call("publish", f"benchmark-publish-{calls}", batch_size=PUBLISH_BATCH_SIZE)
        elapsed = (time.perf_counter() - started) * 1000
        calls += 1
        if result["status"] == "in_progress":
            snapshots_ms.append(elapsed)
            continue
        if result["status"] != "ok":
            raise RuntimeError(f"publication failed: {result}")
        return snapshots_ms, elapsed, calls, result


def verify_and_search(store, size):
    tag, _, _ = candidate(0)
    verified = store.call(
        "search", "benchmark-verification", query=tag, request_id="verification", limit=1
    )
    if verified["status"] != "ok" or len(verified["candidates"]) != 1:
        raise RuntimeError(f"verification search failed: {verified}")
    reference = verified["candidates"][0]["reference"]
    loaded = store.call("load", "benchmark-verification", reference=reference)
    resource = store.call(
        "resource", "benchmark-verification", reference=reference, file="resource.md"
    )
    if loaded["status"] != "ok" or MARKER not in resource.get("content", ""):
        raise RuntimeError("published snapshot did not pass load/resource verification")

    query_count = min(20, size)
    schedule = list(range(query_count)) * 5
    random.Random(SEED).shuffle(schedule)
    samples = []
    for number in range(10 + len(schedule)):
        query_index = number % query_count if number < 10 else schedule[number - 10]
        query, _, _ = candidate(query_index)
        started = time.perf_counter_ns()
        result = store.call(
            "search",
            f"benchmark-search-{number}",
            query=query,
            request_id=f"search-{number}",
            limit=1,
        )
        encoded = serialized(result).encode("utf-8")
        elapsed = (time.perf_counter_ns() - started) / 1_000_000
        if not encoded or result["status"] != "ok" or len(result["candidates"]) != 1:
            raise RuntimeError(f"search failed: {result}")
        if number >= 10:
            samples.append(elapsed)
    return {
        "snapshot_load_and_resource_verified": True,
        "warmup": 10,
        "query_count": query_count,
        "repetitions": 5,
        "samples": len(samples),
        "shuffle_seed": SEED,
        "limit": 1,
        "isolated_session_per_sample": True,
        "scope": "Registry.call(search) plus JSON serialization",
        "p50_ms": statistics.median(samples),
        "p95_ms": nearest_rank(samples, 0.95),
        "max_ms": max(samples),
        "percentile_definition": "nearest rank: sorted[ceil(0.95*N)-1]",
        "samples_ms": samples,
    }


def run_benchmark(root, size, *, resume=False):
    root = require_benchmark_root(root, resume=resume)
    source = root / "sources"
    setup_ms = None if resume else prepare_sources(source, size)
    store = Registry(root / "registry", source)
    try:
        discovery_ms, discovery_batches = (None, None) if resume else discover_all(store)
        items = inventory_all(store)
        if len(items) != size:
            raise RuntimeError(f"discovery count mismatch: expected {size}, got {len(items)}")
        inspect_ms, validation_ms = (None, None) if resume else inspect_and_submit_all(store, items)
        snapshot_batches_ms, activation_call_ms, publication_calls, published = publish_all(store)
        search = verify_and_search(store, size)
        return {
            "status": "ok",
            "benchmark": "registry_public_ingestion_v1",
            "corpus": {
                "synthetic": True,
                "size": size,
                "groups": 20,
                "matches_per_query": 1,
                "human_corpus_20_requests": False,
                "preparation": (
                    "real package files plus public discovery/inspect/submit/publish calls"
                ),
            },
            "configuration": {
                "limits": asdict(Limits()),
                "discovery_batch_limit": 100,
                "publish_batch_size": PUBLISH_BATCH_SIZE,
                "state": str(root),
                "maintenance_contexts": "independent per bounded operator request",
                "resumed_after_interruption": resume,
            },
            "timings": {
                "fixture_setup_ms_excluded": setup_ms,
                "discovery_ms": discovery_ms,
                "descriptor_inspection_ms": inspect_ms,
                "descriptor_validation_ms": validation_ms,
                "snapshot_batch_ms": snapshot_batches_ms,
                "snapshot_batches": len(snapshot_batches_ms),
                "activation_call_ms": activation_call_ms,
                "activation_call_scope": (
                    "final bounded snapshot batch plus atomic catalog activation"
                ),
                "publication_calls": publication_calls,
            },
            "publication": {
                "catalog_id": published["catalog_id"],
                "published": published["published"],
                "active_catalog": store.active(),
            },
            "search": search,
            "workload": {
                "traffic": "artificial independent sessions; no LLM calls",
                "response_budgets": "default Limits.period retained for every session",
            },
            "runtime": {
                "python": platform.python_version(),
                "os": platform.platform(),
                "sqlite": sqlite3.sqlite_version,
                "mcp": importlib.metadata.version("mcp"),
            },
            "hardware": {
                "machine": platform.machine(),
                "processor": platform.processor(),
                "logical_cpu_count": os.cpu_count(),
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
    parser.add_argument("--size", type=int, choices=(100, 5000), default=100)
    parser.add_argument(
        "--state",
        type=Path,
        default=ROOT / ".work" / "benchmark-ingestion",
        help="New or empty disposable .work directory; never a real .fsr state directory.",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume a bounded, interrupted disposable benchmark state.",
    )
    args = parser.parse_args()
    try:
        print(serialized(run_benchmark(args.state, args.size, resume=args.resume)))
    except (OSError, RuntimeError, ValueError) as error:
        print(serialized({"status": "benchmark_failed", "reason": str(error)}))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
