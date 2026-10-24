# TODO / known gaps

## Done

- [x] Evidence parsers for all 94 artefacts (log, SQL, JSON, TXT, PDF, PNG).
- [x] Identity resolution over current + historical e-mail, phone and hash.
- [x] Time gating on both the observation timestamp and the file `captured_at`.
- [x] Policy table from `deletion_rules.csv`; planner emits the ordered step law.
- [x] Contract validator in `serialize.py`; 0 violations on the 518 rows.
- [x] Golden-sample scorer, six custom reasoning cases, 37 unit tests.
- [x] Determinism: three runs byte-identical; identical with `pypdf` absent.
- [x] Report the 12 zero-resource rows as `INCOMPLETE,[]` instead of a fake
      `COMPLETE,[]`.
- [x] Image transcripts for the eleven console screenshots (SHA-256 keyed).

## Open, with why

- [ ] **27 missing sample steps.** `VERIFY_RECORD_ABSENT` on `account_*`,
      `VERIFY_RECORD_ANONYMIZED` on `analytics_*`, `VERIFY_RECORD_PRESENT` on
      `audit_record_*`. The resource ids occur only in `system_activity`
      deletion claims (not admissible as fact) and the anonymised analytics rows
      carry no identifier anywhere in the corpus. Recovering them would need a
      case-id lookup or an invented identity bridge — both forbidden. Kept as
      an honest recall gap (recall 0.8601, precision 1.0000).
- [ ] **Sequence accuracy 0.4688** is a strict consequence of the above: no
      scored case currently differs only in ordering. Re-check after any recall
      work; the ordering law itself is already unit-tested and contract-checked.
- [ ] **OCR.** No engine in the runtime (checked: no `tesseract`, no
      `pytesseract`, no `easyocr`). Images are transcribed into a hashed cache
      instead. If an OCR engine becomes available, `imagedoc.py` could fall back
      to it for uncached images.
- [ ] **`INCOMPLETE,[]` vs `COMPLETE` proof for the 12 zero-resource rows.** The
      sample has no such case, so the choice is argued from the specification
      rather than measured. Revisit only if an organiser clarification appears.

## Deliberately not done

- No case-id, e-mail or request-text lookup anywhere in `code/` (the custom
  evaluation cases select their examples by predicate for the same reason).
- No model calls at prediction time; `usage_report.md` reports zero tokens
  rather than a hypothetical.
- No dependency beyond the standard library for the graded path.
