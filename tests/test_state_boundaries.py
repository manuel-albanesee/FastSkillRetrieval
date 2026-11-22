"""Opt-in cannot create competing databases in another version's state."""

import pytest
import test_registry as shared

from fastskillretrieval.catalog import Catalog
from fastskillretrieval.contracts import Failure
from fastskillretrieval.registry import Registry

registry = shared.registry


def test_registry_startup_requires_explicit_legacy_migration(workspace):
    legacy, root, _ = workspace
    with pytest.raises(Failure) as error:
        Registry(legacy.state, root)
    assert error.value.status == "migration_required"
    assert not (legacy.state / "registry.sqlite3").exists()


def test_new_legacy_entrypoint_refuses_registry_state(registry):
    store, source = registry
    with pytest.raises(Failure) as error:
        Catalog(store.state, source)
    assert error.value.status == "migration_required"
    assert not (store.state / "catalog.sqlite3").exists()
