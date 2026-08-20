# OmniDocBench demo: oracle-reconstruction baseline

This is the first public, unfiltered external-corpus baseline for
`docreconstruct`. It runs all
18 pages in the official OmniDocBench demo rather than selecting successful
examples after seeing the output.

## Scope and version lock

- Dataset/evaluator source:
  [OmniDocBench at the pinned revision](https://github.com/opendatalab/OmniDocBench/tree/193627ae9e97d89188468ed1ee3b7a856ff76044)
- Dataset/evaluator revision: `193627ae9e97d89188468ed1ee3b7a856ff76044`
- `docreconstruct` revision: `11321aa920917b8c946dec6c5a95fe0f6fa1127e`
- Scope: all 18 pages under `demo_data/omnidocbench_demo`
- Pages: 9 source-document classes, two pages per class
- Languages: 7 English, 10 simplified Chinese, 1 mixed English/Chinese
- Layouts: single, double, triple, mixed-column, and other layouts
- Challenging attributes include fuzzy scans, colorful backgrounds, formulas,
  figures, merged table cells, missing table rules, and wireless tables.

OmniDocBench currently documents 1,651 pages and official end-to-end metrics for
text, formulas, tables, and reading order. This baseline uses only its official
18-page demo. It must not be presented as a result on the full dataset.

## What this lane measures

The lane is named `oracle_reconstruction`:

1. The original OmniDocBench page image is the pixel/layout authority.
2. The official ground-truth Markdown is the wording/reading-order authority.
3. The official annotations are deterministically projected into
   `docreconstruct` canonical IR and used as positioned evidence.
4. The project generates a fresh DOCX and runs native or explicit LibreOffice
   QA.

The conversion keeps page dimensions, polygons and bounding boxes, reading
order, block type, text, table HTML/LaTeX, formula LaTeX, annotation attributes,
and source annotation IDs. Annotations marked `ignore=true` are excluded.

Because both Markdown and geometry are ground truth, this lane does **not**
measure OCR, provider routing, or source-only automation. It cannot be compared
with Docling, MinerU, Marker, PaddleOCR, or other parser scores. A separate
provider-realistic lane must generate Markdown and JSON from the page image
without access to ground truth before such a comparison is valid.

## Published baseline

| Measurement | Native | LibreOffice + visual v2.1 |
| --- | ---: | ---: |
| Total cases | 18 | 18 |
| Operational success | 8/18 (44.44%) | 8/18 (44.44%) |
| Reconstruction failures | 10/18 | 10/18 |
| Accepted by all measured gates | 1/18 (5.56%) | 1/18 (5.56%) |
| Mean native gate score over runnable cases | 0.913194 | 0.904954 |
| Rendered quality coverage | 0/18 | 18/18 |
| Failure-inclusive rendered quality | not measured | **0.104675** |
| Mean visual score among only 8 runnable cases | not measured | 0.235518 |
| Wall time recorded by runner | 23.17 s | 107.06 s |

The primary rendered score is `0.104675`: all ten operational failures
contribute zero. The success-only mean is secondary diagnostic information and
must never replace the failure-inclusive number.

All ten operational failures occurred in strict evidence alignment:

> saved OCR evidence did not match any Markdown block with safe geometry

The canonical sidecars themselves validate. The failure shows that the current
matcher cannot safely align ten of the official ground-truth page structures,
or that the documented OmniDocBench-to-canonical projection does not yet expose
the form of evidence those pages need. Disabling strict mode or removing these
pages would make the headline misleading, so the failures remain in the score.

See [RESULTS.md](RESULTS.md) for every case and failed gate. Machine-readable
reports are available as [native-report.json](native-report.json) and
[libreoffice-report.json](libreoffice-report.json). Input and report hashes are
recorded in [corpus-lock.json](corpus-lock.json) and [SHA256SUMS.txt](SHA256SUMS.txt).
The case selection and expected private-corpus layout are captured in
[reconstruction-benchmark.template.json](reconstruction-benchmark.template.json).

## Reproduction rules

1. Fetch the official repository at the exact revision above into the template's
   `private/OmniDocBench` location. Do not substitute
   a newer annotation file without creating a new result directory.
2. Use every page in `demo_data/omnidocbench_demo/OmniDocBench_demo.json`.
3. Create one case per page. Do not use `--fail-fast`.
4. Use strict evidence, no remote assets, and no online OCR.
5. For the rendered lane, select LibreOffice explicitly and record its binary
   version/hash. Do not use `auto`.
6. Do not impose a newly chosen visual threshold after inspecting results.
7. Keep failures as zero in the rendered aggregate and publish slice results.

The canonical sidecars are research-data derivatives and are not redistributed.
Prepare them according to the conversion contract above, place them under
`private/canonical`, and verify every hash against `corpus-lock.json` before
running. The project command is then:

```powershell
.\.venv\Scripts\docreconstruct.exe benchmark-reconstruction `
  reconstruction-benchmark.template.json `
  --qa-backend libreoffice `
  --qa-renderer-path "C:\Program Files\LibreOffice\program\soffice.exe" `
  --output libreoffice-report.json `
  --output-dir run-artifacts
```

The official OmniDocBench evaluator expects per-page Markdown predictions and
reports text edit distance, formula metrics, table TEDS, and reading-order
metrics. `docreconstruct` currently consumes reviewed Markdown rather than
producing an independent source-only parser prediction, so those official
end-to-end scores are intentionally not claimed here.

## Evidence still required

This 18-page result is a harness/pilot baseline, not sufficient evidence for a
general “high-fidelity” claim. The next public release should keep three tracks
separate:

1. **Parser end-to-end:** the same raw pages go to each parser and the official
   OmniDocBench evaluator scores text, formulas, tables, and reading order.
2. **Oracle reconstruction:** ground-truth authorities go to `docreconstruct`
   to isolate planner/renderer failures, as in this baseline.
3. **Full stack:** each parser's own Markdown/JSON goes through the same DOCX
   reconstruction and reports both upstream semantic quality and downstream
   render/editability changes.

The full 1,651-page suite and its 296-page hard subsets have not yet been run by
this project. Published MinerU/Marker leaderboard values use official semantic
metrics and must not be placed beside visual v2.1 as if they measured the same
thing.

## Data rights

The OmniDocBench repository code is Apache-2.0, but its copyright statement
limits the dataset to research use and disallows commercial use. Therefore this
repository does not redistribute the images, source PDFs, ground-truth
Markdown, annotations, canonical derivatives, or generated DOCX files. It
publishes only identifiers, hashes, protocol details, aggregate/per-case
measurements, and links to the official source.
