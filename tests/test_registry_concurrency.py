import threading
from concurrent.futures import ThreadPoolExecutor

import test_registry as shared

from fastskillretrieval.registry import Registry

registry = shared.registry


def test_independent_search_connections_serialize_budget_writes(registry):
    store, source = registry
    shared.publish(store)
    barrier = threading.Barrier(2, timeout=5)

    def search(number):
        local = Registry(store.state, source)
        try:
            barrier.wait()
            return local.call("search", f"parallel-{number}", query="markdown", request_id="one")
        finally:
            local.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(search, range(2)))
    assert [r["status"] for r in results] == ["ok", "ok"], results
    assert store.db.execute("SELECT count(*) FROM request_ledger").fetchone()[0] == 2
