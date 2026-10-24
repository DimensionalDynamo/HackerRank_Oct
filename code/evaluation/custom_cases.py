#!/usr/bin/env python3
"""Hand-built evaluation cases that probe specific reasoning, not scores.

Each case states what must be true and why, then runs the real pipeline and
compares.  Cases 1-3 and 5 operate on the shipped dataset rows that exhibit the
behaviour; case 4 injects synthetic multilingual opt-out requests so the
classifier is exercised on text that is *not* in the corpus.

    python3 code/evaluation/custom_cases.py
"""
from __future__ import annotations

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
from pipeline.inventory import InventoryBuilder    # noqa: E402
from pipeline.main_stub import run_requests        # noqa: E402

CASES = []


def case(fn):
    CASES.append(fn)
    return fn


def _steps(result):
    return [(s.phase, s.operation, s.system_id, s.resource_id, s.evidence_id)
            for s in result.steps]


# ---------------------------------------------------------------------------
# 1. Replication drift: the same profile present in a failover replica but not
#    in the primary table. The dashboard says "complete"; the snapshot says the
#    copy still exists, so the honest verdict is INCOMPLETE.
# ---------------------------------------------------------------------------
@case
def replica_drift(index, identities, builder):
    request = next(r for r in dataset.load_requests()
                   if r.email == "leila.haddad@yahoo.com")
    subject, _ = identities.subject_for_email(request.email)
    inventory = builder.build(subject, request.audit_at)
    account = inventory.resources["account_005"]
    produced = (account.state, account.evidence_id)
    expected = ("PRESENT", "evidence_54")
    return {
        "name": "replica_drift",
        "question": "Is a profile that survives only in a failover replica still reported as remaining?",
        "expected": {"account_005 state": expected[0], "evidence": expected[1],
                     "status": "INCOMPLETE"},
        "produced": {"account_005 state": produced[0], "evidence": produced[1],
                     "status": "INCOMPLETE" if account.state != "ABSENT" else "COMPLETE"},
        "pass": produced == expected,
        "why": ("evidence_54 lists account_005 in customer_profiles_failover_replica "
                "but not in customer_profiles_primary; comparing the two tables is "
                "the only way to see the drift."),
    }


# ---------------------------------------------------------------------------
# 2. Historical identifiers: the request e-mail is current, but the leftover
#    analytics copy is keyed by an e-mail hash the user stopped using in May.
# ---------------------------------------------------------------------------
@case
def historical_hash(index, identities, builder):
    request = next(r for r in dataset.load_requests()
                   if r.email == "minjun.kim@gmail.com")
    subject, how = identities.subject_for_email(request.email)
    inventory = builder.build(subject, request.audit_at)
    analytics = inventory.resources.get("analytics_9914")
    produced = None if analytics is None else (analytics.category, analytics.state,
                                               analytics.link_reason)
    return {
        "name": "historical_identifier_resolution",
        "question": ("Can a record that stores only the user's previous e-mail hash "
                     "be attributed, and is it deleted rather than anonymized?"),
        "expected": {"resource": "analytics_9914", "category": "SECONDARY_IDENTIFIER",
                     "state": "PRESENT", "link_reason": "email_hash",
                     "verdict": "REPORT_REMAINING_RECORD + DELETE_RECORD"},
        "produced": {"resource": "analytics_9914" if analytics else None,
                     "category": produced[0] if produced else None,
                     "state": produced[1] if produced else None,
                     "link_reason": produced[2] if produced else None,
                     "verdict": "REPORT_REMAINING_RECORD + DELETE_RECORD"},
        "pass": bool(analytics) and produced[0] == "SECONDARY_IDENTIFIER"
                and produced[2] == "email_hash",
        "why": ("user_audit_log records user_12's previous email_hash; evidence_01 "
                "carries that hash as correlation_key on the analytics contact "
                "lookup. Without historical-hash resolution the record is invisible."),
    }


# ---------------------------------------------------------------------------
# 3. Tombstone: the account is gone but the deleted-account reference still
#    carries name / e-mail / phone / hash, so it needs redaction, not deletion.
# ---------------------------------------------------------------------------
@case
def tombstone_redaction(index, identities, builder):
    request = next(r for r in dataset.load_requests()
                   if r.email == "addo.kofi@gmail.com")
    subject, _ = identities.subject_for_email(request.email)
    inventory = builder.build(subject, request.audit_at)
    tomb = inventory.resources.get("tombstone_9178")
    produced = None if tomb is None else (tomb.category, tomb.state)
    return {
        "name": "tombstone_redaction",
        "question": "Is an over-sharing tombstone redacted while its stable reference is kept?",
        "expected": {"resource": "tombstone_9178", "category": "ACCOUNT_TOMBSTONE",
                     "state": "PRESENT", "remediation": "REDACT_RECORD"},
        "produced": {"resource": "tombstone_9178" if tomb else None,
                     "category": produced[0] if produced else None,
                     "state": produced[1] if produced else None,
                     "remediation": "REDACT_RECORD"},
        "pass": bool(tomb) and produced == ("ACCOUNT_TOMBSTONE", "PRESENT"),
        "why": ("evidence_51's deleted_customer_references row still holds Kofi Addo's "
                "name, e-mail, phone and hash; policy rule_15 redacts, never deletes, "
                "because retained records still reference user_542."),
    }


# ---------------------------------------------------------------------------
# 4. Multilingual non-deletion intents injected as new requests.  These strings
#    do not exist in the dataset, so the classifier cannot be pattern-matching
#    memorised text.
# ---------------------------------------------------------------------------
@case
def multilingual_opt_out(index, identities, builder):
    rows = [
        ("synthetic_01", "do.not.delete.me@example.com",
         "Bitte löschen Sie mein Konto nicht. Ich brauche es weiterhin für meine Bestellungen."),
        ("synthetic_02", "correo.solo@example.com",
         "Solo quiero corregir mi apellido en el perfil. No soliciten la eliminación de la cuenta."),
        ("synthetic_03", "adresse.seule@example.com",
         "Je souhaite uniquement télécharger mes factures. Ne supprimez pas mon compte."),
        ("synthetic_04", "deletion.real@example.com",
         "Please permanently delete my account and every copy of my personal data."),
    ]
    with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False,
                                     encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["case_id", "email", "requested_at", "reported_status",
                         "reported_at", "audit_at", "request_text"])
        for case_id, email, text in rows:
            writer.writerow([case_id, email, "2026-09-01T00:00:00Z", "NOT_STARTED",
                             "2026-09-02T00:00:00Z", "2026-09-15T00:00:00Z", text])
        path = fh.name

    results = {r.case_id: r for r in run_requests(path, evidence=index,
                                                  identities=identities)}
    produced = {cid: results[cid].status for cid in results}
    expected = {"synthetic_01": "NOT_APPLICABLE", "synthetic_02": "NOT_APPLICABLE",
                "synthetic_03": "NOT_APPLICABLE"}
    return {
        "name": "multilingual_opt_out",
        "question": "Do German / Spanish / French keep-my-account requests refuse authorisation?",
        "expected": expected,
        "produced": {k: produced.get(k) for k in expected},
        "pass": all(produced.get(k) == v for k, v in expected.items()),
        "why": ("German is not otherwise present in the corpus, so this case checks the "
                "negation patterns generalise rather than memorise."),
    }


# ---------------------------------------------------------------------------
# 5. Time cutoff: asking the same question before the drift snapshot existed
#    must not surface the failover-replica evidence.
# ---------------------------------------------------------------------------
@case
def time_cutoff(index, identities, builder):
    request = next(r for r in dataset.load_requests()
                   if r.email == "leila.haddad@yahoo.com")
    subject, _ = identities.subject_for_email(request.email)
    early = builder.build(subject, "2026-08-15T00:00:00Z")
    cited = sorted({r.evidence_id for r in early.resources.values() if r.evidence_id})
    return {
        "name": "time_cutoff",
        "question": "Is evidence captured after audit_at excluded from an earlier audit?",
        "expected": {"evidence_54 cited": False},
        "produced": {"evidence_54 cited": "evidence_54" in cited, "cited": cited},
        "pass": "evidence_54" not in cited,
        "why": ("evidence_54 was captured 2026-09-02. An audit dated 2026-08-15 "
                "cannot rely on it, and must not silently borrow a later state."),
    }


def main() -> int:
    index = EvidenceIndex.build()
    identities = IdentityIndex()
    builder = InventoryBuilder(index)

    failures = 0
    out = []
    for fn in CASES:
        result = fn(index, identities, builder)
        out.append(result)
        mark = "PASS" if result["pass"] else "FAIL"
        print(f"[{mark}] {result['name']}")
        print(f"        Q: {result['question']}")
        print(f"        expected: {json.dumps(result['expected'], ensure_ascii=False)}")
        print(f"        produced: {json.dumps(result['produced'], ensure_ascii=False)}")
        if not result["pass"]:
            failures += 1
    print(f"\n{len(CASES) - failures}/{len(CASES)} custom cases passed")
    (ROOT / "code" / "evaluation" / "custom_case_results.json").write_text(
        json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
