"""Construction and stable identity of localized render diagnostics."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from .components import ForegroundComponent, box_union, mask_fraction, normalized_box
from .models import (
    RENDER_DIFF_METRIC_VERSION,
    RenderDiffComponentScores,
    RenderDiffDiagnostic,
    RenderDiffKind,
    RenderPixelBox,
)


@dataclass(frozen=True, slots=True)
class MappedRegion:
    object_id: str
    bbox: RenderPixelBox
    outside_page: bool = False


def _bounded(value: float) -> float:
    return round(max(0.0, min(1.0, value)), 6)


def _scores(
    *,
    shape: float,
    area: float,
    position: float,
    overlap: float,
    reference_difference: float,
    candidate_difference: float,
    evidence_strength: float,
) -> RenderDiffComponentScores:
    return RenderDiffComponentScores(
        shape_similarity=_bounded(shape),
        area_similarity=_bounded(area),
        position_similarity=_bounded(position),
        foreground_overlap=_bounded(overlap),
        reference_difference_fraction=_bounded(reference_difference),
        candidate_difference_fraction=_bounded(candidate_difference),
        evidence_strength=_bounded(evidence_strength),
    )


def _diagnostic(
    *,
    kind: RenderDiffKind,
    page_number: int,
    bbox: RenderPixelBox,
    page_size: tuple[int, int],
    severity: float,
    scores: RenderDiffComponentScores,
    reference_bbox: RenderPixelBox | None,
    candidate_bbox: RenderPixelBox | None,
    object_ids: Sequence[str],
    evidence: Sequence[str],
) -> RenderDiffDiagnostic:
    ids = tuple(sorted(set(object_ids), key=str.casefold))
    reasons = tuple(sorted(set(evidence)))
    identity_payload = {
        "metric_version": RENDER_DIFF_METRIC_VERSION,
        "kind": kind.value,
        "page_number": page_number,
        "bbox": bbox.model_dump(mode="json"),
        "reference_bbox": (
            reference_bbox.model_dump(mode="json") if reference_bbox is not None else None
        ),
        "candidate_bbox": (
            candidate_bbox.model_dump(mode="json") if candidate_bbox is not None else None
        ),
        "object_ids": ids,
        "evidence": reasons,
    }
    encoded = json.dumps(identity_payload, sort_keys=True, separators=(",", ":")).encode()
    diagnostic_id = "rd-" + hashlib.sha256(encoded).hexdigest()[:16]
    return RenderDiffDiagnostic(
        diagnostic_id=diagnostic_id,
        kind=kind,
        page_number=page_number,
        bbox=bbox,
        normalized_bbox=normalized_box(bbox, page_size),
        severity=_bounded(severity),
        scores=scores,
        reference_bbox=reference_bbox,
        candidate_bbox=candidate_bbox,
        object_ids=ids,
        evidence=reasons,
    )


def paired_diagnostics(
    reference: ForegroundComponent,
    candidate: ForegroundComponent,
    *,
    page_number: int,
    page_size: tuple[int, int],
    values: tuple[float, float, float, float],
    missing_mask: Any,
    extra_mask: Any,
    object_ids: tuple[str, ...],
) -> list[RenderDiffDiagnostic]:
    shape, area, position, overlap = values
    ref_difference = mask_fraction(missing_mask, reference.bbox, reference.foreground_pixels)
    cand_difference = mask_fraction(extra_mask, candidate.bbox, candidate.foreground_pixels)
    evidence_strength = 1.0 if object_ids else 0.50 * shape + 0.30 * area + 0.20 * position
    scores = _scores(
        shape=shape,
        area=area,
        position=position,
        overlap=overlap,
        reference_difference=ref_difference,
        candidate_difference=cand_difference,
        evidence_strength=evidence_strength,
    )
    width, height = page_size
    ref_center = reference.center
    cand_center = candidate.center
    displacement = math.hypot(
        (ref_center[0] - cand_center[0]) / width,
        (ref_center[1] - cand_center[1]) / height,
    )
    width_ratio = min(reference.bbox.width, candidate.bbox.width) / max(
        reference.bbox.width, candidate.bbox.width
    )
    height_ratio = min(reference.bbox.height, candidate.bbox.height) / max(
        reference.bbox.height, candidate.bbox.height
    )
    output: list[RenderDiffDiagnostic] = []
    shared = box_union(reference.bbox, candidate.bbox)
    if displacement >= 0.008 and (shape >= 0.45 or bool(object_ids)):
        output.append(
            _diagnostic(
                kind=RenderDiffKind.DISPLACED_REGION,
                page_number=page_number,
                bbox=shared,
                page_size=page_size,
                severity=0.65 * min(1.0, displacement / 0.20) + 0.35 * (1.0 - overlap),
                scores=scores,
                reference_bbox=reference.bbox,
                candidate_bbox=candidate.bbox,
                object_ids=object_ids,
                evidence=(
                    "shared_object_id" if object_ids else "foreground_shape_assignment",
                    "centroid_displacement",
                ),
            )
        )
    if min(width_ratio, height_ratio, area) < 0.80 and (shape >= 0.40 or bool(object_ids)):
        output.append(
            _diagnostic(
                kind=RenderDiffKind.SIZE_MISMATCH,
                page_number=page_number,
                bbox=shared,
                page_size=page_size,
                severity=1.0 - (0.45 * area + 0.30 * width_ratio + 0.25 * height_ratio),
                scores=scores,
                reference_bbox=reference.bbox,
                candidate_bbox=candidate.bbox,
                object_ids=object_ids,
                evidence=(
                    "shared_object_id" if object_ids else "foreground_shape_assignment",
                    "extent_or_ink_area_ratio",
                ),
            )
        )
    if not output and ref_difference >= 0.15:
        output.append(
            _diagnostic(
                kind=RenderDiffKind.MISSING_REGION,
                page_number=page_number,
                bbox=reference.bbox,
                page_size=page_size,
                severity=0.15 + 0.85 * math.sqrt(ref_difference),
                scores=scores,
                reference_bbox=reference.bbox,
                candidate_bbox=candidate.bbox,
                object_ids=object_ids,
                evidence=("connected_unmatched_reference_foreground",),
            )
        )
    if not output and cand_difference >= 0.15:
        output.append(
            _diagnostic(
                kind=RenderDiffKind.EXTRA_REGION,
                page_number=page_number,
                bbox=candidate.bbox,
                page_size=page_size,
                severity=0.15 + 0.85 * math.sqrt(cand_difference),
                scores=scores,
                reference_bbox=reference.bbox,
                candidate_bbox=candidate.bbox,
                object_ids=object_ids,
                evidence=("connected_unmatched_candidate_foreground",),
            )
        )
    return output


def unmatched_diagnostic(
    component: ForegroundComponent,
    *,
    kind: RenderDiffKind,
    page_number: int,
    page_size: tuple[int, int],
    total_foreground: int,
    object_ids: tuple[str, ...],
) -> RenderDiffDiagnostic:
    share = component.foreground_pixels / max(1, total_foreground)
    missing = 1.0 if kind is RenderDiffKind.MISSING_REGION else 0.0
    extra = 1.0 if kind is RenderDiffKind.EXTRA_REGION else 0.0
    return _diagnostic(
        kind=kind,
        page_number=page_number,
        bbox=component.bbox,
        page_size=page_size,
        severity=0.15 + 0.85 * math.sqrt(min(1.0, share)),
        scores=_scores(
            shape=0.0,
            area=0.0,
            position=0.0,
            overlap=0.0,
            reference_difference=missing,
            candidate_difference=extra,
            evidence_strength=1.0 if object_ids else min(1.0, 0.55 + share),
        ),
        reference_bbox=(component.bbox if kind is RenderDiffKind.MISSING_REGION else None),
        candidate_bbox=(component.bbox if kind is RenderDiffKind.EXTRA_REGION else None),
        object_ids=object_ids,
        evidence=(
            "mapped_object_region" if object_ids else "connected_foreground_component",
            "no_safe_bipartite_match",
        ),
    )


def overflow_diagnostics(
    candidate_regions: Sequence[MappedRegion],
    *,
    page_number: int,
    page_size: tuple[int, int],
) -> list[RenderDiffDiagnostic]:
    output: list[RenderDiffDiagnostic] = []
    for region in candidate_regions:
        if not region.outside_page:
            continue
        output.append(
            _diagnostic(
                kind=RenderDiffKind.CLIPPING_OVERFLOW,
                page_number=page_number,
                bbox=region.bbox,
                page_size=page_size,
                severity=0.75,
                scores=_scores(
                    shape=0.0,
                    area=0.0,
                    position=0.0,
                    overlap=0.0,
                    reference_difference=0.0,
                    candidate_difference=1.0,
                    evidence_strength=1.0,
                ),
                reference_bbox=None,
                candidate_bbox=region.bbox,
                object_ids=(region.object_id,),
                evidence=("candidate_object_bbox_outside_page",),
            )
        )
    return output


__all__ = [
    "MappedRegion",
    "overflow_diagnostics",
    "paired_diagnostics",
    "unmatched_diagnostic",
]
