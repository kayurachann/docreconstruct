"""Typed, serialization-stable render-difference diagnostics."""

from __future__ import annotations

import hashlib
import json
import math
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

RENDER_DIFF_METRIC_VERSION = "1.0.0"
RENDER_DIFF_REPORT_SCHEMA_VERSION = "1.0"


class RenderDiffKind(StrEnum):
    """Geometry failures supported by the deterministic localizer."""

    MISSING_REGION = "missing_region"
    EXTRA_REGION = "extra_region"
    DISPLACED_REGION = "displaced_region"
    SIZE_MISMATCH = "size_mismatch"
    CLIPPING_OVERFLOW = "clipping_overflow"


class RenderPixelBox(BaseModel):
    """Half-open integer box in one page raster's pixel coordinates."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    x0: int
    y0: int
    x1: int
    y1: int

    @model_validator(mode="after")
    def coordinates_are_ordered(self) -> RenderPixelBox:
        if self.x1 <= self.x0 or self.y1 <= self.y0:
            raise ValueError("render pixel boxes must have positive width and height")
        return self

    @property
    def width(self) -> int:
        return self.x1 - self.x0

    @property
    def height(self) -> int:
        return self.y1 - self.y0

    @property
    def area(self) -> int:
        return self.width * self.height


class RenderNormalizedBox(BaseModel):
    """Half-open page box normalized to the closed unit square."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    x0: float = Field(ge=0.0, le=1.0)
    y0: float = Field(ge=0.0, le=1.0)
    x1: float = Field(ge=0.0, le=1.0)
    y1: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def coordinates_are_ordered(self) -> RenderNormalizedBox:
        values = (self.x0, self.y0, self.x1, self.y1)
        if not all(math.isfinite(value) for value in values):
            raise ValueError("normalized render boxes must be finite")
        if self.x1 <= self.x0 or self.y1 <= self.y0:
            raise ValueError("normalized render boxes must have positive width and height")
        return self


class RenderedObjectRegion(BaseModel):
    """Optional renderer/IR geometry used only to name or strengthen a diagnosis.

    Boxes use the original raster's pixel coordinate system. They may extend
    outside that raster: an out-of-bounds candidate region is direct overflow
    evidence and is retained rather than silently clipped on input.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    page_number: int = Field(default=1, ge=1)
    object_id: str = Field(min_length=1)
    bbox: RenderPixelBox


class RenderDiffComponentScores(BaseModel):
    """Bounded evidence components behind one diagnostic severity."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    shape_similarity: float = Field(ge=0.0, le=1.0)
    area_similarity: float = Field(ge=0.0, le=1.0)
    position_similarity: float = Field(ge=0.0, le=1.0)
    foreground_overlap: float = Field(ge=0.0, le=1.0)
    reference_difference_fraction: float = Field(ge=0.0, le=1.0)
    candidate_difference_fraction: float = Field(ge=0.0, le=1.0)
    evidence_strength: float = Field(ge=0.0, le=1.0)


class RenderDiffDiagnostic(BaseModel):
    """One localized render discrepancy; never a content-authority mutation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    diagnostic_id: str = Field(pattern=r"^rd-[0-9a-f]{16}$")
    kind: RenderDiffKind
    page_number: int = Field(ge=1)
    bbox: RenderPixelBox
    normalized_bbox: RenderNormalizedBox
    severity: float = Field(ge=0.0, le=1.0)
    scores: RenderDiffComponentScores
    reference_bbox: RenderPixelBox | None = None
    candidate_bbox: RenderPixelBox | None = None
    object_ids: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()
    metric_version: str = RENDER_DIFF_METRIC_VERSION


class RenderDiffPageSummary(BaseModel):
    """Foreground/difference accounting for one compared page."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    page_number: int = Field(ge=1)
    reference_width: int = Field(gt=0)
    reference_height: int = Field(gt=0)
    candidate_width: int = Field(gt=0)
    candidate_height: int = Field(gt=0)
    reference_foreground_pixels: int = Field(ge=0)
    candidate_foreground_pixels: int = Field(ge=0)
    missing_difference_pixels: int = Field(ge=0)
    extra_difference_pixels: int = Field(ge=0)
    reference_components: int = Field(ge=0)
    candidate_components: int = Field(ge=0)


class RenderDiffReport(BaseModel):
    """Deterministic multi-page render-diff localization report."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = RENDER_DIFF_REPORT_SCHEMA_VERSION
    metric_version: str = RENDER_DIFF_METRIC_VERSION
    reference_page_count: int = Field(ge=0)
    candidate_page_count: int = Field(ge=0)
    pages_compared: int = Field(ge=0)
    page_summaries: tuple[RenderDiffPageSummary, ...]
    diagnostics: tuple[RenderDiffDiagnostic, ...]
    diagnostic_counts: dict[str, int]
    max_severity: float = Field(ge=0.0, le=1.0)

    @property
    def fingerprint(self) -> str:
        """Stable identity of the metric inputs represented in the report."""

        payload = self.model_dump(mode="json")
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        payload = self.model_dump(mode="json")
        payload["fingerprint"] = self.fingerprint
        return payload


__all__ = [
    "RENDER_DIFF_METRIC_VERSION",
    "RENDER_DIFF_REPORT_SCHEMA_VERSION",
    "RenderDiffComponentScores",
    "RenderDiffDiagnostic",
    "RenderDiffKind",
    "RenderDiffPageSummary",
    "RenderDiffReport",
    "RenderNormalizedBox",
    "RenderPixelBox",
    "RenderedObjectRegion",
]
