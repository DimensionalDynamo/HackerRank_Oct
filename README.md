HackerRank Orchestrate — October 2026 — "Ghost Data Still Exists"
==========================================================================

An evidence-driven deletion auditor. For every request it decides whether the
customer actually authorised deletion, reconstructs the customer's current and
historical identifiers, replays the supplied evidence up to the case's
`audit_at` instant, applies `dataset/deletion_rules.csv` to every record it can
attribute, and emits the ordered findings / remediation / verification steps.

The design goal is the one in the problem statement: **the workflow's own
"Complete" claim is never the verdict.** Every verdict is reconstructible from
a file that existed at or before `audit_at`.


Quick start
-----------

```bash
python3 code/main.py                    # writes ./output.csv (518 rows)
python3 code/main.py --validate         # writes, then checks the output contract
python3 code/main.py --dataset sample_deletion_requests.csv --out /tmp/sample.csv
python3 code/evaluation/evaluate.py     # score against dataset/sample_output.csv
python3 code/evaluation/evaluate.py --errors   # per-case diff
python3 code/evaluation/custom_cases.py        # six hand-built reasoning cases
# 55 unit tests (skips cleanly if dataset/ or output.csv is absent)
python3 -m unittest discover -s code/tests
```

Requirements: Python 3.9+ and the standard library only for the graded path.
`code/requirements.txt` records the single optional dependency (`pypdf`, used to
read the four PDFs) and the pipeline degrades gracefully without it — the PDF
parser skips unreadable pages instead of failing the run. No network access, no
API key and no model call is required to produce `output.csv`; the run is
deterministic (verified by byte-comparing repeated runs).


How a case is decided
---------------------

1. **Intent** (`code/pipeline/intent.py`). Destructive-intent patterns compete
   with non-deletion markers; a clause that contains both a destructive verb and
   a negator withholds authorisation, so marketing opt-outs, access requests,
   corrections and "do not close my account" templates become `NOT_APPLICABLE`.
   Multilingual by construction (English, French, Spanish, Portuguese, Japanese,
   Korean). 10 of 518 rows are withheld.

2. **Identity** (`code/pipeline/identity.py`). The request e-mail is resolved
   against current identities *and* superseded values in `user_audit_log.csv`
   (103 of the requests quote an address the customer no longer owns), plus
   phone and `sha256(email)` hashes. The subject bundle therefore contains every
   identifier the customer has ever had.

3. **Evidence index** (`code/pipeline/evidence.py`, `code/pipeline/parsers/`).
   All 94 artefacts are parsed into observations of the form
   `(resource_id, system, category, state, observed_at, evidence_id, identifiers)`:
   * logs — state on `*_completed` lines, identity on `*_started` lines, joined
     on `trace_id`; readback traces give 404/rows=0 → ABSENT, rows=1 → PRESENT;
   * SQL snapshots — INSERT rows replayed per table, primary vs replica drift,
     JSON payloads flattened for identifiers;
   * JSON bundles — service exports, snapshot bundles, ledger manifests;
   * plain text — shift-handover tables;
   * PDFs — `pypdf` extraction;
   * PNGs — read visually and cached in
     `code/pipeline/parsers/image_transcripts.json`, keyed by SHA-256 so a
     changed image yields no facts rather than wrong ones.
   Snapshots also record **coverage** ("this capture covers all profiles"),
   which is what lets the pipeline prove an absence rather than assume one.

4. **Inventory** (`code/pipeline/inventory.py`). Only observations whose own
   timestamp *and* whose file's `captured_at` are ≤ `audit_at` may describe
   state; identity linking is time-invariant. The newest admissible witness wins,
   with in-window artefacts preferred over a later export of an older snapshot.

5. **Policy + plan** (`code/pipeline/policy.py`, `code/pipeline/planner.py`).
   `deletion_rules.csv` maps each category to DELETE / REDACT / ANONYMIZE /
   RETAIN / block-restore. Non-compliant state emits a FINDING plus its
   REMEDIATION; compliant state emits the matching VERIFICATION. `COMPLETE`
   requires at least one verification step with a real evidence id; anything
   else is `INCOMPLETE`. A deletion request whose evidence supports nothing is
   `INCOMPLETE` with an empty step list — never `COMPLETE` and never
   `NOT_APPLICABLE`.


Technology stack
----------------

| Layer | Choice | Why it is the right call here |
| --- | --- | --- |
| Language | Python 3.9+ (developed and verified on 3.11.2) | matches the starter repo; no install step for the reviewer |
| Runtime dependencies | **standard library only** | `output.csv` can be regenerated on a machine with no network, no package index and no credentials |
| Optional dependency | `pypdf==6.21.2`, pinned in `code/requirements.txt` | higher-fidelity PDF reading when present; **not required** — see below |
| PDF reading, dependency-free | `code/pipeline/parsers/pdftext.py` | decodes `ASCII85Decode`/`ASCIIHexDecode`/`FlateDecode`/`RunLengthDecode` filter chains, literal and hex strings, PDF escapes; verified to recover the same facts as `pypdf` on all four documents |
| Data access | CSV via `csv`, snapshots via SQL text replay, logs line-by-line, JSON via `json`, PDFs via `pdftext`, screenshots via hashed transcription cache | every artefact class in the corpus has an explicit reader; nothing is guessed |
| Concurrency | none | the whole 518-case audit takes about 4 s single-core (3.86-4.09 s measured on a 2-core Xeon @ 2.60 GHz), so threads would add failure modes for no gain |
| Persistence | none | each run replays `dataset/` from scratch; no state can drift between runs |
| Determinism | explicit `case_sort_key`, no clock, no randomness, no network | three consecutive runs are byte-identical; `sha256 d24010fe…` |
| Testing | `unittest` (55 tests) + a six-case reasoning suite + a golden-sample scorer | all offline and deterministic, so they can run in the grader's environment too |
| Environment-invariant | 4 hash seeds, 4 locales (incl. `tr_TR`), 3 working directories | every run is byte-identical, so the grade cannot depend on the reviewer's machine |
| Input tolerance | `utf-8-sig` CSV reading, per-artefact exception guards | works with BOM-prefixed exports and with any single artefact corrupted |
| Time-window soundness | `EvidenceIndex.admits()` validates `audit_at` before any comparison | a malformed instant authorises nothing instead of silently admitting everything |

**On the pypdf fallback.** During the final audit the grading sandbox turned out
to have no `pypdf`, and the old `_pdf_text()` returned `""` when the import
failed. That silently discarded all four PDF artefacts: one sample case flipped
to the wrong status and three steps plus one evidence id vanished (status
accuracy 0.9688 → 0.9375). The fix was not to require the package but to write a
standard-library PDF reader and to make an unreadable artefact record itself in
`EvidenceIndex.file_errors` instead of disappearing. The full output is now
byte-identical with `pypdf` installed and with the import blocked, and the
evidence index loads all 94 artefacts with an empty error list.

AI agents and models actually used
----------------------------------

| Where | What ran | In the shipped artifact? |
| --- | --- | --- |
| Authoring this solution | **Arena.ai Agent Mode** (coding agent, this session) | No — development-time only |
| Deciding any individual case | **deterministic rule engine**: intent classifier → identity resolver → evidence replay → policy table → planner | **Yes — this is the product** |
| Reading the 11 PNG consoles | agent visual reading (no OCR engine in the runtime), transcribed once into `parsers/image_transcripts.json` keyed by SHA-256 | Frozen **data**; no inference at run time |
| Reading the 4 PDFs | `pdftext` (standard library) with `pypdf` as an optional accelerator | Yes |
| Model calls at prediction time | **none** | — |
| Tokens / cost | **0 prompt + 0 completion tokens, $0.00** | see `code/evaluation/usage_report.md` |

**Why no LLM in the decision loop.** The specification requires every finding and
every COMPLETE-case verification to cite a `file_id`, `activity_id` or
`audit_id`, and to describe the state at a single `audit_at` instant. A
generative step would add non-determinism and, worse, decisions that cannot be
traced to an artefact. The "agent" in this submission is therefore a rule engine
over evidence: it is autonomous in that it resolves identities, discovers
records and derives verdicts without per-case input, but every step it emits
points at a file that existed at `audit_at`. The coding agent wrote that engine;
it does not run inside it.

**Why the model field in `log.txt` is not a model name.** The Arena runtime does
not expose the underlying model identifier to the agent — there is no
model/harness environment variable and no readable session metadata in the
sandbox. AGENTS.md §5 forbids guessing a name or writing a family label, so the
log records `model=not-exposed-by-runtime` with an explicit note. That is a
deliberate accuracy choice, not an omission.

Repository layout
-----------------

```
code/main.py                    CLI: --dataset, --out, --validate
code/pipeline/main_stub.py      shared entry point (CLI + evaluator)
code/pipeline/intent.py         multilingual deletion-intent classifier
code/pipeline/identity.py       current + historical identifier resolution
code/pipeline/evidence.py       evidence index, capture-time gating
code/pipeline/parsers/          log, sql, json, text, pdf (stdlib + pypdf), image parsers
code/pipeline/inventory.py      per-case resource inventory at audit_at
code/pipeline/policy.py         deletion_rules.csv → required treatment
code/pipeline/planner.py        ordered step list (output-format law)
code/pipeline/serialize.py      writer + contract validator
code/evaluation/evaluate.py     golden-sample scorer (6 metrics, per-case diff)
code/evaluation/custom_cases.py six reasoning cases + results JSON
code/evaluation/evaluation_report.md
code/evaluation/usage_report.md
code/tests/test_pipeline.py     55 unit / regression tests
NOTES/                          working notes: facts, decisions, todo
```


Results (reproduced by the commands above)
------------------------------------------

Golden sample (32 labelled cases): status accuracy 0.9688, step precision
1.0000, recall 0.8601, F1 0.9248, evidence-field accuracy 1.0000, sequence
accuracy 0.4688. Full dataset: 518 rows — 172 COMPLETE, 336 INCOMPLETE,
10 NOT_APPLICABLE; 0 contract violations; byte-identical across runs.

The residual recall gap is analysed in `code/evaluation/evaluation_report.md`:
the 27 missing steps name records that appear nowhere in the supplied evidence.
