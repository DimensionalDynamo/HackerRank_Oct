#!/usr/bin/env python3
"""Hand-built evaluation cases that probe specific reasoning, not scores.

Each case finds a real request that exhibits a phenomenon, runs the real
pipeline on it, and asserts what must be true and why.  Finding the example by
predicate (rather than naming a case id) means the cases keep working as the
dataset changes and keeps the codebase free of case-id lookups.

    python3 code/evaluation/custom_cases.py
"""

import csv
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "code"))

from pipeline import dataset                       # noqa: E402
from pipeline.evidence import EvidenceIndex        # noqa: E402
from pipeline.identity import IdentityIndex        # noqa: E402
from pipeline.intent import classify               # noqa: E402
from pipeline.inventory import InventoryBuilder    # noqa: E402
from pipeline.main_stub import run_requests        # noqa: E402
from pipeline.planner import Planner               # noqa: E402
from pipeline.policy import Policy                 # noqa: E402

CASES = []


def case(fn):
    CASES.append(fn)
    return fn


class Context:
    def __init__(self) -> None:
        self.index = EvidenceIndex.build()
        self.paths = {e.file_id: e.file_path for e in dataset.load_evidence_files()}
        self.captured = {e.file_id: e.captured_at for e in dataset.load_evidence_files()}
        self.identities = IdentityIndex()
        self.builder = InventoryBuilder(self.index)
        self.planner = Planner(Policy())
        self.requests = [r for r in dataset.load_requests()
                         if classify(r.request_text).authorises_deletion]
        self._cache = {}

    def inventory(self, request):
        if request.case_id not in self._cache:
            subject, _how = self.identities.subject_for_email(request.email)
            inventory = (self.builder.build(subject, request.audit_at)
                         if subject else None)
            self._cache[request.case_id] = inventory
        return self._cache[request.case_id]

    def find(self, predicate):
        for request in self.requests:
            inventory = self.inventory(request)
            if inventory is None:
                continue
            hit = predicate(request, inventory)
            if hit:
                return request, inventory, hit
        return None, None, None


# ---------------------------------------------------------------------------
# 1. Replication drift: a profile that survives only in a replica table.
# ---------------------------------------------------------------------------
@case
def replica_drift(ctx):
    def predicate(request, inventory):
        for resource in inventory.resources.values():
            path = ctx.paths.get(resource.evidence_id, "")
            if "failover_replica" in path:
                same_id_in_primary = any(
                    o.resource_id == resource.resource_id
                    and "primary" in ctx.paths.get(o.evidence_id, "")
                    for o in ctx.index.all_observations)
                if resource.state == "PRESENT" and not same_id_in_primary:
                    return resource
        return None

    request, inventory, resource = ctx.find(predicate)
    if resource is None:
        return {"name": "replica_drift", "pass": None,
                "note": "no replica-only record in this dataset"}
    result = ctx.planner.plan(request.case_id, inventory)
    return {
        "name": "replica_drift",
        "question": ("Is a record that survives only in a failover replica reported "
                     "as remaining instead of trusting the primary table?"),
        "expected": {"resource": resource.resource_id, "state": "PRESENT",
                     "status": "INCOMPLETE"},
        "produced": {"resource": resource.resource_id, "state": resource.state,
                     "status": result.status},
        "pass": resource.state == "PRESENT" and result.status == "INCOMPLETE",
        "why": ("the replica table still holds the row while the primary table does "
                "not; a primary-only read would call this deletion complete."),
    }


# ---------------------------------------------------------------------------
# 2. Deletion verified: a 404 profile lookup proves the account is gone and the
#    case must cite that readback, not the workflow's "Complete" claim.
# ---------------------------------------------------------------------------
@case
def absent_account_verification(ctx):
    def predicate(request, inventory):
        account = inventory.resources.get(inventory.account_resource or "")
        if account and account.state == "ABSENT" and account.evidence_id:
            return account
        return None

    request, inventory, account = ctx.find(predicate)
    if account is None:
        return {"name": "absent_account_verification", "pass": None,
                "note": "no absent account resolved in this dataset"}
    result = ctx.planner.plan(request.case_id, inventory)
    expected = ("VERIFICATION", "VERIFY_RECORD_ABSENT")
    found = [(s.phase, s.operation) for s in result.steps
             if s.resource_id == account.resource_id]
    return {
        "name": "absent_account_verification",
        "question": ("When a readback shows the profile is gone, is the absence "
                     "proved by a cited verification step?"),
        "expected": {"step": list(expected), "evidence": "non-empty"},
        "produced": {"resource": account.resource_id, "evidence": account.evidence_id,
                     "steps_for_resource": found},
        "pass": expected in found and bool(account.evidence_id),
        "why": ("the 404 / rows=0 readback at or before audit_at is the only "
                "admissible proof that the account no longer exists."),
    }


# ---------------------------------------------------------------------------
# 3. Tombstone: over-sharing reference row needs redaction, not deletion.
# ---------------------------------------------------------------------------
@case
def tombstone_redaction(ctx):
    def predicate(request, inventory):
        for resource in inventory.resources.values():
            if resource.category == "ACCOUNT_TOMBSTONE" and resource.state == "PRESENT":
                return resource
        return None

    request, inventory, resource = ctx.find(predicate)
    if resource is None:
        return {"name": "tombstone_redaction", "pass": None,
                "note": "no tombstone with residual identifiers"}
    result = ctx.planner.plan(request.case_id, inventory)
    ops = [s.operation for s in result.steps if s.resource_id == resource.resource_id]
    return {
        "name": "tombstone_redaction",
        "question": ("Is an over-sharing tombstone redacted while its stable "
                     "reference is kept?"),
        "expected": {"operations": ["REPORT_EXPOSED_PII", "REDACT_RECORD",
                                    "VERIFY_RECORD_REDACTED"]},
        "produced": {"resource": resource.resource_id, "operations": ops},
        "pass": ops == ["REPORT_EXPOSED_PII", "REDACT_RECORD",
                        "VERIFY_RECORD_REDACTED"],
        "why": ("rule_15 keeps the tombstone but strips direct identifiers, so the "
                "remedy is redaction and the check is VERIFY_RECORD_REDACTED."),
    }


# ---------------------------------------------------------------------------
# 4. Retained audit trail must be verified present, never deleted.
# ---------------------------------------------------------------------------
@case
def audit_retention(ctx):
    def predicate(request, inventory):
        for resource in inventory.resources.values():
            if resource.category == "SECURITY_AUDIT_LOG":
                return resource
        return None

    request, inventory, resource = ctx.find(predicate)
    result = ctx.planner.plan(request.case_id, inventory)
    ops = [s.operation for s in result.steps if s.resource_id == resource.resource_id]
    return {
        "name": "audit_retention",
        "question": "Is the retained audit trail verified present and never deleted?",
        "expected": {"operations": ["VERIFY_RECORD_PRESENT"]},
        "produced": {"resource": resource.resource_id, "operations": ops,
                     "evidence": resource.evidence_id},
        "pass": ops == ["VERIFY_RECORD_PRESENT"] and bool(resource.evidence_id),
        "why": ("rule_12 requires the security audit log to be retained, so the only "
                "correct step is a positive verification with a citation."),
    }


# ---------------------------------------------------------------------------
# 5. Multilingual non-deletion intents, injected so the classifier is exercised
#    on text that does not exist in the corpus.
# ---------------------------------------------------------------------------
@case
def multilingual_opt_out(ctx):
    rows = [
        ("synthetic_01", "do.not.delete.me@example.com",
         "Bitte löschen Sie mein Konto nicht. Ich brauche es weiterhin für meine Bestellungen."),
        ("synthetic_02", "correo.solo@example.com",
         "Solo quiero corregir mi apellido en el perfil. No soliciten la eliminación de la cuenta."),
        ("synthetic_03", "adresse.seule@example.com",
         "Je souhaite uniquement télécharger mes factures. Ne supprimez pas mon compte."),
    ]
    with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False,
                                     encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["case_id", "email", "requested_at", "reported_status",
                         "reported_at", "audit_at", "request_text"])
        for case_id, email, text in rows:
            writer.writerow([case_id, email, "2026-09-01T00:00:00Z", "NOT_STARTED",
                             "2026-09-02T00:00:00Z", "2026-09-15T00:00:00Z", text])
        path = handle.name

    results = {r.case_id: r.status for r in run_requests(
        path, evidence=ctx.index, identities=ctx.identities)}
    return {
        "name": "multilingual_opt_out",
        "question": ("Do German / Spanish / French keep-my-account requests refuse "
                     "authorisation?"),
        "expected": {cid: "NOT_APPLICABLE" for cid, _, _ in rows},
        "produced": results,
        "pass": all(results.get(cid) == "NOT_APPLICABLE" for cid, _, _ in rows),
        "why": ("German is absent from the corpus, so this checks the negation rules "
                "generalise instead of memorising template text."),
    }


# ---------------------------------------------------------------------------
# 6. Time cutoff: the same question asked before a snapshot was captured must
#    not be answered with that snapshot.
# ---------------------------------------------------------------------------
@case
def time_cutoff(ctx):
    def predicate(request, inventory):
        for resource in inventory.resources.values():
            return resource
        return None

    request, inventory, _ = ctx.find(predicate)
    subject = ctx.identities.subject_for_email(request.email)[0]
    early = ctx.builder.build(subject, "2026-01-29T12:00:00Z")
    produced = sorted({r.evidence_id for r in early.resources.values() if r.evidence_id})
    return {
        "name": "time_cutoff",
        "question": "Is evidence captured after audit_at excluded from an earlier audit?",
        "expected": {"all cited files captured at or before audit_at": True},
        "produced": {"audit_at": "2026-01-29T12:00:00Z", "resources": len(early.resources),
                     "cited": produced},
        "pass": all(ctx.captured[f] <= "2026-01-29T12:00:00Z" for f in produced),
        "why": ("the audit describes the state at audit_at; a file captured later "
                "cannot testify about it, even if its contents are older."),
    }


def main() -> int:
    ctx = Context()
    results = []
    failures = 0
    for fn in CASES:
        result = fn(ctx)
        results.append(result)
        if result.get("pass") is None:
            mark = "SKIP"
        elif result["pass"]:
            mark = "PASS"
        else:
            mark = "FAIL"
            failures += 1
        print(f"[{mark}] {result['name']}")
        if "question" in result:
            print(f"        Q: {result['question']}")
            print(f"        expected: {json.dumps(result['expected'], ensure_ascii=False)}")
            print(f"        produced: {json.dumps(result['produced'], ensure_ascii=False)}")
        else:
            print(f"        {result.get('note')}")
    total = len([r for r in results if r.get("pass") is not None])
    print(f"\n{total - failures}/{total} custom cases passed "
          f"({len(results) - total} skipped)")
    (ROOT / "code" / "evaluation" / "custom_case_results.json").write_text(
        json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
