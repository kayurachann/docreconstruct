"""Auditable reconstruction jobs with separate content and layout authorities."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import TYPE_CHECKING, Any

from PIL import Image
from pydantic import BaseModel, ConfigDict, Field

from docreconstruct.exceptions import UnsupportedInputError

if TYPE_CHECKING:
    from docreconstruct.evidence import SidecarEvidenceBundle
    from docreconstruct.reconstruction.evidence_matching import EvidenceMatch
    from docreconstruct.reconstruction.markdown_content import MarkdownContent
    from docreconstruct.reconstruction.scan_layout import ScanDocumentLayout
else:
    # Keep runtime introspection usable without importing the evidence/provider
    # graph while that graph imports reconstruction modules during startup.
    SidecarEvidenceBundle = Any
    EvidenceMatch = Any
    MarkdownContent = Any
    ScanDocumentLayout = Any


class SourceFingerprint(BaseModel):
    """Immutable identity of one source used by a reconstruction job."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str
    media_type: str
    size: int = Field(ge=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class HybridSourceManifest(BaseModel):
    """Explicit authority split for a deterministic hybrid reconstruction."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    content: SourceFingerprint
    layout: SourceFingerprint
    evidence: list[SourceFingerprint] = Field(default_factory=list)
    content_policy: str = "verbatim_markdown"
    layout_policy: str = "pdf_geometry_and_original_figures_only"
    evidence_policy: str = "geometry_style_confidence_only_never_text_authority"
    external_references: list[str] = Field(default_factory=list)


class HybridEvidenceSummary(BaseModel):
    """Auditable outcome of aligning saved OCR JSON to Markdown blocks."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    inputs: int = Field(ge=0)
    normalized_documents: int = Field(ge=0)
    providers: list[str] = Field(default_factory=list)
    matched_blocks: int = Field(ge=0)
    geometry_matches: int = Field(ge=0)
    conflicts: int = Field(ge=0)
    warnings: list[str] = Field(default_factory=list)


class HybridReconstructionResult(BaseModel):
    """Audit record proving which authorities produced a local artifact."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    manifest: HybridSourceManifest
    output: SourceFingerprint
    evidence_summary: HybridEvidenceSummary | None = None


@dataclass(frozen=True, slots=True)
class HybridPreparedSources:
    """Immutable, in-process source analysis shared by reconstruction and QA.

    The public file-oriented APIs remain unchanged.  A complete hybrid job can
    pass this internal artifact from reconstruction to validation so a scan and
    saved OCR sidecar are not decoded and aligned twice.  It intentionally
    contains no rendered output and is never persisted as a trusted cache.
    """

    manifest: HybridSourceManifest
    content_path: Path
    layout_path: Path
    evidence_paths: tuple[Path, ...]
    strict_evidence: bool
    markdown: MarkdownContent
    scan: ScanDocumentLayout
    evidence_bundle: SidecarEvidenceBundle | None
    evidence_matches: tuple[EvidenceMatch, ...]
    evidence_summary: HybridEvidenceSummary | None


def _record_phase(
    timings: dict[str, float] | None,
    name: str,
    started: float,
) -> None:
    if timings is not None:
        timings[name] = perf_counter() - started


def _fingerprint(path: Path, media_type: str) -> SourceFingerprint:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            size += len(block)
            digest.update(block)
    if size == 0:
        raise ValueError(f"Source is empty: {path}")
    return SourceFingerprint(
        path=str(path),
        media_type=media_type,
        size=size,
        sha256=digest.hexdigest(),
    )


_LAYOUT_MEDIA_TYPES = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
    ".webp": "image/webp",
}

_EVIDENCE_MEDIA_TYPES = {
    ".json": "application/json",
    ".jsonl": "application/x-ndjson",
}


def _evidence_fingerprints(
    evidence: str | Path | Sequence[str | Path] | None,
) -> list[SourceFingerprint]:
    """Validate and fingerprint saved OCR sidecars without interpreting them."""

    fingerprints: list[SourceFingerprint] = []
    seen: set[Path] = set()
    sources = (evidence,) if isinstance(evidence, (str, Path)) else evidence or ()
    for source in sources:
        path = Path(source).expanduser().resolve()
        if path in seen:
            raise ValueError(f"Evidence sidecar was supplied more than once: {path}")
        seen.add(path)
        if not path.is_file():
            raise FileNotFoundError(path)
        media_type = _EVIDENCE_MEDIA_TYPES.get(path.suffix.lower())
        if media_type is None:
            supported = ", ".join(sorted(_EVIDENCE_MEDIA_TYPES))
            raise UnsupportedInputError(
                f"OCR evidence must be saved JSON or JSONL ({supported}): {path}"
            )
        fingerprints.append(_fingerprint(path, media_type))
    return fingerprints


def prepare_markdown_layout_sources(
    markdown: str | Path,
    layout: str | Path,
    evidence: str | Path | Sequence[str | Path] | None = None,
) -> HybridSourceManifest:
    """Validate and fingerprint Markdown, original layout, and saved OCR sidecars.

    URLs found in Markdown are recorded for provenance but are never opened.
    This prevents reconstruction from silently replacing original source
    figures with mutable remote assets.
    """

    markdown_path = Path(markdown).expanduser().resolve()
    layout_path = Path(layout).expanduser().resolve()
    if not markdown_path.is_file():
        raise FileNotFoundError(markdown_path)
    if not layout_path.is_file():
        raise FileNotFoundError(layout_path)
    if markdown_path.suffix.lower() not in {".md", ".markdown"}:
        raise UnsupportedInputError("The content authority must be a Markdown file.")
    layout_suffix = layout_path.suffix.lower()
    media_type = _LAYOUT_MEDIA_TYPES.get(layout_suffix)
    if media_type is None:
        supported = ", ".join(sorted(_LAYOUT_MEDIA_TYPES))
        raise UnsupportedInputError(
            f"The layout authority must be a PDF or raster image ({supported})."
        )

    markdown_text = markdown_path.read_text(encoding="utf-8")
    if not markdown_text.strip():
        raise ValueError(f"Markdown source is empty: {markdown_path}")
    if layout_suffix == ".pdf":
        with layout_path.open("rb") as stream:
            if stream.read(5) != b"%PDF-":
                raise ValueError(f"Layout source is not a valid PDF header: {layout_path}")
    else:
        try:
            with Image.open(layout_path) as image:
                image.verify()
        except (OSError, SyntaxError) as exc:
            raise ValueError(f"Layout source is not a valid raster image: {layout_path}") from exc

    references = sorted(set(re.findall(r"!?\[[^\]]*\]\((https?://[^)]+)\)", markdown_text)))
    return HybridSourceManifest(
        content=_fingerprint(markdown_path, "text/markdown"),
        layout=_fingerprint(layout_path, media_type),
        evidence=_evidence_fingerprints(evidence),
        layout_policy=(
            "pdf_geometry_and_original_figures_only"
            if layout_suffix == ".pdf"
            else "image_geometry_and_original_figures_only"
        ),
        external_references=references,
    )


def prepare_markdown_pdf_sources(
    markdown: str | Path,
    pdf: str | Path,
    evidence: str | Path | Sequence[str | Path] | None = None,
) -> HybridSourceManifest:
    """Backward-compatible alias for :func:`prepare_markdown_layout_sources`."""

    return prepare_markdown_layout_sources(markdown, pdf, evidence)


def finalize_hybrid_reconstruction(
    manifest: HybridSourceManifest,
    output: str | Path,
    *,
    evidence_summary: HybridEvidenceSummary | None = None,
) -> HybridReconstructionResult:
    """Fingerprint an existing result without modifying it."""

    output_path = Path(output).expanduser().resolve()
    if not output_path.is_file():
        raise FileNotFoundError(output_path)
    return HybridReconstructionResult(
        manifest=manifest,
        output=_fingerprint(
            output_path,
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ),
        evidence_summary=evidence_summary,
    )


def prepare_hybrid_sources(
    content: str | Path,
    layout: str | Path,
    *,
    evidence: str | Path | Sequence[str | Path] | None = None,
    evidence_provider_hints: (str | Sequence[str | None] | Mapping[str | Path, str] | None) = None,
    strict_evidence: bool = True,
    _phase_seconds: dict[str, float] | None = None,
) -> HybridPreparedSources:
    """Decode and align all immutable sources once for one hybrid job.

    This is an in-process fast path, not a disk cache: every invocation still
    fingerprints the authorities and validates the saved evidence.  Reusing
    the returned object merely prevents the immediately following native QA
    pass from repeating PDF analysis and OCR-to-Markdown alignment.
    """

    from docreconstruct.evidence import SidecarEvidenceError, load_sidecar_evidence
    from docreconstruct.providers import ProviderContext
    from docreconstruct.reconstruction.evidence_matching import match_sidecar_evidence
    from docreconstruct.reconstruction.markdown_content import parse_markdown_content
    from docreconstruct.reconstruction.scan_layout import analyze_scan_source

    evidence_sources = (evidence,) if isinstance(evidence, (str, Path)) else tuple(evidence or ())
    evidence_paths = tuple(Path(source).expanduser().resolve() for source in evidence_sources)
    content_path = Path(content).expanduser().resolve()
    layout_path = Path(layout).expanduser().resolve()

    started = perf_counter()
    manifest = prepare_markdown_layout_sources(content_path, layout_path, evidence_paths)
    _record_phase(_phase_seconds, "prepare.source_validation", started)

    started = perf_counter()
    markdown = parse_markdown_content(content_path)
    _record_phase(_phase_seconds, "prepare.markdown", started)

    started = perf_counter()
    scan = analyze_scan_source(layout_path)
    _record_phase(_phase_seconds, "prepare.scan", started)

    bundle = None
    evidence_matches: tuple[EvidenceMatch, ...] = ()
    evidence_summary: HybridEvidenceSummary | None = None
    if evidence_paths:
        context_updates: dict[str, object] = {
            "source": str(layout_path),
            "metadata": {"authority": "layout", "offline_sidecar": True},
        }
        if len(scan.pages) == 1:
            context_updates.update(
                {
                    "page_width": float(scan.pages[0].width),
                    "page_height": float(scan.pages[0].height),
                }
            )
        started = perf_counter()
        bundle = load_sidecar_evidence(
            evidence_paths,
            provider_hints=evidence_provider_hints,
            context=ProviderContext.model_validate(context_updates),
            strict=strict_evidence,
        )
        _record_phase(_phase_seconds, "prepare.evidence_load", started)
        if strict_evidence:
            bundle.raise_for_errors()
            ambiguous = [item for item in bundle.items if item.detection.ambiguous]
            if ambiguous:
                paths = ", ".join(str(item.path) for item in ambiguous)
                raise SidecarEvidenceError(
                    "ambiguous OCR sidecar schema in strict mode; pass an explicit "
                    f"--evidence-provider hint for: {paths}"
                )
        started = perf_counter()
        evidence_matches = tuple(match_sidecar_evidence(markdown, scan, bundle))
        _record_phase(_phase_seconds, "prepare.evidence_match", started)
        if strict_evidence and not evidence_matches:
            raise SidecarEvidenceError(
                "saved OCR evidence did not match any Markdown block with safe geometry; "
                "verify that the JSON and original layout belong to the same document"
            )
        summary_warnings = [*bundle.warnings, *bundle.errors]
        summary_warnings.extend(
            f"{match.block_id}: {warning}"
            for match in evidence_matches
            for warning in match.warnings
        )
        evidence_summary = HybridEvidenceSummary(
            inputs=len(evidence_paths),
            normalized_documents=len(bundle.documents),
            providers=sorted(
                {provider for match in evidence_matches for provider in match.providers}
                or {item.provider for item in bundle.items if item.provider is not None}
            ),
            matched_blocks=len({match.block_id for match in evidence_matches}),
            geometry_matches=sum(match.source_bbox is not None for match in evidence_matches),
            conflicts=sum(match.conflict for match in evidence_matches),
            warnings=list(dict.fromkeys(summary_warnings)),
        )
    elif _phase_seconds is not None:
        _phase_seconds["prepare.evidence_load"] = 0.0
        _phase_seconds["prepare.evidence_match"] = 0.0

    return HybridPreparedSources(
        manifest=manifest,
        content_path=content_path,
        layout_path=layout_path,
        evidence_paths=evidence_paths,
        strict_evidence=strict_evidence,
        markdown=markdown,
        scan=scan,
        evidence_bundle=bundle,
        evidence_matches=evidence_matches,
        evidence_summary=evidence_summary,
    )


def assert_prepared_hybrid_sources(
    prepared: HybridPreparedSources,
    content: str | Path,
    layout: str | Path,
    evidence: str | Path | Sequence[str | Path] | None,
    *,
    strict_evidence: bool,
) -> None:
    """Reject accidental reuse across different authorities or policies."""

    evidence_sources = (evidence,) if isinstance(evidence, (str, Path)) else tuple(evidence or ())
    evidence_paths = tuple(Path(source).expanduser().resolve() for source in evidence_sources)
    if prepared.content_path != Path(content).expanduser().resolve():
        raise ValueError("prepared hybrid content authority does not match this call")
    if prepared.layout_path != Path(layout).expanduser().resolve():
        raise ValueError("prepared hybrid layout authority does not match this call")
    if prepared.evidence_paths != evidence_paths:
        raise ValueError("prepared hybrid evidence authorities do not match this call")
    if prepared.strict_evidence != strict_evidence:
        raise ValueError("prepared hybrid strict-evidence policy does not match this call")

    expected_sources = (
        (prepared.content_path, prepared.manifest.content),
        (prepared.layout_path, prepared.manifest.layout),
        *zip(prepared.evidence_paths, prepared.manifest.evidence, strict=True),
    )
    for path, expected in expected_sources:
        current = _fingerprint(path, expected.media_type)
        if current.size != expected.size or current.sha256 != expected.sha256:
            raise ValueError(f"hybrid source changed after in-process preparation: {path}")


def reconstruct_hybrid(
    content: str | Path,
    layout: str | Path,
    *,
    evidence: str | Path | Sequence[str | Path] | None = None,
    evidence_provider_hints: (str | Sequence[str | None] | Mapping[str | Path, str] | None) = None,
    strict_evidence: bool = True,
    output: str | Path | None = None,
    allow_remote_assets: bool = True,
    _prepared_sources: HybridPreparedSources | None = None,
    _phase_seconds: dict[str, float] | None = None,
) -> HybridReconstructionResult:
    """Run the generic Markdown-content/scan-layout reconstruction pipeline.

    The destination extension selects the renderer.  DOCX is the default and
    is currently the high-fidelity editable renderer for paginated documents.
    Repeatable saved OCR JSON contributes normalized geometry, style,
    confidence, and provenance only; Markdown remains the exact content
    authority and the original PDF/image remains the pixel/layout authority.
    No page image is used as the document body: Markdown text and tables remain
    native objects, while matched source figures remain raster assets.
    """

    from docreconstruct.reconstruction.asset_matching import match_markdown_assets
    from docreconstruct.reconstruction.hybrid_docx import render_hybrid_docx
    from docreconstruct.reconstruction.hybrid_planner import build_hybrid_layout_plan
    from docreconstruct.reconstruction.table_matching import match_markdown_tables

    evidence_sources = (evidence,) if isinstance(evidence, (str, Path)) else tuple(evidence or ())
    prepared = _prepared_sources
    if prepared is None:
        prepared = prepare_hybrid_sources(
            content,
            layout,
            evidence=evidence_sources,
            evidence_provider_hints=evidence_provider_hints,
            strict_evidence=strict_evidence,
            _phase_seconds=_phase_seconds,
        )
    else:
        assert_prepared_hybrid_sources(
            prepared,
            content,
            layout,
            evidence_sources,
            strict_evidence=strict_evidence,
        )
    manifest = prepared.manifest
    content_path = Path(content).expanduser().resolve()
    destination = (
        Path(output).expanduser().resolve()
        if output is not None
        else content_path.with_name(f"{content_path.stem}.reconstructed.docx")
    )
    selected = destination.suffix.lower()
    if selected != ".docx":
        raise UnsupportedInputError(
            f"Hybrid renderer {selected or '<no extension>'!r} is not installed; "
            "choose an output ending in .docx."
        )
    markdown = prepared.markdown
    scan = prepared.scan
    evidence_matches = prepared.evidence_matches

    started = perf_counter()
    asset_matches = match_markdown_assets(
        markdown,
        scan,
        allow_remote=allow_remote_assets,
    )
    _record_phase(_phase_seconds, "reconstruct.asset_match", started)

    started = perf_counter()
    table_matches = match_markdown_tables(markdown, scan, asset_matches)
    _record_phase(_phase_seconds, "reconstruct.table_match", started)

    started = perf_counter()
    plan = build_hybrid_layout_plan(
        markdown,
        scan,
        asset_matches,
        table_matches,
        evidence_matches=evidence_matches,
    )
    _record_phase(_phase_seconds, "reconstruct.layout_plan", started)

    started = perf_counter()
    payload = render_hybrid_docx(markdown, scan, plan, asset_matches)
    _record_phase(_phase_seconds, "reconstruct.docx_render", started)

    started = perf_counter()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(payload)
    _record_phase(_phase_seconds, "reconstruct.docx_write", started)

    started = perf_counter()
    result = finalize_hybrid_reconstruction(
        manifest,
        destination,
        evidence_summary=prepared.evidence_summary,
    )
    _record_phase(_phase_seconds, "reconstruct.output_fingerprint", started)
    return result


__all__ = [
    "HybridEvidenceSummary",
    "HybridPreparedSources",
    "HybridReconstructionResult",
    "HybridSourceManifest",
    "SourceFingerprint",
    "assert_prepared_hybrid_sources",
    "finalize_hybrid_reconstruction",
    "prepare_hybrid_sources",
    "prepare_markdown_layout_sources",
    "prepare_markdown_pdf_sources",
    "reconstruct_hybrid",
]
