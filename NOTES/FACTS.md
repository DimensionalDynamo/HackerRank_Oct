# Facts established from `dataset/` only

Every line here was read out of a supplied file or printed by a command run in
this repository.  Nothing is taken from an organiser answer key, and nothing is
inferred from a case id.  `dataset/sample_output.csv` is used strictly as a
labelled sample for scoring, never as a lookup table.

## Inputs

| File | Rows | Notes |
| --- | --- | --- |
| `deletion_requests.csv` | 518 | `case_33` … `case_550`, `audit_at` 2026-01-29T12:00Z … 2026-10-01T12:00Z |
| `sample_deletion_requests.csv` | 32 | `case_01` … `case_32` |
| `sample_output.csv` | 32 | 22 INCOMPLETE / 10 COMPLETE, 0 NOT_APPLICABLE |
| `user_identities.csv` | 850 | `user_01` … `user_850`, 751 zero-padded ids, 99 unpadded |
| `user_audit_log.csv` | 306 | field changes: `email` 107, `email_hash` 107, `phone` 92 — **no resource ids** |
| `system_activity.csv` | 4011 | operational events; carries resource ids but no subject identity |
| `deletion_rules.csv` | 15 | category → required action |
| `evidence_files.csv` | 94 | `file_id`, `file_path`, `origin`, `captured_at` |
| `evidence/` | 94 files | 25 SQL, 30 records, 26 logs, 4 PDF, 11 PNG (no OCR available) |

`reported_status` on the 518: COMPLETE 254 / PARTIALLY_COMPLETE 152 / NOT_STARTED 112.
These are claims; they are parsed but never used as the verdict.

## Deletion rules (`deletion_rules.csv`)

| # | Category | Action |
| --- | --- | --- |
| 01 | USER_PROFILE | DELETE |
| 02 | SECONDARY_IDENTIFIER | DELETE |
| 03 | SEARCH_INDEX | DELETE |
| 04 | CACHE | DELETE |
| 05 | SUPPORT_MESSAGE (shared) | REDACT |
| 06 | SUPPORT_ATTACHMENT (subject-only) | DELETE |
| 07 | ANALYTICS_PROFILE | ANONYMIZE |
| 08 | PRODUCT_TELEMETRY | ANONYMIZE |
| 09 | USER_EXPORT | DELETE |
| 10 | MARKETING_PROFILE | DELETE |
| 11 | EMAIL_DELIVERY_LOG | REDACT |
| 12 | SECURITY_AUDIT_LOG | RETAIN |
| 13 | LEGAL_HOLD | RETAIN |
| 14 | BACKUP_ARCHIVE | RETAIN + block restore |
| 15 | ACCOUNT_TOMBSTONE | REDACT |

## Output-format law (derived from all 32 labelled rows, no exceptions)

* `step_no` starts at 1 and increases by 1.
* Phase order FINDING → REMEDIATION → VERIFICATION.
* Within a phase: `REPORT_REMAINING_RECORD`, `REPORT_EXPOSED_PII`,
  `REPORT_ACTIVE_DATA_FLOW`, `REPORT_RESTORE_RISK`, `DISABLE_DATA_FLOW`,
  `BLOCK_RESTORE`, `DELETE_RECORD`, `REDACT_RECORD`, `ANONYMIZE_RECORD`,
  `VERIFY_RECORD_ABSENT`, `VERIFY_RECORD_PRESENT`, `VERIFY_RECORD_REDACTED`,
  `VERIFY_RECORD_ANONYMIZED`, `VERIFY_DATA_FLOW_DISABLED`,
  `VERIFY_RESTORE_BLOCKED`; ties broken by `system_id` then `resource_id`.
* State is fixed per operation: ABSENT / PRESENT / REDACTED / ANONYMIZED /
  DISABLED / BLOCKED as listed in `problem_statement.md`.
* COMPLETE rows carry only verification steps and every step cites evidence.
* INCOMPLETE rows carry findings (with evidence) plus remediation and future
  verification steps with an empty `evidence_id`.
* NOT_APPLICABLE rows are `[]`.

## Evidence mechanics that change verdicts

* **Trace joins.** In the service logs an identity sits on the `*_started`
  line and the state sits on the `*_completed` line of the same `trace_id`.
  e.g. `tr-9750867`: `connector_config_read_started subject_key="lucia-torres@gmail.com"`
  (11:58:08) → `connector_heartbeat resource_id=data_flow_9846 enabled=true` (11:58:11).
* **Readback traces.** `resource_lookup_trace_*.log` are point-in-time
  readbacks: `profile_lookup_completed rows=0 http_status=404` proves ABSENT,
  `rows=1` proves PRESENT, `audit_record_read_completed rows=1` proves a
  retained record still exists.
* **Identifier shapes.** `email_hash` values are `sha256(email)`; verified by
  recomputing `sha256("minjun.kim@gmail.com")` and matching
  `user_identities.email_hash` for `user_12`.  `correlation_key` is either a
  `user_*` reference (64-hex) or a hash; shape decides, not the field name.
* **Historical identifiers matter.** 103 of the request e-mails are addresses
  the user no longer owns (`user_audit_log.old_value`); matching must be
  case-insensitive and must include superseded e-mails, phones and hashes.
* **Replication drift.** `customer_accounts_failover_replica_20260902.sql`
  holds `customer_profiles_primary` and `customer_profiles_failover_replica`;
  `account_005` exists only in the replica, so a primary-only read would call
  the deletion complete.
* **Anonymised analytics cannot be linked.** `analytics_*` records with
  `email = phone = email_hash = user_id = null` carry no identifier anywhere in
  the corpus, so they cannot be attributed to a subject from evidence alone.
* **Reported results are claims.** `system_activity` rows such as
  `account_4092 DELETE_RECORD FAILED` and `account_4990 DELETE_RECORD SUCCESS`
  are the workflow's own claims and are never used as ground truth.
* **Absence of an account is not an error.** Evidence coverage is
  batch-dependent: the 2026-04-09 batch only resolves through ledger rows in
  `customer_export_manifest_20260409.json`.

## Multilingual intent

The 518 requests are templates in English, French, Spanish, Portuguese,
Japanese and Korean.  Exactly ten rows — `case_533` … `case_542` — withhold
authorisation (marketing opt-out / access request / correction / "do not close
the account"), which the classifier reproduces from rules alone.  Templates
that merely *mention* deletion in a negative clause ("not a request to erase",
"n'en supprimez pas") are the hard cases; see `NOTES/DECISIONS.md`.

## Measured state of the pipeline

```
python3 code/main.py --validate
wrote /home/user/HackerRank_Oct/output.csv rows=518 COMPLETE=173 INCOMPLETE=335 NOT_APPLICABLE=10
(no contract violations printed)

python3 code/evaluation/evaluate.py
status accuracy       0.9688
step precision        1.0000  (tp=166 fp=0 fn=27)
step recall           0.8601
step F1               0.9248
field (evidence) acc  1.0000  (166/166)
sequence accuracy     0.4688
```

Funnel over the 508 deletion-authorised cases: subject resolved 498, empty
inventory 10, account resolved 196, audit trail located 300, resources citing a
file captured after `audit_at` **0**.
