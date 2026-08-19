"""Lightweight normalization adapter for saved MinerU JSON."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
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
    SourceType,
    TextCandidate,
)

from ._utils import (
    coerce_bbox,
    coerce_polygon,
    confidence,
    document_id,
    element_type,
    page_dimensions,
    page_number,
    slug,
    text_from,
    unique_elements,
)
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

_CONTENT_LIST_EXTENT = 1000.0
_CONTENT_LIST_COORDINATE_SYSTEM = "mineru_content_list_0_1000"
_CONTENT_LIST_CANONICAL_COORDINATE_SYSTEM = "mineru_content_list_normalized"
_OUTPUT_FORMAT_KEY = "_docreconstruct_mineru_output_format"


class MinerUProvider(SavedJSONProvider):
    """Normalize MinerU middle/content-list JSON without importing MinerU."""

    name = "mineru"
    _capabilities = ProviderCapabilities(
        provider=name,
        supported_inputs=["json", "jsonl"],
        saved_json=True,
        live_inference=False,
        text=True,
        geometry=True,
        reading_order=True,
        styles=False,
        tables=True,
        images=True,
        multilingual=True,
        handwriting=True,
        formulas=True,
        charts=True,
        layout=True,
        execution_modes=[ProviderExecutionMode.SAVED],
        markdown=True,
        bounding_boxes=True,
        confidence_scores=True,
        privacy=ProviderPrivacy.NO_TRANSFER,
        license=ProviderLicense(
            name="MinerU Open Source License",
            open_source=True,
            commercial_use=None,
            restrictions=["Apache-2.0-based custom license with additional conditions."],
        ),
        model_name="MinerU saved output",
        cost=ProviderCost.FREE,
        credentials=ProviderCredentialRequirement.NONE,
        notes=["Live MinerU inference is intentionally not bundled."],
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
        page_payloads = _mineru_pages(payload)
        if not page_payloads and payload not in ([], {}):
            raise ProviderInputError("unrecognized MinerU saved-result shape")

        pages: list[Page] = []
        used_numbers: set[int] = set()
        for page_index, page_payload in enumerate(page_payloads):
            source_page_index = _source_page_index(page_payload, page_index)
            output_format = _page_output_format(page_payload)
            normalized_content_list = output_format in {"content_list", "content_list_v2"}
            geometry_scale: tuple[float, float] | None = None
            if normalized_content_list:
                geometry_scale = (
                    1.0 / _CONTENT_LIST_EXTENT,
                    1.0 / _CONTENT_LIST_EXTENT,
                )
            elements = unique_elements(
                self._elements(
                    page_payload,
                    source_page_index=source_page_index,
                    output_format=output_format,
                    geometry_scale=geometry_scale,
                )
            )
            candidate_number = page_number(page_payload, page_index)
            number = candidate_number
            while number in used_numbers:
                number += 1
            used_numbers.add(number)
            if normalized_content_list:
                assert geometry_scale is not None
                width = height = 1.0
            else:
                width, height = page_dimensions(page_payload, elements, context=context)
            page_metadata: dict[str, Any] = {
                "provider": self.name,
                "source_page_number": candidate_number,
                "source_page_index": source_page_index,
                "output_format": output_format,
            }
            if normalized_content_list:
                assert geometry_scale is not None
                page_metadata.update(
                    {
                        "coordinate_system": (_CONTENT_LIST_CANONICAL_COORDINATE_SYSTEM),
                        "source_coordinate_system": _CONTENT_LIST_COORDINATE_SYSTEM,
                        "source_coordinate_extent": [
                            _CONTENT_LIST_EXTENT,
                            _CONTENT_LIST_EXTENT,
                        ],
                        "geometry_scale": {
                            "x": geometry_scale[0],
                            "y": geometry_scale[1],
                        },
                    }
                )
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
        document_metadata: dict[str, Any] = {"provider": self.name}
        if isinstance(payload, Mapping):
            mineru_metadata = {
                key: payload[key] for key in ("_backend", "_version_name") if key in payload
            }
            if mineru_metadata:
                document_metadata["mineru"] = mineru_metadata
        return Document(
            id=document_id(self.name, context),
            pages=pages,
            source=context.source if context else None,
            metadata=document_metadata,
        )

    def _elements(
        self,
        page_payload: Any,
        *,
        source_page_index: int,
        output_format: str,
        geometry_scale: tuple[float, float] | None,
    ) -> list[Element]:
        blocks = _page_blocks(page_payload)
        elements: list[Element] = []
        for block_index, block in enumerate(blocks):
            if not isinstance(block, Mapping):
                continue
            block_elements = self._block_elements(
                block,
                block_index,
                source_page_index=source_page_index,
                output_format=output_format,
                geometry_scale=geometry_scale,
            )
            elements.extend(block_elements)
        return elements

    def _block_elements(
        self,
        block: Mapping[str, Any],
        block_index: int,
        path: str | None = None,
        *,
        source_page_index: int,
        output_format: str,
        geometry_scale: tuple[float, float] | None,
    ) -> list[Element]:
        path = path or _block_source_path(output_format, source_page_index, block_index)
        source_bbox = coerce_bbox(block)
        if source_bbox is None:
            nested: list[Element] = []
            for key in ("lines", "spans", "children", "blocks"):
                children = block.get(key)
                if isinstance(children, Sequence) and not isinstance(children, (str, bytes)):
                    for child_index, child in enumerate(children):
                        if isinstance(child, Mapping):
                            nested.extend(
                                self._block_elements(
                                    child,
                                    block_index,
                                    f"{path}.{key}[{child_index}]",
                                    source_page_index=source_page_index,
                                    output_format=output_format,
                                    geometry_scale=geometry_scale,
                                )
                            )
            return nested

        bbox = _scale_bbox(source_bbox, geometry_scale)
        source_polygon = coerce_polygon(block)
        polygon = _scale_polygon(source_polygon, geometry_scale)

        label = block.get("type") or block.get("block_type") or block.get("label") or "text"
        kind = _mineru_element_type(block, label)
        text = _mineru_text(block)
        if kind is ElementType.IMAGE and text and text == block.get("image_path"):
            text = None
        score_value = _block_confidence(block)
        element_id = f"page-{source_page_index + 1}-{slug(path)}"
        metadata = {
            key: block[key]
            for key in (
                "html",
                "table_html",
                "table_body",
                "latex",
                "image_path",
                "img_path",
                "image_caption",
                "image_footnote",
                "table_caption",
                "table_footnote",
                "chart_caption",
                "chart_footnote",
                "text_format",
                "text_level",
                "sub_type",
                "anchor",
                "code_body",
                "code_caption",
                "code_footnote",
                "list_items",
                "level",
                "index",
                "page_idx",
            )
            if key in block
        }
        content_payload = block.get("content")
        if isinstance(content_payload, Mapping):
            metadata["mineru_content"] = dict(content_payload)
        coordinate_system = None
        if geometry_scale is not None:
            coordinate_system = _CONTENT_LIST_CANONICAL_COORDINATE_SYSTEM
            metadata.update(
                {
                    "coordinate_system": coordinate_system,
                    "source_coordinate_system": _CONTENT_LIST_COORDINATE_SYSTEM,
                    "source_bbox": source_bbox.model_dump(mode="json"),
                }
            )
        metadata.update(
            {
                "page_idx": source_page_index,
                "output_format": output_format,
                "source_id": path,
            }
        )
        if kind is ElementType.TABLE:
            table_payload = {
                key: block[key]
                for key in (
                    "html",
                    "table_html",
                    "table_body",
                    "cells",
                    "rows",
                    "columns",
                    "table_caption",
                    "table_footnote",
                )
                if key in block
            }
            if "html" not in table_payload and isinstance(table_payload.get("table_body"), str):
                table_payload["html"] = table_payload["table_body"]
            if isinstance(content_payload, Mapping):
                table_payload["content"] = dict(content_payload)
            if table_payload:
                metadata["table"] = table_payload
        image_path = _mineru_asset_path(block)
        if image_path:
            metadata["image_ref"] = image_path
            metadata["image"] = {"path": image_path, "src": image_path}
            metadata["asset"] = {
                "path": image_path,
                "provider": self.name,
                "kind": kind.value,
            }

        if kind is ElementType.FORMULA:
            latex = block.get("latex")
            if not isinstance(latex, str) or not latex.strip():
                latex = _mineru_formula_text(block, text)
            if isinstance(latex, str) and latex.strip():
                metadata["latex"] = latex

        style_payload = block.get("style")
        style = ElementStyle()
        if isinstance(style_payload, Mapping):
            allowed = set(ElementStyle.model_fields)
            style = ElementStyle.model_validate(
                {
                    key: value
                    for key, value in style_payload.items()
                    if key in allowed and value is not None
                }
            )
        clean_text = text if isinstance(text, str) and text != "" else None
        return [
            Element(
                id=element_id,
                type=kind,
                bbox=bbox,
                polygon=polygon,
                text=clean_text,
                reading_order=block_index,
                confidence=score_value,
                style=style,
                provenance=Provenance(
                    engine=self.name,
                    source_id=path,
                    text_confidence=score_value if clean_text is not None else None,
                    layout_confidence=score_value,
                    metadata={
                        "page_idx": source_page_index,
                        "output_format": output_format,
                        "record_type": str(label),
                        **(
                            {
                                "coordinate_system": coordinate_system,
                                "source_coordinate_system": _CONTENT_LIST_COORDINATE_SYSTEM,
                                "source_bbox": source_bbox.model_dump(mode="json"),
                            }
                            if coordinate_system is not None
                            else {}
                        ),
                        **({"asset_path": image_path} if image_path else {}),
                    },
                ),
                text_candidates=(
                    [
                        TextCandidate(
                            engine=self.name,
                            value=clean_text,
                            confidence=score_value,
                            source_element_id=path,
                            metadata={
                                "page_idx": source_page_index,
                                "output_format": output_format,
                            },
                        )
                    ]
                    if clean_text is not None
                    else []
                ),
                metadata=metadata,
            )
        ]


def _mineru_pages(payload: Any) -> list[Any]:
    if isinstance(payload, Mapping):
        for key in ("pdf_info", "pages", "page_info", "page_infos"):
            value = payload.get(key)
            if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
                output_format = "middle_json" if key == "pdf_info" else key
                return [_annotate_page(item, output_format) for item in value]
        for key in ("content_list", "data", "results"):
            value = payload.get(key)
            if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
                if key == "content_list":
                    outer_page_index = _optional_page_index(payload)
                    return _group_content_list(
                        list(value),
                        force=True,
                        default_page_index=outer_page_index or 0,
                    )
                return _group_content_list(list(value))
        return [payload]
    if isinstance(payload, Sequence) and not isinstance(payload, (str, bytes, bytearray)):
        items = list(payload)
        if _looks_like_content_list_v2(items):
            return [
                {
                    "page_idx": page_index,
                    "content_list": list(page),
                    _OUTPUT_FORMAT_KEY: "content_list_v2",
                }
                for page_index, page in enumerate(items)
            ]
        return _group_content_list(items)
    return []


def _group_content_list(
    items: list[Any],
    *,
    force: bool = False,
    default_page_index: int = 0,
) -> list[Any]:
    if not items:
        return []
    mappings = [item for item in items if isinstance(item, Mapping)]
    looks_like_content_list = len(mappings) == len(items) and all(
        _looks_like_content_list_record(item) for item in mappings
    )
    if len(mappings) == len(items) and (force or looks_like_content_list):
        groups: dict[int, list[Any]] = defaultdict(list)
        for item in mappings:
            index = _mapping_page_index(item, default_page_index)
            groups[index].append(item)
        return [
            {
                "page_idx": index,
                "content_list": groups[index],
                _OUTPUT_FORMAT_KEY: "content_list",
            }
            for index in sorted(groups)
        ]
    return items


def _looks_like_content_list_record(item: Mapping[str, Any]) -> bool:
    return (
        coerce_bbox(item) is not None
        and any(key in item for key in ("type", "block_type", "label"))
        and any(key in item for key in ("page_idx", "page_index"))
    )


def _looks_like_content_list_v2(items: Sequence[Any]) -> bool:
    if not items or not all(
        isinstance(page, Sequence) and not isinstance(page, (str, bytes, bytearray))
        for page in items
    ):
        return False
    blocks = [block for page in items for block in page]
    return bool(blocks) and all(
        isinstance(block, Mapping) and "type" in block and isinstance(block.get("content"), Mapping)
        for block in blocks
    )


def _annotate_page(page: Any, output_format: str) -> Any:
    if not isinstance(page, Mapping):
        return page
    return {**page, _OUTPUT_FORMAT_KEY: output_format}


def _optional_page_index(page: Mapping[str, Any]) -> int | None:
    for key in ("page_idx", "page_index"):
        if key in page:
            try:
                return max(0, int(page[key]))
            except (TypeError, ValueError):
                return None
    return None


def _mapping_page_index(page: Mapping[str, Any], fallback: int) -> int:
    page_index = _optional_page_index(page)
    return page_index if page_index is not None else fallback


def _source_page_index(page: Any, fallback: int) -> int:
    if isinstance(page, Mapping):
        return _mapping_page_index(page, fallback)
    return fallback


def _page_output_format(page: Any) -> str:
    if isinstance(page, Mapping):
        value = page.get(_OUTPUT_FORMAT_KEY)
        if isinstance(value, str) and value:
            return value
        if "para_blocks" in page or "preproc_blocks" in page:
            return "middle_json"
        if "content_list" in page:
            return "content_list"
    return "unknown"


def _block_source_path(output_format: str, page_index: int, block_index: int) -> str:
    if output_format == "content_list_v2":
        return f"content_list_v2[{page_index}][{block_index}]"
    if output_format == "content_list":
        return f"content_list.page[{page_index}][{block_index}]"
    if output_format == "middle_json":
        return f"pdf_info[{page_index}].para_blocks[{block_index}]"
    return f"page[{page_index}].blocks[{block_index}]"


def _scale_bbox(bbox: BBox, scale: tuple[float, float] | None) -> BBox:
    if scale is None:
        return bbox
    scale_x, scale_y = scale
    return BBox(
        x0=bbox.x0 * scale_x,
        y0=bbox.y0 * scale_y,
        x1=bbox.x1 * scale_x,
        y1=bbox.y1 * scale_y,
    )


def _scale_polygon(points: Sequence[Point], scale: tuple[float, float] | None) -> list[Point]:
    if scale is None:
        return list(points)
    scale_x, scale_y = scale
    return [Point(x=point.x * scale_x, y=point.y * scale_y) for point in points]


def _mineru_element_type(block: Mapping[str, Any], label: Any) -> ElementType:
    kind = element_type(label)
    if kind is ElementType.TEXT:
        level = block.get("text_level")
        if isinstance(level, int) and not isinstance(level, bool) and level > 0:
            return ElementType.HEADING
    return kind


def _mineru_text(block: Mapping[str, Any]) -> str | None:
    text = text_from(block)
    if text is not None:
        return text
    content = block.get("content")
    return _structured_content_text(content)


def _structured_content_text(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        direct = value.get("content")
        if isinstance(direct, str):
            return direct
        pieces: list[str] = []
        for key, child in value.items():
            if key in {"image_path", "img_path", "url"}:
                continue
            if key.endswith("_content") or key in {"children", "list_items"}:
                text = _structured_content_text(child)
                if text:
                    pieces.append(text)
        return "\n".join(pieces) or None
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        pieces = [text for item in value if (text := _structured_content_text(item))]
        inline_spans = all(
            isinstance(item, Mapping) and isinstance(item.get("content"), str) for item in value
        )
        return ("" if inline_spans else "\n").join(pieces) or None
    return None


def _mineru_asset_path(block: Mapping[str, Any]) -> str | None:
    for key in ("img_path", "image_path"):
        value = block.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    content = block.get("content")
    if isinstance(content, Mapping):
        for key in ("img_path", "image_path"):
            value = content.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    for key in ("blocks", "lines", "spans", "children"):
        children = block.get(key)
        if isinstance(children, Sequence) and not isinstance(children, (str, bytes, bytearray)):
            for child in children:
                if isinstance(child, Mapping) and (path := _mineru_asset_path(child)):
                    return path
    return None


def _mineru_formula_text(block: Mapping[str, Any], fallback: str | None) -> str | None:
    content = block.get("content")
    if isinstance(content, Mapping):
        math_content = content.get("math_content")
        if isinstance(math_content, str) and math_content.strip():
            return math_content
        structured = _structured_content_text(math_content)
        if structured:
            return structured
    text_format = block.get("text_format")
    if text_format == "latex" and fallback:
        return fallback
    return fallback


def _page_blocks(page: Any) -> list[Any]:
    if isinstance(page, Mapping):
        for key in (
            "para_blocks",
            "blocks",
            "content_list",
            "elements",
            "layout",
            "content",
        ):
            value = page.get(key)
            if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
                return list(value)
        if coerce_bbox(page) is not None:
            return [page]
    elif isinstance(page, Sequence) and not isinstance(page, (str, bytes, bytearray)):
        return list(page)
    return []


def _block_confidence(block: Mapping[str, Any]) -> float | None:
    for key in ("score", "confidence", "prob", "layout_score"):
        value = confidence(block.get(key))
        if value is not None:
            return value
    return None


# Friendly spelling used in configuration documentation.
MineruProvider = MinerUProvider
