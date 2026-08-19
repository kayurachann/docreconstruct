# OCR providers, cloud routing, and training data

`docreconstruct` treats OCR as replaceable evidence. It does not assume that a
single model is best for every printed page, handwritten note, formula, table,
newspaper, historical scan, or photographed/curved sheet.

## Recommended provider policy

Use native PDF text first. For raster pages, choose by declared capabilities
and retain the original provider response, model/version, coordinates,
confidence, and preprocessing transform in provenance.

| Scenario | Primary evidence | Independent check |
| --- | --- | --- |
| Multilingual scan, warped photo, newspaper | PaddleOCR-VL / PP-StructureV3 | GLM-OCR or olmOCR |
| Handwriting mixed with print | Mistral OCR, PaddleOCR-VL, or olmOCR | Azure/Google document OCR |
| Scientific page or exam | PaddleOCR-VL or Mathpix | olmOCR / GLM-OCR / GOT-OCR2 |
| Forms, tables, checkboxes, signatures | Azure, Google Document AI, or AWS Textract | PaddleOCR-VL |
| Born-digital PDF | native PDF extraction | OCR only missing raster regions |

The core includes saved-result adapters for PaddleOCR, MinerU, and olmOCR,
explicit credential-gated hosted adapters, and an operator-managed live
PaddleOCR-VL pipeline adapter named `paddleocr_vl_server`. Other engines should
be installed through the `docreconstruct.providers` entry-point interface
rather than vendored into the core package.

Primary projects reviewed:

- [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR): local/open document
  parsing, OCR, layout, tables, formulas, charts, handwriting, and image
  unwarping.
- [MinerU](https://github.com/opendatalab/MinerU): PDF/image/Office to
  Markdown/JSON; its current custom license has additional conditions.
- [olmOCR](https://github.com/allenai/olmocr): local or OpenAI-compatible remote
  PDF/image to Markdown, including handwriting, math, tables, and complex
  reading order.
- [GLM-OCR](https://github.com/zai-org/GLM-OCR): local/hosted Markdown plus
  structured layout response.
- [Docling](https://github.com/docling-project/docling): a useful optional
  orchestrator with multiple local and remote OCR engines.
- [Mistral Document AI](https://docs.mistral.ai/studio-api/document-processing/basic_ocr),
  [Azure Document Intelligence](https://learn.microsoft.com/en-us/azure/ai-services/document-intelligence/prebuilt/layout),
  [Google Document AI](https://cloud.google.com/document-ai/docs/overview),
  [AWS Textract](https://docs.aws.amazon.com/textract/latest/dg/how-it-works-analyzing.html),
  and [Mathpix](https://docs.mathpix.com/) are opt-in hosted specialists.

Do not automate a provider's consumer website by scraping or browser macros.
Use its documented API. A website UI may remain useful for manual export, whose
saved Markdown/JSON can be passed back to this project.

Open-source code and free hosted computation are different promises.
PaddleOCR and olmOCR can be installed or deployed under their published
licenses, but a third party that runs the model must still pay for hardware and
may impose accounts, quotas, fees, retention, regional processing, or changing
terms. The [olmOCR repository](https://github.com/allenai/olmocr) documents
local inference and OpenAI-compatible external model servers, including
separately operated providers; those services are not bundled with or
guaranteed by `docreconstruct`.

## Privacy modes

- `local`: document bytes never leave the machine; only installed local
  providers are eligible.
- `cloud`: a hosted API is eligible only after an explicit per-call opt-in and
  credential configuration.
- `hybrid`: local/native evidence is preserved and selected cloud specialists
  can adjudicate difficult regions.

No credential is written to a run report. Remote providers are disabled by
default, even if an API key exists in the environment.

### Operator-managed PaddleOCR-VL

`paddleocr_vl_server` calls the complete official `/layout-parsing` pipeline
contract rather than a bare chat/completions endpoint. That distinction retains
the preprocessing, layout, page restructuring, table, formula, and Markdown
evidence needed by reconstruction. The adapter then normalizes the response to
the same canonical IR used for saved PaddleOCR JSON.

The API operator supplies configuration; a browser client cannot submit an OCR
URL or token:

```powershell
$env:PADDLEOCR_VL_SERVER_URL = "http://127.0.0.1:8080"
$env:PADDLEOCR_VL_SERVER_TOKEN = "optional-private-token"
$env:DOCRECONSTRUCT_CORS_ORIGINS = "https://kayurachann.github.io"
docreconstruct-api
```

Loopback HTTP is allowed for a sidecar or local reverse proxy. Direct
non-loopback connections must use HTTPS and require a separate trusted-endpoint
opt-in in the programmatic provider context. Every live inference call also
requires remote-upload consent. The server URL and credential belong to the
operator, never to an untrusted multipart request.

The official high-performance PaddleOCR-VL deployment uses a FastAPI gateway,
Triton, and vLLM with dynamic and continuous batching. It is an operator
deployment recipe, not a free public endpoint supplied by the PaddleOCR project
or this repository. See the
[official serving guide](https://github.com/PaddlePaddle/PaddleOCR/blob/main/deploy/paddleocr_vl_docker/hps/README_en.md).

### Hosted extraction to Markdown

Mistral OCR, Azure Document Intelligence, Google Document AI, and Mathpix are
built-in hosted adapters. Their official REST APIs are called from the project;
no browser automation or vendor SDK is required. AWS Textract JSON exported by
the service/console is supported as saved evidence; live AWS signing remains an
optional plugin concern. Set credentials in the process environment, then
authorize the specific command:

```powershell
$env:MISTRAL_API_KEY = "..."
docreconstruct extract scan.pdf --mode cloud --allow-cloud `
  --provider mistral_ocr --output scan.md --report scan.run.json

$env:AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT = "https://<resource>.cognitiveservices.azure.com"
$env:AZURE_DOCUMENT_INTELLIGENCE_KEY = "..."
docreconstruct extract photo.jpg --mode cloud --allow-cloud `
  --provider azure_document_intelligence --handwriting --distorted-photo `
  --output photo.md

$env:MATHPIX_APP_ID = "..."
$env:MATHPIX_APP_KEY = "..."
docreconstruct extract stem-exam.pdf --mode cloud --allow-cloud `
  --provider mathpix --handwriting --formulas --tables --output stem-exam.md

$env:GOOGLE_DOCUMENT_AI_ACCESS_TOKEN = "...short-lived OAuth token..."
$env:GOOGLE_CLOUD_PROJECT = "project-id"
$env:GOOGLE_DOCUMENT_AI_LOCATION = "us"
$env:GOOGLE_DOCUMENT_AI_PROCESSOR_ID = "processor-id"
docreconstruct extract form.pdf --mode cloud --allow-cloud `
  --provider google_document_ai --handwriting --tables --output form.md
```

When a reviewed Markdown file already supplies the wording, the `hybrid`
command can call the hosted provider, retain its Markdown only as an audit
artifact, and feed each canonical positioned JSON result directly into DOCX
reconstruction and QA:

```powershell
$env:MISTRAL_API_KEY = "..."
docreconstruct hybrid reviewed-content.md original-photo.jpg `
  --online-ocr --allow-cloud --ocr-provider mistral_ocr `
  --ocr-handwriting --ocr-distorted-photo --ocr-dewarping `
  --ocr-artifacts-dir output/job.ocr `
  --qa-backend libreoffice --min-visual-score 0.80 `
  --qa-render-dir output/job.render `
  --qa-report output/job.qa.json --output output/job.docx
```

This is one project job, not a document-specific helper script. The Markdown
file remains immutable content authority; online OCR contributes only geometry,
semantic type, style, confidence, and provenance. `output/job.ocr` contains the
audit Markdown, independent canonical JSON sidecars, a sanitized extraction
manifest, and an optional SHA-verified cache. Disable the cache with
`--no-ocr-cache`. LibreOffice is never discovered or launched unless the
render backend is explicitly selected.

For a saved Textract response:

```powershell
docreconstruct reconstruct textract-response.json --engine aws_textract `
  --to markdown --output textract-response.md
```

Official vendor endpoints are enforced by default. A trusted self-hosted
endpoint needs the separate programmatic `allow_custom_endpoint=true` opt-in,
in addition to cloud consent, so an untrusted benchmark manifest cannot silently
redirect API credentials.

### Static web client and upload consent

After deployment, GitHub Pages will host the static interface at
[kayurachann.github.io/docreconstruct](https://kayurachann.github.io/docreconstruct/).
It contains only HTML, CSS, and JavaScript; it cannot run the Python
reconstruction pipeline, LibreOffice, Triton, vLLM, PaddleOCR, or olmOCR. No
public backend or public GPU is bundled.

The user must enter or select a backend operated by a party they trust. Before
submission, the client discloses that the reviewed Markdown, original
PDF/image, and optional JSON will be uploaded to that backend; enabling
PaddleOCR-VL may cause the backend to forward the original to its configured
OCR operator. Submission is blocked until the user accepts the disclosure, and
consent must be renewed after changing the backend or OCR choice. The user
should review the operator's retention, privacy, residency, quota, and pricing
before continuing.

Linked Markdown images are a separate outbound-network permission. The HTTP
API keeps them disabled unless its operator sets
`DOCRECONSTRUCT_ALLOW_REMOTE_ASSETS=1`; clients cannot enable that server
capability by themselves.

An ensemble is explicit because it can upload the same document to more than
one service and incur multiple charges:

```powershell
docreconstruct extract exam.pdf --mode cloud --allow-cloud `
  --providers mistral_ocr,azure_document_intelligence --ensemble `
  --formulas --tables --output exam.md --report exam.run.json
```

To use an OCR website manually, export its `.md` and run the normal hybrid
reconstruction with the original layout source. The built-in `markdown`
provider also imports such files as canonical linearized evidence:

```powershell
docreconstruct hybrid website-output.md original-scan.pdf --output editable.docx
docreconstruct analyze website-output.md --output website-output.ir.json
```

If the website also exports JSON, keep both files. The three inputs have
different authority and are intentionally not flattened together:

| Input | Authority in reconstruction |
| --- | --- |
| `.md` | Exact editable wording, formulas, and table-cell content |
| provider `.json`/`.jsonl` | Page/block association, bbox, type, style, confidence, provenance |
| original PDF/image | Page size, margins, pixels, figures, charts, stamps, and scan geometry |

```powershell
docreconstruct hybrid website-output.md original-scan.pdf `
  --evidence website-output.json `
  --output editable.docx --qa-report editable.qa.json

# Independent provider sidecars are repeatable and fused only after each one
# has been aligned to the Markdown content authority.
docreconstruct hybrid content.md original.png `
  --evidence paddle.json --evidence mineru.json --evidence olmocr.json `
  --output editable.docx
```

Loading sidecars is offline: it invokes only saved-result normalization and
never a hosted provider's live OCR method. Auto-detection recognizes canonical
IR, PaddleOCR, MinerU, olmOCR, Mistral OCR, Azure Document Intelligence,
Google Document AI, Mathpix, and AWS Textract schemas. Use repeatable
`--evidence-provider FILE=PROVIDER` when a vendor wrapper makes the schema
ambiguous. Markdown remains exact even when OCR candidates disagree.

## Training is opt-in and license-aware

The core validates and plans training; it does not silently download a model,
scrape documents, or train on user files. Actual fine-tuning is supplied by an
optional `docreconstruct.trainers` plugin.

Two data lanes are mandatory:

- `commercial-permissive`: commercial use is explicitly allowed and a license
  ID is recorded for every sample.
- `research-only`: benchmark/research samples never contribute to a commercial
  model release.

`private-opt-in` is available for corrections whose owner explicitly supplied
a consent scope. PII/secret samples without consent fail validation.

```powershell
docreconstruct dataset-validate dataset.json --lane commercial-permissive `
  --output validation.json
docreconstruct train-plan dataset.json --backend olmocr `
  --lane commercial-permissive --output training-plan.json
```

The manifest records source and ground-truth paths/hashes, document lineage,
writer/template/collection groups, language/script, document/content type,
capture degradation, license, commercial/redistribution rights, PII/consent,
and metadata. Splits are assigned by source-document group, not by page, line,
or word, preventing leakage.

## End-to-end OCR benchmark

`benchmark-ocr` calls the same extraction orchestrator used by real jobs. Each
case names its source, authoritative Markdown, provider mode/options, features,
and tags. Cloud cases still require runtime `--allow-cloud`; a manifest cannot
grant upload permission.

```powershell
docreconstruct benchmark-ocr benchmark/ocr-benchmark.json `
  --allow-cloud --output benchmark/report.json `
  --output-dir benchmark/candidates
```

The report fingerprints inputs, provider/model declarations, emitted Markdown,
and configuration; stores extraction failures by phase; and reports text,
layout, structure, editability, and visual components by language, script,
document type, degradation, and content kind. Use these project-owned results
to route providers rather than comparing unrelated vendor benchmark claims.

## Dataset lanes

Good initial permissive candidates are
[DocLayNet](https://github.com/DS4SD/DocLayNet) for layout and
[PubTables-1M](https://github.com/microsoft/table-transformer) for table
structure. PubLayNet annotations are permissive, but each source image remains
subject to PMC Open Access terms.

Use popular datasets such as FUNSD, XFUND, IAM/IAMonDo, READ, CROHME,
MathWriting, TableBank, and OmniDocBench only in the lane allowed by their own
data terms. A repository's code license does not grant rights to its images or
annotations. [OmniDocBench](https://github.com/opendatalab/OmniDocBench) is a
valuable end-to-end benchmark, but its dataset is research-only; pin the exact
dataset/evaluator version because its metrics evolve.

## Required evaluation slices

Never accept a single aggregate score. Reports must break out language,
script, printed/handwritten, document type, 1-4 columns, formulas, ruled and
borderless tables, forms, historical scans, and capture degradation (rotation,
perspective, curvature/fold, shadow/glare, blur, noise, bleed-through,
occlusion, crop, and background).

Minimum metric families are grapheme CER/WER, layout mAP, reading-order edit,
table TEDS/GriTS, formula structural/render metrics, Markdown AST validity,
hallucination/coverage, confidence calibration, and downstream render fidelity.
