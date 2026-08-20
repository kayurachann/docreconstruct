"""Deterministic text, layout, structure, and editability metrics."""

from __future__ import annotations

import dataclasses
import math
import re
import zipfile
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from docreconstruct.renderers._utils import (
    bbox_tuple,
    element_metadata,
    element_text,
    element_type,
    elements,
    finite_number,
    mapping,
    ordered_elements,
    pages,
    table_rows,
    value,
)

from .assignment import minimum_cost_assignment

TEXT_METRIC_VERSION = "2.0.0"
LAYOUT_METRIC_VERSION = "3.0.0-alpha.1"
STRUCTURE_METRIC_VERSION = "3.0.0-alpha.1"
EDITABILITY_METRIC_VERSION = "2.0.0-alpha.1"


def _clamp(score: float) -> float:
    return max(0.0, min(1.0, float(score)))


def _mean(values: Sequence[float], default: float = 1.0) -> float:
    return sum(values) / len(values) if values else default


def _distance(reference: Sequence[Any], candidate: Sequence[Any]) -> int:
    """Memory-efficient Levenshtein edit distance for arbitrary sequences."""

    if len(reference) < len(candidate):
        reference, candidate = candidate, reference
    if not candidate:
        return len(reference)
    previous = list(range(len(candidate) + 1))
    for row, left in enumerate(reference, start=1):
        current = [row]
        for column, right in enumerate(candidate, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[column] + 1,
                    previous[column - 1] + (left != right),
                )
            )
        previous = current
    return previous[-1]


def _error_rate_of(
    distance: int,
    reference: Sequence[Any],
    candidate: Sequence[Any],
) -> float:
    if not reference:
        return 0.0 if not candidate else 1.0
    return distance / len(reference)


def _accuracy_of(distance: int, reference: Sequence[Any], candidate: Sequence[Any]) -> float:
    return _clamp(1.0 - distance / max(len(reference), len(candidate), 1))


def _error_rate(reference: Sequence[Any], candidate: Sequence[Any]) -> float:
    return _error_rate_of(_distance(reference, candidate), reference, candidate)


def _accuracy(reference: Sequence[Any], candidate: Sequence[Any]) -> float:
    return _accuracy_of(_distance(reference, candidate), reference, candidate)


def extract_text(source: Any) -> str:
    """Extract immutable OCR text in page and reading order."""

    if source is None:
        return ""
    if isinstance(source, str):
        return source
    if isinstance(source, (bytes, bytearray)):
        return bytes(source).decode("utf-8", errors="replace")
    source_pages = pages(source)
    if source_pages:
        page_text: list[str] = []
        for page in source_pages:
            page_text.append(
                "\n".join(
                    element_text(element)
                    for element in ordered_elements(page)
                    if element_text(element) != ""
                )
            )
        return "\f".join(page_text)
    found = value(source, "text", None)
    return "" if found is None else str(found)


_NUMBER = re.compile(r"(?<![\w.])[+\-−]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?%?")


def _numbers(text: str) -> list[str]:
    return [match.group(0).replace("−", "-").replace(",", "") for match in _NUMBER.finditer(text)]


@dataclass(frozen=True)
class TextMetrics:
    character_error_rate: float
    word_error_rate: float
    character_accuracy: float
    word_accuracy: float
    exact_match: float
    numerical_accuracy: float
    reference_characters: int
    candidate_characters: int
    metric_version: str = TEXT_METRIC_VERSION

    @property
    def cer(self) -> float:
        return self.character_error_rate

    @property
    def wer(self) -> float:
        return self.word_error_rate

    @property
    def score(self) -> float:
        return _clamp(
            0.45 * self.character_accuracy
            + 0.35 * self.word_accuracy
            + 0.10 * self.exact_match
            + 0.10 * self.numerical_accuracy
        )

    def to_dict(self) -> dict[str, Any]:
        return {**dataclasses.asdict(self), "score": self.score}


def evaluate_text(reference: Any, candidate: Any) -> TextMetrics:
    reference_text = extract_text(reference)
    candidate_text = extract_text(candidate)
    reference_words = re.findall(r"\S+", reference_text)
    candidate_words = re.findall(r"\S+", candidate_text)
    reference_numbers = _numbers(reference_text)
    candidate_numbers = _numbers(candidate_text)
    # The error rate and the accuracy of one pair are two readings of a single
    # edit distance.  Asking each helper for its own doubled the whole
    # `O(len(reference) * len(candidate))` matrix for both the character and the
    # word comparison on every evaluated case.
    character_distance = _distance(reference_text, candidate_text)
    word_distance = _distance(reference_words, candidate_words)
    return TextMetrics(
        character_error_rate=_error_rate_of(character_distance, reference_text, candidate_text),
        word_error_rate=_error_rate_of(word_distance, reference_words, candidate_words),
        character_accuracy=_accuracy_of(character_distance, reference_text, candidate_text),
        word_accuracy=_accuracy_of(word_distance, reference_words, candidate_words),
        exact_match=float(reference_text == candidate_text),
        numerical_accuracy=_accuracy(reference_numbers, candidate_numbers),
        reference_characters=len(reference_text),
        candidate_characters=len(candidate_text),
    )


@dataclass(frozen=True)
class _ElementRecord:
    page_index: int
    page: Any
    element: Any
    source_index: int

    @property
    def id(self) -> str:
        return str(value(self.element, "id", ""))

    @property
    def type(self) -> str:
        return element_type(self.element)

    @property
    def text(self) -> str:
        return element_text(self.element)


def _records(document: Any) -> list[_ElementRecord]:
    result: list[_ElementRecord] = []
    index = 0
    for page_index, page in enumerate(pages(document)):
        for element in elements(page):
            result.append(_ElementRecord(page_index, page, element, index))
            index += 1
    return result


_TEXT_COMPATIBLE = {
    "text",
    "title",
    "heading",
    "paragraph",
    "list_item",
    "caption",
    "header",
    "footer",
    "footnote",
    "page_number",
}


def _semantic_compatibility(left: str, right: str) -> float | None:
    if left == right:
        return 0.0
    if left in _TEXT_COMPATIBLE and right in _TEXT_COMPATIBLE:
        return 0.35
    return None


def _record_sort_key(record: _ElementRecord) -> tuple[Any, ...]:
    raw_order = value(record.element, "reading_order", None)
    box = bbox_tuple(record.element)
    return (
        record.page_index,
        "text" if record.type in _TEXT_COMPATIBLE else record.type,
        1 if raw_order is None else 0,
        finite_number(raw_order, 1_000_000),
        box[1],
        box[0],
        box[3],
        box[2],
        record.type,
        re.sub(r"\s+", " ", record.text).strip().casefold(),
        record.id,
        record.source_index,
    )


def _normalized_text_cost(left: str, right: str) -> float:
    normalized_left = re.sub(r"\s+", " ", left).strip().casefold()
    normalized_right = re.sub(r"\s+", " ", right).strip().casefold()
    if normalized_left == normalized_right:
        return 0.0
    return 1.0 - _accuracy(normalized_left, normalized_right)


def _match_elements(
    reference: Any, candidate: Any
) -> tuple[list[tuple[_ElementRecord, _ElementRecord]], int, int]:
    """Globally assign compatible elements page-by-page.

    Candidate list order is not evidence.  Records are first canonicalized,
    then a minimum-cost page-local assignment combines semantic type, text,
    geometry, size, and reading-order evidence.  Duplicate IDs receive no
    special treatment; a unique ID is only a small tie preference and never
    overrides a hard semantic incompatibility.
    """

    refs = _records(reference)
    cands = _records(candidate)
    pairs: list[tuple[_ElementRecord, _ElementRecord]] = []

    ref_id_counts = Counter(record.id for record in refs if record.id)
    cand_id_counts = Counter(record.id for record in cands if record.id)
    page_indices = sorted(
        {record.page_index for record in refs} | {record.page_index for record in cands}
    )
    for page_index in page_indices:
        page_refs = sorted(
            (record for record in refs if record.page_index == page_index), key=_record_sort_key
        )
        page_cands = sorted(
            (record for record in cands if record.page_index == page_index), key=_record_sort_key
        )
        normalized_candidate_text: dict[str, list[int]] = {}
        for cand_index, cand in enumerate(page_cands):
            normalized_candidate_text.setdefault(
                re.sub(r"\s+", " ", cand.text).strip().casefold(), []
            ).append(cand_index)
        unique_candidate_ids = {
            cand.id: cand_index
            for cand_index, cand in enumerate(page_cands)
            if cand.id and cand_id_counts[cand.id] == 1
        }
        pair_costs: list[list[float | None]] = []
        order_denominator = max(len(page_refs), len(page_cands), 2) - 1
        for ref_rank, ref in enumerate(page_refs):
            row: list[float | None] = [None] * len(page_cands)
            if max(len(page_refs), len(page_cands)) <= 96:
                candidate_indices = set(range(len(page_cands)))
            else:
                proportional_rank = round(
                    ref_rank * (len(page_cands) - 1) / max(len(page_refs) - 1, 1)
                )
                candidate_indices = set(
                    range(
                        max(0, proportional_rank - 32),
                        min(len(page_cands), proportional_rank + 33),
                    )
                )
                normalized_ref_text = re.sub(r"\s+", " ", ref.text).strip().casefold()
                exact_text_indices = normalized_candidate_text.get(normalized_ref_text, ())
                candidate_indices.update(
                    sorted(exact_text_indices, key=lambda index: abs(index - proportional_rank))[
                        :64
                    ]
                )
                if ref.id and ref_id_counts[ref.id] == 1 and ref.id in unique_candidate_ids:
                    candidate_indices.add(unique_candidate_ids[ref.id])
            for cand_rank in sorted(candidate_indices):
                cand = page_cands[cand_rank]
                type_cost = _semantic_compatibility(ref.type, cand.type)
                if type_cost is None:
                    continue
                geometry_cost = 1.0 - _mean(
                    [_bbox_iou(ref.element, cand.element), _position_similarity(ref, cand)]
                )
                text_cost = _normalized_text_cost(ref.text, cand.text)
                size_cost = 1.0 - _size_similarity(ref.element, cand.element)
                order_cost = abs(ref_rank - cand_rank) / order_denominator
                cost = (
                    0.45 * geometry_cost
                    + 0.25 * text_cost
                    + 0.15 * type_cost
                    + 0.10 * size_cost
                    + 0.05 * order_cost
                )
                if (
                    ref.id
                    and ref.id == cand.id
                    and ref_id_counts[ref.id] == 1
                    and cand_id_counts[cand.id] == 1
                ):
                    cost = max(0.0, cost - 0.02)
                row[cand_rank] = cost
            pair_costs.append(row)
        assignment = minimum_cost_assignment(
            pair_costs,
            candidate_count=len(page_cands),
            unmatched_cost=0.4,
        )
        pairs.extend(
            (page_refs[pair.reference_index], page_cands[pair.candidate_index])
            for pair in assignment.pairs
        )
    pairs.sort(key=lambda pair: pair[0].source_index)
    return pairs, len(refs), len(cands)


def _bbox_iou(left: Any, right: Any) -> float:
    ax0, ay0, ax1, ay1 = bbox_tuple(left)
    bx0, by0, bx1, by1 = bbox_tuple(right)
    intersection_width = max(0.0, min(ax1, bx1) - max(ax0, bx0))
    intersection_height = max(0.0, min(ay1, by1) - max(ay0, by0))
    intersection = intersection_width * intersection_height
    area_a = max(0.0, ax1 - ax0) * max(0.0, ay1 - ay0)
    area_b = max(0.0, bx1 - bx0) * max(0.0, by1 - by0)
    union = area_a + area_b - intersection
    if union == 0:
        return float((ax0, ay0, ax1, ay1) == (bx0, by0, bx1, by1))
    return _clamp(intersection / union)


def _size_similarity(left: Any, right: Any) -> float:
    ax0, ay0, ax1, ay1 = bbox_tuple(left)
    bx0, by0, bx1, by1 = bbox_tuple(right)
    widths = (max(0.0, ax1 - ax0), max(0.0, bx1 - bx0))
    heights = (max(0.0, ay1 - ay0), max(0.0, by1 - by0))

    def ratio(pair: tuple[float, float]) -> float:
        low, high = sorted(pair)
        return 1.0 if high == 0 else low / high

    return _mean([ratio(widths), ratio(heights)])


def _position_similarity(ref: _ElementRecord, cand: _ElementRecord) -> float:
    ax0, ay0, ax1, ay1 = bbox_tuple(ref.element)
    bx0, by0, bx1, by1 = bbox_tuple(cand.element)
    distance = math.hypot((ax0 + ax1 - bx0 - bx1) / 2, (ay0 + ay1 - by0 - by1) / 2)
    page_width = max(1.0, finite_number(value(ref.page, "width", 1.0), 1.0))
    page_height = max(1.0, finite_number(value(ref.page, "height", 1.0), 1.0))
    return _clamp(1.0 - distance / math.hypot(page_width, page_height))


def _ratio(left: float, right: float) -> float:
    low, high = sorted((max(0.0, left), max(0.0, right)))
    return 1.0 if high == 0 else low / high


def _page_similarity(reference: Any, candidate: Any) -> float:
    ref_pages = pages(reference)
    cand_pages = pages(candidate)
    maximum = max(len(ref_pages), len(cand_pages))
    if maximum == 0:
        return 1.0
    scores: list[float] = []
    for index in range(maximum):
        if index >= len(ref_pages) or index >= len(cand_pages):
            scores.append(0.0)
            continue
        ref, cand = ref_pages[index], cand_pages[index]
        scores.append(
            _mean(
                [
                    _ratio(finite_number(value(ref, "width")), finite_number(value(cand, "width"))),
                    _ratio(
                        finite_number(value(ref, "height")), finite_number(value(cand, "height"))
                    ),
                ]
            )
        )
    return _mean(scores)


@dataclass(frozen=True)
class LayoutMetrics:
    mean_iou: float
    position_similarity: float
    size_similarity: float
    page_similarity: float
    element_precision: float
    element_recall: float
    matched_elements: int
    reference_elements: int
    candidate_elements: int
    metric_version: str = LAYOUT_METRIC_VERSION

    @property
    def element_f1(self) -> float:
        denominator = self.element_precision + self.element_recall
        return (
            0.0
            if denominator == 0
            else 2 * self.element_precision * self.element_recall / denominator
        )

    @property
    def score(self) -> float:
        return _clamp(
            0.45 * self.mean_iou
            + 0.20 * self.position_similarity
            + 0.15 * self.size_similarity
            + 0.10 * self.page_similarity
            + 0.10 * self.element_f1
        )

    def to_dict(self) -> dict[str, Any]:
        return {**dataclasses.asdict(self), "element_f1": self.element_f1, "score": self.score}


def evaluate_layout(reference: Any, candidate: Any) -> LayoutMetrics:
    pairs, ref_count, cand_count = _match_elements(reference, candidate)
    return _layout_metrics(reference, candidate, pairs, ref_count, cand_count)


def _layout_metrics(
    reference: Any,
    candidate: Any,
    pairs: list[tuple[_ElementRecord, _ElementRecord]],
    ref_count: int,
    cand_count: int,
) -> LayoutMetrics:
    matched = len(pairs)
    precision = matched / cand_count if cand_count else float(ref_count == 0)
    recall = matched / ref_count if ref_count else float(cand_count == 0)
    empty_default = 1.0 if ref_count == cand_count == 0 else 0.0
    return LayoutMetrics(
        mean_iou=_mean(
            [_bbox_iou(ref.element, cand.element) for ref, cand in pairs], empty_default
        ),
        position_similarity=_mean(
            [_position_similarity(ref, cand) for ref, cand in pairs], empty_default
        ),
        size_similarity=_mean(
            [_size_similarity(ref.element, cand.element) for ref, cand in pairs], empty_default
        ),
        page_similarity=_page_similarity(reference, candidate),
        element_precision=precision,
        element_recall=recall,
        matched_elements=matched,
        reference_elements=ref_count,
        candidate_elements=cand_count,
    )


def _f1_from_counts(reference: Counter[str], candidate: Counter[str]) -> tuple[float, float, float]:
    matches = sum((reference & candidate).values())
    ref_total = sum(reference.values())
    cand_total = sum(candidate.values())
    precision = matches / cand_total if cand_total else float(ref_total == 0)
    recall = matches / ref_total if ref_total else float(cand_total == 0)
    denominator = precision + recall
    f1 = 0.0 if denominator == 0 else 2 * precision * recall / denominator
    return precision, recall, f1


def _reading_order_accuracy(pairs: list[tuple[_ElementRecord, _ElementRecord]]) -> float:
    if len(pairs) < 2:
        return 1.0

    def order(record: _ElementRecord) -> tuple[float, int]:
        found = value(record.element, "reading_order", None)
        return (finite_number(found, record.source_index), record.source_index)

    ordered = sorted(pairs, key=lambda pair: order(pair[0]))
    candidate_ranks = {
        pair[1].source_index: rank
        for rank, pair in enumerate(sorted(pairs, key=lambda pair: order(pair[1])))
    }
    ranks = [candidate_ranks[pair[1].source_index] for pair in ordered]
    concordant = 0
    total = 0
    for left in range(len(ranks)):
        for right in range(left + 1, len(ranks)):
            total += 1
            concordant += ranks[left] < ranks[right]
    return concordant / total if total else 1.0


def _relationship_accuracy(pairs: list[tuple[_ElementRecord, _ElementRecord]]) -> float:
    comparable: list[float] = []
    for ref, cand in pairs:
        ref_relationships = mapping(value(ref.element, "relationships", None))
        cand_relationships = mapping(value(cand.element, "relationships", None))
        for key in ("parent", "caption_of", "continued_from", "continued_to"):
            ref_value = ref_relationships.get(key)
            cand_value = cand_relationships.get(key)
            if ref_value is not None or cand_value is not None:
                comparable.append(float(ref_value == cand_value))
        ref_children = tuple(ref_relationships.get("children") or ())
        cand_children = tuple(cand_relationships.get("children") or ())
        if ref_children or cand_children:
            comparable.append(float(ref_children == cand_children))
    return _mean(comparable, 1.0)


@dataclass(frozen=True)
class _TableCellRecord:
    row: int
    column: int
    row_span: int
    column_span: int
    text: str


def _table_cells(element: Any) -> list[_TableCellRecord]:
    metadata = element_metadata(element)
    raw_cells = metadata.get("cells")
    if raw_cells is None and isinstance(metadata.get("table"), dict):
        raw_cells = metadata["table"].get("cells")
    cells: list[_TableCellRecord] = []
    if isinstance(raw_cells, Sequence) and not isinstance(raw_cells, (str, bytes, bytearray)):
        for raw in raw_cells:
            if not isinstance(raw, dict) and not hasattr(raw, "text"):
                continue
            row = max(0, int(finite_number(value(raw, "row", value(raw, "row_index", 0)))))
            column = max(
                0,
                int(
                    finite_number(
                        value(raw, "column", value(raw, "col", value(raw, "column_index", 0)))
                    )
                ),
            )
            cells.append(
                _TableCellRecord(
                    row=row,
                    column=column,
                    row_span=max(1, int(finite_number(value(raw, "row_span", 1), 1))),
                    column_span=max(
                        1,
                        int(finite_number(value(raw, "column_span", value(raw, "col_span", 1)), 1)),
                    ),
                    text=str(value(raw, "text", value(raw, "value", "")) or ""),
                )
            )
    if cells:
        minimum_row = min(cell.row for cell in cells)
        minimum_column = min(cell.column for cell in cells)
        return [
            dataclasses.replace(
                cell,
                row=cell.row - minimum_row,
                column=cell.column - minimum_column,
            )
            for cell in cells
        ]
    return [
        _TableCellRecord(row_index, column_index, 1, 1, str(text))
        for row_index, row in enumerate(table_rows(element))
        for column_index, text in enumerate(row)
    ]


def _table_dimensions(cells: Sequence[_TableCellRecord]) -> tuple[int, int]:
    return (
        max((cell.row + cell.row_span for cell in cells), default=0),
        max((cell.column + cell.column_span for cell in cells), default=0),
    )


def _table_pair_scores(ref: _ElementRecord, cand: _ElementRecord) -> tuple[float, float, float]:
    ref_cells = _table_cells(ref.element)
    cand_cells = _table_cells(cand.element)
    ref_rows, ref_columns = _table_dimensions(ref_cells)
    cand_rows, cand_columns = _table_dimensions(cand_cells)
    topology = _mean([_ratio(ref_rows, cand_rows), _ratio(ref_columns, cand_columns)])
    ref_by_position = {(cell.row, cell.column): cell for cell in ref_cells}
    cand_by_position = {(cell.row, cell.column): cell for cell in cand_cells}
    positions = set(ref_by_position) | set(cand_by_position)
    if not positions:
        return topology, 1.0, 1.0
    text_scores: list[float] = []
    span_scores: list[float] = []
    for position in sorted(positions):
        ref_cell = ref_by_position.get(position)
        cand_cell = cand_by_position.get(position)
        if ref_cell is None or cand_cell is None:
            text_scores.append(0.0)
            span_scores.append(0.0)
            continue
        text_scores.append(1.0 - _normalized_text_cost(ref_cell.text, cand_cell.text))
        span_scores.append(
            _mean(
                [
                    _ratio(ref_cell.row_span, cand_cell.row_span),
                    _ratio(ref_cell.column_span, cand_cell.column_span),
                ]
            )
        )
    return topology, _mean(text_scores, 0.0), _mean(span_scores, 0.0)


def _table_accuracy(
    pairs: list[tuple[_ElementRecord, _ElementRecord]],
    refs: Sequence[_ElementRecord],
    cands: Sequence[_ElementRecord],
) -> tuple[float, float, float, float]:
    ref_tables = [record for record in refs if record.type == "table"]
    cand_tables = [record for record in cands if record.type == "table"]
    if not ref_tables and not cand_tables:
        return 1.0, 1.0, 1.0, 1.0
    table_pairs = [pair for pair in pairs if pair[0].type == pair[1].type == "table"]
    denominator = max(len(ref_tables), len(cand_tables), 1)
    topology_scores: list[float] = []
    text_scores: list[float] = []
    span_scores: list[float] = []
    for ref, cand in table_pairs:
        topology, text, span = _table_pair_scores(ref, cand)
        topology_scores.append(topology)
        text_scores.append(text)
        span_scores.append(span)
    topology = sum(topology_scores) / denominator
    text = sum(text_scores) / denominator
    span = sum(span_scores) / denominator
    combined = 0.35 * topology + 0.40 * text + 0.25 * span
    return combined, topology, text, span


@dataclass(frozen=True)
class StructureMetrics:
    type_precision: float
    type_recall: float
    type_f1: float
    reading_order_accuracy: float
    hierarchy_accuracy: float
    table_structure_accuracy: float
    table_topology_accuracy: float = 1.0
    table_cell_text_accuracy: float = 1.0
    table_span_accuracy: float = 1.0
    metric_version: str = STRUCTURE_METRIC_VERSION

    @property
    def score(self) -> float:
        return _clamp(
            0.40 * self.type_f1
            + 0.25 * self.reading_order_accuracy
            + 0.20 * self.hierarchy_accuracy
            + 0.15 * self.table_structure_accuracy
        )

    def to_dict(self) -> dict[str, Any]:
        return {**dataclasses.asdict(self), "score": self.score}


def evaluate_structure(reference: Any, candidate: Any) -> StructureMetrics:
    pairs, _, _ = _match_elements(reference, candidate)
    return _structure_metrics(pairs, _records(reference), _records(candidate))


def _structure_metrics(
    pairs: list[tuple[_ElementRecord, _ElementRecord]],
    refs: list[_ElementRecord],
    cands: list[_ElementRecord],
) -> StructureMetrics:
    ref_types = Counter(record.type for record in refs)
    cand_types = Counter(record.type for record in cands)
    precision, recall, f1 = _f1_from_counts(ref_types, cand_types)
    table, table_topology, table_text, table_span = _table_accuracy(pairs, refs, cands)
    return StructureMetrics(
        type_precision=precision,
        type_recall=recall,
        type_f1=f1,
        reading_order_accuracy=_reading_order_accuracy(pairs),
        hierarchy_accuracy=_relationship_accuracy(pairs),
        table_structure_accuracy=table,
        table_topology_accuracy=table_topology,
        table_cell_text_accuracy=table_text,
        table_span_accuracy=table_span,
    )


def evaluate_layout_and_structure(
    reference: Any,
    candidate: Any,
) -> tuple[LayoutMetrics, StructureMetrics]:
    """Score layout and structure from one element matching.

    `evaluate_layout` and `evaluate_structure` each ran `_match_elements` on the
    same two documents, and that is the expensive step: a dense cost grid whose
    every cell is an edit distance, followed by a cubic assignment. A caller
    that wants both metrics pays for it once here.
    """

    pairs, ref_count, cand_count = _match_elements(reference, candidate)
    return (
        _layout_metrics(reference, candidate, pairs, ref_count, cand_count),
        _structure_metrics(pairs, _records(reference), _records(candidate)),
    )


_NATIVE_TEXT_TYPES = {
    "text",
    "title",
    "heading",
    "paragraph",
    "list_item",
    "caption",
    "formula",
    "header",
    "footer",
    "footnote",
    "page_number",
    "checkbox",
}
_NATIVE_STRUCTURAL_TYPES = {"table"}


@dataclass(frozen=True)
class EditabilityMetrics:
    editable_elements: int
    flattened_elements: int
    total_elements: int
    editable_ratio: float
    native_text_ratio: float
    native_structure_ratio: float
    metric_version: str = EDITABILITY_METRIC_VERSION

    @property
    def score(self) -> float:
        return _clamp(self.editable_ratio)

    def to_dict(self) -> dict[str, Any]:
        return {**dataclasses.asdict(self), "score": self.score}


def _evaluate_docx_artifact(path: Path) -> EditabilityMetrics:
    try:
        with zipfile.ZipFile(path) as archive:
            xml = archive.read("word/document.xml")
    except (OSError, KeyError, zipfile.BadZipFile) as exc:
        raise ValueError(f"not a readable DOCX artifact: {path}") from exc
    try:
        from xml.etree import ElementTree

        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError as exc:
        raise ValueError(f"not a readable DOCX artifact: {path}") from exc
    word = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    drawing = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}drawing"
    pict = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}pict"
    math_text = "{http://schemas.openxmlformats.org/officeDocument/2006/math}t"
    paragraphs = sum(
        1
        for paragraph in root.iter(word + "p")
        if any(
            (node.text or "").strip()
            for node in paragraph.iter()
            if node.tag in {word + "t", math_text}
        )
    )
    tables = sum(1 for _ in root.iter(word + "tbl"))
    drawings = sum(1 for node in root.iter() if node.tag in {drawing, pict})
    editable = paragraphs + tables
    total = editable + drawings
    return EditabilityMetrics(
        editable_elements=editable,
        flattened_elements=drawings,
        total_elements=total,
        editable_ratio=editable / total if total else 1.0,
        native_text_ratio=paragraphs / total if total else 1.0,
        native_structure_ratio=tables / total if total else 1.0,
    )


def evaluate_editability(candidate: Any, output_format: str | None = None) -> EditabilityMetrics:
    if isinstance(candidate, (str, Path)):
        path = Path(candidate)
        suffix = (output_format or path.suffix.lstrip(".")).lower()
        if suffix == "docx" and path.is_file():
            return _evaluate_docx_artifact(path)
        if suffix in {
            "image",
            "png",
            "jpg",
            "jpeg",
            "tif",
            "tiff",
            "bmp",
            "gif",
            "webp",
            "pdf",
        }:
            return EditabilityMetrics(0, 1, 1, 0.0, 0.0, 0.0)
        raise ValueError("artifact editability currently supports DOCX or raster/flattened formats")

    records = _records(candidate)
    editable = 0
    flattened = 0
    native_text = 0
    native_structure = 0
    for record in records:
        metadata = element_metadata(record.element)
        explicit = metadata.get("editable")
        is_flattened = bool(metadata.get("flattened", False))
        has_native_text = bool(record.text.strip()) or bool(
            metadata.get("latex") or metadata.get("omml") or metadata.get("native_text")
        )
        if explicit is not None:
            is_editable = (
                bool(explicit)
                and not is_flattened
                and (record.type in _NATIVE_STRUCTURAL_TYPES or has_native_text)
            )
        else:
            is_editable = (
                (record.type in _NATIVE_TEXT_TYPES and has_native_text)
                or record.type in _NATIVE_STRUCTURAL_TYPES
            ) and not is_flattened
        editable += is_editable
        flattened += is_flattened or (
            record.type in {"image", "figure"} and bool(metadata.get("full_page", False))
        )
        native_text += is_editable and record.type in _NATIVE_TEXT_TYPES
        native_structure += is_editable and record.type in _NATIVE_STRUCTURAL_TYPES
    total = len(records)
    return EditabilityMetrics(
        editable_elements=editable,
        flattened_elements=flattened,
        total_elements=total,
        editable_ratio=editable / total if total else 1.0,
        native_text_ratio=native_text / total if total else 1.0,
        native_structure_ratio=native_structure / total if total else 1.0,
    )


# Compact aliases used by callers that treat metrics as functions.
text_fidelity = evaluate_text
layout_fidelity = evaluate_layout
structure_fidelity = evaluate_structure
editability_fidelity = evaluate_editability
