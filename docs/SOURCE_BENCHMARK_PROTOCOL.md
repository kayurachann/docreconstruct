# Source-only OmniDocBench protocol

This protocol is the gate for any future claim that `docreconstruct` is more
accurate or faster than another parser. The workflow exists to collect evidence;
its presence is **not** a benchmark result. No superiority claim is valid until a
complete run finishes, the official evaluator succeeds, and the artifacts are
published without removing failures.

## What this test measures

The existing oracle-reconstruction test injects reference Markdown and reference
geometry to isolate the planner and renderer. This source-only test is different:
each candidate starts from the same official raw page image bytes and must produce
its own Markdown.
No reference text, formulas, tables, reading order, or geometry are passed to a
candidate.

Two manual suites are available:

- `hard`: all 296 pages tagged `equation_hard`, `layout_hard`, or `table_hard`;
- `all`: all 1,651 pages in the pinned OmniDocBench release.

The workflow uses 20 deterministic shards. Page selection preserves annotation
order, then assigns selected page ordinal `n` to shard `n mod 20`. Changing the
shard count changes the execution protocol and must start a separately labelled
run.

## Immutable inputs and separation from ground truth

The dataset is pinned to Hugging Face revision
`aa1ee96d106dbe53d0ae59474d75c6e6d9b53fec`. Its official annotation JSON must
have SHA-256
`a45cd84b04ad8b793e775089640e6b681209abea33ead54c1828ddca35fae496`.

Inference jobs use a committed source index containing only filenames, hard-set
labels, raw byte lengths, and raw SHA-256 values. It contains no recognition or
layout ground truth. Preparation rejects any additional index key, so text,
formula, table, geometry, or reading-order fields cannot silently reach candidate
jobs. A trusted preparation job downloads the pinned release's mixed PNG/JPEG
inputs and checks every byte length and SHA-256 against that index. All four
candidate jobs for a shard restore that one immutable Actions cache entry; no PDF
wrapper, recompression, resampling, or candidate-specific source conversion is
allowed in this lane. Any future PDF-container adapter must be reported as a
separate, non-comparable lane. Cache restoration never substitutes for integrity
checking: both the preparation job and every inference job re-hash every selected
image, reject missing/extra/symlinked files, and compare the cached index and
manifest with the committed source index. Each public shard report carries the
digest of its source-verification record; merged validation recomputes the expected
per-shard file digest from the committed index.

The full OmniDocBench JSON is downloaded only in official-evaluator jobs, after
every inference matrix job has finished. The hard suite then materializes a private
296-page evaluator slice containing all three hard labels; the full suite retains
all 1,651 pages. Candidate argv never contains a ground truth path. This is a
practical separation against accidental annotation leakage, not an adversarial
sandbox. Candidate jobs retain outbound network access for dependency setup, and
the measured process is not placed in a verified loopback-only network namespace.
A malicious upstream CLI could therefore fetch the public GT independently.
`HF_HUB_OFFLINE` protects pinned model-cache integrity; it is not a general network
isolation control. Results under this protocol assume non-adversarial pinned
candidates. A future adversarial-isolation claim requires a separately verified
network namespace that still permits Marker's local llama-server. The private
evaluator slice is never uploaded.

OmniDocBench states that its evaluation data is for research use and not for
commercial use. The workflow is manual, and its short-lived source caches must
not be copied into releases or the repository.

## Candidate lanes

Each page runs in a fresh, process-isolated candidate invocation. A separate
ground-truth-free warm-up job materializes the exact Hugging Face revisions in
`model-pins.json`, hashes the realized cache, and gives that immutable cache key to
every shard for the candidate. Cache keys depend on the declared system/model
versions and pin-manifest hash, not on the workflow commit or hard/full selection,
so a verified cache is reused across both suites. Measured inference forbids remote
Hugging Face access. This protects the failure ledger and prevents shards from
silently receiving different mutable model blobs. Timing is therefore
**model-prewarmed, process-isolated latency**, not first-download latency or warmed
batch throughput.

The cache namespace explicitly includes Docling 2.120.3, MinerU 3.4.5, Marker
2.0.0, Python 3.11, and the SHA-256 of `model-pins.json`; it deliberately excludes
the workflow commit and suite. Cache restoration precedes installation, model
materialization, and warm-up. Marker's dated direct-cache files and rendering font
are byte-size/SHA-256 pinned as well as inventoried. Every inference shard hashes
its restored cache and must match the prepared inventory before its artifact can
be uploaded. On a cache hit, inventories taken immediately before and after the
repeat warm-up must also match; a warm-up that mutates the supposedly immutable
cache invalidates preparation instead of silently changing the full run.

The normalized environment fingerprint also gates the exact Python version and
installed package set, plus sanitized pip archive hashes from the single combined
project/candidate installation report. Thus two wheels with the same declared
name/version but different bytes cannot pass the prepared-vs-inference or
cross-shard gate. For Tesseract it includes the executable bytes/version and
all three traineddata byte hashes; for Marker it includes the realized
`llama-server` bytes/version in addition to the pinned release-archive hash.
Local paths, environment values, and secrets are absent. All 20 shard fingerprints
must be identical, and model-backed shards must also match their prepared
environment fingerprint.

MinerU is deliberately restricted to the 15 model files that its pinned 3.4.5
pipeline loaded in an offline smoke run. Those files total 1,082,446,509 bytes;
each filename, byte length, and SHA-256 is declared and checked. Downloading the
entire 15.13 GB `PDF-Extract-Kit-1.0` repository would exceed a free hosted
runner's disk and is not this protocol.

| Lane | Pinned candidate | Source mode |
|---|---|---|
| `docreconstruct-tesseract` | repository commit under test; Tesseract 5.3.4; `tessdata_fast` revision `87416418657359cb625c412a48b6e1d6d41c29bd` | local OCR, `eng+chi_sim+chi_tra`, original pixels |
| `docling` | 2.120.3, tag commit `46a1103b8c4adc6bbde1e30ec48fd0f7142d5600` | CPU, native Markdown |
| `mineru` | 3.4.5, tag commit `fbb1257a555a3fde78ae5aaaa931e3b3f8fb2883` | pipeline backend, forced OCR, formulas and tables enabled |
| `marker` | 2.0.0, tag commit `947d7688c0739297a7b9eb08b1a463e3a6853981` | fast mode on CPU, llama.cpp b10507 |

The wrappers only copy each tool's native Markdown to the required prediction
name; they do not rewrite its content. Package versions, Hugging Face snapshot
revisions, llama.cpp release bytes, and versioned direct-cache directories are
declared in `model-pins.json`. The resolved transitive environment and realized
model files are inventoried after inference with versions, byte lengths, and
SHA-256 hashes. Until those inventories exist in a completed run, the lanes are
auditable but not yet proven bit-for-bit reproducible across future executions.

The Marker lane is explicitly a CPU `fast` lane chosen to fit a free hosted runner.
It must not be described as Marker `balanced`, as a GPU result, or as the best
accuracy Marker can reach.

## Failures remain in the denominator

`benchmark-source` applies the same 180-second hard per-page timeout to all four
candidates and records success, timeout,
OOM, crash, non-zero exit, missing output, empty output, or invalid output. Every
operational failure materializes as an empty `.md` prediction. Exit code 3 means
one or more retained page failures; the workflow preserves and uploads the shard
instead of treating code 3 as infrastructure failure.

Candidate wrappers re-raise POSIX child signals instead of converting negative
return codes into shell exit codes. The harness can therefore distinguish
SIGKILL/OOM-like termination and SIGSEGV crashes from an ordinary non-zero exit.

Native Markdown is capped at 512 KiB per page. Larger output is recorded as
invalid instead of being truncated into an apparently valid prediction or
allowed to exhaust hosted-runner disk and artifact quotas.

Before evaluation, `validate_predictions.py` requires exactly 296 or 1,651
prediction filenames for each candidate and exactly 20 shard reports. Empty
predictions are counted and hashed. A missing shard therefore invalidates the run
instead of silently shrinking the denominator.

The largest full-suite shard has 83 pages, so its declared sequential worst case
is 14,940 seconds (249 minutes). Manifest creation rejects any shard whose bound
exceeds the 18,000-second inference budget, leaving at least one hour in the
six-hour job for installation, inventory, staging, and upload. This bound is why
all lanes use the same tight cap; latency is reported as cold process-isolated
latency and timeouts are not selectively retried.

Whole-job runner loss, dependency-install failure, or failure to download a pinned
model invalidates that candidate run because the harness cannot create trustworthy
per-page records. Such a run must be reported as incomplete, never converted into
a score.

## Official evaluation

The evaluator source is pinned to OmniDocBench commit
`193627ae9e97d89188468ed1ee3b7a856ff76044`. The canonical official image is
`ghcr.io/zeng-weijun/omnidocbench-eval@sha256:6116ad72172e763b5c43e963d5efebf2093f2362b975f58156ce4f6c9142e617`,
but it is a reference only: its 13.65 GB of compressed layers cannot fit safely on
a free 14 GB hosted-runner disk, and extraction needs still more space. The
workflow does **not** claim to execute that image.

Instead, it follows the official documented native path using Python 3.10, a
hash-locked Python dependency set, the frozen TeX Live 2025 `tlnet-final`
repository, `scheme-minimal` plus the exact LaTeX/CJK packages needed by the
official smoke (`latex-bin`, `latex`, `geometry`, `booktabs`, `multirow`,
`amsmath`, `amsfonts`, `was`, `xcolor`, `cjk`, `cjkutils`, and `arphic`),
ImageMagick 7.1.1-47, and Ghostscript
9.55.0. Downloaded installer/archive bytes are pinned; installed TeX and system
packages and executable hashes are inventoried. The official environment and CDM
smoke tests must pass both when the cache is prepared and immediately before each
score. The prepared evaluator tree records its total files, bytes, and a
path/size/content Merkle-style SHA-256; every scoring job must reproduce that
inventory exactly. This lane must be labelled **official evaluator code / documented native
runtime**, not canonical-container execution.

Text edit distance, formula CDM, table TEDS/edit distance, and reading-order edit
distance are enabled. Quick matching, CDM, and TEDS each run with exactly one
worker. This serial setting avoids a reproduced unsafe-fork interaction between
the pinned evaluator and its locked `filelock` dependency that can otherwise exit
zero while assigning erroneous zero scores. In addition to the official
environment/CDM tests, a one-page official demo runs quick matching and TEDS with
the exact serial settings before scoring. Evaluation is capped at 300 minutes, reserving one hour
inside the six-hour job for setup, staging, and upload. Evaluator timeout, crash,
smoke failure, or non-zero exit is preserved and marks the job invalid; it is not
a candidate score.

An evaluator exit code of zero is not sufficient. Publication also requires
exactly one official metric result and one run summary, all configured metric
keys, an official page-coverage count of exactly 296 or 1,651, and per-metric
page denominators exactly matching a count-only manifest derived from the pinned
selected GT after inference. This safe manifest contains only totals, not
filenames or content. Its pinned contracts are 267/106/107/293 pages for
text/formula/table/reading order in `hard`, and 1,557/313/458/1,638 in `all`.
These counts were cross-checked with the pinned evaluator using complete empty
prediction sets; they validate denominator semantics and are not accuracy scores.
Missing, duplicate, partial, or malformed output is staged
as an invalid run and fails the job. The six required headline values are checked
at their exact official JSON paths and must be finite numbers; `null`, strings,
booleans, NaN, and infinity are rejected rather than published as scores.
The official execution report must also confirm one worker and zero timeout,
error, and exception cases for CDM/TEDS; any non-zero metric failure count
invalidates the run even if the evaluator process exited zero.

Public inference artifacts include native predictions, failure records, the JSONL
ledger, reports, source hashes, model-prewarmed process-isolated timings, and
redacted environment inventories. They explicitly exclude `_private_logs`, `_work`, and
`_evaluation/selected-annotations.json`. Evaluator artifacts are uploaded even
after evaluator failure so the failure can be audited. They contain aggregate
metrics, a curated count-only execution summary, provenance, and the prediction
manifest. Raw
official-evaluator stdout/stderr can echo GT-derived LaTeX or text, so only their
byte lengths and SHA-256 digests are public. Raw run-summary, stage-execution, and
runtime-environment reports are also excluded because exception records can contain
GT snippets and environment reports contain local paths. Metric JSON is reduced to
six validated headline aggregates. The curated execution summary retains only
page/denominator totals, workers, timeouts, and failure counts; it strips filenames,
case records, exception reasons, paths, and optional matcher-debug payloads. Raw GT,
the private hard slice, and per-element matched-sample JSON are excluded.

## Running the protocol

Open **Actions → Source-only OmniDocBench benchmark → Run workflow**, select
`hard`, and inspect all four official-evaluator jobs. Run `all` only after the hard
suite completes. Do not edit artifacts or re-run only successful shards to assemble
a result. A report is publishable only when its workflow URL, repository commit,
dataset revision, 20 shard reports, prediction manifest, environment inventories,
official evaluator revision, native runtime inventory, canonical-image reference,
and all failure counts are retained together.
