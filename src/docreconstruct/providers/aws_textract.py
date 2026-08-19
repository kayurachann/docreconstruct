"""Offline normalization for saved Amazon Textract response JSON.

The adapter intentionally has no AWS SDK, HTTP, credential, or SigV4 surface.
It consumes the ``Blocks`` returned by ``DetectDocumentText`` or
``AnalyzeDocument`` and preserves their graph in the canonical IR.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from docreconstruct.ir import (
    BBox,
    Document,
    Element,
    ElementStyle,
    ElementType,
    Page,
    Point,
    Provenance,
    Relationship,
    SourceType,
    TextCandidate,
)

from ._utils import as_float, document_id
from .base import (
    ProviderCapabilities,
    ProviderContext,
    ProviderCost,
    ProviderCredentialRequirement,
    ProviderExecutionMode,
    ProviderInputError,
    ProviderLicense,
    ProviderPrivacy,
    SavedJSONProvider,
)

_ANALYSIS_BLOCK_TYPES = {
    "CELL",
    "KEY_VALUE_SET",
    "MERGED_CELL",
    "QUERY",
    "QUERY_RESULT",
    "SELECTION_ELEMENT",
    "SIGNATURE",
    "TABLE",
    "TABLE_FOOTER",
    "TABLE_TITLE",
    "TITLE",
}
_LAYOUT_TYPES = {
    "LAYOUT_FIGURE",
    "LAYOUT_FOOTER",
    "LAYOUT_HEADER",
    "LAYOUT_KEY_VALUE",
    "LAYOUT_LIST",
    "LAYOUT_PAGE_NUMBER",
    "LAYOUT_SECTION_HEADER",
    "LAYOUT_TABLE",
    "LAYOUT_TEXT",
    "LAYOUT_TITLE",
}


@dataclass(frozen=True)
class _Block:
    data: Mapping[str, Any]
    id: str
    type: str
    index: int
    generated_id: bool = False


class _BlockGraph:
    """Indexed Textract block graph with cycle-safe text reconstruction."""

    def __init__(self, blocks: Sequence[_Block]) -> None:
        self.blocks = list(blocks)
        self.by_id = {block.id: block for block in blocks}
        self.relationships = {block.id: _relationship_types(block.data) for block in blocks}
        self.incoming: dict[str, list[tuple[str, str]]] = {}
        for block in blocks:
            for relation_type, target_ids in self.relationships[block.id].items():
                for target_id in target_ids:
                    self.incoming.setdefault(target_id, []).append((block.id, relation_type))
        self._text_cache: dict[str, str | None] = {}
        self._text_type_cache: dict[str, tuple[str, ...]] = {}

    def text(self, block_id: str) -> str | None:
        return self._text(block_id, set())

    def _text(self, block_id: str, trail: set[str]) -> str | None:
        if block_id in self._text_cache:
            return self._text_cache[block_id]
        if block_id in trail:
            return None
        block = self.by_id.get(block_id)
        if block is None:
            return None

        direct = block.data.get("Text")
        if isinstance(direct, str) and direct:
            self._text_cache[block_id] = direct
            return direct
        if block.type == "QUERY":
            query = block.data.get("Query")
            if isinstance(query, Mapping):
                question = query.get("Text")
                if isinstance(question, str) and question:
                    self._text_cache[block_id] = question
                    return question
        if block.type == "SELECTION_ELEMENT":
            selected = str(block.data.get("SelectionStatus") or "").upper() == "SELECTED"
            selection_text = "☒" if selected else "☐"
            self._text_cache[block_id] = selection_text
            return selection_text

        next_trail = {*trail, block_id}
        pieces: list[str] = []
        child_types: list[str] = []
        for child_id in self.relationships[block_id].get("CHILD", []):
            child = self.by_id.get(child_id)
            if child is not None:
                child_types.append(child.type)
            piece = self._text(child_id, next_trail)
            if piece and (not pieces or pieces[-1] != piece):
                pieces.append(piece)
        resolved_text: str | None
        if not pieces:
            resolved_text = None
        elif block.type in _LAYOUT_TYPES or "LINE" in child_types:
            resolved_text = "\n".join(pieces)
        else:
            resolved_text = " ".join(pieces)
        self._text_cache[block_id] = resolved_text
        return resolved_text

    def text_types(self, block_id: str) -> tuple[str, ...]:
        return self._text_types(block_id, set())

    def _text_types(self, block_id: str, trail: set[str]) -> tuple[str, ...]:
        if block_id in self._text_type_cache:
            return self._text_type_cache[block_id]
        if block_id in trail:
            return ()
        block = self.by_id.get(block_id)
        if block is None:
            return ()

        result: list[str] = []
        direct = str(block.data.get("TextType") or "").strip().upper()
        if direct in {"HANDWRITING", "PRINTED"}:
            result.append(direct)
        next_trail = {*trail, block_id}
        for child_id in self.relationships[block_id].get("CHILD", []):
            for text_type in self._text_types(child_id, next_trail):
                if text_type not in result:
                    result.append(text_type)
        value = tuple(result)
        self._text_type_cache[block_id] = value
        return value


class AWSTextractProvider(SavedJSONProvider):
    """Normalize saved DetectDocumentText and AnalyzeDocument responses."""

    name = "aws_textract"
    _capabilities = ProviderCapabilities(
        provider=name,
        supported_inputs=["json"],
        saved_json=True,
        live_inference=False,
        text=True,
        geometry=True,
        reading_order=True,
        styles=False,
        tables=True,
        images=False,
        multilingual=False,
        handwriting=True,
        formulas=False,
        charts=False,
        layout=True,
        execution_modes=[ProviderExecutionMode.SAVED],
        markdown=False,
        bounding_boxes=True,
        confidence_scores=True,
        privacy=ProviderPrivacy.NO_TRANSFER,
        license=ProviderLicense(
            name="Amazon Textract service output",
            open_source=False,
            commercial_use=True,
        ),
        model_name="Amazon Textract saved response",
        cost=ProviderCost.FREE,
        credentials=ProviderCredentialRequirement.NONE,
        notes=[
            "Normalizes saved DetectDocumentText and AnalyzeDocument JSON only.",
            "No AWS SDK, network request, credential handling, or SigV4 signing is bundled.",
        ],
    )

    @property
    def capabilities(self) -> ProviderCapabilities:
        return self._capabilities

    def normalize(
        self,
        payload: Any,
        *,
        context: ProviderContext | None = None,
    ) -> Document:
        response = _textract_response(payload)
        blocks = _blocks(response)
        graph = _BlockGraph(blocks)
        page_assignments, page_numbers, page_blocks = _assign_pages(
            blocks,
            graph,
            response.get("DocumentMetadata"),
        )
        operation, model_version = _operation_and_model(response, blocks)
        emitted_ids = {block.id for block in blocks if block.type != "PAGE"}
        page_items: dict[int, list[tuple[int, Element]]] = {number: [] for number in page_numbers}

        for block in blocks:
            if block.type == "PAGE":
                continue
            number = page_assignments[block.id]
            width, height = _page_dimensions(context)
            bbox, polygon, rotation, has_geometry = _geometry(
                block.data,
                width=width,
                height=height,
            )
            score = _textract_confidence(block.data.get("Confidence"))
            text = None if block.type == "TABLE" else graph.text(block.id)
            metadata = _block_metadata(
                block,
                graph=graph,
                page_number=number,
                has_geometry=has_geometry,
                context=context,
            )
            if block.type == "TABLE":
                metadata.update(_table_metadata(block, graph))
            relationships = _canonical_relationships(
                block,
                graph=graph,
                emitted_ids=emitted_ids,
            )
            provenance_metadata: dict[str, Any] = {
                "block_type": block.type,
                "operation": operation,
            }
            if model_version is not None:
                provenance_metadata["model_version"] = model_version
            text_types = graph.text_types(block.id)
            if text_types:
                provenance_metadata["text_types"] = list(text_types)
            candidate_metadata: dict[str, Any] = {"block_type": block.type}
            if text_types:
                candidate_metadata["text_types"] = list(text_types)
            element = Element(
                id=block.id,
                type=_element_type(block),
                bbox=bbox,
                polygon=polygon,
                text=text,
                confidence=score,
                style=ElementStyle(rotation=rotation),
                relationships=relationships,
                provenance=Provenance(
                    engine=self.name,
                    source_id=block.id,
                    text_confidence=score if text is not None else None,
                    layout_confidence=score,
                    metadata=provenance_metadata,
                ),
                text_candidates=(
                    [
                        TextCandidate(
                            engine=self.name,
                            value=text,
                            confidence=score,
                            source_element_id=block.id,
                            metadata=candidate_metadata,
                        )
                    ]
                    if text is not None
                    else []
                ),
                metadata=metadata,
            )
            page_items.setdefault(number, []).append((block.index, element))

        pages: list[Page] = []
        width, height = _page_dimensions(context)
        coordinate_system = (
            "context_scaled_normalized"
            if context is not None and (context.page_width or context.page_height)
            else "normalized"
        )
        document_metadata = response.get("DocumentMetadata")
        for number in page_numbers:
            ordered = sorted(page_items.get(number, []), key=lambda item: item[0])
            elements = [
                element.model_copy(update={"reading_order": order})
                for order, (_, element) in enumerate(ordered)
            ]
            page_block = page_blocks.get(number)
            page_metadata: dict[str, Any] = {
                "provider": self.name,
                "coordinate_system": coordinate_system,
            }
            if page_block is not None:
                page_metadata["source_page_id"] = page_block.id
            pages.append(
                Page(
                    id=f"page-{number}",
                    number=number,
                    width=width,
                    height=height,
                    elements=elements,
                    source_type=SourceType.IMAGE,
                    metadata=page_metadata,
                )
            )

        document_result_metadata: dict[str, Any] = {
            "provider": self.name,
            "operation": operation,
        }
        if model_version is not None:
            document_result_metadata["model_version"] = model_version
        if isinstance(document_metadata, Mapping):
            document_result_metadata["document_metadata"] = dict(document_metadata)
        return Document(
            id=document_id(self.name, context),
            pages=pages,
            source=context.source if context else None,
            metadata=document_result_metadata,
        )


def _textract_response(payload: Any) -> Mapping[str, Any]:
    if not isinstance(payload, Mapping):
        raise ProviderInputError("Amazon Textract saved response must be a JSON object")
    if "Blocks" not in payload:
        raise ProviderInputError("Amazon Textract saved response must contain a Blocks array")
    return payload


def _blocks(response: Mapping[str, Any]) -> list[_Block]:
    raw_blocks = response.get("Blocks")
    if not isinstance(raw_blocks, Sequence) or isinstance(raw_blocks, (str, bytes, bytearray)):
        raise ProviderInputError("Amazon Textract Blocks must be an array")
    blocks: list[_Block] = []
    seen: set[str] = set()
    for index, raw_block in enumerate(raw_blocks):
        if not isinstance(raw_block, Mapping):
            raise ProviderInputError(f"Amazon Textract Blocks[{index}] must be an object")
        raw_id = raw_block.get("Id")
        if isinstance(raw_id, str) and raw_id.strip():
            generated = False
            block_id = raw_id.strip()
        else:
            generated = True
            block_id = f"aws-textract-block-{index + 1}"
        if block_id in seen:
            raise ProviderInputError(f"duplicate Amazon Textract block Id: {block_id}")
        seen.add(block_id)
        block_type = str(raw_block.get("BlockType") or "UNKNOWN").strip().upper()
        blocks.append(
            _Block(
                data=raw_block,
                id=block_id,
                type=block_type or "UNKNOWN",
                index=index,
                generated_id=generated,
            )
        )
    return blocks


def _relationship_types(block: Mapping[str, Any]) -> dict[str, list[str]]:
    raw_relationships = block.get("Relationships")
    if not isinstance(raw_relationships, Sequence) or isinstance(
        raw_relationships, (str, bytes, bytearray)
    ):
        return {}
    result: dict[str, list[str]] = {}
    for raw_relationship in raw_relationships:
        if not isinstance(raw_relationship, Mapping):
            continue
        relation_type = str(raw_relationship.get("Type") or "").strip().upper()
        raw_ids = raw_relationship.get("Ids")
        if (
            not relation_type
            or not isinstance(raw_ids, Sequence)
            or isinstance(raw_ids, (str, bytes, bytearray))
        ):
            continue
        target_ids = result.setdefault(relation_type, [])
        for raw_id in raw_ids:
            if isinstance(raw_id, str) and raw_id.strip() and raw_id.strip() not in target_ids:
                target_ids.append(raw_id.strip())
    return result


def _assign_pages(
    blocks: Sequence[_Block],
    graph: _BlockGraph,
    raw_document_metadata: Any,
) -> tuple[dict[str, int], list[int], dict[int, _Block]]:
    assignments: dict[str, int] = {}
    page_blocks: dict[int, _Block] = {}
    page_records = [block for block in blocks if block.type == "PAGE"]
    for fallback, block in enumerate(page_records, start=1):
        number = _positive_int(block.data.get("Page")) or fallback
        while number in page_blocks:
            number += 1
        page_blocks[number] = block
        assignments[block.id] = number

    for block in blocks:
        explicit = _positive_int(block.data.get("Page"))
        if block.type != "PAGE" and explicit is not None:
            assignments[block.id] = explicit

    queue = deque((block.id, number) for number, block in page_blocks.items())
    visited: set[tuple[str, int]] = set()
    while queue:
        block_id, number = queue.popleft()
        if (block_id, number) in visited:
            continue
        visited.add((block_id, number))
        for target_ids in graph.relationships.get(block_id, {}).values():
            for target_id in target_ids:
                if target_id not in graph.by_id:
                    continue
                assignments.setdefault(target_id, number)
                if assignments[target_id] == number:
                    queue.append((target_id, number))

    current_page = min(page_blocks, default=1)
    for block in blocks:
        if block.type == "PAGE":
            current_page = assignments[block.id]
        else:
            assignments.setdefault(block.id, current_page)
            current_page = assignments[block.id]

    page_numbers = set(assignments.values())
    if isinstance(raw_document_metadata, Mapping):
        page_count = _positive_int(raw_document_metadata.get("Pages"))
        if page_count is not None:
            page_numbers.update(range(1, page_count + 1))
    if blocks and not page_numbers:
        page_numbers.add(1)
    return assignments, sorted(page_numbers), page_blocks


def _page_dimensions(context: ProviderContext | None) -> tuple[float, float]:
    width = context.page_width if context and context.page_width is not None else 1.0
    height = context.page_height if context and context.page_height is not None else 1.0
    return float(width), float(height)


def _geometry(
    block: Mapping[str, Any],
    *,
    width: float,
    height: float,
) -> tuple[BBox, list[Point], float | None, bool]:
    geometry = block.get("Geometry")
    if not isinstance(geometry, Mapping):
        return BBox(x0=0, y0=0, x1=0, y1=0), [], None, False

    polygon: list[Point] = []
    raw_polygon = geometry.get("Polygon")
    if isinstance(raw_polygon, Sequence) and not isinstance(raw_polygon, (str, bytes, bytearray)):
        for raw_point in raw_polygon:
            if not isinstance(raw_point, Mapping):
                polygon = []
                break
            x = as_float(raw_point.get("X"))
            y = as_float(raw_point.get("Y"))
            if x is None or y is None:
                polygon = []
                break
            polygon.append(Point(x=x * width, y=y * height))

    bbox: BBox | None = None
    raw_bbox = geometry.get("BoundingBox")
    if isinstance(raw_bbox, Mapping):
        left = as_float(raw_bbox.get("Left"))
        top = as_float(raw_bbox.get("Top"))
        box_width = as_float(raw_bbox.get("Width"))
        box_height = as_float(raw_bbox.get("Height"))
        if None not in (left, top, box_width, box_height):
            assert left is not None
            assert top is not None
            assert box_width is not None
            assert box_height is not None
            x0, x1 = sorted((left * width, (left + box_width) * width))
            y0, y1 = sorted((top * height, (top + box_height) * height))
            bbox = BBox(x0=x0, y0=y0, x1=x1, y1=y1)
    if bbox is None and polygon:
        bbox = BBox(
            x0=min(point.x for point in polygon),
            y0=min(point.y for point in polygon),
            x1=max(point.x for point in polygon),
            y1=max(point.y for point in polygon),
        )
    rotation = as_float(geometry.get("RotationAngle"))
    return bbox or BBox(x0=0, y0=0, x1=0, y1=0), polygon, rotation, bbox is not None


def _textract_confidence(value: Any) -> float | None:
    score = as_float(value)
    if score is None:
        return None
    return max(0.0, min(1.0, score / 100.0))


def _element_type(block: _Block) -> ElementType:
    direct = {
        "LAYOUT_FIGURE": ElementType.FIGURE,
        "LAYOUT_FOOTER": ElementType.FOOTER,
        "LAYOUT_HEADER": ElementType.HEADER,
        "LAYOUT_LIST": ElementType.LIST_ITEM,
        "LAYOUT_PAGE_NUMBER": ElementType.PAGE_NUMBER,
        "LAYOUT_SECTION_HEADER": ElementType.HEADING,
        "LAYOUT_TABLE": ElementType.TABLE,
        "LAYOUT_TEXT": ElementType.PARAGRAPH,
        "LAYOUT_TITLE": ElementType.TITLE,
        "SELECTION_ELEMENT": ElementType.CHECKBOX,
        "SIGNATURE": ElementType.SIGNATURE,
        "TABLE": ElementType.TABLE,
        "TABLE_FOOTER": ElementType.FOOTER,
        "TABLE_TITLE": ElementType.TITLE,
        "TITLE": ElementType.TITLE,
    }
    return direct.get(
        block.type,
        ElementType.TEXT
        if block.type
        in {
            "CELL",
            "KEY_VALUE_SET",
            "LAYOUT_KEY_VALUE",
            "LINE",
            "MERGED_CELL",
            "QUERY",
            "QUERY_RESULT",
            "WORD",
        }
        else ElementType.UNKNOWN,
    )


def _block_metadata(
    block: _Block,
    *,
    graph: _BlockGraph,
    page_number: int,
    has_geometry: bool,
    context: ProviderContext | None,
) -> dict[str, Any]:
    relation_types = graph.relationships[block.id]
    metadata: dict[str, Any] = {
        "provider": AWSTextractProvider.name,
        "block_type": block.type,
        "source_block_index": block.index,
        "source_page_number": page_number,
        "coordinate_system": (
            "unavailable"
            if not has_geometry
            else "context_scaled_normalized"
            if context is not None and (context.page_width or context.page_height)
            else "normalized"
        ),
    }
    if block.generated_id:
        metadata["generated_source_id"] = True
    if relation_types:
        metadata["relationship_types"] = relation_types
    raw_confidence = as_float(block.data.get("Confidence"))
    if raw_confidence is not None:
        metadata["confidence_percent"] = raw_confidence
    geometry = block.data.get("Geometry")
    if isinstance(geometry, Mapping):
        metadata["normalized_geometry"] = dict(geometry)

    entity_types = _entity_types(block.data)
    if entity_types:
        metadata["entity_types"] = entity_types
    text_types = list(graph.text_types(block.id))
    if text_types:
        metadata["text_types"] = text_types
        if len(text_types) == 1:
            metadata["text_type"] = text_types[0]
        metadata["handwriting"] = "HANDWRITING" in text_types
        metadata["printed"] = "PRINTED" in text_types

    for source_name, metadata_name in (
        ("RowIndex", "row_index"),
        ("ColumnIndex", "column_index"),
        ("RowSpan", "row_span"),
        ("ColumnSpan", "column_span"),
    ):
        value = _positive_int(block.data.get(source_name))
        if value is not None:
            metadata[metadata_name] = value

    if block.type == "SELECTION_ELEMENT":
        status = str(block.data.get("SelectionStatus") or "").strip().upper()
        metadata["selection_status"] = status
        metadata["selected"] = status == "SELECTED"
    if block.type.startswith("LAYOUT_"):
        metadata["layout_type"] = block.type.removeprefix("LAYOUT_").lower()

    if block.type == "KEY_VALUE_SET":
        if "KEY" in entity_types:
            value_ids = relation_types.get("VALUE", [])
            metadata["key_value_role"] = "key"
            metadata["value_ids"] = value_ids
            values = [graph.text(value_id) for value_id in value_ids]
            value_texts = [value for value in values if value is not None]
            metadata["values"] = value_texts
            if value_texts:
                metadata["value_text"] = " ".join(value_texts)
        elif "VALUE" in entity_types:
            key_ids = [
                source_id
                for source_id, relation_type in graph.incoming.get(block.id, [])
                if relation_type == "VALUE"
            ]
            metadata["key_value_role"] = "value"
            metadata["key_ids"] = key_ids
            key_texts = [graph.text(key_id) for key_id in key_ids]
            present_keys = [value for value in key_texts if value is not None]
            if present_keys:
                metadata["key_text"] = " ".join(present_keys)

    if block.type == "QUERY":
        query = block.data.get("Query")
        if isinstance(query, Mapping):
            query_metadata = {
                str(key): value for key, value in query.items() if key in {"Text", "Alias", "Pages"}
            }
            metadata["query"] = query_metadata
            if isinstance(query.get("Text"), str):
                metadata["query_text"] = query["Text"]
            if isinstance(query.get("Alias"), str):
                metadata["query_alias"] = query["Alias"]
        answer_ids = relation_types.get("ANSWER", [])
        metadata["answer_ids"] = answer_ids
        metadata["answers"] = [
            {
                "id": answer_id,
                "text": graph.text(answer_id),
                "confidence": _textract_confidence(graph.by_id[answer_id].data.get("Confidence")),
            }
            for answer_id in answer_ids
            if answer_id in graph.by_id
        ]
    elif block.type == "QUERY_RESULT":
        query_ids = [
            source_id
            for source_id, relation_type in graph.incoming.get(block.id, [])
            if relation_type == "ANSWER"
        ]
        metadata["query_ids"] = query_ids
        if query_ids:
            query = graph.by_id[query_ids[0]].data.get("Query")
            if isinstance(query, Mapping):
                if isinstance(query.get("Text"), str):
                    metadata["query_text"] = query["Text"]
                if isinstance(query.get("Alias"), str):
                    metadata["query_alias"] = query["Alias"]
    return metadata


def _canonical_relationships(
    block: _Block,
    *,
    graph: _BlockGraph,
    emitted_ids: set[str],
) -> Relationship:
    relation_types = graph.relationships[block.id]
    children = [
        target_id for target_id in relation_types.get("CHILD", []) if target_id in emitted_ids
    ]
    incoming = graph.incoming.get(block.id, [])
    parent_candidates = [
        source_id
        for source_id, relation_type in incoming
        if relation_type == "CHILD" and source_id in emitted_ids
    ]
    if not parent_candidates:
        parent_candidates = [
            source_id
            for source_id, relation_type in incoming
            if relation_type in {"ANSWER", "MERGED_CELL", "VALUE"} and source_id in emitted_ids
        ]
    references: list[str] = []
    for relation_type, target_ids in relation_types.items():
        if relation_type == "CHILD":
            continue
        for target_id in target_ids:
            if target_id in emitted_ids and target_id not in references:
                references.append(target_id)
    for source_id, relation_type in incoming:
        if relation_type != "CHILD" and source_id in emitted_ids and source_id not in references:
            references.append(source_id)

    caption_candidates = [
        source_id
        for source_id, relation_type in incoming
        if relation_type in {"TABLE_TITLE", "TABLE_FOOTER", "TITLE"} and source_id in emitted_ids
    ]
    relationship_metadata: dict[str, Any] = {}
    if relation_types:
        relationship_metadata["outgoing"] = relation_types
    if incoming:
        relationship_metadata["incoming"] = [
            {"source_id": source_id, "type": relation_type} for source_id, relation_type in incoming
        ]
    return Relationship(
        parent=parent_candidates[0] if parent_candidates else None,
        caption_of=caption_candidates[0] if caption_candidates else None,
        children=children,
        references=references,
        metadata=relationship_metadata,
    )


def _table_metadata(block: _Block, graph: _BlockGraph) -> dict[str, Any]:
    relationships = graph.relationships[block.id]
    cell_ids = [
        block_id
        for block_id in relationships.get("CHILD", [])
        if block_id in graph.by_id and graph.by_id[block_id].type == "CELL"
    ]
    merged_ids = [
        block_id
        for block_id in relationships.get("MERGED_CELL", [])
        if block_id in graph.by_id and graph.by_id[block_id].type == "MERGED_CELL"
    ]
    cells = [_cell_metadata(graph.by_id[cell_id], graph) for cell_id in cell_ids]
    merged_cells = [_cell_metadata(graph.by_id[cell_id], graph) for cell_id in merged_ids]
    row_count = max(
        (int(cell.get("row_index", 0)) + int(cell.get("row_span", 1)) - 1 for cell in cells),
        default=0,
    )
    column_count = max(
        (int(cell.get("column_index", 0)) + int(cell.get("column_span", 1)) - 1 for cell in cells),
        default=0,
    )
    rows = [["" for _ in range(column_count)] for _ in range(row_count)]
    for cell in cells:
        row_index = int(cell.get("row_index", 0))
        column_index = int(cell.get("column_index", 0))
        if row_index > 0 and column_index > 0:
            rows[row_index - 1][column_index - 1] = str(cell.get("text") or "")
    header_rows = sorted(
        {
            int(cell["row_index"])
            for cell in cells
            if "COLUMN_HEADER" in cell.get("entity_types", []) and "row_index" in cell
        }
    )
    table_payload: dict[str, Any] = {
        "rows": rows,
        "cells": cells,
        "merged_cells": merged_cells,
    }
    result: dict[str, Any] = {
        "rows": rows,
        "cells": cells,
        "merged_cells": merged_cells,
        "row_count": row_count,
        "column_count": column_count,
        "table": table_payload,
    }
    if header_rows:
        result["header_rows"] = len(header_rows)
        result["header_row_indices"] = header_rows
    entity_types = _entity_types(block.data)
    if entity_types:
        result["table_entity_types"] = entity_types
    for relation_type, metadata_name in (
        ("TABLE_TITLE", "title_ids"),
        ("TABLE_FOOTER", "footer_ids"),
    ):
        if relationships.get(relation_type):
            result[metadata_name] = relationships[relation_type]
    return result


def _cell_metadata(block: _Block, graph: _BlockGraph) -> dict[str, Any]:
    result: dict[str, Any] = {
        "id": block.id,
        "block_type": block.type,
        "text": graph.text(block.id),
        "confidence": _textract_confidence(block.data.get("Confidence")),
    }
    for source_name, metadata_name in (
        ("RowIndex", "row_index"),
        ("ColumnIndex", "column_index"),
        ("RowSpan", "row_span"),
        ("ColumnSpan", "column_span"),
    ):
        value = _positive_int(block.data.get(source_name))
        if value is not None:
            result[metadata_name] = value
    entity_types = _entity_types(block.data)
    if entity_types:
        result["entity_types"] = entity_types
    child_ids = graph.relationships[block.id].get("CHILD", [])
    if child_ids:
        result["child_ids"] = child_ids
    return result


def _entity_types(block: Mapping[str, Any]) -> list[str]:
    raw_types = block.get("EntityTypes")
    if not isinstance(raw_types, Sequence) or isinstance(raw_types, (str, bytes, bytearray)):
        return []
    result: list[str] = []
    for raw_type in raw_types:
        value = str(raw_type).strip().upper()
        if value and value not in result:
            result.append(value)
    return result


def _operation_and_model(
    response: Mapping[str, Any], blocks: Sequence[_Block]
) -> tuple[str, str | None]:
    analyze_version = response.get("AnalyzeDocumentModelVersion")
    if isinstance(analyze_version, str):
        return "AnalyzeDocument", analyze_version
    detection_version = response.get("DetectDocumentTextModelVersion")
    if isinstance(detection_version, str):
        return "DetectDocumentText", detection_version
    if any(block.type in _ANALYSIS_BLOCK_TYPES | _LAYOUT_TYPES for block in blocks):
        return "AnalyzeDocument", None
    return "DetectDocumentText", None


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    number = as_float(value)
    if number is None or number < 1:
        return None
    return int(number)


# Common import spellings. The registered provider name remains ``aws_textract``.
AmazonTextractProvider = AWSTextractProvider
AwsTextractProvider = AWSTextractProvider
TextractProvider = AWSTextractProvider

__all__ = [
    "AmazonTextractProvider",
    "AWSTextractProvider",
    "AwsTextractProvider",
    "TextractProvider",
]
