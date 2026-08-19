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
- PaddleOCR code and the package metadata are Apache-2.0. Review the license of
  every selected model, dataset, hosted service, and transitive dependency.
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

### 2. Self-managed PaddleOCR-VL server for the best open path

The built-in provider name is `paddleocr_vl_server`. It speaks the official
PaddleOCR-VL `/layout-parsing` contract and normalizes the response into the
same canonical evidence used by saved JSON.

Configure the API process rather than exposing a token or arbitrary endpoint
to web clients:

```text
PADDLEOCR_VL_SERVER_URL=http://127.0.0.1:8080
PADDLEOCR_VL_SERVER_TOKEN=optional-private-token
DOCRECONSTRUCT_CORS_ORIGINS=https://kayurachann.github.io
```

For a non-loopback endpoint the adapter requires HTTPS and a separate
operator-controlled trusted-endpoint opt-in; an untrusted web client cannot
grant it. The hybrid API also requires the client to opt in with
`"use_paddleocr_vl":true`, because document bytes will leave the reconstruction
server.

PaddleOCR's official high-performance serving stack is:

```text
client -> FastAPI gateway -> Triton -> vLLM
```

It supports concurrent requests, Triton dynamic batching, continuous VLM
batching, and independent concurrency controls for inference and page
restructuring. Start from the official defaults and tune with a representative
corpus; excessive concurrency can increase latency or exhaust GPU memory.

### 3. Hosted OCR with user authorization

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

## Hybrid HTTP request

The public web client sends the following multipart fields to an operator's
backend, not to GitHub Pages:

- `content`: required reviewed Markdown;
- `layout`: required original PDF or image;
- `evidence`: optional OCR/layout JSON;
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

When JSON is absent, an operator with a warm PaddleOCR-VL server may use:

```json
{"quality":"fast","use_paddleocr_vl":true}
```

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
- [olmOCR repository and external inference options](https://github.com/allenai/olmocr)
- [GitHub Pages custom workflows](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages)
- [GitHub Pages limits](https://docs.github.com/en/pages/getting-started-with-github-pages/github-pages-limits)
