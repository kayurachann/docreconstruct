"""Auditable reconstruction jobs with separate content and layout authorities."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from pathlib import Path

from PIL import Image
from pydantic import BaseModel, ConfigDict, Field

from docreconstruct.exceptions import UnsupportedInputError


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


def reconstruct_hybrid(
    content: str | Path,
    layout: str | Path,
    *,
    evidence: str | Path | Sequence[str | Path] | None = None,
    evidence_provider_hints: (str | Sequence[str | None] | Mapping[str | Path, str] | None) = None,
    strict_evidence: bool = True,
    output: str | Path | None = None,
    allow_remote_assets: bool = True,
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

    from docreconstruct.evidence import (
        SidecarEvidenceError,
        load_sidecar_evidence,
    )
    from docreconstruct.providers import ProviderContext
    from docreconstruct.reconstruction.asset_matching import match_markdown_assets
    from docreconstruct.reconstruction.evidence_matching import match_sidecar_evidence
    from docreconstruct.reconstruction.hybrid_docx import render_hybrid_docx
    from docreconstruct.reconstruction.hybrid_planner import build_hybrid_layout_plan
    from docreconstruct.reconstruction.markdown_content import parse_markdown_content
    from docreconstruct.reconstruction.scan_layout import analyze_scan_source
    from docreconstruct.reconstruction.table_matching import match_markdown_tables

    evidence_sources = (evidence,) if isinstance(evidence, (str, Path)) else tuple(evidence or ())
    manifest = prepare_markdown_layout_sources(content, layout, evidence_sources)
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
    markdown = parse_markdown_content(content_path)
    scan = analyze_scan_source(layout)
    evidence_matches = []
    evidence_summary: HybridEvidenceSummary | None = None
    if evidence_sources:
        context_updates: dict[str, object] = {
            "source": str(Path(layout).expanduser().resolve()),
            "metadata": {"authority": "layout", "offline_sidecar": True},
        }
        if len(scan.pages) == 1:
            context_updates.update(
                {
                    "page_width": float(scan.pages[0].width),
                    "page_height": float(scan.pages[0].height),
                }
            )
        bundle = load_sidecar_evidence(
            evidence_sources,
            provider_hints=evidence_provider_hints,
            context=ProviderContext.model_validate(context_updates),
            strict=strict_evidence,
        )
        if strict_evidence:
            bundle.raise_for_errors()
            ambiguous = [item for item in bundle.items if item.detection.ambiguous]
            if ambiguous:
                paths = ", ".join(str(item.path) for item in ambiguous)
                raise SidecarEvidenceError(
                    "ambiguous OCR sidecar schema in strict mode; pass an explicit "
                    f"--evidence-provider hint for: {paths}"
                )
        evidence_matches = match_sidecar_evidence(markdown, scan, bundle)
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
            inputs=len(evidence_sources),
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
    asset_matches = match_markdown_assets(
        markdown,
        scan,
        allow_remote=allow_remote_assets,
    )
    table_matches = match_markdown_tables(markdown, scan, asset_matches)
    plan = build_hybrid_layout_plan(
        markdown,
        scan,
        asset_matches,
        table_matches,
        evidence_matches=evidence_matches,
    )
    payload = render_hybrid_docx(markdown, scan, plan, asset_matches)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(payload)
    return finalize_hybrid_reconstruction(
        manifest,
        destination,
        evidence_summary=evidence_summary,
    )


__all__ = [
    "HybridEvidenceSummary",
    "HybridReconstructionResult",
    "HybridSourceManifest",
    "SourceFingerprint",
    "finalize_hybrid_reconstruction",
    "prepare_markdown_layout_sources",
    "prepare_markdown_pdf_sources",
    "reconstruct_hybrid",
]
