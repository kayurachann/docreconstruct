"""Provider-independent geometry analysis for raster and image-only PDF pages.

The analyzer deliberately does not recognize or rewrite text.  It recovers
page geometry, text-line rhythm, tables, and non-text visual regions from ink
distribution only.  This makes it suitable for hybrid jobs where another
source (for example Markdown) is the content authority.
"""

from __future__ import annotations

import io
import math
import os
from collections import deque
from concurrent.futures import ProcessPoolExecutor
from enum import StrEnum
from pathlib import Path
from typing import Any

from PIL import Image, ImageFilter
from pydantic import BaseModel, ConfigDict, Field, model_validator

from docreconstruct.exceptions import ProviderUnavailableError
from docreconstruct.ir import BBox

_DEFAULT_MAX_PAGE_WORKERS = 4
_ABSOLUTE_MAX_PAGE_WORKERS = 8


class ScanRegionKind(StrEnum):
    """Geometric region classes inferred without OCR."""

    FIGURE = "figure"
    TABLE = "table"
    MIXED = "mixed"


class PixelBox(BaseModel):
    """Integer bounding box in page-raster coordinates."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    x0: int = Field(ge=0)
    y0: int = Field(ge=0)
    x1: int = Field(gt=0)
    y1: int = Field(gt=0)

    @property
    def width(self) -> int:
        return self.x1 - self.x0

    @property
    def height(self) -> int:
        return self.y1 - self.y0

    @property
    def area(self) -> int:
        return self.width * self.height


class SourceToScanBand(BaseModel):
    """One serializable band of a photographed-page row-mesh transform.

    PIL consumes the corresponding mesh in destination-to-source form.  These
    values retain the same four source edges and destination row interval in a
    compact form that can also be evaluated in the forward direction.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_y0: float
    source_y1: float
    source_left0: float
    source_right0: float
    source_left1: float
    source_right1: float
    target_y0: int = Field(ge=0)
    target_y1: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_band(self) -> SourceToScanBand:
        values = (
            self.source_y0,
            self.source_y1,
            self.source_left0,
            self.source_right0,
            self.source_left1,
            self.source_right1,
        )
        if not all(math.isfinite(value) for value in values):
            raise ValueError("source-to-scan band coordinates must be finite")
        if self.source_y1 <= self.source_y0 or self.target_y1 <= self.target_y0:
            raise ValueError("source-to-scan band rows must be strictly ordered")
        if self.source_right0 <= self.source_left0 or self.source_right1 <= self.source_left1:
            raise ValueError("source-to-scan band edges must be strictly ordered")
        return self


class SourceToScanMap(BaseModel):
    """Compact source-photo to rectified-scan coordinate mapping."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    method: str = "neutral-paper-row-mesh"
    source_width: int = Field(gt=0)
    source_height: int = Field(gt=0)
    target_width: int = Field(gt=0)
    target_height: int = Field(gt=0)
    bands: list[SourceToScanBand] = Field(min_length=1)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_mapping(self) -> SourceToScanMap:
        previous_source_y = float("-inf")
        previous_target_y = -1
        for band in self.bands:
            if band.source_y0 < previous_source_y or band.target_y0 < previous_target_y:
                raise ValueError("source-to-scan bands must be ordered")
            if (
                band.source_y0 < 0
                or band.source_y1 > self.source_height
                or min(band.source_left0, band.source_left1) < 0
                or max(band.source_right0, band.source_right1) > self.source_width
                or band.target_y1 > self.target_height
            ):
                raise ValueError("source-to-scan band lies outside its declared dimensions")
            previous_source_y = band.source_y1
            previous_target_y = band.target_y1
        return self


class ScanTextLine(BaseModel):
    """One OCR-free text-line hypothesis and its horizontal ink segments."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    bbox: PixelBox
    segments: list[PixelBox] = Field(default_factory=list)
    ink_density: float = Field(ge=0.0, le=1.0)


class ScanRegion(BaseModel):
    """One table/figure candidate retained from the source scan."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: ScanRegionKind
    bbox: PixelBox
    confidence: float = Field(ge=0.0, le=1.0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ScanPageLayout(BaseModel):
    """Geometry evidence recovered from one scan page raster."""

    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)

    number: int = Field(ge=1)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    pdf_width: float = Field(gt=0)
    pdf_height: float = Field(gt=0)
    content_bbox: PixelBox
    line_pitch: float = Field(gt=0)
    line_bands: list[tuple[int, int]] = Field(default_factory=list)
    text_lines: list[ScanTextLine] = Field(default_factory=list)
    regions: list[ScanRegion] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    image: Image.Image = Field(exclude=True)


class ScanDocumentLayout(BaseModel):
    """Complete raster-backed layout authority."""

    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)

    source: str
    pages: list[ScanPageLayout]


_COORDINATE_ASPECT_TOLERANCE = 0.06
_COORDINATE_EXACT_SIZE_TOLERANCE = 0.02


def _aspect_error(
    width: float,
    height: float,
    reference_width: float,
    reference_height: float,
) -> float:
    return abs(math.log((width / height) / (reference_width / reference_height)))


def _size_matches(
    width: float,
    height: float,
    reference_width: float,
    reference_height: float,
) -> bool:
    return (
        abs(width - reference_width) / reference_width <= _COORDINATE_EXACT_SIZE_TOLERANCE
        and abs(height - reference_height) / reference_height <= _COORDINATE_EXACT_SIZE_TOLERANCE
    )


def _float_box_to_pixels(
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    *,
    width: int,
    height: int,
) -> PixelBox | None:
    if not all(math.isfinite(value) for value in (x0, y0, x1, y1)):
        return None
    clipped_x0 = max(0.0, min(float(width), x0))
    clipped_y0 = max(0.0, min(float(height), y0))
    clipped_x1 = max(0.0, min(float(width), x1))
    clipped_y1 = max(0.0, min(float(height), y1))
    if clipped_x1 <= clipped_x0 or clipped_y1 <= clipped_y0:
        return None
    pixel_x0 = max(0, min(width - 1, math.floor(clipped_x0)))
    pixel_y0 = max(0, min(height - 1, math.floor(clipped_y0)))
    pixel_x1 = max(pixel_x0 + 1, min(width, math.ceil(clipped_x1)))
    pixel_y1 = max(pixel_y0 + 1, min(height, math.ceil(clipped_y1)))
    return PixelBox(x0=pixel_x0, y0=pixel_y0, x1=pixel_x1, y1=pixel_y1)


def _project_flat_box(
    page: ScanPageLayout,
    bbox: BBox | PixelBox,
    *,
    source_width: float,
    source_height: float,
) -> PixelBox | None:
    source_x0 = max(0.0, min(source_width, float(bbox.x0)))
    source_y0 = max(0.0, min(source_height, float(bbox.y0)))
    source_x1 = max(0.0, min(source_width, float(bbox.x1)))
    source_y1 = max(0.0, min(source_height, float(bbox.y1)))
    if source_x1 <= source_x0 or source_y1 <= source_y0:
        return None
    return _float_box_to_pixels(
        source_x0 / source_width * page.width,
        source_y0 / source_height * page.height,
        source_x1 / source_width * page.width,
        source_y1 / source_height * page.height,
        width=page.width,
        height=page.height,
    )


def _covered_length(intervals: list[tuple[float, float]]) -> float:
    if not intervals:
        return 0.0
    merged: list[tuple[float, float]] = []
    for start, end in sorted(intervals):
        if not merged or start > merged[-1][1] + 1e-6:
            merged.append((start, end))
        else:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
    return sum(end - start for start, end in merged)


def _project_row_mesh_box(
    page: ScanPageLayout,
    bbox: BBox | PixelBox,
    mapping: SourceToScanMap,
    *,
    source_width: float,
    source_height: float,
) -> PixelBox | None:
    scale_x = mapping.source_width / source_width
    scale_y = mapping.source_height / source_height
    source_x0 = float(bbox.x0) * scale_x
    source_y0 = float(bbox.y0) * scale_y
    source_x1 = float(bbox.x1) * scale_x
    source_y1 = float(bbox.y1) * scale_y
    if source_x1 <= source_x0 or source_y1 <= source_y0:
        return None

    mapping_y0 = min(band.source_y0 for band in mapping.bands)
    mapping_y1 = max(band.source_y1 for band in mapping.bands)
    clipped_y0 = max(source_y0, mapping_y0)
    clipped_y1 = min(source_y1, mapping_y1)
    if clipped_y1 <= clipped_y0:
        return None

    intervals = [
        (max(clipped_y0, band.source_y0), min(clipped_y1, band.source_y1))
        for band in mapping.bands
        if min(clipped_y1, band.source_y1) > max(clipped_y0, band.source_y0)
    ]
    # A missing destination band is left white by PIL.  Interpolating through
    # such a gap would manufacture geometry not represented by the scan.
    if _covered_length(intervals) < (clipped_y1 - clipped_y0) * 0.98:
        return None

    projected: list[tuple[float, float]] = []
    for band in mapping.bands:
        overlap_y0 = max(clipped_y0, band.source_y0)
        overlap_y1 = min(clipped_y1, band.source_y1)
        if overlap_y1 <= overlap_y0:
            continue
        for source_y in (overlap_y0, overlap_y1):
            fraction = (source_y - band.source_y0) / (band.source_y1 - band.source_y0)
            left = band.source_left0 + (band.source_left1 - band.source_left0) * fraction
            right = band.source_right0 + (band.source_right1 - band.source_right0) * fraction
            clipped_x0 = max(source_x0, left)
            clipped_x1 = min(source_x1, right)
            if clipped_x1 <= clipped_x0:
                continue
            target_y = band.target_y0 + (band.target_y1 - band.target_y0) * fraction
            projected.extend(
                (
                    ((clipped_x0 - left) / (right - left) * mapping.target_width, target_y),
                    ((clipped_x1 - left) / (right - left) * mapping.target_width, target_y),
                )
            )
    if not projected:
        return None
    # The stored mapping describes the actual rectified raster.  Scale once
    # more only when a caller reconstructed the page model at another DPI.
    target_scale_x = page.width / mapping.target_width
    target_scale_y = page.height / mapping.target_height
    return _float_box_to_pixels(
        min(point[0] for point in projected) * target_scale_x,
        min(point[1] for point in projected) * target_scale_y,
        max(point[0] for point in projected) * target_scale_x,
        max(point[1] for point in projected) * target_scale_y,
        width=page.width,
        height=page.height,
    )


def project_source_box_to_scan(
    page: ScanPageLayout,
    bbox: BBox | PixelBox,
    source_width: float,
    source_height: float,
) -> PixelBox | None:
    """Project a provider/source-page box into this scan page's pixel grid.

    Flat scans and PDF coordinate systems are scaled after validating their
    aspect ratio.  Rectified photographs additionally retain a compact forward
    row-mesh mapping.  ``None`` is returned when dimensions are incompatible or
    a box crosses an unrepresented mesh gap; callers can then safely fall back
    to page-only evidence instead of guessing a position.
    """

    source_width = float(source_width)
    source_height = float(source_height)
    if (
        not math.isfinite(source_width)
        or not math.isfinite(source_height)
        or source_width <= 0
        or source_height <= 0
        or float(bbox.x1) <= float(bbox.x0)
        or float(bbox.y1) <= float(bbox.y0)
    ):
        return None

    raw_mapping = page.metadata.get("source_to_scan_map")
    try:
        mapping = SourceToScanMap.model_validate(raw_mapping) if raw_mapping is not None else None
    except (TypeError, ValueError):
        return None

    # Several providers deliberately retain normalized [0, 1] coordinates.
    # Their unit-square dimensions carry semantics, not the page's aspect.
    normalized = abs(source_width - 1.0) <= 1e-6 and abs(source_height - 1.0) <= 1e-6
    if mapping is None or normalized:
        if (
            not normalized
            and _aspect_error(
                source_width,
                source_height,
                page.width,
                page.height,
            )
            > _COORDINATE_ASPECT_TOLERANCE
        ):
            return None
        return _project_flat_box(
            page,
            bbox,
            source_width=source_width,
            source_height=source_height,
        )

    target_exact = _size_matches(
        source_width,
        source_height,
        mapping.target_width,
        mapping.target_height,
    )
    source_exact = _size_matches(
        source_width,
        source_height,
        mapping.source_width,
        mapping.source_height,
    )
    if target_exact and not source_exact:
        return _project_flat_box(
            page,
            bbox,
            source_width=source_width,
            source_height=source_height,
        )
    if source_exact and not target_exact:
        return _project_row_mesh_box(
            page,
            bbox,
            mapping,
            source_width=source_width,
            source_height=source_height,
        )

    source_error = _aspect_error(
        source_width,
        source_height,
        mapping.source_width,
        mapping.source_height,
    )
    target_error = _aspect_error(
        source_width,
        source_height,
        mapping.target_width,
        mapping.target_height,
    )
    if min(source_error, target_error) > _COORDINATE_ASPECT_TOLERANCE:
        return None
    if abs(source_error - target_error) < 0.01:
        # Same-aspect source and target dimensions are genuinely ambiguous
        # without an explicit provider coordinate-space declaration.
        return None
    if source_error < target_error:
        return _project_row_mesh_box(
            page,
            bbox,
            mapping,
            source_width=source_width,
            source_height=source_height,
        )
    return _project_flat_box(
        page,
        bbox,
        source_width=source_width,
        source_height=source_height,
    )


def _require_numpy() -> Any:
    try:
        import numpy as np
    except ImportError as exc:  # pragma: no cover - depends on optional install
        raise ProviderUnavailableError(
            "Hybrid scan analysis requires NumPy. Install `docreconstruct[hybrid]`."
        ) from exc
    return np


def _runs(values: Any) -> list[tuple[int, int]]:
    """Return inclusive-exclusive runs of truthy values."""

    indices = [int(item) for item in _require_numpy().flatnonzero(values)]
    if not indices:
        return []
    result: list[tuple[int, int]] = []
    start = previous = indices[0]
    for index in indices[1:]:
        if index != previous + 1:
            result.append((start, previous + 1))
            start = index
        previous = index
    result.append((start, previous + 1))
    return result


def _merge_runs(runs: list[tuple[int, int]], gap: int) -> list[tuple[int, int]]:
    if not runs:
        return []
    merged = [runs[0]]
    for start, end in runs[1:]:
        previous_start, previous_end = merged[-1]
        if start - previous_end <= gap:
            merged[-1] = (previous_start, max(previous_end, end))
        else:
            merged.append((start, end))
    return merged


def _estimate_line_pitch(ink: Any, content: PixelBox) -> tuple[float, list[tuple[int, int]]]:
    np = _require_numpy()
    cropped = ink[content.y0 : content.y1, content.x0 : content.x1]
    row_ink = np.count_nonzero(cropped, axis=1)
    threshold = max(3, int(content.width * 0.002))
    bands = _runs(row_ink >= threshold)
    useful = [band for band in bands if 3 <= band[1] - band[0] <= 45]
    centers = [(start + end) / 2 for start, end in useful]
    gaps = [right - left for left, right in zip(centers, centers[1:], strict=False)]
    plausible = [gap for gap in gaps if 16 <= gap <= 60]
    pitch = float(np.median(plausible)) if plausible else max(18.0, content.height / 55.0)
    return pitch, [(start + content.y0, end + content.y0) for start, end in useful]


def _detect_text_lines(
    ink: Any,
    content: PixelBox,
    initial_pitch: float,
) -> tuple[list[ScanTextLine], float]:
    """Recover stable line boxes while rejecting photograph/background noise.

    The former two-pixel row threshold was intentionally permissive for clean
    scans, but it joined most rows of a photographed page into one noisy band.
    Requiring a small fraction of the usable width preserves short headings and
    form labels while separating dense body lines.
    """

    np = _require_numpy()
    cropped = ink[content.y0 : content.y1, content.x0 : content.x1]
    row_counts = np.count_nonzero(cropped, axis=1)
    row_threshold = max(6, round(content.width * 0.011))
    broad_bands = _merge_runs(_runs(row_counts >= row_threshold), gap=2)
    maximum_height = max(14, round(initial_pitch * 1.72))
    bands: list[tuple[int, int]] = []
    for broad_top, broad_bottom in broad_bands:
        if broad_bottom - broad_top <= maximum_height:
            bands.append((broad_top, broad_bottom))
            continue
        # Dense italic/large-font pages can have no completely empty raster row
        # between baselines.  Split those long active bands at the darkest-row
        # projection minima near the already estimated baseline rhythm.
        period = max(12, round(initial_pitch))
        boundaries = [broad_top]
        target = broad_top + period
        radius = max(2, round(period * 0.23))
        while target < broad_bottom - period * 0.45:
            search_top = max(boundaries[-1] + max(4, period // 2), target - radius)
            search_bottom = min(broad_bottom - 2, target + radius + 1)
            if search_bottom <= search_top:
                break
            boundary = search_top + int(np.argmin(row_counts[search_top:search_bottom]))
            boundaries.append(boundary)
            target = boundary + period
        boundaries.append(broad_bottom)
        for top, bottom in zip(boundaries, boundaries[1:], strict=False):
            if bottom - top >= 2:
                bands.append((top, bottom))
    lines: list[ScanTextLine] = []
    for relative_top, relative_bottom in bands:
        height = relative_bottom - relative_top
        if height < 2 or height > maximum_height:
            continue
        top = max(content.y0, content.y0 + relative_top - 1)
        bottom = min(content.y1, content.y0 + relative_bottom + 1)
        band = ink[top:bottom, content.x0 : content.x1]
        column_counts = np.count_nonzero(band, axis=0)
        column_threshold = max(1, round((bottom - top) * 0.055))
        raw_segments = _runs(column_counts >= column_threshold)
        if not raw_segments:
            continue
        # Merge characters and ordinary word spaces, but retain the materially
        # wider gaps that distinguish side-by-side masthead zones.
        segments = _merge_runs(raw_segments, gap=max(3, round(content.width * 0.008)))
        segment_boxes = [
            PixelBox(
                x0=content.x0 + start,
                y0=top,
                x1=content.x0 + end,
                y1=bottom,
            )
            for start, end in segments
            if end - start >= 3
        ]
        if not segment_boxes:
            continue
        x0 = min(box.x0 for box in segment_boxes)
        x1 = max(box.x1 for box in segment_boxes)
        bbox = PixelBox(x0=x0, y0=top, x1=x1, y1=bottom)
        density = float(np.count_nonzero(ink[top:bottom, x0:x1]) / max(1, bbox.area))
        lines.append(
            ScanTextLine(
                bbox=bbox,
                segments=segment_boxes,
                ink_density=min(1.0, density),
            )
        )

    centers = [(line.bbox.y0 + line.bbox.y1) / 2 for line in lines]
    gaps = [right - left for left, right in zip(centers, centers[1:], strict=False)]
    plausible = [gap for gap in gaps if 12 <= gap <= 60]
    if plausible:
        # Pick the most populated two-pixel cluster rather than a plain median;
        # headers and paragraph breaks otherwise bias the body rhythm upward.
        cluster = max(
            plausible,
            key=lambda candidate: sum(abs(candidate - other) <= 2.25 for other in plausible),
        )
        neighbors = [gap for gap in plausible if abs(gap - cluster) <= 2.25]
        refined_pitch = float(np.median(neighbors))
    else:
        refined_pitch = initial_pitch
    return lines, refined_pitch


def _detect_header_layout(
    text_lines: list[ScanTextLine],
    content: PixelBox,
    line_pitch: float,
) -> dict[str, Any]:
    """Describe a split masthead above an otherwise single-column page."""

    # Split mastheads occupy the opening band.  A wider quarter-page search
    # mistakes side-by-side multiple-choice answers for header columns on exam
    # sheets, so stop before ordinary body rows begin.
    top_limit = content.y0 + round(content.height * 0.16)
    gap_candidates: list[tuple[float, int, ScanTextLine]] = []
    for line in text_lines:
        if line.bbox.y0 >= top_limit or len(line.segments) < 2:
            continue
        # Ignore narrow rails/shadows that repeatedly touch the photographed
        # canvas edge.  They are not a text column, but previously made the
        # largest-gap heuristic choose a divider deep inside the right title.
        edge_band = content.width * 0.045
        narrow_edge = content.width * 0.04
        ordered = sorted(
            (
                segment
                for segment in line.segments
                if not (
                    (segment.x0 < content.x0 + edge_band or segment.x1 > content.x1 - edge_band)
                    and segment.width < narrow_edge
                )
            ),
            key=lambda box: box.x0,
        )
        if len(ordered) < 2:
            continue
        gaps = [
            (right.x0 - left.x1, (left.x1 + right.x0) / 2, line)
            for left, right in zip(ordered, ordered[1:], strict=False)
        ]
        for gap, midpoint, candidate_line in gaps:
            relative_midpoint = (midpoint - content.x0) / max(1, content.width)
            if gap >= content.width * 0.018 and 0.22 <= relative_midpoint <= 0.78:
                gap_candidates.append((midpoint, gap, candidate_line))
    if len(gap_candidates) < 2:
        return {"header_column_count": 1}

    # A real masthead produces approximately the same gutter on several
    # consecutive rows.  Choose that modal gutter instead of the single widest
    # gap per row: a short subject or document-code line may legitimately leave
    # a much wider empty area inside one column.
    cluster_radius = max(12.0, content.width * 0.045)
    cluster = max(
        gap_candidates,
        key=lambda item: (
            len(
                {
                    candidate[2].bbox.y0
                    for candidate in gap_candidates
                    if abs(candidate[0] - item[0]) <= cluster_radius
                }
            ),
            sum(
                min(candidate[1], round(content.width * 0.16))
                for candidate in gap_candidates
                if abs(candidate[0] - item[0]) <= cluster_radius
            ),
        ),
    )
    candidates = [
        (midpoint, line)
        for midpoint, _gap, line in gap_candidates
        if abs(midpoint - cluster[0]) <= cluster_radius
    ]
    if len({line.bbox.y0 for _, line in candidates}) < 2:
        return {"header_column_count": 1}

    candidate_lines = [line for _, line in candidates]
    outer_left = min(line.bbox.x0 for line in candidate_lines)
    outer_right = max(line.bbox.x1 for line in candidate_lines)
    # A real split masthead normally occupies both outer zones of the page.
    # Centered display mathematics also contains large internal gaps, but it
    # does not reach the left edge and must not be promoted to a two-zone header.
    if (
        outer_left > content.x0 + content.width * 0.20
        or outer_right < content.x1 - content.width * 0.20
    ):
        return {"header_column_count": 1}

    np = _require_numpy()
    divider = float(np.median([item[0] for item in candidates]))
    slots: list[dict[str, int | str]] = []
    last_candidate_bottom = max(line.bbox.y1 for _, line in candidates)
    slot_bottom = min(top_limit, int(last_candidate_bottom + line_pitch * 0.8))
    for line in text_lines:
        if line.bbox.y0 >= slot_bottom:
            continue
        left_segments = [segment for segment in line.segments if segment.x1 <= divider]
        right_segments = [segment for segment in line.segments if segment.x0 >= divider]
        crossing = [segment for segment in line.segments if segment.x0 < divider < segment.x1]
        for side, segments in (("left", left_segments), ("right", right_segments)):
            if not segments:
                continue
            slots.append(
                {
                    "side": side,
                    "x0": min(segment.x0 for segment in segments),
                    "y0": line.bbox.y0,
                    "x1": max(segment.x1 for segment in segments),
                    "y1": line.bbox.y1,
                }
            )
        for segment in crossing:
            center = (segment.x0 + segment.x1) / 2
            side = "left" if center < divider else "right"
            slots.append(
                {
                    "side": side,
                    "x0": segment.x0,
                    "y0": line.bbox.y0,
                    "x1": segment.x1,
                    "y1": line.bbox.y1,
                }
            )
    slots.sort(key=lambda item: (int(item["y0"]), int(item["x0"])))
    return {
        "header_column_count": 2,
        "header_divider": round(divider, 2),
        "header_slots": slots,
        "header_confidence": round(min(0.98, 0.62 + len(candidates) * 0.07), 4),
    }


def _render_content_frame(
    text_lines: list[ScanTextLine],
    content: PixelBox,
) -> PixelBox:
    """Estimate the printable body frame while retaining page furniture.

    ``content`` deliberately includes headers and footers, but photographed
    paper edges and desk shadows can make its horizontal margins nearly zero.
    The dominant body text edges are a more reliable Word text frame.  This
    remains OCR-free: only segment geometry and robust percentiles are used.
    """

    np = _require_numpy()
    top = content.y0 + content.height * 0.18
    bottom = content.y1 - content.height * 0.05
    edge_band = content.width * 0.045
    narrow_edge = content.width * 0.04
    extents: list[tuple[int, int]] = []
    for line in text_lines:
        center = (line.bbox.y0 + line.bbox.y1) / 2
        if not top <= center <= bottom:
            continue
        meaningful = [
            segment
            for segment in line.segments
            if not (
                (segment.x0 < content.x0 + edge_band or segment.x1 > content.x1 - edge_band)
                and segment.width < narrow_edge
            )
        ]
        if meaningful:
            extents.append(
                (
                    min(segment.x0 for segment in meaningful),
                    max(segment.x1 for segment in meaningful),
                )
            )
    if len(extents) < 8:
        return content
    left = int(round(float(np.percentile([item[0] for item in extents], 10))))
    right = int(round(float(np.percentile([item[1] for item in extents], 90))))
    if right - left < content.width * 0.68:
        return content
    return PixelBox(x0=left, y0=content.y0, x1=right, y1=content.y1)


def _periodic_line_pitch(ink: Any, box: PixelBox) -> tuple[float, float] | None:
    """Estimate baseline rhythm from row-projection autocorrelation."""

    np = _require_numpy()
    signal = np.count_nonzero(ink[box.y0 : box.y1, box.x0 : box.x1], axis=1).astype(float)
    if len(signal) < 80 or not signal.any():
        return None
    trend_width = min(101, max(21, len(signal) // 8))
    if trend_width % 2 == 0:
        trend_width += 1
    signal -= np.convolve(signal, np.ones(trend_width) / trend_width, mode="same")
    candidates: list[tuple[float, int]] = []
    for lag in range(12, min(49, len(signal) // 5)):
        left = signal[:-lag]
        right = signal[lag:]
        denominator = float(np.linalg.norm(left) * np.linalg.norm(right))
        if denominator:
            candidates.append((float(np.dot(left, right) / denominator), lag))
    if not candidates:
        return None
    score, lag = max(candidates)
    return float(lag), score


def _persistent_gutter_contrast(
    ink: Any,
    content: PixelBox,
    *,
    search_top: int,
    start: int,
    end: int,
) -> float:
    """Return the body-wide ink contrast of a candidate column gutter.

    Repeated gaps on a subset of lines are not sufficient evidence of a
    newspaper gutter.  Centered equations and other variable-width lines can
    align several such gaps even though adjacent full-width rows cross them.
    A real gutter remains materially clearer than representative strips on
    both sides throughout the body.
    """

    np = _require_numpy()
    if end <= start or search_top >= content.y1:
        return 0.0
    flank_width = max(end - start, round(content.width * 0.05))
    left_start = max(content.x0, start - flank_width)
    right_end = min(content.x1, end + flank_width)
    gutter = ink[search_top : content.y1, start:end]
    left_flank = ink[search_top : content.y1, left_start:start]
    right_flank = ink[search_top : content.y1, end:right_end]
    if not gutter.size or not left_flank.size or not right_flank.size:
        return 0.0
    gutter_density = float(np.mean(gutter))
    flank_density = (float(np.mean(left_flank)) + float(np.mean(right_flank))) / 2.0
    if flank_density <= 0:
        return 0.0
    return max(0.0, 1.0 - gutter_density / flank_density)


def _segment_column_layout(
    ink: Any,
    content: PixelBox,
    text_lines: list[ScanTextLine],
    line_pitch: float,
) -> dict[str, Any] | None:
    """Detect two to four persistent text columns from repeated line gaps."""

    np = _require_numpy()
    search_top = content.y0 + round(content.height * 0.22)
    body_lines = [line for line in text_lines if line.bbox.y0 >= search_top]
    if len(body_lines) < 8:
        return None
    width = content.width
    coverage = np.zeros(width, dtype=int)
    line_gaps: list[list[tuple[int, int]]] = []
    # A persistent newspaper gutter is wider than ordinary inter-word white
    # space.  Keeping the lower bound proportional to the printable width
    # prevents regularly aligned word gaps from masquerading as four columns
    # in dense single-column prose.
    minimum_gap = max(7, round(width * 0.016))
    maximum_gap = max(minimum_gap + 1, round(width * 0.12))
    for line in body_lines:
        ordered = sorted(line.segments, key=lambda box: box.x0)
        gaps: list[tuple[int, int]] = []
        for left, right in zip(ordered, ordered[1:], strict=False):
            start = max(content.x0, left.x1)
            end = min(content.x1, right.x0)
            if not minimum_gap <= end - start <= maximum_gap:
                continue
            relative_start = start - content.x0
            relative_end = end - content.x0
            midpoint = (relative_start + relative_end) / 2
            if not width * 0.10 <= midpoint <= width * 0.90:
                continue
            gaps.append((relative_start, relative_end))
            coverage[relative_start:relative_end] += 1
        line_gaps.append(gaps)
    minimum_support = max(4, round(len(body_lines) * 0.20))
    supported = _merge_runs(_runs(coverage >= minimum_support), gap=max(1, round(width * 0.004)))
    candidates: list[tuple[float, int, int]] = []
    tolerance = max(4, round(width * 0.012))
    for run_start, run_end in supported:
        midpoint = (run_start + run_end) / 2
        related = [
            gap
            for gaps in line_gaps
            for gap in gaps
            if gap[0] - tolerance <= midpoint <= gap[1] + tolerance
        ]
        if len(related) < minimum_support:
            continue
        start = round(float(np.median([gap[0] for gap in related])))
        end = round(float(np.median([gap[1] for gap in related])))
        if end <= start:
            continue
        absolute_start = content.x0 + start
        absolute_end = content.x0 + end
        contrast = _persistent_gutter_contrast(
            ink,
            content,
            search_top=search_top,
            start=absolute_start,
            end=absolute_end,
        )
        # Requiring at least 35% body-wide contrast retains photographic
        # multi-column gutters while rejecting the three aligned gaps created
        # by a one-fragmented/two-full-width row cycle.
        if contrast < 0.35:
            continue
        support = float(coverage[run_start:run_end].max(initial=0))
        candidates.append((support * contrast, start, end))
    selected: list[tuple[float, int, int]] = []
    for candidate in sorted(candidates, reverse=True):
        midpoint = (candidate[1] + candidate[2]) / 2
        if any(abs(midpoint - (other[1] + other[2]) / 2) < width * 0.14 for other in selected):
            continue
        selected.append(candidate)
        if len(selected) == 3:
            break
    selected.sort(key=lambda item: item[1])
    while selected:
        boundaries = [0, *[item for candidate in selected for item in candidate[1:]], width]
        column_widths = [
            boundaries[index + 1] - boundaries[index] for index in range(0, len(boundaries) - 1, 2)
        ]
        if min(column_widths) >= width * 0.14 and max(column_widths) <= width * 0.58:
            break
        selected.remove(min(selected, key=lambda item: item[0]))
        selected.sort(key=lambda item: item[1])
    if not selected:
        return None
    gutters = [[content.x0 + candidate[1], content.x0 + candidate[2]] for candidate in selected]
    x_ranges: list[tuple[int, int]] = []
    cursor = content.x0
    for start, end in gutters:
        x_ranges.append((cursor, start))
        cursor = end
    x_ranges.append((cursor, content.x1))

    def occupies_every_column(line: ScanTextLine) -> bool:
        return all(
            any(
                max(0, min(segment.x1, right) - max(segment.x0, left)) >= 3
                for segment in line.segments
            )
            for left, right in x_ranges
        )

    evidence = [line for line in body_lines if occupies_every_column(line)]
    body_top = search_top
    for index, line in enumerate(evidence):
        nearby = [
            candidate
            for candidate in evidence[index : index + 4]
            if candidate.bbox.y0 - line.bbox.y0 <= line_pitch * 4.5
        ]
        if len(nearby) >= 3:
            body_top = line.bbox.y0
            break
    boxes = [PixelBox(x0=left, y0=body_top, x1=right, y1=content.y1) for left, right in x_ranges]

    def content_bottom(box: PixelBox) -> int:
        row_counts = np.count_nonzero(ink[box.y0 : box.y1, box.x0 : box.x1], axis=1)
        minimum = max(4, round(box.width * 0.02))
        rows = np.flatnonzero(row_counts >= minimum)
        return int(box.y0 + rows[-1] + 1) if len(rows) else box.y1

    periodic = [
        estimate
        for estimate in (_periodic_line_pitch(ink, box) for box in boxes)
        if estimate is not None and estimate[1] >= 0.025
    ]
    support_ratio = min(candidate[0] for candidate in selected) / max(1, len(body_lines))
    return {
        "column_count": len(boxes),
        "column_gutters": gutters,
        "column_gutter": gutters[0],
        "column_boxes": [[box.x0, box.y0, box.x1, box.y1] for box in boxes],
        "column_confidence": float(round(min(0.99, 0.62 + support_ratio * 0.42), 4)),
        "periodic_line_pitch": min((item[0] for item in periodic), default=None),
        "column_content_bottoms": [content_bottom(box) for box in boxes],
    }


def _detect_column_layout(
    ink: Any,
    content: PixelBox,
    text_lines: list[ScanTextLine],
    line_pitch: float,
) -> dict[str, Any]:
    """Detect persistent two-to-four-column body layout without OCR."""

    segmented = _segment_column_layout(ink, content, text_lines, line_pitch)
    if segmented is not None:
        return segmented

    np = _require_numpy()
    projection_top = content.y0 + round(content.height * 0.16)
    cropped = ink[projection_top : content.y1, content.x0 : content.x1]
    if cropped.shape[0] < 80 or cropped.shape[1] < 180:
        return {"column_count": 1}
    projection = np.count_nonzero(cropped, axis=0).astype(float)
    smooth_width = max(7, round(content.width * 0.018))
    if smooth_width % 2 == 0:
        smooth_width += 1
    smoothed = np.convolve(projection, np.ones(smooth_width) / smooth_width, mode="same")
    search_start = round(len(smoothed) * 0.32)
    search_end = round(len(smoothed) * 0.68)
    center = search_start + int(np.argmin(smoothed[search_start:search_end]))
    baseline = float(np.median(smoothed))
    if baseline <= 0 or smoothed[center] >= baseline * 0.42:
        return {"column_count": 1}
    threshold = baseline * 0.36
    gap_start = center
    gap_end = center + 1
    while gap_start > search_start and smoothed[gap_start - 1] <= threshold:
        gap_start -= 1
    while gap_end < search_end and smoothed[gap_end] <= threshold:
        gap_end += 1
    gap_width = gap_end - gap_start
    if not content.width * 0.012 <= gap_width <= content.width * 0.16:
        return {"column_count": 1}
    left_ink = int(np.count_nonzero(cropped[:, :gap_start]))
    right_ink = int(np.count_nonzero(cropped[:, gap_end:]))
    total = left_ink + right_ink
    if total <= 0 or min(left_ink, right_ink) / total < 0.22:
        return {"column_count": 1}
    absolute_gap_start = content.x0 + gap_start
    absolute_gap_end = content.x0 + gap_end
    band_height = max(16, round(content.height * 0.027))
    body_top = projection_top
    for candidate in range(content.y0, content.y1 - band_height, max(2, band_height // 6)):
        band = ink[candidate : candidate + band_height]
        left_density = float(np.mean(band[:, content.x0 : absolute_gap_start]))
        gap_density = float(np.mean(band[:, absolute_gap_start:absolute_gap_end]))
        right_density = float(np.mean(band[:, absolute_gap_end : content.x1]))
        if (
            min(left_density, right_density) >= 0.075
            and gap_density <= min(left_density, right_density) * 0.55
        ):
            body_top = candidate
            break
    left_box = PixelBox(
        x0=content.x0,
        y0=body_top,
        x1=absolute_gap_start,
        y1=content.y1,
    )
    right_box = PixelBox(
        x0=absolute_gap_end,
        y0=body_top,
        x1=content.x1,
        y1=content.y1,
    )
    periodic = [
        estimate
        for estimate in (
            _periodic_line_pitch(ink, left_box),
            _periodic_line_pitch(ink, right_box),
        )
        if estimate is not None and estimate[1] >= 0.025
    ]

    def content_bottom(box: PixelBox) -> int:
        row_counts = np.count_nonzero(ink[box.y0 : box.y1, box.x0 : box.x1], axis=1)
        minimum = max(4, round(box.width * 0.02))
        rows = np.flatnonzero(row_counts >= minimum)
        return int(box.y0 + rows[-1] + 1) if len(rows) else box.y1

    return {
        "column_count": 2,
        "column_gutter": [absolute_gap_start, absolute_gap_end],
        "column_boxes": [
            [left_box.x0, left_box.y0, left_box.x1, left_box.y1],
            [right_box.x0, right_box.y0, right_box.x1, right_box.y1],
        ],
        "column_confidence": float(
            round(min(0.99, 0.58 + (1.0 - smoothed[center] / baseline) * 0.36), 4)
        ),
        "periodic_line_pitch": min((item[0] for item in periodic), default=None),
        "column_content_bottoms": [content_bottom(left_box), content_bottom(right_box)],
    }


def _content_bbox(ink: Any) -> PixelBox:
    np = _require_numpy()
    height, width = ink.shape
    guarded = ink.copy()
    # Broad border suppression rejects scanner rails and photographed desk
    # corners without assuming any page-specific coordinates.
    edge = max(2, int(min(width, height) * 0.026))
    guarded[:edge, :] = False
    guarded[-edge:, :] = False
    guarded[:, :edge] = False
    guarded[:, -edge:] = False
    row_counts = np.count_nonzero(guarded, axis=1)
    column_counts = np.count_nonzero(guarded, axis=0)
    ys = np.flatnonzero(row_counts >= max(3, round(width * 0.008)))
    xs = np.flatnonzero(column_counts >= max(3, round(height * 0.008)))
    if not len(xs) or not len(ys):
        return PixelBox(x0=0, y0=0, x1=width, y1=height)
    pad_x = max(2, int(width * 0.004))
    pad_y = max(2, int(height * 0.003))
    return PixelBox(
        x0=max(0, int(xs.min()) - pad_x),
        y0=max(0, int(ys.min()) - pad_y),
        x1=min(width, int(xs.max()) + pad_x + 1),
        y1=min(height, int(ys.max()) + pad_y + 1),
    )


def _coarse_components(mask: Any) -> list[tuple[int, int, int, int, int]]:
    """Connected components on a small boolean mask without SciPy/OpenCV."""

    np = _require_numpy()
    rows, columns = mask.shape
    visited = np.zeros(mask.shape, dtype=bool)
    result: list[tuple[int, int, int, int, int]] = []
    for row in range(rows):
        for column in range(columns):
            if not mask[row, column] or visited[row, column]:
                continue
            queue: deque[tuple[int, int]] = deque([(row, column)])
            visited[row, column] = True
            min_row = max_row = row
            min_column = max_column = column
            count = 0
            while queue:
                current_row, current_column = queue.popleft()
                count += 1
                min_row = min(min_row, current_row)
                max_row = max(max_row, current_row)
                min_column = min(min_column, current_column)
                max_column = max(max_column, current_column)
                for delta_row, delta_column in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                    next_row = current_row + delta_row
                    next_column = current_column + delta_column
                    if not (0 <= next_row < rows and 0 <= next_column < columns):
                        continue
                    if mask[next_row, next_column] and not visited[next_row, next_column]:
                        visited[next_row, next_column] = True
                        queue.append((next_row, next_column))
            result.append((min_column, min_row, max_column + 1, max_row + 1, count))
    return result


def _box_distance(left: PixelBox, right: PixelBox) -> tuple[int, int]:
    horizontal = max(0, max(left.x0, right.x0) - min(left.x1, right.x1))
    vertical = max(0, max(left.y0, right.y0) - min(left.y1, right.y1))
    return horizontal, vertical


def _union(left: PixelBox, right: PixelBox) -> PixelBox:
    return PixelBox(
        x0=min(left.x0, right.x0),
        y0=min(left.y0, right.y0),
        x1=max(left.x1, right.x1),
        y1=max(left.y1, right.y1),
    )


def _merge_boxes(
    boxes: list[PixelBox],
    *,
    horizontal_gap: int,
    vertical_gap: int,
) -> list[PixelBox]:
    merged = list(boxes)
    changed = True
    while changed:
        changed = False
        output: list[PixelBox] = []
        while merged:
            current = merged.pop(0)
            index = 0
            while index < len(merged):
                horizontal, vertical = _box_distance(current, merged[index])
                overlaps_axis = not (
                    current.x1 <= merged[index].x0
                    or merged[index].x1 <= current.x0
                    or current.y1 <= merged[index].y0
                    or merged[index].y1 <= current.y0
                )
                if (horizontal <= horizontal_gap and vertical <= vertical_gap) and (
                    overlaps_axis
                    or horizontal <= max(4, horizontal_gap // 3)
                    or vertical <= max(4, vertical_gap // 3)
                ):
                    current = _union(current, merged.pop(index))
                    changed = True
                else:
                    index += 1
            output.append(current)
        merged = output
    return sorted(merged, key=lambda box: (box.y0, box.x0))


def _table_boxes(ink: Any, content: PixelBox, line_pitch: float) -> list[PixelBox]:
    """Find ruled table candidates from repeated long horizontal strokes."""

    np = _require_numpy()
    cropped = ink[content.y0 : content.y1, content.x0 : content.x1]
    long_rows: list[tuple[int, int, int]] = []
    minimum = max(90, int(content.width * 0.16))
    # A photographed page can tilt one end of a long rule by roughly half a
    # text line.  Collapse that vertical band before measuring continuity.
    tolerance = max(2, round(line_pitch * 0.50))
    bridge = max(2, round(line_pitch * 0.12))
    for row_index in range(cropped.shape[0]):
        top = max(0, row_index - tolerance)
        bottom = min(cropped.shape[0], row_index + tolerance + 1)
        projection = np.any(cropped[top:bottom], axis=0)
        runs = _merge_runs(_runs(projection), gap=bridge)
        if not runs:
            continue
        credible = [
            run
            for run in runs
            if run[1] - run[0] >= minimum
            and float(np.count_nonzero(projection[run[0] : run[1]])) / max(1, run[1] - run[0])
            >= 0.70
        ]
        if not credible:
            continue
        longest = max(credible, key=lambda run: run[1] - run[0])
        if longest[1] - longest[0] >= minimum:
            long_rows.append(
                (row_index + content.y0, longest[0] + content.x0, longest[1] + content.x0)
            )
    if not long_rows:
        return []
    row_groups: list[list[tuple[int, int, int]]] = [[long_rows[0]]]
    for item in long_rows[1:]:
        if item[0] - row_groups[-1][-1][0] <= max(3, int(line_pitch * 2.4)):
            row_groups[-1].append(item)
        else:
            row_groups.append([item])
    boxes: list[PixelBox] = []
    for group in row_groups:
        distinct_y = _merge_runs([(item[0], item[0] + 1) for item in group], gap=2)
        if len(distinct_y) < 2:
            continue
        x0 = min(item[1] for item in group)
        x1 = max(item[2] for item in group)
        y0 = distinct_y[0][0]
        y1 = distinct_y[-1][1]
        if y1 - y0 < max(10, line_pitch * 0.6):
            continue
        pad = max(2, int(line_pitch * 0.18))
        boxes.append(
            PixelBox(
                x0=max(content.x0, x0 - pad),
                y0=max(content.y0, y0 - pad),
                x1=min(content.x1, x1 + pad),
                y1=min(content.y1, y1 + pad),
            )
        )
    return _merge_boxes(
        boxes,
        horizontal_gap=max(5, int(line_pitch * 0.4)),
        vertical_gap=max(5, int(line_pitch * 0.7)),
    )


def _near_axis_rule_count(
    ink: Any,
    *,
    horizontal: bool,
    line_pitch: float,
) -> int:
    """Count slightly skewed/broken ruling lines without requiring one exact row."""

    np = _require_numpy()
    primary = ink if horizontal else ink.T
    span = primary.shape[1]
    tolerance = max(2, round(line_pitch * 0.35))
    bridge = max(2, round(line_pitch * 0.12))
    active: list[tuple[int, int]] = []
    for index in range(primary.shape[0]):
        start = max(0, index - tolerance)
        end = min(primary.shape[0], index + tolerance + 1)
        projection = np.any(primary[start:end], axis=0)
        runs = _merge_runs(_runs(projection), gap=bridge)
        if any(
            right - left >= span * 0.52
            and float(np.count_nonzero(projection[left:right])) / max(1, right - left) >= 0.70
            for left, right in runs
        ):
            active.append((index, index + 1))
    return len(_merge_runs(active, gap=max(2, tolerance)))


def _figure_boxes(ink: Any, content: PixelBox, line_pitch: float) -> list[PixelBox]:
    """Find connected non-text shapes and merge their nearby labels."""

    np = _require_numpy()
    tile = max(3, int(round(line_pitch / 8)))
    height = math.ceil(ink.shape[0] / tile)
    width = math.ceil(ink.shape[1] / tile)
    padded = np.zeros((height * tile, width * tile), dtype=bool)
    padded[: ink.shape[0], : ink.shape[1]] = ink
    counts = padded.reshape(height, tile, width, tile).sum(axis=(1, 3))
    # Requiring several pixels rejects isolated glyph noise but retains curves,
    # axes, arrows, and diagram strokes.
    coarse = counts >= max(2, int(tile * tile * 0.08))
    components = _coarse_components(coarse)
    candidates: list[PixelBox] = []
    for x0, y0, x1, y1, count in components:
        box = PixelBox(
            x0=max(content.x0, x0 * tile),
            y0=max(content.y0, y0 * tile),
            x1=min(content.x1, x1 * tile),
            y1=min(content.y1, y1 * tile),
        )
        if box.width <= 0 or box.height <= 0:
            continue
        tall_shape = box.height >= line_pitch * 1.8 and box.width >= line_pitch * 1.2
        wide_shape = box.width >= content.width * 0.14 and box.height >= line_pitch * 0.8
        dense_shape = count >= 35 and box.height >= line_pitch * 1.2
        if tall_shape or wide_shape or dense_shape:
            candidates.append(box)
    merged = _merge_boxes(
        candidates,
        horizontal_gap=max(12, int(line_pitch * 1.2)),
        vertical_gap=max(8, int(line_pitch * 0.8)),
    )
    minimum_area = max(800, int(content.area * 0.003))
    return [
        box
        for box in merged
        if box.area >= minimum_area
        and box.width >= line_pitch * 2
        and box.height >= line_pitch * 1.5
    ]


def _deduplicate_regions(
    figures: list[PixelBox],
    tables: list[PixelBox],
    content: PixelBox,
    ink: Any,
    text_lines: list[ScanTextLine] | None = None,
    line_pitch: float | None = None,
) -> list[ScanRegion]:
    regions: list[ScanRegion] = []
    for table in tables:
        cropped = ink[table.y0 : table.y1, table.x0 : table.x1]
        horizontal_count = _near_axis_rule_count(
            cropped,
            horizontal=True,
            line_pitch=line_pitch or 18.0,
        )
        vertical_count = _near_axis_rule_count(
            cropped,
            horizontal=False,
            line_pitch=line_pitch or 18.0,
        )
        if horizontal_count < 2 or vertical_count < 1:
            continue
        regions.append(
            ScanRegion(
                kind=ScanRegionKind.TABLE,
                bbox=table,
                confidence=0.86,
                metadata={
                    "detection": "repeated-long-rules",
                    "horizontal_rules": horizontal_count,
                    "vertical_rules": vertical_count,
                    "aspect_ratio": table.width / max(1, table.height),
                },
            )
        )
    for figure in figures:
        if text_lines and line_pitch and figure.height <= line_pitch * 3.2:
            overlap_area = 0
            overlapping_lines = 0
            for line in text_lines:
                width = max(0, min(figure.x1, line.bbox.x1) - max(figure.x0, line.bbox.x0))
                height = max(0, min(figure.y1, line.bbox.y1) - max(figure.y0, line.bbox.y0))
                if width and height:
                    overlap_area += width * height
                    overlapping_lines += 1
            if overlapping_lines >= 1 and overlap_area / max(1, figure.area) >= 0.20:
                # Short connected components that substantially coincide with
                # stable text lines are usually bold/italic words, not figures.
                continue
        best_overlap = 0.0
        for table in tables:
            intersection_width = max(0, min(figure.x1, table.x1) - max(figure.x0, table.x0))
            intersection_height = max(0, min(figure.y1, table.y1) - max(figure.y0, table.y0))
            overlap = intersection_width * intersection_height
            best_overlap = max(best_overlap, overlap / max(1, min(figure.area, table.area)))
        if best_overlap >= 0.72:
            continue
        kind = ScanRegionKind.MIXED if best_overlap >= 0.25 else ScanRegionKind.FIGURE
        scale_score = min(1.0, figure.area / max(1.0, content.area * 0.08))
        regions.append(
            ScanRegion(
                kind=kind,
                bbox=figure,
                confidence=0.55 + 0.35 * scale_score,
                metadata={"detection": "connected-nontext-shapes"},
            )
        )
    return sorted(regions, key=lambda region: (region.bbox.y0, region.bbox.x0))


def analyze_scan_page(
    image: Image.Image,
    *,
    number: int,
    pdf_width: float,
    pdf_height: float,
    metadata: dict[str, Any] | None = None,
) -> ScanPageLayout:
    """Recover layout evidence from a page image without OCR."""

    np = _require_numpy()
    rgb = image.convert("RGB")
    gray_image = rgb.convert("L")
    grayscale = np.asarray(gray_image)
    # Local illumination normalization handles photographed paper shadows while
    # retaining the deterministic fixed-threshold behaviour of clean scans.
    radius = max(7.0, min(rgb.size) / 42.0)
    background = np.asarray(gray_image.filter(ImageFilter.GaussianBlur(radius=radius)))
    ink = (grayscale.astype(int) + 17 < background.astype(int)) | (grayscale < 72)
    content = _content_bbox(ink)
    line_pitch, line_bands = _estimate_line_pitch(ink, content)
    text_lines, refined_pitch = _detect_text_lines(ink, content, line_pitch)
    if len(text_lines) >= 4:
        line_pitch = refined_pitch
        line_bands = [(line.bbox.y0, line.bbox.y1) for line in text_lines]
    column_metadata = _detect_column_layout(ink, content, text_lines, line_pitch)
    periodic_pitch = column_metadata.get("periodic_line_pitch")
    if isinstance(periodic_pitch, (int, float)) and periodic_pitch >= 12:
        periodic_value = float(periodic_pitch)
        # A dense masthead or display title can make the first global pass
        # merge every second body baseline.  Once repeated column rhythm gives
        # stronger evidence for a materially smaller pitch, rerun the actual
        # line detector instead of only changing the numeric metadata.  The
        # previous behaviour reported a 13 px newspaper rhythm while retaining
        # 29 px text-line boxes, so downstream geometry had only half the rows.
        if periodic_value <= line_pitch * 0.86:
            periodic_lines, periodic_refined = _detect_text_lines(
                ink,
                content,
                periodic_value,
            )
            if len(periodic_lines) >= max(4, round(len(text_lines) * 1.12)):
                text_lines = periodic_lines
                line_pitch = min(periodic_value, periodic_refined)
                line_bands = [(line.bbox.y0, line.bbox.y1) for line in text_lines]
                column_metadata = _detect_column_layout(
                    ink,
                    content,
                    text_lines,
                    line_pitch,
                )
            else:
                line_pitch = min(line_pitch, periodic_value)
        else:
            line_pitch = min(line_pitch, periodic_value)
    tables = _table_boxes(ink, content, line_pitch)
    figures = _figure_boxes(ink, content, line_pitch)
    regions = _deduplicate_regions(
        figures,
        tables,
        content,
        ink,
        text_lines=text_lines,
        line_pitch=line_pitch,
    )
    header_metadata = _detect_header_layout(text_lines, content, line_pitch)
    render_frame = _render_content_frame(text_lines, content)
    paper_color = _estimate_paper_color(rgb, ink, render_frame)
    return ScanPageLayout(
        number=number,
        width=rgb.width,
        height=rgb.height,
        pdf_width=pdf_width,
        pdf_height=pdf_height,
        content_bbox=content,
        line_pitch=line_pitch,
        line_bands=line_bands,
        text_lines=text_lines,
        regions=regions,
        metadata={
            **(metadata or {}),
            **column_metadata,
            **header_metadata,
            "render_content_bbox": render_frame.model_dump(mode="json"),
            "paper_color": paper_color,
        },
        image=rgb,
    )


def _estimate_paper_color(image: Image.Image, ink: Any, frame: PixelBox) -> str:
    """Estimate a flat editable page colour while rejecting ink and shadows."""

    np = _require_numpy()
    pixels = np.asarray(image.convert("RGB"), dtype=np.float32)[
        frame.y0 : frame.y1,
        frame.x0 : frame.x1,
    ]
    non_ink = ~ink[frame.y0 : frame.y1, frame.x0 : frame.x1]
    samples = pixels[non_ink]
    if len(samples) < 64:
        return "FFFFFF"
    luminance = samples[:, 0] * 0.299 + samples[:, 1] * 0.587 + samples[:, 2] * 0.114
    chroma = samples.max(axis=1) - samples.min(axis=1)
    # Reject deep page-edge shadows and saturated figures.  The median of the
    # remaining paper pixels preserves off-white/blue paper without embedding
    # the photographed page as a raster background.
    light_floor = max(120.0, float(np.percentile(luminance, 28)))
    selected = samples[(luminance >= light_floor) & (chroma <= 72.0)]
    if len(selected) < 64:
        selected = samples[luminance >= light_floor]
    if len(selected) < 64:
        return "FFFFFF"
    red, green, blue = (int(round(value)) for value in np.median(selected, axis=0))
    if max(abs(red - 255), abs(green - 255), abs(blue - 255)) <= 6:
        return "FFFFFF"
    return f"{red:02X}{green:02X}{blue:02X}"


def _rolling_fraction(values: Any, window: int) -> Any:
    """Return a centered moving mean without a SciPy dependency."""

    np = _require_numpy()
    width = max(3, int(window) | 1)
    padded = np.pad(values.astype(float), (width // 2, width // 2), mode="edge")
    integral = np.concatenate(([0.0], np.cumsum(padded)))
    return (integral[width:] - integral[:-width]) / width


def _smooth_edge(values: Any, radius: int = 4) -> Any:
    np = _require_numpy()
    result = np.asarray(values, dtype=float).copy()
    valid = result >= 0
    if not valid.any():
        return result
    indices = np.arange(len(result))
    result[~valid] = np.interp(indices[~valid], indices[valid], result[valid])
    output = result.copy()
    for index in range(len(result)):
        start = max(0, index - radius)
        end = min(len(result), index + radius + 1)
        output[index] = float(np.median(result[start:end]))
    return output


def _rectify_photographed_page(image: Image.Image) -> tuple[Image.Image, dict[str, Any]]:
    """Find and normalize a light paper sheet photographed on a background.

    This is intentionally provider-independent.  It uses neutral-paper colour
    evidence and a piecewise row mesh, so perspective and gently curved page
    edges are corrected without recognizing any text.
    """

    np = _require_numpy()
    rgb = image.convert("RGB")
    scale = min(1.0, 800.0 / max(rgb.size))
    preview = rgb.resize(
        (max(32, round(rgb.width * scale)), max(32, round(rgb.height * scale))),
        Image.Resampling.BILINEAR,
    )
    pixels = np.asarray(preview, dtype=np.float32)
    maximum = pixels.max(axis=2)
    minimum = pixels.min(axis=2)
    luminance = pixels[:, :, 0] * 0.299 + pixels[:, :, 1] * 0.587 + pixels[:, :, 2] * 0.114
    chroma = maximum - minimum
    # A photographed page is normally bright and close to neutral.  Adaptive
    # limits retain off-white paper while rejecting coloured desks and floors.
    light_limit = max(68.0, min(128.0, float(np.percentile(luminance, 10)) - 8.0))
    chroma_limit = max(24.0, min(42.0, float(np.percentile(chroma, 68))))
    paper = (luminance >= light_limit) & (chroma <= chroma_limit)
    height, width = paper.shape
    window = max(9, round(width * 0.052))
    left = np.full(height, -1.0)
    right = np.full(height, -1.0)
    spans = np.zeros(height, dtype=float)
    for row in range(height):
        supported = _rolling_fraction(paper[row], window) >= 0.48
        runs = _merge_runs(_runs(supported), gap=max(5, round(width * 0.18)))
        if not runs:
            continue
        start, end = max(runs, key=lambda item: item[1] - item[0])
        if end - start < width * 0.16:
            continue
        left[row] = start
        right[row] = end
        spans[row] = end - start
    positive = spans[spans > 0]
    if not len(positive):
        return rgb, {
            "source_kind": "image",
            "rectified": False,
            "reason": "no-paper-mask",
            "original_width": rgb.width,
            "original_height": rgb.height,
        }
    reference_span = float(np.percentile(positive, 88))
    stable = spans >= reference_span * 0.72
    left[~stable] = -1
    right[~stable] = -1
    supported_rows = np.flatnonzero(stable)
    if len(supported_rows) < height * 0.24:
        return rgb, {
            "source_kind": "image",
            "rectified": False,
            "reason": "insufficient-paper-boundary",
            "original_width": rgb.width,
            "original_height": rgb.height,
        }
    vertical_inset = max(3, round(width * 0.022))
    top = min(height - 2, int(supported_rows[0]) + vertical_inset)
    bottom = max(top + 2, int(supported_rows[-1] + 1) - vertical_inset // 2)
    left = _smooth_edge(left)
    right = _smooth_edge(right)
    source_scale = 1.0 / scale
    top_source = max(0.0, top * source_scale)
    bottom_source = min(float(rgb.height), bottom * source_scale)
    span_source = reference_span * source_scale
    observed_ratio = span_source / max(1.0, bottom_source - top_source)
    standards = [
        (595.28, 841.89, "a4"),
        (612.0, 792.0, "letter"),
    ]
    if observed_ratio < 0.64:
        standards.append((612.0, 1008.0, "legal"))
    pdf_width, pdf_height, page_standard = min(
        standards,
        key=lambda item: abs(math.log(max(0.1, observed_ratio) / (item[0] / item[1]))),
    )
    target_ratio = pdf_width / pdf_height
    target_width = max(720, min(1800, round(span_source)))
    target_height = max(960, round(target_width / target_ratio))
    bands = max(12, min(48, round((bottom - top) / 12)))
    mesh: list[tuple[tuple[int, int, int, int], tuple[float, ...]]] = []
    mapping_bands: list[SourceToScanBand] = []
    for band in range(bands):
        destination_top = round(band * target_height / bands)
        destination_bottom = round((band + 1) * target_height / bands)
        preview_top = top + band * (bottom - top) / bands
        preview_bottom = top + (band + 1) * (bottom - top) / bands
        top_index = min(height - 1, max(0, round(preview_top)))
        bottom_index = min(height - 1, max(0, round(preview_bottom)))
        horizontal_inset = width * 0.012
        top_left = max(0.0, (left[top_index] + horizontal_inset) * source_scale)
        top_right = min(float(rgb.width), (right[top_index] - horizontal_inset) * source_scale)
        bottom_left = max(0.0, (left[bottom_index] + horizontal_inset) * source_scale)
        bottom_right = min(
            float(rgb.width),
            (right[bottom_index] - horizontal_inset) * source_scale,
        )
        if min(top_right - top_left, bottom_right - bottom_left) < rgb.width * 0.12:
            continue
        mesh.append(
            (
                (0, destination_top, target_width, destination_bottom),
                (
                    top_left,
                    preview_top * source_scale,
                    bottom_left,
                    preview_bottom * source_scale,
                    bottom_right,
                    preview_bottom * source_scale,
                    top_right,
                    preview_top * source_scale,
                ),
            )
        )
        mapping_bands.append(
            SourceToScanBand(
                source_y0=preview_top * source_scale,
                source_y1=preview_bottom * source_scale,
                source_left0=top_left,
                source_right0=top_right,
                source_left1=bottom_left,
                source_right1=bottom_right,
                target_y0=destination_top,
                target_y1=destination_bottom,
            )
        )
    if len(mesh) < bands * 0.75:
        return rgb, {
            "source_kind": "image",
            "rectified": False,
            "reason": "unstable-mesh",
            "original_width": rgb.width,
            "original_height": rgb.height,
        }
    rectified = rgb.transform(
        (target_width, target_height),
        Image.Transform.MESH,
        mesh,
        resample=Image.Resampling.BICUBIC,
        fillcolor="white",
    )
    page_coverage = reference_span / width * (bottom - top) / height
    aspect_delta = abs(rectified.width / rectified.height - rgb.width / rgb.height)
    if page_coverage > 0.88 and aspect_delta < 0.04:
        # A flat scan already filling the canvas should not be resampled.
        return rgb, {
            "source_kind": "image",
            "rectified": False,
            "page_standard": page_standard,
            "pdf_width": pdf_width,
            "pdf_height": pdf_height,
            "original_width": rgb.width,
            "original_height": rgb.height,
        }
    confidence = round(min(0.98, 0.55 + page_coverage * 0.5), 4)
    coordinate_mapping = SourceToScanMap(
        source_width=rgb.width,
        source_height=rgb.height,
        target_width=target_width,
        target_height=target_height,
        bands=mapping_bands,
        confidence=confidence,
    )
    return rectified, {
        "source_kind": "image",
        "rectified": True,
        "rectification": "neutral-paper-row-mesh",
        "original_width": rgb.width,
        "original_height": rgb.height,
        "page_standard": page_standard,
        "pdf_width": pdf_width,
        "pdf_height": pdf_height,
        "confidence": confidence,
        "source_to_scan_map": coordinate_mapping.model_dump(mode="json"),
    }


def _extract_with_pypdf(path: Path) -> list[tuple[Image.Image, float, float]]:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise ProviderUnavailableError("pypdf is not installed") from exc
    reader = PdfReader(str(path))
    pages: list[tuple[Image.Image, float, float]] = []
    for page in reader.pages:
        media_box = page.mediabox
        pdf_width = float(media_box.width)
        pdf_height = float(media_box.height)
        images = list(page.images)
        if len(images) != 1:
            raise ValueError("PDF page does not contain one unambiguous full-page raster")
        page_image = images[0].image
        if page_image is None:
            raise ValueError("PDF page raster could not be decoded")
        image = page_image.convert("RGB")
        pages.append((image, pdf_width, pdf_height))
    return pages


def _extract_with_pymupdf(path: Path, dpi: int) -> list[tuple[Image.Image, float, float]]:
    try:
        import pymupdf
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise ProviderUnavailableError("PyMuPDF is not installed") from exc
    pages: list[tuple[Image.Image, float, float]] = []
    with pymupdf.open(path) as pdf:
        for page in pdf:
            pixmap = page.get_pixmap(dpi=dpi, alpha=False)
            image = Image.open(io.BytesIO(pixmap.tobytes("png"))).convert("RGB")
            pages.append((image, float(page.rect.width), float(page.rect.height)))
    return pages


def _scan_page_worker_count(page_count: int, maximum_workers: int | None) -> int:
    """Resolve a conservative page-analysis worker count.

    Page rasters are held in memory while they are analyzed, so an unbounded
    executor can multiply memory pressure on long, high-DPI documents.  The
    automatic setting respects both available CPUs and a small project-wide
    ceiling.  Explicit settings remain bounded by the page count, CPU count,
    and a higher hard safety ceiling.
    """

    if page_count < 1:
        return 0
    if isinstance(maximum_workers, bool) or (
        maximum_workers is not None
        and (not isinstance(maximum_workers, int) or maximum_workers < 1)
    ):
        raise ValueError("maximum scan page workers must be a positive integer")
    cpu_count = max(1, os.cpu_count() or 1)
    requested = _DEFAULT_MAX_PAGE_WORKERS if maximum_workers is None else maximum_workers
    return min(page_count, cpu_count, requested, _ABSOLUTE_MAX_PAGE_WORKERS)


def _analyze_extracted_pdf_page(
    work_item: tuple[int, Image.Image, float, float],
) -> ScanPageLayout:
    number, image, pdf_width, pdf_height = work_item
    return analyze_scan_page(
        image,
        number=number,
        pdf_width=pdf_width,
        pdf_height=pdf_height,
        metadata={"source_kind": "pdf", "rectified": False},
    )


def analyze_scan_pdf(
    source: str | Path,
    *,
    dpi: int = 192,
    maximum_workers: int | None = None,
) -> ScanDocumentLayout:
    """Extract and analyze every PDF page using installed local backends only.

    Independent page analyses use bounded worker processes for multi-page inputs.
    The returned pages always retain source order regardless of completion order.
    Set ``maximum_workers=1`` for serial analysis.
    """

    path = Path(source).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    if path.suffix.lower() != ".pdf":
        raise ValueError("scan layout authority must be a PDF")
    try:
        extracted = _extract_with_pypdf(path)
    except (ProviderUnavailableError, ValueError, OSError):
        extracted = _extract_with_pymupdf(path, dpi)
    work_items = [
        (index, image, pdf_width, pdf_height)
        for index, (image, pdf_width, pdf_height) in enumerate(extracted, start=1)
    ]
    worker_count = _scan_page_worker_count(len(work_items), maximum_workers)
    if worker_count == 1:
        pages = [_analyze_extracted_pdf_page(item) for item in work_items]
    elif worker_count > 1:
        with ProcessPoolExecutor(
            max_workers=worker_count,
        ) as executor:
            pages = list(executor.map(_analyze_extracted_pdf_page, work_items))
    else:
        pages = []
    if not pages:
        raise ValueError("layout PDF contains no pages")
    return ScanDocumentLayout(source=str(path), pages=pages)


def analyze_scan_image(source: str | Path) -> ScanDocumentLayout:
    """Normalize and analyze one photographed or flat raster document page."""

    path = Path(source).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    if path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp"}:
        raise ValueError("scan image must be PNG, JPEG, TIFF, or WebP")
    with Image.open(path) as opened:
        source_image = opened.convert("RGB")
    image, metadata = _rectify_photographed_page(source_image)
    pdf_width = float(metadata.get("pdf_width", 595.28))
    pdf_height = float(metadata.get("pdf_height", 841.89))
    page = analyze_scan_page(
        image,
        number=1,
        pdf_width=pdf_width,
        pdf_height=pdf_height,
        metadata=metadata,
    )
    return ScanDocumentLayout(source=str(path), pages=[page])


def analyze_scan_source(
    source: str | Path,
    *,
    dpi: int = 192,
    maximum_workers: int | None = None,
) -> ScanDocumentLayout:
    """Dispatch a PDF or raster layout authority to the matching analyzer."""

    path = Path(source).expanduser().resolve()
    if path.suffix.lower() == ".pdf":
        return analyze_scan_pdf(path, dpi=dpi, maximum_workers=maximum_workers)
    return analyze_scan_image(path)


__all__ = [
    "PixelBox",
    "ScanDocumentLayout",
    "ScanPageLayout",
    "ScanRegion",
    "ScanRegionKind",
    "ScanTextLine",
    "SourceToScanBand",
    "SourceToScanMap",
    "analyze_scan_page",
    "analyze_scan_image",
    "analyze_scan_pdf",
    "analyze_scan_source",
    "project_source_box_to_scan",
]
