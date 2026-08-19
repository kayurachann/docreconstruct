"""Lightweight normalization adapter for saved PaddleOCR result JSON."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from docreconstruct.ir import (
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

from ._utils import (
    coerce_bbox,
    coerce_polygon,
    confidence,
    document_id,
    element_type,
    page_dimensions,
    page_number,
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


class PaddleOCRProvider(SavedJSONProvider):
    """Normalize PaddleOCR/PP-Structure JSON without importing PaddleOCR."""

    name = "paddleocr"
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
        distorted_photos=True,
        dewarping=True,
        execution_modes=[ProviderExecutionMode.SAVED],
        markdown=True,
        bounding_boxes=True,
        confidence_scores=True,
        privacy=ProviderPrivacy.NO_TRANSFER,
        license=ProviderLicense(
            name="Apache License 2.0",
            spdx="Apache-2.0",
            open_source=True,
            commercial_use=True,
        ),
        model_name="PaddleOCR / PP-Structure saved output",
        cost=ProviderCost.FREE,
        credentials=ProviderCredentialRequirement.NONE,
        notes=["Live PaddleOCR inference is intentionally not bundled."],
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
        pages_payload = _paddle_pages(payload)
        if not pages_payload and payload not in ([], {}):
            raise ProviderInputError("unrecognized PaddleOCR saved-result shape")

        pages: list[Page] = []
        used_numbers: set[int] = set()
        for page_index, page_payload in enumerate(pages_payload):
            elements = self._elements(page_payload, page_index)
            candidate_number = page_number(page_payload, page_index)
            number = candidate_number
            while number in used_numbers:
                number += 1
            used_numbers.add(number)
            data = (
                page_payload.get("res", page_payload)
                if isinstance(page_payload, Mapping)
                else page_payload
            )
            dimension_payload: Any = page_payload
            if isinstance(page_payload, Mapping) and isinstance(data, Mapping):
                dimension_payload = {**page_payload, **data}
            width, height = page_dimensions(dimension_payload, elements, context=context)
            rotation = 0.0
            if isinstance(page_payload, Mapping):
                rotation = float(page_payload.get("rotation") or page_payload.get("angle") or 0)
            page_metadata: dict[str, Any] = {
                "provider": self.name,
                "source_page_number": candidate_number,
            }
            preprocessor = _doc_preprocessor_metadata(data)
            if preprocessor:
                page_metadata["doc_preprocessor"] = preprocessor
            pages.append(
                Page(
                    id=f"page-{number}",
                    number=number,
                    width=width,
                    height=height,
                    rotation=rotation,
                    elements=elements,
                    source_type=SourceType.IMAGE,
                    metadata=page_metadata,
                )
            )

        return Document(
            id=document_id(self.name, context),
            pages=pages,
            source=context.source if context else None,
            metadata={"provider": self.name},
        )

    def _elements(self, page_payload: Any, page_index: int) -> list[Element]:
        elements: list[Element] = []
        data = (
            page_payload.get("res", page_payload)
            if isinstance(page_payload, Mapping)
            else page_payload
        )

        if isinstance(data, Mapping):
            texts = data.get("rec_texts")
            boxes = (
                data.get("rec_boxes")
                or data.get("dt_polys")
                or data.get("rec_polys")
                or data.get("boxes")
            )
            scores = data.get("rec_scores") or data.get("scores") or []
            labels = data.get("labels") or data.get("types") or []
            if (
                isinstance(texts, Sequence)
                and not isinstance(texts, (str, bytes))
                and isinstance(boxes, Sequence)
            ):
                for index, (text, box_value) in enumerate(zip(texts, boxes, strict=False)):
                    score = (
                        scores[index]
                        if isinstance(scores, Sequence) and index < len(scores)
                        else None
                    )
                    label = (
                        labels[index]
                        if isinstance(labels, Sequence) and index < len(labels)
                        else "text"
                    )
                    element = self._make_element(
                        page_index,
                        len(elements),
                        box_value,
                        text if isinstance(text, str) else str(text),
                        score,
                        label,
                        source_id=f"array-{index}",
                    )
                    if element:
                        elements.append(element)

        for box_value, text, score, label, source_id, metadata in _walk_paddle_items(data):
            element = self._make_element(
                page_index,
                len(elements),
                box_value,
                text,
                score,
                label,
                source_id=source_id,
                metadata=metadata,
            )
            if element:
                elements.append(element)

        elements = unique_elements(elements)
        next_element_index = len(elements)
        elements = _merge_layout_detection(elements)
        if isinstance(data, Mapping):
            elements.extend(
                self._overall_ocr_elements(
                    data.get("overall_ocr_res"),
                    page_index,
                    start_index=next_element_index,
                )
            )
        _restore_provider_reading_order(elements)
        _link_ocr_lines_to_blocks(elements)
        return elements

    def _overall_ocr_elements(
        self,
        payload: Any,
        page_index: int,
        *,
        start_index: int,
    ) -> list[Element]:
        """Preserve PP-StructureV3 line polygons and recognition confidence."""

        if not isinstance(payload, Mapping):
            return []
        texts = _sequence(payload.get("rec_texts"))
        boxes = _first_sequence(
            payload,
            "rec_polys",
            "rec_boxes",
            "dt_polys",
        )
        if texts is None or boxes is None:
            return []
        recognition_scores = _sequence(payload.get("rec_scores")) or []
        detection_scores = _sequence(payload.get("dt_scores")) or []
        elements: list[Element] = []
        for index, (raw_text, box_value) in enumerate(zip(texts, boxes, strict=False)):
            text = raw_text if isinstance(raw_text, str) else str(raw_text)
            text_score = recognition_scores[index] if index < len(recognition_scores) else None
            detection_score = confidence(
                detection_scores[index] if index < len(detection_scores) else None
            )
            metadata: dict[str, Any] = {
                "paddle_section": "overall_ocr_res",
                "block_type": "WORD",
                "ocr_line_index": index,
            }
            if detection_score is not None:
                metadata["detection_confidence"] = detection_score
            element = self._make_element(
                page_index,
                start_index + len(elements),
                box_value,
                text,
                text_score,
                "text",
                source_id=f"overall_ocr_res.rec_texts[{index}]",
                metadata=metadata,
            )
            if element is None:
                continue
            if element.provenance is not None:
                element.provenance = element.provenance.model_copy(
                    update={"layout_confidence": detection_score}
                )
            elements.append(element)
        return elements

    def _make_element(
        self,
        page_index: int,
        element_index: int,
        box_value: Any,
        text: str | None,
        score: Any,
        label: Any,
        *,
        source_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> Element | None:
        bbox = coerce_bbox(box_value)
        if bbox is None:
            return None
        element_id = f"page-{page_index + 1}-element-{element_index + 1}"
        score_value = confidence(score)
        style_data = (metadata or {}).pop("style", None)
        style = ElementStyle()
        if isinstance(style_data, Mapping):
            allowed = set(ElementStyle.model_fields)
            style = ElementStyle.model_validate(
                {
                    key: value
                    for key, value in style_data.items()
                    if key in allowed and value is not None
                }
            )
        clean_text = text if isinstance(text, str) and text != "" else None
        reading_order = _reading_order((metadata or {}).get("block_order"), element_index)
        return Element(
            id=element_id,
            type=element_type(label),
            bbox=bbox,
            polygon=coerce_polygon(box_value),
            text=clean_text,
            reading_order=reading_order,
            confidence=score_value,
            style=style,
            provenance=Provenance(
                engine=self.name,
                source_id=source_id,
                text_confidence=score_value if clean_text is not None else None,
                layout_confidence=score_value,
            ),
            text_candidates=(
                [
                    TextCandidate(
                        engine=self.name,
                        value=clean_text,
                        confidence=score_value,
                        source_element_id=source_id,
                    )
                ]
                if clean_text is not None
                else []
            ),
            metadata=metadata or {},
        )


def _paddle_pages(payload: Any) -> list[Any]:
    if isinstance(payload, Mapping):
        for key in ("pages", "results", "ocr_results"):
            value = payload.get(key)
            if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
                return list(value)
        data = payload.get("data")
        if (
            isinstance(data, Sequence)
            and not isinstance(data, (str, bytes))
            and data
            and all(isinstance(item, Mapping) and _has_page_marker(item) for item in data)
        ):
            return list(data)
        return [payload]
    if not isinstance(payload, Sequence) or isinstance(payload, (str, bytes, bytearray)):
        return []
    payload = list(payload)
    if not payload:
        return []
    if _looks_like_legacy_entry(payload[0]):
        return [payload]
    if all(isinstance(item, Mapping) for item in payload):
        if any(_has_page_marker(item) for item in payload):
            return payload
        # PP-Structure often returns a list of regions for one image.
        return [{"blocks": payload}]
    if all(isinstance(item, Sequence) for item in payload):
        return payload
    return [payload]


def _has_page_marker(value: Mapping[str, Any]) -> bool:
    return any(
        key in value for key in ("page_index", "page_idx", "page_number", "page_num", "rec_texts")
    )


def _looks_like_legacy_entry(value: Any) -> bool:
    return (
        isinstance(value, Sequence)
        and not isinstance(value, (str, bytes, bytearray))
        and len(value) >= 2
        and coerce_bbox(value[0]) is not None
        and isinstance(value[1], Sequence)
        and not isinstance(value[1], (str, bytes, bytearray))
        and len(value[1]) >= 1
        and isinstance(value[1][0], str)
    )


def _walk_paddle_items(
    value: Any,
    path: str = "root",
) -> list[tuple[Any, str | None, Any, Any, str, dict[str, Any]]]:
    results: list[tuple[Any, str | None, Any, Any, str, dict[str, Any]]] = []
    if _looks_like_legacy_entry(value):
        text_score = value[1]
        results.append(
            (
                value[0],
                text_score[0],
                text_score[1] if len(text_score) > 1 else None,
                "text",
                path,
                {},
            )
        )
        return results
    if isinstance(value, Mapping):
        box_value = None
        for key in (
            "block_bbox",
            "bbox",
            "box",
            "coordinate",
            "coordinates",
            "polygon",
            "poly",
            "dt_poly",
        ):
            if key in value and coerce_bbox(value[key]) is not None:
                box_value = value[key]
                break
        if box_value is not None:
            block_content = value.get("block_content")
            text = block_content if isinstance(block_content, str) else text_from(value)
            label = (
                value.get("block_label")
                or value.get("type")
                or value.get("label")
                or value.get("block_type")
                or "text"
            )
            score = value.get("score", value.get("confidence", value.get("rec_score")))
            metadata: dict[str, Any] = {
                key: value[key]
                for key in (
                    "html",
                    "table_html",
                    "image_path",
                    "latex",
                    "style",
                    "block_id",
                    "block_order",
                    "block_label",
                    "cls_id",
                )
                if key in value
            }
            section = _paddle_section(path)
            if section is not None:
                metadata["paddle_section"] = section
            kind = element_type(label)
            if kind is ElementType.TABLE and isinstance(text, str) and "<table" in text.lower():
                metadata.setdefault("html", text)
            if kind is ElementType.FORMULA and isinstance(text, str):
                metadata.setdefault("latex", text)
            nested_result = value.get("res")
            if isinstance(nested_result, Mapping):
                for key in ("html", "table_html", "image_path", "latex", "style"):
                    if key in nested_result:
                        metadata.setdefault(key, nested_result[key])
            results.append((box_value, text, score, label, path, metadata))
        for key, child in value.items():
            if key in {"rec_texts", "rec_scores", "rec_boxes", "dt_polys", "rec_polys"}:
                continue
            if isinstance(child, (Mapping, list, tuple)):
                results.extend(_walk_paddle_items(child, f"{path}.{key}"))
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, child in enumerate(value):
            results.extend(_walk_paddle_items(child, f"{path}[{index}]"))
    return results


def _sequence(value: Any) -> list[Any] | None:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return list(value)
    return None


def _first_sequence(payload: Mapping[str, Any], *keys: str) -> list[Any] | None:
    for key in keys:
        result = _sequence(payload.get(key))
        if result:
            return result
    return None


def _reading_order(value: Any, fallback: int) -> int:
    if isinstance(value, bool):
        return fallback
    try:
        result = int(value)
    except (TypeError, ValueError):
        return fallback
    return result if result >= 0 else fallback


def _paddle_section(path: str) -> str | None:
    for section in (
        "parsing_res_list",
        "layout_det_res",
        "table_res_list",
        "formula_res_list",
        "seal_res_list",
    ):
        if f".{section}" in path or path.startswith(section):
            return section
    return None


def _merge_layout_detection(elements: list[Element]) -> list[Element]:
    """Attach layout-only detections to equivalent parsed blocks.

    PP-StructureV3 repeats many regions in both ``layout_det_res`` and
    ``parsing_res_list``. Keeping two independent visual elements for the same
    box creates false duplicate tables and figures, so the raw layout score is
    retained on the richer parsed block instead.
    """

    parsed = [
        element
        for element in elements
        if element.metadata.get("paddle_section") == "parsing_res_list"
    ]
    result: list[Element] = []
    for element in elements:
        if element.metadata.get("paddle_section") != "layout_det_res":
            result.append(element)
            continue
        match = next(
            (
                candidate
                for candidate in parsed
                if candidate.type is element.type and candidate.bbox.iou(element.bbox) >= 0.98
            ),
            None,
        )
        if match is None:
            result.append(element)
            continue
        layout_confidence = element.confidence
        match.confidence = match.confidence or layout_confidence
        if match.provenance is not None:
            match.provenance = match.provenance.model_copy(
                update={"layout_confidence": layout_confidence}
            )
        match.metadata["layout_detection"] = {
            "source_id": element.provenance.source_id if element.provenance else None,
            "confidence": layout_confidence,
            "cls_id": element.metadata.get("cls_id"),
        }
    return result


def _link_ocr_lines_to_blocks(elements: list[Element]) -> None:
    """Relate retained OCR-line evidence to the smallest containing block."""

    blocks = [
        element
        for element in elements
        if element.metadata.get("paddle_section") == "parsing_res_list" and element.text
    ]
    for line in elements:
        if line.metadata.get("paddle_section") != "overall_ocr_res":
            continue
        containing = [
            block
            for block in blocks
            if block.bbox.x0 <= line.bbox.center_x <= block.bbox.x1
            and block.bbox.y0 <= line.bbox.center_y <= block.bbox.y1
        ]
        if not containing:
            continue
        parent = min(containing, key=lambda block: block.bbox.area)
        line.relationships = Relationship(parent=parent.id)
        if line.id not in parent.relationships.children:
            parent.relationships.children.append(line.id)


def _restore_provider_reading_order(elements: list[Element]) -> None:
    for fallback, element in enumerate(elements):
        element.reading_order = _reading_order(element.metadata.get("block_order"), fallback)


def _doc_preprocessor_metadata(data: Any) -> dict[str, Any]:
    if not isinstance(data, Mapping):
        return {}
    payload = data.get("doc_preprocessor_res")
    if not isinstance(payload, Mapping):
        return {}
    result: dict[str, Any] = {}
    if "angle" in payload:
        result["angle"] = payload["angle"]
    if "page_index" in payload:
        result["page_index"] = payload["page_index"]
    settings = payload.get("model_settings")
    if isinstance(settings, Mapping):
        retained = {
            key: settings[key]
            for key in ("use_doc_orientation_classify", "use_doc_unwarping")
            if key in settings
        }
        if retained:
            result["model_settings"] = retained
    return result


# Alternate class name used by some integrations.
PaddleOcrProvider = PaddleOCRProvider
