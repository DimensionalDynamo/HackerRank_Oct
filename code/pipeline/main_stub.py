"""Programmatic entry point shared by main.py and the evaluation harness.

Keeping this in the package lets the evaluator run exactly the same code path
as the graded submission (no re-implementation of the pipeline in the tests).
"""

from typing import List

from . import dataset
from .evidence import EvidenceIndex
from .identity import IdentityIndex
from .intent import classify
from .inventory import InventoryBuilder
from .model import CaseResult
from .planner import Planner
from .policy import Policy
from .serialize import case_sort_key


def run_requests(request_file: str,
                 evidence: EvidenceIndex | None = None,
                 identities: IdentityIndex | None = None) -> List[CaseResult]:
    requests = dataset.load_requests(request_file)
    identities = identities or IdentityIndex()
    evidence = evidence or EvidenceIndex.build()
    builder = InventoryBuilder(evidence)
    planner = Planner(Policy())

    results: List[CaseResult] = []
    for request in sorted(requests, key=lambda r: case_sort_key(r.case_id)):
        decision = classify(request.request_text)
        if not decision.authorises_deletion:
            results.append(CaseResult(request.case_id, "NOT_APPLICABLE", ()))
            continue

        subject, _how = identities.subject_for_email(request.email)
        if subject is None:
            # The request authorises deletion but the actor is not in the
            # identity register, so nothing can be located, treated or
            # verified.  That is an unproven request, never a completed one.
            results.append(CaseResult(request.case_id, "INCOMPLETE", ()))
            continue

        inventory = builder.build(subject, request.audit_at)
        results.append(planner.plan(request.case_id, inventory))
    return results
