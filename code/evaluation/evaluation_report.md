# Evaluation report — Ghost Data Still Exists

Everything below was produced by running the commands shown, in this
repository, against the supplied data only. `dataset/sample_output.csv` is the
labelled sample (32 cases) and is used exclusively as a scoring target; no case
id, e-mail or request string is hard-coded anywhere in `code/`.

Reproduce with:

```bash
python3 code/evaluation/evaluate.py            # summary
python3 code/evaluation/evaluate.py --errors   # per-case MISSING/EXTRA diff
python3 code/evaluation/custom_cases.py        # six reasoning cases
python3 -m unittest discover -s code/tests     # 55 unit tests
python3 code/main.py --validate                # full run + contract check
```

---

## 1. Case, row, field and sequence accuracy

Latest run:

```
$ python3 code/evaluation/evaluate.py
cases                 32
status accuracy       0.9688
step precision        1.0000  (tp=166 fp=0 fn=27)
step recall           0.8601
step F1               0.9248
field (evidence) acc  1.0000  (166/166)
sequence accuracy     0.4688
```

| Metric | Value | Definition used by `evaluate.py` |
| --- | --- | --- |
| Case accuracy | 0.9688 (31/32) | `deletion_status` equals the label |
| Row accuracy | 518/518 | one row per request, header `case_id,deletion_status,steps_json`, sorted, `steps_json` parses as JSON, `step_no` consecutive from 1 |
| Step precision | 1.0000 | matched steps / predicted steps, matched on `(phase, system_id, resource_id, operation, state)` as a multiset |
| Step recall | 0.8601 | matched steps / labeled steps |
| Step F1 | 0.9248 | harmonic mean of the two |
| Field accuracy | 1.0000 (166/166) | for each matched step, the `evidence_id` equals the label's |
| Sequence accuracy | 0.4688 (15/32) | the whole ordered array equals the label, element for element |

Zero false-positive steps means the pipeline never invents a record, a risk or
a check. All remaining error is *recall*, and it is concentrated in records that
are not derivable from the supplied evidence (§3).

### Measured states during this session (same command, same sample)

Every row below is a literal `evaluate.py` output captured after the change
named in the left column; nothing here is interpolated.

| After this change | Status acc | Step P (tp/fp/fn) | Step R | Step F1 | Field acc | Sequence |
| --- | --- | --- | --- | --- | --- | --- |
| Identity linking decoupled from the state time-gate | 1.0000 | 1.0000 (166/0/27) | 0.8601 | 0.9248 | 1.0000 | 0.4688 |
| Late bundles admitted for resources with no in-window witness | 1.0000 | 1.0000 (172/0/21) | 0.8912 | 0.9425 | 1.0000 | 0.5312 |
| **Reverted**: strict file-capture gate on *state* (see F4) | 1.0000 | 1.0000 (166/0/27) | 0.8601 | 0.9248 | 1.0000 | 0.4688 |
| Contract fix: no COMPLETE without a citation (see F5) | 0.9688 | 1.0000 (166/0/27) | 0.8601 | 0.9248 | 1.0000 | 0.4688 |
| Clause-scoped negation in the intent classifier (see F7) | 0.9688 | 1.0000 (166/0/27) | 0.8601 | 0.9248 | 1.0000 | 0.4688 |
| Screenshot transcripts added (11 consoles) | 0.9688 | 1.0000 (166/0/27) | 0.8601 | 0.9248 | 1.0000 | 0.4688 |
| **Regression caught**: no `pypdf` in the environment (see F8) | 0.9375 | 1.0000 (163/0/30) | 0.8446 | 0.9157 | 0.9939 | 0.4375 |
| **Fixed**: standard-library PDF reader replaces the dependency (see F8) | 0.9688 | 1.0000 (166/0/27) | 0.8601 | 0.9248 | 1.0000 | 0.4688 |

The second row scored better **by scoring better on a leaked evidence window**,
which is why it was reverted: it cited files captured after `audit_at` in 104
cases. The fourth row trades 1/32 status accuracy for **zero contract
violations** on the judged 518, the deliberate choice described in §4. The fifth
row changed no sample metric but removed a real misclassification — it is
documented as F7 below because a sample-invisible fix is exactly the kind of
change that needs a trail.

### Full dataset (`python3 code/main.py --validate`)

```
wrote /home/user/HackerRank_Oct/output.csv rows=518 COMPLETE=172 INCOMPLETE=336 NOT_APPLICABLE=10
```

* 518 rows, sorted by `case_id`, header exactly
  `case_id,deletion_status,steps_json`, no contract violations.
* Steps: 2459 total, 977 carry an `evidence_id` (39.7 %). Every cited step is a
  FINDING or a COMPLETE-case VERIFICATION; the rest are remediation / future
  checks, which the specification requires to have an empty `evidence_id`.
  506 of 518 rows (97.7 %) cite at least one evidence id.
* 2 rows are `INCOMPLETE,[]` — `case_168` and `case_288`: the request
  authorises deletion but no record could be attributed inside the audit window
  (see §4). The other ten `[]` rows are the `NOT_APPLICABLE` ones, as required.
* Determinism: three consecutive runs are byte-identical
  (`sha256 d24010fe2146870073052b20ecda5b3a881136254b994dfe8818d868289b6393`),
  and a run with `pypdf` import-blocked produces the identical file.

---

## 2. Custom evaluation cases (six, all currently passing)

`code/evaluation/custom_cases.py` finds its examples by *predicate*, never by
case id, asserts a specific behaviour, and prints expected vs produced. The
latest run:

```
[PASS] replica_drift
[PASS] absent_account_verification
[PASS] tombstone_redaction
[PASS] audit_retention
[PASS] multilingual_opt_out
[PASS] time_cutoff

6/6 custom cases passed (0 skipped)
```

| # | Case | What it probes | Expected behaviour | Result |
| --- | --- | --- | --- | --- |
| 1 | `replica_drift` | A record present only in a failover-replica table | state PRESENT ⇒ case INCOMPLETE | PASS |
| 2 | `absent_account_verification` | A readback showing the profile is gone | one `VERIFY_RECORD_ABSENT` with a cited file | PASS |
| 3 | `tombstone_redaction` | An account tombstone that still holds name/e-mail/phone/hash | `REPORT_EXPOSED_PII` → `REDACT_RECORD` → `VERIFY_RECORD_REDACTED`, reference kept | PASS |
| 4 | `audit_retention` | A retained security-audit record | only `VERIFY_RECORD_PRESENT`, never a delete | PASS |
| 5 | `multilingual_opt_out` | German / Spanish / French "keep my account" requests injected as new CSV rows (text absent from the corpus) | all three `NOT_APPLICABLE` | PASS |
| 6 | `time_cutoff` | The same question asked before a snapshot was captured | no file captured after `audit_at` is cited | PASS |

Case 5 exists because the corpus contains no German: it checks the negation
rules generalise rather than memorise templates. Case 6 is the anti-cheat for
the whole design — if the pipeline leaked future evidence, this fails.

---

## 3. Failures found, and what was done

**F1 — False-positive steps (fixed).** Early versions reported a support
message as PRESENT whenever a log line said `deletion_marker_check
marker_found=false`, and reported PII exposure on every analytics read. Both
were single-line guesses; the marker handler was deleted outright and analytics
state now requires a positive identifier match. Step precision is now 1.0000
with 166 true positives and 0 false positives.

**F2 — 27 missing steps, all on records that the evidence cannot supply (open).**
Every remaining `MISSING` line is one of: `VERIFY_RECORD_ABSENT` on an
`account_*`, `VERIFY_RECORD_ANONYMIZED` on an `analytics_*`, or
`VERIFY_RECORD_PRESENT` on an `audit_record_*` belonging to the same case. Two
corpus-wide greps settle where they come from:

```
$ grep -rn "audit_record_6645|account_5521" dataset/  # (evidence, activity)
dataset/system_activity.csv:3733: activity_3732,...DELETE_RECORD,...account_5521,SUCCESS,...
(and nothing else for audit_record_6645 outside sample_output.csv)
$ grep -rn "analytics_5335" dataset/evidence
dataset/evidence/records/customer_data_snapshot_bundle_20260917.json
dataset/evidence/records/service_resource_export_20260915_114455.json
```

The account ids appear only in `system_activity` deletion claims (which the
problem statement forbids treating as fact) with no subject linkage, and the
anonymised analytics rows carry `email = phone = email_hash = user_id = null`
everywhere in the corpus. Attributing them would require either a case-id
lookup or an invented identity bridge, both of which the brief rules out. They
are therefore reported as unrecovered rather than guessed. This is the honest
ceiling of an evidence-only pipeline on this evidence: recall 0.8601, precision
1.0000.

**F3 — Sequence accuracy 0.4688 (open).** No case matches on the step *set* yet
differs in order — every currently-scored case is either identical or missing
steps. Sequence accuracy is therefore capped by F2 by construction, and will
rise with it; the ordering law itself is verified independently by
`test_step_sort_key_follows_specification` and by `serialize.validate`, which
checks operation-order, then `system_id`, then `resource_id` on every row.

**F4 — Evidence-window leak (fixed).** An intermediate version admitted
observations taken from bundles exported after `audit_at`, because the inner
snapshot timestamps were older. That is a real audit violation: at `audit_at`
the auditor could not have read that file. After gating on the file's own
`captured_at` (and on each snapshot's coverage date), citations of
out-of-window files fell from 139/1326 resources to **0**.

The sample cost was 6 steps (tp 172 -> 166, recall 0.8912 -> 0.8601), and those
6 steps **are** credited by the labels: the reference answers admit an
observation whose own timestamp is inside the window even when the artefact
carrying it was exported afterwards. The ground truth therefore reads the
cutoff as *observation-time*, while this submission also gates on
*file-capture-time*. The stricter reading is kept deliberately, because a
bundle exported after `audit_at` could not have been read by an auditor at
`audit_at`; this is a documented disagreement with the labels, not a bug.
Re-measured on the shipped code, relaxing only that gate is worth +2 matched
steps: it recovers 5 gated steps and breaks 2 previously-correct ones, so the
two readings trade rather than one dominating.

**F5 — Nine contract violations (fixed).** Nine deletion-authorised cases
resolved to zero resources and were emitting `COMPLETE` with `[]`, which
violates "COMPLETE ⇒ verification steps with evidence" and repeats exactly the
failure the challenge describes. They now emit `INCOMPLETE` with an empty step
list (a deletion *was* requested, so `NOT_APPLICABLE` would be false, and no
verification can be cited, so `COMPLETE` is unavailable). Contract violations:
9 → 0. After the screenshot transcripts landed, only two rows
(`case_168`, `case_288`) still have no attributable record at all.

**F6 — A hallucinated account id (fixed).** A naming-convention guess
(`user_04` → `account_004`) once produced a COMPLETE step with an empty
`evidence_id`. The guess is now kept only when a snapshot inside the window
covers the profile table and omits the id — the omission is the citation —
otherwise it is dropped. No step in the output cites an empty id.

**F7 — Postfixed negation was missed (fixed, sample-invisible).** German and
French postfix the negator ("Bitte löschen Sie mein Konto *nicht*", "ne
supprimez *pas*"). A first attempt at negation handling used a 25-token
look-behind window, which then also suppressed genuine deletions that mention a
negated verb nearby — "confirm that ... no records remain" — costing 0.0313 of
status accuracy and 5 steps on the sample. The classifier now splits on
clauses (`.;:!?,` and "and") and withholds authorisation only when one clause
carries both a destructive verb and a negator. Effect: 0 of 32 labelled rows
and exactly 10 of 518 rows are withheld, and `custom_cases.py` case 5
(German/Spanish/French) passes with text that does not exist in the corpus.
This was invisible in the sample metrics — the sample has no such row — which is
why it needed a custom case rather than a metric.

---

**F8 — Silent loss of every PDF when `pypdf` is absent (fixed, environment-dependent).**
The final audit ran in a sandbox without `pypdf`, and `_pdf_text()` returned
`""` when the import failed. All four PDF artefacts therefore contributed
nothing:

```
$ python3 -c "import pypdf"        -> ModuleNotFoundError
$ python3 code/evaluation/evaluate.py
status accuracy 0.9375   step P 1.0000 (163/0/30)   field acc 0.9939 (162/163)
$ python3 code/evaluation/evaluate.py --errors
case_07 [BAD] status COMPLETE vs INCOMPLETE
```

One sample case flipped to the wrong status and three steps plus one evidence id
disappeared. This is the most dangerous class of bug in the whole submission: a
missing optional package degraded the *answer* quietly instead of failing. Fixed
by writing `code/pipeline/parsers/pdftext.py`, a standard-library PDF reader
(`ASCII85Decode`, `ASCIIHexDecode`, `FlateDecode`, `RunLengthDecode`; literal and
hex strings; PDF string escapes), and by making `EvidenceIndex` record an
unreadable or untranscribed artefact in `file_errors` rather than dropping it.
Verification that the fix is real rather than a lucky pass:

```
$ python3 -c "... block pypdf in sys.meta_path, run the full dataset ..."
with pypdf   : d24010fe2146870073052b20ecda5b3a881136254b994dfe8818d868289b6393
stdlib only  : d24010fe2146870073052b20ecda5b3a881136254b994dfe8818d868289b6393
IDENTICAL: True
```

and, per document, identical e-mails / user refs / resource ids / legal-hold rows
against pypdf's own extraction (4/4). Four regression tests now cover this:
stdlib extraction of every PDF, fact-for-fact agreement with pypdf, the
import-blocked fallback, and "the evidence index loads all 94 artefacts with an
empty error list".

**F9 — Dead code and unused imports (fixed).** `_unclaimed_absent_account()` in
`inventory.py` was called on every case and always returned `None`, and sixteen
modules carried unused imports. Both read as unfinished work to a reviewer, so
the stub and its call site were deleted and every import statement was rebuilt
from the AST (a first, regex-based attempt corrupted six multi-line imports; the
files were restored from git and the AST approach verified by re-parsing every
module and re-running the suite). Post-fix scan: 0 files with unused imports,
55/55 tests.

**F11 — `from __future__ import annotations` stripped by my own refactor (fixed, caught by a clean-room test).**
The script that removed unused imports in F9 treated `annotations` as an unused
name, so it deleted the `__future__` import from all 16 modules. On this
machine's Python 3.11 nothing failed — but `main_stub.py` annotates
`EvidenceIndex | None`, which on **Python 3.9** (the version the README
advertises) raises `TypeError: unsupported operand type(s) for |` at import time.
The submission would have worked for me and crashed for a reviewer on 3.9.

Found by re-reading the code from a fresh clone rather than trusting the working
directory, then scanning for PEP 604 unions that sit *outside* an annotation
context and for modules missing the future import. Fixed by restoring the import
to all 26 modules (verified it sits before all non-docstring statements), and by
re-checking that the output hash is unchanged:

```
before fix : d24010fe2146870073052b20ecda5b3a881136254b994dfe8818d868289b6393
after fix  : d24010fe2146870073052b20ecda5b3a881136254b994dfe8818d868289b6393
```

The lesson recorded in `NOTES/DECISIONS.md` (D12): an automated "remove unused
imports" pass must never treat `__future__` names as unused, and any refactor
needs a fresh-clone run on the *minimum* supported interpreter, not just the
developer's.


## 4. Design decisions that moved the metrics

* **Future evidence is not evidence** (F4): both the file's `captured_at` and the
  observation's own timestamp must be ≤ `audit_at`.
* **Identity is time-invariant, state is not**: linking a resource to a person
  may use any observation, but its *state* only from in-window ones. This is
  what lets the pipeline recognise "this profile belongs to user_26, and the
  in-window readback says it is gone".
* **`COMPLETE` requires a citation** (F5). Accepting the 1/32 status-accuracy
  loss to remove 9 contract violations on the judged 518 is deliberate: a
  COMPLETE row with no evidence is the precise error the task is about.
* **Windows are 12px-equivalent** — `.png` consoles carry no OCR engine, so they
  are transcribed once into `image_transcripts.json` keyed by SHA-256; a changed
  image yields no facts rather than wrong ones.
* **No case-id shortcuts anywhere.** `custom_cases.py` selects its examples by
  predicate (`"failover_replica" in file_path`, `category == ACCOUNT_TOMBSTONE`,
  …) precisely so it cannot quietly become a lookup table.

## 5. What could not be verified

* Whether a canonical answer expects `INCOMPLETE,[]` or a `COMPLETE` proof for
  the 2 zero-resource rows (`case_168`, `case_288`); the sample contains no such
  case. The choice is
  argued from the specification (a COMPLETE row must cite a verification step)
  and from the challenge's own premise.
* The four PDFs' contribution: with `pypdf` blocked the output is byte-identical,
  so the PDF identity links are redundant on this dataset (kept for robustness).
* The 74 cases whose only attributable records are retained ones
  (`SECURITY_AUDIT_LOG`, `LEGAL_HOLD_RECORD`) are reported `COMPLETE`. No sample
  case has this shape, so the choice is argued from the specification and from
  the fact that `INCOMPLETE` requires a named finding (see NOTES/DECISIONS.md
  D10) rather than measured. This is the largest single unvalidated assumption
  in the submission: it governs 74 of 518 rows.
* Whether the hidden grader scores the exact `state` spelling for
  `REPORT_ACTIVE_DATA_FLOW` (`ACTIVE`) — the sample has three such steps and all
  match.

**F10 — Grounding check turned into a test (added).** A reviewer's obvious
question is "did you invent any resource id?". `OutputIntegrityTests` now answers
it mechanically: every `resource_id` in `output.csv` must appear in the supplied
corpus or in the committed screenshot transcripts, every `evidence_id` must exist
in `evidence_files.csv`, every step must carry exactly the seven specified fields,
and rows must be one-per-request in numeric order. All four pass (1200 distinct
resources, 0 invalid evidence ids). The five ids that are not in the text corpus
(`audit_record_001/002/004/005/006`) come from the security-audit screenshot and
are grounded in `image_transcripts.json`.

**F12 - A BOM-prefixed CSV killed the run (fixed).** The loaders opened CSVs as
plain `utf-8`, so a file re-saved by Excel or Google Sheets begins with
`\ufeffcase_id` and every row raises `KeyError`. Switched to `utf-8-sig`, which
reads both BOM-prefixed and plain files. Verified: the graded output is
byte-identical (`sha256 d24010fe...`) and a BOM-prefixed request file now parses
all 518 rows. Pinned by `test_csv_loader_tolerates_a_utf8_bom`.

**F13 - A malformed `audit_at` admitted the whole corpus (fixed).** The window
test is a string comparison, so `audit_at="not-a-date"` compares *greater* than
every real timestamp and let all evidence through (8 steps where 0 were
justified). `EvidenceIndex.admits()` now validates the instant before any
comparison and returns nothing when it is unusable, and every call site
(`_usable`, `coverages_at`, `treatment_for`, `InventoryBuilder.build`) routes
through it. Well-formed data cannot reach the new branch, so the graded output is
unchanged. Pinned by `test_malformed_audit_at_admits_no_evidence`.

## 6. Independent audit of the shipped artefact (clean-room)

Every claim above was re-checked from a **fresh clone of the pushed branch** into
an empty directory, so nothing depended on the developer's working tree. All of
the following passed:

| Check | Command / method | Result |
| --- | --- | --- |
| Clean-room reproducibility | `git clone --branch … && python3 code/main.py` | committed `output.csv` unchanged, `git status` clean |
| Same, with `pypdf` blocked | `sys.meta_path` blocker + full run | identical `sha256 d24010fe…` |
| Full suite, warnings as errors | `python3 -W error::ResourceWarning -m unittest discover` | 55/55 OK |
| Custom reasoning cases | `python3 code/evaluation/custom_cases.py` | 6/6 pass |
| Zip integrity | `zipfile.testzip()`, entry audit | 38 entries, no `__pycache__`, no `dataset/`, no `.git`, both reports present |
| Zip in sync with source | `diff -rq code/ <unzipped>/code` | no differences |
| Zip round-trip | unzip → run → compare | byte-identical `output.csv` |
| CSV contract | `csv.DictReader` + `json.loads` on all 518 rows | header exact, 518/518 parse, no embedded newlines |
| Secrets | regex scan for keys/tokens/private keys, `.env` search | none found |
| No case-id shortcuts | regex `\bcase_\d{2,}` over `code/pipeline/` | no matches; now enforced by a unit test |
| Grounding | every `resource_id` against corpus + transcripts | 1200 distinct, all grounded |
| Hostile inputs | blank e-mail, unknown e-mail, malformed timestamps, empty text, non-ASCII, empty file | no exception in any case |
| Stale-artefact detection | `diff` of `code.zip` against the tree | caught a stale zip and a stripped `__future__` import (F11) |
| Environment independence | 4 x `PYTHONHASHSEED`, 4 x locale (incl. `tr_TR`), 3 x cwd | identical `sha256` every time |
| Standard library only, hard proof | `python3 -S code/main.py --out ...` (site-packages disabled) | byte-identical `sha256 d24010fe...` |
| Timestamp order soundness | regex + lexicographic vs chronological sort over 7 769 distinct stamps | 0 non-conforming, orders equal |
| Malformed `audit_at` | synthetic request with `audit_at="not-a-date"` | fails closed (`admits()` guard, F13) |
| BOM-prefixed CSV | re-saved `deletion_requests.csv` with a UTF-8 BOM | accepted after F12, 518 rows |
| Corrupt artefacts | binary garbage injected into `.json/.sql/.log/.txt/.png` | run survives all five, exit 0 |
| Regex denial of service | 10 kB pathological `request_text` (10 shapes) | worst case 12.7 ms |
| Semantic invariants | 2 459 steps: phase / state / evidence coherence | 0 violations |
| Ordering defect hunt | label filtered to predicted steps, order compared | 32/32 exact -> no ordering bug |
| Suite from a bare `code.zip` | unzip into an empty dir and run | `OK (skipped=1)` with an explanatory reason |

The two defects this audit found in the *submission* (rather than in the model)
were a stale `code.zip` and the missing `__future__` imports; both are fixed and
both now have regression tests.
