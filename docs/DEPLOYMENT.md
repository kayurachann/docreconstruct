# Running docreconstruct as a public website on free third-party infrastructure

This guide describes a deployment where users only upload files in their
browser and download a finished, editable Word document — no local install, no
cloud OCR credential, no paid service anywhere in the stack. Everything below
is free software on infrastructure with a genuinely free tier.

## What the stack is

One container, built from the repository `Dockerfile` with `--target full`:

| Piece | Role | License |
| --- | --- | --- |
| docreconstruct API (FastAPI + uvicorn) | upload endpoints, reconstruction, QA | Apache-2.0 |
| Tesseract OCR (`tesseract-ocr` + `vie`, `eng`, `chi_sim`) | free server-side OCR evidence | Apache-2.0 |
| LibreOffice Writer (headless) | render QA and `verified` quality | MPL-2.0 |
| `site/` static page | the browser UI | Apache-2.0 |

```bash
docker build --target full -t docreconstruct-server .
docker run -p 8000:8000 docreconstruct-server
```

The `full` image pre-sets two environment variables:

- `DOCRECONSTRUCT_PUBLIC_OCR_PROVIDERS=tesseract_local` — offers the
  server-local Tesseract engine on `/v1/hybrid/capabilities`. It is the only
  entry on the hosted-OCR list that needs no credential; documents never leave
  the machine (`privacy: no_transfer`, `cost: free`). Remove or change the
  variable to withdraw or extend the offer — it remains an operator allowlist.
- `DOCRECONSTRUCT_LIBREOFFICE_PATH=/usr/bin/soffice` — enables render QA and
  the `verified` quality level.

Useful knobs: `DOCRECONSTRUCT_MAX_UPLOAD_MB` (default 50) and
`DOCRECONSTRUCT_MAX_API_PAGES` (default 400) bound what one request can cost.

## The two service levels, honestly

- **`quality: "fast"`** returns the DOCX whenever one was produced, with the
  QA verdict in response headers (`X-DocReconstruct-QA-Score`,
  `X-DocReconstruct-QA-Passed`). Real scans rarely clear all 39 gates; the
  caller gets the document *and* the truth about it.
- **`quality: "verified"`** fails closed: no artifact unless every gate
  passed. Use it where an unproven document is worse than none.

## Free hosting options that actually fit

Measured footprint of one reconstruction on this stack: 1–7 s CPU, well under
1 GB RAM; LibreOffice render QA adds ~8–12 s when enabled. The image with
Tesseract + LibreOffice is roughly 1.5 GB.

| Host | Free tier | Fit |
| --- | --- | --- |
| **Hugging Face Spaces** (Docker Space) | 2 vCPU, 16 GB RAM, no credit card | Best starting point. Add `app_port: 8000` to the Space README metadata. Sleeps after inactivity (cold start ~1 min); public URL and HTTPS included. |
| **Oracle Cloud Always Free** | 4 ARM OCPU, 24 GB RAM VM, always-on | The most capable truly free option; a real VM you operate (card required at signup, not charged). `docker compose up -d` and a reverse proxy. |
| **Google Cloud Run** | 2M requests/month, scale-to-zero | Good for bursty public use; set memory ≥ 2 GiB. Cold starts are noticeable with a 1.5 GB image. Card required. |

Render/Railway/Fly free tiers are too small for LibreOffice (512 MB-class
instances) — use the slim `runtime` target there and skip render QA, or skip
them entirely.

## What "identical to the original" means here — read this before promising it

This project's own measurements, on its own showcase corpus, with its shipped
visual metric (`VISUAL_METRIC_VERSION 2.2`, LibreOffice backend): headline
visual scores of **23–57%**, where the score reads as "how much of the ink
lands where the source put it", not "percentage of the page that looks right"
(pixel similarity on the same files is 79–95%, because most of a page is blank
paper). The OmniDocBench demo report records 0.07–0.36 on the same family of
metric.

Accuracy is bounded by the OCR engine. Tesseract is strong on clean printed
Latin text, usable on Vietnamese with the `vie` traineddata, and **poor on
mathematics** — a formula-heavy page will carry garbled evidence text, and the
content-authority rule means the reconstruction preserves what OCR actually
read rather than inventing corrections. The design's path to a faithful
document is unchanged: a human reviews the Markdown content authority. The
fully automatic flow is honest about being an approximation; the QA headers
say exactly how good each result was.

## Operational notes

- The API has no authentication or rate limiting; put a reverse proxy or the
  host platform's controls in front of anything public.
- Uploads are staged under the process temp directory and deleted after each
  request; error messages redact server paths; page and byte budgets refuse
  decompression-bomb-shaped inputs. These are already in the codebase.
- The static `site/` can be served by any static host (GitHub Pages included);
  point its endpoint field at the API URL. Its provider list is driven by
  `/v1/hybrid/capabilities`, so a server offering `tesseract_local` shows the
  free engine automatically.
