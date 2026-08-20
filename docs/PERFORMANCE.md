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
`"quality":"fast"`. It never starts LibreOffice.

### Verified response

Use `"quality":"verified"` when an Office render is required before delivery.
The server operator must configure `DOCRECONSTRUCT_LIBREOFFICE_PATH`. This mode
adds conversion, rasterization, and visual comparison latency; it should be a
background job for a high-traffic public service.

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
