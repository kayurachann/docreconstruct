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

## High-fidelity evidence contract

High-fidelity reconstruction begins only when the job has all three sources:

1. the original PDF/image as pixel and page-geometry authority;
2. reviewed Markdown as wording and reading-sequence authority; and
3. positioned JSON as page/block, semantic-type, geometry, confidence, and
   provenance evidence.

The positioned JSON may be uploaded by the user or generated through an
explicitly selected hosted OCR provider. It must include page identifiers and
dimensions plus a bounding box or polygon for each usable block. A generic JSON
file containing only extracted text does not satisfy this contract. A
best-effort reconstruction may still run without all three sources, but it must
be labeled lower-confidence and must not be presented as the high-fidelity
path.

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
- [Mistral OCR](https://docs.mistral.ai/api/endpoint/ocr),
  [Azure Document Intelligence](https://learn.microsoft.com/en-us/azure/ai-services/document-intelligence/prebuilt/layout),
  [Google Document AI](https://cloud.google.com/document-ai/docs/overview),
  [AWS Textract](https://docs.aws.amazon.com/textract/latest/dg/how-it-works-analyzing.html),
  and [Mathpix](https://docs.mathpix.com/) are opt-in hosted specialists.
- [OCR.space](https://ocr.space/ocrapi) documents a small browser-callable OCR
  API, while [Hugging Face Spaces](https://huggingface.co/docs/hub/spaces-api-endpoints)
  provides APIs for individual demo Spaces. Neither should be described as an
  unlimited public production service.

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

## Hosted OCR chooser

This table summarizes official documentation, not permanent entitlements.
Quotas, prices, availability, and policy can change without a project release;
show the linked provider page and the terms in the user's own account at the
moment of consent.

| Choice | Evidence useful to reconstruction | Hosted-service reality | Safe integration in this project |
| --- | --- | --- | --- |
| [PaddleOCR official API / AI Studio](https://www.paddleocr.ai/latest/en/version3.x/inference_deployment/serving/paddleocr_official_api/overview.html) | Per-page Markdown, `prunedResult` JSON, images, layout, tables, and formulas | Uses a user AI Studio access token. The [current quota documentation](https://ai.baidu.com/ai-doc/AISTUDIO/pmjcld5qm) lists 3,000 pages/day/model/user, returns `429` after quota, and processes only the first 100 pages of a larger file. It is a quota, not a production SLA. The cited PaddleOCR API pages do not state a service-specific retention period; do not infer one. | Prefer manual export from the [official product UI](https://aistudio.baidu.com/paddleocr) or a connector that uses the user's own credentials. The official TypeScript client is not a promise that a long-lived token is safe in public JavaScript. |
| [Mistral OCR](https://docs.mistral.ai/api/endpoint/ocr) | Markdown by page, page dimensions and images; OCR 4 can return structured annotations, confidence, and bounding boxes | [API pricing](https://mistral.ai/pricing/api/) is per page (currently USD 4/1,000 OCR pages and USD 5/1,000 annotated pages). Experimental/free access and rate limits are account-specific, not guaranteed production capacity. [Zero Data Retention](https://help.mistral.ai/en/articles/347612-can-i-activate-zero-data-retention-zdr) is restricted to eligible paid plans and excludes file, batch, and other stateful routes. | Use a user-owned key through a trusted backend. Do not publish a shared key. If an upload uses the Files route, do not imply that OCR-endpoint Zero Data Retention covers the stored file. |
| [Mathpix](https://docs.mathpix.com/) | Mathpix Markdown/LaTeX plus `.lines.json` text, confidence, printed/handwritten flags, and line polygons | The [billing guide](https://website.mathpix.com/docs/convert/billing) says there is no free trial and describes setup/usage charges. The [retention guide](https://docs.mathpix.com/concepts/data-retention) currently states up to 30 days for source/page images and 90 days for text output. | Mathpix [explicitly forbids exposing secret keys in client code](https://docs.mathpix.com/reference/authentication). Its five-minute app token does not authorize PDF/batch processing, so static-browser PDF upload requires a trusted token broker/backend. |
| [Azure Document Intelligence](https://learn.microsoft.com/en-us/azure/ai-services/document-intelligence/prebuilt/layout?view=doc-intel-4.0.0) | Layout JSON with word/block polygons, tables, figures, and Markdown output | The [F0 limits](https://learn.microsoft.com/en-us/azure/ai-services/document-intelligence/service-limits?view=doc-intel-4.0.0) currently allow 500 pages/month, 4 MB documents, one analyze transaction/second, and only the first two pages per request. Microsoft says in its [FAQ](https://learn.microsoft.com/en-us/azure/ai-services/document-intelligence/faq?view=doc-intel-4.0.0) that analysis data is temporarily stored in the selected region and deleted within 24 hours. | Optional BYOK for users who already have an Azure resource. Microsoft says not to put an API key directly in code; keep credentials server-side or use an appropriate Entra flow. |
| [Google Document AI](https://docs.cloud.google.com/document-ai/docs/overview) | `Document` JSON with text anchors, confidence, blocks, words, and `boundingPoly`; Markdown must be derived by the project | The [pricing table](https://cloud.google.com/products/document-ai/pricing) currently gives the first 1,000 Enterprise Document OCR pages/account/month at no charge and then charges by usage. A Cloud project, processor, billing setup, and credentials are still required. Google states that it [does not use customer documents or predictions to train Document AI models](https://docs.cloud.google.com/document-ai/docs/security). | An advanced option for users with their own Google Cloud credentials. Never bundle a service-account JSON file in a static site. Normalize Document JSON and preserve the raw response. |
| [OCR.space](https://ocr.space/ocrapi) | Parsed text and optional word overlay coordinates; engine 3 can emit table Markdown and handwriting output | The documented free plan currently limits use to 500 requests/day/IP, 25,000/month, 1 MB/file, and three PDF pages, with no SLA. Overlay output is slower, and engine 3 geometry is less exact. The provider states that source files and OCR text are not stored. | It is the only choice in this table whose official docs show direct browser AJAX. Require each user to enter their own key and keep it in memory/session; a public-browser key can still be copied and its quota exhausted. Use only for short, non-sensitive files. |
| [olmOCR](https://github.com/allenai/olmocr) | Markdown with natural reading order for equations, tables, handwriting, scans, and multi-column pages | Apache-2.0 code does not include hosted compute. Local inference needs a GPU; the repo's verified external providers publish separate token prices. The [online demo](https://olmocr.allenai.org/) is for testing and has no documented production API or SLA. | Import saved output, run locally, or use a user-authorized OpenAI-compatible inference host. Never automate the public demo as a backend. olmOCR Markdown alone still needs a positioned JSON source for high-fidelity reconstruction. |
| [Hugging Face public Spaces](https://huggingface.co/spaces/PaddlePaddle/PaddleOCR-VL-1.6_Online_Demo) | Model-dependent demo output; useful for evaluation before deployment | A Space belongs to its operator. [ZeroGPU](https://huggingface.co/docs/hub/main/spaces-zerogpu) uses small account-dependent daily GPU-minute quotas, queues, and per-function duration limits. [Dedicated endpoints](https://huggingface.co/docs/inference-endpoints/en/pricing) are paid. Dedicated-endpoint privacy claims do not automatically apply to a public Space. | Link to an official demo as an optional trial, not as a hidden production dependency. Import exported files only when they include the required geometry. |

### Static-browser credential boundary

GitHub Pages cannot keep a secret: any shared API key embedded in JavaScript,
HTML, a build-time variable, or a downloadable configuration file is visible to
visitors. Browser-direct processing is acceptable only when the provider
documents cross-origin browser calls and the user supplies their own scoped
credential. Otherwise, use the provider's official website for manual export,
a trusted backend, or a separately deployed token broker. A public demo is not
a token broker and must not be scraped or automated.

## Privacy modes

- `local`: document bytes never leave the machine; only installed local
  providers are eligible.
- `cloud`: a hosted API is eligible only after an explicit per-call opt-in and
  credential configuration.
- `hybrid`: local/native evidence is preserved and selected cloud specialists
  can adjudicate difficult regions.

No credential is written to a run report. Remote providers are disabled by
default, even if an API key exists in the environment.

### PaddleOCR official hosted API

The provider name `paddleocr_official` calls PaddleOCR's official asynchronous
AI Studio service: it submits a job, polls its status, downloads the completed
JSONL result, and normalizes that result as positioned evidence. Set the access
token documented by the [official PaddleOCR API SDK](https://www.paddleocr.ai/latest/en/version3.x/inference_deployment/serving/paddleocr_official_api/python.html),
then authorize that upload explicitly:

```powershell
$env:PADDLEOCR_ACCESS_TOKEN = "..."
docreconstruct extract scan.pdf --mode cloud --allow-cloud `
  --provider paddleocr_official --output scan.md --report scan.run.json
```

The official SDK overview says the service clients submit files, poll jobs, and
read `PADDLEOCR_ACCESS_TOKEN`; they do not run local inference. It also describes
the TypeScript SDK as a Node.js 18+ server-side client, not a safe place to hide
a shared key in browser JavaScript. `paddleocr_official` is therefore different
from `paddleocr_vl_server` below: the former uses Paddle AI Studio with the
user's token and service quota, while the latter calls a PaddleOCR-VL server
chosen and operated by the reconstruction backend owner.

### Operator-managed PaddleOCR-VL

`paddleocr_vl_server` calls the complete official `/layout-parsing` pipeline
contract rather than a bare chat/completions endpoint. That distinction retains
the preprocessing, layout, page restructuring, table, formula, and Markdown
evidence needed by reconstruction. The adapter then normalizes the response to
the same canonical IR used for saved PaddleOCR JSON.

The API operator supplies configuration; a browser client cannot submit an OCR
URL or token:

```powershell
$env:DOCRECONSTRUCT_PUBLIC_OCR_PROVIDERS = "paddleocr_vl_server"
$env:PADDLEOCR_VL_SERVER_URL = "http://127.0.0.1:8080"
$env:PADDLEOCR_VL_SERVER_TOKEN = "optional-private-token"
$env:DOCRECONSTRUCT_CORS_ORIGINS = "https://kayurachann.github.io"
docreconstruct-api
```

The URL alone is not enough. The operator must also place
`paddleocr_vl_server` in `DOCRECONSTRUCT_PUBLIC_OCR_PROVIDERS`. Both the current
`ocr_provider` field and the legacy `use_paddleocr_vl` switch pass through that
same allowlist and configuration check.

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

PaddleOCR official, Mistral OCR, Azure Document Intelligence, Google Document
AI, and Mathpix are built-in hosted adapters. Their official REST APIs are
called from the project; no browser automation is required. AWS Textract JSON
exported by the service/console is supported as saved evidence; live AWS signing
remains an optional plugin concern. Set credentials in the process environment,
then authorize the specific command:

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
  --qa-backend libreoffice `
  --qa-render-dir output/job.render `
  --qa-report output/job.qa.json --output output/job.docx
```

This is one project job, not a document-specific helper script. The Markdown
file remains immutable content authority; online OCR contributes only geometry,
semantic type, style, confidence, and provenance. `output/job.ocr` contains the
audit Markdown, independent canonical JSON sidecars, a sanitized extraction
manifest, and an optional SHA-verified cache. Disable the cache with
`--no-ocr-cache`. LibreOffice is never discovered or launched unless the
render backend is explicitly selected. Rendered QA always applies the visual
metric v2.1 `0.05` blank-render safety floor. That floor is not a document-
quality target; after measuring a reviewed corpus, an operator may raise it
with `--min-visual-score`.

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

The backend operator may publish a narrow OCR allowlist and keep every
credential in the server process:

```text
DOCRECONSTRUCT_PUBLIC_OCR_PROVIDERS=paddleocr_official,mistral_ocr
PADDLEOCR_ACCESS_TOKEN=operator-secret
MISTRAL_API_KEY=operator-secret
```

`GET /v1/hybrid/capabilities` lists only allowlisted names. A provider is usable
for server-generated JSON only when its required server configuration is also
present. The response reports the high-fidelity evidence requirement, available
evidence modes, upload limit, and non-secret provider capabilities; it never
returns credential names or values, tokens, keys, base URLs, or endpoints. The
browser may choose a discovered `ocr_provider`, but cannot supply a new service
URL or browser-held credential.

The user must enter or select a backend operated by a party they trust. In the
high-fidelity workflow, positioned JSON is required: the user can upload a
saved result or explicitly authorize the selected hosted OCR service to create
one. Before submission, the client names the destination and discloses that the
reviewed Markdown, original PDF/image, and JSON will be uploaded; generating
JSON online may cause the backend to forward the original to the selected OCR
operator. Submission is blocked until the user accepts the disclosure, and
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

To use an OCR website manually, export its `.md` and positioned `.json`, then
run normal hybrid reconstruction with the original layout source. The built-in
`markdown` provider also imports the text as canonical linearized evidence. If
a website exports Markdown only, it can support best-effort reconstruction but
does not complete the high-fidelity evidence set:

```powershell
docreconstruct hybrid website-output.md original-scan.pdf --output editable.docx
docreconstruct analyze website-output.md --output website-output.ir.json
```

Keep both exported files. The three inputs have different authority and are
intentionally not flattened together:

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

An initial [public OmniDocBench demo baseline](../benchmark/omnidocbench-demo/README.md)
now retains all 18 official demo pages, including ten strict-alignment
failures. It is an oracle-reconstruction lane using ground-truth Markdown and
geometry, not an OCR/parser score. The project has not yet published a valid
provider-realistic OmniDocBench comparison with Docling, MinerU, or Marker.

## Required evaluation slices

Never accept a single aggregate score. Reports must break out language,
script, printed/handwritten, document type, 1-4 columns, formulas, ruled and
borderless tables, forms, historical scans, and capture degradation (rotation,
perspective, curvature/fold, shadow/glare, blur, noise, bleed-through,
occlusion, crop, and background).

Minimum metric families are grapheme CER/WER, layout mAP, reading-order edit,
table TEDS/GriTS, formula structural/render metrics, Markdown AST validity,
hallucination/coverage, confidence calibration, and downstream render fidelity.
