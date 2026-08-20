# Performance and public deployment

`docreconstruct` separates the inexpensive deterministic reconstruction stages
from heavyweight OCR and optional visual verification. This keeps the core
open, auditable, and usable on an ordinary computer while allowing a warm GPU
service to accelerate difficult documents.

There is no honest way to promise that every multi-page document will finish
in a few seconds. Latency depends on page count, resolution, layout complexity,
OCR model, GPU, network, cache state, and whether LibreOffice verification is
requested. Measure the same corpus and hardware before publishing an SLA.

## What “free and open source” means

- `docreconstruct` is Apache-2.0 software.
- PaddleOCR and olmOCR publish Apache-2.0 code. Review the license of every
  selected model, dataset, hosted service, and transitive dependency.
- Running local or browser inference does not incur an OCR API fee, but the
  computer, GPU, electricity, bandwidth, and storage are not cost-free.
- A public demo or hosted API is not an unlimited free GPU. Do not automate an
  undocumented demo endpoint. Use an official API contract or a server that
  you are authorized to operate.
- GitHub Pages hosts only the static client. It cannot run Python,
  LibreOffice, Triton, vLLM, or a GPU model.

After the Pages workflow is deployed, the project web client will be available
at
[kayurachann.github.io/docreconstruct](https://kayurachann.github.io/docreconstruct/).
No public backend or GPU is bundled at that address. A useful public service
therefore needs a separately operated API, or a backend URL supplied by each
user. GitHub Actions is a deployment mechanism, not a safe low-latency public
inference API.

The client requires affirmative upload consent after the backend and
PaddleOCR-VL choice are known. Changing either choice invalidates that consent.
The disclosure covers the selected backend, possible forwarding to its OCR
operator, and the fact that retention, privacy, residency, quota, and fees are
controlled by those operators. A public deployment should link to their current
policies before accepting a document.

## Recommended latency profiles

### Fast response

Use this for the interactive download path:

1. Reuse reviewed Markdown and saved OCR JSON whenever possible.
2. Keep the OCR model and inference server warm.
3. Run providers concurrently only when an ensemble is explicitly requested.
4. Use the project-native QA gates and return the editable DOCX immediately.
5. Cache normalized provider evidence by source/configuration/model hash.

The `/v1/hybrid` upload endpoint uses this profile by default with
`"quality":"fast"`. It never starts LibreOffice. The resulting QA report
measures OOXML structure, content projection, geometry, editability, evidence
placement, and artifact identity. Its passed-gate fraction is conditional
conformance, not a rendered-fidelity or visual-quality score.

### Verified response

Use `"quality":"verified"` when an Office render is required before delivery.
The server operator must configure `DOCRECONSTRUCT_LIBREOFFICE_PATH`. This mode
adds conversion, rasterization, and visual comparison latency. Visual metric
v2.1 combines tolerance-aware foreground F1, multi-radius edge alignment,
region/page macro scores, and page-count/dimension penalties. Adaptive
low-contrast and blank/missing-page negative controls stop white background
from dominating the score.

Every completed Office render must clear the built-in `0.05` v2.1 floor. This
is only a guard against blank or nearly blank renderer failures. It is not a
universal acceptance threshold, and `--min-visual-score` can raise but cannot
lower it. Calibrate thresholds by document family, language, degradation, and
renderer/font environment. Verified work is normally queued or run after the
interactive download in a high-traffic public service.

## Measured P0 reference runs

The table below records one local development run on 2026-08-20. All three
cases reused already-reviewed Markdown and saved positioned JSON, disabled
remote assets, and used native QA; no OCR model, network upload, LibreOffice, or
GPU was part of the timed pipeline. `Wall` includes CLI process startup and
report writing. `Pipeline` is the instrumented hybrid job. These observations
are regression references, not an SLA or a promise that every document will
finish within the same time.

| Case | Pages | Wall | Pipeline | Scan analysis | Evidence | DOCX | Native QA | Result |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| Physics exam 0204 | 1 | 1.881 s | 1.234 s | 0.885 s | 0.113 s | 0.088 s | 0.033 s | 36/36 gates; 51/51 geometry placements |
| Dan-Ba newspaper | 1 | 1.759 s | 1.177 s | 0.519 s | 0.456 s | 0.051 s | 0.059 s | 36/36 gates; 29/29 geometry placements |
| ZOOM exam | 4 | 5.749 s | 5.114 s | 3.604 s | 0.341 s | 0.337 s | 0.164 s | 36/36 gates; 146/146 geometry placements |

The same final tree was then checked with explicit LibreOffice rendering. The
numbers below include a cold isolated Office process and visual v2.1; they are
the slower verification lane, not the interactive fast-path target.

| Case | Wall | Pipeline | Office render | Visual QA | Result | v2.1 score |
| --- | ---: | ---: | ---: | ---: | --- | ---: |
| Physics exam 0204 | 12.076 s | 11.398 s | 9.561 s | 0.642 s | 40/40 gates; 1/1 page | 0.5685 |
| Dan-Ba newspaper | 7.763 s | 7.140 s | 5.590 s | 0.355 s | 41/41 gates; 1/1 page | 0.4760 |
| ZOOM exam | 18.792 s | 18.111 s | 8.805 s | 4.301 s | 40/40 gates; 4/4 pages | 0.3168 |

The same development session also compared the indexed evidence matcher with
its exhaustive implementation while holding thresholds and output ordering
constant. Every case produced an identical ordered match payload. The indexed
path uses exact normalized spans and monotonic anchors; when it has fewer than
two anchors or cannot prove completeness, it deliberately falls back to the
exhaustive scan.

| Case | Exhaustive | Indexed | Speed-up | Accepted matches |
| --- | ---: | ---: | ---: | ---: |
| Physics exam 0204 | 0.1968 s | 0.0981 s | 2.0x | 15 |
| Dan-Ba newspaper | 2.9235 s | 0.4874 s | 6.0x | 23 |
| ZOOM exam | 5.5399 s | 0.3627 s | 15.3x | 73 |

End-to-end time is not the matcher time alone. In the four-page example, scan
analysis remains the largest measured phase. OCR/upload latency, a cold model,
LibreOffice conversion, more pages, larger images, and an unavailable cache can
all dominate these local numbers.

## Reproducible reconstruction benchmark

The first [public unfiltered baseline](../benchmark/omnidocbench-demo/README.md)
runs all 18 official OmniDocBench demo pages and publishes every failure. Its
8/18 operational result is deliberately kept separate from the three selected
showcases. Because it uses ground-truth Markdown and geometry, it measures only
the reconstruction lane and is not an end-to-end parser comparison.

`benchmark-reconstruction` evaluates the production three-authority path. A
case must include the original PDF/image, reviewed Markdown, and one or more
positioned JSON sidecars. The runner generates a fresh DOCX and QA report for
every case; a manifest cannot supply a prebuilt candidate. Paths are resolved
relative to the manifest.

```json
{
  "schema_version": "0.1",
  "seed": 0,
  "cases": [
    {
      "id": "physics-exam-0204",
      "original_layout": "cases/physics/source.png",
      "reviewed_markdown": "cases/physics/reviewed.md",
      "evidence": [
        {"path": "cases/physics/paddleocr.json", "provider": "paddleocr"}
      ],
      "tags": {
        "language": "vi",
        "script": "Latin",
        "document_type": "exam",
        "degradation": "photographed",
        "content_kind": ["text", "formula", "multiple-choice"]
      }
    }
  ]
}
```

Run the quick structural benchmark first:

```bash
docreconstruct benchmark-reconstruction benchmark/reconstruction-benchmark.json \
  --qa-backend native \
  --output-dir benchmark/runs/native \
  --output benchmark/native-report.json
```

Native mode records fresh-candidate hashes, the complete render-input digest,
per-phase timing, operational success, validation-gate conformance, acceptance,
failures, and tag slices. It intentionally reports `quality_score: null` and
`quality_complete: false`, because native gates do not observe the rendered
pages.

Use explicit rendered QA for comparable visual fidelity:

```bash
docreconstruct benchmark-reconstruction benchmark/reconstruction-benchmark.json \
  --qa-backend libreoffice \
  --save-render-artifacts \
  --output-dir benchmark/runs/rendered \
  --output benchmark/rendered-report.json
```

A successful rendered case receives a quality profile such as
`rendered_visual|backend=libreoffice|metric=2.1`. The report publishes a mean
quality score only when every case has complete quality under one comparable
profile. A failed rendered case contributes zero rather than disappearing from
the mean. Native validation-gate conformance remains a separate field and is
never relabeled as fidelity; operational success is reported independently.
For a complete job, QA also requires the candidate SHA-256 to equal the bytes
just written by reconstruction and to remain unchanged throughout validation.
The digest embedded in DOCX core properties is checked through the package's
standard core-properties relationship; duplicate package parts or identifiers
are rejected.
The current manifest contract is version `0.1`; the generated report schema is
version `0.2`. Remote Markdown assets remain disabled unless both a case asks
for them and the operator supplies `--allow-remote-assets`. The manifest
contract is
[`schemas/reconstruction-benchmark.schema.json`](../schemas/reconstruction-benchmark.schema.json).

## One prepared plan for rendering and QA

One hybrid job analyzes and fingerprints its authorities once, then prepares
asset/table matches and the layout plan once. Both DOCX generation and QA use
that exact prepared object. QA rejects a mutated plan or a candidate whose
embedded identifier does not match the expected render input.

The canonical SHA-256 digest covers the original content/layout/evidence file
hashes, normalized Markdown and scan-model hashes, layout plan, asset and table
matches, remote-asset policy, and the media type, size, and SHA-256 of every
snapshotted asset byte sequence. The renderer writes it to the DOCX core
`identifier` property. This is reproducibility and plan-drift evidence, not a
semantic-correctness certificate.

## PaddleOCR deployment choices

### 1. Browser OCR for the lowest operating cost

The official `@paddleocr/paddleocr-js` package runs PP-OCR detection and
recognition in a browser and supports a worker, SIMD, multiple images, and
runtime timing metrics. It is useful for basic line text and polygons without
uploading the page. It is not a replacement for the full PaddleOCR-VL document
parser when tables, formulas, charts, reading order, or rich Markdown are
required.

### 2. Official PaddleOCR AI Studio service

The provider name `paddleocr_official` submits an asynchronous job to the
official Paddle AI Studio service, polls it, downloads the JSONL result, and
normalizes positioned evidence. It reads the user's token from
`PADDLEOCR_ACCESS_TOKEN` and still requires explicit cloud-upload consent:

```text
PADDLEOCR_ACCESS_TOKEN=user-owned-access-token
```

The [official Python SDK documentation](https://www.paddleocr.ai/latest/en/version3.x/inference_deployment/serving/paddleocr_official_api/python.html)
describes the same submit-and-poll service contract. Its overview calls the
TypeScript SDK a Node.js 18+ server-side client, so it is not a way to conceal a
shared credential inside the public GitHub Pages client. This adapter uses
Paddle's hosted quota and is distinct from `paddleocr_vl_server` below.

### 3. Self-managed PaddleOCR-VL server for the best open path

The built-in provider name is `paddleocr_vl_server`. It speaks the official
PaddleOCR-VL `/layout-parsing` contract and normalizes the response into the
same canonical evidence used by saved JSON.

Configure the API process rather than exposing a token or arbitrary endpoint
to web clients:

```text
DOCRECONSTRUCT_PUBLIC_OCR_PROVIDERS=paddleocr_vl_server
PADDLEOCR_VL_SERVER_URL=http://127.0.0.1:8080
PADDLEOCR_VL_SERVER_TOKEN=optional-private-token
DOCRECONSTRUCT_CORS_ORIGINS=https://kayurachann.github.io
```

For a non-loopback endpoint the adapter requires HTTPS and a separate
operator-controlled trusted-endpoint opt-in; an untrusted web client cannot
grant it. After discovery, the hybrid API also requires the client to select
`"ocr_provider":"paddleocr_vl_server"`, because document bytes will leave the
reconstruction server. The older `use_paddleocr_vl` switch is retained only for
compatibility and passes through the same provider allowlist and server-
configuration checks. Setting `PADDLEOCR_VL_SERVER_URL` alone does not expose
the service and neither option bypasses discovery policy.

PaddleOCR's official high-performance serving stack is:

```text
client -> FastAPI gateway -> Triton -> vLLM
```

It supports concurrent requests, Triton dynamic batching, continuous VLM
batching, and independent concurrency controls for inference and page
restructuring. Start from the official defaults and tune with a representative
corpus; excessive concurrency can increase latency or exhaust GPU memory.

### 4. Other hosted OCR with user authorization

Hosted providers can reduce local hardware requirements, but their quota,
pricing, retention, residency, and terms are separate from this repository.
Every cloud upload is opt-in. Credentials stay in process environment or
operator-controlled configuration and are removed from reports, cache keys,
and canonical evidence.

The PaddleOCR and olmOCR repositories publish open code and deployment paths;
they do not grant this project unlimited free access to a hosted GPU. olmOCR
can call OpenAI-compatible external inference servers and lists independently
operated services in its documentation. Availability and charges come from
those service operators, not from the olmOCR license or `docreconstruct`.

### Friendly hosted-choice table

This is a decision aid, not a promise of speed, free capacity, or availability.
Provider limits change independently of this repository; check the linked
official page and the user's own account immediately before a cloud upload.

| Choice | Fastest sensible use | Cost, quota, and privacy boundary |
| --- | --- | --- |
| [PaddleOCR official API / AI Studio](https://www.paddleocr.ai/latest/en/version3.x/inference_deployment/serving/paddleocr_official_api/overview.html) | General multilingual layout and paired Markdown/JSON when the user already has AI Studio access | The [current quota page](https://ai.baidu.com/ai-doc/AISTUDIO/pmjcld5qm) lists 3,000 pages/day/model/user and a 100-page parsing cap per file. This is best-effort quota, not a latency SLA. The cited PaddleOCR API pages do not give a service-specific retention period, so the UI must not claim one. |
| [Mistral OCR](https://docs.mistral.ai/api/endpoint/ocr) | Complex pages when paying per page is acceptable | [Pricing](https://mistral.ai/pricing/api/) currently starts at USD 4/1,000 OCR pages and USD 5/1,000 annotated pages. Account limits vary. [ZDR](https://help.mistral.ai/en/articles/347612-can-i-activate-zero-data-retention-zdr) is available only on eligible paid plans and does not cover every storage/batch route. |
| [Mathpix](https://docs.mathpix.com/) | Mathematics and STEM documents | The [billing guide](https://website.mathpix.com/docs/convert/billing) says no free trial; PDF work is asynchronous and paid. The provider says source/page images may be retained for up to 30 days and text for up to 90 days in its [retention guide](https://docs.mathpix.com/concepts/data-retention). Secret keys must not be put in browser code. |
| [Azure Document Intelligence](https://learn.microsoft.com/en-us/azure/ai-services/document-intelligence/prebuilt/layout?view=doc-intel-4.0.0) | Forms, tables, and layout for users with an Azure resource | The [F0 limits](https://learn.microsoft.com/en-us/azure/ai-services/document-intelligence/service-limits?view=doc-intel-4.0.0) currently allow 500 pages/month but only two pages/request and one analyze transaction/second. The [FAQ](https://learn.microsoft.com/en-us/azure/ai-services/document-intelligence/faq?view=doc-intel-4.0.0) states temporary regional storage with deletion within 24 hours. |
| [Google Document AI](https://docs.cloud.google.com/document-ai/docs/overview) | Enterprise OCR/forms for users already set up in Google Cloud | The [pricing table](https://cloud.google.com/products/document-ai/pricing) currently gives a 1,000-page/month no-charge allowance for Enterprise Document OCR, followed by usage fees. Google says customer documents/predictions are [not used to train Document AI](https://docs.cloud.google.com/document-ai/docs/security). Project, processor, billing, and OAuth setup still add startup time. |
| [OCR.space](https://ocr.space/ocrapi) | Short, non-sensitive documents directly from the browser | The free plan currently lists 500 requests/day/IP, 25,000/month, 1 MB/file, three PDF pages, and no SLA. Position overlay is slower. Require the user's own key; a key in public JavaScript can be copied and its quota exhausted. |
| [olmOCR](https://github.com/allenai/olmocr) | Saved output, local GPU work, or a user-paid compatible inference host | The model code is open, but a capable GPU or separately billed remote provider is still required. Its [online demo](https://olmocr.allenai.org/) is for evaluation and has no published production SLA. |
| [Hugging Face public Space demo](https://huggingface.co/spaces/PaddlePaddle/PaddleOCR-VL-1.6_Online_Demo) | A manual trial before the user chooses a deployment | [ZeroGPU](https://huggingface.co/docs/hub/main/spaces-zerogpu) has account-dependent daily GPU-minute quotas, queues, and duration limits. [Dedicated endpoints](https://huggingface.co/docs/inference-endpoints/en/pricing) are paid. Never make a public demo the hidden default backend. |

For high-fidelity reconstruction, positioned JSON is mandatory. The fastest
path is to reuse a saved, source-hash-matched JSON sidecar. If it is absent, the
user must explicitly choose a hosted OCR service that creates geometry-rich
JSON; the OCR upload and wait time become part of the request. Markdown-only or
text-only JSON remains a best-effort path and must not receive a high-fidelity
label.

## Hybrid HTTP request

The backend operator decides which hosted OCR services the browser may discover
and configures their credentials only in the server process:

```text
DOCRECONSTRUCT_PUBLIC_OCR_PROVIDERS=paddleocr_official,mistral_ocr
PADDLEOCR_ACCESS_TOKEN=operator-secret
MISTRAL_API_KEY=operator-secret
```

`GET /v1/hybrid/capabilities` lists only names on that allowlist; only a provider
whose required server configuration is present is marked `available` and can
make `hosted_ocr` an evidence mode. Its browser-safe response includes
`evidence_required`, `evidence_modes`, `server_generates_json`,
`browser_credentials_accepted`, `maximum_upload_mb`, and non-secret provider
labels/capabilities. It never returns environment-variable names or values,
tokens, keys, base URLs, or provider endpoints. The client sends only the
discovered provider name in `options.ocr_provider`.

The public web client sends the following multipart fields to an operator's
backend, not to GitHub Pages:

- `content`: required reviewed Markdown;
- `layout`: required original PDF or image;
- `evidence`: required positioned OCR/layout JSON in high-fidelity mode; it may
  be omitted only when the selected backend will create it through an
  explicitly authorized OCR call;
- `options`: a JSON-encoded `HybridOptions` object.

Example:

```bash
curl -f https://your-backend.example/v1/hybrid \
  -F "content=@content.md" \
  -F "layout=@original.pdf" \
  -F "evidence=@paddleocr.json" \
  -F 'options={"quality":"fast","evidence_provider":"paddleocr"}' \
  --output reconstructed.docx
```

When JSON is absent, high-fidelity mode requires an operator with a warm,
explicitly authorized PaddleOCR-VL server (or another geometry-capable hosted
provider). The operator must both configure the server and include
`paddleocr_vl_server` in `DOCRECONSTRUCT_PUBLIC_OCR_PROVIDERS`. The preferred
request is:

```json
{"quality":"fast","ocr_provider":"paddleocr_vl_server"}
```

Older clients may still send `"use_paddleocr_vl":true`, but it succeeds only
under the same allowlist and configuration checks; it does not bypass them.

The upload API does not accept local filesystem paths, renderer executables,
OCR server URLs, or credentials from an unauthenticated client.

Fetching images linked from Markdown is disabled on the HTTP API unless the
operator deliberately sets `DOCRECONSTRUCT_ALLOW_REMOTE_ASSETS=1`. Local image
references remain confined to the uploaded Markdown directory, and remote
HTTPS targets are checked against private, loopback, link-local, and reserved
networks. Keep the feature disabled when a deployment does not need it.

## Practical production checklist

- Preload all OCR/model weights before accepting traffic.
- Separate web workers from GPU workers; do not load a model per HTTP request.
- Cache canonical OCR evidence, not secret-bearing raw requests.
- Bound provider concurrency and timeouts; preserve deterministic provider
  order even when requests finish out of order.
- Parallelize independent pages only when the provider contract supports it.
- Keep visual verification asynchronous when user-perceived latency matters.
- Record phase timings, cache hits, provider/model versions, and failures.
- Enforce upload-size, request-rate, job-count, and storage-retention limits.
- Configure CORS only for the known static site origin.
- Treat documents as sensitive and publish a deletion/retention policy.

## Primary references

- [PaddleOCR repository and license](https://github.com/PaddlePaddle/PaddleOCR)
- [PaddleOCR-VL high-performance serving](https://github.com/PaddlePaddle/PaddleOCR/blob/main/deploy/paddleocr_vl_docker/hps/README_en.md)
- [PaddleOCR high-performance inference](https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/inference_deployment/local_inference/high_performance_inference.en.md)
- [PaddleOCR.js browser deployment](https://www.paddleocr.ai/latest/en/version3.x/inference_deployment/cross_platform/browser.html)
- [PaddleOCR official API overview](https://www.paddleocr.ai/latest/en/version3.x/inference_deployment/serving/paddleocr_official_api/overview.html)
- [PaddleOCR official Python SDK](https://www.paddleocr.ai/latest/en/version3.x/inference_deployment/serving/paddleocr_official_api/python.html)
- [PaddleOCR AI Studio quota documentation](https://ai.baidu.com/ai-doc/AISTUDIO/pmjcld5qm)
- [Mistral OCR API](https://docs.mistral.ai/api/endpoint/ocr) and [pricing](https://mistral.ai/pricing/api/)
- [Mathpix API](https://docs.mathpix.com/), [authentication](https://docs.mathpix.com/reference/authentication), and [retention](https://docs.mathpix.com/concepts/data-retention)
- [Azure Document Intelligence limits](https://learn.microsoft.com/en-us/azure/ai-services/document-intelligence/service-limits?view=doc-intel-4.0.0)
- [Google Document AI pricing](https://cloud.google.com/products/document-ai/pricing) and [security](https://docs.cloud.google.com/document-ai/docs/security)
- [OCR.space API limits, AJAX example, and privacy statement](https://ocr.space/ocrapi)
- [olmOCR repository and external inference options](https://github.com/allenai/olmocr)
- [Hugging Face ZeroGPU quotas](https://huggingface.co/docs/hub/main/spaces-zerogpu) and [dedicated endpoint pricing](https://huggingface.co/docs/inference-endpoints/en/pricing)
- [GitHub Pages custom workflows](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages)
- [GitHub Pages limits](https://docs.github.com/en/pages/getting-started-with-github-pages/github-pages-limits)
