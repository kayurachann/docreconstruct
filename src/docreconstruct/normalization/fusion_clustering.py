"""Deterministic, bounded, provider-constrained evidence clustering."""

from __future__ import annotations

import json
import math
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from statistics import median
from typing import Any

from docreconstruct.ir import Element, ElementType, Page, Provenance

from .fusion_assignment import (
    AssignmentBudgetExceeded,
    maximum_cardinality_score_sparse_assignment,
)
from .fusion_spatial import (
    SpatialClusterIndex,
    SpatialIndexBudgetExceeded,
    SpatialQueryBudgetExceeded,
    cluster_envelope,
)

_TEXT_TYPES = {
    ElementType.TEXT,
    ElementType.TITLE,
    ElementType.HEADING,
    ElementType.PARAGRAPH,
    ElementType.LIST_ITEM,
    ElementType.CAPTION,
    ElementType.HEADER,
    ElementType.FOOTER,
    ElementType.FOOTNOTE,
    ElementType.PAGE_NUMBER,
}
_BUILTIN_PROVIDER_ORDER = {
    name: index
    for index, name in enumerate(
        (
            "paddleocr",
            "mineru",
            "olmocr",
            "mistral_ocr",
            "azure_document_intelligence",
            "mathpix",
            "google_document_ai",
            "aws_textract",
            "tesseract_local",
            "native_pdf",
            "json",
        )
    )
}
_MAX_SEQUENCE_MATCHER_CHARS = 2_048


@dataclass(frozen=True, slots=True)
class FusionPageSource:
    """A page plus its stable identity within one source document."""

    page: Page
    source_identity: str


@dataclass(frozen=True, slots=True)
class FusionObservation:
    """One element labelled with source and recursively flattened providers."""

    source_identity: str
    logical_providers: frozenset[str]
    page_id: str
    element: Element


FusionCluster = tuple[FusionObservation, ...]


@dataclass(slots=True)
class FusionTelemetry:
    candidate_budget: int
    comparison_budget: int
    assignment_budget: int
    candidate_pairs: int = 0
    spatial_index_entries: int = 0
    spatial_grid_x: int = 0
    spatial_grid_y: int = 0
    spatially_pruned_pairs: int = 0
    element_comparisons: int = 0
    assignment_cells: int = 0
    budget_exhausted: bool = False
    fallback_reason: str | None = None
    over_split_elements: int = 0

    def as_dict(self) -> dict[str, int | bool | str | None]:
        return {
            "candidate_budget": self.candidate_budget,
            "comparison_budget": self.comparison_budget,
            "assignment_budget": self.assignment_budget,
            "candidate_pairs": self.candidate_pairs,
            "spatial_index_entries": self.spatial_index_entries,
            "spatial_grid_x": self.spatial_grid_x,
            "spatial_grid_y": self.spatial_grid_y,
            "spatially_pruned_pairs": self.spatially_pruned_pairs,
            "element_comparisons": self.element_comparisons,
            "assignment_cells": self.assignment_cells,
            "budget_exhausted": self.budget_exhausted,
            "fallback_reason": self.fallback_reason,
            "over_split_elements": self.over_split_elements,
        }


@dataclass(frozen=True, slots=True)
class FusionClusteringResult:
    clusters: tuple[FusionCluster, ...]
    telemetry: FusionTelemetry


def cluster_page_elements(
    sources: list[FusionPageSource],
    *,
    iou_threshold: float,
    text_similarity_threshold: float,
    candidate_budget: int,
    comparison_budget: int,
    assignment_budget: int,
) -> FusionClusteringResult:
    """Return bounded complete-link clusters with no logical-provider overlap."""

    page_numbers = {source.page.number for source in sources}
    if len(page_numbers) != 1:
        raise ValueError("source pages must have the same page number")
    telemetry = FusionTelemetry(candidate_budget, comparison_budget, assignment_budget)
    by_source = _observations_by_source(sources)
    source_order = sorted(by_source, key=lambda key: _source_sort_key(key, by_source[key]))
    clusters: list[list[FusionObservation]] = []
    page_width = float(median(source.page.width for source in sources))
    page_height = float(median(source.page.height for source in sources))

    for source_position, source_identity in enumerate(source_order):
        observations = sorted(by_source[source_identity], key=observation_sort_key)
        assignments, exhausted = _assign_source(
            observations,
            clusters,
            telemetry=telemetry,
            page_width=page_width,
            page_height=page_height,
            iou_threshold=iou_threshold,
            text_similarity_threshold=text_similarity_threshold,
        )
        if exhausted:
            remaining = source_order[source_position:]
            for remaining_source in remaining:
                remaining_observations = sorted(
                    by_source[remaining_source], key=observation_sort_key
                )
                clusters.extend([[observation] for observation in remaining_observations])
                telemetry.over_split_elements += len(remaining_observations)
            break
        assigned: set[int] = set()
        for observation_index, cluster_index in assignments:
            clusters[cluster_index].append(observations[observation_index])
            assigned.add(observation_index)
        clusters.extend(
            [observation] for index, observation in enumerate(observations) if index not in assigned
        )

    canonical = [tuple(sorted(cluster, key=observation_sort_key)) for cluster in clusters]
    canonical.sort(key=cluster_sort_key)
    return FusionClusteringResult(tuple(canonical), telemetry)


def logical_provider_set(
    provenance: Provenance | None,
    *,
    source_identity: str,
) -> frozenset[str]:
    """Flatten nested ensembles to their leaf provider engines."""

    if provenance is None:
        return frozenset({f"unknown-source:{source_identity}"})

    def visit(record: Provenance, ancestors: frozenset[int]) -> set[str]:
        marker = id(record)
        if marker in ancestors:
            return {f"cycle:{record.engine.casefold()}"}
        if record.engine.casefold() == "ensemble" and record.contributors:
            providers: set[str] = set()
            for contributor in record.contributors:
                providers.update(visit(contributor, ancestors | {marker}))
            return providers
        return {record.engine.casefold()}

    return frozenset(visit(provenance, frozenset()))


def observation_sort_key(observation: FusionObservation) -> tuple[object, ...]:
    element = observation.element
    return (
        tuple(provider_sort_key(provider) for provider in sorted(observation.logical_providers)),
        observation.source_identity,
        element.reading_order if element.reading_order is not None else float("inf"),
        element.bbox.y0,
        element.bbox.x0,
        element.bbox.y1,
        element.bbox.x1,
        element.type.value,
        normalized_text(element.text or ""),
        element.id,
        _canonical_json(element.model_dump(mode="json")),
    )


def cluster_sort_key(cluster: FusionCluster) -> tuple[object, ...]:
    elements = [observation.element for observation in cluster]
    reading_orders = [
        element.reading_order for element in elements if element.reading_order is not None
    ]
    return (
        median(reading_orders) if reading_orders else float("inf"),
        median(element.bbox.y0 for element in elements),
        median(element.bbox.x0 for element in elements),
        median(element.bbox.y1 for element in elements),
        median(element.bbox.x1 for element in elements),
        tuple(observation_sort_key(observation) for observation in cluster),
    )


def provider_sort_key(provider: str) -> tuple[int, str]:
    name = provider.casefold()
    return (_BUILTIN_PROVIDER_ORDER.get(name, len(_BUILTIN_PROVIDER_ORDER)), name)


def _observations_by_source(
    sources: list[FusionPageSource],
) -> dict[str, list[FusionObservation]]:
    by_source: dict[str, list[FusionObservation]] = {}
    for source in sources:
        for element in source.page.elements:
            by_source.setdefault(source.source_identity, []).append(
                FusionObservation(
                    source_identity=source.source_identity,
                    logical_providers=logical_provider_set(
                        element.provenance, source_identity=source.source_identity
                    ),
                    page_id=source.page.id,
                    element=element,
                )
            )
    return by_source


def _source_sort_key(
    source_identity: str,
    observations: list[FusionObservation],
) -> tuple[object, ...]:
    providers = sorted(
        {provider for observation in observations for provider in observation.logical_providers},
        key=provider_sort_key,
    )
    return (tuple(provider_sort_key(provider) for provider in providers), source_identity)


def _assign_source(
    observations: list[FusionObservation],
    clusters: list[list[FusionObservation]],
    *,
    telemetry: FusionTelemetry,
    page_width: float,
    page_height: float,
    iou_threshold: float,
    text_similarity_threshold: float,
) -> tuple[list[tuple[int, int]], bool]:
    if not observations or not clusters:
        return [], False
    envelopes = [cluster_envelope([item.element.bbox for item in cluster]) for cluster in clusters]
    remaining_candidate_work = (
        telemetry.candidate_budget - telemetry.candidate_pairs - telemetry.spatial_index_entries
    )
    grid_x, grid_y = _spatial_resolution(len(clusters), page_width, page_height)
    telemetry.spatial_grid_x = max(telemetry.spatial_grid_x, grid_x)
    telemetry.spatial_grid_y = max(telemetry.spatial_grid_y, grid_y)
    try:
        spatial = SpatialClusterIndex(
            envelopes,
            page_width=page_width,
            page_height=page_height,
            x_resolution=grid_x,
            y_resolution=grid_y,
            entry_budget=remaining_candidate_work,
        )
    except SpatialIndexBudgetExceeded as exc:
        telemetry.spatial_index_entries += exc.consumed_entries
        return [], _exhaust(telemetry, "candidate_budget")
    telemetry.spatial_index_entries += spatial.entry_count
    candidates: list[list[int]] = []
    for observation in observations:
        remaining_candidate_work = (
            telemetry.candidate_budget - telemetry.spatial_index_entries - telemetry.candidate_pairs
        )
        try:
            indices = spatial.query(
                observation.element.bbox,
                result_budget=remaining_candidate_work,
            )
        except SpatialQueryBudgetExceeded:
            telemetry.candidate_pairs += remaining_candidate_work
            return [], _exhaust(telemetry, "candidate_budget")
        telemetry.candidate_pairs += len(indices)
        if iou_threshold > 0:
            indices = [
                index
                for index in indices
                if observation.element.bbox.intersection(envelopes[index]) is not None
            ]
        telemetry.spatially_pruned_pairs += len(clusters) - len(indices)
        candidates.append(indices)

    scores: dict[tuple[int, int], float] = {}
    for observation_index, observation in enumerate(observations):
        for cluster_index in candidates[observation_index]:
            comparisons = len(clusters[cluster_index])
            if telemetry.element_comparisons + comparisons > telemetry.comparison_budget:
                return [], _exhaust(telemetry, "comparison_budget")
            telemetry.element_comparisons += comparisons
            score = _complete_link_score(
                observation,
                clusters[cluster_index],
                iou_threshold=iou_threshold,
                text_similarity_threshold=text_similarity_threshold,
            )
            if score is not None:
                scores[(observation_index, cluster_index)] = score
    if not scores:
        return [], False
    remaining_assignment_cells = telemetry.assignment_budget - telemetry.assignment_cells
    if remaining_assignment_cells <= 0:
        return [], _exhaust(telemetry, "assignment_budget")
    try:
        matches, assignment_cells = maximum_cardinality_score_sparse_assignment(
            len(observations),
            scores,
            cell_budget=remaining_assignment_cells,
        )
    except AssignmentBudgetExceeded:
        return [], _exhaust(telemetry, "assignment_budget")
    telemetry.assignment_cells += assignment_cells
    return matches, False


def _exhaust(telemetry: FusionTelemetry, reason: str) -> bool:
    telemetry.budget_exhausted = True
    telemetry.fallback_reason = reason
    return True


def _spatial_resolution(
    cluster_count: int,
    page_width: float,
    page_height: float,
) -> tuple[int, int]:
    target_cells = max(64, cluster_count * 4)
    aspect = max(0.0001, min(10_000.0, page_width / page_height))
    x_resolution = math.ceil(math.sqrt(target_cells * aspect))
    y_resolution = math.ceil(math.sqrt(target_cells / aspect))
    return (max(4, min(4_096, x_resolution)), max(4, min(4_096, y_resolution)))


def _complete_link_score(
    observation: FusionObservation,
    cluster: list[FusionObservation],
    *,
    iou_threshold: float,
    text_similarity_threshold: float,
) -> float | None:
    if any(
        member.source_identity == observation.source_identity
        or member.logical_providers & observation.logical_providers
        for member in cluster
    ):
        return None
    scores = [
        _pair_match_score(
            observation.element,
            member.element,
            iou_threshold=iou_threshold,
            text_similarity_threshold=text_similarity_threshold,
        )
        for member in cluster
    ]
    if not scores or any(score is None for score in scores):
        return None
    compatible_scores = [score for score in scores if score is not None]
    return min(compatible_scores)


def _pair_match_score(
    left: Element,
    right: Element,
    *,
    iou_threshold: float,
    text_similarity_threshold: float,
) -> float | None:
    if not _types_compatible(left.type, right.type):
        return None
    overlap = left.bbox.iou(right.bbox)
    if overlap < iou_threshold:
        return None
    similarity = _text_similarity(left.text, right.text, left.type, right.type)
    if similarity < text_similarity_threshold:
        return None
    return overlap * 0.6 + similarity * 0.4


def _types_compatible(left: ElementType, right: ElementType) -> bool:
    return (
        left == right
        or (left in _TEXT_TYPES and right in _TEXT_TYPES)
        or ElementType.UNKNOWN in {left, right}
    )


def _text_similarity(
    left: str | None,
    right: str | None,
    left_type: ElementType,
    right_type: ElementType,
) -> float:
    if left is None and right is None:
        return 1.0
    if left is None or right is None:
        return 0.0 if {left_type, right_type} & _TEXT_TYPES else 1.0
    normalized_left = normalized_text(left)
    normalized_right = normalized_text(right)
    if normalized_left == normalized_right:
        return 1.0
    if max(len(normalized_left), len(normalized_right)) > _MAX_SEQUENCE_MATCHER_CHARS:
        return 0.0
    forward = SequenceMatcher(None, normalized_left, normalized_right).ratio()
    reverse = SequenceMatcher(None, normalized_right, normalized_left).ratio()
    return min(forward, reverse)


def normalized_text(value: str) -> str:
    """Fold a text value into a provider-independent comparison key.

    Engines disagree about Unicode composition.  The same Vietnamese heading
    arrives precomposed from one provider and decomposed from another — 58
    code points against 76 for an identical reading — and comparing the raw
    strings scores them at 0.69, under the 0.75 clustering threshold, so the
    paragraph is emitted twice instead of corroborated once.  Folding to NFKC
    first matches what ``evidence_matching`` already does for the same reason.

    The result is only ever a comparison key.  The text that reaches the
    document is still a provider's own value, unfolded.
    """

    return " ".join(unicodedata.normalize("NFKC", value).split()).casefold()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


__all__ = [
    "FusionCluster",
    "FusionClusteringResult",
    "FusionObservation",
    "FusionPageSource",
    "cluster_page_elements",
    "cluster_sort_key",
    "logical_provider_set",
    "normalized_text",
    "observation_sort_key",
    "provider_sort_key",
]
