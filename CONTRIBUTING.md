# Contributing to docreconstruct

Thank you for helping turn extracted document evidence into reliable, editable
structure. Contributions should preserve the project's central separation:
providers observe and normalize evidence, the canonical IR carries it, and
deterministic reconstruction code plans and renders it.

## Set up a development environment

Use Python 3.11 or newer in an isolated environment:

```bash
python -m venv .venv
# PowerShell: .venv\Scripts\Activate.ps1
# POSIX:      source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[all,dev]"
```

Run the checks relevant to your change before opening a pull request:

```bash
pytest
ruff check .
mypy src/docreconstruct
```

Tests should be deterministic, avoid network and model downloads, and use small
synthetic fixtures wherever possible. Add a regression test for every bug fix.

## Contribution boundaries

### Canonical IR changes

Treat `Document` as a public interchange contract. New fields should be typed,
portable, backward-compatible where practical, and represented in the JSON
Schema and example fixture. Keep provider-specific payloads in `metadata` until
there is a clear cross-provider concept. Never discard confidence or provenance
merely because a renderer cannot use it yet.

### Provider adapters

- Implement the provider protocol and register a stable name.
- Normalize coordinates into the source page coordinate system.
- Preserve exact observed text and source element identifiers.
- Keep heavyweight inference stacks optional. Unit tests should consume saved,
  minimal provider output rather than downloading a model.
- Document live-inference capability honestly. Parsing exported JSON and running
  an OCR engine are different capabilities.
- Review and document the provider and model licenses; do not vendor third-party
  code or weights without explicit approval.

### Routing and adjudication

- Prefer the lowest-cost capable provider for ordinary regions.
- Route specialists by page/region evidence; do not run every engine by default.
- Escalate to fusion or consensus only for low-confidence, conflicting, or
  explicitly forced repair regions.
- An AI verifier may select an existing candidate or change structure, but it
  must not author document content. Validate all proposals through the public
  verifier contract.

### Renderers

- Render from canonical IR, not directly from a provider payload.
- Keep output deterministic for the same IR and options.
- Prefer native editable objects over flattened page screenshots.
- Raise an actionable optional-dependency error only when the renderer is used.
- Add structural assertions in addition to snapshot or visual checks.

### Evaluation

Metrics must state their input requirements and score range. A missing visual
render, font match, or ground-truth label is `unavailable`, not a perfect score.
Keep component scores visible rather than hiding all behavior in one aggregate.

## Pull requests

Keep pull requests focused and explain:

1. the document behavior that changed;
2. how the change was verified;
3. any new optional dependency or third-party license;
4. compatibility impact on IR, providers, renderers, CLI, or API.

Do not include real confidential documents in issues, tests, screenshots, or
benchmark fixtures. Redact metadata as well as visible page content.
