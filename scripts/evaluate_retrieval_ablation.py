"""Offline historical ablation; copies registry state before writing diagnostic ledgers."""

import argparse
import json
import shutil
from pathlib import Path

from fastskillretrieval.retrieval_evaluation import evaluate_ablation


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--state", type=Path, required=True)
    p.add_argument("--source", type=Path, required=True)
    p.add_argument("--dataset", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    state = args.output / "registry-copy"
    shutil.copytree(args.state, state)
    dataset = json.loads(args.dataset.read_text(encoding="utf-8"))
    report = evaluate_ablation(
        state, args.source, dataset.get("requests", dataset.get("cases", []))
    )
    (args.output / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
