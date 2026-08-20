# OmniDocBench demo: projection 0.2 and visual metric 2.2

This directory is a versioned follow-up to the historical baseline in the
parent directory. It keeps all 18 official demo pages and strict evidence
alignment enabled. No page was removed, retried selectively, or replaced by a
full-page image fallback.

## What changed

The ten former alignment failures did not originate in fuzzy text matching.
For exactly those ten pages, the conversion used transposed width and height
metadata. The strict projection guard rejected every positioned element before
the matcher could align it. Projection contract 0.2 validates the declared page
dimensions against the annotation geometry and source raster, records the
decision, and corrects only a safely detected width/height transposition.

It does **not** lower an alignment threshold or change the reference Markdown,
polygons, reading order, or source annotation IDs. The public
[alignment diagnostics](alignment-diagnostics.json) retain the before/after
reason counts without redistributing ground-truth content.

## Result

| Measurement | Native | LibreOffice + visual 2.2 |
| --- | ---: | ---: |
| Total pages | 18 | 18 |
| Operational success | 18/18 | 18/18 |
| Operational failures | 0 | 0 |
| Accepted by all measured gates | 2/18 | 2/18 |
| Mean validation-gate score | 0.899691 | 0.886551 |
| Rendered quality coverage | 0/18 | 18/18 |
| Failure-inclusive rendered visual | not measured | **0.214798** |
| Runner wall time | 38.87 s | 251.98 s |

The alignment defect is fixed for this corpus slice, but the quality result is
still weak: only 2 of 18 pages pass every gate. Several pages still fail page
count, physical page size, column flow, source geometry, math, or body-flow
checks. A page can also pass the current gates at a low visual score because the
visual 2.2 safety floor is deliberately minimal and is not a human-calibrated
“high fidelity” threshold.

See [RESULTS.md](RESULTS.md) for every page and failed gate. Machine-readable
reports are [native-report.json](native-report.json) and
[libreoffice-report.json](libreoffice-report.json). The environment and font
lock is [environment-lock.json](environment-lock.json).

## Comparison rules

- This is an **oracle reconstruction** test: it injects official reference
  Markdown and geometry. It measures projection, alignment, planning, DOCX
  reconstruction, and QA—not OCR or parser quality.
- It must not be compared with source-only Docling, MinerU, Marker, PaddleOCR,
  or Tesseract scores.
- The historical report used visual metric 2.1; this report uses metric 2.2.
  The two visual numbers are not directly comparable. Operational success and
  accepted-page counts may be compared because their denominators are unchanged.
- This result does not support a project-wide superiority claim. The source-only
  hard and full OmniDocBench lanes must finish with the official evaluator and
  every timeout, OOM, crash, empty output, and missing output retained.

## Reproducibility and data rights

- `docreconstruct` revision:
  `e4ac0ae4f40f55d63713efb0cae645ab378640c5`
- OmniDocBench demo/evaluator revision:
  `193627ae9e97d89188468ed1ee3b7a856ff76044`
- Converter contract: `omnidocbench-demo-to-canonical-ir/0.2`
- Visual metric profile: `rendered_visual|backend=libreoffice|metric=2.2`

The parent [corpus lock](../corpus-lock.json) records the 18 source-image and
reference-Markdown hashes. OmniDocBench data is research-only, so this repository
does not redistribute images, reference Markdown, annotations, canonical
sidecars, or generated DOCX files.

