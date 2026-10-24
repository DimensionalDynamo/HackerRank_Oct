#!/usr/bin/env python3
"""Ghost Data Still Exists - deletion audit agent.

    python3 code/main.py                # audit dataset/deletion_requests.csv
    python3 code/main.py --dataset sample_deletion_requests.csv \
                         --out /tmp/sample_pred.csv

Deterministic: no network, no clock, no randomness.  The same inputs always
produce a byte-identical output.csv.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from pipeline import dataset, serialize          # noqa: E402
from pipeline.main_stub import run_requests      # noqa: E402


def run(request_file: str = "deletion_requests.csv"):
    return run_requests(request_file)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", default="deletion_requests.csv",
                        help="request file inside dataset/")
    parser.add_argument("--out", default=None, help="output CSV path")
    parser.add_argument("--validate", action="store_true",
                        help="run the output contract checks and exit non-zero on failure")
    args = parser.parse_args(argv)

    results = run(args.dataset)

    out_path = Path(args.out) if args.out else dataset.repo_root() / "output.csv"
    serialize.write_output(out_path, results)

    answers = {r.case_id for r in dataset.load_requests(args.dataset)}
    problems = serialize.validate(results, answers)

    counts = {}
    for result in results:
        counts[result.status] = counts.get(result.status, 0) + 1
    print(f"wrote {out_path} rows={len(results)} " +
          " ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    if problems:
        print(f"contract violations: {len(problems)}")
        for problem in problems[:20]:
            print("  -", problem)
        return 1 if args.validate else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
