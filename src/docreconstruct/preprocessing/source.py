"""Classify inputs before OCR so native PDF information is not discarded."""

from __future__ import annotations

from contextlib import suppress
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from docreconstruct.exceptions import ProviderUnavailableError, UnsupportedInputError


class SourceKind(StrEnum):
    NATIVE = "native"
    SCANNED = "scanned"
    HYBRID = "hybrid"
    STRUCTURED = "structured"


class PageAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page_number: int = Field(ge=1)
    kind: SourceKind
    width: float = Field(gt=0)
    height: float = Field(gt=0)
    rotation: float = 0.0
    dpi_x: float | None = Field(default=None, gt=0)
    dpi_y: float | None = Field(default=None, gt=0)
    orientation: str | None = None
    native_characters: int = Field(default=0, ge=0)
    embedded_images: int = Field(default=0, ge=0)
    image_coverage: float = Field(default=0.0, ge=0.0, le=1.0)


class InputAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str
    media_type: str
    kind: SourceKind
    pages: list[PageAnalysis]
    recommended_provider: str
    warnings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp", ".bmp"}


def _media_type(path: Path) -> str:
    return {
        ".pdf": "application/pdf",
        ".json": "application/json",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".tif": "image/tiff",
        ".tiff": "image/tiff",
        ".webp": "image/webp",
        ".bmp": "image/bmp",
    }.get(path.suffix.lower(), "application/octet-stream")


def _analyze_image(path: Path) -> InputAnalysis:
    try:
        from PIL import Image
    except ImportError as exc:  # pragma: no cover - core dependency in packaged build
        raise ProviderUnavailableError(
            "Image inspection requires Pillow. Install docreconstruct with its core dependencies."
        ) from exc

    with Image.open(path) as image:
        width, height = image.size
        frames = getattr(image, "n_frames", 1)
        raw_dpi = image.info.get("dpi")
        dpi_x = dpi_y = None
        if isinstance(raw_dpi, (list, tuple)) and len(raw_dpi) >= 2:
            try:
                dpi_x, dpi_y = float(raw_dpi[0]), float(raw_dpi[1])
                dpi_x = dpi_x if dpi_x > 0 else None
                dpi_y = dpi_y if dpi_y > 0 else None
            except (TypeError, ValueError):
                dpi_x = dpi_y = None
        exif_orientation: int | None = None
        with suppress(AttributeError, TypeError, ValueError):
            raw_orientation = image.getexif().get(274)
            if isinstance(raw_orientation, int):
                exif_orientation = raw_orientation
        rotation = (
            {3: 180.0, 6: 90.0, 8: 270.0}.get(exif_orientation, 0.0)
            if exif_orientation is not None
            else 0.0
        )
        if exif_orientation in {5, 6, 7, 8}:
            # EXIF 5-8 are quarter turns. ``image.size`` is the stored raster,
            # but preprocessing.image applies exif_transpose before any provider
            # sees the pixels, so the page frame has to be the transposed one —
            # matching how _analyze_pdf reports PyMuPDF's display rect.
            width, height = height, width
            dpi_x, dpi_y = dpi_y, dpi_x
        orientation = "square" if width == height else "landscape" if width > height else "portrait"
        pages = [
            PageAnalysis(
                page_number=number,
                kind=SourceKind.SCANNED,
                width=width,
                height=height,
                rotation=rotation,
                dpi_x=dpi_x,
                dpi_y=dpi_y,
                orientation=orientation,
                embedded_images=1,
                image_coverage=1.0,
            )
            for number in range(1, frames + 1)
        ]
        return InputAnalysis(
            source=str(path),
            media_type=_media_type(path),
            kind=SourceKind.SCANNED,
            pages=pages,
            recommended_provider="paddleocr",
            metadata={"image_mode": image.mode, "format": image.format},
        )


def _analyze_pdf(path: Path) -> InputAnalysis:
    try:
        import pymupdf as fitz
    except ImportError as exc:
        raise ProviderUnavailableError(
            "PDF analysis requires PyMuPDF. Install `docreconstruct[pdf]`."
        ) from exc

    pages: list[PageAnalysis] = []
    with fitz.open(path) as pdf:
        for index, page in enumerate(pdf):
            native_characters = len(page.get_text("text").strip())
            page_area = max(float(page.rect.width * page.rect.height), 1.0)
            coverage = 0.0
            image_count = 0
            for image in page.get_images(full=True):
                image_count += 1
                try:
                    rects = page.get_image_rects(image[0])
                    coverage += sum(float(rect.width * rect.height) for rect in rects) / page_area
                except (RuntimeError, ValueError):
                    continue
            coverage = min(coverage, 1.0)
            if native_characters >= 20 and coverage < 0.75:
                kind = SourceKind.NATIVE
            elif native_characters == 0 and image_count:
                kind = SourceKind.SCANNED
            elif native_characters or image_count:
                kind = SourceKind.HYBRID
            else:
                kind = SourceKind.NATIVE
            pages.append(
                PageAnalysis(
                    page_number=index + 1,
                    kind=kind,
                    width=float(page.rect.width),
                    height=float(page.rect.height),
                    rotation=float(page.rotation or 0),
                    orientation=(
                        "square"
                        if page.rect.width == page.rect.height
                        else "landscape"
                        if page.rect.width > page.rect.height
                        else "portrait"
                    ),
                    native_characters=native_characters,
                    embedded_images=image_count,
                    image_coverage=coverage,
                )
            )

    kinds = {page.kind for page in pages}
    overall = next(iter(kinds)) if len(kinds) == 1 else SourceKind.HYBRID
    recommendation = "native_pdf" if overall is SourceKind.NATIVE else "ensemble"
    warnings = []
    if overall in {SourceKind.SCANNED, SourceKind.HYBRID}:
        warnings.append("Raster regions need an installed OCR provider for text extraction.")
    return InputAnalysis(
        source=str(path),
        media_type="application/pdf",
        kind=overall,
        pages=pages,
        recommended_provider=recommendation,
        warnings=warnings,
    )


def analyze_source(source: str | Path) -> InputAnalysis:
    """Inspect an input without OCR or lossy rendering."""

    path = Path(source).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return _analyze_pdf(path)
    if suffix in _IMAGE_SUFFIXES:
        return _analyze_image(path)
    if suffix == ".json":
        return InputAnalysis(
            source=str(path),
            media_type="application/json",
            kind=SourceKind.STRUCTURED,
            pages=[],
            recommended_provider="json",
        )
    raise UnsupportedInputError(
        f"Unsupported input '{suffix or '<no extension>'}'. "
        "Supported: PDF, JSON, PNG, JPEG, TIFF, WebP, BMP."
    )
