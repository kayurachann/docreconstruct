# docreconstruct

[English](README.md) · [Tiếng Việt](docs/i18n/README.vi.md) ·
[简体中文](docs/i18n/README.zh-CN.md) · [Русский](docs/i18n/README.ru.md)

**Turn document pixels into editable document structure.**

`docreconstruct` is a model-agnostic Python framework for reconstructing PDFs,
scans, and images as structured, editable documents. It keeps coordinates,
reading order, style evidence, relationships, confidence, and provenance in a
canonical document IR instead of reducing every source to Markdown.

> OCR recovers the words. Reconstruction recovers the document.

## Web client

After the Pages workflow is deployed, the static client is intended to be
available at
[kayurachann.github.io/docreconstruct](https://kayurachann.github.io/docreconstruct/).
It is a browser interface, not a hosted reconstruction service: GitHub Pages
cannot run Python, LibreOffice, Triton, vLLM, or GPU OCR. This repository does
**not** include a public backend or an unlimited free GPU endpoint.

Before submitting a document, the user must choose a backend operated by an
organization they trust. The high-fidelity workflow requires reviewed
Markdown, the original PDF/image, and positioned JSON evidence. That JSON can
be uploaded as a saved OCR result or created by a hosted OCR service that the
user explicitly selects. The user must consent before any of these files leave
the device. If hosted OCR is enabled, the backend may forward the original to
the selected OCR operator. Retention, privacy, region, quota, and charges are
governed by those operators, not by GitHub Pages or this repository. The client
blocks submission until that disclosure is accepted and asks for consent again
when the backend or OCR choice changes. See
[Performance and public deployment](docs/PERFORMANCE.md) before publishing or
using a backend.

This repository is an early v0.1 foundation. It provides the portable IR,
adapter contracts, deterministic planning and rendering, and evaluation
building blocks. It does **not** bundle or silently download heavyweight OCR
models. PaddleOCR, MinerU, olmOCR, and future engines remain separate projects;
users or backend operators are responsible for installing, operating, or
contracting with any inference system they choose. An open-source OCR engine
does not imply free hosted compute.

“High fidelity” is a design target for the three-authority workflow, not a
claim of universal accuracy. The project has not yet published a full
OmniDocBench end-to-end parser score or a controlled comparison with Docling,
MinerU, or Marker.

## Public benchmark status

The oracle-reconstruction lane now runs **all 18 pages** of the official
OmniDocBench demo at pinned revision `193627a`. It uses ground-truth Markdown
and ground-truth geometry to isolate reconstruction behavior; it is not an OCR
comparison.

| Result | Measured value |
| --- | ---: |
| Operational success after projection fix | **18/18 (100%)** |
| Accepted by every measured gate | **2/18 (11.11%)** |
| Strict evidence-alignment failures | **0/18** |
| Failure-inclusive LibreOffice visual v2.2 score | **0.214798** |

The former 10/18 failures were traced to transposed page dimensions in the
OmniDocBench-to-canonical projection, not to fuzzy matching. Correcting that
conversion without weakening strict mode produces DOCX output for all 18 pages,
but only two pages pass every measured gate. The renderer/planner result is
therefore still weak. See the versioned [projection 0.2 reports and per-page
failure ledger](benchmark/omnidocbench-demo/projection-0.2-metric-2.2/README.md)
and the preserved [historical baseline](benchmark/omnidocbench-demo/README.md).
Visual 2.1 and 2.2 scores are not directly comparable.

The repository also contains a failure-inclusive source-only harness and a
free local Tesseract lane. The 296-page hard and 1,651-page full source-only
runs have not yet completed, so they are not results and are not evidence of
superiority.
Until a provider-realistic lane is run through the official OmniDocBench
semantic evaluator, this repository must not claim benchmark superiority over
document parsers or OCR systems.

## What v0.1 can do

- Represent pages and elements in a strict Pydantic `Document` model with
  geometry, styles, relationships, confidence, text candidates, and provenance.
- Inspect raster images and optionally born-digital PDFs without discarding
  source geometry.
- Run a bounded local Tesseract OCR adapter for a free, private baseline; it is
  intended for accessibility and benchmarking, not advertised as the best
  quality lane.
- Normalize saved PaddleOCR, MinerU, and olmOCR evidence through provider
  adapters, call explicitly authorized hosted specialists, or connect to an
  operator-managed full PaddleOCR-VL server through `paddleocr_vl_server`.
- Build cost-aware page and region routing plans: native extraction first,
  specialist engines for tables/formulas/handwriting, and multi-engine
  adjudication only for uncertain or disagreeing evidence.
- Infer reading order and build traceable, profile-aware reconstruction plans.
- Render canonical IR as JSON, self-contained fixed-page HTML, Markdown, or a
  basic semantic DOCX (the DOCX dependency is optional).
- Rebuild a paginated editable DOCX from a Markdown content authority and a
  PDF or raster-image layout authority. The hybrid path keeps paragraphs,
  tables, and Office Math equations native; reuses cached Markdown image URLs;
  rectifies photographed pages; recovers multi-column geometry; and uses the
  scan to retain complete source figures. Multi-page PDFs retain one Word
  section per source page, including evidenced cross-page continuations and
  blank/OCR-omitted pages.
- Score text, layout, structure, visual evidence, and editability when the
  required reference data is available.
- Benchmark the complete three-authority reconstruction job, including fresh
  DOCX generation, native/LibreOffice QA, per-phase timings, failure capture,
  reproducible fingerprints, and document-family slices.
- Use the same synchronous pipeline from Python, the CLI, or an optional
  FastAPI upload service.
- Validate optional VLM/LLM correction proposals against existing object IDs
  and OCR candidates so an adjudicator cannot silently invent source text.

The built-in renderers are deliberately modest. XLSX, PPTX, reconstructed PDF,
bundled heavyweight model execution, and an automatic render/compare/correct
critic are roadmap work. Live OCR adapters call separately operated services;
they do not turn the API or GitHub Pages into a bundled inference platform. The
current DOCX renderer favors native paragraphs and tables over hundreds of
hard-to-edit positioned text boxes, so it should not be described as
pixel-perfect.

## Best-evidence input: Markdown + JSON + original

The high-fidelity workflow requires all three complementary inputs below. They
are not interchangeable. When positioned JSON is missing, the user must either
upload it or explicitly ask a hosted OCR provider to create it before
high-fidelity reconstruction begins:

| Input | Authority retained by the project |
| --- | --- |
| Reviewed `content.md` | Exact wording and intended reading sequence; never silently rewritten |
| One or more OCR/layout `.json` sidecars | Page/block association, coordinates, semantic type, style, confidence, and provenance |
| Original PDF or raster image | Physical page geometry, pixels, columns, tables, figures, and source crops |

Each JSON provider is normalized and aligned independently before consensus.
JSON may improve geometry or structure but cannot replace Markdown wording;
the original file remains the final pixel and page-geometry authority. A JSON
file that contains only loose text is not positioned evidence: it must identify
pages and blocks and include page dimensions plus bounding boxes or polygons.
A simpler best-effort path may run without one of the three sources, but its
output must be presented as lower-confidence, not as a high-fidelity result.

### Need positioned JSON? Choose a source

These are integration choices, not a quality ranking. Limits and prices can
change; follow the linked official page and the terms shown in the user's own
account before uploading a document. Never publish a shared API key in the
GitHub Pages JavaScript.

| Choice | Useful for | What the user should know |
| --- | --- | --- |
| [PaddleOCR official API / AI Studio](https://www.paddleocr.ai/latest/en/version3.x/inference_deployment/serving/paddleocr_official_api/overview.html) | Multilingual scans, photographed pages, tables, formulas, and paired Markdown/JSON | Uses the user's Baidu AI Studio access. The [current quota page](https://ai.baidu.com/ai-doc/AISTUDIO/pmjcld5qm) lists 3,000 pages per day per model per user and parses at most the first 100 pages of a larger file; this is a changeable service quota, not an SLA. The cited API pages do not make a PaddleOCR-specific retention promise, so review Baidu's current policy before sending sensitive material. |
| [Mistral OCR](https://docs.mistral.ai/api/endpoint/ocr) | Complex scans and structured Markdown/JSON, including optional annotations and bounding boxes | A user-owned key and cloud upload are required. The [pricing page](https://mistral.ai/pricing/api/) currently charges per page; any experimental allowance or account limit is not a promise of free production capacity. [Zero Data Retention](https://help.mistral.ai/en/articles/347612-can-i-activate-zero-data-retention-zdr) is restricted to eligible paid plans and does not cover every file or batch route. |
| [Mathpix](https://docs.mathpix.com/) | Mathematics, STEM pages, handwriting, Mathpix Markdown, and line geometry | Mathpix [does not offer a free trial](https://website.mathpix.com/docs/convert/billing) and charges setup/usage fees. Its [authentication guide](https://docs.mathpix.com/reference/authentication) says not to place secret keys in client-side code; PDF processing therefore needs a trusted backend. Its [retention page](https://docs.mathpix.com/concepts/data-retention) currently states up to 30 days for source/page images and 90 days for text output. |
| [Azure Document Intelligence](https://learn.microsoft.com/en-us/azure/ai-services/document-intelligence/prebuilt/layout?view=doc-intel-4.0.0) | Forms, tables, reading order, Markdown, and JSON polygons | Requires the user's Azure resource and credentials. The [F0 service limits](https://learn.microsoft.com/en-us/azure/ai-services/document-intelligence/service-limits?view=doc-intel-4.0.0) currently include 500 pages per month but only the first two pages per request; the [FAQ](https://learn.microsoft.com/en-us/azure/ai-services/document-intelligence/faq?view=doc-intel-4.0.0) says analysis inputs/results are temporarily stored in the selected region and deleted within 24 hours. |
| [Google Document AI](https://docs.cloud.google.com/document-ai/docs/overview) | Enterprise OCR, forms, tables, and block/word geometry | Requires a Google Cloud project, processor, billing, and OAuth credentials; never ship a service-account key in a static site. The [pricing page](https://cloud.google.com/products/document-ai/pricing) currently includes the first 1,000 Enterprise Document OCR pages per account per month at no charge, then usage pricing. Google says in its [security documentation](https://docs.cloud.google.com/document-ai/docs/security) that customer documents and predictions are not used to train Document AI models. The project must derive Markdown from the returned Document JSON. |
| [OCR.space](https://ocr.space/ocrapi) | A quick browser-direct option for short, non-sensitive files | Its official page documents browser AJAX and a free plan, currently 500 requests per day per IP, 25,000 per month, 1 MB per file, and at most three PDF pages, with no SLA. Use the user's own key because a browser key can be copied and exhaust its quota. The provider states that uploaded documents and OCR text are not stored. |
| [olmOCR](https://github.com/allenai/olmocr) | Open-source processing of difficult PDFs, handwriting, math, tables, and multi-column reading order | The code is Apache-2.0, but local use needs a capable GPU and remote providers charge for compute. The [public demo](https://olmocr.allenai.org/) is for evaluation, not a documented production API or SLA. |
| [Hugging Face public Spaces](https://huggingface.co/spaces/PaddlePaddle/PaddleOCR-VL-1.6_Online_Demo) | Trying a model before choosing a deployment | A public Space is a demo run by its owner. [ZeroGPU](https://huggingface.co/docs/hub/main/spaces-zerogpu) has small account-dependent daily GPU-minute quotas, queues, and execution limits; [dedicated Inference Endpoints](https://huggingface.co/docs/inference-endpoints/en/pricing) are paid. Do not use a demo endpoint as the project's production backend. |

```bash
docreconstruct hybrid content.md original.pdf \
  --evidence paddleocr.json \
  --evidence mineru.json \
  --output output/result.docx \
  --qa-report output/result.qa.json
```

Repeat `--evidence` for independent providers. If automatic schema detection
is ambiguous, bind a file explicitly with
`--evidence-provider result.json=paddleocr` rather than allowing the project to
guess.

Provider page labels are matched exactly by default. A complete, same-length,
consecutive sequence may be rebound by ordinal position (for example OCR pages
5–6 beside a two-page crop numbered 1–2), with an audit warning. Partial or
irregular sequences are never remapped speculatively. Provider-specific
coordinate units and preprocessing metadata are retained during normalization
so geometry can be projected back to the original page rather than treated as
untyped pixels.

### Deterministic render input and fast evidence matching

The evidence matcher pre-indexes exact normalized spans and monotonic anchors,
then evaluates bounded candidate windows instead of repeatedly comparing every
OCR block with every Markdown span. If fewer than two reliable anchors exist,
or the index cannot prove that its candidate set is complete, it returns to the
original exhaustive search. Thresholds, ordering, and accepted matches remain
the same; the index is a performance optimization, not a looser matching mode.

Each hybrid job now prepares its source analysis, asset/table matches, and
layout plan once. The DOCX renderer and native/LibreOffice QA inspect that same
prepared plan rather than rebuilding a similar plan later. A canonical SHA-256
render-input digest covers the source authority hashes, normalized Markdown and
scan models, canonical page-raster bytes, layout plan, asset/table matches,
remote-asset policy, and hashes of the exact snapshotted asset bytes. Matching,
rendering, and QA reuse those snapshots rather than reading a mutable image a
second time. The renderer stores the digest in the DOCX core `identifier`
property, and QA checks the in-memory plan, the standard core-properties
relationship, the identifier embedded in the artifact, and the candidate
DOCX SHA-256 before and after validation.

This digest makes accidental plan drift and asset changes detectable. It does
not prove that OCR wording, formulas, or layout interpretation are correct.

### Multi-page originals

Multi-page PDFs are analyzed and planned one page at a time. Each source page
becomes a separate Word section with its own physical page size and a forced
new-page boundary. A semantic group may continue across pages when independent
evidence anchors prove the continuation, and a blank or OCR-omitted source page
is preserved as an empty section rather than stealing content from the next
page. Native QA verifies the planned section count; explicit LibreOffice QA
also requires the rendered page count to equal the source page count.

```bash
docreconstruct hybrid complete-document.md multi-page-original.pdf \
  --evidence provider-result.json \
  --output output/complete-document.docx \
  --qa-backend libreoffice \
  --qa-report output/complete-document.qa.json
```

## Successful reconstruction examples

These are real outputs from the same generic hybrid reconstruction path used by
the CLI. The full-resolution source images, downloadable editable DOCX files,
and project-rendered previews are included so results can be inspected rather
than accepted on screenshots alone. See the [showcase artifact notes](docs/showcases/README.md)
and [SHA-256 manifest](docs/showcases/SHA256SUMS.txt) for provenance details.

> **Verification required:** OCR and provider-exported Markdown can contain
> spelling, diacritic, symbol, formula, table, or reading-order errors. The DOCX
> can faithfully preserve those errors because Markdown is the content
> authority. Always compare the editable result with the original source before
> relying on it.

### Tuyen Quang gifted school Math exam - Page 1 - Exam code: 0110 (Source: VietnamNet)

**Source:** [VietnamNet](https://vietnamnet.vn/) — attribution supplied by the
contributor; see the showcase rights notice.

| Original photographed page | Editable DOCX rendered by the project |
| :---: | :---: |
| [<img src="docs/showcases/math-exam/source-original.png" alt="Original Tuyen Quang math exam page" width="420">](docs/showcases/math-exam/source-original.png) | [<img src="docs/showcases/math-exam/rendered-preview.png" alt="Rendered editable math exam DOCX" width="420">](docs/showcases/math-exam/rendered-preview.png) |

**Artifacts:** [original image](docs/showcases/math-exam/source-original.png) ·
[editable Word file](docs/showcases/math-exam/editable.docx) ·
[rendered preview](docs/showcases/math-exam/rendered-preview.png)

This example demonstrates a photographed page, mixed header geometry, native
Word tables, editable Office Math, four-choice answer layouts, and reuse of the
source variation chart. Handwriting, photo distortion, missing OCR text, and
some source furniture are not guaranteed to be reproduced as editable content.

### Calculus derivation - editable Office Math (Source: PaddleOCR)

**Source:** [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR) — OCR/export
attribution supplied by the contributor.

| Original source | Editable DOCX rendered by the project |
| :---: | :---: |
| [<img src="docs/showcases/calculus-derivation/source-original.jpg" alt="Original calculus derivation" width="420">](docs/showcases/calculus-derivation/source-original.jpg) | [<img src="docs/showcases/calculus-derivation/rendered-preview.png" alt="Rendered editable calculus DOCX" width="420">](docs/showcases/calculus-derivation/rendered-preview.png) |

**Artifacts:** [original image](docs/showcases/calculus-derivation/source-original.jpg) ·
[editable Word file](docs/showcases/calculus-derivation/editable.docx) ·
[rendered preview](docs/showcases/calculus-derivation/rendered-preview.png)

This example demonstrates native, selectable Office Math for fractions,
integrals, limits, scripts, aligned derivations, and mixed Chinese prose. The
generic planner maps 10 editable blocks to all 18 source rows; the final DOCX
renders as one A4 page, keeps 8 native Office Math expressions and 13 display
rows, and does not expose TeX alignment markers. Project QA passed 34/34 measured
gates when the showcase artifact was produced; the gate set has since grown to
39. The archived `92.58%` visual figure predates the current metric and must not
be compared with it: re-measured under `VISUAL_METRIC_VERSION = 2.2` this same
artifact scores **31.46%**, because 2.2 scores foreground, edge and region
agreement rather than raw pixel agreement (its pixel similarity is 94.71%). See
[docs/showcases/README.md](docs/showcases/README.md) for the re-measured table
of all three showcases. Automated similarity is not proof that every glyph or
mathematical statement is semantically correct.

### Tuyen Quang gifted school - Vietnamese 2nd exam (Source: VNExpress)

**Source:** [VNExpress](https://vnexpress.net/) — attribution supplied by the
contributor; see the showcase rights notice.

| Original source | Editable DOCX rendered by the project |
| :---: | :---: |
| [<img src="docs/showcases/vietnamese-exam/source-original.png" alt="Original Vietnamese second exam page" width="420">](docs/showcases/vietnamese-exam/source-original.png) | [<img src="docs/showcases/vietnamese-exam/rendered-preview.png" alt="Rendered editable Vietnamese exam DOCX" width="420">](docs/showcases/vietnamese-exam/rendered-preview.png) |

**Artifacts:** [original image](docs/showcases/vietnamese-exam/source-original.png) ·
[editable Word file](docs/showcases/vietnamese-exam/editable.docx) ·
[rendered preview](docs/showcases/vietnamese-exam/rendered-preview.png)

This example demonstrates a two-zone exam header, Vietnamese serif typography,
indented justified passages, attribution placement, dotted candidate fields,
and editable questions. OCR spelling and diacritic errors remain possible; the
source watermark, obscured candidate data, and other raster-only marks are not
silently recreated as editable text.

## Accuracy limitations and required verification

- OCR/provider Markdown may miss text or misread spelling, accents, numbers,
  punctuation, scientific notation, mathematical operators, and handwriting.
- Formula conversion may preserve editable structure while still getting an
  operator, delimiter, alignment point, limit position, or line break wrong.
- Tables, columns, reading order, fonts, spacing, figures, headers, footers, and
  pagination can differ between the source, Microsoft Word, and LibreOffice.
- Provider confidence and a passing automated QA gate are useful evidence, not
  proof that the semantic content is correct.
- When Markdown and the source disagree, the hybrid workflow keeps Markdown as
  the wording authority and does not silently invent or correct content.
- Pixel-identical output and deeply editable native Word objects are competing
  goals; this project does not promise universal 1:1 reproduction.

Users must manually compare the output against the original and obtain an
appropriate subject-matter review before using it for exams, archival records,
legal, medical, financial, compliance, or other high-stakes work.

## Installation

Python 3.11 or newer is required.

```bash
python -m pip install -e .
```

Install only the adapters you need:

```bash
python -m pip install -e ".[pdf]"       # PyMuPDF native-PDF support
python -m pip install -e ".[docx]"      # DOCX rendering
python -m pip install -e ".[hybrid]"    # Markdown + PDF/image -> editable layout-aware DOCX
python -m pip install -e ".[api]"       # FastAPI, Uvicorn, multipart uploads
python -m pip install -e ".[all,dev]"   # all runtime extras plus contributor tools
```

These extras do not install PaddleOCR, MinerU, or olmOCR. Keeping those engines
outside the core avoids forced GPU stacks, model downloads, and third-party
license surprises.

## Quick start

The repository includes a small, valid IR document. Load it and render HTML
without invoking OCR:

```python
from pathlib import Path

from docreconstruct import Document, export

document = Document.from_json(Path("examples/example_document.json").read_text())
written = export(document, "output/example.html", output_format="html")
print(written)
```

For a source document, use the high-level pipeline. A raster image can be
wrapped as source evidence with no OCR; provide an installed provider when text
recognition is required:

```python
from docreconstruct import reconstruct

document = reconstruct(
    "scan.png",
    engines=["my_provider"],
    profile="balanced",
    output="output/scan.html",
    output_format="html",
)
print(document.metadata["output"])
```

Provider output can also be normalized from saved JSON, which is useful when a
GPU OCR service runs separately. Provider-specific expected shapes are kept in
the adapter documentation and tests; the normalized result always becomes the
same `Document` model.

Routing is planning, not hidden model execution. It selects the cheapest
credible primary engine and records fallbacks; the bundled heavyweight-engine
adapters remain saved-output normalizers unless a live provider plugin is
installed:

```python
from docreconstruct import analyze, build_routing_plan

document = analyze("scan.png")
plan = build_routing_plan(document)
print(plan.tasks[0].primary_provider)  # paddleocr
print(plan.tasks[0].fallback_providers)  # olmocr, mineru
```

## Command line

For saved OCR/AI Markdown plus its original scan, use the generic hybrid
command. It has no document-specific template or script: Markdown supplies the
content, a PDF/JPEG/PNG/TIFF/WebP scan supplies the page geometry, and the
output path selects the installed renderer. DOCX is the high-fidelity editable
renderer in v0.1.

```bash
docreconstruct hybrid content.md layout.pdf --output output/result.docx
docreconstruct hybrid content.md photographed-page.jpg --output output/result.docx

# Best-evidence mode: Markdown wording + saved OCR JSON geometry/style/confidence
# + the original PDF/image pixels. Repeat --evidence for independent providers.
docreconstruct hybrid content.md original.pdf \
  --evidence paddleocr.json --evidence mineru.json \
  --output output/result.docx

# Auto-detection is the default. Override an ambiguous vendor schema explicitly.
docreconstruct hybrid content.md original.png \
  --evidence result.json --evidence-provider result.json=paddleocr \
  --output output/result.docx

# Integrated hosted evidence: content.md remains the exact wording while
# Mistral contributes positioned canonical JSON. Credentials stay in the
# environment and --allow-cloud is mandatory.
export MISTRAL_API_KEY="..."
docreconstruct hybrid content.md original.png \
  --online-ocr --allow-cloud --ocr-provider mistral_ocr \
  --ocr-artifacts-dir output/result.ocr \
  --output output/result.docx --qa-report output/result.qa.json

# PaddleOCR's official asynchronous AI Studio service is a separate provider.
# It uses the user's own access token; it is not paddleocr_vl_server.
export PADDLEOCR_ACCESS_TOKEN="..."
docreconstruct hybrid content.md original.pdf \
  --online-ocr --allow-cloud --ocr-provider paddleocr_official \
  --ocr-artifacts-dir output/result.ocr \
  --output output/result.docx --qa-report output/result.qa.json

# Explicit multi-provider ensemble; this can upload and bill more than once.
docreconstruct hybrid content.md original.png \
  --online-ocr --allow-cloud \
  --ocr-providers mistral_ocr,mathpix --ocr-ensemble --ocr-max-providers 2 \
  --ocr-artifacts-dir output/result.ocr --output output/result.docx

# Write a project-native OOXML/layout QA report without LibreOffice or rasterization.
docreconstruct hybrid content.md layout.jpg -o output/result.docx \
  --qa-report output/result.qa.json

# Make the same command render through LibreOffice, compare source/candidate
# pages at identical pixel dimensions, and enforce the built-in nonblank floor.
docreconstruct hybrid content.md layout.jpg -o output/result.docx \
  --qa-backend libreoffice \
  --qa-render-dir output/render-qa --qa-report output/result.qa.json

# After calibrating v2.1 on your own reviewed corpus, you may raise the floor.
docreconstruct hybrid content.md layout.jpg -o output/result.docx \
  --qa-backend libreoffice --min-visual-score 0.25 \
  --qa-report output/result.qa.json

# Disable HTTPS image reuse and fall back to matched source crops.
docreconstruct hybrid content.md layout.pdf -o output/result.docx --no-remote-assets
```

Remote Markdown images are downloaded only over HTTPS, validated as bounded
raster images, cached by URL hash, and aligned back to the layout source. If a provider
crop touches an image boundary, the renderer extends the crop from the layout
source so labels and legends are not silently lost.

Raster analysis is OCR-free: it recovers dense text-line rhythm, separates
split mastheads from an independently single-column body, and rejects short
bold/italic text components that would otherwise be mistaken for figures.
The DOCX renderer keeps masthead cells, dotted form leaders, headings,
numbered quoted passages, attributions, questions, paragraphs, and tables as
native editable objects. It never inserts the full scan as a page background.
Source wording is never corrected from the layout image; if OCR Markdown and
the scan disagree, the Markdown remains the content authority and the mismatch
is measurable rather than silently invented away.

When saved JSON sidecars are supplied, the project detects their provider
schema offline, normalizes each one independently to canonical IR, maps its
coordinates into the original scan raster (including photographed-page
rectification), and aligns it monotonically to Markdown blocks. Consensus JSON
can improve page assignment, block boxes, semantic type, style, and confidence,
but it cannot replace Markdown wording or provide body pixels. Page dimensions,
figures, charts, stamps, and other raster crops always come from the original
PDF/image. Conflicting or unrelated JSON is rejected or reported rather than
silently averaged.

`--online-ocr` uses the same provider registry and authority rules in the
one-command hybrid job. Every successful result is saved independently as
canonical JSON, SHA-256 fingerprinted, and aligned to Markdown before
rendering. Provider-generated Markdown is retained under
`--ocr-artifacts-dir` for audit only; it never replaces the user-supplied
Markdown. The optional cache contains content-bearing canonical evidence,
verifies artifact hashes before reuse, and can be disabled with
`--no-ocr-cache`. Cache keys and reports omit credentials. No upload can occur
without both `--online-ocr` and `--allow-cloud`; an ensemble is a separate
explicit multi-upload choice.

Every hybrid run performs native QA against the generated OOXML package. It
checks the Markdown presentation stream, editable Office Math count and
structure, display-equation rows, tagged split mastheads, source-anchor order,
paragraph width/leading sanity, native footers, tables, page sections, page
size and margins, East-Asian font mapping, external relationships, and
full-page scan flattening. `--qa-report` persists these gates and fingerprints
for the Markdown, original layout, every JSON sidecar, and output; the command
exits nonzero if any measured gate fails.

Native QA is the fast structural path. It does not measure pixels, installed
font substitution, renderer line wrapping, or the pages that Microsoft Word or
LibreOffice will actually produce. Its passed-gate fraction is conformance to
the gates that were measured, not an end-to-end fidelity score.

`--qa-backend libreoffice` enables the optional project-owned render adapter.
It discovers a system LibreOffice installation (or accepts
`--qa-renderer-path`), uses an isolated temporary profile, rasterizes the DOCX
at the source page dimensions, and runs visual metric v2.1. The metric combines
tolerance-aware foreground precision/recall/F1, multi-radius edge alignment,
macro-averaged page regions, and explicit page-count/dimension penalties. Its
adaptive low-contrast path and negative-control tests prevent a blank page from
passing merely because both canvases are mostly white.

Every successful Office render must clear the built-in `0.05` v2.1 floor;
`--min-visual-score` may raise but cannot lower it. The default is a failure-
detection floor for blank or nearly blank renders, not a claim of acceptable
document fidelity. Calibrate a higher threshold by document family against a
reviewed corpus. `--qa-render-dir` can retain source/candidate/difference PNGs
for debugging. The default `native` backend never discovers or starts an Office
process and continues to list all rendered measurements as unmeasured.

LibreOffice itself remains a separately installed application. The project
integrates it through a bounded argv-only render helper instead of copying the
office suite into the Python package. The helper verifies the selected binary,
records its version and SHA-256, rendered PDF/page hashes and physical page
sizes, and never starts a process under the default `native` backend. General
`compare` calls require an explicit `--render-backend libreoffice` (or `auto`)
before Office discovery is allowed.

The bare command reconstructs a source; the explicit `reconstruct` subcommand
is equivalent:

```bash
docreconstruct source.pdf \
  --output output/report.docx \
  --output-format docx \
  --engine auto \
  --profile balanced

docreconstruct reconstruct source.png --output output/page.html --output-format html
```

Introspection and experiment commands are also available:

```bash
docreconstruct analyze source.png --output output/source.json
docreconstruct route source.png --output output/routing-plan.json
docreconstruct providers
docreconstruct formats
docreconstruct schema --output schemas/generated-document-ir.schema.json
docreconstruct compare reference.json candidate.json
docreconstruct benchmark ./benchmark-dataset
docreconstruct benchmark-reconstruction benchmark/reconstruction-benchmark.json \
  --output benchmark/reconstruction-report.json
```

`compare` accepts canonical JSON, raster images, text/Markdown, HTML, DOCX,
and—with the `pdf` extra—PDF. A benchmark directory must follow the evaluator's
manifest format; neither command downloads ground truth or external models.

`benchmark-reconstruction` is the end-to-end benchmark for the best-evidence
path. Every case must name an original PDF/image, reviewed Markdown, and at
least one positioned JSON sidecar; the runner always generates a fresh DOCX,
so a prebuilt candidate cannot bypass reconstruction. It records fingerprints,
per-phase timing, operational success, native validation-gate conformance, and
slices such as language, script, document type, degradation, and content kind.

With native QA, visual quality is deliberately reported as **incomplete**:
native validation-gate conformance is not relabeled as fidelity. Run with
`--qa-backend libreoffice` to obtain v2.1 rendered-fidelity scores. A report
publishes a mean quality score only when every case has complete, comparable
rendered quality under one metric profile; operational failures remain visible
and are never silently excluded. See
[Performance and public deployment](docs/PERFORMANCE.md#reproducible-reconstruction-benchmark)
for a manifest example and interpretation guidance.

## Canonical document IR

Coordinates use the source page coordinate system and bounding boxes are
objects with `x0`, `y0`, `x1`, and `y1` fields. Every derived object should be
traceable to source evidence.

```json
{
  "id": "quarterly-report",
  "schema_version": "0.1",
  "pages": [
    {
      "id": "page-1",
      "number": 1,
      "width": 612,
      "height": 792,
      "source_type": "native",
      "elements": [
        {
          "id": "heading-1",
          "type": "heading",
          "bbox": {"x0": 72, "y0": 64, "x1": 540, "y1": 98},
          "polygon": [],
          "z_index": 0,
          "text": "Quarterly Financial Report",
          "reading_order": 0,
          "confidence": 0.99,
          "style": {"font_size": 24, "font_weight": 700},
          "provenance": {
            "engine": "native_pdf",
            "text_confidence": 1.0,
            "layout_confidence": 1.0
          }
        }
      ]
    }
  ]
}
```

See [the complete example](examples/example_document.json) and the
[JSON Schema](schemas/document-ir.schema.json).

## Reconstruction profiles

Profiles make the trade-off between appearance and native editability explicit:

| Profile | Primary intent |
| --- | --- |
| `balanced` | General-purpose fidelity and editability |
| `pixel-perfect` / `visual` / `fidelity` / `replica` | Spatial and visual similarity |
| `editable` / `semantic` | Native document structure and normal editing |
| `data` | Text, numbers, and table structure |
| `archival` | Stable visual preservation |
| `presentation` | Strongly positioned page composition |

A profile controls planning and scoring weights; it cannot manufacture evidence
that an upstream extractor did not provide.

## HTTP API

Install the API extra, then start the local service:

```bash
docreconstruct-api
# or: python -m docreconstruct.api
```

OpenAPI documentation is served at `http://127.0.0.1:8000/docs`. The main
endpoints are:

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/health` | Liveness and package version |
| `GET` | `/v1/providers` | Registered or detectable providers |
| `GET` | `/v1/formats` | Input/output format availability |
| `GET` | `/v1/hybrid/capabilities` | Browser-safe high-fidelity requirements and operator-enabled hosted OCR choices |
| `POST` | `/v1/analyze` | Upload a source and return canonical IR |
| `POST` | `/v1/route` | Produce a cost-aware page/region provider plan |
| `POST` | `/v1/reconstruct` | Upload a source and download the rendered artifact |
| `POST` | `/v1/hybrid` | Rebuild editable DOCX from Markdown + original + positioned JSON, uploaded or explicitly generated by hosted OCR |
| `POST` | `/v1/compare` | Compare supported IR or rendered artifacts and return a fidelity report |

Uploads are multipart. The optional `options` part is a JSON object validated by
the corresponding Pydantic request model:

```bash
curl -f http://127.0.0.1:8000/v1/reconstruct \
  -F "file=@scan.png" \
  -F 'options={"output_format":"html","profile":"balanced"}' \
  --output scan.html
```

The web client uses `/v1/hybrid`. `fast` runs project-native OOXML QA and is
the low-latency default. `verified` additionally renders through the
operator-configured LibreOffice binary and performs visual comparison, so it
takes longer. High-fidelity mode requires positioned JSON. A saved sidecar
avoids another OCR upload; when it is absent, an operator may expose its
configured hosted OCR service as an explicit opt-in that creates it.

An operator can expose a small, deliberate OCR chooser without exposing its
credentials or service URLs:

```text
DOCRECONSTRUCT_PUBLIC_OCR_PROVIDERS=paddleocr_official,mistral_ocr
PADDLEOCR_ACCESS_TOKEN=operator-secret
MISTRAL_API_KEY=operator-secret
```

`GET /v1/hybrid/capabilities` returns only providers named in the allowlist. A
provider is marked available only when its required server-side configuration
is also present. The response includes `evidence_required`, `evidence_modes`,
`server_generates_json`, `browser_credentials_accepted`, `maximum_upload_mb`,
and non-secret provider labels/capabilities. It never returns environment
variable names or values, tokens, API keys, or provider endpoints. The browser
must select one of these discovered names through `options.ocr_provider`; it
cannot introduce an arbitrary service or credential.

```bash
curl -f https://your-backend.example/v1/hybrid \
  -F "content=@content.md" \
  -F "layout=@original.pdf" \
  -F "evidence=@paddleocr.json" \
  -F 'options={"quality":"fast","evidence_provider":"paddleocr"}' \
  --output reconstructed.docx

# Omit evidence only after capabilities advertises the selected hosted service.
curl -f https://your-backend.example/v1/hybrid \
  -F "content=@content.md" \
  -F "layout=@original.pdf" \
  -F 'options={"quality":"fast","ocr_provider":"paddleocr_official"}' \
  --output reconstructed.docx
```

The backend operator, not the browser, controls OCR URLs, tokens, local
renderer paths, CORS, and retention. Clients cannot supply those server-side
values. Configure `PADDLEOCR_VL_SERVER_URL` for the built-in
`paddleocr_vl_server`; optionally configure `PADDLEOCR_VL_SERVER_TOKEN`, and add
`paddleocr_vl_server` to `DOCRECONSTRUCT_PUBLIC_OCR_PROVIDERS`. A configured URL
alone does not make the provider discoverable or selectable. The legacy
`use_paddleocr_vl` option is subject to the same allowlist and configuration
checks; it is not a bypass. For `verified`, configure
`DOCRECONSTRUCT_LIBREOFFICE_PATH`. See the
[deployment guide](docs/PERFORMANCE.md) for the trust and consent boundary.

Remote images referenced by Markdown are also operator-controlled. The upload
API rejects `"remote_assets":true` unless
`DOCRECONSTRUCT_ALLOW_REMOTE_ASSETS=1` is configured. Local asset paths are
confined to the uploaded Markdown directory, and public HTTPS destinations are
validated before download.

The comparison endpoint accepts canonical JSON, raster images, text/Markdown,
HTML, DOCX, and—with the `pdf` extra—PDF. It evaluates only dimensions supported
by the supplied evidence and renormalizes the aggregate score over those
dimensions; a missing visual render or document structure is not reported as a
perfect measurement. The upload limit defaults to 50 MiB and can be changed with
`DOCRECONSTRUCT_MAX_UPLOAD_MB`.

## Architecture

```text
source PDF / image
        |
        +-- native extraction where available
        +-- page/region router (cost + content + confidence)
        |         |
        |         +-- primary provider
        |         +-- selective fallback/consensus for uncertain regions
        v
provider normalization -> canonical Document IR -> reconstruction plan
                                                    |
                         JSON / HTML / Markdown / DOCX renderer
                                                    |
                         compare -> diagnose -> guarded correction / reroute
```

The fusion resolver is therefore an escalation mechanism, not a requirement to
run every heavyweight OCR engine over every page.

AI components may interpret ambiguous evidence; deterministic code performs IR
validation, reconstruction planning, and rendering. Source text should not be
rewritten, summarized, translated, or "corrected" without traceable evidence.

## Provider and renderer extensions

Providers implement the contract in `docreconstruct.providers.base` and are
registered by a stable, case-insensitive name. A provider should declare its
capabilities, preserve raw provenance in metadata, and return schema-valid IR.
Live inference is optional; a lightweight adapter may intentionally support
saved provider JSON only.

The capability matrix can rank providers for handwriting, formulas, tables,
multilingual pages, distorted photographs, local/privacy-only work, or hosted
API execution without importing heavyweight engines. Hosted processing is
always opt-in; an API key alone never authorizes document upload. See
[OCR providers, cloud routing, and training data](docs/OCR_AND_TRAINING.md) for
the cloud/local/hybrid policy, reviewed provider families, dataset rights, and
reproducible training manifests.

Training-data validation and trainer planning do not require a deep-learning
stack:

```bash
docreconstruct dataset-validate dataset.json --lane commercial-permissive
docreconstruct train-plan dataset.json --backend olmocr --lane commercial-permissive
```

Actual fine-tuning requires an explicit `docreconstruct.trainers` plugin. The
core never scrapes training data, downloads model weights, or learns from user
documents without consent.

Hosted OCR can produce the Markdown authority directly, but every upload must
be authorized on that invocation:

```bash
docreconstruct extract scan.pdf --mode cloud --allow-cloud \
  --provider mistral_ocr --output scan.md --report scan.run.json
docreconstruct hybrid scan.md scan.pdf --output scan.editable.docx
```

Built-in hosted adapters currently cover `paddleocr_official`, Mistral OCR,
Azure Document Intelligence, Google Document AI, and Mathpix; saved AWS
Textract JSON is also normalized. `paddleocr_official` uses the user's
`PADDLEOCR_ACCESS_TOKEN` and Paddle AI Studio's asynchronous job service; see
the [official PaddleOCR API SDK](https://www.paddleocr.ai/latest/en/version3.x/inference_deployment/serving/paddleocr_official_api/python.html).
It is distinct from `paddleocr_vl_server`, which connects, with explicit upload
consent, to a PaddleOCR-VL server chosen and managed by the reconstruction
backend operator.
Website-exported Markdown is accepted directly, so users can run OCR manually
in a provider's web UI and keep reconstruction inside this project. PaddleOCR
and olmOCR publish open-source code, but their repositories do not provide this
project with an unlimited public GPU: any external inference host has its own
availability, quota, pricing, retention, and terms. End-to-end provider
comparisons use the same extraction path as production:

```bash
docreconstruct benchmark-ocr benchmark/ocr-benchmark.json \
  --allow-cloud --output benchmark/report.json
```

Renderers implement `docreconstruct.renderers.Renderer`. Rendering should be
deterministic and should prefer genuinely editable target objects. Optional
dependencies must be checked only when their renderer is requested.

## Research acknowledgements

`docreconstruct` is an independent implementation. Its evidence model,
provider-normalization boundaries, reconstruction pipeline, and QA strategy
were informed by the public documentation, published interfaces, and
evaluation methods of the projects below. Acknowledgement does not imply that
their source code or model weights are bundled, copied, or required, and it
does not imply affiliation or endorsement.

| Project | Public design ideas studied | License note |
| --- | --- | --- |
| [PaddleOCR / PP-StructureV3](https://github.com/PaddlePaddle/PaddleOCR) | Modular orientation correction, document unwarping, layout/OCR/table/formula analysis, reading order, and paired [JSON/Markdown output](https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/pipeline_usage/PP-StructureV3.en.md) | Code: Apache-2.0; independently verify the terms of any selected model or hosted service |
| [MinerU](https://github.com/opendatalab/MinerU) | Hybrid native-text/OCR/VLM routing, rich intermediate evidence, cross-page structures, and documented [content-list geometry](https://github.com/opendatalab/MinerU/blob/master/docs/en/reference/output_files.md) | [Custom Apache-2.0-based license](https://github.com/opendatalab/MinerU/blob/master/LICENSE.md) with additional commercial-threshold and online-service attribution terms |
| [Docling](https://github.com/docling-project/docling) | Unified lossless document representation, hierarchy, provenance, coordinate origins, and multi-format conversion | Code: MIT; Docling explicitly requires users to check individual model licenses |
| [Marker](https://github.com/datalab-to/marker) | Hierarchical page/block JSON, native-text-first processing, targeted OCR repair, and optional escalation for difficult blocks | Code: Apache-2.0; [model weights have separate modified OpenRAIL terms](https://github.com/datalab-to/marker/blob/master/MODEL_LICENSE) |
| [olmOCR and olmOCR-Bench](https://github.com/allenai/olmocr) | Natural reading-order linearization and fact-level tests for text presence/absence, reading order, tables, formulas, scans, and dense text | Code: Apache-2.0; datasets and externally served models may have separate terms |
| [Surya](https://github.com/datalab-to/surya) | Polygon geometry, block labels, reading-order positions, confidence, multilingual OCR, table structure, math, and page-to-block fallback | Code: Apache-2.0; model weights use separate modified OpenRAIL terms |
| [Unstructured](https://github.com/Unstructured-IO/unstructured) | Input-type partitioning and per-element page, coordinate-system, and detection-origin metadata | Code: Apache-2.0; hosted platform terms are separate |
| [LayoutParser](https://github.com/Layout-Parser/layout-parser) | Provider-neutral spatial data structures, region operations, model adapters, and layout visualizations | Code: Apache-2.0; third-party models/backends retain their own terms |
| [OCRmyPDF](https://github.com/ocrmypdf/OCRmyPDF) | Conservative rotation, deskewing, cleanup, and preservation-first preprocessing for scanned PDFs | Code: MPL-2.0; its documentation warns that aggressive cleanup can remove content and must be reviewed |
| [OmniDocBench](https://github.com/opendatalab/OmniDocBench) | Attribute-aware evaluation of text, tables, formulas, layout, and reading order across diverse document classes | Code: Apache-2.0; dataset terms and source-document rights must also be checked |

Project names and trademarks belong to their respective owners. License notes
above are concise engineering reminders, not legal advice. Before enabling an
optional adapter, model, dataset, or hosted OCR service, review its current
code license, model/data license, privacy policy, and service terms directly.

## Development and safety

```bash
pytest
ruff check .
mypy src/docreconstruct
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for extension and test expectations and
[SECURITY.md](SECURITY.md) before exposing the unauthenticated development API.
Documents can contain sensitive data; choose providers and deployment boundaries
accordingly.

## License

The framework is licensed under Apache-2.0. Optional OCR engines are separate
projects with their own licenses and terms. Installing or calling an adapter
does not relicense that engine or its models.
