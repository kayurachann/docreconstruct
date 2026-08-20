"""Privacy-safe candidate observations shared by alignment diagnostics."""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import docreconstruct.reconstruction.evidence_matching as matching
from docreconstruct.reconstruction.markdown_content import MarkdownBlock, MarkdownBlockKind

from .models import AlignmentCandidateTrace, AlignmentReason, canonical_reasons
from .privacy import (
    canonical_sha256,
    opaque_block_id,
    opaque_candidate_id,
    opaque_element_id,
    opaque_source_id,
)


@dataclass(frozen=True, slots=True)
class ObservedCandidate:
    source_id: str
    candidate: Any
    element_ids: tuple[str, ...]

    @property
    def identity(self) -> tuple[str, int, int, int, tuple[str, ...]]:
        return (
            self.source_id,
            self.candidate.page_number,
            self.candidate.start,
            self.candidate.end,
            self.element_ids,
        )


def source_id(document: Any) -> str:
    """Derive an opaque identifier without hashing OCR or authority content.

    Text is deliberately absent from this identity.  Short labels and numeric
    fields have a small enough search space that an unsalted content digest
    would otherwise be vulnerable to dictionary recovery in a public report.
    Geometry, type, reading order, and an already-opaque digest of the raw
    element identifier are sufficient to keep the report deterministic.
    """

    pages = []
    for page in document.pages:
        elements = sorted(
            canonical_sha256(
                {
                    "raw_id_sha256": canonical_sha256(element.id),
                    "type": element.type.value,
                    "bbox": element.bbox.model_dump(mode="json"),
                    "polygon": [point.model_dump(mode="json") for point in element.polygon],
                    "source_crop": (
                        element.source_crop.model_dump(mode="json")
                        if element.source_crop is not None
                        else None
                    ),
                    "z_index": element.z_index,
                    "reading_order": element.reading_order,
                }
            )
            for element in page.elements
        )
        pages.append(
            {
                "number": page.number,
                "width": page.width,
                "height": page.height,
                "rotation": page.rotation,
                "elements": elements,
            }
        )
    payload = {
        "pages": sorted(pages, key=lambda page: (page["number"], canonical_sha256(page))),
    }
    return opaque_source_id(payload)


def unit_element_id(source_identifier: str, unit: Any) -> str:
    identity = canonical_sha256(
        {
            "raw_id_sha256": canonical_sha256(unit.element_id),
            "page": unit.page_number,
            "type": unit.element_type.value,
            "bbox": unit.bbox.model_dump(mode="json"),
        }
    )
    return opaque_element_id(source_identifier, unit.page_number, identity)


def candidate_element_ids(
    source_identifier: str, candidate: Any, units: Sequence[Any]
) -> tuple[str, ...]:
    by_position = {unit.position: unit for unit in units}
    return tuple(
        sorted(
            {
                unit_element_id(source_identifier, by_position[position])
                for position in range(candidate.start, candidate.end)
                if position in by_position
            }
        )
    )


def accepted_trace(
    block: MarkdownBlock,
    observed: ObservedCandidate,
    *,
    reasons: Sequence[AlignmentReason] = (),
    order_score: float = 1.0,
) -> AlignmentCandidateTrace:
    candidate = observed.candidate
    return AlignmentCandidateTrace(
        candidate_id=opaque_candidate_id(
            block_id=opaque_block_id(block.index),
            element_ids=observed.element_ids,
            page_number=candidate.page_number,
            source_id=observed.source_id,
            start=candidate.start,
            end=candidate.end,
            origin="accepted" if not reasons else "accepted_not_selected",
        ),
        block_id=opaque_block_id(block.index),
        element_ids=observed.element_ids,
        page_number=candidate.page_number,
        text_score=candidate.text_score,
        geometry_score=1.0,
        type_score=candidate.type_score,
        order_score=order_score,
        total_score=candidate.match_score,
        rejection_reasons=canonical_reasons(tuple(reasons)),
    )


def _span_trace(
    block: MarkdownBlock,
    source_identifier: str,
    span: Any,
    *,
    text_score: float,
    total_score: float,
    type_score: float,
    reasons: Sequence[AlignmentReason],
    origin: str,
) -> AlignmentCandidateTrace:
    element_ids = tuple(sorted({unit_element_id(source_identifier, unit) for unit in span.units}))
    return AlignmentCandidateTrace(
        candidate_id=opaque_candidate_id(
            block_id=opaque_block_id(block.index),
            element_ids=element_ids,
            page_number=span.page_number,
            source_id=source_identifier,
            start=span.start,
            end=span.end,
            origin=origin,
        ),
        block_id=opaque_block_id(block.index),
        element_ids=element_ids,
        page_number=span.page_number,
        text_score=text_score,
        geometry_score=1.0,
        type_score=type_score,
        order_score=1.0,
        total_score=total_score,
        rejection_reasons=canonical_reasons(tuple(reasons)),
    )


def _score_span(
    block: MarkdownBlock, target: str, span: Any, workspace: Any
) -> tuple[float, float, float]:
    text_score = workspace.similarity(target, span.normalized_text)
    type_score = matching._type_score(block.kind, [unit.element_type for unit in span.units])
    confidences = [unit.confidence for unit in span.units if unit.confidence is not None]
    confidence = math.fsum(confidences) / len(confidences) if confidences else 0.5
    total_score = min(1.0, 0.82 * text_score + 0.13 * type_score + 0.05 * confidence)
    return text_score, type_score, total_score


def text_inventory(
    block: MarkdownBlock,
    source_identifier: str,
    candidate_index: Any,
    units: Sequence[Any],
    workspace: Any,
    *,
    exhaustive: bool,
    retained_count: int,
    top_n: int,
) -> tuple[int, list[AlignmentCandidateTrace], list[AlignmentCandidateTrace], bool]:
    target = workspace.normalize(
        matching._block_text(block),
        formula=block.kind is MarkdownBlockKind.EQUATION,
    )
    spans = (
        candidate_index.exhaustive_spans_for(block)
        if exhaustive
        else candidate_index.spans_for(block)
    )
    if not target:
        return len(spans), [], [], False
    below: list[AlignmentCandidateTrace] = []
    accepted_count = retained_count
    if retained_count == 0 or retained_count >= matching._MAX_CANDIDATES_PER_BLOCK:
        accepted_count = 0
        for span in spans:
            if not matching._plausible_start(
                target,
                span.units[0].normalized_text,
                len(candidate_index._spans_by_start),
                workspace,
            ):
                continue
            candidate = matching._span_candidate(
                block,
                target,
                span.start,
                span.end,
                span.units,
                workspace,
                candidate_text=span.normalized_text,
            )
            if candidate is not None:
                accepted_count += 1
                continue
            if retained_count:
                continue
            text_score, type_score, total_score = _score_span(block, target, span, workspace)
            reasons = [AlignmentReason.TEXT_BELOW_THRESHOLD]
            if type_score < 0.62:
                reasons.append(AlignmentReason.TYPE_CONFLICT)
            below.append(
                _span_trace(
                    block,
                    source_identifier,
                    span,
                    text_score=text_score,
                    total_score=total_score,
                    type_score=type_score,
                    reasons=reasons,
                    origin="below_threshold",
                )
            )
    span_limited = _overlong_span_traces(block, source_identifier, units, target, workspace)
    return (
        len(spans),
        top_traces(below, top_n),
        top_traces(span_limited, top_n),
        accepted_count > matching._MAX_CANDIDATES_PER_BLOCK,
    )


def _overlong_span_traces(
    block: MarkdownBlock,
    source_identifier: str,
    units: Sequence[Any],
    target: str,
    workspace: Any,
) -> list[AlignmentCandidateTrace]:
    maximum = matching._maximum_span(block)
    traces: list[AlignmentCandidateTrace] = []
    for start in range(len(units)):
        if units[start].element_type in matching._VISUAL_TYPES:
            continue
        for length in range(maximum + 1, min(len(units) - start, maximum + 4) + 1):
            span_units = tuple(units[start : start + length])
            if any(
                unit.page_number != span_units[0].page_number
                or unit.element_type in matching._VISUAL_TYPES
                for unit in span_units
            ):
                break
            span = matching._TextSpan(
                start=start,
                end=start + length,
                page_number=span_units[0].page_number,
                normalized_text=" ".join(unit.normalized_text for unit in span_units),
                units=span_units,
            )
            text_score, type_score, total_score = _score_span(block, target, span, workspace)
            if text_score < matching._text_threshold(block, target) or total_score < 0.62:
                continue
            traces.append(
                _span_trace(
                    block,
                    source_identifier,
                    span,
                    text_score=text_score,
                    total_score=total_score,
                    type_score=type_score,
                    reasons=(AlignmentReason.SPAN_LIMIT_REACHED,),
                    origin="span_limit",
                )
            )
    return traces


def unsafe_geometry_traces(
    source: Any,
    blocks: Sequence[MarkdownBlock],
    scan_pages: Mapping[int, Any],
    workspace: Any,
    top_n: int,
) -> tuple[dict[str, list[AlignmentCandidateTrace]], dict[str, int]]:
    """Attribute similar raw elements that page binding or projection rejected."""

    source_identifier = source_id(source.document)
    bindings = matching._bind_document_pages(source.document, scan_pages)
    by_page_identity = {id(binding.page): binding for binding in bindings}
    rejected: dict[str, list[AlignmentCandidateTrace]] = defaultdict(list)
    considered: dict[str, int] = defaultdict(int)
    raw_position = 0
    pages = sorted(source.document.pages, key=lambda page: (page.number, canonical_sha256(page.id)))
    for page in pages:
        binding = by_page_identity.get(id(page))
        elements_by_id = {element.id: element for element in page.elements}
        elements = sorted(
            page.elements,
            key=lambda element: canonical_sha256(element.model_dump(mode="json")),
        )
        for element in elements:
            if matching._redundant_child(element, elements_by_id):
                continue
            normalized = workspace.normalize(
                matching._element_text(element),
                formula=element.type.value == "formula",
            )
            if not normalized:
                continue
            reasons: list[AlignmentReason] = []
            page_number = max(1, page.number)
            if binding is None:
                reasons.append(AlignmentReason.PAGE_CONFLICT)
            else:
                page_number = binding.scan_page.number
                ignored = (
                    matching._coordinate_system(page.metadata)
                    in matching._IGNORED_COORDINATE_SYSTEMS
                    or matching._coordinate_system(element.metadata)
                    in matching._IGNORED_COORDINATE_SYSTEMS
                )
                projected = (
                    None
                    if ignored
                    else matching._project_page_box(binding.scan_page, page, element.bbox)
                )
                if (
                    projected is None
                    or projected.x1 <= projected.x0
                    or projected.y1 <= projected.y0
                ):
                    reasons.extend(
                        (AlignmentReason.UNSAFE_GEOMETRY, AlignmentReason.PROJECTION_INVALID)
                    )
            if not reasons:
                continue
            element_identity = canonical_sha256(element.model_dump(mode="json"))
            opaque_element = opaque_element_id(source_identifier, page_number, element_identity)
            for block in blocks:
                if block.kind is MarkdownBlockKind.IMAGE:
                    continue
                target = workspace.normalize(
                    matching._block_text(block),
                    formula=block.kind is MarkdownBlockKind.EQUATION,
                )
                if not target:
                    continue
                text_score = workspace.similarity(target, normalized)
                threshold = matching._text_threshold(block, target)
                if text_score < max(0.45, threshold - 0.12):
                    continue
                considered[block.id] += 1
                type_score = matching._type_score(block.kind, [element.type])
                candidate_reasons = list(reasons)
                if type_score < 0.62:
                    candidate_reasons.append(AlignmentReason.TYPE_CONFLICT)
                confidence = matching._element_confidence(element)
                total_score = min(
                    1.0,
                    0.82 * text_score
                    + 0.13 * type_score
                    + 0.05 * (confidence if confidence is not None else 0.5),
                )
                rejected[block.id].append(
                    AlignmentCandidateTrace(
                        candidate_id=opaque_candidate_id(
                            block_id=opaque_block_id(block.index),
                            element_ids=(opaque_element,),
                            page_number=page_number,
                            source_id=source_identifier,
                            start=raw_position,
                            end=raw_position + 1,
                            origin="projection_rejected",
                        ),
                        block_id=opaque_block_id(block.index),
                        element_ids=(opaque_element,),
                        page_number=page_number,
                        text_score=text_score,
                        geometry_score=0.0,
                        type_score=type_score,
                        order_score=0.0,
                        total_score=total_score,
                        rejection_reasons=canonical_reasons(candidate_reasons),
                    )
                )
            raw_position += 1
    return (
        {block_id: top_traces(traces, top_n) for block_id, traces in rejected.items()},
        dict(considered),
    )


def top_traces(
    traces: Sequence[AlignmentCandidateTrace], top_n: int
) -> list[AlignmentCandidateTrace]:
    unique = {trace.candidate_id: trace for trace in traces}
    return sorted(
        unique.values(),
        key=lambda trace: (
            -trace.total_score,
            -trace.text_score,
            -trace.geometry_score,
            -trace.type_score,
            -trace.order_score,
            trace.page_number,
            trace.element_ids,
            trace.candidate_id,
        ),
    )[:top_n]


__all__ = [
    "ObservedCandidate",
    "accepted_trace",
    "candidate_element_ids",
    "source_id",
    "text_inventory",
    "top_traces",
    "unsafe_geometry_traces",
]
