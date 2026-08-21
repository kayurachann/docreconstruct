# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/); versions follow SemVer once
the project reaches a stable API.

## [0.1.0] — 2026-08-21

First tagged release. Everything below was landed across seven merged pull
requests in this cycle; earlier history predates tagging.

### Fixed — correctness and fidelity (54+ audited defects, three adversarial rounds)
- TeX→OMML: unbraced script arguments no longer swallow the rest of the
  expression (`a^2+b^2=c^2` rendered as `a2`).
- Markdown: list handling, display-`$$` inside paragraphs, currency `$5 and
  $10` false math, UTF-8 BOM demoting titles, block-marker round-tripping.
- OOXML: child-element ordering that made Microsoft Word "repair" documents
  (LibreOffice tolerated it, so QA missed it); XML-illegal control characters
  no longer abort a whole render.
- Renderers: `Page.rotation` honoured; local image paths and WEBP no longer
  crash DOCX output; math row height is no longer read as a font size;
  row merging no longer joins ink that shares no horizontal extent.
- Benchmarks: crashed cases now drag the mean instead of vanishing; the OCR
  benchmark publishes `mean_overall_strict`/`mean_measurement_coverage`;
  editability no longer scores an empty DOCX as perfect.
- Providers: `.jpg`/`.tif` capability aliases, Mistral page-range off-by-one,
  PaddleOCR-VL PDF-vs-image `fileType`, EXIF orientation in page frames.

### Fixed — security
- Upload API can no longer select the executable the server runs
  (`executable`/`binary`/`cmd`/`command` plus suffix rules blocked).
- Error responses no longer echo absolute server filesystem paths.
- Page budgets on `/v1/analyze` and `/v1/route` (an 828 KiB PDF no longer
  expands to 5 000 pages of response).
- Windows reserved device filenames in uploads are neutralised.

### Added
- Server-local Tesseract as a credential-free hosted-OCR option; `fast`
  quality returns the artifact with honest QA headers, `verified` fails closed.
- Docker `full` target (API + Tesseract vie/eng/chi_sim + LibreOffice),
  built and smoke-tested in CI.
- Showcase regeneration gate: every committed showcase rebuilds from its
  committed `content.md` in CI and fails on drift.
- Dependabot, pip-audit CI job, deployment and self-hosting documentation.

### Changed
- README rewritten around measured numbers (23–57% visual on showcases;
  the stale "92.58%" claim corrected to the re-measured 31.46%).
- Repository description no longer claims "high-fidelity"; the project is
  labelled experimental until measurements support more.
- GitHub Pages site taken down until a real backend exists.

### Known limitations
- Page fill is compressed on 1.5-spaced sources; source-locked layout is in
  progress. Two-column mastheads render stacked. No published comparison with
  pandoc, Docling, MinerU, Marker or commercial converters yet.
