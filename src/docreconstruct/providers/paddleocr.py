"""Lightweight normalization adapter for saved PaddleOCR result JSON."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from docreconstruct.ir import (
    Document,
    Element,
    ElementStyle,
    Page,
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
            elements = unique_elements(elements)
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
            pages.append(
                Page(
                    id=f"page-{number}",
                    number=number,
                    width=width,
                    height=height,
                    rotation=rotation,
                    elements=elements,
                    source_type=SourceType.IMAGE,
                    metadata={
                        "provider": self.name,
                        "source_page_number": candidate_number,
                    },
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
        return Element(
            id=element_id,
            type=element_type(label),
            bbox=bbox,
            polygon=coerce_polygon(box_value),
            text=clean_text,
            reading_order=element_index,
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
        for key in ("bbox", "box", "coordinate", "coordinates", "polygon", "poly", "dt_poly"):
            if key in value and coerce_bbox(value[key]) is not None:
                box_value = value[key]
                break
        if box_value is not None:
            text = text_from(value)
            label = value.get("type") or value.get("label") or value.get("block_type") or "text"
            score = value.get("score", value.get("confidence", value.get("rec_score")))
            metadata: dict[str, Any] = {
                key: value[key]
                for key in ("html", "table_html", "image_path", "latex", "style")
                if key in value
            }
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


# Alternate class name used by some integrations.
PaddleOcrProvider = PaddleOCRProvider
