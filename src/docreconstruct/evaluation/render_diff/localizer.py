"""Deterministic, non-semantic localization of raster render differences."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from docreconstruct.evaluation.assignment import minimum_cost_assignment

from .components import (
    ForegroundComponent,
    blank_page,
    clip_box,
    component_similarity,
    difference_masks,
    extract_components,
    foreground_count,
    foreground_page,
    image_source_size,
    scale_box,
)
from .diagnostics import (
    MappedRegion,
    overflow_diagnostics,
    paired_diagnostics,
    unmatched_diagnostic,
)
from .models import (
    RenderDiffDiagnostic,
    RenderDiffKind,
    RenderDiffPageSummary,
    RenderDiffReport,
    RenderedObjectRegion,
    RenderPixelBox,
)


@dataclass(frozen=True, slots=True)
class _PageLocalization:
    summary: RenderDiffPageSummary
    diagnostics: tuple[RenderDiffDiagnostic, ...]


def _intersection_fraction(left: RenderPixelBox, right: RenderPixelBox) -> float:
    width = max(0, min(left.x1, right.x1) - max(left.x0, right.x0))
    height = max(0, min(left.y1, right.y1) - max(left.y0, right.y0))
    denominator = min(left.area, right.area)
    return 0.0 if denominator == 0 else width * height / denominator


def _component_object_ids(
    component: ForegroundComponent,
    regions: Sequence[MappedRegion],
) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                region.object_id
                for region in regions
                if _intersection_fraction(component.bbox, region.bbox) >= 0.10
            },
            key=str.casefold,
        )
    )


def _region_key(region: RenderedObjectRegion) -> tuple[Any, ...]:
    return (
        region.page_number,
        region.object_id.casefold(),
        region.object_id,
        region.bbox.y0,
        region.bbox.x0,
        region.bbox.y1,
        region.bbox.x1,
    )


def _validate_regions(
    regions: Sequence[RenderedObjectRegion],
    *,
    page_count: int,
    label: str,
) -> tuple[RenderedObjectRegion, ...]:
    ordered = tuple(sorted(regions, key=_region_key))
    identities = [(region.page_number, region.object_id) for region in ordered]
    if len(identities) != len(set(identities)):
        raise ValueError(f"{label} object IDs must be unique within each page")
    invalid_pages = [region.page_number for region in ordered if region.page_number > page_count]
    if invalid_pages:
        raise ValueError(f"{label} region refers to absent page {min(invalid_pages)}")
    return ordered


def _map_regions(
    regions: Sequence[RenderedObjectRegion],
    *,
    page_number: int,
    original_size: tuple[int, int],
    target_size: tuple[int, int],
    reference: bool,
) -> tuple[MappedRegion, ...]:
    width, height = original_size
    mapped: list[MappedRegion] = []
    for region in regions:
        if region.page_number != page_number:
            continue
        outside = (
            region.bbox.x0 < 0
            or region.bbox.y0 < 0
            or region.bbox.x1 > width
            or region.bbox.y1 > height
        )
        if reference and outside:
            raise ValueError(
                f"reference region {region.object_id!r} extends outside page {page_number}"
            )
        scaled = scale_box(region.bbox, source_size=original_size, target_size=target_size)
        clipped = clip_box(scaled, target_size)
        if clipped is None:
            clipped = _boundary_box(scaled, target_size)
        mapped.append(
            MappedRegion(
                object_id=region.object_id,
                bbox=clipped,
                outside_page=outside,
            )
        )
    return tuple(sorted(mapped, key=lambda item: (item.object_id.casefold(), item.object_id)))


def _boundary_box(box: RenderPixelBox, size: tuple[int, int]) -> RenderPixelBox:
    width, height = size
    x = min(width - 1, max(0, box.x0))
    y = min(height - 1, max(0, box.y0))
    return RenderPixelBox(x0=x, y0=y, x1=x + 1, y1=y + 1)


def _pair_costs(
    reference: Sequence[ForegroundComponent],
    candidate: Sequence[ForegroundComponent],
    reference_ids: Sequence[tuple[str, ...]],
    candidate_ids: Sequence[tuple[str, ...]],
    *,
    page_size: tuple[int, int],
) -> tuple[list[list[float | None]], dict[tuple[int, int], tuple[float, float, float, float]]]:
    costs: list[list[float | None]] = []
    observations: dict[tuple[int, int], tuple[float, float, float, float]] = {}
    for ref_index, ref_component in enumerate(reference):
        row: list[float | None] = []
        for cand_index, cand_component in enumerate(candidate):
            values = component_similarity(ref_component, cand_component, page_size=page_size)
            shape, area, position, overlap = values
            observations[(ref_index, cand_index)] = values
            shared_ids = set(reference_ids[ref_index]) & set(candidate_ids[cand_index])
            eligible = (
                bool(shared_ids)
                or (shape >= 0.55 and area >= 0.50 and position >= 0.65)
                or (overlap >= 0.35 and shape >= 0.35 and area >= 0.35)
            )
            similarity = 0.50 * shape + 0.30 * area + 0.20 * position
            row.append(max(0.0, 1.0 - similarity) if eligible else None)
        costs.append(row)
    return costs, observations


def _page_localization(
    reference_source: Any,
    candidate_source: Any,
    *,
    page_number: int,
    reference_regions: Sequence[RenderedObjectRegion],
    candidate_regions: Sequence[RenderedObjectRegion],
    distance_tolerance: int,
    foreground_threshold: int,
) -> _PageLocalization:
    reference = foreground_page(reference_source, foreground_threshold=foreground_threshold)
    candidate = foreground_page(
        candidate_source,
        target_size=(reference.width, reference.height),
        foreground_threshold=foreground_threshold,
    )
    page_size = (reference.width, reference.height)
    mapped_reference = _map_regions(
        reference_regions,
        page_number=page_number,
        original_size=(reference.original_width, reference.original_height),
        target_size=page_size,
        reference=True,
    )
    mapped_candidate = _map_regions(
        candidate_regions,
        page_number=page_number,
        original_size=(candidate.original_width, candidate.original_height),
        target_size=page_size,
        reference=False,
    )
    missing_mask, extra_mask = difference_masks(
        reference.mask,
        candidate.mask,
        tolerance=distance_tolerance,
    )
    reference_components = extract_components(reference.mask)
    candidate_components = extract_components(candidate.mask)
    reference_ids = [
        _component_object_ids(component, mapped_reference) for component in reference_components
    ]
    candidate_ids = [
        _component_object_ids(component, mapped_candidate) for component in candidate_components
    ]
    costs, observations = _pair_costs(
        reference_components,
        candidate_components,
        reference_ids,
        candidate_ids,
        page_size=page_size,
    )
    assignment = minimum_cost_assignment(
        costs,
        candidate_count=len(candidate_components),
        unmatched_cost=0.27,
    )
    diagnostics = overflow_diagnostics(
        mapped_candidate,
        page_number=page_number,
        page_size=page_size,
    )
    for pair in assignment.pairs:
        ids = tuple(
            sorted(
                set(reference_ids[pair.reference_index]) | set(candidate_ids[pair.candidate_index]),
                key=str.casefold,
            )
        )
        diagnostics.extend(
            paired_diagnostics(
                reference_components[pair.reference_index],
                candidate_components[pair.candidate_index],
                page_number=page_number,
                page_size=page_size,
                values=observations[(pair.reference_index, pair.candidate_index)],
                missing_mask=missing_mask,
                extra_mask=extra_mask,
                object_ids=ids,
            )
        )
    total_reference = foreground_count(reference.mask)
    total_candidate = foreground_count(candidate.mask)
    diagnostics.extend(
        unmatched_diagnostic(
            reference_components[index],
            kind=RenderDiffKind.MISSING_REGION,
            page_number=page_number,
            page_size=page_size,
            total_foreground=total_reference,
            object_ids=reference_ids[index],
        )
        for index in assignment.unmatched_reference
    )
    diagnostics.extend(
        unmatched_diagnostic(
            candidate_components[index],
            kind=RenderDiffKind.EXTRA_REGION,
            page_number=page_number,
            page_size=page_size,
            total_foreground=total_candidate,
            object_ids=candidate_ids[index],
        )
        for index in assignment.unmatched_candidate
    )
    ordered = tuple(
        sorted(
            {diagnostic.diagnostic_id: diagnostic for diagnostic in diagnostics}.values(),
            key=lambda diagnostic: (
                diagnostic.page_number,
                diagnostic.kind.value,
                diagnostic.bbox.y0,
                diagnostic.bbox.x0,
                diagnostic.bbox.y1,
                diagnostic.bbox.x1,
                diagnostic.object_ids,
                diagnostic.diagnostic_id,
            ),
        )
    )
    summary = RenderDiffPageSummary(
        page_number=page_number,
        reference_width=reference.original_width,
        reference_height=reference.original_height,
        candidate_width=candidate.original_width,
        candidate_height=candidate.original_height,
        reference_foreground_pixels=total_reference,
        candidate_foreground_pixels=total_candidate,
        missing_difference_pixels=foreground_count(missing_mask),
        extra_difference_pixels=foreground_count(extra_mask),
        reference_components=len(reference_components),
        candidate_components=len(candidate_components),
    )
    return _PageLocalization(summary=summary, diagnostics=ordered)


def _report(
    *,
    reference_page_count: int,
    candidate_page_count: int,
    pages: Sequence[_PageLocalization],
) -> RenderDiffReport:
    diagnostics = tuple(diagnostic for page in pages for diagnostic in page.diagnostics)
    counts = Counter(diagnostic.kind.value for diagnostic in diagnostics)
    return RenderDiffReport(
        reference_page_count=reference_page_count,
        candidate_page_count=candidate_page_count,
        pages_compared=len(pages),
        page_summaries=tuple(page.summary for page in pages),
        diagnostics=diagnostics,
        diagnostic_counts={kind.value: counts[kind.value] for kind in RenderDiffKind},
        max_severity=max((diagnostic.severity for diagnostic in diagnostics), default=0.0),
    )


def localize_page_render_diff(
    reference: Any,
    candidate: Any,
    *,
    page_number: int = 1,
    reference_regions: Sequence[RenderedObjectRegion] = (),
    candidate_regions: Sequence[RenderedObjectRegion] = (),
    distance_tolerance: int = 2,
    foreground_threshold: int = 16,
) -> RenderDiffReport:
    """Localize one page without changing renderer output or acceptance policy."""

    if page_number < 1:
        raise ValueError("page_number must be positive")
    references = _validate_regions(reference_regions, page_count=page_number, label="reference")
    candidates = _validate_regions(candidate_regions, page_count=page_number, label="candidate")
    page = _page_localization(
        reference,
        candidate,
        page_number=page_number,
        reference_regions=references,
        candidate_regions=candidates,
        distance_tolerance=max(0, int(distance_tolerance)),
        foreground_threshold=foreground_threshold,
    )
    return _report(reference_page_count=1, candidate_page_count=1, pages=(page,))


def localize_render_differences(
    reference_pages: Sequence[Any],
    candidate_pages: Sequence[Any],
    *,
    reference_regions: Sequence[RenderedObjectRegion] = (),
    candidate_regions: Sequence[RenderedObjectRegion] = (),
    distance_tolerance: int = 2,
    foreground_threshold: int = 16,
) -> RenderDiffReport:
    """Localize a page sequence, keeping absent/extra pages in the report."""

    references = tuple(reference_pages)
    candidates = tuple(candidate_pages)
    reference_regions = _validate_regions(
        reference_regions,
        page_count=len(references),
        label="reference",
    )
    candidate_regions = _validate_regions(
        candidate_regions,
        page_count=len(candidates),
        label="candidate",
    )
    page_count = max(len(references), len(candidates))
    pages: list[_PageLocalization] = []
    for index in range(page_count):
        if index < len(references):
            reference = references[index]
            reference_size = image_source_size(reference)
        else:
            candidate_size = image_source_size(candidates[index])
            reference = blank_page(candidate_size)
            reference_size = candidate_size
        candidate = candidates[index] if index < len(candidates) else blank_page(reference_size)
        pages.append(
            _page_localization(
                reference,
                candidate,
                page_number=index + 1,
                reference_regions=reference_regions,
                candidate_regions=candidate_regions,
                distance_tolerance=max(0, int(distance_tolerance)),
                foreground_threshold=foreground_threshold,
            )
        )
    return _report(
        reference_page_count=len(references),
        candidate_page_count=len(candidates),
        pages=pages,
    )


__all__ = ["localize_page_render_diff", "localize_render_differences"]
