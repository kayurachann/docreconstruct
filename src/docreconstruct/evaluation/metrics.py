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


def _error_rate(reference: Sequence[Any], candidate: Sequence[Any]) -> float:
    if not reference:
        return 0.0 if not candidate else 1.0
    return _distance(reference, candidate) / len(reference)


def _accuracy(reference: Sequence[Any], candidate: Sequence[Any]) -> float:
    denominator = max(len(reference), len(candidate), 1)
    return _clamp(1.0 - _distance(reference, candidate) / denominator)


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
    return TextMetrics(
        character_error_rate=_error_rate(reference_text, candidate_text),
        word_error_rate=_error_rate(reference_words, candidate_words),
        character_accuracy=_accuracy(reference_text, candidate_text),
        word_accuracy=_accuracy(reference_words, candidate_words),
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


def _match_elements(
    reference: Any, candidate: Any
) -> tuple[list[tuple[_ElementRecord, _ElementRecord]], int, int]:
    """Match IDs first, then exact type/text pairs, then type-only pairs."""

    refs = _records(reference)
    cands = _records(candidate)
    pairs: list[tuple[_ElementRecord, _ElementRecord]] = []
    used_ref: set[int] = set()
    used_cand: set[int] = set()

    candidate_ids: dict[str, int] = {}
    duplicate_ids: set[str] = set()
    for index, record in enumerate(cands):
        if not record.id:
            continue
        if record.id in candidate_ids:
            duplicate_ids.add(record.id)
        candidate_ids[record.id] = index
    for ref_index, record in enumerate(refs):
        if record.id and record.id not in duplicate_ids and record.id in candidate_ids:
            cand_index = candidate_ids[record.id]
            if cand_index not in used_cand:
                pairs.append((record, cands[cand_index]))
                used_ref.add(ref_index)
                used_cand.add(cand_index)

    for strict_text in (True, False):
        for ref_index, ref in enumerate(refs):
            if ref_index in used_ref:
                continue
            best: int | None = None
            for cand_index, cand in enumerate(cands):
                if cand_index in used_cand or ref.type != cand.type:
                    continue
                if strict_text and ref.text != cand.text:
                    continue
                best = cand_index
                # Prefer the same page even when IDs were not propagated.
                if ref.page_index == cand.page_index:
                    break
            if best is not None:
                pairs.append((ref, cands[best]))
                used_ref.add(ref_index)
                used_cand.add(best)
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


def _table_accuracy(pairs: list[tuple[_ElementRecord, _ElementRecord]]) -> float:
    table_pairs = [pair for pair in pairs if pair[0].type == "table"]
    scores: list[float] = []
    for ref, cand in table_pairs:
        ref_rows, cand_rows = table_rows(ref.element), table_rows(cand.element)
        ref_columns = max((len(row) for row in ref_rows), default=0)
        cand_columns = max((len(row) for row in cand_rows), default=0)
        scores.append(
            _mean([_ratio(len(ref_rows), len(cand_rows)), _ratio(ref_columns, cand_columns)])
        )
    return _mean(scores, 1.0)


@dataclass(frozen=True)
class StructureMetrics:
    type_precision: float
    type_recall: float
    type_f1: float
    reading_order_accuracy: float
    hierarchy_accuracy: float
    table_structure_accuracy: float

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
    ref_types = Counter(record.type for record in _records(reference))
    cand_types = Counter(record.type for record in _records(candidate))
    precision, recall, f1 = _f1_from_counts(ref_types, cand_types)
    return StructureMetrics(
        type_precision=precision,
        type_recall=recall,
        type_f1=f1,
        reading_order_accuracy=_reading_order_accuracy(pairs),
        hierarchy_accuracy=_relationship_accuracy(pairs),
        table_structure_accuracy=_table_accuracy(pairs),
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
    paragraphs = xml.count(b"<w:p>") + xml.count(b"<w:p ")
    tables = xml.count(b"<w:tbl>") + xml.count(b"<w:tbl ")
    drawings = xml.count(b"<w:drawing>") + xml.count(b"<w:drawing ") + xml.count(b"<w:pict>")
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
        if explicit is not None:
            is_editable = bool(explicit) and not is_flattened
        else:
            is_editable = (
                record.type in _NATIVE_TEXT_TYPES | _NATIVE_STRUCTURAL_TYPES and not is_flattened
            )
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
