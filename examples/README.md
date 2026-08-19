# Examples

`example_document.json` is a synthetic canonical-IR fixture. It contains no
private source material and requires no OCR model.

After installing the package in editable mode, run:

```bash
python examples/basic_usage.py
```

The example validates the JSON through the public `Document` model, builds a
selective region-routing plan, and writes a self-contained HTML reconstruction
to `output/example.html`.

`ocr-benchmark.example.json` is a template for comparing hosted or plugin OCR
providers through the project's real extraction path. Replace the placeholder
source and truth paths with a licensed corpus, configure provider credentials
through environment variables, and run:

```bash
docreconstruct benchmark-ocr examples/ocr-benchmark.example.json \
  --allow-cloud --output output/ocr-benchmark-report.json
```

The manifest never grants upload permission by itself; `--allow-cloud` remains
mandatory on every hosted run.
