#!/usr/bin/env python3
"""Score the pipeline against dataset/sample_output.csv.

Reports the four metrics the challenge asks for:

* case-level deletion-status accuracy
* step-level micro precision / recall / F1 on
  (phase, system_id, resource_id, operation, state)
* field-level accuracy on evidence_id (over steps that match on the tuple)
* sequence accuracy (exact ordered match of the whole steps_json array)

    python3 code/evaluation/evaluate.py            # summary
    python3 code/evaluation/evaluate.py --errors   # per-case diff
"""

import argparse
import csv
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List, Tuple

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "code"))

from pipeline.main_stub import run_requests         # noqa: E402


def load_expected() -> Dict[str, dict]:
    path = ROOT / "dataset" / "sample_output.csv"
    with path.open(newline="", encoding="utf-8") as fh:
        return {r["case_id"]: r for r in csv.DictReader(fh)}


def parse_steps(raw: str) -> List[dict]:
    try:
        parsed = json.loads(raw or "[]")
    except Exception:
        return []
    return parsed if isinstance(parsed, list) else []


def step_tuple(step: dict) -> Tuple:
    return (step.get("phase"), step.get("system_id"), step.get("resource_id"),
            step.get("operation"), step.get("state"))


def compare(predicted: Dict[str, dict], expected: Dict[str, dict]):
    status_ok = 0
    tp = fp = fn = 0
    field_total = 0
    field_ok = 0
    sequence_ok = 0
    per_case = {}

    for case_id in sorted(expected):
        exp = expected[case_id]
        got = predicted.get(case_id, {})
        if got.get("deletion_status") == exp.get("deletion_status"):
            status_ok += 1

        exp_steps = parse_steps(exp.get("steps_json", ""))
        got_steps = parse_steps(got.get("steps_json", ""))
        exp_t = [step_tuple(s) for s in exp_steps]
        got_t = [step_tuple(s) for s in got_steps]

        exp_counter = Counter(exp_t)
        got_counter = Counter(got_t)
        matched = sum((exp_counter & got_counter).values())
        tp += matched
        fp += sum(got_counter.values()) - matched
        fn += sum(exp_counter.values()) - matched

        # evidence_id accuracy over tuple-aligned steps
        exp_by_tuple = defaultdict(list)
        for step in exp_steps:
            exp_by_tuple[step_tuple(step)].append(step)
        for step in got_steps:
            key = step_tuple(step)
            bucket = exp_by_tuple.get(key)
            if not bucket:
                continue
            field_total += 1
            if any(step.get("evidence_id") == other.get("evidence_id") for other in bucket):
                field_ok += 1

        if exp_t == got_t:
            sequence_ok += 1

        per_case[case_id] = {
            "status_ok": got.get("deletion_status") == exp.get("deletion_status"),
            "expected_status": exp.get("deletion_status"),
            "got_status": got.get("deletion_status"),
            "extra": [t for t in (got_counter - exp_counter).elements()],
            "missing": [t for t in (exp_counter - got_counter).elements()],
            "sequence_ok": exp_t == got_t,
        }

    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "n": len(expected),
        "status_accuracy": status_ok / len(expected) if expected else 0.0,
        "step_precision": precision,
        "step_recall": recall,
        "step_f1": f1,
        "tp": tp, "fp": fp, "fn": fn,
        "field_accuracy": field_ok / field_total if field_total else 0.0,
        "field_total": field_total,
        "field_ok": field_ok,
        "sequence_accuracy": sequence_ok / len(expected) if expected else 0.0,
        "per_case": per_case,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--errors", action="store_true")
    args = parser.parse_args(argv)

    results = run_requests("sample_deletion_requests.csv")
    predicted = {r.case_id: r.as_row() for r in results}
    expected = load_expected()
    report = compare(predicted, expected)

    print(f"cases                 {report['n']}")
    print(f"status accuracy       {report['status_accuracy']:.4f}")
    print(f"step precision        {report['step_precision']:.4f}  "
          f"(tp={report['tp']} fp={report['fp']} fn={report['fn']})")
    print(f"step recall           {report['step_recall']:.4f}")
    print(f"step F1               {report['step_f1']:.4f}")
    print(f"field (evidence) acc  {report['field_accuracy']:.4f}  "
          f"({report['field_ok']}/{report['field_total']})")
    print(f"sequence accuracy     {report['sequence_accuracy']:.4f}")

    if args.errors:
        print("\n--- per-case ---")
        for case_id, info in sorted(report["per_case"].items()):
            flag = "ok " if info["status_ok"] and not info["extra"] and not info["missing"] else "BAD"
            print(f"{case_id} [{flag}] status {info['got_status']} vs {info['expected_status']}")
            for item in info["missing"]:
                print(f"    MISSING {item}")
            for item in info["extra"]:
                print(f"    EXTRA   {item}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
