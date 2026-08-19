"""Saved-response and opt-in hosted adapter for the official Mistral OCR API."""

from __future__ import annotations

import base64
import json
import mimetypes
from collections.abc import Mapping, Sequence
from pathlib import Path
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

from ._hosted import (
    HTTPTransport,
    context_options,
    decode_json_response,
    load_hosted_source,
    numeric_option,
    require_credential,
    require_remote_opt_in,
    response_request_id,
    safe_raw,
    stdlib_http_transport,
    validate_service_endpoint,
)
from ._utils import (
    as_float,
    coerce_bbox,
    coerce_polygon,
    confidence,
    document_id,
    element_type,
    looks_like_inline_json,
    text_from,
)
from .base import (
    ProviderCapabilities,
    ProviderContext,
    ProviderCost,
    ProviderCredentialRequirement,
    ProviderExecutionMode,
    ProviderInput,
    ProviderInputError,
    ProviderLicense,
    ProviderPrivacy,
    ProviderResult,
    SavedJSONProvider,
)


class MistralOCRProvider(SavedJSONProvider):
    """Normalize Mistral OCR JSON or call ``POST /v1/ocr`` after explicit consent."""

    name = "mistral_ocr"
    _capabilities = ProviderCapabilities(
        provider=name,
        supported_inputs=["pdf", "png", "jpeg", "webp", "json"],
        saved_json=True,
        live_inference=True,
        text=True,
        geometry=True,
        reading_order=True,
        styles=False,
        tables=True,
        images=True,
        multilingual=True,
        formulas=True,
        layout=True,
        execution_modes=[ProviderExecutionMode.SAVED, ProviderExecutionMode.API],
        markdown=True,
        confidence_scores=True,
        privacy=ProviderPrivacy.THIRD_PARTY,
        license=ProviderLicense(
            name="Mistral AI hosted service",
            open_source=False,
            commercial_use=True,
        ),
        model_name="mistral-ocr-latest",
        cost=ProviderCost.METERED,
        credentials=ProviderCredentialRequirement.REQUIRED,
        credential_env_vars=["MISTRAL_API_KEY"],
        notes=[
            "Hosted inference is disabled unless ProviderContext.options.allow_remote is true.",
            "Uses the official Mistral OCR HTTPS API; no vendor SDK is required.",
        ],
    )

    def __init__(self, *, transport: HTTPTransport | None = None) -> None:
        self._transport = transport or stdlib_http_transport

    @property
    def capabilities(self) -> ProviderCapabilities:
        return self._capabilities

    def parse(
        self,
        source: ProviderInput,
        *,
        context: ProviderContext | None = None,
    ) -> ProviderResult:
        if isinstance(source, (bytes, bytearray)):
            stripped = bytes(source).lstrip()
            if stripped.startswith(b"\xef\xbb\xbf"):
                stripped = stripped[3:].lstrip()
            if stripped.startswith((b"{", b"[")):
                return super().parse(source, context=context)
            return self.infer(source, context=context)
        if isinstance(source, (Mapping, Sequence)) and not isinstance(source, (str, Path)):
            return super().parse(source, context=context)
        if isinstance(source, str) and looks_like_inline_json(source):
            return super().parse(source, context=context)
        if isinstance(source, (str, Path)) and Path(source).suffix.lower() in {
            ".json",
            ".jsonl",
            ".ndjson",
        }:
            return super().parse(source, context=context)
        return self.infer(source, context=context)

    def infer(
        self,
        source: str | bytes | bytearray | Path,
        *,
        context: ProviderContext | None = None,
    ) -> ProviderResult:
        require_remote_opt_in(context, self.name)
        api_key = require_credential(
            context,
            provider=self.name,
            option_name="api_key",
            environment_names=("MISTRAL_API_KEY",),
        )
        options = context_options(context)
        endpoint = validate_service_endpoint(
            str(options.get("endpoint", "https://api.mistral.ai/v1/ocr")),
            label="Mistral OCR endpoint",
            allowed_hosts=("api.mistral.ai",),
            allow_custom_endpoint=options.get("allow_custom_endpoint") is True,
        )
        model = str(options.get("model", "mistral-ocr-latest")).strip()
        if not model:
            raise ProviderInputError("Mistral OCR model must not be blank")
        maximum_megabytes = numeric_option(
            context,
            "max_upload_mb",
            50.0,
            minimum=0.1,
            maximum=512.0,
        )
        hosted_source = load_hosted_source(
            source,
            context=context,
            maximum_megabytes=maximum_megabytes,
        )
        document_kind = (
            "image_url" if hosted_source.media_type.startswith("image/") else "document_url"
        )
        if hosted_source.url is not None:
            document_value = hosted_source.url
        else:
            assert hosted_source.data is not None
            encoded = base64.b64encode(hosted_source.data).decode("ascii")
            document_value = f"data:{hosted_source.media_type};base64,{encoded}"

        request_payload: dict[str, Any] = {
            "model": model,
            "document": {
                "type": document_kind,
                document_kind: document_value,
            },
            "include_blocks": bool(options.get("include_blocks", True)),
            "include_image_base64": bool(options.get("include_image_base64", True)),
            "confidence_scores_granularity": str(
                options.get("confidence_scores_granularity", "block")
            ),
            "table_format": str(options.get("table_format", "html")),
        }
        for name in ("pages", "image_limit", "image_min_size", "extract_header", "extract_footer"):
            if name in options:
                request_payload[name] = options[name]
        timeout = numeric_option(context, "timeout_seconds", 120.0, minimum=1.0, maximum=600.0)
        response = self._transport(
            method="POST",
            url=endpoint,
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "User-Agent": "docreconstruct/0.1",
            },
            body=json.dumps(request_payload, ensure_ascii=False).encode("utf-8"),
            timeout=timeout,
        )
        payload = decode_json_response(response, provider=self.name)
        effective_context = context or ProviderContext()
        if effective_context.source is None:
            effective_context = effective_context.model_copy(update={"source": hosted_source.label})
        document = self.normalize(payload, context=effective_context)
        return ProviderResult(
            provider=self.name,
            document=document,
            metadata={
                "hosted": True,
                "model": model,
                "request_id": response_request_id(response),
            },
        )

    def normalize(
        self,
        payload: Any,
        *,
        context: ProviderContext | None = None,
    ) -> Document:
        root = _mistral_root(payload)
        raw_pages = root.get("pages")
        if not isinstance(raw_pages, Sequence) or isinstance(raw_pages, (str, bytes)):
            raise ProviderInputError("Mistral OCR response must contain a pages array")
        pages_payload = [page for page in raw_pages if isinstance(page, Mapping)]
        zero_based = any(_integer(page.get("index")) == 0 for page in pages_payload)
        pages: list[Page] = []
        used_numbers: set[int] = set()
        model = str(root.get("model") or "mistral-ocr")
        for position, page_payload in enumerate(pages_payload):
            raw_index = _integer(page_payload.get("index"))
            number = (raw_index + 1 if zero_based and raw_index is not None else raw_index) or (
                position + 1
            )
            while number in used_numbers:
                number += 1
            used_numbers.add(number)
            width, height = _mistral_dimensions(page_payload, context)
            page_confidence = _record_confidence(page_payload)
            elements: list[Element] = []
            reading_order = 0
            for block_index, block in enumerate(_mistral_blocks(page_payload)):
                bbox = _record_bbox(block)
                kind = element_type(block.get("type") or block.get("kind") or "paragraph")
                text = text_from(block)
                if text is None and kind is ElementType.TABLE:
                    html = block.get("html")
                    text = html if isinstance(html, str) else None
                if bbox is None or text is None:
                    continue
                score = _record_confidence(block)
                if kind is ElementType.TEXT and text.lstrip().startswith("# "):
                    kind = ElementType.TITLE
                elif kind is ElementType.TEXT and text.lstrip().startswith("##"):
                    kind = ElementType.HEADING
                source_id = str(block.get("id") or f"page-{number}-block-{block_index + 1}")
                metadata: dict[str, Any] = {
                    "content_format": "markdown"
                    if isinstance(block.get("markdown"), str)
                    else "text",
                    "raw": safe_raw(block),
                }
                if kind is ElementType.TABLE:
                    html = block.get("html")
                    if not isinstance(html, str) and text.lstrip().lower().startswith("<table"):
                        html = text
                    if isinstance(html, str):
                        metadata["html"] = html
                        metadata["content_format"] = "html"
                if kind is ElementType.FORMULA:
                    latex = block.get("latex") or block.get("value")
                    if isinstance(latex, str):
                        metadata["latex"] = latex
                elements.append(
                    _element(
                        provider=self.name,
                        element_id=f"page-{number}-block-{block_index + 1}",
                        source_id=source_id,
                        kind=kind,
                        bbox=bbox,
                        polygon=coerce_polygon(block),
                        text=text,
                        confidence_value=score,
                        reading_order=reading_order,
                        model=model,
                        metadata=metadata,
                    )
                )
                reading_order += 1

            for field, kind, y0, y1 in (
                ("header", ElementType.HEADER, 0.0, height * 0.12),
                ("footer", ElementType.FOOTER, height * 0.88, height),
            ):
                text = page_payload.get(field)
                if isinstance(text, str) and text:
                    elements.append(
                        _element(
                            provider=self.name,
                            element_id=f"page-{number}-{field}",
                            source_id=f"page-{number}-{field}",
                            kind=kind,
                            bbox=BBox(x0=0, y0=y0, x1=width, y1=y1),
                            polygon=[],
                            text=text,
                            confidence_value=page_confidence,
                            reading_order=reading_order,
                            model=model,
                            metadata={"raw": {field: text}},
                        )
                    )
                    reading_order += 1

            for image_index, image in enumerate(_mistral_images(page_payload)):
                bbox = _record_bbox(image)
                if bbox is None:
                    continue
                image_id = str(image.get("id") or f"image-{image_index + 1}")
                elements.append(
                    _element(
                        provider=self.name,
                        element_id=f"page-{number}-image-{image_index + 1}",
                        source_id=image_id,
                        kind=ElementType.IMAGE,
                        bbox=bbox,
                        polygon=coerce_polygon(image),
                        text=None,
                        confidence_value=_record_confidence(image),
                        reading_order=reading_order,
                        model=model,
                        metadata={
                            "image_ref": image_id,
                            "image": _mistral_image_asset(image, image_id),
                            "raw": safe_raw(image),
                        },
                    )
                )
                reading_order += 1

            markdown = page_payload.get("markdown")
            if not any(element.text for element in elements) and isinstance(markdown, str):
                elements.insert(
                    0,
                    _element(
                        provider=self.name,
                        element_id=f"page-{number}-markdown",
                        source_id=f"page-{number}",
                        kind=ElementType.TEXT,
                        bbox=BBox(x0=0, y0=0, x1=width, y1=height),
                        polygon=[],
                        text=markdown,
                        confidence_value=page_confidence,
                        reading_order=0,
                        model=model,
                        metadata={
                            "content_format": "markdown",
                            "coordinate_system": "full_page_fallback",
                            "raw": {"markdown": markdown},
                        },
                    ),
                )
                elements = [
                    element.model_copy(update={"reading_order": index})
                    for index, element in enumerate(elements)
                ]

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
                        "source_page_index": raw_index,
                        "markdown": markdown if isinstance(markdown, str) else None,
                        "dimensions": safe_raw(page_payload.get("dimensions", {})),
                        "raw": safe_raw(page_payload),
                    },
                )
            )
        return Document(
            id=document_id(self.name, context),
            pages=pages,
            source=context.source if context else None,
            metadata={
                "provider": self.name,
                "model": model,
                "usage_info": safe_raw(root.get("usage_info", {})),
                "document_annotation": safe_raw(root.get("document_annotation")),
            },
        )


def _mistral_root(payload: Any) -> Mapping[str, Any]:
    if not isinstance(payload, Mapping):
        raise ProviderInputError("Mistral OCR saved response must be a JSON object")
    current: Mapping[str, Any] = payload
    for key in ("response", "result", "data"):
        nested = current.get(key)
        if isinstance(nested, Mapping) and "pages" in nested:
            current = nested
            break
    return current


def _integer(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _mistral_dimensions(
    page: Mapping[str, Any], context: ProviderContext | None
) -> tuple[float, float]:
    dimensions = page.get("dimensions")
    dimensions = dimensions if isinstance(dimensions, Mapping) else {}
    width = as_float(dimensions.get("width")) or as_float(page.get("width"))
    height = as_float(dimensions.get("height")) or as_float(page.get("height"))
    if width is None and context is not None:
        width = context.page_width
    if height is None and context is not None:
        height = context.page_height
    return max(1.0, width or 1000.0), max(1.0, height or 1400.0)


def _mistral_blocks(page: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    for key in ("blocks", "content_blocks", "bbox_annotations", "block_annotations"):
        value = page.get(key)
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            return [item for item in value if isinstance(item, Mapping)]
    return []


def _mistral_images(page: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    value = page.get("images")
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _record_bbox(record: Mapping[str, Any]) -> BBox | None:
    bbox = coerce_bbox(record)
    if bbox is not None:
        return bbox
    nested = record.get("bounding_box")
    bbox = coerce_bbox(nested)
    if bbox is not None:
        return bbox
    coordinates = [
        as_float(record.get("top_left_x")),
        as_float(record.get("top_left_y")),
        as_float(record.get("bottom_right_x")),
        as_float(record.get("bottom_right_y")),
    ]
    if all(value is not None for value in coordinates):
        return BBox.from_sequence([float(value) for value in coordinates if value is not None])
    return None


def _record_confidence(record: Mapping[str, Any]) -> float | None:
    for key in ("confidence", "confidence_score", "score", "probability"):
        value = confidence(record.get(key))
        if value is not None:
            return value
    scores = record.get("scores")
    if isinstance(scores, Mapping):
        values = [confidence(value) for value in scores.values()]
        present = [value for value in values if value is not None]
        if present:
            return sum(present) / len(present)
    return None


def _mistral_image_asset(image: Mapping[str, Any], image_id: str) -> dict[str, Any]:
    """Expose returned image bytes in the renderer's generic image contract."""

    asset: dict[str, Any] = {"src": image_id}
    encoded = image.get("image_base64")
    if not isinstance(encoded, str) or not encoded:
        return asset
    mime_type = mimetypes.guess_type(image_id)[0] or "image/png"
    payload = encoded
    if encoded.startswith("data:") and ";base64," in encoded:
        header, payload = encoded.split(",", 1)
        declared_type = header[5:].split(";", 1)[0].strip()
        if declared_type.startswith("image/"):
            mime_type = declared_type
    asset.update({"data": payload, "mime_type": mime_type})
    return asset


def _element(
    *,
    provider: str,
    element_id: str,
    source_id: str,
    kind: ElementType,
    bbox: BBox,
    polygon: list[Any],
    text: str | None,
    confidence_value: float | None,
    reading_order: int,
    model: str,
    metadata: dict[str, Any],
) -> Element:
    return Element(
        id=element_id,
        type=kind,
        bbox=bbox,
        polygon=polygon,
        text=text,
        reading_order=reading_order,
        confidence=confidence_value,
        provenance=Provenance(
            engine=provider,
            source_id=source_id,
            text_confidence=confidence_value if text is not None else None,
            layout_confidence=confidence_value,
            metadata={"model": model},
        ),
        text_candidates=(
            [
                TextCandidate(
                    engine=provider,
                    value=text,
                    confidence=confidence_value,
                    source_element_id=source_id,
                    metadata={"content_format": metadata.get("content_format", "text")},
                )
            ]
            if text is not None
            else []
        ),
        metadata=metadata,
    )


MistralOcrProvider = MistralOCRProvider

__all__ = ["MistralOCRProvider", "MistralOcrProvider"]
