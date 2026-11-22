"""Explicit operator interface for offline plans and budgeted Claude experiments."""

import argparse
import hashlib
import json
import os
import shutil
import signal
import subprocess
import time
from pathlib import Path

from fastskillretrieval.client_observations import parse_client_events
from fastskillretrieval.client_profiles import (
    build_command,
    guarded_environment,
    materialize_profile,
    verify_preflight,
)
from fastskillretrieval.experiment import (
    ExperimentError,
    canonical,
    fingerprint,
    validate_protocol,
)
from fastskillretrieval.experiment_runner import RunStore, plan_run, write_new

C_INSTRUCTION = (
    "Use FastSkillRetrieval to search for relevant capabilities, prepare their use, and load "
    "the complete manuals before applying their instructions. Return a declarative answer. "
    "Do not claim a skill was applied without task evidence."
)
TASK_INSTRUCTION = "Return a declarative answer. Do not execute code or access the network."


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def execution_config(mcp_config, descriptors):
    return {
        "mcp_config": mcp_config,
        "descriptors": descriptors,
        "C_instruction": C_INSTRUCTION,
        "task_instruction": TASK_INSTRUCTION,
        "task_boundary": "declarative_no_filesystem_tools",
    }


def _executable(value):
    found = shutil.which(str(value))
    if not found:
        raise ExperimentError("client_binary_missing")
    path = Path(found).resolve()
    # A batch wrapper requires cmd.exe and shell parsing. Require an actual executable.
    if path.suffix.casefold() in (".cmd", ".bat", ".ps1"):
        raise ExperimentError("client_requires_native_executable_not_shell_wrapper")
    return path


def _version(binary):
    result = subprocess.run(
        [str(binary), "--version"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
        check=False,
        env=guarded_environment(),
    )
    if result.returncode:
        raise ExperimentError("client_version_unavailable")
    return result.stdout.strip()


def _capture(command, cwd, timeout, *, local_only):
    """Capture a client without shell; stop its process tree after a supervisor timeout."""
    start = time.monotonic()
    environment = guarded_environment() if local_only else dict(os.environ)
    environment["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] = "1"
    process = subprocess.Popen(
        command,
        cwd=cwd,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        start_new_session=os.name != "nt",
    )
    timed_out = False
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except (subprocess.TimeoutExpired, KeyboardInterrupt):
        timed_out = True
        try:
            if os.name == "nt":
                taskkill = (
                    Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32/taskkill.exe"
                )
                subprocess.run(
                    [str(taskkill), "/PID", str(process.pid), "/T", "/F"],
                    capture_output=True,
                    timeout=5,
                    check=False,
                )
            else:
                os.killpg(process.pid, signal.SIGKILL)
        except (OSError, subprocess.SubprocessError):
            pass
        finally:
            try:
                process.kill()
            except OSError:
                pass
        try:
            stdout, stderr = process.communicate(timeout=5)
        except subprocess.TimeoutExpired as error:
            stdout = error.output or ""
            stderr = error.stderr or ""
            if isinstance(stdout, bytes):
                stdout = stdout.decode("utf-8", errors="replace")
            if isinstance(stderr, bytes):
                stderr = stderr.decode("utf-8", errors="replace")
            process.stdout.close()
            process.stderr.close()
    events, partial = [], False
    for line in stdout.splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
            if isinstance(event, dict):
                events.append(event)
            else:
                partial = True
        except json.JSONDecodeError:
            partial = True
    return {
        "events": events,
        "returncode": process.returncode,
        "timed_out": timed_out,
        "partial": partial,
        "duration_seconds": time.monotonic() - start,
        "stdout_sha256": hashlib.sha256(stdout.encode()).hexdigest(),
        "stderr_sha256": hashlib.sha256(stderr.encode()).hexdigest(),
    }


def _zero_use(events):
    results = [e for e in events if e.get("type") == "result"]
    if len(results) != 1:
        return False
    row = results[0]
    usage = row.get("usage", {})
    return (
        row.get("num_turns") == 0
        and row.get("duration_api_ms") == 0
        and row.get("total_cost_usd") == 0
        and row.get("modelUsage") == {}
        and all(
            type(usage.get(k)) in (float, int) and usage[k] == 0
            for k in (
                "input_tokens",
                "output_tokens",
                "cache_read_input_tokens",
                "cache_creation_input_tokens",
            )
        )
    )


def minimize_events(events):
    """Keep replayable task/manual data and drop opaque client metadata.

    Evaluation content is stored only in the experiment bundle. This minimization
    does not certify an artifact for external sharing or prove that it is secret-free.
    """
    allowed = {
        "type",
        "subtype",
        "uuid",
        "session_id",
        "model",
        "claude_code_version",
        "tools",
        "skills",
        "mcp_servers",
        "message",
        "result",
        "usage",
        "modelUsage",
        "is_error",
        "duration_ms",
        "duration_api_ms",
        "num_turns",
        "total_cost_usd",
        "local_command",
    }
    result = []
    for event in events:
        row = {key: value for key, value in event.items() if key in allowed}
        if isinstance(row.get("message"), dict):
            row["message"] = {
                key: value
                for key, value in row["message"].items()
                if key in ("role", "content", "model", "id", "usage")
            }
        result.append(row)
    return result


def _probe(profile, binary, pinned_version):
    capture = _capture(
        build_command(profile, "/context", local_only=True, executable=binary),
        profile.cwd,
        30,
        local_only=True,
    )
    result = verify_preflight(profile, capture["events"])
    if not _zero_use(capture["events"]) or capture["timed_out"] or capture["partial"]:
        result["errors"].append("local_zero_usage_not_verified")
    if result["client_version"] != pinned_version:
        result["errors"].append("client_version_pin_mismatch")
    result["status"] = "blocked" if result["errors"] else "verified"
    result["zero_usage"] = _zero_use(capture["events"])
    result["capture_sha256"] = capture["stdout_sha256"]
    return result


def _profile(protocol, arm, source_root, directory, config):
    return materialize_profile(
        arm,
        protocol["corpus"],
        source_root,
        directory,
        descriptors=config["descriptors"],
        mcp_config=config["mcp_config"],
        expected_skill_count=len(protocol["corpus"]),
    )


def preflight(protocol, output, source_root, config, executable):
    validate_protocol(protocol)
    if fingerprint(config) != protocol["config_sha256"]:
        raise ExperimentError("execution_config_hash_mismatch")
    output = Path(output)
    if output.exists():
        raise ExperimentError("preflight_directory_must_be_new")
    binary = _executable(executable)
    version = _version(binary)
    output.mkdir(parents=True)
    results = {}
    for arm in protocol["arms"]:
        profile = _profile(protocol, arm, source_root, output / arm, config)
        results[arm] = _probe(profile, binary, protocol["client"]["version"])
    result = {
        "status": "verified"
        if all(r["status"] == "verified" for r in results.values())
        else "blocked",
        "client_version_output": version,
        "arms": results,
        "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
        "config_sha256": fingerprint(config),
        "protocol_sha256": fingerprint(protocol),
        "model_calls": 0 if all(r["zero_usage"] for r in results.values()) else None,
        "catalog_A_to_C": None,
        "catalog_B_to_C": None,
    }
    write_new(output / "preflight.json", result)
    return result


def execute_queue(store, source_root, config, executable, *, retry=None):
    validate_protocol(store.protocol, for_execution=True)
    if fingerprint(config) != store.protocol["config_sha256"]:
        raise ExperimentError("execution_config_hash_mismatch")
    binary = _executable(executable)
    # No default endpoint or auth override is applied to real execution.
    _version(binary)
    store.recover_interrupted()

    def client(trial, payload):
        directory = store.path / "trials" / trial["trial_id"] / f"attempt-{trial['attempt']:04d}"
        directory.mkdir(parents=True)
        profile = _profile(store.protocol, trial["arm"], source_root, directory / "client", config)
        check = _probe(profile, binary, store.protocol["client"]["version"])
        write_new(directory / "preflight.json", check)
        if check["status"] != "verified":
            return {"state": "invalid", "cost_usd": 0, "error": "client_preflight_blocked"}
        prompt = TASK_INSTRUCTION + "\n\n" + payload["query"]
        if trial["arm"] == "C_FSR":
            prompt = C_INSTRUCTION + "\n\n" + prompt
        command = build_command(profile, prompt, store.protocol["budget"], executable=binary)
        # Model pin is explicit rather than inheriting a mutable client default.
        command[1:1] = ["--model", store.protocol["client"]["model"]]
        write_new(
            directory / "invocation.json",
            {
                "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
                "profile_sha256": profile.profile_sha256,
                "payload_sha256": fingerprint(payload),
                "prompt_sha256": fingerprint(prompt),
                "argv": [*command[:-1], "<trial-payload-redacted>"],
                "instruction_C_bytes": len(C_INSTRUCTION.encode())
                if trial["arm"] == "C_FSR"
                else 0,
            },
        )
        capture = _capture(
            command, profile.cwd, store.protocol["budget"]["timeout_seconds"], local_only=False
        )
        captured_events = minimize_events(capture["events"])
        with (directory / "events.jsonl").open("xb") as stream:
            for event in captured_events:
                stream.write(canonical(event) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        parsed = parse_client_events(
            captured_events, corpus=store.protocol["corpus"], run_id=store.path.name
        )
        # Raw model text and tool arguments are deliberately not copied into the journal.
        write_new(directory / "receipts.json", parsed)
        init = verify_preflight(profile, capture["events"])
        final = next((e for e in reversed(capture["events"]) if e.get("type") == "result"), {})
        write_new(
            directory / "task-output.json",
            {
                "result": final.get("result"),
                "stdout_sha256": capture["stdout_sha256"],
                "stderr_sha256": capture["stderr_sha256"],
                "grading_status": "awaiting_independent_review",
            },
        )
        state, error = "succeeded", None
        if capture["timed_out"]:
            state, error = "timed_out", "supervisor_timeout_inflight_cost_may_be_unknown"
        elif capture["returncode"] or final.get("is_error"):
            state, error = "failed", "client_failed"
        elif capture["partial"] or not final:
            state, error = "unknown", "partial_or_missing_terminal_output"
        elif (
            init["status"] != "verified"
            or init["client_version"] != store.protocol["client"]["version"]
            or parsed["client"]["model"] != store.protocol["client"]["model"]
        ):
            state, error = "invalid", "real_client_surface_mismatch"
        return {
            "state": state,
            "error": error,
            "cost_usd": parsed["cost_usd"],
            "usage": parsed["usage"],
            "duration_seconds": capture["duration_seconds"],
            "selected_skill_ids": parsed["selected"],
            "loaded_skill_ids": parsed["loaded"] if trial["arm"] != "A_NATIVE" else None,
            "applied_skill_ids": None,
            "task_success": None,
            "catalog_measurement": None,
        }

    prior = {a["trial_id"] for a in store.attempts()}
    targets = (
        [retry] if retry else [t["trial_id"] for t in store.schedule if t["trial_id"] not in prior]
    )
    for trial_id in targets:
        observation = store.execute(trial_id, client, retry=bool(retry))
        if observation["state"] != "succeeded" or observation["cost_usd"] is None:
            break  # Operator must inspect costs/receipts before the queue can continue.
    return store.report()


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    sub = result.add_subparsers(dest="action", required=True)
    for name in ("validate", "plan", "preflight", "run"):
        command = sub.add_parser(name)
        command.add_argument("--protocol", type=Path, required=True)
        if name != "validate":
            command.add_argument("--output", type=Path, required=True)
        if name in ("preflight", "run"):
            command.add_argument("--source-root", type=Path, required=True)
            command.add_argument("--config", type=Path, required=True)
            command.add_argument("--executable", default="claude")
        if name == "preflight":
            command.add_argument("--local-only", action="store_true", required=True)
    resume = sub.add_parser("resume")
    resume.add_argument("--run", type=Path, required=True)
    resume.add_argument("--retry", help="Explicit failed/unknown trial ID; never implicit")
    resume.add_argument("--source-root", type=Path, required=True)
    resume.add_argument("--config", type=Path, required=True)
    resume.add_argument("--executable", default="claude")
    report = sub.add_parser("report")
    report.add_argument("--run", type=Path, required=True)
    report.add_argument("--output", type=Path, required=True)
    review = sub.add_parser("review")
    review.add_argument("--run", type=Path, required=True)
    review.add_argument("--review-file", type=Path, required=True)
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.action in ("validate", "plan", "preflight", "run"):
            protocol = _read(args.protocol)
            validate_protocol(protocol, for_execution=args.action == "run")
        if args.action == "validate":
            result = {"status": "valid", "protocol_sha256": fingerprint(protocol)}
        elif args.action == "plan":
            result = plan_run(protocol, args.output)
        elif args.action == "preflight":
            result = preflight(
                protocol, args.output, args.source_root, _read(args.config), args.executable
            )
        elif args.action in ("run", "resume"):
            if args.action == "run":
                plan_run(protocol, args.output)
            with RunStore(args.output if args.action == "run" else args.run) as store:
                result = execute_queue(
                    store,
                    args.source_root,
                    _read(args.config),
                    args.executable,
                    retry=getattr(args, "retry", None),
                )
        elif args.action == "review":
            with RunStore(args.run) as store:
                result = store.import_review(_read(args.review_file))
        else:
            with RunStore(args.run) as store:
                result = store.report()
            write_new(args.output, result)
        print(canonical(result).decode())
        return 2 if result.get("status") == "blocked" else 0
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        print(json.dumps({"status": "rejected", "reason": str(error)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
