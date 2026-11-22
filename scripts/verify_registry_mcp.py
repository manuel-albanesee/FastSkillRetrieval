"""Real SDK stdio smoke for the single registry MCP interface."""

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
    with tempfile.TemporaryDirectory(prefix="fsr-registry-mcp-", dir=ROOT / ".work") as temporary:
        work = Path(temporary)
        source = work / "sources"
        shutil.copytree(ROOT / "examples/skills", source)
        manual = source / "markdown-check/SKILL.md"
        content = (
            manual.read_text(encoding="utf-8").replace(
                "\n---\n",
                "\nmetadata:\n  origin: ECC\ntools: Read, Bash\nargument-hint: filename\n---\n",
                1,
            )
            + "\n"
            + "Prefer `assert_eq!` over `assert!`; use `!` in conventional commit types.\n"
            + "Host identifiers include `${CLAUDE_PROJECT_DIR}` and `$ARGUMENTS`.\n"
            + "Verify the complete manual, including its final section.\n" * 500
        )
        manual.write_text(content, encoding="utf-8", newline="")
        entry = ["-m", "fastskillretrieval"]
        if os.environ.get("FSR_COVERAGE") == "1":
            entry = ["-m", "coverage", "run", "--parallel-mode", *entry]
        environment = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
        if not installed:
            environment["PYTHONPATH"] = str(ROOT / "src")
        else:
            import fastskillretrieval

            assert not Path(fastskillretrieval.__file__).resolve().is_relative_to(ROOT / "src"), (
                "--installed requires an installed wheel"
            )
        parameters = StdioServerParameters(
            command=sys.executable,
            args=[
                *entry,
                "--registry",
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
                assert tools == {
                    "publish_catalog",
                    "discover_capabilities",
                    "inspect_capability_source",
                    "submit_capability_descriptor",
                    "plan_capabilities",
                    "search_capabilities",
                    "prepare_capability_use",
                    "load_capability",
                    "read_capability_resource",
                    "registry_diagnostics",
                }

                async def call(name, context="registry-mcp", **arguments):
                    result = await session.call_tool(name, {"session_id": context, **arguments})
                    assert not result.isError, f"MCP tool failed: {name}"
                    payload = result.structuredContent
                    if payload is None:
                        payload = json.loads(result.content[0].text)
                    checks.append({"tool": name, "status": payload["status"]})
                    assert payload["status"] == "ok", f"Unexpected status for {name}"
                    return payload

                inventory = await call("discover_capabilities", context="preparation")
                item = inventory["items"][0]
                identity = {"capability_id": item["id"], "source_revision": item["source_revision"]}
                inspected = await call(
                    "inspect_capability_source", context="preparation", **identity
                )
                assert inspected["source_is_untrusted_data"] is True
                candidate = json.loads((ROOT / "examples/markdown-candidate.json").read_text())
                await call(
                    "submit_capability_descriptor",
                    context="preparation",
                    **identity,
                    candidate=candidate,
                )
                published = await call("publish_catalog", context="preparation")
                assert published["published"] == 1
                found = await call("search_capabilities", query="markdown", request_id="v2-smoke")
                reference = found["candidates"][0]["reference"]
                assert reference["schema_version"] == 2
                plan = await call("plan_capabilities", reference=reference)
                assert plan["actions"][0]["action"] == "load"
                await call("prepare_capability_use", plan_id=plan["plan_id"])
                loaded = await call("load_capability", reference=reference)
                assert loaded["content"].encode("utf-8") == manual.read_bytes()
                assert len(loaded["content"].encode("utf-8")) > 4000
                resource = await call(
                    "read_capability_resource", reference=reference, file="references/checklist.md"
                )
                assert "FSR-MARKDOWN-CHECK-01" in resource["content"]
                diagnostics = await call("registry_diagnostics")
                assert diagnostics["native_activation"] == "unverified"
                assert diagnostics["graph"]["ready"] is True
                # Verify graph expansion over the actual MCP transport with no shared keywords.
                for name in ("starlight-launch", "orbital-audit"):
                    folder = source / name
                    folder.mkdir()
                    related = (
                        "## Related Skills\n- `orbital-audit`\n"
                        if name == "starlight-launch"
                        else ""
                    )
                    (folder / "SKILL.md").write_text(
                        f"---\nname: {name}\ndescription: Review local evidence.\n---\n"
                        f"Review local evidence.\n{related}",
                        encoding="utf-8",
                    )
                graph_inventory = await call("discover_capabilities", context="graph-setup")
                for item in graph_inventory["items"]:
                    if item["name"] not in {"starlight-launch", "orbital-audit"}:
                        continue
                    descriptor = json.loads(json.dumps(candidate))
                    descriptor["aliases"] = [item["name"]]
                    descriptor["capabilities"][0]["evidence"] = {
                        "file": "SKILL.md",
                        "quote": "Review local evidence.",
                    }
                    await call(
                        "submit_capability_descriptor",
                        context="graph-setup",
                        capability_id=item["id"],
                        source_revision=item["source_revision"],
                        candidate=descriptor,
                    )
                await call("publish_catalog", context="graph-setup")
                expanded = await call(
                    "search_capabilities",
                    context="graph-reader",
                    query="starlight-launch",
                    request_id="graph-smoke",
                )
                assert any(
                    c.get("retrieval", {}).get("method") == "graph" for c in expanded["candidates"]
                )
        return {
            "status": "ok",
            "transport": "stdio",
            "tools": len(tools),
            "checks": checks,
            "native_execution": "not_implemented",
            "claude_code": "not_tested_by_this_script",
        }


async def bounded_smoke(installed: bool = False):
    with anyio.fail_after(45):
        return await smoke(installed)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--installed", action="store_true")
    try:
        report = anyio.run(bounded_smoke, parser.parse_args().installed)
    except TimeoutError:
        report = {"status": "timeout", "transport": "stdio", "reason": "mcp_smoke_deadline"}
    print(json.dumps(report, ensure_ascii=False))
    raise SystemExit(0 if report["status"] == "ok" else 2)
