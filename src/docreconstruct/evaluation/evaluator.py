"""High-level evaluation entry point with graceful artifact coercion."""

from __future__ import annotations

import html.parser
import json
import re
import zipfile
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from .document_rendering import DocumentRenderResult, render_docx_pages
from .fidelity import FidelityScore, calculate_fidelity
from .metrics import (
    EditabilityMetrics,
    LayoutMetrics,
    StructureMetrics,
    TextMetrics,
    evaluate_editability,
    evaluate_layout_and_structure,
    evaluate_text,
)
from .visual import VisualMetrics, evaluate_visual

_RASTER_SUFFIXES = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".gif", ".webp"}
EVALUATION_REPORT_SCHEMA_VERSION = "1.1.0"
EVALUATION_METRIC_VERSION = "3.0.0-alpha.1"


class MeasurementStatus(StrEnum):
    """Whether a component was actually evaluated in this report."""

    MEASURED = "measured"
    NOT_MEASURED = "not_measured"
    NOT_APPLICABLE = "not_applicable"


def _has_pages(source: Any) -> bool:
    return (isinstance(source, dict) and isinstance(source.get("pages"), list)) or hasattr(
        source, "pages"
    )


class _TextHTMLParser(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.suppressed = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"style", "script", "template"}:
            self.suppressed += 1
        elif not self.suppressed and tag in {
            "p",
            "div",
            "section",
            "h1",
            "h2",
            "h3",
            "h4",
            "h5",
            "h6",
            "tr",
            "br",
        }:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"style", "script", "template"} and self.suppressed:
            self.suppressed -= 1
        elif not self.suppressed and tag in {
            "p",
            "div",
            "section",
            "h1",
            "h2",
            "h3",
            "h4",
            "h5",
            "h6",
            "tr",
        }:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.suppressed:
            self.parts.append(data)

    @property
    def text(self) -> str:
        return "\n".join(line.strip() for line in "".join(self.parts).splitlines() if line.strip())


def _docx_text(path: Path) -> str:
    try:
        with zipfile.ZipFile(path) as archive:
            root = ElementTree.fromstring(archive.read("word/document.xml"))
    except (OSError, KeyError, zipfile.BadZipFile, ElementTree.ParseError) as exc:
        raise ValueError(f"not a readable DOCX artifact: {path}") from exc
    namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    math_namespace = "{http://schemas.openxmlformats.org/officeDocument/2006/math}"
    paragraphs: list[str] = []
    for paragraph in root.iter(namespace + "p"):
        text = "".join(
            node.text or ""
            for node in paragraph.iter()
            if node.tag in {namespace + "t", math_namespace + "t"}
        )
        if text:
            paragraphs.append(text)
    return "\n".join(paragraphs)


def _markdown_text(path: Path) -> str:
    """Return rendered Markdown text rather than syntax/control markers."""

    from docreconstruct.reconstruction.markdown_content import (
        MarkdownBlockKind,
        parse_markdown_content,
    )
    from docreconstruct.reconstruction.math_omml import latex_visible_text

    def inline_projection(value: str) -> str:
        value = re.sub(r"<eq>(.*?)</eq>", r"$\1$", value)
        return re.sub(
            r"\$([^$]+)\$",
            lambda match: latex_visible_text(match.group(1)),
            value,
        )

    content = parse_markdown_content(path)
    parts: list[str] = []
    for block in content.blocks:
        if block.kind is MarkdownBlockKind.TABLE:
            parts.extend(" ".join(cell for cell in row if cell) for row in block.table_rows)
        elif block.kind is MarkdownBlockKind.EQUATION:
            parts.append(latex_visible_text(block.text))
        elif block.kind is not MarkdownBlockKind.IMAGE and block.text:
            parts.append(inline_projection(block.text))
    return "\n".join(parts)


def _visible_text_stream(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    return re.sub(r"[\s\ufeff\u200b]+", " ", value).strip()


def _pdf_text(path: Path) -> str:
    try:
        import pymupdf as fitz
    except (ImportError, ModuleNotFoundError) as exc:
        raise ImportError(
            "PDF artifact comparison requires PyMuPDF. Install "
            "`docreconstruct[pdf]` or provide analyzed Document IR JSON."
        ) from exc
    with fitz.open(path) as document:
        return "\f".join(page.get_text("text", sort=True) for page in document)


def _pdf_images(path: Path) -> list[bytes]:
    try:
        import pymupdf as fitz
    except (ImportError, ModuleNotFoundError) as exc:
        raise ImportError("PDF visual comparison requires PyMuPDF") from exc
    images: list[bytes] = []
    with fitz.open(path) as document:
        for page in document:
            images.append(page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False).tobytes("png"))
    return images


def _docx_images(
    path: Path,
    *,
    backend: str,
    executable: str | Path | None,
) -> DocumentRenderResult:
    """Render a DOCX only through a backend explicitly selected by the caller."""

    return render_docx_pages(path, backend=backend, executable=executable)


@dataclass(frozen=True)
class _Coerced:
    value: Any
    original: Any
    kind: str


def _coerce(source: Any) -> _Coerced:
    if not isinstance(source, (str, Path)):
        return _Coerced(source, source, "document" if _has_pages(source) else "text")
    if isinstance(source, str) and ("\n" in source or "\r" in source or len(source) > 240):
        return _Coerced(source, source, "text")
    path = Path(source)
    try:
        is_file = path.is_file()
    except OSError:
        is_file = False
    if not is_file:
        return _Coerced(source, source, "text")
    suffix = path.suffix.lower()
    if suffix == ".json":
        payload = json.loads(path.read_text(encoding="utf-8"))
        return _Coerced(payload, path, "document" if _has_pages(payload) else "text")
    if suffix == ".txt":
        return _Coerced(path.read_text(encoding="utf-8"), path, "text")
    if suffix in {".md", ".markdown"}:
        return _Coerced(_markdown_text(path), path, "markdown")
    if suffix in {".html", ".htm"}:
        parser = _TextHTMLParser()
        parser.feed(path.read_text(encoding="utf-8"))
        return _Coerced(parser.text, path, "html")
    if suffix == ".docx":
        return _Coerced(_docx_text(path), path, "docx")
    if suffix == ".pdf":
        return _Coerced(_pdf_text(path), path, "pdf")
    if suffix in _RASTER_SUFFIXES:
        return _Coerced(path, path, "image")
    # Treat unknown readable files as UTF-8 text only when that is actually
    # possible; binary formats receive a precise unsupported-format error.
    try:
        return _Coerced(path.read_text(encoding="utf-8"), path, "text")
    except UnicodeDecodeError as exc:
        raise ValueError(f"unsupported evaluation artifact format: {suffix or '<none>'}") from exc


@dataclass(frozen=True)
class EvaluationReport:
    text: TextMetrics | None
    layout: LayoutMetrics | None
    structure: StructureMetrics | None
    editability: EditabilityMetrics | None
    visual: VisualMetrics | None
    fidelity: FidelityScore
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def score(self) -> float:
        return self.fidelity.overall

    @property
    def component_statuses(self) -> dict[str, str]:
        return {
            name: str(
                MeasurementStatus.MEASURED
                if getattr(self, name) is not None
                else MeasurementStatus.NOT_MEASURED
            )
            for name in ("text", "layout", "structure", "editability", "visual")
        }

    @property
    def accepted(self) -> bool:
        """Conservative gate for reports without caller-supplied thresholds.

        A report with missing required measurements can never pass.  In this
        threshold-free public API, only a completely measured exact result is
        accepted; benchmark-specific gates remain responsible for calibrated
        non-exact thresholds.
        """

        measured = [score for score in self.fidelity.components.values() if score is not None]
        return (
            self.fidelity.measurement_coverage == 1.0
            and bool(measured)
            and all(score == 1.0 for score in measured)
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": EVALUATION_REPORT_SCHEMA_VERSION,
            "metric_version": EVALUATION_METRIC_VERSION,
            "text": self.text.to_dict() if self.text else None,
            "layout": self.layout.to_dict() if self.layout else None,
            "structure": self.structure.to_dict() if self.structure else None,
            "editability": self.editability.to_dict() if self.editability else None,
            "visual": self.visual.to_dict() if self.visual else None,
            "fidelity": self.fidelity.to_dict(),
            "overall_measured": self.fidelity.overall_measured,
            "overall_strict": self.fidelity.overall_strict,
            "measurement_coverage": self.fidelity.measurement_coverage,
            "component_statuses": self.component_statuses,
            "accepted": self.accepted,
            "metadata": dict(self.metadata),
        }


def _artifact_editability(
    coerced: _Coerced, output_format: str | None
) -> EditabilityMetrics | None:
    if coerced.kind == "document":
        return evaluate_editability(coerced.value, output_format=output_format)
    if coerced.kind == "docx":
        return evaluate_editability(coerced.original, output_format="docx")
    if coerced.kind in {"image", "pdf"}:
        return evaluate_editability(coerced.original, output_format=coerced.kind)
    if coerced.kind in {"text", "markdown", "html"}:
        # The content remains selectable and directly editable, but plain text
        # contains no native structural object beyond its text stream.
        return EditabilityMetrics(1, 0, 1, 1.0, 1.0, 0.0)
    return None


def evaluate(
    reference: Any,
    candidate: Any,
    *,
    profile: str = "balanced",
    reference_images: Any = None,
    candidate_images: Any = None,
    output_format: str | None = None,
    weights: dict[str, float] | None = None,
    render_backend: str = "native",
    renderer_path: str | Path | None = None,
) -> EvaluationReport:
    """Evaluate the dimensions supported by the supplied IR or artifacts.

    ``overall_measured`` preserves the historical renormalized diagnostic
    score. ``overall_strict`` keeps the configured denominator fixed, so a
    missing required dimension cannot inflate a quality gate. DOCX rendering
    is disabled by default; selecting ``auto`` or
    ``libreoffice`` is the caller's explicit authorization to start the
    corresponding external renderer.
    """

    normalized_render_backend = render_backend.strip().casefold()
    if normalized_render_backend not in {"native", "auto", "libreoffice"}:
        raise ValueError("render_backend must be native, auto, or libreoffice")
    if renderer_path is not None and normalized_render_backend == "native":
        raise ValueError("renderer_path requires auto or libreoffice render_backend")

    ref = _coerce(reference)
    cand = _coerce(candidate)
    text_metrics: TextMetrics | None = None
    layout_metrics: LayoutMetrics | None = None
    structure_metrics: StructureMetrics | None = None
    visual_metrics: VisualMetrics | None = None
    visual_mode = "raw-raster"
    render_results: list[DocumentRenderResult] = []

    def render_docx(path: Path) -> list[bytes] | None:
        # Native comparison is intentionally OOXML/text-only.  In particular,
        # it must not even discover an Office executable.
        if normalized_render_backend == "native":
            return None
        result = _docx_images(
            path,
            backend=normalized_render_backend,
            executable=renderer_path,
        )
        render_results.append(result)
        if result.rendered:
            return list(result.pages)
        if normalized_render_backend == "libreoffice" or renderer_path is not None:
            raise RuntimeError(result.diagnostic or "the selected renderer did not produce pages")
        return None

    if ref.kind != "image" and cand.kind != "image":
        if "markdown" in {ref.kind, cand.kind}:
            text_metrics = evaluate_text(
                _visible_text_stream(ref.value),
                _visible_text_stream(cand.value),
            )
        else:
            text_metrics = evaluate_text(ref.value, cand.value)
    if ref.kind == "document" and cand.kind == "document":
        layout_metrics, structure_metrics = evaluate_layout_and_structure(
            ref.value,
            cand.value,
        )

    if reference_images is not None or candidate_images is not None:
        if reference_images is None or candidate_images is None:
            raise ValueError("reference_images and candidate_images must be supplied together")
        visual_metrics = evaluate_visual(reference_images, candidate_images)
    elif ref.kind == "image" and cand.kind == "image":
        visual_metrics = evaluate_visual(ref.original, cand.original)
    elif ref.kind == "pdf" and cand.kind == "pdf":
        visual_metrics = evaluate_visual(_pdf_images(ref.original), _pdf_images(cand.original))
    elif ref.kind == "image" and cand.kind == "docx":
        rendered = render_docx(cand.original)
        if rendered:
            visual_metrics = evaluate_visual(
                [ref.original],
                rendered,
                normalize_illumination=True,
            )
            visual_mode = "foreground-normalized"
    elif ref.kind == "docx" and cand.kind == "image":
        rendered = render_docx(ref.original)
        if rendered:
            visual_metrics = evaluate_visual(
                rendered,
                [cand.original],
                normalize_illumination=True,
            )
            visual_mode = "foreground-normalized"
    elif ref.kind == "pdf" and cand.kind == "docx":
        rendered = render_docx(cand.original)
        if rendered:
            visual_metrics = evaluate_visual(_pdf_images(ref.original), rendered)
    elif ref.kind == "docx" and cand.kind == "pdf":
        rendered = render_docx(ref.original)
        if rendered:
            visual_metrics = evaluate_visual(rendered, _pdf_images(cand.original))
    elif ref.kind == "docx" and cand.kind == "docx":
        rendered_reference = render_docx(ref.original)
        rendered_candidate = render_docx(cand.original)
        if rendered_reference and rendered_candidate:
            visual_metrics = evaluate_visual(rendered_reference, rendered_candidate)

    editability_metrics = _artifact_editability(cand, output_format)
    fidelity = calculate_fidelity(
        text=text_metrics,
        layout=layout_metrics,
        structure=structure_metrics,
        editability=editability_metrics,
        visual=visual_metrics,
        profile=profile,
        weights=weights,
    )
    return EvaluationReport(
        text=text_metrics,
        layout=layout_metrics,
        structure=structure_metrics,
        editability=editability_metrics,
        visual=visual_metrics,
        fidelity=fidelity,
        metadata={
            "reference_kind": ref.kind,
            "candidate_kind": cand.kind,
            "visual_mode": visual_mode if visual_metrics is not None else None,
            "render_backend": normalized_render_backend,
            "renderer_provenance": [result.provenance() for result in render_results],
            "schema_version": EVALUATION_REPORT_SCHEMA_VERSION,
            "metric_version": EVALUATION_METRIC_VERSION,
            "measurement_coverage": fidelity.measurement_coverage,
            "measured_components": [
                name
                for name, metric in (
                    ("text", text_metrics),
                    ("layout", layout_metrics),
                    ("structure", structure_metrics),
                    ("editability", editability_metrics),
                    ("visual", visual_metrics),
                )
                if metric is not None
            ],
        },
    )


compare = evaluate
