# docreconstruct

**Turn document pixels into editable document structure.**

`docreconstruct` is a model-agnostic Python framework for reconstructing PDFs,
scans, and images as structured, editable documents. It keeps coordinates,
reading order, style evidence, relationships, confidence, and provenance in a
canonical document IR instead of reducing every source to Markdown.

> OCR recovers the words. Reconstruction recovers the document.

This repository is an early v0.1 foundation. It provides the portable IR,
adapter contracts, deterministic planning and rendering, and evaluation
building blocks. It does **not** bundle or silently download heavyweight OCR
models. PaddleOCR, MinerU, olmOCR, and future engines remain optional adapters;
users are responsible for installing and operating any inference system they
choose.

## What v0.1 can do

- Represent pages and elements in a strict Pydantic `Document` model with
  geometry, styles, relationships, confidence, text candidates, and provenance.
- Inspect raster images and optionally born-digital PDFs without discarding
  source geometry.
- Normalize saved PaddleOCR, MinerU, and olmOCR evidence through provider
  adapters, with a registry for custom providers.
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
  scan to retain complete source figures.
- Score text, layout, structure, visual evidence, and editability when the
  required reference data is available.
- Use the same synchronous pipeline from Python, the CLI, or an optional
  FastAPI upload service.
- Validate optional VLM/LLM correction proposals against existing object IDs
  and OCR candidates so an adjudicator cannot silently invent source text.

The built-in renderers are deliberately modest. XLSX, PPTX, reconstructed PDF,
live heavyweight OCR inference, and an automatic render/compare/correct critic
are roadmap work. The current DOCX renderer favors native paragraphs and tables
over hundreds of hard-to-edit positioned text boxes, so it should not be
described as pixel-perfect.

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

# Explicit multi-provider ensemble; this can upload and bill more than once.
docreconstruct hybrid content.md original.png \
  --online-ocr --allow-cloud \
  --ocr-providers mistral_ocr,mathpix --ocr-ensemble --ocr-max-providers 2 \
  --ocr-artifacts-dir output/result.ocr --output output/result.docx

# Write a project-native OOXML/layout QA report without LibreOffice or rasterization.
docreconstruct hybrid content.md layout.jpg -o output/result.docx \
  --qa-report output/result.qa.json

# Make the same command render through LibreOffice, compare source/candidate
# pages at identical pixel dimensions, and fail below an explicit score.
docreconstruct hybrid content.md layout.jpg -o output/result.docx \
  --qa-backend libreoffice --min-visual-score 0.80 \
  --qa-render-dir output/render-qa --qa-report output/result.qa.json

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

`--qa-backend libreoffice` enables the optional project-owned render adapter.
It discovers a system LibreOffice installation (or accepts
`--qa-renderer-path`), uses an isolated temporary profile, rasterizes the DOCX
at the source page dimensions, and performs foreground-normalized comparison.
`--min-visual-score` turns that measurement into a hard gate, while
`--qa-render-dir` can retain source/candidate/difference PNGs for debugging.
The default `native` backend never discovers or starts an Office process and
continues to list pixel similarity, installed-font substitution, actual line
wrapping, and renderer-confirmed pagination as unmeasured.

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
```

`compare` accepts canonical JSON, raster images, text/Markdown, HTML, DOCX,
and—with the `pdf` extra—PDF. A benchmark directory must follow the evaluator's
manifest format; neither command downloads ground truth or external models.

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
| `POST` | `/v1/analyze` | Upload a source and return canonical IR |
| `POST` | `/v1/route` | Produce a cost-aware page/region provider plan |
| `POST` | `/v1/reconstruct` | Upload a source and download the rendered artifact |
| `POST` | `/v1/compare` | Compare supported IR or rendered artifacts and return a fidelity report |

Uploads are multipart. The optional `options` part is a JSON object validated by
the corresponding Pydantic request model:

```bash
curl -f http://127.0.0.1:8000/v1/reconstruct \
  -F "file=@scan.png" \
  -F 'options={"output_format":"html","profile":"balanced"}' \
  --output scan.html
```

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

Built-in hosted adapters currently cover Mistral OCR, Azure Document
Intelligence, Google Document AI, and Mathpix; saved AWS Textract JSON is also
normalized. Website-exported Markdown is accepted directly, so users can run
OCR manually in a provider's web UI and keep reconstruction inside this
project. End-to-end provider comparisons use the same extraction path as
production:

```bash
docreconstruct benchmark-ocr benchmark/ocr-benchmark.json \
  --allow-cloud --output benchmark/report.json
```

Renderers implement `docreconstruct.renderers.Renderer`. Rendering should be
deterministic and should prefer genuinely editable target objects. Optional
dependencies must be checked only when their renderer is requested.

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
