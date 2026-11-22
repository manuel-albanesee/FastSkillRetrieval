"""Real stdio MCP smoke test against a disposable benign fixture catalog."""

import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]


async def smoke(installed: bool = False):
    (ROOT / ".work").mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="fsr-mcp-", dir=ROOT / ".work") as temporary:
        work = Path(temporary)
        source = work / "skills"
        shutil.copytree(ROOT / "examples" / "skills", source)
        entry = ["-m", "fastskillretrieval"]
        if os.environ.get("FSR_COVERAGE") == "1":
            entry = ["-m", "coverage", "run", "--parallel-mode", *entry]
        environment = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
        if not installed:
            environment["PYTHONPATH"] = str(ROOT / "src")
        else:
            import fastskillretrieval

            assert not Path(fastskillretrieval.__file__).resolve().is_relative_to(ROOT / "src"), (
                "--installed requires a wheel installation, not the editable source environment"
            )
        parameters = StdioServerParameters(
            command=sys.executable,
            args=[
                *entry,
                "--state",
                str(work / "state"),
                "--source-root",
                str(source),
                "serve",
            ],
            env=environment,
            cwd=str(ROOT),
        )
        checks = []
        async with stdio_client(parameters) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                tools = {tool.name for tool in (await session.list_tools()).tools}
                assert {"search_skills", "load_skill", "read_skill_resource"} <= tools

                async def call(name, **arguments):
                    result = await session.call_tool(name, {"session_id": "mcp-smoke", **arguments})
                    assert not result.isError, result
                    payload = result.structuredContent
                    if payload is None:
                        payload = json.loads(result.content[0].text)
                    checks.append({"tool": name, "status": payload["status"]})
                    return payload

                assert (await call("search_skills", query="Markdown"))[
                    "status"
                ] == "catalog_not_ready"
                inventory = await call("inventory_skills", refresh=True)
                item = inventory["items"][0]
                identity = {"skill_id": item["id"], "version": item["version"]}
                assert (await call("inspect_source", **identity))["source_is_untrusted_data"]
                metadata = json.loads((ROOT / "examples" / "markdown-candidate.json").read_text())
                assert (await call("submit_candidate", **identity, candidate=metadata))[
                    "status"
                ] == "ok"
                assert (await call("publish_catalog"))["published"] == 1
                found = await call("search_skills", query="controllare Markdown")
                assert found["status"] == "ok"
                reference = found["candidates"][0]["reference"]
                assert (await call("load_skill", reference=reference))["status"] == "ok"
                resource = await call(
                    "read_skill_resource", reference=reference, file="references/checklist.md"
                )
                assert "FSR-MARKDOWN-CHECK-01" in resource["content"]
                assert (
                    await call("read_skill_resource", reference=reference, file="../outside.txt")
                )["status"] == "policy_denied"
                assert (await call("search_skills", query="nomatchunique98765"))[
                    "status"
                ] == "no_match"
                diagnostics = await call("catalog_diagnostics")
                assert diagnostics["mode"] == "assistance"
                assert diagnostics["session_spent_estimate"] < 12000
                forbidden = {**metadata, "enabled": True}
                rejected = await call("submit_candidate", **identity, candidate=forbidden)
                assert rejected["status"] == "needs_revision"
                assert (await call("submit_candidate", **identity, candidate=metadata))[
                    "status"
                ] == "limit_exceeded"
        return {
            "status": "ok",
            "transport": "stdio",
            "tools": len(tools),
            "checks": checks,
            "claude_code": "not_tested_by_this_script",
        }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--installed", action="store_true", help="Use installed wheel, not source")
    print(json.dumps(anyio.run(smoke, parser.parse_args().installed), ensure_ascii=False))
