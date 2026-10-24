"""Unit tests for the deletion-audit pipeline.

Run from the repository root:

    python3 -m unittest discover -s code/tests -v
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "code"))

from pipeline import dataset, serialize                      # noqa: E402
from pipeline.evidence import EvidenceIndex                  # noqa: E402
from pipeline.identity import IdentityIndex                  # noqa: E402
from pipeline.intent import classify                         # noqa: E402
from pipeline.inventory import InventoryBuilder              # noqa: E402
from pipeline.model import (                                 # noqa: E402
    OPERATION_ORDER, PHASE_OF_OPERATION, STATE_OF_OPERATION,
    CaseResult, Observation, Step,
)
from pipeline.parsers import jsonbundle, logs, pdfdoc, sqlsnap, textdoc  # noqa: E402
from pipeline.planner import Planner                         # noqa: E402
from pipeline.policy import Policy                           # noqa: E402

EVIDENCE = ROOT / "dataset" / "evidence"


class IdentityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index = IdentityIndex()

    def test_current_email(self):
        user_id, how = self.index.resolve("amina.elmasri@gmail.com")
        self.assertEqual(how, "current_email")
        self.assertEqual(user_id, "user_01")

    def test_current_email_is_case_insensitive(self):
        user_id, _ = self.index.resolve("LDAHL9787@FASTMAIL.COM")
        self.assertIsNotNone(user_id)

    def test_historical_email_resolves(self):
        # A request that quotes an address the account no longer uses must
        # still resolve, via user_audit_log's old_value.
        audit = [e for e in self.index.audit_log if e.field_name == "email"]
        self.assertTrue(audit, "dataset should carry historical e-mail changes")
        sample = audit[0]
        user_id, how = self.index.resolve(sample.old_value)
        self.assertEqual(how, "historical_email")
        self.assertEqual(user_id, sample.user_id)

    def test_unknown_email(self):
        user_id, how = self.index.resolve("nobody-here@example.invalid")
        self.assertIsNone(user_id)
        self.assertEqual(how, "unresolved")

    def test_bundle_includes_historical_hash(self):
        bundle = self.index.subject("user_12")
        self.assertIn("minjun.kim@gmail.com", bundle.emails)
        # user_12 changed their e-mail hash in May 2026; the old hash must be
        # part of the identity bundle or historic copies become unlinkable.
        self.assertGreaterEqual(len(bundle.hashes), 2)


class IntentTests(unittest.TestCase):
    def test_english_deletion(self):
        self.assertTrue(classify(
            "Please permanently delete the account associated with this email "
            "address.").authorises_deletion)

    def test_japanese_deletion(self):
        self.assertTrue(classify(
            "このメールアドレスに関連するアカウントの完全な削除を依頼します。"
        ).authorises_deletion)

    def test_spanish_deletion(self):
        self.assertTrue(classify(
            "Solicito la eliminación permanente de la cuenta asociada a este correo."
        ).authorises_deletion)

    def test_french_deletion(self):
        self.assertTrue(classify(
            "Je demande la suppression définitive du compte associé à cette "
            "adresse e-mail.").authorises_deletion)

    def test_korean_deletion(self):
        self.assertTrue(classify(
            "이 이메일 주소에 연결된 계정의 영구 삭제를 요청합니다."
        ).authorises_deletion)

    def test_negation_blocks_authorisation(self):
        self.assertFalse(classify(
            "Do not close the account. I need help resetting the login."
        ).authorises_deletion)

    def test_marketing_optout_is_not_deletion(self):
        self.assertFalse(classify(
            "Please remove me from marketing lists only. I still want receipts "
            "for this account.").authorises_deletion)

    def test_access_request_is_not_deletion(self):
        self.assertFalse(classify(
            "Can you tell me what personal information you hold about me? This "
            "is an information request, not a request to erase it."
        ).authorises_deletion)

    def test_corpus_cluster_is_recovered_without_hardcoding(self):
        """The opt-out family must fall out of the rules, not a case-id list."""
        flagged = [r.case_id for r in dataset.load_requests()
                   if not classify(r.request_text).authorises_deletion]
        self.assertEqual(len(flagged), 10)


class ParserTests(unittest.TestCase):
    def test_log_parser_uses_trace_join(self):
        path = EVIDENCE / "logs" / "resource_lookup_trace_20260926_103856.log"
        obs = logs.extract(path.read_text(), "evidence_94", "2026-09-26T10:38:56Z")
        account = [o for o in obs if o.resource_id == "account_7566"]
        self.assertTrue(account)
        self.assertEqual(account[0].state, "ABSENT")
        # the identifier lives on the "started" line of the same trace
        self.assertIn("l.fontaine46@yahoo.com", account[0].emails)

    def test_json_parser_service_export(self):
        path = EVIDENCE / "records" / "service_resource_export_20260915_114455.json"
        obs, scopes = jsonbundle.extract(path.read_text(), "evidence_92",
                                         "2026-09-15T11:44:55Z")
        self.assertTrue(obs)
        categories = {o.category for o in obs}
        self.assertIn("ANALYTICS_PROFILE", categories)
        self.assertIn("SECURITY_AUDIT_LOG", categories)
        # a 404 profile lookup is an absence observation
        self.assertTrue(any(o.state == "ABSENT" for o in obs))
        self.assertTrue(scopes)

    def test_sql_parser_replays_dump(self):
        path = EVIDENCE / "databases" / "customer_data_read_replica_20260921.sql"
        obs, _ = sqlsnap.extract(path.read_text(), "evidence_05",
                                 "2026-09-21T10:00:00Z")
        by_id = {o.resource_id: o for o in obs}
        self.assertIn("analytics_4440", by_id)
        # identifiers nested inside record_json must be pulled out
        self.assertIn("seoyeon-han@gmail.com", by_id["analytics_4440"].emails)
        self.assertEqual(by_id["audit_record_3482"].category, "SECURITY_AUDIT_LOG")

    def test_sql_parser_detects_replica_drift(self):
        path = (EVIDENCE / "databases" /
                "customer_accounts_failover_replica_20260902.sql")
        obs, _ = sqlsnap.extract(path.read_text(), "evidence_54",
                                 "2026-09-02T23:00:00Z")
        present = {o.resource_id for o in obs if o.state == "PRESENT"}
        # account_005 exists only in the failover replica -> drift
        self.assertIn("account_005", present)

    def test_text_parser_handover(self):
        path = EVIDENCE / "records" / "customer_account_handover_20261001.txt"
        obs, scopes = textdoc.extract(path.read_text(), "evidence_03",
                                      "2026-10-01T12:00:00Z")
        by_id = {o.resource_id: o for o in obs}
        self.assertEqual(by_id["search_052"].category, "SEARCH_INDEX_ENTRY")
        self.assertEqual(by_id["backup_3870"].state, "RESTORE_ALLOWED")
        self.assertEqual(by_id["support_864"].category, "SUPPORT_MESSAGE")
        self.assertEqual(by_id["support_4218"].category, "SUPPORT_ATTACHMENT")
        self.assertTrue(scopes)

    def test_pdf_parser_extracts_identity_link(self):
        path = EVIDENCE / "documents" / "delivery_correction_attachment.pdf"
        obs = pdfdoc.extract(str(path), "evidence_58", "2026-09-12T07:30:00Z")
        support = [o for o in obs if o.resource_id == "support_001"]
        self.assertTrue(support)
        self.assertEqual(support[0].category, "SUPPORT_ATTACHMENT")
        self.assertIn("priya.raman@gmail.com", support[0].emails)


class PolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.policy = Policy()

    def test_delete_category_present_is_violation(self):
        a = self.policy.assess("USER_PROFILE", "PRESENT")
        self.assertFalse(a.compliant)
        self.assertEqual(a.finding_operation, "REPORT_REMAINING_RECORD")
        self.assertEqual(a.remediation_operation, "DELETE_RECORD")

    def test_delete_category_absent_is_compliant(self):
        self.assertTrue(self.policy.assess("USER_PROFILE", "ABSENT").compliant)

    def test_analytics_anonymize(self):
        a = self.policy.assess("ANALYTICS_PROFILE", "PRESENT")
        self.assertEqual(a.finding_operation, "REPORT_EXPOSED_PII")
        self.assertEqual(a.remediation_operation, "ANONYMIZE_RECORD")
        self.assertTrue(self.policy.assess("ANALYTICS_PROFILE", "ANONYMIZED").compliant)

    def test_retained_audit_is_compliant_and_verified_present(self):
        a = self.policy.assess("SECURITY_AUDIT_LOG", "RETAINED")
        self.assertTrue(a.compliant)
        self.assertEqual(a.verification_operation, "VERIFY_RECORD_PRESENT")

    def test_tombstone_redaction(self):
        a = self.policy.assess("ACCOUNT_TOMBSTONE", "PRESENT")
        self.assertEqual(a.remediation_operation, "REDACT_RECORD")
        self.assertTrue(self.policy.assess("ACCOUNT_TOMBSTONE", "REDACTED").compliant)

    def test_backup_and_flow(self):
        self.assertEqual(self.policy.assess("BACKUP_ARCHIVE", "RESTORE_ALLOWED")
                         .finding_operation, "REPORT_RESTORE_RISK")
        self.assertEqual(self.policy.assess("DATA_FLOW", "FLOW_ACTIVE")
                         .finding_operation, "REPORT_ACTIVE_DATA_FLOW")


class OrderingTests(unittest.TestCase):
    def test_step_sort_key_follows_specification(self):
        steps = [
            Step(0, "VERIFICATION", "account_service", "account_1",
                 "VERIFY_RECORD_ABSENT", "ABSENT", "e"),
            Step(0, "FINDING", "search_service", "search_1",
                 "REPORT_REMAINING_RECORD", "PRESENT", "e"),
            Step(0, "REMEDIATION", "search_service", "search_1",
                 "DELETE_RECORD", "ABSENT", ""),
            Step(0, "FINDING", "account_service", "account_1",
                 "REPORT_REMAINING_RECORD", "PRESENT", "e"),
        ]
        ordered = [s.operation for s in sorted(steps, key=Step.sort_key)]
        self.assertEqual(ordered, [
            "REPORT_REMAINING_RECORD", "REPORT_REMAINING_RECORD",
            "DELETE_RECORD", "VERIFY_RECORD_ABSENT",
        ])

    def test_phase_and_state_tables_are_consistent(self):
        for operation, phase in PHASE_OF_OPERATION.items():
            self.assertIn(operation, OPERATION_ORDER)
            self.assertIn(STATE_OF_OPERATION[operation],
                          {"PRESENT", "ABSENT", "REDACTED", "ANONYMIZED",
                           "ACTIVE", "DISABLED", "BLOCKED"})


class SerializerTests(unittest.TestCase):
    def test_row_round_trip(self):
        steps = (Step(1, "FINDING", "account_service", "account_1",
                      "REPORT_REMAINING_RECORD", "PRESENT", "evidence_01"),)
        row = CaseResult("case_00", "INCOMPLETE", steps).as_row()
        self.assertEqual(row["case_id"], "case_00")
        parsed = json.loads(row["steps_json"])
        self.assertEqual(parsed[0]["resource_id"], "account_1")
        self.assertEqual(parsed[0]["step_no"], 1)

    def test_validation_catches_contract_breaks(self):
        bad = CaseResult("case_00", "COMPLETE", ())
        problems = serialize.validate([bad], ["case_00"])
        self.assertTrue(any("complete_without_verification" in p for p in problems))

    def test_not_applicable_must_be_empty(self):
        step = Step(1, "VERIFICATION", "account_service", "account_1",
                    "VERIFY_RECORD_ABSENT", "ABSENT", "evidence_01")
        problems = serialize.validate([CaseResult("c", "NOT_APPLICABLE", (step,))], ["c"])
        self.assertTrue(any("na_with_steps" in p for p in problems))


class TimeCutoffTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index = EvidenceIndex.build()
        cls.identities = IdentityIndex()
        cls.builder = InventoryBuilder(cls.index)

    def _inventory(self, email, audit_at):
        subject, _ = self.identities.subject_for_email(email)
        return self.builder.build(subject, audit_at)

    def test_future_evidence_is_ignored(self):
        """A file captured after audit_at must not testify about the state."""
        email = "leila.haddad@yahoo.com"
        early = self._inventory(email, "2026-09-08T13:40:00Z")
        self.assertIn("account_005", early.resources)
        self.assertEqual(early.resources["account_005"].evidence_id, "evidence_54")

        # Ask the same question *before* evidence_54 was captured (2026-09-02):
        # the failover-replica drift must no longer be usable as evidence.
        before = self._inventory(email, "2026-08-15T00:00:00Z")
        cited = {r.evidence_id for r in before.resources.values()}
        self.assertNotIn("evidence_54", cited)

    def test_no_observation_after_audit_instant_is_used(self):
        email = "amina.elmasri@gmail.com"
        inventory = self._inventory(email, "2026-09-03T11:30:00Z")
        for resource in inventory.resources.values():
            self.assertLessEqual(resource.observed_at, "2026-09-03T11:30:00Z")


class GoldenSampleTests(unittest.TestCase):
    """Regression guard on the labelled sample (32 cases)."""

    @classmethod
    def setUpClass(cls):
        from pipeline.main_stub import run_requests
        from evaluation.evaluate import compare, load_expected
        # evaluate.py lives outside the package; import it by path
        sys.path.insert(0, str(ROOT / "code" / "evaluation"))
        import importlib
        evaluator = importlib.import_module("evaluate")
        results = run_requests("sample_deletion_requests.csv")
        predicted = {r.case_id: r.as_row() for r in results}
        cls.report = evaluator.compare(predicted, evaluator.load_expected())

    def test_status_accuracy_floor(self):
        self.assertGreaterEqual(self.report["status_accuracy"], 0.95)

    def test_step_f1_floor(self):
        self.assertGreaterEqual(self.report["step_f1"], 0.90)

    def test_evidence_accuracy_floor(self):
        self.assertGreaterEqual(self.report["field_accuracy"], 0.95)

    def test_no_false_positive_steps(self):
        self.assertEqual(self.report["fp"], 0)


if __name__ == "__main__":
    unittest.main()
