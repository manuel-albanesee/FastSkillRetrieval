import json
import shutil
from pathlib import Path

import pytest

from fastskillretrieval.catalog import Catalog

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def workspace(tmp_path):
    source = tmp_path / "skills"
    shutil.copytree(ROOT / "examples" / "skills", source)
    service = Catalog(tmp_path / "state", source)
    metadata = json.loads((ROOT / "examples" / "markdown-candidate.json").read_text())
    yield service, source, metadata
    service.close()


def prepared(service, metadata):
    item = service.call("inventory", "prepare", refresh=True)["items"][0]
    assert (
        service.call(
            "submit", "prepare", skill_id=item["id"], version=item["version"], candidate=metadata
        )["status"]
        == "ok"
    )
    assert service.call("publish", "prepare")["status"] == "ok"
    found = service.call("search", "find", query="markdown")
    return item, found["candidates"][0]["reference"]
