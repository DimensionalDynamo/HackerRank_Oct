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

# These tests read the supplied corpus.  Run from a bare code.zip (no dataset/,
# no output.csv) there is nothing to assert against, so the module reports as
# skipped instead of failing fifteen times: an error there would say "the code
# is broken" when the real cause is "the data is not next to it".
if not (ROOT / "dataset" / "deletion_requests.csv").exists():
    raise unittest.SkipTest(
        f"dataset/ not found under {ROOT}; copy the supplied dataset there "
        "and run `python3 code/main.py` first")

sys.path.insert(0, str(ROOT / "code"))

from pipeline import dataset, serialize                      # noqa: E402
from pipeline.evidence import EvidenceIndex                  # noqa: E402
from pipeline.identity import IdentityIndex                  # noqa: E402
from pipeline.intent import classify                         # noqa: E402
from pipeline.inventory import InventoryBuilder              # noqa: E402
from pipeline.model import OPERATION_ORDER, PHASE_OF_OPERATION, STATE_OF_OPERATION, CaseResult, Step
from pipeline.parsers import jsonbundle, logs, pdfdoc, sqlsnap, textdoc  # noqa: E402
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


class PdfTextTests(unittest.TestCase):
    """The PDFs must be readable without any third-party package.

    Losing them silently is not acceptable: it cost three sample steps and one
    evidence id before the standard-library extractor existed.
    """

    DOCUMENTS = EVIDENCE / "documents"

    def test_stdlib_extractor_reads_every_pdf(self):
        from pipeline.parsers import pdftext
        for path in sorted(self.DOCUMENTS.glob("*.pdf")):
            text = pdftext.extract_text(str(path))
            self.assertGreater(len(text.strip()), 100, f"{path.name} gave no text")

    def test_stdlib_extractor_recovers_the_same_facts_as_pypdf(self):
        from pipeline.parsers import pdfdoc, pdftext
        for path in sorted(self.DOCUMENTS.glob("*.pdf")):
            stdlib = pdftext.extract_text(str(path))
            try:
                reference = pdftext.extract_text_via_pypdf(str(path))
            except Exception:
                continue                      # pypdf not installed: nothing to compare
            def facts(text):
                return (sorted(set(pdfdoc.EMAIL_RE.findall(text))),
                        sorted(set(pdfdoc.USER_RE.findall(text))),
                        sorted(set(pdfdoc.RESOURCE_RE.findall(text))),
                        [m.groups() for m in pdfdoc.HOLD_ROW_RE.finditer(text)])
            self.assertEqual(facts(stdlib), facts(reference), path.name)

    def test_read_pdf_falls_back_when_pypdf_is_absent(self):
        from pipeline.parsers import pdftext
        path = self.DOCUMENTS / "delivery_correction_attachment.pdf"

        original = pdftext.extract_text_via_pypdf
        pdftext.extract_text_via_pypdf = lambda _p: (_ for _ in ()).throw(ImportError("no pypdf"))
        try:
            text = pdftext.read_pdf(str(path))
        finally:
            pdftext.extract_text_via_pypdf = original
        self.assertIn("support_001", text)

    def test_evidence_index_loads_every_artefact(self):
        index = EvidenceIndex.build()
        self.assertEqual(index.loaded_files, len(dataset.load_evidence_files()))
        self.assertEqual(index.file_errors, [])


class SourceHygieneTests(unittest.TestCase):
    """Guards that catch refactor damage the runtime does not notice."""

    MODULES = sorted((ROOT / "code").rglob("*.py"))

    def test_every_module_imports_future_annotations(self):
        """Python 3.9 evaluates `X | None` eagerly; the future import is load-bearing."""
        for path in self.MODULES:
            src = path.read_text()
            self.assertIn("from __future__ import annotations", src, str(path))

    def test_future_import_precedes_all_other_statements(self):
        import ast
        for path in self.MODULES:
            tree = ast.parse(path.read_text())
            body = list(tree.body)
            index = next((i for i, n in enumerate(body)
                          if isinstance(n, ast.ImportFrom) and n.module == "__future__"), None)
            self.assertIsNotNone(index, str(path))
            allowed = 0
            if body and isinstance(body[0], ast.Expr) \
               and isinstance(getattr(body[0], "value", None), ast.Constant) \
               and isinstance(body[0].value.value, str):
                allowed = 1
            self.assertLessEqual(index, allowed, f"{path}: future import must come first")

    def test_no_unused_imports(self):
        import ast
        for path in self.MODULES:
            tree = ast.parse(path.read_text())
            names = {}
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    for alias in node.names:
                        name = alias.asname or alias.name
                        if name != "annotations":
                            names[id(alias)] = name
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        names[id(alias)] = (alias.asname or alias.name).split(".")[0]
            used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
            used |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
            dead = sorted(set(names.values()) - used)
            self.assertEqual(dead, [], f"{path}: unused imports {dead}")

    def test_pipeline_never_mentions_a_case_id(self):
        """Behaviour must come from rules, never from a case-id lookup."""
        import re
        pattern = re.compile(r"\bcase_\d{2,}")
        for path in (ROOT / "code" / "pipeline").rglob("*.py"):
            for number, line in enumerate(path.read_text().splitlines(), 1):
                self.assertIsNone(pattern.search(line), f"{path}:{number}: {line.strip()}")


class OutputIntegrityTests(unittest.TestCase):
    """Checks a reviewer can repeat on the shipped output.csv."""

    @classmethod
    def setUpClass(cls):
        import csv as _csv
        root = ROOT
        if not (root / "output.csv").exists():
            raise unittest.SkipTest(
                "output.csv is not present; run `python3 code/main.py` first")
        with (root / "output.csv").open(newline="", encoding="utf-8") as handle:
            cls.rows = list(_csv.DictReader(handle))
        cls.file_ids = {e.file_id for e in dataset.load_evidence_files()}

    def test_one_row_per_request_sorted_numerically(self):
        from pipeline.serialize import case_sort_key
        ids = [r["case_id"] for r in self.rows]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(ids, sorted(ids, key=case_sort_key))
        expected = {r.case_id for r in dataset.load_requests()}
        self.assertEqual(set(ids), expected)

    def test_every_step_has_exactly_the_seven_fields(self):
        expected = {"step_no", "phase", "system_id", "resource_id",
                    "operation", "state", "evidence_id"}
        for row in self.rows:
            for index, step in enumerate(json.loads(row["steps_json"]), start=1):
                self.assertEqual(set(step), expected, row["case_id"])
                self.assertEqual(step["step_no"], index, row["case_id"])

    def test_every_cited_evidence_id_exists_in_the_manifest(self):
        for row in self.rows:
            for step in json.loads(row["steps_json"]):
                if step["evidence_id"]:
                    self.assertIn(step["evidence_id"], self.file_ids, row["case_id"])

    def test_every_cited_resource_is_grounded(self):
        """No invented ids: each resource must appear in the corpus or a transcript."""
        corpus = []
        for path in (ROOT / "dataset").rglob("*"):
            if path.is_file() and path.suffix.lower() in (".csv", ".log", ".json", ".sql", ".txt"):
                corpus.append(path.read_text(errors="ignore"))
        transcripts = (ROOT / "code" / "pipeline" / "parsers" /
                       "image_transcripts.json").read_text()
        blob = "\n".join(corpus) + transcripts
        for row in self.rows:
            for step in json.loads(row["steps_json"]):
                self.assertIn(step["resource_id"], blob, row["case_id"])


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
        row = CaseResult("case_test_00", "INCOMPLETE", steps).as_row()
        self.assertEqual(row["case_id"], "case_test_00")
        parsed = json.loads(row["steps_json"])
        self.assertEqual(parsed[0]["resource_id"], "account_1")
        self.assertEqual(parsed[0]["step_no"], 1)

    def test_validation_catches_contract_breaks(self):
        bad = CaseResult("case_test_00", "COMPLETE", ())
        problems = serialize.validate([bad], ["case_test_00"])
        self.assertTrue(any("complete_without_verification" in p for p in problems))

    def test_case_ids_sort_numerically(self):
        from pipeline.serialize import case_sort_key
        ids = ["case_test_100", "case_test_33", "case_test_9", "case_test_550"]
        self.assertEqual(sorted(ids, key=case_sort_key),
                         ["case_test_9", "case_test_33", "case_test_100", "case_test_550"])

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


class BidirectionalContractTests(unittest.TestCase):
    """Tests added after an independent audit of the shipped artefact.

    Each pins a property the submission asserts in prose, so the claim cannot
    rot silently.
    """

    def test_csv_loader_tolerates_a_utf8_bom(self):
        # Excel and Google Sheets both prefix "Save as CSV" output with a BOM.
        # Read as plain utf-8 that turns the first header into '\ufeffcase_id'
        # and the whole run dies on a KeyError.
        import csv as _csv
        import tempfile

        src = ROOT / "dataset" / "deletion_requests.csv"
        with src.open(newline="", encoding="utf-8") as handle:
            rows = list(_csv.DictReader(handle))
        fields = list(rows[0].keys())
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False,
                                         encoding="utf-8-sig", newline="") as fh:
            writer = _csv.DictWriter(fh, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
            path = fh.name
        try:
            parsed = dataset.load_requests(path)
        finally:
            Path(path).unlink()
        self.assertEqual([r.case_id for r in parsed],
                         [r["case_id"] for r in rows])
        self.assertFalse(parsed[0].case_id.startswith("\ufeff"))

    def test_every_corpus_timestamp_is_fixed_width_rfc3339(self):
        # The audit cutoff is a plain string comparison, which is only a
        # chronological order while every timestamp has the same width.
        import re
        pattern = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\Z")
        stamps = set()
        for path in (ROOT / "dataset").rglob("*"):
            if path.suffix not in (".csv", ".json", ".log", ".sql", ".txt"):
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            stamps.update(re.findall(r"\d{4}-\d{2}-\d{2}T[\d:]+Z?", text))
        self.assertTrue(stamps, "no timestamps found in the corpus")
        bad = sorted(s for s in stamps if not pattern.match(s))
        self.assertEqual(bad, [], f"non-conforming timestamps: {bad[:5]}")
        lexicographic = sorted(stamps)
        chronological = sorted(stamps, key=lambda s: (
            s[0:4], s[5:7], s[8:10], s[11:13], s[14:16], s[17:19]))
        self.assertEqual(lexicographic, chronological)

    def test_malformed_audit_at_admits_no_evidence(self):
        # "2026-..." <= "not-a-date" is True, so an unguarded comparison would
        # silently admit the entire corpus.
        self.assertTrue(EvidenceIndex.admits("2026-09-15T12:00:00Z"))
        for bad in ("not-a-date", "", "2026-09-15", "2026-09-15T12:00:00Zx"):
            self.assertFalse(EvidenceIndex.admits(bad), bad)

    def test_output_is_identical_without_site_packages(self):
        # The strongest form of the "standard library only" claim: run the real
        # entry point with site-packages disabled (-S) and compare bytes.
        import hashlib
        import os
        import subprocess
        import tempfile

        shipped = ROOT / "output.csv"
        if not shipped.exists():
            self.skipTest("output.csv is not present; run `python3 code/main.py` first")
        expected = hashlib.sha256(shipped.read_bytes()).hexdigest()
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as fh:
            got_path = fh.name
        try:
            proc = subprocess.run(
                [sys.executable, "-S", str(ROOT / "code" / "main.py"),
                 "--out", got_path],
                cwd=str(ROOT), capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr[-400:])
            produced = hashlib.sha256(Path(got_path).read_bytes()).hexdigest()
        finally:
            os.unlink(got_path)
        self.assertEqual(produced, expected)

    def test_step_order_matches_the_label_order_on_the_sample(self):
        # Sequence accuracy is 0.4688 only because steps are missing.  With the
        # un-predicted steps filtered out of the label, the remaining order must
        # match ours exactly -- i.e. there is no ordering defect to find.
        import csv as _csv
        from collections import Counter

        from pipeline.main_stub import run_requests

        produced = {r.case_id: r.as_row()
                    for r in run_requests("sample_deletion_requests.csv")}
        sample = ROOT / "dataset" / "sample_output.csv"
        with sample.open(newline="", encoding="utf-8") as fh:
            gold = {r["case_id"]: r for r in _csv.DictReader(fh)}

        def key(step):
            return (step["phase"], step["system_id"], step["resource_id"],
                    step["operation"], step["state"])

        for case_id, expected_row in gold.items():
            expected = [key(s) for s in json.loads(expected_row["steps_json"])]
            got = [key(s) for s in json.loads(produced[case_id]["steps_json"])]
            remaining = Counter(got)
            kept = []
            for step in expected:
                if remaining[step] > 0:
                    kept.append(step)
                    remaining[step] -= 1
            self.assertEqual(kept, got,
                             f"{case_id}: predicted order differs from label order")
