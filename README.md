# docreconstruct

[English](README.md) · [Tiếng Việt](docs/i18n/README.vi.md) ·
[简体中文](docs/i18n/README.zh-CN.md) · [Русский](docs/i18n/README.ru.md)

Layout-aware reconstruction of scanned documents into **editable**
DOCX/HTML/Markdown. Experimental.

## Status — measured, not promised

| What | Measured |
| --- | --- |
| Rendered visual similarity on the three committed showcases (metric 2.2, LibreOffice) | **23–57%** |
| OmniDocBench official demo, oracle lane (ground-truth inputs, 18/18 pages operational) | mean rendered quality **0.07–0.36** per page class |
| Real 4-page Vietnamese exam, three-authority flow | **40/40 QA gates**, 22 native equations, 7 figures from source pixels — but page ink sits at 24–38% height vs the source's 45–60% |

Read the visual score as "how much ink lands where the source put it", not
"percent of the page that looks right". The reconstruction is fully editable
(native paragraphs, tables, Office Math); making it *fill the page like the
source* is active work, tracked as source-locked layout.

What this project deliberately does **not** do: invent text the OCR did not
read. Reviewed Markdown is the content authority; errors in it are preserved,
not silently "fixed" from pixels.

## Quick start

```bash
pip install -e ".[all]"
```

Best quality — three authorities (reviewed Markdown + original scan + OCR JSON):

```bash
python -m docreconstruct.cli hybrid content.md original.pdf -E evidence.json -o out.docx
```

One file in, editable DOCX out (quality bounded by the OCR engine you have):

```bash
python -m docreconstruct.cli reconstruct scan.pdf -o out.docx
```

Evaluate any result against the source image, with the same metric CI uses:

```bash
python -m docreconstruct.cli hybrid content.md original.pdf -o out.docx --qa-backend libreoffice
```

The three-file requirement is the price of the "never invent text" rule; the
Markdown for the showcases below ships in `docs/showcases/*/content.md` so
every number here can be regenerated with one command (CI does exactly that).

## Showcases

Three committed examples with sources, inputs, outputs and re-measured scores:
[docs/showcases](docs/showcases/README.md). A CI job rebuilds each from its
committed inputs on every push and fails if the result drifts.

## Documentation

- [Extended overview](docs/OVERVIEW.md) — the full architecture, IR, providers,
  benchmarks, legal notes.
- [Self-hosting](docs/DEPLOYMENT.md) — for yourself or a trusted group. The API
  has no authentication, no rate limiting and no job queue; it is **not** ready
  for the public internet.
- [Performance](docs/PERFORMANCE.md) — measured timings and budgets.
- [Contributing](CONTRIBUTING.md) · [Security policy](SECURITY.md) ·
  [Changelog](CHANGELOG.md)

## Honest limitations

- Best-quality output requires human-reviewed Markdown. Fully automatic mode
  inherits every OCR error and states its own QA score in the response.
- Page fill is compressed on 1.5-spaced sources (the line-rhythm work above).
- Two-column mastheads render stacked, not side by side.
- Output is verified with LibreOffice in CI; Microsoft Word is stricter about
  OOXML and has caught ordering bugs LibreOffice tolerated. Open important
  documents in Word before trusting them.
- No published comparison against pandoc, Docling, MinerU, Marker or commercial
  converters yet; until one exists, treat "better than X" as unknown.

## License

Apache-2.0. Showcase images remain the property of their original publishers;
see [docs/showcases/README.md](docs/showcases/README.md) for provenance and
rights notes.
