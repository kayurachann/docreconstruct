"""Deterministic geometry helpers for page topology inference."""

from __future__ import annotations

import math
import statistics
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from docreconstruct.ir import BBox, Element, ElementType, Page

from .topology_models import PageRegion


def element_sort_key(element: Element) -> tuple[float, float, float, float, str, str]:
    """Canonical geometric order with an ID tie-break independent of input order."""

    return (
        element.bbox.y0,
        element.bbox.x0,
        element.bbox.y1,
        element.bbox.x1,
        element.type.value,
        element.id,
    )


def reading_sort_key(element: Element) -> tuple[int, int, float, float, str]:
    """Use explicit reading hints first and geometry for missing or tied hints."""

    if element.reading_order is None:
        return (1, 0, element.bbox.y0, element.bbox.x0, element.id)
    return (0, element.reading_order, element.bbox.y0, element.bbox.x0, element.id)


def union_bbox(elements: Iterable[Element]) -> BBox:
    """Return the positive-area union of a non-empty element collection."""

    boxes = [element.bbox for element in elements]
    if not boxes:
        raise ValueError("cannot create a region from no elements")
    x0 = min(box.x0 for box in boxes)
    y0 = min(box.y0 for box in boxes)
    x1 = max(box.x1 for box in boxes)
    y1 = max(box.y1 for box in boxes)
    epsilon = 1e-9
    if x1 <= x0:
        x1 = x0 + epsilon
    if y1 <= y0:
        y1 = y0 + epsilon
    return BBox(x0=x0, y0=y0, x1=x1, y1=y1)


def horizontal_overlap_ratio(left: BBox, right: BBox) -> float:
    """Intersection width divided by the narrower positive width."""

    denominator = min(left.width, right.width)
    if denominator <= 0:
        return 0.0
    overlap = max(0.0, min(left.x1, right.x1) - max(left.x0, right.x0))
    return min(1.0, overlap / denominator)


def vertical_overlap_ratio(top: BBox, bottom: BBox) -> float:
    denominator = min(top.height, bottom.height)
    if denominator <= 0:
        return 0.0
    overlap = max(0.0, min(top.y1, bottom.y1) - max(top.y0, bottom.y0))
    return min(1.0, overlap / denominator)


def normalized_metadata_role(element: Element) -> str:
    """Return a conservative explicit layout role from common metadata fields."""

    for key in ("region_kind", "layout_role", "semantic_role", "role"):
        value = element.metadata.get(key)
        if isinstance(value, str):
            return value.strip().casefold().replace("-", "_").replace(" ", "_")
    if element.metadata.get("floating") is True:
        return "floating"
    return ""


@dataclass(frozen=True)
class ColumnClustering:
    """Flow elements partitioned into one to three lanes and optional spans."""

    groups: tuple[tuple[Element, ...], ...]
    spanning: tuple[Element, ...]
    confidence: float
    used_reading_hints: bool

    @property
    def column_count(self) -> int:
        return len(self.groups)


def _initial_centroids(values: Sequence[float], count: int) -> list[float]:
    size = len(values)
    return [
        values[min(size - 1, ((2 * index + 1) * size) // (2 * count))] for index in range(count)
    ]


def _fit_centers(elements: Sequence[Element], count: int) -> tuple[tuple[Element, ...], ...] | None:
    ordered = tuple(sorted(elements, key=lambda item: (item.bbox.center_x, element_sort_key(item))))
    centroids = _initial_centroids([item.bbox.center_x for item in ordered], count)
    assignments: list[int] = []
    for _ in range(32):
        assignments = [
            min(range(count), key=lambda index: (abs(item.bbox.center_x - centroids[index]), index))
            for item in ordered
        ]
        if len(set(assignments)) != count:
            return None
        updated = [
            statistics.fmean(
                item.bbox.center_x
                for item, assignment in zip(ordered, assignments, strict=True)
                if assignment == index
            )
            for index in range(count)
        ]
        if all(math.isclose(a, b, abs_tol=1e-9) for a, b in zip(updated, centroids, strict=True)):
            break
        centroids = updated
    raw_groups = [
        tuple(
            item
            for item, assignment in zip(ordered, assignments, strict=True)
            if assignment == index
        )
        for index in range(count)
    ]
    groups = sorted(
        raw_groups, key=lambda group: statistics.fmean(item.bbox.center_x for item in group)
    )
    return tuple(tuple(sorted(group, key=reading_sort_key)) for group in groups)


def _clustering_quality(groups: tuple[tuple[Element, ...], ...], page: Page) -> float | None:
    if any(len(group) < 2 for group in groups):
        return None
    centers = [statistics.fmean(item.bbox.center_x for item in group) for group in groups]
    gaps = [(right - left) / page.width for left, right in zip(centers, centers[1:], strict=False)]
    if not gaps or min(gaps) < 0.16:
        return None
    dispersions = [
        max(abs(item.bbox.center_x - center) for item in group) / page.width
        for group, center in zip(groups, centers, strict=True)
    ]
    if max(dispersions) > 0.09:
        return None
    group_boxes = [union_bbox(group) for group in groups]
    continuities = [
        vertical_overlap_ratio(left, right)
        for left, right in zip(group_boxes, group_boxes[1:], strict=False)
    ]
    if min(continuities, default=1.0) < 0.12:
        return None
    all_items = [item for group in groups for item in group]
    global_center = statistics.fmean(item.bbox.center_x for item in all_items)
    total_sse = sum((item.bbox.center_x - global_center) ** 2 for item in all_items)
    clustered_sse = sum(
        (item.bbox.center_x - center) ** 2
        for group, center in zip(groups, centers, strict=True)
        for item in group
    )
    improvement = 1.0 if total_sse == 0 else 1.0 - clustered_sse / total_sse
    minimum_improvement = 0.70 if len(groups) == 2 else 0.82
    if improvement < minimum_improvement:
        return None
    gutter_scores = []
    for left, right in zip(groups, groups[1:], strict=False):
        left_edge = statistics.median(item.bbox.x1 for item in left)
        right_edge = statistics.median(item.bbox.x0 for item in right)
        gutter_scores.append(max(0.0, right_edge - left_edge) / page.width)
    compactness = max(0.0, 1.0 - max(dispersions) / 0.09)
    gutter = min(1.0, min(gutter_scores, default=0.0) / 0.04)
    return min(1.0, 0.50 * improvement + 0.30 * compactness + 0.20 * gutter)


def _wide_spanning_candidates(elements: Sequence[Element], page: Page) -> tuple[Element, ...]:
    if len(elements) < 5:
        return ()
    widths = [item.bbox.width for item in elements if item.bbox.width > 0]
    if not widths:
        return ()
    median_width = statistics.median(widths)
    candidates = []
    for item in elements:
        relative_width = item.bbox.width / page.width
        heading = item.type in {ElementType.TITLE, ElementType.HEADING}
        threshold = 1.20 if heading else 1.40
        if (
            relative_width >= (0.52 if heading else 0.62)
            and item.bbox.width >= threshold * median_width
        ):
            candidates.append(item)
    return tuple(sorted(candidates, key=reading_sort_key))


def cluster_page_columns(elements: Sequence[Element], page: Page) -> ColumnClustering:
    """Infer a deterministic 1/2/3-column partition from geometry and hints."""

    ordered = tuple(sorted(elements, key=element_sort_key))
    if not ordered:
        return ColumnClustering(groups=(), spanning=(), confidence=1.0, used_reading_hints=False)
    possible_spans = _wide_spanning_candidates(ordered, page)
    span_ids = {item.id for item in possible_spans}
    candidates = tuple(item for item in ordered if item.id not in span_ids)
    chosen: tuple[tuple[Element, ...], ...] | None = None
    confidence = 0.0
    for count in (3, 2):
        if len(candidates) < 2 * count:
            continue
        groups = _fit_centers(candidates, count)
        if groups is None:
            continue
        quality = _clustering_quality(groups, page)
        if quality is not None:
            chosen = groups
            confidence = quality
            break
    if chosen is None:
        group = tuple(sorted(ordered, key=reading_sort_key))
        return ColumnClustering(
            groups=(group,),
            spanning=(),
            confidence=0.75,
            used_reading_hints=any(item.reading_order is not None for item in group),
        )
    hinted = [item for group in chosen for item in group if item.reading_order is not None]
    used_hints = len(hinted) >= max(2, len(candidates) // 2)
    if used_hints:
        by_id = {item.id: index for index, group in enumerate(chosen) for item in group}
        hint_order = sorted(hinted, key=lambda item: (item.reading_order, element_sort_key(item)))
        transitions = sum(
            by_id[left.id] != by_id[right.id]
            for left, right in zip(hint_order, hint_order[1:], strict=False)
        )
        if transitions <= len(chosen):
            confidence = min(1.0, confidence + 0.05)
    return ColumnClustering(
        groups=chosen,
        spanning=possible_spans,
        confidence=confidence,
        used_reading_hints=used_hints,
    )


def split_column_at_blockers(
    elements: Sequence[Element], blockers: Sequence[PageRegion]
) -> tuple[tuple[Element, ...], ...]:
    """Split one lane where full/same-lane semantic regions interrupt its flow."""

    if not elements:
        return ()
    ordered_blockers = tuple(sorted(blockers, key=lambda item: (item.bbox.center_y, item.id)))
    partitions: dict[int, list[Element]] = {}
    for element in sorted(elements, key=reading_sort_key):
        segment = sum(
            blocker.bbox.center_y < element.bbox.center_y
            and horizontal_overlap_ratio(element.bbox, blocker.bbox) >= 0.20
            for blocker in ordered_blockers
        )
        partitions.setdefault(segment, []).append(element)
    return tuple(
        tuple(sorted(partitions[index], key=reading_sort_key)) for index in sorted(partitions)
    )


__all__ = [
    "ColumnClustering",
    "cluster_page_columns",
    "element_sort_key",
    "horizontal_overlap_ratio",
    "normalized_metadata_role",
    "reading_sort_key",
    "split_column_at_blockers",
    "union_bbox",
    "vertical_overlap_ratio",
]
