"""Human-gated CLI for validating, approving, and reporting the 100/20 evaluation."""

import argparse
import json
from pathlib import Path

from fastskillretrieval.evaluation import (
    EvaluationError,
    approve_proposal,
    evaluate_results,
    proposal_fingerprint,
    validate_proposal,
    write_json_new,
)


def _read(path: Path) -> dict:
    with path.open("rb") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise EvaluationError("json_object_required")
    return value


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Deterministic, human-gated capability evaluation")
    commands = result.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate")
    validate.add_argument("--proposal", type=Path, required=True)
    approve = commands.add_parser("approve")
    approve.add_argument("--proposal", type=Path, required=True)
    approve.add_argument("--output", type=Path, required=True)
    approve.add_argument("--operator", required=True)
    approve.add_argument("--note", required=True)
    measure = commands.add_parser("measure")
    measure.add_argument("--proposal", type=Path, required=True)
    measure.add_argument("--results", type=Path, required=True)
    measure.add_argument("--output", type=Path, required=True)
    return result


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    try:
        proposal = _read(args.proposal)
        if args.command == "validate":
            validate_proposal(proposal)
            output = {
                "status": "valid",
                "proposal_fingerprint": proposal_fingerprint(proposal),
                "proposal_status": proposal["status"],
            }
        elif args.command == "approve":
            output = approve_proposal(proposal, operator=args.operator, note=args.note)
            write_json_new(args.output, output)
            output = {
                "status": "operator_recorded",
                "proposal_fingerprint": output["approval"]["review_fingerprint"],
                "corpus_fingerprint": output["approval"]["corpus_fingerprint"],
            }
        else:
            output = evaluate_results(proposal, _read(args.results))
            write_json_new(args.output, output)
            output = {"status": "written", "proposal_fingerprint": output["proposal_fingerprint"]}
    except (EvaluationError, OSError, ValueError, json.JSONDecodeError) as error:
        output = {"status": "rejected", "reason": str(error)}
        print(json.dumps(output, ensure_ascii=False, sort_keys=True))
        return 2
    print(json.dumps(output, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
