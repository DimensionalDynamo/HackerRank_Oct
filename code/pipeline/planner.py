"""Turn a resolved inventory into the canonical ordered step list."""
from __future__ import annotations

from typing import List, Optional, Tuple

from .inventory import CaseInventory, ResourceState
from .model import (
    CaseResult, PHASE_OF_OPERATION, STATE_OF_OPERATION, Step,
    Finding, Verification,
)
from .policy import Policy


class Planner:
    def __init__(self, policy: Optional[Policy] = None) -> None:
        self.policy = policy or Policy()

    def plan(self, case_id: str, inventory: CaseInventory) -> CaseResult:
        findings: List[Finding] = []
        remediations: List[Tuple[str, str, str]] = []   # (op, system, resource)
        verifications: List[Verification] = []
        remediated_keys = set()

        for resource in sorted(inventory.resources.values(),
                               key=ResourceState.sort_key):
            assessment = self.policy.assess(resource.category, resource.state)
            if not assessment.compliant:
                findings.append(Finding(
                    operation=assessment.finding_operation or "REPORT_REMAINING_RECORD",
                    remediation=assessment.remediation_operation or "DELETE_RECORD",
                    system_id=resource.system_id,
                    resource_id=resource.resource_id,
                    evidence_id=resource.evidence_id,
                    reason=assessment.reason,
                ))
                remediated_keys.add((resource.system_id, resource.resource_id))
            if assessment.verification_operation:
                verifications.append(Verification(
                    operation=assessment.verification_operation,
                    system_id=resource.system_id,
                    resource_id=resource.resource_id,
                    evidence_id=resource.evidence_id,
                    reason=assessment.reason,
                ))

        if findings:
            for finding in findings:
                remediations.append((finding.remediation, finding.system_id,
                                     finding.resource_id))
            steps = self._build(findings, remediations, verifications,
                                incomplete=True)
            return CaseResult(case_id=case_id, status="INCOMPLETE", steps=steps)

        steps = self._build([], [], verifications, incomplete=False)
        if not steps:
            # No record could be attributed to the subject at this audit
            # instant.  Absence of evidence is not evidence of erasure: a
            # "COMPLETE" here would repeat exactly the failure the task warns
            # about, and the format forbids a COMPLETE row without a cited
            # verification step.
            return CaseResult(case_id=case_id, status="INCOMPLETE", steps=())
        return CaseResult(case_id=case_id, status="COMPLETE", steps=steps)

    # ------------------------------------------------------------------
    def _build(self, findings, remediations, verifications,
               incomplete: bool) -> Tuple[Step, ...]:
        raw: List[Step] = []
        for finding in findings:
            raw.append(Step(0, "FINDING", finding.system_id, finding.resource_id,
                            finding.operation, STATE_OF_OPERATION[finding.operation],
                            finding.evidence_id))
        for operation, system_id, resource_id in remediations:
            raw.append(Step(0, "REMEDIATION", system_id, resource_id, operation,
                            STATE_OF_OPERATION[operation], ""))
        for verification in verifications:
            raw.append(Step(0, "VERIFICATION", verification.system_id,
                            verification.resource_id, verification.operation,
                            STATE_OF_OPERATION[verification.operation],
                            "" if incomplete else verification.evidence_id))
        raw.sort(key=Step.sort_key)
        return tuple(
            Step(index + 1, step.phase, step.system_id, step.resource_id,
                 step.operation, step.state, step.evidence_id)
            for index, step in enumerate(raw)
        )
