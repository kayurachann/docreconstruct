"""Lightweight normalization adapter for saved olmOCR JSON/JSONL."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from docreconstruct.ir import (
    BBox,
    Document,
    Element,
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
    page_dimensions,
    page_number,
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


class OlmOCRProvider(SavedJSONProvider):
    """Normalize linearized olmOCR output without importing its inference stack."""

    name = "olmocr"
    _capabilities = ProviderCapabilities(
        provider=name,
        supported_inputs=["json", "jsonl", "ndjson"],
        saved_json=True,
        live_inference=False,
        text=True,
        geometry=False,
        reading_order=True,
        styles=False,
        tables=True,
        images=False,
        multilingual=True,
        handwriting=True,
        formulas=True,
        layout=True,
        execution_modes=[ProviderExecutionMode.SAVED],
        markdown=True,
        bounding_boxes=False,
        privacy=ProviderPrivacy.NO_TRANSFER,
        license=ProviderLicense(
            name="Apache License 2.0",
            spdx="Apache-2.0",
            open_source=True,
            commercial_use=True,
        ),
        model_name="olmOCR linearized saved output",
        cost=ProviderCost.FREE,
        credentials=ProviderCredentialRequirement.NONE,
        notes=[
            "olmOCR linearized output may not contain element coordinates; "
            "missing geometry is represented by a full-page box."
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
        records = _olmocr_pages(payload)
        if not records and payload not in ([], {}):
            raise ProviderInputError("unrecognized olmOCR saved-result shape")

        pages: list[Page] = []
        used_numbers: set[int] = set()
        inferred_source: str | None = context.source if context else None
        for page_index, record in enumerate(records):
            merged = _with_metadata(record)
            if inferred_source is None:
                inferred_source = _source_from(merged)
            text = _olmocr_text(record)
            candidate_number = page_number(merged, page_index)
            number = candidate_number
            while number in used_numbers:
                number += 1
            used_numbers.add(number)
            width, height = page_dimensions(merged, [], context=context)
            explicit_bbox = coerce_bbox(record) if isinstance(record, Mapping) else None
            bbox = explicit_bbox or BBox(x0=0, y0=0, x1=width, y1=height)
            score = _score(merged)
            elements: list[Element] = []
            if text is not None:
                source_id = _record_id(merged, number)
                elements.append(
                    Element(
                        id=f"page-{number}-text-1",
                        type=ElementType.TEXT,
                        bbox=bbox,
                        polygon=coerce_polygon(record),
                        text=text,
                        reading_order=0,
                        confidence=score,
                        provenance=Provenance(
                            engine=self.name,
                            source_id=source_id,
                            text_confidence=score,
                            layout_confidence=score if explicit_bbox else None,
                            metadata={
                                key: merged[key]
                                for key in ("model", "version", "finish_reason")
                                if key in merged
                            },
                        ),
                        text_candidates=[
                            TextCandidate(
                                engine=self.name,
                                value=text,
                                confidence=score,
                                source_element_id=source_id,
                            )
                        ],
                        metadata={
                            "coordinate_system": (
                                "source" if explicit_bbox else "full_page_fallback"
                            ),
                            "linearized": True,
                        },
                    )
                )
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
                        "geometry_available": explicit_bbox is not None,
                    },
                )
            )

        effective_context = context
        if effective_context is None:
            effective_context = ProviderContext(source=inferred_source)
        elif effective_context.source is None and inferred_source:
            effective_context = effective_context.model_copy(update={"source": inferred_source})
        return Document(
            id=document_id(self.name, effective_context),
            pages=pages,
            source=inferred_source,
            metadata={"provider": self.name},
        )


def _olmocr_pages(payload: Any) -> list[Any]:
    if isinstance(payload, Mapping):
        for key in ("pages", "results", "records", "outputs"):
            value = payload.get(key)
            if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
                return list(value)
        return [payload]
    if isinstance(payload, Sequence) and not isinstance(payload, (str, bytes, bytearray)):
        records = list(payload)
        if records and all(
            isinstance(item, Mapping) and "role" in item and "content" in item for item in records
        ):
            return [{"messages": records}]
        return records
    return []


def _olmocr_text(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        for key in ("natural_text", "text", "markdown", "completion"):
            candidate = value.get(key)
            if isinstance(candidate, str):
                return candidate
        messages = value.get("messages")
        if isinstance(messages, Sequence) and not isinstance(messages, (str, bytes)):
            for message in reversed(messages):
                if isinstance(message, Mapping) and message.get("role") == "assistant":
                    candidate = message.get("content")
                    if isinstance(candidate, str):
                        return candidate
            for message in reversed(messages):
                if isinstance(message, Mapping) and isinstance(message.get("content"), str):
                    return message["content"]
        choices = value.get("choices")
        if isinstance(choices, Sequence) and not isinstance(choices, (str, bytes)):
            for choice in choices:
                candidate = _olmocr_text(choice)
                if candidate is not None:
                    return candidate
        for key in ("message", "response", "body", "output", "result", "data"):
            if key in value:
                candidate = _olmocr_text(value[key])
                if candidate is not None:
                    return candidate
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        pieces = [candidate for item in value if (candidate := _olmocr_text(item))]
        return "\n".join(pieces) if pieces else None
    return None


def _with_metadata(record: Any) -> dict[str, Any]:
    if not isinstance(record, Mapping):
        return {}
    result = dict(record)
    metadata = record.get("metadata")
    if isinstance(metadata, Mapping):
        for key, value in metadata.items():
            result.setdefault(str(key), value)
    return result


def _source_from(record: Mapping[str, Any]) -> str | None:
    for key in ("source", "Source-File", "source_file", "filename", "file_name"):
        value = record.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _record_id(record: Mapping[str, Any], page_num: int) -> str:
    for key in ("id", "record_id", "source_id"):
        value = record.get(key)
        if value is not None:
            return str(value)
    return f"page-{page_num}"


def _score(record: Mapping[str, Any]) -> float | None:
    for key in ("confidence", "score", "probability"):
        result = confidence(record.get(key))
        if result is not None:
            return result
    return None


# Common capitalization variants.
OlmOcrProvider = OlmOCRProvider
OLMOCRProvider = OlmOCRProvider
