"""Order-invariant evidence fusion for independently normalized providers."""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Sequence
from copy import deepcopy
from dataclasses import dataclass
from statistics import median

from docreconstruct.ir import Document, Element, Page, Provenance, SourceType

from .fusion_clustering import FusionPageSource, cluster_page_elements
from .fusion_reduction import (
    candidate_confidence,
    canonical_elements,
    choose_text,
    choose_type,
    fused_bbox,
    fused_confidence,
    fused_metadata,
    fused_polygon,
    fused_relationships,
    fused_source_crop,
    fused_style,
    fused_z_index,
    merge_preferred_metadata,
    provenance_contributors,
    remap_relationships,
    text_candidates,
)
from .fusion_sources import (
    document_sort_key,
    document_source_identity,
    page_sort_key,
    page_sources,
)

DEFAULT_CANDIDATE_BUDGET = 100_000
DEFAULT_COMPARISON_BUDGET = 250_000
DEFAULT_ASSIGNMENT_BUDGET = 25_000


def fuse_element_evidence(
    elements: Sequence[Element],
    *,
    element_id: str | None = None,
) -> Element:
    """Reduce corresponding evidence without discarding canonical fields."""

    sources = canonical_elements(elements)
    if not sources:
        raise ValueError("at least one element is required for evidence fusion")
    if len(sources) == 1 and element_id is None:
        return sources[0].model_copy(deep=True)

    chosen_id = element_id or min(element.id for element in sources)
    candidates = text_candidates(sources)
    text = choose_text(candidates)
    confidence = fused_confidence(sources)
    reading_orders = [
        element.reading_order for element in sources if element.reading_order is not None
    ]
    provenance = Provenance(
        engine="ensemble",
        source_id=chosen_id,
        text_confidence=candidate_confidence(candidates, text),
        layout_confidence=confidence,
        metadata={"source_count": len(sources)},
        contributors=provenance_contributors(sources),
    )
    return Element(
        id=chosen_id,
        type=choose_type(sources),
        bbox=fused_bbox(sources),
        polygon=fused_polygon(sources),
        z_index=fused_z_index(sources),
        source_crop=fused_source_crop(sources),
        text=text,
        reading_order=int(round(median(reading_orders))) if reading_orders else None,
        confidence=confidence,
        style=fused_style(sources),
        relationships=fused_relationships(sources),
        provenance=provenance,
        text_candidates=candidates,
        metadata=fused_metadata(sources),
    )


fuse_elements = fuse_element_evidence


def fuse_pages(
    pages: Sequence[Page],
    *,
    page_id: str | None = None,
    iou_threshold: float = 0.5,
    text_similarity_threshold: float = 0.75,
    candidate_budget: int = DEFAULT_CANDIDATE_BUDGET,
    comparison_budget: int = DEFAULT_COMPARISON_BUDGET,
    assignment_budget: int = DEFAULT_ASSIGNMENT_BUDGET,
) -> Page:
    """Cluster and fuse corresponding pages deterministically and safely."""

    raw_pages = list(pages)
    if not raw_pages:
        raise ValueError("at least one page is required for evidence fusion")
    return _fuse_page_sources(
        page_sources(raw_pages),
        page_id=page_id,
        iou_threshold=iou_threshold,
        text_similarity_threshold=text_similarity_threshold,
        candidate_budget=candidate_budget,
        comparison_budget=comparison_budget,
        assignment_budget=assignment_budget,
    )


def _fuse_page_sources(
    sources: Sequence[FusionPageSource],
    *,
    page_id: str | None,
    iou_threshold: float,
    text_similarity_threshold: float,
    candidate_budget: int,
    comparison_budget: int,
    assignment_budget: int,
) -> Page:
    page_inputs = sorted(sources, key=lambda source: page_sort_key(source.page))
    if not page_inputs:
        raise ValueError("at least one page is required for evidence fusion")
    _validate_threshold("iou_threshold", iou_threshold)
    _validate_threshold("text_similarity_threshold", text_similarity_threshold)
    _validate_budget("candidate_budget", candidate_budget)
    _validate_budget("comparison_budget", comparison_budget)
    _validate_budget("assignment_budget", assignment_budget)

    clustering = cluster_page_elements(
        page_inputs,
        iou_threshold=iou_threshold,
        text_similarity_threshold=text_similarity_threshold,
        candidate_budget=candidate_budget,
        comparison_budget=comparison_budget,
        assignment_budget=assignment_budget,
    )
    clusters = clustering.clusters
    number = page_inputs[0].page.number
    fused_ids = [f"page-{number}-element-{index + 1}" for index in range(len(clusters))]
    source_id_targets: dict[tuple[str, str], set[str]] = defaultdict(set)
    for fused_id, cluster in zip(fused_ids, clusters, strict=True):
        for observation in cluster:
            source_id_targets[(observation.source_identity, observation.element.id)].add(fused_id)

    fused: list[Element] = []
    for fused_id, cluster in zip(fused_ids, clusters, strict=True):
        remapped = [
            observation.element.model_copy(
                update={
                    "relationships": remap_relationships(
                        observation.element.relationships,
                        source_id_targets,
                        source_identity=observation.source_identity,
                    )
                }
            )
            for observation in cluster
        ]
        element = fuse_element_evidence(remapped, element_id=fused_id)
        metadata = deepcopy(element.metadata)
        metadata["fusion"]["source_observations"] = [
            {
                "source_identity": observation.source_identity,
                "source_element_id": observation.element.id,
            }
            for observation in cluster
        ]
        fused.append(element.model_copy(update={"metadata": metadata}))

    fused.sort(
        key=lambda element: (
            element.reading_order if element.reading_order is not None else math.inf,
            element.bbox.y0,
            element.bbox.x0,
            element.bbox.y1,
            element.bbox.x1,
            element.id,
        )
    )
    fused = [
        element.model_copy(update={"reading_order": index}) for index, element in enumerate(fused)
    ]
    pages = [source.page for source in page_inputs]
    page_metadata = merge_preferred_metadata(page.metadata for page in pages)
    page_metadata["fusion"] = {
        "source_page_ids": [page.id for page in pages],
        "source_identities": [source.source_identity for source in page_inputs],
        "source_metadata": [deepcopy(page.metadata) for page in pages],
        "iou_threshold": iou_threshold,
        "text_similarity_threshold": text_similarity_threshold,
        "clustering": clustering.telemetry.as_dict(),
    }
    return Page(
        id=page_id or min(page.id for page in pages),
        number=number,
        width=float(median(page.width for page in pages)),
        height=float(median(page.height for page in pages)),
        rotation=float(median(page.rotation for page in pages)),
        elements=fused,
        source_type=_fused_source_type(pages),
        metadata=page_metadata,
    )


def fuse_documents(
    documents: Sequence[Document],
    *,
    document_id: str | None = None,
    iou_threshold: float = 0.5,
    text_similarity_threshold: float = 0.75,
    candidate_budget: int = DEFAULT_CANDIDATE_BUDGET,
    comparison_budget: int = DEFAULT_COMPARISON_BUDGET,
    assignment_budget: int = DEFAULT_ASSIGNMENT_BUDGET,
) -> Document:
    """Fuse normalized documents page-by-page and element-by-element."""

    sources = sorted(documents, key=document_sort_key)
    if not sources:
        raise ValueError("at least one document is required for evidence fusion")
    _validate_threshold("iou_threshold", iou_threshold)
    _validate_threshold("text_similarity_threshold", text_similarity_threshold)
    _validate_budget("candidate_budget", candidate_budget)
    _validate_budget("comparison_budget", comparison_budget)
    _validate_budget("assignment_budget", assignment_budget)
    identities = [(document, document_source_identity(document)) for document in sources]
    by_number: dict[int, list[FusionPageSource]] = defaultdict(list)
    for document, source_identity in identities:
        for page in document.pages:
            by_number[page.number].append(
                FusionPageSource(page=page, source_identity=source_identity)
            )
    pages = [
        _fuse_page_sources(
            by_number[number],
            page_id=f"page-{number}",
            iou_threshold=iou_threshold,
            text_similarity_threshold=text_similarity_threshold,
            candidate_budget=candidate_budget,
            comparison_budget=comparison_budget,
            assignment_budget=assignment_budget,
        )
        for number in sorted(by_number)
    ]
    pages = _remap_document_relationships(pages)
    source_values = sorted(document.source for document in sources if document.source)
    source = source_values[0] if source_values and len(set(source_values)) == 1 else None
    document_metadata = merge_preferred_metadata(document.metadata for document in sources)
    document_metadata["fusion"] = {
        "source_document_ids": [document.id for document in sources],
        "source_identities": [identity for _, identity in identities],
        "sources": source_values,
        "source_metadata": [deepcopy(document.metadata) for document in sources],
    }
    return Document(
        id=document_id or min(document.id for document in sources),
        pages=pages,
        source=source,
        metadata=document_metadata,
        schema_version=Document.CURRENT_SCHEMA_VERSION,
    )


def _remap_document_relationships(pages: Sequence[Page]) -> list[Page]:
    """Resolve relationship targets after every page has received fused IDs.

    Page-local fusion cannot resolve ``continued_from``/``continued_to`` (or
    other legitimate cross-page references) because the target page has not
    been assigned its fused IDs yet.  The source-observation audit metadata is
    sufficient to perform that remap once the whole document is available.
    """

    source_targets: dict[tuple[str, str], set[str]] = defaultdict(set)
    fused_ids = {element.id for page in pages for element in page.elements}
    for page in pages:
        for element in page.elements:
            for observation in _source_observations(element):
                source_targets[
                    (observation["source_identity"], observation["source_element_id"])
                ].add(element.id)

    remapped_pages: list[Page] = []
    for page in pages:
        remapped_elements: list[Element] = []
        for element in page.elements:
            identities = {
                observation["source_identity"] for observation in _source_observations(element)
            }

            def targets(
                value: str | None,
                source_identities: frozenset[str] = frozenset(identities),
            ) -> set[str]:
                if value is None:
                    return set()
                # The audit map is authoritative: a relationship names an
                # element of its own source document, so its recorded fused
                # target is the right answer even when the source id happens to
                # look like a fused one.  Fusion re-numbers elements by reading
                # order, and a source that already uses the
                # `page-{n}-element-{k}` namespace — any earlier fusion or
                # `analyze` output reloaded through the `json` provider — would
                # otherwise keep a stale id that now names a different element.
                resolved = {
                    target
                    for identity in source_identities
                    for target in source_targets.get((identity, value), set())
                }
                if resolved:
                    return resolved
                return {value} if value in fused_ids else set()

            def singular(value: str | None) -> str | None:
                candidates = targets(value)
                return next(iter(candidates)) if len(candidates) == 1 else value

            def plural(values: Sequence[str]) -> list[str]:
                resolved: list[str] = []
                for value in values:
                    candidates = targets(value)
                    resolved.extend(sorted(candidates) if candidates else [value])
                return list(dict.fromkeys(resolved))

            relationships = element.relationships
            remapped_elements.append(
                element.model_copy(
                    update={
                        "relationships": relationships.model_copy(
                            update={
                                "parent": singular(relationships.parent),
                                "caption_of": singular(relationships.caption_of),
                                "continued_from": singular(relationships.continued_from),
                                "continued_to": singular(relationships.continued_to),
                                "children": plural(relationships.children),
                                "references": plural(relationships.references),
                            },
                            deep=True,
                        )
                    },
                    deep=True,
                )
            )
        remapped_pages.append(page.model_copy(update={"elements": remapped_elements}, deep=True))
    return remapped_pages


def _source_observations(element: Element) -> list[dict[str, str]]:
    fusion = element.metadata.get("fusion")
    if not isinstance(fusion, dict):
        return []
    observations = fusion.get("source_observations")
    if not isinstance(observations, list):
        return []
    return [
        observation
        for observation in observations
        if isinstance(observation, dict)
        and isinstance(observation.get("source_identity"), str)
        and isinstance(observation.get("source_element_id"), str)
    ]


@dataclass(frozen=True, slots=True)
class EvidenceFusion:
    """Configured object wrapper around the functional fusion API."""

    iou_threshold: float = 0.5
    text_similarity_threshold: float = 0.75
    candidate_budget: int = DEFAULT_CANDIDATE_BUDGET
    comparison_budget: int = DEFAULT_COMPARISON_BUDGET
    assignment_budget: int = DEFAULT_ASSIGNMENT_BUDGET

    def __post_init__(self) -> None:
        _validate_threshold("iou_threshold", self.iou_threshold)
        _validate_threshold("text_similarity_threshold", self.text_similarity_threshold)
        _validate_budget("candidate_budget", self.candidate_budget)
        _validate_budget("comparison_budget", self.comparison_budget)
        _validate_budget("assignment_budget", self.assignment_budget)

    def fuse_elements(
        self, elements: Sequence[Element], *, element_id: str | None = None
    ) -> Element:
        return fuse_element_evidence(elements, element_id=element_id)

    def fuse_pages(self, pages: Sequence[Page], *, page_id: str | None = None) -> Page:
        return fuse_pages(
            pages,
            page_id=page_id,
            iou_threshold=self.iou_threshold,
            text_similarity_threshold=self.text_similarity_threshold,
            candidate_budget=self.candidate_budget,
            comparison_budget=self.comparison_budget,
            assignment_budget=self.assignment_budget,
        )

    def fuse_documents(
        self, documents: Sequence[Document], *, document_id: str | None = None
    ) -> Document:
        return fuse_documents(
            documents,
            document_id=document_id,
            iou_threshold=self.iou_threshold,
            text_similarity_threshold=self.text_similarity_threshold,
            candidate_budget=self.candidate_budget,
            comparison_budget=self.comparison_budget,
            assignment_budget=self.assignment_budget,
        )

    def fuse(self, documents: Sequence[Document], *, document_id: str | None = None) -> Document:
        return self.fuse_documents(documents, document_id=document_id)


def _fused_source_type(pages: Sequence[Page]) -> SourceType:
    source_types = {page.source_type for page in pages}
    if SourceType.HYBRID in source_types or (
        SourceType.NATIVE in source_types and len(source_types - {SourceType.UNKNOWN}) > 1
    ):
        return SourceType.HYBRID
    for source_type in (SourceType.NATIVE, SourceType.SCANNED, SourceType.IMAGE):
        if source_type in source_types:
            return source_type
    return SourceType.UNKNOWN


def _validate_threshold(name: str, value: float) -> None:
    if not 0 <= value <= 1:
        raise ValueError(f"{name} must be between 0 and 1")


def _validate_budget(name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")


__all__ = [
    "DEFAULT_ASSIGNMENT_BUDGET",
    "DEFAULT_CANDIDATE_BUDGET",
    "DEFAULT_COMPARISON_BUDGET",
    "EvidenceFusion",
    "fuse_documents",
    "fuse_element_evidence",
    "fuse_elements",
    "fuse_pages",
]
