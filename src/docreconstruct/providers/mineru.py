"""Lightweight normalization adapter for saved MinerU JSON."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any

from docreconstruct.ir import (
    Document,
    Element,
    ElementStyle,
    ElementType,
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
            elements = unique_elements(self._elements(page_payload, page_index))
            candidate_number = page_number(page_payload, page_index)
            number = candidate_number
            while number in used_numbers:
                number += 1
            used_numbers.add(number)
            width, height = page_dimensions(page_payload, elements, context=context)
            pages.append(
                Page(
                    id=f"page-{number}",
                    number=number,
                    width=width,
                    height=height,
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
        blocks = _page_blocks(page_payload)
        elements: list[Element] = []
        for block_index, block in enumerate(blocks):
            if not isinstance(block, Mapping):
                continue
            block_elements = self._block_elements(block, page_index, block_index)
            elements.extend(block_elements)
        return elements

    def _block_elements(
        self,
        block: Mapping[str, Any],
        page_index: int,
        block_index: int,
        path: str | None = None,
    ) -> list[Element]:
        path = path or f"block-{block_index}"
        bbox = coerce_bbox(block)
        if bbox is None:
            nested: list[Element] = []
            for key in ("lines", "spans", "children", "blocks"):
                children = block.get(key)
                if isinstance(children, Sequence) and not isinstance(children, (str, bytes)):
                    for child_index, child in enumerate(children):
                        if isinstance(child, Mapping):
                            nested.extend(
                                self._block_elements(
                                    child,
                                    page_index,
                                    block_index,
                                    f"{path}.{key}[{child_index}]",
                                )
                            )
            return nested

        label = block.get("type") or block.get("block_type") or block.get("label") or "text"
        kind = element_type(label)
        text = text_from(block)
        if kind is ElementType.IMAGE and text and text == block.get("image_path"):
            text = None
        score_value = _block_confidence(block)
        element_id = f"page-{page_index + 1}-{slug(path)}"
        metadata = {
            key: block[key]
            for key in (
                "html",
                "table_html",
                "latex",
                "image_path",
                "img_path",
                "level",
                "index",
                "page_idx",
            )
            if key in block
        }
        if kind is ElementType.TABLE:
            table_payload = {
                key: block[key]
                for key in ("html", "table_html", "cells", "rows", "columns")
                if key in block
            }
            if table_payload:
                metadata["table"] = table_payload
        image_path = block.get("image_path") or block.get("img_path")
        if kind in {ElementType.IMAGE, ElementType.FIGURE, ElementType.CHART} and image_path:
            metadata["image"] = {"path": image_path}

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
                polygon=coerce_polygon(block),
                text=clean_text,
                reading_order=block_index,
                confidence=score_value,
                style=style,
                provenance=Provenance(
                    engine=self.name,
                    source_id=path,
                    text_confidence=score_value if clean_text is not None else None,
                    layout_confidence=score_value,
                ),
                text_candidates=(
                    [
                        TextCandidate(
                            engine=self.name,
                            value=clean_text,
                            confidence=score_value,
                            source_element_id=path,
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
                return list(value)
        for key in ("content_list", "data", "results"):
            value = payload.get(key)
            if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
                return _group_content_list(list(value))
        return [payload]
    if isinstance(payload, Sequence) and not isinstance(payload, (str, bytes, bytearray)):
        return _group_content_list(list(payload))
    return []


def _group_content_list(items: list[Any]) -> list[Any]:
    if not items:
        return []
    if all(isinstance(item, Mapping) for item in items) and any(
        "page_idx" in item or "page_index" in item for item in items
    ):
        groups: dict[int, list[Any]] = defaultdict(list)
        for item in items:
            raw_index = item.get("page_idx", item.get("page_index", 0))
            try:
                index = int(raw_index)
            except (TypeError, ValueError):
                index = 0
            groups[index].append(item)
        return [{"page_idx": index, "content_list": groups[index]} for index in sorted(groups)]
    return items


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
