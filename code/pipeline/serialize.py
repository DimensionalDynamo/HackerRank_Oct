"""Output serialisation plus the contract checks the submission must satisfy."""

import csv
from pathlib import Path
from typing import Iterable, List, Sequence

from .model import (
    OPERATION_ORDER, PHASE_OF_OPERATION, STATE_OF_OPERATION, CaseResult,
)

HEADER = ["case_id", "deletion_status", "steps_json"]


def case_sort_key(case_id: str):
    """Order ``case_N`` numerically, not lexicographically.

    The specification says rows are sorted by ``case_id``; the request file
    itself runs case_33 … case_550, so 100 must follow 99 rather than lead the
    file.  A trailing integer is compared numerically, anything else falls back
    to the plain string so unknown ids still order deterministically.
    """
    prefix, _, digits = case_id.rpartition("_")
    if digits.isdigit():
        return (prefix, 0, int(digits), "")
    return (case_id, 1, 0, case_id)
ALLOWED_STATUS = {"COMPLETE", "INCOMPLETE", "NOT_APPLICABLE"}
ALLOWED_STATES = set(STATE_OF_OPERATION.values())


def write_output(path: Path, results: Sequence[CaseResult]) -> None:
    ordered = sorted(results, key=lambda r: case_sort_key(r.case_id))
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=HEADER, lineterminator="\n")
        writer.writeheader()
        for result in ordered:
            writer.writerow(result.as_row())


def validate(results: Sequence[CaseResult], expected_cases: Iterable[str]) -> List[str]:
    """Return a list of contract violations (empty list == valid output)."""
    problems: List[str] = []
    expected = set(expected_cases)
    seen = set()

    for result in results:
        if result.case_id in seen:
            problems.append(f"{result.case_id}:duplicate_row")
        seen.add(result.case_id)
        if result.status not in ALLOWED_STATUS:
            problems.append(f"{result.case_id}:bad_status:{result.status}")
        if result.status == "NOT_APPLICABLE" and result.steps:
            problems.append(f"{result.case_id}:na_with_steps")
        if result.status == "COMPLETE" and not any(
                s.phase == "VERIFICATION" for s in result.steps):
            problems.append(f"{result.case_id}:complete_without_verification")
        if result.status == "INCOMPLETE" and result.steps:
            # An INCOMPLETE row with *no* steps is the documented
            # "nothing attributable at this audit instant" verdict: the
            # request is a deletion request, so it cannot be NOT_APPLICABLE,
            # and it cannot be COMPLETE because no verification can be cited.
            # Every other INCOMPLETE row must carry findings and remediation.
            if not any(s.phase == "FINDING" for s in result.steps):
                problems.append(f"{result.case_id}:incomplete_without_finding")
            if not any(s.phase == "REMEDIATION" for s in result.steps):
                problems.append(f"{result.case_id}:incomplete_without_remediation")

        previous = None
        for index, step in enumerate(result.steps, start=1):
            if step.step_no != index:
                problems.append(f"{result.case_id}:step_no_gap:{step.step_no}")
            if step.operation not in OPERATION_ORDER:
                problems.append(f"{result.case_id}:bad_operation:{step.operation}")
                continue
            if PHASE_OF_OPERATION[step.operation] != step.phase:
                problems.append(f"{result.case_id}:phase_mismatch:{step.operation}")
            if STATE_OF_OPERATION[step.operation] != step.state:
                problems.append(f"{result.case_id}:state_mismatch:{step.operation}")
            if step.phase in ("FINDING",) and not step.evidence_id:
                problems.append(f"{result.case_id}:finding_without_evidence")
            if result.status == "INCOMPLETE" and step.phase != "FINDING" and step.evidence_id:
                problems.append(f"{result.case_id}:remediation_with_evidence")
            if result.status == "COMPLETE" and not step.evidence_id:
                problems.append(f"{result.case_id}:complete_step_without_evidence")
            key = step.sort_key()
            if previous is not None and key < previous:
                problems.append(f"{result.case_id}:out_of_order:{step.step_no}")
            previous = key

    for missing in sorted(expected - seen):
        problems.append(f"{missing}:missing_row")
    for extra in sorted(seen - expected):
        problems.append(f"{extra}:unexpected_row")
    return problems
