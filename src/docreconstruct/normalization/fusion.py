"""Element-level evidence fusion for independently normalized providers."""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from difflib import SequenceMatcher
from statistics import median
from typing import Any

from docreconstruct.ir import (
    BBox,
    Document,
    Element,
    ElementStyle,
    ElementType,
    Page,
    Provenance,
    Relationship,
    SourceType,
    TextCandidate,
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


def fuse_element_evidence(
    elements: Sequence[Element],
    *,
    element_id: str | None = None,
) -> Element:
    """Fuse corresponding provider elements without discarding hypotheses.

    Text is selected by confidence-weighted cross-engine consensus.  Every
    original text hypothesis remains in ``text_candidates`` and every source
    provenance remains in ``provenance.contributors``.
    """

    sources = list(elements)
    if not sources:
        raise ValueError("at least one element is required for evidence fusion")
    if len(sources) == 1 and element_id is None:
        # Return a deep copy so callers cannot accidentally mutate provider data.
        return sources[0].model_copy(deep=True)

    chosen_id = element_id or sources[0].id
    candidates = _text_candidates(sources)
    text = _choose_text(candidates)
    kind = _choose_type(sources)
    bbox = _fused_bbox(sources)
    confidence = _fused_confidence(sources)
    reading_orders = [
        element.reading_order for element in sources if element.reading_order is not None
    ]
    reading_order = int(round(median(reading_orders))) if reading_orders else None
    style = _fused_style(sources)
    relationships = _fused_relationships(sources)
    contributors = _provenance_contributors(sources)
    provenance = Provenance(
        engine="ensemble",
        source_id=chosen_id,
        text_confidence=_candidate_confidence(candidates, text),
        layout_confidence=confidence,
        metadata={"source_count": len(sources)},
        contributors=contributors,
    )
    metadata = _fused_metadata(sources)
    return Element(
        id=chosen_id,
        type=kind,
        bbox=bbox,
        text=text,
        reading_order=reading_order,
        confidence=confidence,
        style=style,
        relationships=relationships,
        provenance=provenance,
        text_candidates=candidates,
        metadata=metadata,
    )


# Short alias convenient in custom normalization pipelines.
fuse_elements = fuse_element_evidence


def fuse_pages(
    pages: Sequence[Page],
    *,
    page_id: str | None = None,
    iou_threshold: float = 0.5,
    text_similarity_threshold: float = 0.75,
) -> Page:
    """Cluster and fuse element evidence from corresponding source pages."""

    page_sources = list(pages)
    if not page_sources:
        raise ValueError("at least one page is required for evidence fusion")
    _validate_threshold("iou_threshold", iou_threshold)
    _validate_threshold("text_similarity_threshold", text_similarity_threshold)

    clusters: list[list[Element]] = []
    for page in page_sources:
        for element in page.elements:
            best_index: int | None = None
            best_score = -1.0
            for cluster_index, cluster in enumerate(clusters):
                score = _cluster_match_score(
                    element,
                    cluster,
                    iou_threshold=iou_threshold,
                    text_similarity_threshold=text_similarity_threshold,
                )
                if score is not None and score > best_score:
                    best_index = cluster_index
                    best_score = score
            if best_index is None:
                clusters.append([element])
            else:
                clusters[best_index].append(element)

    number = page_sources[0].number
    fused = [
        fuse_element_evidence(
            cluster,
            element_id=f"page-{number}-element-{cluster_index + 1}",
        )
        for cluster_index, cluster in enumerate(clusters)
    ]
    source_id_targets: dict[str, set[str]] = defaultdict(set)
    for fused_element, cluster in zip(fused, clusters, strict=True):
        for source_element in cluster:
            source_id_targets[source_element.id].add(fused_element.id)
    fused = [
        element.model_copy(
            update={
                "relationships": _remap_relationships(
                    element.relationships,
                    source_id_targets,
                )
            }
        )
        for element in fused
    ]
    fused.sort(
        key=lambda element: (
            element.reading_order if element.reading_order is not None else math.inf,
            element.bbox.y0,
            element.bbox.x0,
        )
    )
    fused = [
        element.model_copy(update={"reading_order": index}) for index, element in enumerate(fused)
    ]

    source_types = {page.source_type for page in page_sources}
    if SourceType.HYBRID in source_types or (
        SourceType.NATIVE in source_types and len(source_types - {SourceType.UNKNOWN}) > 1
    ):
        source_type = SourceType.HYBRID
    elif SourceType.NATIVE in source_types:
        source_type = SourceType.NATIVE
    elif SourceType.SCANNED in source_types:
        source_type = SourceType.SCANNED
    elif SourceType.IMAGE in source_types:
        source_type = SourceType.IMAGE
    else:
        source_type = SourceType.UNKNOWN

    widths = [page.width for page in page_sources]
    heights = [page.height for page in page_sources]
    rotations = [page.rotation for page in page_sources]
    page_metadata = _merge_preferred_metadata(page.metadata for page in page_sources)
    page_metadata["fusion"] = {
        "source_page_ids": [page.id for page in page_sources],
        "source_metadata": [page.metadata for page in page_sources],
        "iou_threshold": iou_threshold,
        "text_similarity_threshold": text_similarity_threshold,
    }
    return Page(
        id=page_id or page_sources[0].id,
        number=number,
        width=float(median(widths)),
        height=float(median(heights)),
        rotation=float(median(rotations)),
        elements=fused,
        source_type=source_type,
        metadata=page_metadata,
    )


def fuse_documents(
    documents: Sequence[Document],
    *,
    document_id: str | None = None,
    iou_threshold: float = 0.5,
    text_similarity_threshold: float = 0.75,
) -> Document:
    """Fuse normalized documents page-by-page and element-by-element."""

    sources = list(documents)
    if not sources:
        raise ValueError("at least one document is required for evidence fusion")
    _validate_threshold("iou_threshold", iou_threshold)
    _validate_threshold("text_similarity_threshold", text_similarity_threshold)

    by_number: dict[int, list[Page]] = defaultdict(list)
    for document in sources:
        for page in document.pages:
            by_number[page.number].append(page)
    pages = [
        fuse_pages(
            by_number[number],
            page_id=f"page-{number}",
            iou_threshold=iou_threshold,
            text_similarity_threshold=text_similarity_threshold,
        )
        for number in sorted(by_number)
    ]
    source_values = [document.source for document in sources if document.source]
    source = source_values[0] if source_values and len(set(source_values)) == 1 else None
    document_metadata = _merge_preferred_metadata(document.metadata for document in sources)
    document_metadata["fusion"] = {
        "source_document_ids": [document.id for document in sources],
        "sources": source_values,
        "source_metadata": [document.metadata for document in sources],
    }
    return Document(
        id=document_id or sources[0].id,
        pages=pages,
        source=source,
        metadata=document_metadata,
        schema_version=Document.CURRENT_SCHEMA_VERSION,
    )


@dataclass(frozen=True, slots=True)
class EvidenceFusion:
    """Configured object wrapper around the functional fusion API."""

    iou_threshold: float = 0.5
    text_similarity_threshold: float = 0.75

    def __post_init__(self) -> None:
        _validate_threshold("iou_threshold", self.iou_threshold)
        _validate_threshold("text_similarity_threshold", self.text_similarity_threshold)

    def fuse_elements(
        self,
        elements: Sequence[Element],
        *,
        element_id: str | None = None,
    ) -> Element:
        return fuse_element_evidence(elements, element_id=element_id)

    def fuse_pages(self, pages: Sequence[Page], *, page_id: str | None = None) -> Page:
        return fuse_pages(
            pages,
            page_id=page_id,
            iou_threshold=self.iou_threshold,
            text_similarity_threshold=self.text_similarity_threshold,
        )

    def fuse_documents(
        self,
        documents: Sequence[Document],
        *,
        document_id: str | None = None,
    ) -> Document:
        return fuse_documents(
            documents,
            document_id=document_id,
            iou_threshold=self.iou_threshold,
            text_similarity_threshold=self.text_similarity_threshold,
        )

    def fuse(
        self,
        documents: Sequence[Document],
        *,
        document_id: str | None = None,
    ) -> Document:
        return self.fuse_documents(documents, document_id=document_id)


def _text_candidates(elements: Sequence[Element]) -> list[TextCandidate]:
    candidates: list[TextCandidate] = []
    by_key: dict[tuple[str, str, str | None], int] = {}
    for element in elements:
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
            existing_index = by_key.get(key)
            if existing_index is None:
                by_key[key] = len(candidates)
                candidates.append(candidate.model_copy(deep=True))
            else:
                existing = candidates[existing_index]
                if (candidate.confidence or 0.0) > (existing.confidence or 0.0):
                    candidates[existing_index] = candidate.model_copy(deep=True)
    return candidates


def _choose_text(candidates: Sequence[TextCandidate]) -> str | None:
    if not candidates:
        return None
    groups: dict[str, list[TextCandidate]] = defaultdict(list)
    for candidate in candidates:
        groups[_normalized_text(candidate.value)].append(candidate)

    def group_score(group: list[TextCandidate]) -> tuple[int, float, float, int]:
        engines = {candidate.engine for candidate in group}
        confidences = [
            candidate.confidence if candidate.confidence is not None else 0.5 for candidate in group
        ]
        return (len(engines), sum(confidences), max(confidences), -len(group[0].value))

    winning_group = max(groups.values(), key=group_score)
    winner = max(
        enumerate(winning_group),
        key=lambda item: (
            item[1].confidence if item[1].confidence is not None else 0.5,
            -item[0],
        ),
    )[1]
    return winner.value


def _normalized_text(value: str) -> str:
    return " ".join(value.split()).casefold()


def _candidate_confidence(candidates: Sequence[TextCandidate], value: str | None) -> float | None:
    if value is None:
        return None
    normalized = _normalized_text(value)
    values = [
        candidate.confidence
        for candidate in candidates
        if _normalized_text(candidate.value) == normalized and candidate.confidence is not None
    ]
    return _combine_confidences(values)


def _choose_type(elements: Sequence[Element]) -> ElementType:
    votes: dict[ElementType, float] = defaultdict(float)
    for element in elements:
        weight = element.confidence if element.confidence is not None else 0.5
        if element.type is ElementType.UNKNOWN:
            weight *= 0.25
        votes[element.type] += weight
    return max(votes, key=lambda kind: (votes[kind], kind is not ElementType.UNKNOWN))


def _fused_bbox(elements: Sequence[Element]) -> BBox:
    weights = []
    for element in elements:
        weight = element.confidence if element.confidence is not None else 0.5
        if element.provenance and element.provenance.layout_confidence is not None:
            weight = element.provenance.layout_confidence
        if element.metadata.get("coordinate_system") == "full_page_fallback":
            weight *= 0.05
        weights.append(max(weight, 0.01))
    total = sum(weights)
    return BBox(
        x0=sum(element.bbox.x0 * weight for element, weight in zip(elements, weights, strict=True))
        / total,
        y0=sum(element.bbox.y0 * weight for element, weight in zip(elements, weights, strict=True))
        / total,
        x1=sum(element.bbox.x1 * weight for element, weight in zip(elements, weights, strict=True))
        / total,
        y1=sum(element.bbox.y1 * weight for element, weight in zip(elements, weights, strict=True))
        / total,
    )


def _fused_confidence(elements: Sequence[Element]) -> float | None:
    values = [element.confidence for element in elements if element.confidence is not None]
    return _combine_confidences(values)


def _combine_confidences(values: Sequence[float]) -> float | None:
    if not values:
        return None
    # Independent-evidence combination retains monotonicity without exceeding 1.
    miss_probability = 1.0
    for value in values:
        miss_probability *= 1.0 - max(0.0, min(1.0, value))
    return 1.0 - miss_probability


def _fused_style(elements: Sequence[Element]) -> ElementStyle:
    ordered = sorted(
        enumerate(elements),
        key=lambda item: (
            item[1].confidence if item[1].confidence is not None else 0.5,
            -item[0],
        ),
        reverse=True,
    )
    merged: dict[str, Any] = {}
    for _, element in ordered:
        for key, value in element.style.model_dump().items():
            if value is not None and key not in merged:
                merged[key] = value
    return ElementStyle.model_validate(merged)


def _fused_relationships(elements: Sequence[Element]) -> Relationship:
    ordered = sorted(
        elements,
        key=lambda element: element.confidence if element.confidence is not None else 0.5,
        reverse=True,
    )
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
        child for element in elements for child in element.relationships.children
    )
    references = _ordered_unique(
        reference for element in elements for reference in element.relationships.references
    )
    return Relationship(
        **singular,
        children=children,
        references=references,
        metadata={
            "source_metadata": [
                element.relationships.metadata
                for element in elements
                if element.relationships.metadata
            ]
        },
    )


def _ordered_unique(values: Any) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _provenance_contributors(elements: Sequence[Element]) -> list[Provenance]:
    result: list[Provenance] = []
    seen: set[str] = set()
    for element in elements:
        if element.provenance is None:
            provenance = Provenance(
                engine="unknown",
                source_id=element.id,
                text_confidence=element.confidence,
                layout_confidence=element.confidence,
            )
            records = [provenance]
        elif element.provenance.engine == "ensemble" and element.provenance.contributors:
            records = element.provenance.contributors
        else:
            records = [element.provenance]
        for record in records:
            key = record.model_dump_json()
            if key not in seen:
                seen.add(key)
                result.append(record.model_copy(deep=True))
    return result


def _fused_metadata(elements: Sequence[Element]) -> dict[str, Any]:
    ordered = sorted(
        elements,
        key=lambda element: element.confidence if element.confidence is not None else 0.5,
        reverse=True,
    )
    metadata = _merge_preferred_metadata(element.metadata for element in ordered)
    metadata["fusion"] = {
        "source_element_ids": [element.id for element in elements],
        "source_boxes": [element.bbox.model_dump() for element in elements],
        "source_metadata": [element.metadata for element in elements],
    }
    return metadata


def _merge_preferred_metadata(values: Any) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for metadata in values:
        if not isinstance(metadata, Mapping):
            continue
        for key, value in metadata.items():
            if key not in merged:
                merged[key] = deepcopy(value)
    return merged


def _remap_relationships(
    relationships: Relationship,
    source_id_targets: Mapping[str, set[str]],
) -> Relationship:
    def target(value: str | None) -> str | None:
        if value is None:
            return None
        candidates = source_id_targets.get(value, set())
        return next(iter(candidates)) if len(candidates) == 1 else value

    return Relationship(
        parent=target(relationships.parent),
        caption_of=target(relationships.caption_of),
        continued_from=target(relationships.continued_from),
        continued_to=target(relationships.continued_to),
        children=[target(value) or value for value in relationships.children],
        references=[target(value) or value for value in relationships.references],
        metadata=deepcopy(relationships.metadata),
    )


def _cluster_match_score(
    element: Element,
    cluster: Sequence[Element],
    *,
    iou_threshold: float,
    text_similarity_threshold: float,
) -> float | None:
    best: float | None = None
    for existing in cluster:
        if not _types_compatible(element.type, existing.type):
            continue
        overlap = element.bbox.iou(existing.bbox)
        if overlap < iou_threshold:
            continue
        similarity = _text_similarity(element.text, existing.text, element.type)
        if similarity < text_similarity_threshold:
            continue
        score = overlap * 0.6 + similarity * 0.4
        best = score if best is None else max(best, score)
    return best


def _types_compatible(left: ElementType, right: ElementType) -> bool:
    return (
        left == right
        or (left in _TEXT_TYPES and right in _TEXT_TYPES)
        or ElementType.UNKNOWN in {left, right}
    )


def _text_similarity(left: str | None, right: str | None, kind: ElementType) -> float:
    if left is None and right is None:
        return 1.0
    if left is None or right is None:
        return 0.0 if kind in _TEXT_TYPES else 1.0
    return SequenceMatcher(None, _normalized_text(left), _normalized_text(right)).ratio()


def _validate_threshold(name: str, value: float) -> None:
    if not 0 <= value <= 1:
        raise ValueError(f"{name} must be between 0 and 1")
