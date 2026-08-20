"""Order-invariant reducers used after evidence clustering."""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any

from docreconstruct.ir import (
    BBox,
    Element,
    ElementStyle,
    ElementType,
    Point,
    Provenance,
    Relationship,
    TextCandidate,
)

from .fusion_clustering import normalized_text, provider_sort_key


def canonical_elements(elements: Sequence[Element]) -> list[Element]:
    """Return elements in a stable order suitable for deterministic reduction."""

    return sorted(elements, key=element_sort_key)


def element_sort_key(element: Element) -> tuple[object, ...]:
    provenance = element.provenance
    return (
        provider_sort_key(provenance.engine) if provenance else (-1, ""),
        provenance.source_id or "" if provenance else "",
        element.reading_order if element.reading_order is not None else float("inf"),
        element.bbox.y0,
        element.bbox.x0,
        element.bbox.y1,
        element.bbox.x1,
        element.type.value,
        normalized_text(element.text or ""),
        element.id,
        _canonical_model_json(element),
    )


def preferred_elements(elements: Sequence[Element]) -> list[Element]:
    """Order observations by confidence, breaking ties canonically."""

    return sorted(
        elements,
        key=lambda element: (
            -(element.confidence if element.confidence is not None else 0.5),
            element_sort_key(element),
        ),
    )


def geometry_preferred_elements(elements: Sequence[Element]) -> list[Element]:
    """Order geometry by layout confidence, then complete canonical content."""

    return sorted(
        elements,
        key=lambda element: (
            -(
                element.provenance.layout_confidence
                if element.provenance and element.provenance.layout_confidence is not None
                else element.confidence
                if element.confidence is not None
                else 0.5
            ),
            element_sort_key(element),
        ),
    )


def text_candidates(elements: Sequence[Element]) -> list[TextCandidate]:
    candidates: dict[tuple[str, str, str | None], TextCandidate] = {}
    for element in canonical_elements(elements):
        element_candidates = list(element.text_candidates)
        if element.text is not None:
            engine = element.provenance.engine if element.provenance else "unknown"
            if not any(
                candidate.engine == engine and candidate.value == element.text
                for candidate in element_candidates
            ):
                element_candidates.append(
                    TextCandidate(
                        engine=engine,
                        value=element.text,
                        confidence=(
                            element.provenance.text_confidence
                            if element.provenance and element.provenance.text_confidence is not None
                            else element.confidence
                        ),
                        source_element_id=element.id,
                    )
                )
        for candidate in element_candidates:
            key = (candidate.engine, candidate.value, candidate.source_element_id)
            existing = candidates.get(key)
            if existing is None or _candidate_preference(candidate) < _candidate_preference(
                existing
            ):
                candidates[key] = candidate.model_copy(deep=True)
    return sorted(candidates.values(), key=_candidate_sort_key)


def choose_text(candidates: Sequence[TextCandidate]) -> str | None:
    if not candidates:
        return None
    groups: dict[str, list[TextCandidate]] = defaultdict(list)
    for candidate in candidates:
        groups[normalized_text(candidate.value)].append(candidate)

    def group_preference(item: tuple[str, list[TextCandidate]]) -> tuple[object, ...]:
        normalized, group = item
        engines = {candidate.engine for candidate in group}
        confidences = [
            candidate.confidence if candidate.confidence is not None else 0.5 for candidate in group
        ]
        return (
            -len(engines),
            -sum(confidences),
            -max(confidences),
            min(len(candidate.value) for candidate in group),
            normalized,
        )

    winning_group = min(groups.items(), key=group_preference)[1]
    return min(winning_group, key=_candidate_preference).value


def candidate_confidence(candidates: Sequence[TextCandidate], value: str | None) -> float | None:
    if value is None:
        return None
    normalized = normalized_text(value)
    values = [
        candidate.confidence
        for candidate in candidates
        if normalized_text(candidate.value) == normalized and candidate.confidence is not None
    ]
    return combine_confidences(values)


def choose_type(elements: Sequence[Element]) -> ElementType:
    votes: dict[ElementType, float] = defaultdict(float)
    for element in canonical_elements(elements):
        weight = element.confidence if element.confidence is not None else 0.5
        if element.type is ElementType.UNKNOWN:
            weight *= 0.25
        votes[element.type] += weight
    return min(
        votes,
        key=lambda kind: (
            -votes[kind],
            kind is ElementType.UNKNOWN,
            kind.value,
        ),
    )


def fused_bbox(elements: Sequence[Element]) -> BBox:
    sources = canonical_elements(elements)
    weights: list[float] = []
    for element in sources:
        weight = element.confidence if element.confidence is not None else 0.5
        if element.provenance and element.provenance.layout_confidence is not None:
            weight = element.provenance.layout_confidence
        if element.metadata.get("coordinate_system") == "full_page_fallback":
            weight *= 0.05
        weights.append(max(weight, 0.01))
    total = sum(weights)
    return BBox(
        x0=sum(element.bbox.x0 * weight for element, weight in zip(sources, weights, strict=True))
        / total,
        y0=sum(element.bbox.y0 * weight for element, weight in zip(sources, weights, strict=True))
        / total,
        x1=sum(element.bbox.x1 * weight for element, weight in zip(sources, weights, strict=True))
        / total,
        y1=sum(element.bbox.y1 * weight for element, weight in zip(sources, weights, strict=True))
        / total,
    )


def fused_confidence(elements: Sequence[Element]) -> float | None:
    values = [element.confidence for element in elements if element.confidence is not None]
    return combine_confidences(values)


def fused_polygon(elements: Sequence[Element]) -> list[Point]:
    """Select the best supported non-empty polygon deterministically."""

    for element in geometry_preferred_elements(elements):
        if element.polygon:
            return [point.model_copy(deep=True) for point in element.polygon]
    return []


def fused_source_crop(elements: Sequence[Element]) -> BBox | None:
    """Select the best supported source crop deterministically."""

    for element in geometry_preferred_elements(elements):
        if element.source_crop is not None:
            return element.source_crop.model_copy(deep=True)
    return None


def fused_z_index(elements: Sequence[Element]) -> int:
    """Choose a confidence-weighted z-index consensus with a stable tie."""

    votes: dict[int, float] = defaultdict(float)
    for element in canonical_elements(elements):
        votes[element.z_index] += element.confidence if element.confidence is not None else 0.5
    return min(votes, key=lambda value: (-votes[value], value))


def combine_confidences(values: Sequence[float]) -> float | None:
    if not values:
        return None
    miss_probability = 1.0
    for value in sorted(values):
        miss_probability *= 1.0 - max(0.0, min(1.0, value))
    return 1.0 - miss_probability


def fused_style(elements: Sequence[Element]) -> ElementStyle:
    merged: dict[str, Any] = {}
    for element in preferred_elements(elements):
        for key, value in element.style.model_dump().items():
            if value is not None and key not in merged:
                merged[key] = value
    return ElementStyle.model_validate(merged)


def fused_relationships(elements: Sequence[Element]) -> Relationship:
    ordered = preferred_elements(elements)
    singular: dict[str, str | None] = {}
    for key in ("parent", "caption_of", "continued_from", "continued_to"):
        singular[key] = next(
            (
                getattr(element.relationships, key)
                for element in ordered
                if getattr(element.relationships, key)
            ),
            None,
        )
    children = _ordered_unique(
        child for element in ordered for child in element.relationships.children
    )
    references = _ordered_unique(
        reference for element in ordered for reference in element.relationships.references
    )
    source_metadata = sorted(
        (
            deepcopy(element.relationships.metadata)
            for element in elements
            if element.relationships.metadata
        ),
        key=_canonical_json,
    )
    return Relationship(
        **singular,
        children=children,
        references=references,
        metadata={"source_metadata": source_metadata},
    )


def provenance_contributors(elements: Sequence[Element]) -> list[Provenance]:
    """Retain each top-level provenance record, including nested ensembles."""

    records: dict[str, Provenance] = {}
    for element in canonical_elements(elements):
        if element.provenance is None:
            element_records = [
                Provenance(
                    engine="unknown",
                    source_id=element.id,
                    text_confidence=element.confidence,
                    layout_confidence=element.confidence,
                )
            ]
        else:
            element_records = [element.provenance]
        for record in element_records:
            key = _canonical_model_json(record)
            records.setdefault(key, record.model_copy(deep=True))
    return [records[key] for key in sorted(records)]


def fused_metadata(elements: Sequence[Element]) -> dict[str, Any]:
    sources = canonical_elements(elements)
    metadata = merge_preferred_metadata(element.metadata for element in preferred_elements(sources))
    metadata["fusion"] = {
        "source_element_ids": [element.id for element in sources],
        "source_boxes": [element.bbox.model_dump() for element in sources],
        "source_metadata": [deepcopy(element.metadata) for element in sources],
    }
    return metadata


def merge_preferred_metadata(values: Any) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for metadata in values:
        if not isinstance(metadata, Mapping):
            continue
        for key, value in metadata.items():
            if key not in merged:
                merged[key] = deepcopy(value)
    return merged


def remap_relationships(
    relationships: Relationship,
    source_id_targets: Mapping[tuple[str, str], set[str]],
    *,
    source_identity: str,
) -> Relationship:
    def target(value: str | None) -> str | None:
        if value is None:
            return None
        candidates = source_id_targets.get((source_identity, value), set())
        return min(candidates) if len(candidates) == 1 else value

    return Relationship(
        parent=target(relationships.parent),
        caption_of=target(relationships.caption_of),
        continued_from=target(relationships.continued_from),
        continued_to=target(relationships.continued_to),
        children=[target(value) or value for value in relationships.children],
        references=[target(value) or value for value in relationships.references],
        metadata=deepcopy(relationships.metadata),
    )


def _ordered_unique(values: Any) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _candidate_sort_key(candidate: TextCandidate) -> tuple[object, ...]:
    return (
        normalized_text(candidate.value),
        candidate.engine.casefold(),
        candidate.source_element_id or "",
        candidate.value,
        -(candidate.confidence if candidate.confidence is not None else 0.5),
        _canonical_model_json(candidate),
    )


def _candidate_preference(candidate: TextCandidate) -> tuple[object, ...]:
    return (
        -(candidate.confidence if candidate.confidence is not None else 0.5),
        candidate.value,
        candidate.engine.casefold(),
        candidate.source_element_id or "",
        _canonical_model_json(candidate),
    )


def _canonical_model_json(value: Any) -> str:
    return _canonical_json(value.model_dump(mode="json"))


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


__all__ = [
    "candidate_confidence",
    "canonical_elements",
    "choose_text",
    "choose_type",
    "element_sort_key",
    "fused_bbox",
    "fused_confidence",
    "fused_metadata",
    "fused_polygon",
    "fused_relationships",
    "fused_style",
    "fused_source_crop",
    "fused_z_index",
    "geometry_preferred_elements",
    "merge_preferred_metadata",
    "preferred_elements",
    "provenance_contributors",
    "remap_relationships",
    "text_candidates",
]
