# Usage report — final full-dataset run

## Summary

The graded path makes **no model calls**. `output.csv` is produced by a
deterministic, rule-based audit over the supplied evidence, using the Python
standard library only.

```
$ time python3 code/main.py
wrote /home/user/HackerRank_Oct/output.csv rows=518 COMPLETE=172 INCOMPLETE=336 NOT_APPLICABLE=10
real 0m2.405s   user 0m2.386s   sys 0m0.020s
```

The run above was measured with `pypdf` installed. With the import blocked the
same command produces a **byte-identical** file (see §"Reading the PDFs"), so the
figures below hold in either environment.

| Item | Value |
| --- | --- |
| Requests processed | 518 |
| Model providers | none |
| Model names | none |
| Model calls | **0** |
| Prompt tokens | **0** |
| Completion tokens | **0** |
| Total tokens | **0** |
| Average tokens per request | **0** |
| Total cost | **$0.00** |
| Average cost per request | **$0.00** |
| Wall-clock for the full run | 2.4 s (essentially all of it parsing the 94 evidence files) |
| Network access at prediction time | none |
| API keys required | none |

These are measured, not estimated: `grep -rn "openai\|anthropic\|api_key\|requests\.\|urllib" code --include=*.py`
returns no client code (only `http_status`/`rows=` field names inside the log
parser), and the run above completes with no credentials in the environment.

## Why there are zero calls

The task is an evidence-reconciliation problem: reconstruct a state at a
timestamp from 94 supplied artefacts and apply a 15-row policy table. Every
decision the pipeline makes is traceable to a file, a field and a timestamp,
which is exactly what the rubric asks for ("findings and verification steps must
cite a `file_id`, `activity_id`, or `audit_id`"). A generative step would have
added non-determinism and uncitable decisions, so it was deliberately left out
rather than added for its own sake.

The only "model-shaped" component is image understanding: the eleven PNG
console screenshots have no OCR engine available in the runtime. They were read
**visually once during development** and transcribed into
`code/pipeline/parsers/image_transcripts.json`, keyed by the SHA-256 of each
image. At prediction time that file is read as plain data — no inference call,
and a changed image produces no facts instead of wrong ones.

## Reading the PDFs — zero calls either way

Four artefacts are PDFs. They are read by `code/pipeline/parsers/pdftext.py`, a
standard-library decoder (ASCII85 / ASCIIHex / Flate / RunLength filter chains,
literal and hex strings, PDF escapes). When `pypdf` happens to be installed it is
preferred for fidelity, but it is optional and its absence changes nothing:

```
with pypdf   : d24010fe2146870073052b20ecda5b3a881136254b994dfe8818d868289b6393
stdlib only  : d24010fe2146870073052b20ecda5b3a881136254b994dfe8818d868289b6393
IDENTICAL: True
```

Neither path makes a model call, so the token and cost totals above are
unaffected by which one is used.

## Development-time agent usage

The solution was written inside Arena.ai Agent Mode. That harness does not
expose token accounting to the agent, so **no development-token figures are
reported here** — inventing them would be worse than omitting them. The
`tool=`/`model=` fields in `log.txt` follow the same rule: the harness name is
recorded exactly, and the model line says the identifier is not exposed by the
runtime rather than guessing one. That is a deliberate honesty choice under
AGENTS.md §5, which rejects `unknown`, family labels and inferred names alike; the
runtime exposes no model/harness environment variable and no readable session
metadata, so a concrete name could only have been invented.

## Cost if a model were added

Left out on purpose: any per-token figure here would be a hypothetical, and the
brief asks for measured usage. For scale, the entire input the pipeline reasons
over is ~3.5 MB of text across 94 files plus three CSVs, and a run costs 2.4 s of
one CPU core.
