"""Deterministic alignment of canonical OCR evidence to Markdown and scan geometry.

Markdown remains the content authority.  Provider documents contribute only
validated positions, confidence, and style hints; this module never invokes a
provider, reads a sidecar, or performs network access.
"""

from __future__ import annotations

import difflib
import html
import math
import re
import unicodedata
from bisect import bisect_right
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any, Literal, Protocol, TypeAlias
from urllib.parse import unquote, urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from docreconstruct.ir import BBox, Document, Element, ElementStyle, ElementType, Page
from docreconstruct.reconstruction.markdown_content import (
    MarkdownBlock,
    MarkdownBlockKind,
    MarkdownContent,
)
from docreconstruct.reconstruction.math_omml import latex_visible_text
from docreconstruct.reconstruction.scan_layout import (
    PixelBox,
    ScanDocumentLayout,
    ScanPageLayout,
    project_source_box_to_scan,
)

if TYPE_CHECKING:
    from docreconstruct.reconstruction.alignment.models import AlignmentReport


class _SidecarEvidenceItemLike(Protocol):
    @property
    def document(self) -> Document | None: ...

    @property
    def provider(self) -> str | None: ...

    @property
    def path(self) -> object: ...

    @property
    def warnings(self) -> tuple[str, ...]: ...


class _SidecarEvidenceBundleLike(Protocol):
    @property
    def items(self) -> Sequence[_SidecarEvidenceItemLike]: ...


EvidenceDocuments: TypeAlias = _SidecarEvidenceBundleLike | Document | Sequence[Document]

_MAX_SPAN = 8
_MAX_CANDIDATES_PER_BLOCK = 256
_GEOMETRY_AGREEMENT = 0.42
_VISUAL_TYPES = frozenset({ElementType.IMAGE, ElementType.FIGURE, ElementType.CHART})
_IGNORED_COORDINATE_SYSTEMS = {
    "full_page_fallback",
    "synthetic_reading_order_only",
    "unavailable",
}


class EvidenceAlignmentReason(StrEnum):
    """Stable reason codes for a failed or degraded evidence alignment."""

    MATCHED = "matched"
    NO_EVIDENCE_DOCUMENTS = "no_evidence_documents"
    NO_MARKDOWN_BLOCKS = "no_markdown_blocks"
    NO_LAYOUT_PAGES = "no_layout_pages"
    NO_BOUND_EVIDENCE_PAGES = "no_bound_evidence_pages"
    PROJECTION_ASPECT_MISMATCH = "projection_aspect_mismatch"
    PROJECTION_REJECTED = "projection_rejected"
    NO_SAFE_TEXT_CANDIDATES = "no_safe_text_candidates"


class EvidenceAlignmentDiagnostics(BaseModel):
    """Counted, non-mutating explanation of one strict alignment attempt."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    reason_codes: tuple[EvidenceAlignmentReason, ...]
    markdown_blocks: int = Field(ge=0)
    evidence_documents: int = Field(ge=0)
    evidence_pages: int = Field(ge=0)
    bound_pages: int = Field(ge=0)
    eligible_elements: int = Field(ge=0)
    textual_elements: int = Field(ge=0)
    visual_elements: int = Field(ge=0)
    boxes_outside_declared_page: int = Field(ge=0)
    projection_aspect_mismatches: int = Field(ge=0)
    projection_rejections: int = Field(ge=0)
    projected_units: int = Field(ge=0)
    aligned_candidates: int = Field(ge=0)
    matched_blocks: int = Field(ge=0)


def _reject_coerced_pixel_box(value: Any) -> None:
    if not isinstance(value, Mapping):
        return
    for field_name in ("x0", "y0", "x1", "y1"):
        coordinate = value.get(field_name)
        if coordinate is not None and (
            not isinstance(coordinate, int) or isinstance(coordinate, bool)
        ):
            raise ValueError(f"{field_name} must be a strict integer pixel coordinate")


class EvidenceMatch(BaseModel):
    """One Markdown block aligned to consensus scan-pixel evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    block_id: str = Field(min_length=1)
    block_index: int = Field(ge=0)
    page_number: int = Field(ge=1)
    source_bbox: PixelBox
    source_rows: list[PixelBox] = Field(default_factory=list)
    match_score: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    providers: tuple[str, ...] = ()
    element_ids: tuple[str, ...] = ()
    style: ElementStyle | None = None
    style_metadata: dict[str, Any] = Field(default_factory=dict)
    geometry_source: Literal["json_consensus"] = "json_consensus"
    conflict: bool = False
    warnings: list[str] = Field(default_factory=list)

    @field_validator("source_bbox", mode="before")
    @classmethod
    def source_bbox_must_use_strict_pixels(cls, value: Any) -> Any:
        _reject_coerced_pixel_box(value)
        return value

    @field_validator("source_rows", mode="before")
    @classmethod
    def source_rows_must_use_strict_pixels(cls, value: Any) -> Any:
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            for row in value:
                _reject_coerced_pixel_box(row)
        return value

    @model_validator(mode="after")
    def pixel_boxes_must_have_positive_area(self) -> EvidenceMatch:
        boxes = (self.source_bbox, *self.source_rows)
        if any(box.x1 <= box.x0 or box.y1 <= box.y0 for box in boxes):
            raise ValueError("evidence pixel boxes must have positive area")
        return self

    @property
    def bbox(self) -> PixelBox:
        """Compatibility name for callers that do not use planner terminology."""

        return self.source_bbox

    @property
    def score(self) -> float:
        """Compatibility name for the normalized match score."""

        return self.match_score


@dataclass(frozen=True, slots=True)
class _DocumentSource:
    document: Document
    provider_hint: str | None
    key: str
    warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _PageBinding:
    """One provider page bound to the corresponding layout page."""

    page: Page
    scan_page: ScanPageLayout
    warning: str | None = None


@dataclass(frozen=True, slots=True)
class _Unit:
    position: int
    page_number: int
    element_id: str
    text: str
    normalized_text: str
    element_type: ElementType
    bbox: PixelBox
    confidence: float | None
    style: ElementStyle | None
    provider: str
    source_key: str
    visual_keys: tuple[str, ...]
    reading_order_reliable: bool
    warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _Candidate:
    block: MarkdownBlock
    start: int
    end: int
    page_number: int
    bbox: PixelBox
    source_rows: tuple[PixelBox, ...]
    text_score: float
    type_score: float
    match_score: float
    confidence: float | None
    provider: str
    source_key: str
    element_ids: tuple[str, ...]
    style: ElementStyle | None
    warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _Node:
    candidate: _Candidate
    previous: _Node | None


@dataclass(frozen=True, slots=True)
class _State:
    total_score: float
    match_count: int
    node: _Node | None


@dataclass(frozen=True, slots=True)
class _TextFeatures:
    characters: Counter[str]
    tokens: Counter[str]
    token_count: int


@dataclass(slots=True)
class _MatchWorkspace:
    """Per-invocation text caches released when one match job completes."""

    normalized: dict[tuple[str, bool], str]
    features: dict[str, _TextFeatures]
    similarities: dict[tuple[str, str], float]
    thresholded_similarities: dict[tuple[str, str, float], float | None]

    @classmethod
    def create(cls) -> _MatchWorkspace:
        return cls(normalized={}, features={}, similarities={}, thresholded_similarities={})

    def normalize(self, value: str, *, formula: bool = False) -> str:
        key = (value, formula)
        normalized = self.normalized.get(key)
        if normalized is None:
            normalized = _normalize_text(value, formula=formula)
            self.normalized[key] = normalized
        return normalized

    def text_features(self, value: str) -> _TextFeatures:
        features = self.features.get(value)
        if features is None:
            tokens = Counter(value.split())
            features = _TextFeatures(
                characters=Counter(value),
                tokens=tokens,
                token_count=sum(tokens.values()),
            )
            self.features[value] = features
        return features

    def similarity(self, left: str, right: str) -> float:
        pair = _canonical_text_pair(left, right)
        cached = self.similarities.get(pair)
        if cached is None:
            cached = _cached_text_similarity(*pair)
            self.similarities[pair] = cached
        return cached

    def similarity_at_least(
        self,
        left: str,
        right: str,
        minimum: float,
    ) -> float | None:
        left, right = _canonical_text_pair(left, right)
        key = (left, right, minimum)
        if key in self.thresholded_similarities:
            return self.thresholded_similarities[key]
        result = _text_similarity_at_least_uncached(left, right, minimum, self)
        self.thresholded_similarities[key] = result
        return result


@dataclass(frozen=True, slots=True)
class _TextSpan:
    start: int
    end: int
    page_number: int
    normalized_text: str
    units: tuple[_Unit, ...]


@dataclass(frozen=True, slots=True)
class _ExactAnchor:
    block_index: int
    span: _TextSpan


class _TextCandidateIndex:
    """Pre-normalized spans plus conservative exact/monotonic search windows."""

    __slots__ = ("_anchors", "_exact", "_spans_by_start", "_unit_count", "_windows")

    def __init__(
        self,
        blocks: Sequence[MarkdownBlock],
        units: Sequence[_Unit],
        workspace: _MatchWorkspace,
    ) -> None:
        spans_by_start: list[list[_TextSpan]] = [[] for _unit in units]
        exact: dict[str, list[_TextSpan]] = defaultdict(list)
        for start, unit in enumerate(units):
            if unit.element_type in _VISUAL_TYPES:
                continue
            span_units: list[_Unit] = []
            fragments: list[str] = []
            for end in range(start, min(len(units), start + _MAX_SPAN)):
                next_unit = units[end]
                if (
                    next_unit.page_number != unit.page_number
                    or next_unit.element_type in _VISUAL_TYPES
                ):
                    break
                span_units.append(next_unit)
                fragments.append(next_unit.normalized_text)
                span = _TextSpan(
                    start=start,
                    end=end + 1,
                    page_number=unit.page_number,
                    normalized_text=" ".join(fragments),
                    units=tuple(span_units),
                )
                spans_by_start[start].append(span)
                exact[span.normalized_text].append(span)
                workspace.text_features(span.normalized_text)

        anchors: list[_ExactAnchor] = []
        last_end = 0
        for block in blocks:
            if block.kind is MarkdownBlockKind.IMAGE:
                continue
            target = workspace.normalize(
                _block_text(block),
                formula=block.kind is MarkdownBlockKind.EQUATION,
            )
            matches = [
                span
                for span in exact.get(target, ())
                if span.end - span.start <= _maximum_span(block)
            ]
            # Greedy document order is intentional.  A provider can expose a
            # second column earlier in its element list; accepting a later
            # longest-subsequence alternative would move the alignment across
            # that column boundary.  Conflicting exact spans remain fuzzy
            # fallback territory instead of becoming anchors.
            if len(matches) == 1 and matches[0].start >= last_end:
                anchors.append(_ExactAnchor(block_index=block.index, span=matches[0]))
                last_end = matches[0].end

        windows: dict[str, tuple[int, int]] = {}
        # One anchor cannot establish a bounded interval, so retain the exact
        # exhaustive behavior for sparse/ambiguous inputs.
        active_anchors = anchors if len(anchors) >= 2 else []
        for block in blocks:
            previous = next(
                (anchor for anchor in reversed(active_anchors) if anchor.block_index < block.index),
                None,
            )
            following = next(
                (anchor for anchor in active_anchors if anchor.block_index > block.index),
                None,
            )
            windows[block.id] = (
                previous.span.end if previous is not None else 0,
                following.span.start if following is not None else len(units),
            )

        self._anchors = tuple(active_anchors)
        self._exact = {key: tuple(value) for key, value in exact.items()}
        self._spans_by_start = tuple(tuple(value) for value in spans_by_start)
        self._unit_count = len(units)
        self._windows = windows

    @property
    def anchors(self) -> tuple[_ExactAnchor, ...]:
        return self._anchors

    def spans_for(self, block: MarkdownBlock) -> list[_TextSpan]:
        lower, upper = self._windows.get(block.id, (0, self._unit_count))
        maximum_span = _maximum_span(block)
        return [
            span
            for start in range(lower, min(upper, self._unit_count))
            for span in self._spans_by_start[start]
            if span.end <= upper and span.end - span.start <= maximum_span
        ]

    def exhaustive_spans_for(self, block: MarkdownBlock) -> list[_TextSpan]:
        maximum_span = _maximum_span(block)
        return [
            span
            for spans in self._spans_by_start
            for span in spans
            if span.end - span.start <= maximum_span
        ]

    def alignment_respects_anchors(self, aligned: Sequence[_Candidate]) -> bool:
        locations = {
            (candidate.block.index, candidate.start, candidate.end) for candidate in aligned
        }
        return all(
            (anchor.block_index, anchor.span.start, anchor.span.end) in locations
            for anchor in self._anchors
        )


def match_sidecar_evidence(
    content: MarkdownContent,
    layout: ScanDocumentLayout,
    evidence: EvidenceDocuments,
) -> list[EvidenceMatch]:
    """Match provider documents monotonically, then fuse their geometry.

    Each sidecar/document is aligned independently with contiguous 1:N element
    spans.  Only after alignment are provider boxes clustered and reduced with
    weighted medians, which prevents a dissenting provider from pulling the
    chosen geometry between unrelated regions.
    """

    sources = _document_sources(evidence)
    if not sources or not content.blocks or not layout.pages:
        return []
    workspace = _MatchWorkspace.create()
    scan_pages = {page.number: page for page in layout.pages}
    contributions: list[_Candidate] = []
    for source in sources:
        units = _evidence_units(source, scan_pages, workspace)
        if units:
            candidate_index = _TextCandidateIndex(content.blocks, units, workspace)
            aligned = _align_source(content.blocks, units, candidate_index, workspace)
            if not candidate_index.alignment_respects_anchors(aligned):
                aligned = _align_source(
                    content.blocks,
                    units,
                    candidate_index,
                    workspace,
                    exhaustive=True,
                )
            contributions.extend(aligned)
            contributions.extend(
                _shared_consecutive_text_candidates(
                    content.blocks,
                    units,
                    aligned,
                    workspace,
                )
            )
            contributions.extend(
                _unreliable_exact_visual_candidates(content.blocks, units, aligned)
            )

    by_block: dict[str, list[_Candidate]] = defaultdict(list)
    for contribution in contributions:
        by_block[contribution.block.id].append(contribution)
    matches: list[EvidenceMatch] = []
    for block in content.blocks:
        block_contributions = by_block.get(block.id, [])
        if block_contributions:
            matches.append(_fuse_block(block, block_contributions, scan_pages))
    return matches


def trace_sidecar_evidence(
    content: MarkdownContent,
    layout: ScanDocumentLayout,
    evidence: EvidenceDocuments,
    *,
    matches: Sequence[EvidenceMatch] | None = None,
    top_n: int = 5,
) -> AlignmentReport:
    """Return observation-only per-block traces without changing matcher decisions."""

    from docreconstruct.reconstruction.alignment.diagnostics import build_alignment_report

    return build_alignment_report(
        content,
        layout,
        evidence,
        matches=matches,
        top_n=top_n,
    )


def diagnose_sidecar_evidence(
    content: MarkdownContent,
    layout: ScanDocumentLayout,
    evidence: EvidenceDocuments,
) -> EvidenceAlignmentDiagnostics:
    """Explain strict alignment without changing candidates or acceptance rules.

    The regular matcher intentionally drops geometry it cannot project safely.
    This companion API counts where that happened.  It is suitable for
    benchmark failure ledgers: callers receive stable reason codes instead of
    having to infer a root cause from the final empty match list.
    """

    sources = _document_sources(evidence)
    scan_pages = {page.number: page for page in layout.pages}
    workspace = _MatchWorkspace.create()
    evidence_pages = sum(len(source.document.pages) for source in sources)
    bound_pages = 0
    eligible_elements = 0
    textual_elements = 0
    visual_elements = 0
    outside = 0
    aspect_mismatches = 0
    projection_rejections = 0
    projected_units = 0
    aligned_candidates = 0

    for source in sources:
        bindings = _bind_document_pages(source.document, scan_pages)
        bound_pages += len(bindings)
        for binding in bindings:
            page = binding.page
            scan_page = binding.scan_page
            if _coordinate_system(page.metadata) in _IGNORED_COORDINATE_SYSTEMS:
                continue
            elements_by_id = {element.id: element for element in page.elements}
            for element in page.elements:
                if _redundant_child(element, elements_by_id):
                    continue
                if _coordinate_system(element.metadata) in _IGNORED_COORDINATE_SYSTEMS:
                    continue
                text = _element_text(element)
                normalized = workspace.normalize(
                    text,
                    formula=element.type is ElementType.FORMULA,
                )
                is_visual = element.type in _VISUAL_TYPES
                if not normalized and not is_visual:
                    continue
                eligible_elements += 1
                if is_visual:
                    visual_elements += 1
                else:
                    textual_elements += 1
                if (
                    element.bbox.x0 < 0
                    or element.bbox.y0 < 0
                    or element.bbox.x1 > page.width
                    or element.bbox.y1 > page.height
                ):
                    outside += 1
                projected = _project_page_box(scan_page, page, element.bbox)
                if (
                    projected is not None
                    and projected.x1 > projected.x0
                    and projected.y1 > projected.y0
                ):
                    projected_units += 1
                    continue
                projection_rejections += 1
                mapping = scan_page.metadata.get("source_to_scan_map")
                normalized_coordinates = (
                    abs(page.width - 1.0) <= 1e-6 and abs(page.height - 1.0) <= 1e-6
                )
                if mapping is None and not normalized_coordinates:
                    aspect_error = abs(
                        math.log((page.width / page.height) / (scan_page.width / scan_page.height))
                    )
                    if aspect_error > 0.06:
                        aspect_mismatches += 1
                elif isinstance(mapping, Mapping) and not normalized_coordinates:
                    source_width = mapping.get("source_width")
                    source_height = mapping.get("source_height")
                    target_width = mapping.get("target_width")
                    target_height = mapping.get("target_height")
                    dimensions = (
                        source_width,
                        source_height,
                        target_width,
                        target_height,
                    )
                    numeric_dimensions = [
                        float(value)
                        for value in dimensions
                        if isinstance(value, (int, float))
                        and not isinstance(value, bool)
                        and value > 0
                    ]
                    if len(numeric_dimensions) == 4:
                        source_width_value, source_height_value = numeric_dimensions[:2]
                        target_width_value, target_height_value = numeric_dimensions[2:]
                        source_error = abs(
                            math.log(
                                (page.width / page.height)
                                / (source_width_value / source_height_value)
                            )
                        )
                        target_error = abs(
                            math.log(
                                (page.width / page.height)
                                / (target_width_value / target_height_value)
                            )
                        )
                        if min(source_error, target_error) > 0.06:
                            aspect_mismatches += 1

        units = _evidence_units(source, scan_pages, workspace)
        if units and content.blocks:
            candidate_index = _TextCandidateIndex(content.blocks, units, workspace)
            aligned = _align_source(content.blocks, units, candidate_index, workspace)
            if not candidate_index.alignment_respects_anchors(aligned):
                aligned = _align_source(
                    content.blocks,
                    units,
                    candidate_index,
                    workspace,
                    exhaustive=True,
                )
            aligned_candidates += len(aligned)

    matches = (
        match_sidecar_evidence(content, layout, evidence)
        if sources and content.blocks and layout.pages
        else []
    )
    reasons: list[EvidenceAlignmentReason] = []
    if not sources:
        reasons.append(EvidenceAlignmentReason.NO_EVIDENCE_DOCUMENTS)
    if not content.blocks:
        reasons.append(EvidenceAlignmentReason.NO_MARKDOWN_BLOCKS)
    if not layout.pages:
        reasons.append(EvidenceAlignmentReason.NO_LAYOUT_PAGES)
    if sources and layout.pages and bound_pages == 0:
        reasons.append(EvidenceAlignmentReason.NO_BOUND_EVIDENCE_PAGES)
    if aspect_mismatches:
        reasons.append(EvidenceAlignmentReason.PROJECTION_ASPECT_MISMATCH)
    if projection_rejections > aspect_mismatches:
        reasons.append(EvidenceAlignmentReason.PROJECTION_REJECTED)
    if sources and content.blocks and layout.pages and not matches and projected_units:
        reasons.append(EvidenceAlignmentReason.NO_SAFE_TEXT_CANDIDATES)
    if matches:
        reasons.append(EvidenceAlignmentReason.MATCHED)

    return EvidenceAlignmentDiagnostics(
        reason_codes=tuple(reasons),
        markdown_blocks=len(content.blocks),
        evidence_documents=len(sources),
        evidence_pages=evidence_pages,
        bound_pages=bound_pages,
        eligible_elements=eligible_elements,
        textual_elements=textual_elements,
        visual_elements=visual_elements,
        boxes_outside_declared_page=outside,
        projection_aspect_mismatches=aspect_mismatches,
        projection_rejections=projection_rejections,
        projected_units=projected_units,
        aligned_candidates=aligned_candidates,
        matched_blocks=len({match.block_id for match in matches}),
    )


def _document_sources(evidence: EvidenceDocuments) -> list[_DocumentSource]:
    # Avoid importing the loader at runtime: providers import reconstruction
    # helpers while the loader itself is still initializing.  The public
    # bundle contract is deliberately small, so structural detection keeps
    # this matcher independent and breaks that otherwise circular import.
    items = getattr(evidence, "items", None)
    if items is not None and not isinstance(evidence, Document):
        return [
            _DocumentSource(
                document=item.document,
                provider_hint=item.provider,
                key=f"{index}:{item.path}",
                warnings=item.warnings,
            )
            for index, item in enumerate(items)
            if item.document is not None
        ]
    if isinstance(evidence, Document):
        documents = [evidence]
    elif isinstance(evidence, Sequence) and not isinstance(evidence, (str, bytes, bytearray)):
        documents = list(evidence)
        if not all(isinstance(document, Document) for document in documents):
            raise TypeError("evidence sequences must contain canonical Document objects")
    else:
        raise TypeError("evidence must be a SidecarEvidenceBundle, Document, or Document sequence")
    return [
        _DocumentSource(
            document=document,
            provider_hint=None,
            key=f"document-{index}",
            warnings=(),
        )
        for index, document in enumerate(documents)
    ]


def _evidence_units(
    source: _DocumentSource,
    scan_pages: Mapping[int, ScanPageLayout],
    workspace: _MatchWorkspace | None = None,
) -> list[_Unit]:
    document_provider = _provider_name(source.document, source.provider_hint)
    if (
        document_provider == "markdown"
        or str(source.document.metadata.get("content_authority", "")).casefold() == "markdown"
    ):
        return []

    units: list[_Unit] = []
    position = 0
    for binding in _bind_document_pages(source.document, scan_pages):
        page = binding.page
        scan_page = binding.scan_page
        if _coordinate_system(page.metadata) in _IGNORED_COORDINATE_SYSTEMS:
            continue
        indexed_elements = list(enumerate(page.elements))
        ordered = sorted(
            indexed_elements,
            key=lambda item: (
                item[1].reading_order is None,
                item[1].reading_order if item[1].reading_order is not None else item[0],
                item[0],
            ),
        )
        elements_by_id = {element.id: element for element in page.elements}
        for _, element in ordered:
            if _redundant_child(element, elements_by_id):
                continue
            coordinate_system = _coordinate_system(element.metadata)
            if coordinate_system in _IGNORED_COORDINATE_SYSTEMS:
                continue
            text = _element_text(element)
            normalized = (
                workspace.normalize(text, formula=element.type is ElementType.FORMULA)
                if workspace is not None
                else _normalize_text(text, formula=element.type is ElementType.FORMULA)
            )
            is_visual = element.type in _VISUAL_TYPES
            if not normalized and not is_visual:
                continue
            projected = _project_page_box(scan_page, page, element.bbox)
            if projected is None or projected.x1 <= projected.x0 or projected.y1 <= projected.y0:
                continue
            warnings = list(source.warnings)
            if binding.warning is not None:
                warnings.append(binding.warning)
            if (
                element.bbox.x0 < 0
                or element.bbox.y0 < 0
                or element.bbox.x1 > page.width
                or element.bbox.y1 > page.height
            ):
                warnings.append(f"geometry clipped for element {element.id}")
            provider = _element_provider(element, document_provider)
            if provider == "markdown":
                continue
            units.append(
                _Unit(
                    position=position,
                    page_number=scan_page.number,
                    element_id=element.id,
                    text=text,
                    normalized_text=normalized,
                    element_type=element.type,
                    bbox=projected,
                    confidence=_element_confidence(element),
                    style=_nonempty_style(element.style),
                    provider=provider,
                    source_key=source.key,
                    visual_keys=(_element_visual_keys(element) if is_visual else ()),
                    reading_order_reliable=_element_reading_order_reliable(element),
                    warnings=tuple(_unique(warnings)),
                )
            )
            position += 1
    return units


def _bind_document_pages(
    document: Document,
    scan_pages: Mapping[int, ScanPageLayout],
) -> list[_PageBinding]:
    """Bind canonical evidence pages to layout pages without guessing subsets.

    Provider APIs do not always retain the original document's one-based page
    labels.  A saved result for pages 5--6, for example, may be supplied next
    to a two-page cropped PDF whose layout pages are numbered 1--2.  Exact
    labels remain authoritative when the sequences agree.  A constant-offset
    ordinal mapping is accepted only when both complete sequences are the same
    length and consecutive; partial or irregular sequences use exact matches
    only, because their missing-page correspondence is ambiguous.
    """

    indexed_pages = list(enumerate(document.pages))
    provider_pages = [
        page for _, page in sorted(indexed_pages, key=lambda item: (item[1].number, item[0]))
    ]
    layout_pages = sorted(scan_pages.values(), key=lambda page: page.number)
    provider_numbers = [page.number for page in provider_pages]
    layout_numbers = [page.number for page in layout_pages]

    if provider_numbers == layout_numbers:
        return [
            _PageBinding(page=page, scan_page=scan_pages[page.number]) for page in provider_pages
        ]

    complete_consecutive_sequences = (
        len(provider_pages) == len(layout_pages)
        and bool(provider_pages)
        and _consecutive(provider_numbers)
        and _consecutive(layout_numbers)
    )
    if complete_consecutive_sequences:
        return [
            _PageBinding(
                page=page,
                scan_page=scan_page,
                warning=(
                    "provider page "
                    f"{page.number} mapped by complete ordinal sequence to layout page "
                    f"{scan_page.number}"
                ),
            )
            for page, scan_page in zip(provider_pages, layout_pages, strict=True)
        ]

    return [
        _PageBinding(page=page, scan_page=scan_pages[page.number])
        for page in provider_pages
        if page.number in scan_pages
    ]


def _consecutive(numbers: Sequence[int]) -> bool:
    return all(right == left + 1 for left, right in zip(numbers, numbers[1:], strict=False))


def _provider_name(document: Document, hint: str | None) -> str:
    raw = hint or document.metadata.get("provider")
    if isinstance(raw, str) and raw.strip():
        return raw.strip().casefold().replace("-", "_")
    for page in document.pages:
        for element in page.elements:
            if element.provenance is not None and element.provenance.engine.strip():
                return element.provenance.engine.strip().casefold().replace("-", "_")
    return "unknown"


def _element_provider(element: Element, fallback: str) -> str:
    if element.provenance is not None:
        engine = element.provenance.engine.strip()
        if engine and engine != "ensemble":
            return engine.casefold().replace("-", "_")
    return fallback


def _coordinate_system(metadata: Mapping[str, Any]) -> str:
    value = metadata.get("coordinate_system")
    return value.strip().casefold() if isinstance(value, str) else ""


def _project_page_box(
    scan_page: ScanPageLayout,
    page: Page,
    bbox: BBox,
) -> PixelBox | None:
    rotated = _orthogonal_page_box(page, bbox)
    if rotated is None:
        return None
    rotated_bbox, source_width, source_height = rotated
    return project_source_box_to_scan(
        scan_page,
        rotated_bbox,
        source_width,
        source_height,
    )


def _orthogonal_page_box(
    page: Page,
    bbox: BBox,
) -> tuple[BBox, float, float] | None:
    rotation = page.rotation % 360.0
    orthogonal = next(
        (angle for angle in (0.0, 90.0, 180.0, 270.0) if abs(rotation - angle) <= 1e-6),
        None,
    )
    if orthogonal is None:
        return None
    if orthogonal == 0.0:
        return bbox, page.width, page.height
    if orthogonal == 90.0:
        return (
            BBox(
                x0=page.height - bbox.y1,
                y0=bbox.x0,
                x1=page.height - bbox.y0,
                y1=bbox.x1,
            ),
            page.height,
            page.width,
        )
    if orthogonal == 180.0:
        return (
            BBox(
                x0=page.width - bbox.x1,
                y0=page.height - bbox.y1,
                x1=page.width - bbox.x0,
                y1=page.height - bbox.y0,
            ),
            page.width,
            page.height,
        )
    return (
        BBox(
            x0=bbox.y0,
            y0=page.width - bbox.x1,
            x1=bbox.y1,
            y1=page.width - bbox.x0,
        ),
        page.height,
        page.width,
    )


def _redundant_child(element: Element, elements_by_id: Mapping[str, Element]) -> bool:
    block_type = str(element.metadata.get("block_type") or "").upper()
    if block_type == "QUERY":
        return True
    if block_type not in {"CELL", "MERGED_CELL", "WORD"}:
        return False
    parent_id = element.relationships.parent
    if parent_id is None:
        return False
    parent = elements_by_id.get(parent_id)
    if parent is None:
        return False
    if block_type in {"CELL", "MERGED_CELL"}:
        return parent.type is ElementType.TABLE
    return bool(parent.text)


def _element_text(element: Element) -> str:
    if element.type is ElementType.TABLE:
        rows = _metadata_rows(element.metadata)
        if rows:
            return "\n".join("\t".join(row) for row in rows)
    if element.type is ElementType.FORMULA:
        latex = element.metadata.get("latex")
        if isinstance(latex, str) and latex.strip():
            return latex
    return element.text or ""


_VISUAL_REFERENCE_FIELDS = {
    "asset",
    "file",
    "file_name",
    "filename",
    "image",
    "image_path",
    "image_ref",
    "img_path",
    "path",
    "source",
    "src",
    "uri",
    "url",
}
_VISUAL_TEXT_FIELDS = {"alt", "alt_text", "caption", "description", "title"}
_GENERIC_VISUAL_STEMS = {"asset", "chart", "figure", "image", "img", "photo", "picture"}
_HTML_VISUAL_REFERENCE_PATTERN = re.compile(
    r"<(?:img|source)\b[^>]*?\b(?:src|data-src)\b\s*=\s*"
    r"(?:\"(?P<double>[^\"]+)\"|'(?P<single>[^']+)'|(?P<bare>[^\s>]+))",
    flags=re.IGNORECASE | re.DOTALL,
)


def _element_visual_keys(element: Element) -> tuple[str, ...]:
    keys: list[str] = []
    if element.text:
        keys.extend(_visual_text_keys(element.text))
    keys.extend(_visual_reference_keys(element.id))
    if element.provenance is not None and element.provenance.source_id:
        keys.extend(_visual_reference_keys(element.provenance.source_id))
    keys.extend(_metadata_visual_keys(element.metadata))
    return tuple(_unique(keys))


def _block_visual_keys(block: MarkdownBlock) -> tuple[str, ...]:
    keys: list[str] = []
    if block.source:
        keys.extend(_visual_reference_keys(block.source))
    if block.text:
        keys.extend(_visual_text_keys(block.text))
    keys.extend(_metadata_visual_keys(block.metadata))
    return tuple(_unique(keys))


def _metadata_visual_keys(metadata: Mapping[str, Any]) -> list[str]:
    keys: list[str] = []
    for raw_name, value in metadata.items():
        name = str(raw_name).strip().casefold().replace("-", "_")
        if name in _VISUAL_REFERENCE_FIELDS:
            keys.extend(_visual_value_keys(value, text=False))
        elif name in _VISUAL_TEXT_FIELDS:
            keys.extend(_visual_value_keys(value, text=True))
    return _unique(keys)


def _visual_value_keys(value: Any, *, text: bool) -> list[str]:
    if isinstance(value, str):
        return _visual_text_keys(value) if text else _visual_reference_keys(value)
    if isinstance(value, Mapping):
        return _metadata_visual_keys(value)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return _unique(key for item in value for key in _visual_value_keys(item, text=text))
    return []


def _visual_reference_keys(value: str) -> list[str]:
    normalized = html.unescape(unicodedata.normalize("NFKC", value)).strip()
    if not normalized:
        return []
    parsed = urlsplit(normalized)
    reference = unquote(parsed.path if parsed.scheme or parsed.netloc else normalized)
    reference = reference.split("#", 1)[0].split("?", 1)[0]
    reference = re.sub(r"/+", "/", reference.replace("\\", "/")).strip(" /")
    if not reference:
        return []
    reference = reference.casefold()
    name = PurePosixPath(reference).name
    keys = [f"ref:{reference}"]
    if name:
        keys.append(f"name:{name}")
        stem = PurePosixPath(name).stem
        if len(stem) >= 4 and stem not in _GENERIC_VISUAL_STEMS:
            keys.append(f"stem:{stem}")
    return _unique(keys)


def _visual_text_keys(value: str) -> list[str]:
    normalized = _normalize_text(value)
    keys = [f"text:{normalized}"] if normalized else []
    for match in _HTML_VISUAL_REFERENCE_PATTERN.finditer(value):
        reference = match.group("double") or match.group("single") or match.group("bare")
        if reference:
            keys.extend(_visual_reference_keys(reference))
    return _unique(keys)


def _metadata_rows(metadata: Mapping[str, Any]) -> list[list[str]]:
    raw_rows = metadata.get("rows")
    table = metadata.get("table")
    if raw_rows is None and isinstance(table, Mapping):
        raw_rows = table.get("rows")
    if not isinstance(raw_rows, Sequence) or isinstance(raw_rows, (str, bytes, bytearray)):
        return []
    rows: list[list[str]] = []
    for raw_row in raw_rows:
        if not isinstance(raw_row, Sequence) or isinstance(raw_row, (str, bytes, bytearray)):
            continue
        rows.append([str(cell) for cell in raw_row])
    return rows


def _element_confidence(element: Element) -> float | None:
    if element.confidence is not None:
        return element.confidence
    if element.provenance is not None:
        if element.provenance.text_confidence is not None:
            return element.provenance.text_confidence
        return element.provenance.layout_confidence
    return None


def _element_reading_order_reliable(element: Element) -> bool:
    value = element.metadata.get("reading_order_reliable")
    return value if isinstance(value, bool) else True


def _nonempty_style(style: ElementStyle) -> ElementStyle | None:
    return style if style.model_dump(exclude_none=True) else None


def _block_text(block: MarkdownBlock) -> str:
    if block.kind is MarkdownBlockKind.TABLE:
        return "\n".join("\t".join(row) for row in block.table_rows)
    return block.text


def _normalize_text(value: str, *, formula: bool = False) -> str:
    value = html.unescape(unicodedata.normalize("NFKC", value)).strip()
    if formula:
        try:
            value = latex_visible_text(value)
        except (TypeError, ValueError):
            value = re.sub(r"\\[A-Za-z]+", " ", value)
    else:
        value = re.sub(
            r"\$([^$]+)\$",
            lambda match: _inline_latex_text(match.group(1)),
            value,
        )
    value = re.sub(r"<[^>]+>", " ", value)
    value = value.replace("\\%", "%")
    value = re.sub(r"[_*`#{}\[\]()]", " ", value)
    value = re.sub(r"[^\w\s.+\-=/<>%×÷√∑∫]", " ", value, flags=re.UNICODE)
    return " ".join(value.casefold().split())


def _inline_latex_text(value: str) -> str:
    try:
        return latex_visible_text(value)
    except (TypeError, ValueError):
        return value


def _canonical_text_pair(left: str, right: str) -> tuple[str, str]:
    return (left, right) if left <= right else (right, left)


def _cached_text_similarity(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    sequence = difflib.SequenceMatcher(a=left, b=right, autojunk=False).ratio()
    left_tokens = Counter(left.split())
    right_tokens = Counter(right.split())
    overlap = sum((left_tokens & right_tokens).values())
    token_f1 = (
        2.0 * overlap / (sum(left_tokens.values()) + sum(right_tokens.values())) if overlap else 0.0
    )
    containment = (
        min(len(left), len(right)) / max(len(left), len(right))
        if (left in right or right in left)
        else 0.0
    )
    return min(1.0, max(sequence, 0.68 * sequence + 0.32 * token_f1, containment))


def _text_similarity(
    left: str,
    right: str,
    workspace: _MatchWorkspace | None = None,
) -> float:
    """Return exact symmetric similarity, optionally cached for one match job."""

    if workspace is not None:
        return workspace.similarity(left, right)
    return _cached_text_similarity(*_canonical_text_pair(left, right))


def _cached_text_similarity_at_least(
    left: str,
    right: str,
    minimum: float,
) -> float | None:
    """Return exact similarity only when it can reach ``minimum``.

    The number of characters in matching blocks cannot exceed the multiset
    character overlap.  That gives a strict upper bound for SequenceMatcher's
    ratio; combining it with the exact token and containment components lets
    obviously impossible spans avoid the expensive quadratic comparison
    without changing any accepted candidate or its score.
    """

    return _text_similarity_at_least_uncached(
        left,
        right,
        minimum,
        _MatchWorkspace.create(),
    )


def _text_similarity_at_least_uncached(
    left: str,
    right: str,
    minimum: float,
    workspace: _MatchWorkspace,
) -> float | None:
    if not left or not right:
        return None
    if left == right:
        return 1.0 if minimum <= 1.0 else None

    left_features = workspace.text_features(left)
    right_features = workspace.text_features(right)
    character_overlap = sum((left_features.characters & right_features.characters).values())
    sequence_upper_bound = 2.0 * character_overlap / (len(left) + len(right))

    token_overlap = sum((left_features.tokens & right_features.tokens).values())
    token_f1 = (
        2.0 * token_overlap / (left_features.token_count + right_features.token_count)
        if token_overlap
        else 0.0
    )
    containment = (
        min(len(left), len(right)) / max(len(left), len(right))
        if (left in right or right in left)
        else 0.0
    )
    upper_bound = min(
        1.0,
        max(
            sequence_upper_bound,
            0.68 * sequence_upper_bound + 0.32 * token_f1,
            containment,
        ),
    )
    if upper_bound < minimum - 1e-12:
        return None
    similarity = workspace.similarity(left, right)
    return similarity if similarity >= minimum else None


def _text_similarity_at_least(
    left: str,
    right: str,
    minimum: float,
    workspace: _MatchWorkspace | None = None,
) -> float | None:
    if workspace is not None:
        return workspace.similarity_at_least(left, right, minimum)
    return _cached_text_similarity_at_least(*_canonical_text_pair(left, right), minimum)


def _align_source(
    blocks: Sequence[MarkdownBlock],
    units: Sequence[_Unit],
    candidate_index: _TextCandidateIndex | None = None,
    workspace: _MatchWorkspace | None = None,
    *,
    exhaustive: bool = False,
) -> list[_Candidate]:
    workspace = workspace or _MatchWorkspace.create()
    candidate_index = candidate_index or _TextCandidateIndex(blocks, units, workspace)
    states: dict[int, _State] = {0: _State(total_score=0.0, match_count=0, node=None)}
    text_candidate_cache: dict[int, list[_Candidate]] = {}
    for block in blocks:
        candidates = _block_candidates(
            block,
            blocks,
            units,
            text_candidate_cache,
            candidate_index,
            workspace,
            exhaustive=exhaustive,
        )
        if not candidates:
            continue
        state_ends, prefix_states = _prefix_states(states)
        next_states = dict(states)
        for candidate in candidates:
            index = bisect_right(state_ends, candidate.start) - 1
            if index < 0:
                continue
            previous = prefix_states[index]
            proposal = _State(
                total_score=previous.total_score + candidate.match_score,
                match_count=previous.match_count + 1,
                node=_Node(candidate=candidate, previous=previous.node),
            )
            existing = next_states.get(candidate.end)
            if existing is None or _better_state(proposal, existing):
                next_states[candidate.end] = proposal
        states = _prune_states(next_states)
    best = max(states.values(), key=_state_key)
    result: list[_Candidate] = []
    node = best.node
    while node is not None:
        result.append(node.candidate)
        node = node.previous
    result.reverse()
    return result


def _shared_consecutive_text_candidates(
    blocks: Sequence[MarkdownBlock],
    units: Sequence[_Unit],
    aligned: Sequence[_Candidate],
    workspace: _MatchWorkspace | None = None,
) -> list[_Candidate]:
    """Recover leading Markdown text fused into one provider text unit.

    Layout OCR commonly emits a heading stack or a complete question plus its
    A--D choices as one rectangle, while Markdown deliberately exposes those
    parts as consecutive editable blocks.  A provider rectangle may anchor
    the unmatched *leading text owners* only when the normalized concatenation
    of the whole Markdown span accounts for that provider unit almost exactly.
    Option children deliberately remain unmatched here: the hybrid planner can
    split their distinct scan rows/columns without pretending that every
    choice owns the same coarse provider rectangle.

    Repeated text and locations that contradict already aligned surrounding
    blocks are rejected.  The matcher contributes geometry only; Markdown
    remains the content authority and no text is split or rewritten.
    """

    if len(blocks) < 2:
        return []
    workspace = workspace or _MatchWorkspace.create()
    aligned_by_block = {candidate.block.id: candidate for candidate in aligned}
    ordered_aligned = sorted(aligned, key=lambda candidate: candidate.block.index)
    normalized_blocks = [
        workspace.normalize(
            _block_text(block),
            formula=block.kind is MarkdownBlockKind.EQUATION,
        )
        for block in blocks
    ]
    eligible_units = [unit for unit in units if unit.element_type not in _VISUAL_TYPES]
    proposals: list[tuple[int, int, _Unit, float, tuple[MarkdownBlock, ...]]] = []

    for start in range(len(blocks) - 1):
        fragments: list[str] = []
        for end in range(start + 1, min(len(blocks), start + _MAX_SPAN) + 1):
            block = blocks[end - 1]
            if (
                block.kind
                in {
                    MarkdownBlockKind.IMAGE,
                    MarkdownBlockKind.TABLE,
                    MarkdownBlockKind.RULE,
                }
                or not normalized_blocks[end - 1]
                or block.index != blocks[start].index + (end - start - 1)
            ):
                break
            fragments.append(normalized_blocks[end - 1])
            if end - start < 2:
                continue

            recoverable: list[MarkdownBlock] = []
            for member in blocks[start:end]:
                if member.kind is MarkdownBlockKind.OPTION:
                    break
                if member.id not in aligned_by_block:
                    recoverable.append(member)
            if not recoverable:
                continue

            combined_text = " ".join(fragments)
            aligned_members = [
                aligned_by_block[member.id]
                for member in blocks[start:end]
                if member.id in aligned_by_block
            ]
            previous = next(
                (
                    candidate
                    for candidate in reversed(ordered_aligned)
                    if candidate.block.index < blocks[start].index
                ),
                None,
            )
            following = next(
                (
                    candidate
                    for candidate in ordered_aligned
                    if candidate.block.index > blocks[end - 1].index
                ),
                None,
            )
            for unit in eligible_units:
                if not _inside_anchor_window(unit, previous, following):
                    continue
                source_text = unit.normalized_text
                # A layout provider often appends a short byline/signature to
                # the final prose rectangle.  Recover that trailing heading
                # when it is an exact suffix of the very unit already owning
                # the preceding Markdown block.  The relaxed whole-span score
                # tolerates OCR spelling disagreement in the prose, while the
                # exact suffix, shared unit identity, and heading-only rule
                # keep this from assigning option children or unrelated text.
                trailing_heading_suffix = (
                    len(recoverable) == 1
                    and recoverable[0] is blocks[end - 1]
                    and recoverable[0].kind is MarkdownBlockKind.HEADING
                    and bool(aligned_members)
                    and source_text.endswith(normalized_blocks[end - 1])
                    and all(
                        candidate.source_key == unit.source_key
                        and candidate.start <= unit.position < candidate.end
                        for candidate in aligned_members
                    )
                )
                length_agreement = min(len(combined_text), len(source_text)) / max(
                    1,
                    max(len(combined_text), len(source_text)),
                )
                minimum_length = 0.95 if trailing_heading_suffix else 0.985
                minimum_similarity = 0.94 if trailing_heading_suffix else 0.985
                if length_agreement < minimum_length:
                    continue
                similarity = _text_similarity_at_least(
                    combined_text,
                    source_text,
                    minimum_similarity,
                    workspace,
                )
                if similarity is None:
                    continue
                proposals.append((start, end, unit, similarity, tuple(recoverable)))

    # One provider unit cannot prove two different Markdown spans, and one
    # Markdown block cannot choose between repeated provider units.  Reject
    # both ambiguity shapes instead of resolving them by list position.
    spans_by_unit: dict[tuple[str, int], set[tuple[int, int]]] = defaultdict(set)
    proposals_by_block: dict[
        str,
        list[tuple[int, int, _Unit, float, tuple[MarkdownBlock, ...]]],
    ] = defaultdict(list)
    for proposal in proposals:
        start, end, unit, _similarity, recovered_blocks = proposal
        spans_by_unit[(unit.source_key, unit.position)].add((start, end))
        for block in recovered_blocks:
            proposals_by_block[block.id].append(proposal)

    result: list[_Candidate] = []
    for block in blocks:
        block_proposals = [
            proposal
            for proposal in proposals_by_block.get(block.id, ())
            if len(spans_by_unit[(proposal[2].source_key, proposal[2].position)]) == 1
        ]
        identities = {
            (proposal[0], proposal[1], proposal[2].source_key, proposal[2].position)
            for proposal in block_proposals
        }
        if len(identities) != 1:
            continue
        start, end, unit, similarity, _recoverable = max(
            block_proposals,
            key=lambda proposal: (
                proposal[3],
                -(proposal[1] - proposal[0]),
                -proposal[2].position,
            ),
        )
        type_score = _type_score(block.kind, [unit.element_type])
        confidence_factor = unit.confidence if unit.confidence is not None else 0.5
        match_score = min(
            1.0,
            0.82 * similarity + 0.13 * type_score + 0.05 * confidence_factor,
        )
        result.append(
            _Candidate(
                block=block,
                start=unit.position,
                end=unit.position + 1,
                page_number=unit.page_number,
                bbox=unit.bbox,
                source_rows=(unit.bbox,),
                text_score=similarity,
                type_score=type_score,
                match_score=match_score,
                confidence=unit.confidence,
                provider=unit.provider,
                source_key=unit.source_key,
                element_ids=(unit.element_id,),
                style=unit.style,
                warnings=tuple(
                    _unique(
                        [
                            *unit.warnings,
                            (
                                "Markdown block shares one exact provider line with "
                                f"consecutive authority blocks {blocks[start].id}--"
                                f"{blocks[end - 1].id}"
                            ),
                        ]
                    )
                ),
            )
        )
    return result


def _unreliable_exact_visual_candidates(
    blocks: Sequence[MarkdownBlock],
    units: Sequence[_Unit],
    aligned: Sequence[_Candidate],
) -> list[_Candidate]:
    """Retain exact side visuals whose provider supplied no stable order.

    Some layout providers assign a fallback list index to side figures that
    deliberately have no reading-order field.  A unique shared filename is
    still strong identity evidence, but it must remain on the page interval
    established by surrounding text matches.  Reliable provider order and
    ambiguous/repeated visual identifiers continue to use the strict
    monotonic alignment path.
    """

    aligned_ids = {candidate.block.id for candidate in aligned}
    visual_units = [unit for unit in units if unit.element_type in _VISUAL_TYPES]
    ordered_aligned = sorted(aligned, key=lambda candidate: candidate.block.index)
    result: list[_Candidate] = []
    for block in blocks:
        if block.kind is not MarkdownBlockKind.IMAGE or block.id in aligned_ids:
            continue
        block_keys = set(_block_visual_keys(block))
        identity_matches = [
            (_visual_identity_strength(block_keys, set(unit.visual_keys)), unit)
            for unit in visual_units
            if not unit.reading_order_reliable
        ]
        identity_matches = [item for item in identity_matches if item[0] > 0]
        if not identity_matches:
            continue
        best_strength = max(strength for strength, _unit in identity_matches)
        exact = [unit for strength, unit in identity_matches if strength == best_strength]
        if len(exact) != 1:
            continue
        unit = exact[0]
        previous = next(
            (
                candidate
                for candidate in reversed(ordered_aligned)
                if candidate.block.index < block.index
            ),
            None,
        )
        following = next(
            (candidate for candidate in ordered_aligned if candidate.block.index > block.index),
            None,
        )
        if not _inside_anchor_page_window(unit, previous, following):
            continue
        result.append(
            _visual_candidate(
                block,
                unit,
                exact_reference=True,
                unreliable_order_override=True,
            )
        )
    return result


def _block_candidates(
    block: MarkdownBlock,
    blocks: Sequence[MarkdownBlock],
    units: Sequence[_Unit],
    text_candidate_cache: dict[int, list[_Candidate]],
    candidate_index: _TextCandidateIndex,
    workspace: _MatchWorkspace,
    *,
    exhaustive: bool,
) -> list[_Candidate]:
    if block.kind is MarkdownBlockKind.IMAGE:
        return _visual_candidates(
            block,
            blocks,
            units,
            text_candidate_cache,
            candidate_index,
            workspace,
            exhaustive=exhaustive,
        )
    return _cached_text_block_candidates(
        block,
        units,
        text_candidate_cache,
        candidate_index,
        workspace,
        exhaustive=exhaustive,
    )


def _cached_text_block_candidates(
    block: MarkdownBlock,
    units: Sequence[_Unit],
    cache: dict[int, list[_Candidate]],
    candidate_index: _TextCandidateIndex,
    workspace: _MatchWorkspace,
    *,
    exhaustive: bool,
) -> list[_Candidate]:
    key = id(block)
    candidates = cache.get(key)
    if candidates is None:
        candidates = _text_block_candidates(
            block,
            units,
            candidate_index,
            workspace,
            exhaustive=exhaustive,
        )
        cache[key] = candidates
    return candidates


def _text_block_candidates(
    block: MarkdownBlock,
    units: Sequence[_Unit],
    candidate_index: _TextCandidateIndex | None = None,
    workspace: _MatchWorkspace | None = None,
    *,
    exhaustive: bool = False,
) -> list[_Candidate]:
    workspace = workspace or _MatchWorkspace.create()
    candidate_index = candidate_index or _TextCandidateIndex([block], units, workspace)
    target = workspace.normalize(
        _block_text(block),
        formula=block.kind is MarkdownBlockKind.EQUATION,
    )
    if not target:
        return []
    candidates: list[_Candidate] = []
    spans = (
        candidate_index.exhaustive_spans_for(block)
        if exhaustive
        else candidate_index.spans_for(block)
    )
    for span in spans:
        if not _plausible_start(
            target,
            span.units[0].normalized_text,
            len(units),
            workspace,
        ):
            continue
        candidate = _span_candidate(
            block,
            target,
            span.start,
            span.end,
            span.units,
            workspace,
            candidate_text=span.normalized_text,
        )
        if candidate is not None:
            candidates.append(candidate)
    candidates.sort(key=lambda item: (-item.match_score, item.start, item.end, item.element_ids))
    return sorted(
        candidates[:_MAX_CANDIDATES_PER_BLOCK],
        key=lambda item: (item.start, item.end, -item.match_score, item.element_ids),
    )


def _visual_candidates(
    block: MarkdownBlock,
    blocks: Sequence[MarkdownBlock],
    units: Sequence[_Unit],
    text_candidate_cache: dict[int, list[_Candidate]],
    candidate_index: _TextCandidateIndex,
    workspace: _MatchWorkspace,
    *,
    exhaustive: bool,
) -> list[_Candidate]:
    visual_units = [unit for unit in units if unit.element_type in _VISUAL_TYPES]
    if not visual_units:
        return []
    block_keys = set(_block_visual_keys(block))
    previous = _nearest_text_anchor(
        block,
        blocks,
        units,
        text_candidate_cache,
        candidate_index,
        workspace,
        reverse=True,
        exhaustive=exhaustive,
    )
    following = _nearest_text_anchor(
        block,
        blocks,
        units,
        text_candidate_cache,
        candidate_index,
        workspace,
        reverse=False,
        exhaustive=exhaustive,
    )
    identity_matches = [
        (_visual_identity_strength(block_keys, set(unit.visual_keys)), unit)
        for unit in visual_units
    ]
    identity_matches = [item for item in identity_matches if item[0] > 0]
    if identity_matches:
        best_strength = max(strength for strength, _unit in identity_matches)
        exact = [unit for strength, unit in identity_matches if strength == best_strength]
        if len(exact) == 1 and _inside_anchor_window(exact[0], previous, following):
            return [_visual_candidate(block, exact[0], exact_reference=True)]
        # Repeated identifiers and identities that contradict surrounding text
        # order are both insufficient to choose a crop.
        return []

    lower_position = previous.end if previous is not None else 0
    upper_position = following.start if following is not None else len(units)
    eligible = [unit for unit in visual_units if lower_position <= unit.position < upper_position]

    lower_block = previous.block.index if previous is not None else -1
    upper_block = (
        following.block.index
        if following is not None
        else max(
            (candidate.index for candidate in blocks),
            default=block.index,
        )
        + 1
    )
    image_blocks = [
        candidate
        for candidate in blocks
        if candidate.kind is MarkdownBlockKind.IMAGE and lower_block < candidate.index < upper_block
    ]
    if len(eligible) != 1 or len(image_blocks) != 1 or image_blocks[0].id != block.id:
        return []
    if (
        previous is None
        and following is None
        and (
            len(visual_units) != 1
            or sum(candidate.kind is MarkdownBlockKind.IMAGE for candidate in blocks) != 1
        )
    ):
        return []
    return [_visual_candidate(block, eligible[0], exact_reference=False)]


def _visual_identity_strength(block_keys: set[str], unit_keys: set[str]) -> int:
    strengths = {
        "ref": 4,
        "name": 3,
        "stem": 2,
        "text": 2,
    }
    return max(
        (strengths.get(key.partition(":")[0], 1) for key in block_keys.intersection(unit_keys)),
        default=0,
    )


def _inside_anchor_window(
    unit: _Unit,
    previous: _Candidate | None,
    following: _Candidate | None,
) -> bool:
    if previous is not None and unit.position < previous.end:
        return False
    return following is None or unit.position < following.start


def _inside_anchor_page_window(
    unit: _Unit,
    previous: _Candidate | None,
    following: _Candidate | None,
) -> bool:
    if previous is not None and unit.page_number < previous.page_number:
        return False
    return following is None or unit.page_number <= following.page_number


def _nearest_text_anchor(
    block: MarkdownBlock,
    blocks: Sequence[MarkdownBlock],
    units: Sequence[_Unit],
    text_candidate_cache: dict[int, list[_Candidate]],
    candidate_index: _TextCandidateIndex,
    workspace: _MatchWorkspace,
    *,
    reverse: bool,
    exhaustive: bool,
) -> _Candidate | None:
    neighbors = [
        candidate
        for candidate in blocks
        if (candidate.index < block.index if reverse else candidate.index > block.index)
        and candidate.kind is not MarkdownBlockKind.IMAGE
    ]
    neighbors.sort(key=lambda candidate: candidate.index, reverse=reverse)
    for neighbor in neighbors:
        candidates = _cached_text_block_candidates(
            neighbor,
            units,
            text_candidate_cache,
            candidate_index,
            workspace,
            exhaustive=exhaustive,
        )
        if not candidates:
            continue
        best_score = max(candidate.match_score for candidate in candidates)
        strongest = [
            candidate
            for candidate in candidates
            if candidate.match_score >= best_score - 0.01 and candidate.text_score >= 0.88
        ]
        locations = {(candidate.start, candidate.end) for candidate in strongest}
        if len(locations) == 1:
            return min(
                strongest,
                key=lambda candidate: (
                    -candidate.match_score,
                    candidate.start,
                    candidate.end,
                    candidate.element_ids,
                ),
            )
    return None


def _visual_candidate(
    block: MarkdownBlock,
    unit: _Unit,
    *,
    exact_reference: bool,
    unreliable_order_override: bool = False,
) -> _Candidate:
    confidence_factor = unit.confidence if unit.confidence is not None else 0.5
    match_score = (
        min(1.0, 0.95 + 0.05 * confidence_factor)
        if exact_reference
        else 0.65 + 0.08 * confidence_factor
    )
    warnings = list(unit.warnings)
    if not exact_reference:
        warnings.append(
            "visual geometry matched by a unique monotonic position; no shared asset identifier"
        )
    if unreliable_order_override:
        warnings.append("exact visual identity overrides provider fallback order on the same page")
    return _Candidate(
        block=block,
        start=unit.position,
        end=unit.position + 1,
        page_number=unit.page_number,
        bbox=unit.bbox,
        source_rows=(unit.bbox,),
        text_score=1.0 if exact_reference else 0.0,
        type_score=1.0,
        match_score=match_score,
        confidence=unit.confidence,
        provider=unit.provider,
        source_key=unit.source_key,
        element_ids=(unit.element_id,),
        style=unit.style,
        warnings=tuple(_unique(warnings)),
    )


def _maximum_span(block: MarkdownBlock) -> int:
    return (
        2
        if block.kind is MarkdownBlockKind.TABLE
        else 4
        if block.kind in {MarkdownBlockKind.HEADING, MarkdownBlockKind.EQUATION}
        else _MAX_SPAN
    )


def _plausible_start(
    target: str,
    unit: str,
    unit_count: int,
    workspace: _MatchWorkspace | None = None,
) -> bool:
    if unit_count <= 400:
        return True
    target_tokens = set(target.split())
    unit_tokens = set(unit.split())
    if target_tokens & unit_tokens:
        return True
    prefix = min(12, len(target), len(unit))
    return (
        prefix >= 3
        and _text_similarity_at_least(
            target[:prefix],
            unit[:prefix],
            0.55,
            workspace,
        )
        is not None
    )


def _span_candidate(
    block: MarkdownBlock,
    target: str,
    start: int,
    end: int,
    units: Sequence[_Unit],
    workspace: _MatchWorkspace | None = None,
    *,
    candidate_text: str | None = None,
) -> _Candidate | None:
    if candidate_text is None:
        candidate_text = " ".join(unit.normalized_text for unit in units)
    threshold = _text_threshold(block, target)
    text_score = _text_similarity_at_least(
        target,
        candidate_text,
        threshold,
        workspace,
    )
    if text_score is None:
        return None
    type_score = _type_score(block.kind, [unit.element_type for unit in units])
    confidences = [unit.confidence for unit in units if unit.confidence is not None]
    confidence = sum(confidences) / len(confidences) if confidences else None
    confidence_factor = confidence if confidence is not None else 0.5
    match_score = min(1.0, 0.82 * text_score + 0.13 * type_score + 0.05 * confidence_factor)
    if match_score < 0.62:
        return None
    rows = _merge_rows([unit.bbox for unit in units])
    bbox = _union_boxes(rows)
    style = _span_style(units)
    return _Candidate(
        block=block,
        start=start,
        end=end,
        page_number=units[0].page_number,
        bbox=bbox,
        source_rows=tuple(rows),
        text_score=text_score,
        type_score=type_score,
        match_score=match_score,
        confidence=confidence,
        provider=units[0].provider,
        source_key=units[0].source_key,
        element_ids=tuple(unit.element_id for unit in units),
        style=style,
        warnings=tuple(_unique(warning for unit in units for warning in unit.warnings)),
    )


def _text_threshold(block: MarkdownBlock, target: str) -> float:
    """Return the existing matcher threshold for diagnostics without duplicating policy."""

    return (
        0.56
        if block.kind is MarkdownBlockKind.EQUATION
        else 0.66
        if block.kind is MarkdownBlockKind.TABLE
        else 0.82
        if len(target) <= 3
        else 0.62
    )


def _type_score(kind: MarkdownBlockKind, element_types: Sequence[ElementType]) -> float:
    expected = {
        MarkdownBlockKind.HEADING: {ElementType.HEADING, ElementType.TITLE, ElementType.HEADER},
        MarkdownBlockKind.PARAGRAPH: {ElementType.PARAGRAPH, ElementType.TEXT},
        MarkdownBlockKind.OPTION: {ElementType.LIST_ITEM, ElementType.TEXT},
        MarkdownBlockKind.LIST_ITEM: {ElementType.LIST_ITEM, ElementType.TEXT},
        MarkdownBlockKind.TABLE: {ElementType.TABLE},
        MarkdownBlockKind.EQUATION: {ElementType.FORMULA},
        MarkdownBlockKind.CODE: {ElementType.TEXT},
    }.get(kind, {ElementType.TEXT})
    if any(element_type in expected for element_type in element_types):
        return 1.0
    if any(
        element_type in {ElementType.TEXT, ElementType.PARAGRAPH} for element_type in element_types
    ):
        return 0.62
    return 0.25


def _span_style(units: Sequence[_Unit]) -> ElementStyle | None:
    styled = [unit for unit in units if unit.style is not None]
    if not styled:
        return None
    winner = max(
        styled,
        key=lambda unit: (
            len(unit.style.model_dump(exclude_none=True)) if unit.style is not None else 0,
            unit.confidence if unit.confidence is not None else -1.0,
            -unit.position,
        ),
    )
    return winner.style


def _prefix_states(states: Mapping[int, _State]) -> tuple[list[int], list[_State]]:
    ends: list[int] = []
    prefix: list[_State] = []
    best: _State | None = None
    for end, state in sorted(states.items()):
        if best is None or _better_state(state, best):
            best = state
        ends.append(end)
        prefix.append(best)
    return ends, prefix


def _prune_states(states: Mapping[int, _State]) -> dict[int, _State]:
    result: dict[int, _State] = {}
    best: _State | None = None
    for end, state in sorted(states.items()):
        if best is None or _better_state(state, best):
            result[end] = state
            best = state
    return result


def _state_key(state: _State) -> tuple[float, int, int]:
    end = state.node.candidate.end if state.node is not None else 0
    return (round(state.total_score, 12), state.match_count, -end)


def _better_state(left: _State, right: _State) -> bool:
    return _state_key(left) > _state_key(right)


def _fuse_block(
    block: MarkdownBlock,
    contributions: Sequence[_Candidate],
    scan_pages: Mapping[int, ScanPageLayout],
) -> EvidenceMatch:
    ordered = sorted(
        contributions,
        key=lambda item: (
            item.page_number,
            item.provider,
            item.source_key,
            item.element_ids,
        ),
    )
    page_groups: dict[int, list[_Candidate]] = defaultdict(list)
    for contribution in ordered:
        page_groups[contribution.page_number].append(contribution)
    page_number, page_contributions = max(
        page_groups.items(),
        key=lambda item: (
            len({candidate.provider for candidate in item[1]}),
            sum(candidate.match_score for candidate in item[1]),
            -item[0],
        ),
    )
    selected = _geometry_cluster(page_contributions)
    weights = [_candidate_weight(candidate) for candidate in selected]
    source_bbox = _consensus_bbox(selected, weights, scan_pages[page_number])
    medoid = min(
        selected,
        key=lambda candidate: (
            _box_distance(candidate.bbox, source_bbox),
            -candidate.match_score,
            candidate.provider,
            candidate.source_key,
        ),
    )
    source_rows = sorted(medoid.source_rows or (source_bbox,), key=lambda box: (box.y0, box.x0))
    match_score = _weighted_median(
        [candidate.match_score for candidate in selected],
        weights,
    )
    confidence_values = [
        candidate.confidence if candidate.confidence is not None else candidate.match_score
        for candidate in selected
    ]
    confidence = _weighted_median(confidence_values, weights)
    style, style_fields = _style_consensus(selected, weights)

    selected_keys = {_candidate_key(candidate) for candidate in selected}
    outliers = [
        candidate for candidate in ordered if _candidate_key(candidate) not in selected_keys
    ]
    warnings = _unique(warning for candidate in ordered for warning in candidate.warnings)
    conflict = False
    if len(page_groups) > 1:
        conflict = True
        warnings.append("providers matched the Markdown block on different pages")
    if outliers:
        conflict = True
        warnings.append("provider geometry disagreement; robust consensus excluded outlier boxes")
    text_scores = [candidate.text_score for candidate in ordered]
    if block.kind is not MarkdownBlockKind.IMAGE and (
        min(text_scores) < 0.82 or max(text_scores) - min(text_scores) > 0.18
    ):
        conflict = True
        warnings.append("provider text evidence disagrees with the Markdown authority")
    if style_fields:
        conflict = True
        warnings.append(f"provider style disagreement: {', '.join(style_fields)}")

    providers = tuple(sorted({candidate.provider for candidate in ordered}))
    element_ids = tuple(
        _unique(element_id for candidate in ordered for element_id in candidate.element_ids)
    )
    style_metadata = {
        "contributors": [
            {
                "provider": candidate.provider,
                "element_ids": list(candidate.element_ids),
                "style": (
                    candidate.style.model_dump(mode="json", exclude_none=True)
                    if candidate.style is not None
                    else {}
                ),
                "selected_for_geometry": _candidate_key(candidate) in selected_keys,
            }
            for candidate in ordered
        ],
        "disagreement_fields": style_fields,
    }
    return EvidenceMatch(
        block_id=block.id,
        block_index=block.index,
        page_number=page_number,
        source_bbox=source_bbox,
        source_rows=source_rows,
        match_score=match_score,
        confidence=confidence,
        providers=providers,
        element_ids=element_ids,
        style=style,
        style_metadata=style_metadata,
        conflict=conflict,
        warnings=warnings,
    )


def _geometry_cluster(contributions: Sequence[_Candidate]) -> list[_Candidate]:
    best: list[_Candidate] = []
    best_key: tuple[int, float, int, int, int] | None = None
    for seed in contributions:
        agreeing = [
            candidate
            for candidate in contributions
            if _box_agreement(seed.bbox, candidate.bbox) >= _GEOMETRY_AGREEMENT
        ]
        by_provider: dict[str, _Candidate] = {}
        for candidate in agreeing:
            existing = by_provider.get(candidate.provider)
            if existing is None or (
                candidate.match_score,
                candidate.confidence if candidate.confidence is not None else -1.0,
                candidate.source_key,
            ) > (
                existing.match_score,
                existing.confidence if existing.confidence is not None else -1.0,
                existing.source_key,
            ):
                by_provider[candidate.provider] = candidate
        cluster = sorted(
            by_provider.values(),
            key=lambda item: (item.provider, item.source_key, item.element_ids),
        )
        key = (
            len(cluster),
            sum(candidate.match_score for candidate in cluster),
            -seed.bbox.y0,
            -seed.bbox.x0,
            -seed.bbox.area,
        )
        if best_key is None or key > best_key:
            best = cluster
            best_key = key
    return best or [contributions[0]]


def _candidate_weight(candidate: _Candidate) -> float:
    confidence = candidate.confidence if candidate.confidence is not None else 0.5
    return max(0.05, candidate.match_score * (0.65 + 0.35 * confidence))


def _consensus_bbox(
    contributions: Sequence[_Candidate],
    weights: Sequence[float],
    page: ScanPageLayout,
) -> PixelBox:
    coordinates = (
        [_candidate.bbox.x0 for _candidate in contributions],
        [_candidate.bbox.y0 for _candidate in contributions],
        [_candidate.bbox.x1 for _candidate in contributions],
        [_candidate.bbox.y1 for _candidate in contributions],
    )
    x0 = max(0, min(page.width - 1, round(_weighted_median(coordinates[0], weights))))
    y0 = max(0, min(page.height - 1, round(_weighted_median(coordinates[1], weights))))
    x1 = max(x0 + 1, min(page.width, round(_weighted_median(coordinates[2], weights))))
    y1 = max(y0 + 1, min(page.height, round(_weighted_median(coordinates[3], weights))))
    return PixelBox(x0=x0, y0=y0, x1=x1, y1=y1)


def _weighted_median(values: Sequence[float], weights: Sequence[float]) -> float:
    ordered = sorted(zip(values, weights, strict=True), key=lambda item: item[0])
    threshold = sum(weight for _, weight in ordered) / 2.0
    cumulative = 0.0
    for value, weight in ordered:
        cumulative += weight
        if cumulative >= threshold:
            return float(value)
    return float(ordered[-1][0])


def _style_consensus(
    contributions: Sequence[_Candidate],
    weights: Sequence[float],
) -> tuple[ElementStyle | None, list[str]]:
    records = [
        (candidate.style, weight)
        for candidate, weight in zip(contributions, weights, strict=True)
        if candidate.style is not None
    ]
    if not records:
        return None, []
    result: dict[str, Any] = {}
    disagreements: list[str] = []
    for field_name in ElementStyle.model_fields:
        values = [
            (getattr(style, field_name), weight)
            for style, weight in records
            if getattr(style, field_name) is not None
        ]
        if not values:
            continue
        grouped: dict[str, tuple[Any, float]] = {}
        for value, weight in values:
            key = str(value)
            previous = grouped.get(key)
            grouped[key] = (value, weight + (previous[1] if previous else 0.0))
        winner = max(grouped.values(), key=lambda item: (item[1], str(item[0])))[0]
        result[field_name] = winner
        if len(grouped) > 1:
            disagreements.append(field_name)
    return ElementStyle.model_validate(result), disagreements


def _candidate_key(candidate: _Candidate) -> tuple[str, str, tuple[str, ...], int]:
    return (candidate.provider, candidate.source_key, candidate.element_ids, candidate.page_number)


def _box_agreement(left: PixelBox, right: PixelBox) -> float:
    width = max(0, min(left.x1, right.x1) - max(left.x0, right.x0))
    height = max(0, min(left.y1, right.y1) - max(left.y0, right.y0))
    intersection = width * height
    if intersection <= 0:
        return 0.0
    union = left.area + right.area - intersection
    iou = intersection / union if union else 0.0
    containment = intersection / max(1, min(left.area, right.area))
    return max(iou, containment)


def _box_distance(left: PixelBox, right: PixelBox) -> int:
    return (
        abs(left.x0 - right.x0)
        + abs(left.y0 - right.y0)
        + abs(left.x1 - right.x1)
        + abs(left.y1 - right.y1)
    )


def _merge_rows(boxes: Sequence[PixelBox]) -> list[PixelBox]:
    rows: list[PixelBox] = []
    for box in sorted(boxes, key=lambda item: (item.y0, item.x0)):
        if rows:
            previous = rows[-1]
            overlap = max(0, min(previous.y1, box.y1) - max(previous.y0, box.y0))
            minimum_height = max(1, min(previous.height, box.height))
            if overlap / minimum_height >= 0.55:
                rows[-1] = PixelBox(
                    x0=min(previous.x0, box.x0),
                    y0=min(previous.y0, box.y0),
                    x1=max(previous.x1, box.x1),
                    y1=max(previous.y1, box.y1),
                )
                continue
        rows.append(box)
    return rows


def _union_boxes(boxes: Sequence[PixelBox]) -> PixelBox:
    return PixelBox(
        x0=min(box.x0 for box in boxes),
        y0=min(box.y0 for box in boxes),
        x1=max(box.x1 for box in boxes),
        y1=max(box.y1 for box in boxes),
    )


def _unique(values: Any) -> list[Any]:
    result: list[Any] = []
    for value in values:
        if value not in result:
            result.append(value)
    return result


__all__ = ["EvidenceDocuments", "EvidenceMatch", "match_sidecar_evidence"]
