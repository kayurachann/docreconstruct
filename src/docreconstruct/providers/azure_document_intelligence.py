"""Saved-response and hosted adapter for Azure AI Document Intelligence layout."""

from __future__ import annotations

import base64
import json
import re
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode, urlparse

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

from ._hosted import (
    HTTPResponse,
    HTTPTransport,
    ProviderAuthenticationError,
    ProviderHTTPError,
    context_options,
    decode_json_response,
    load_hosted_source,
    numeric_option,
    option_or_environment,
    require_credential,
    require_remote_opt_in,
    response_header,
    response_request_id,
    safe_raw,
    stdlib_http_transport,
    validate_https_url,
    validate_service_endpoint,
)
from ._utils import as_float, coerce_polygon, confidence, document_id, looks_like_inline_json
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

_MODEL_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._~-]{1,63}$")


class AzureDocumentIntelligenceProvider(SavedJSONProvider):
    """Normalize v4 layout results or run the official asynchronous REST API."""

    name = "azure_document_intelligence"
    _capabilities = ProviderCapabilities(
        provider=name,
        supported_inputs=["pdf", "png", "jpeg", "tiff", "office", "json"],
        saved_json=True,
        live_inference=True,
        text=True,
        geometry=True,
        reading_order=True,
        styles=True,
        tables=True,
        images=True,
        multilingual=True,
        handwriting=True,
        formulas=True,
        layout=True,
        execution_modes=[ProviderExecutionMode.SAVED, ProviderExecutionMode.API],
        markdown=True,
        confidence_scores=True,
        privacy=ProviderPrivacy.THIRD_PARTY,
        license=ProviderLicense(
            name="Microsoft Azure hosted service",
            open_source=False,
            commercial_use=True,
        ),
        model_name="prebuilt-layout",
        model_version="2024-11-30",
        cost=ProviderCost.METERED,
        credentials=ProviderCredentialRequirement.REQUIRED,
        credential_env_vars=[
            "AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT",
            "AZURE_DOCUMENT_INTELLIGENCE_KEY",
        ],
        notes=[
            "Hosted inference is disabled unless ProviderContext.options.allow_remote is true.",
            "Uses Azure Document Intelligence v4 REST with Markdown output and polling.",
        ],
    )

    def __init__(
        self,
        *,
        transport: HTTPTransport | None = None,
        sleeper: Callable[[float], None] | None = None,
    ) -> None:
        self._transport = transport or stdlib_http_transport
        self._sleep = sleeper or time.sleep

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
        options = context_options(context)
        endpoint = option_or_environment(
            context,
            "endpoint",
            (
                "AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT",
                "DOCUMENTINTELLIGENCE_ENDPOINT",
            ),
        )
        if endpoint is None:
            raise ProviderAuthenticationError(
                "azure_document_intelligence requires ProviderContext option 'endpoint' or "
                "AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT."
            )
        endpoint = validate_service_endpoint(
            endpoint.rstrip("/"),
            label="Azure endpoint",
            allowed_hosts=(
                "*.cognitiveservices.azure.com",
                "*.api.cognitive.microsoft.com",
            ),
            allow_custom_endpoint=options.get("allow_custom_endpoint") is True,
        )
        api_key = require_credential(
            context,
            provider=self.name,
            option_name="api_key",
            environment_names=(
                "AZURE_DOCUMENT_INTELLIGENCE_KEY",
                "DOCUMENTINTELLIGENCE_API_KEY",
            ),
        )
        model = str(options.get("model", "prebuilt-layout")).strip()
        if not _MODEL_PATTERN.fullmatch(model):
            raise ProviderInputError("invalid Azure Document Intelligence model ID")
        api_version = str(options.get("api_version", "2024-11-30")).strip()
        if not api_version:
            raise ProviderInputError("Azure Document Intelligence api_version must not be blank")
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
        if hosted_source.url is not None:
            request_payload = {"urlSource": hosted_source.url}
        else:
            assert hosted_source.data is not None
            request_payload = {"base64Source": base64.b64encode(hosted_source.data).decode("ascii")}

        features = options.get(
            "features",
            ["ocrHighResolution", "languages", "formulas", "styleFont"],
        )
        if isinstance(features, str):
            features_value = features
        elif isinstance(features, Sequence):
            features_value = ",".join(str(feature) for feature in features if str(feature))
        else:
            raise ProviderInputError("Azure features must be a string or sequence")
        query: dict[str, str] = {
            "_overload": "analyzeDocument",
            "api-version": api_version,
            "outputContentFormat": "markdown",
            "stringIndexType": "unicodeCodePoint",
        }
        if features_value:
            query["features"] = features_value
        for name in ("locale", "pages"):
            if name in options and options[name] is not None:
                query[name] = str(options[name])
        analyze_url = (
            f"{endpoint}/documentintelligence/documentModels/{quote(model, safe='._~-')}:analyze?"
            + urlencode(query)
        )
        timeout = numeric_option(context, "timeout_seconds", 120.0, minimum=1.0, maximum=600.0)
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Ocp-Apim-Subscription-Key": api_key,
            "User-Agent": "docreconstruct/0.1",
        }
        submitted = self._transport(
            method="POST",
            url=analyze_url,
            headers=headers,
            body=json.dumps(request_payload).encode("utf-8"),
            timeout=timeout,
        )
        if submitted.status == 200:
            payload = decode_json_response(submitted, provider=self.name)
            completed = submitted
        elif submitted.status == 202:
            operation_location = response_header(submitted, "Operation-Location")
            if not operation_location:
                raise ProviderHTTPError(
                    "Azure Document Intelligence accepted the request without Operation-Location"
                )
            operation_location = validate_https_url(
                operation_location,
                label="Azure Operation-Location",
            )
            if (
                urlparse(operation_location).hostname != urlparse(endpoint).hostname
                and options.get("allow_cross_host_poll") is not True
            ):
                raise ProviderHTTPError(
                    "Azure Operation-Location host differs from the configured endpoint"
                )
            payload, completed = self._poll(
                operation_location,
                api_key=api_key,
                context=context,
                timeout=timeout,
            )
        else:
            decode_json_response(submitted, provider=self.name)
            raise AssertionError("unreachable")

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
                "api_version": api_version,
                "request_id": response_request_id(completed) or response_request_id(submitted),
            },
        )

    def _poll(
        self,
        operation_location: str,
        *,
        api_key: str,
        context: ProviderContext | None,
        timeout: float,
    ) -> tuple[Any, HTTPResponse]:
        attempts = int(numeric_option(context, "max_poll_attempts", 120, minimum=1, maximum=1000))
        interval = numeric_option(
            context,
            "poll_interval_seconds",
            1.0,
            minimum=0.0,
            maximum=30.0,
        )
        for attempt in range(attempts):
            response = self._transport(
                method="GET",
                url=operation_location,
                headers={
                    "Accept": "application/json",
                    "Ocp-Apim-Subscription-Key": api_key,
                    "User-Agent": "docreconstruct/0.1",
                },
                body=None,
                timeout=timeout,
            )
            payload = decode_json_response(response, provider=self.name)
            status = (
                str(payload.get("status", "succeeded")).casefold()
                if isinstance(payload, Mapping)
                else "succeeded"
            )
            if status == "succeeded":
                return payload, response
            if status in {"failed", "canceled", "cancelled"}:
                detail = (
                    payload.get("error", "analysis failed")
                    if isinstance(payload, Mapping)
                    else "analysis failed"
                )
                raise ProviderHTTPError(f"Azure Document Intelligence {status}: {detail}")
            if attempt + 1 < attempts:
                retry_after = response_header(response, "Retry-After")
                try:
                    delay = float(retry_after) if retry_after is not None else interval
                except ValueError:
                    delay = interval
                if delay > 0:
                    self._sleep(min(30.0, delay))
        raise ProviderHTTPError(
            f"Azure Document Intelligence did not finish after {attempts} polls"
        )

    def normalize(
        self,
        payload: Any,
        *,
        context: ProviderContext | None = None,
    ) -> Document:
        result = _azure_result(payload)
        raw_pages = result.get("pages")
        if not isinstance(raw_pages, Sequence) or isinstance(raw_pages, (str, bytes)):
            raise ProviderInputError("Azure analyze result must contain a pages array")
        page_payloads = [page for page in raw_pages if isinstance(page, Mapping)]
        pages_by_number: dict[int, Mapping[str, Any]] = {}
        for index, page in enumerate(page_payloads):
            number = _integer(page.get("pageNumber")) or index + 1
            pages_by_number[number] = page
        if not pages_by_number:
            return Document(
                id=document_id(self.name, context),
                pages=[],
                source=context.source if context else None,
                metadata={"provider": self.name},
            )

        raw_content = result.get("content")
        content = raw_content if isinstance(raw_content, str) else ""
        styles = _mapping_list(result.get("styles"))
        paragraphs = _mapping_list(result.get("paragraphs"))
        tables = _mapping_list(result.get("tables"))
        figures = _mapping_list(result.get("figures"))
        page_elements: dict[int, list[tuple[int, Element]]] = {
            number: [] for number in pages_by_number
        }
        counters: dict[int, int] = {number: 0 for number in pages_by_number}

        for record in paragraphs:
            page_number = _page_number_for_record(record, pages_by_number)
            if page_number is None:
                continue
            page = pages_by_number[page_number]
            counters[page_number] += 1
            text = record.get("content")
            if not isinstance(text, str):
                continue
            role = str(record.get("role") or "paragraph")
            kind = _paragraph_type(role)
            score = _azure_confidence(record, page)
            bbox, polygon, fallback = _azure_geometry(record, page)
            matching_style = _matching_style(record, styles)
            metadata = {
                "role": role,
                "raw": safe_raw(record),
                "coordinate_system": "full_page_fallback" if fallback else "source",
            }
            if matching_style and matching_style.get("isHandwritten") is True:
                metadata["handwriting"] = True
            element = _azure_element(
                provider=self.name,
                element_id=f"page-{page_number}-paragraph-{counters[page_number]}",
                source_id=f"/paragraphs/{counters[page_number] - 1}",
                kind=kind,
                bbox=bbox,
                polygon=polygon,
                text=text,
                score=score,
                order=_span_offset(record),
                metadata=metadata,
                style=_azure_style(matching_style),
                model=str(result.get("modelId") or "prebuilt-layout"),
            )
            page_elements[page_number].append((_span_offset(record), element))

        for table_index, record in enumerate(tables, start=1):
            page_number = _page_number_for_record(record, pages_by_number)
            if page_number is None:
                continue
            page = pages_by_number[page_number]
            bbox, polygon, fallback = _azure_geometry(record, page)
            rows = _table_rows(record)
            element = _azure_element(
                provider=self.name,
                element_id=f"page-{page_number}-table-{table_index}",
                source_id=f"/tables/{table_index - 1}",
                kind=ElementType.TABLE,
                bbox=bbox,
                polygon=polygon,
                text=None,
                score=_azure_confidence(record, page),
                order=_span_offset(record),
                metadata={
                    "rows": rows,
                    "cells": safe_raw(record.get("cells", [])),
                    "raw": safe_raw(record),
                    "coordinate_system": "full_page_fallback" if fallback else "source",
                },
                style=ElementStyle(),
                model=str(result.get("modelId") or "prebuilt-layout"),
            )
            page_elements[page_number].append((_span_offset(record), element))

        for figure_index, record in enumerate(figures, start=1):
            page_number = _page_number_for_record(record, pages_by_number)
            if page_number is None:
                continue
            page = pages_by_number[page_number]
            bbox, polygon, fallback = _azure_geometry(record, page)
            caption = record.get("caption")
            text = caption.get("content") if isinstance(caption, Mapping) else None
            element = _azure_element(
                provider=self.name,
                element_id=f"page-{page_number}-figure-{figure_index}",
                source_id=str(record.get("id") or f"/figures/{figure_index - 1}"),
                kind=ElementType.FIGURE,
                bbox=bbox,
                polygon=polygon,
                text=text if isinstance(text, str) else None,
                score=_azure_confidence(record, page),
                order=_span_offset(record),
                metadata={
                    "image_ref": str(record.get("id") or f"figure-{figure_index}"),
                    "raw": safe_raw(record),
                    "coordinate_system": "full_page_fallback" if fallback else "source",
                },
                style=ElementStyle(),
                model=str(result.get("modelId") or "prebuilt-layout"),
            )
            page_elements[page_number].append((_span_offset(record), element))

        for number, page in pages_by_number.items():
            for formula_index, record in enumerate(_mapping_list(page.get("formulas")), start=1):
                bbox, polygon, fallback = _azure_geometry(record, page)
                latex = record.get("value")
                element = _azure_element(
                    provider=self.name,
                    element_id=f"page-{number}-formula-{formula_index}",
                    source_id=f"page-{number}/formulas/{formula_index - 1}",
                    kind=ElementType.FORMULA,
                    bbox=bbox,
                    polygon=polygon,
                    text=latex if isinstance(latex, str) else None,
                    score=_azure_confidence(record, page),
                    order=_span_offset(record),
                    metadata={
                        "latex": latex,
                        "formula_kind": record.get("kind"),
                        "raw": safe_raw(record),
                        "coordinate_system": "full_page_fallback" if fallback else "source",
                    },
                    style=ElementStyle(),
                    model=str(result.get("modelId") or "prebuilt-layout"),
                )
                page_elements[number].append((_span_offset(record), element))
            for mark_index, record in enumerate(_mapping_list(page.get("selectionMarks")), start=1):
                bbox, polygon, fallback = _azure_geometry(record, page)
                selected = str(record.get("state", "")).casefold() == "selected"
                element = _azure_element(
                    provider=self.name,
                    element_id=f"page-{number}-checkbox-{mark_index}",
                    source_id=f"page-{number}/selectionMarks/{mark_index - 1}",
                    kind=ElementType.CHECKBOX,
                    bbox=bbox,
                    polygon=polygon,
                    text="☒" if selected else "☐",
                    score=_azure_confidence(record, page),
                    order=_span_offset(record),
                    metadata={
                        "selected": selected,
                        "raw": safe_raw(record),
                        "coordinate_system": "full_page_fallback" if fallback else "source",
                    },
                    style=ElementStyle(),
                    model=str(result.get("modelId") or "prebuilt-layout"),
                )
                page_elements[number].append((_span_offset(record), element))

            if not any(element.text for _, element in page_elements[number]):
                for line_index, record in enumerate(_mapping_list(page.get("lines")), start=1):
                    text = record.get("content")
                    if not isinstance(text, str):
                        continue
                    bbox, polygon, fallback = _azure_geometry(record, page)
                    element = _azure_element(
                        provider=self.name,
                        element_id=f"page-{number}-line-{line_index}",
                        source_id=f"page-{number}/lines/{line_index - 1}",
                        kind=ElementType.TEXT,
                        bbox=bbox,
                        polygon=polygon,
                        text=text,
                        score=_azure_confidence(record, page),
                        order=_span_offset(record),
                        metadata={
                            "raw": safe_raw(record),
                            "coordinate_system": "full_page_fallback" if fallback else "source",
                        },
                        style=ElementStyle(),
                        model=str(result.get("modelId") or "prebuilt-layout"),
                    )
                    page_elements[number].append((_span_offset(record), element))

        pages: list[Page] = []
        for number, page in sorted(pages_by_number.items()):
            width, height, scale = _azure_page_dimensions(page)
            ordered = sorted(
                page_elements[number],
                key=lambda item: (item[0], item[1].bbox.y0, item[1].bbox.x0, item[1].id),
            )
            elements = [
                element.model_copy(update={"reading_order": index})
                for index, (_, element) in enumerate(ordered)
            ]
            page_markdown = _content_for_spans(content, page.get("spans"))
            if not elements and page_markdown:
                elements = [
                    _azure_element(
                        provider=self.name,
                        element_id=f"page-{number}-markdown",
                        source_id=f"page-{number}",
                        kind=ElementType.TEXT,
                        bbox=BBox(x0=0, y0=0, x1=width, y1=height),
                        polygon=[],
                        text=page_markdown,
                        score=None,
                        order=0,
                        metadata={
                            "content_format": "markdown",
                            "coordinate_system": "full_page_fallback",
                        },
                        style=ElementStyle(),
                        model=str(result.get("modelId") or "prebuilt-layout"),
                    )
                ]
            pages.append(
                Page(
                    id=f"page-{number}",
                    number=number,
                    width=width,
                    height=height,
                    rotation=float(as_float(page.get("angle")) or 0.0),
                    elements=elements,
                    source_type=SourceType.IMAGE,
                    metadata={
                        "provider": self.name,
                        "markdown": page_markdown,
                        "source_unit": page.get("unit"),
                        "coordinate_scale": scale,
                        "raw": safe_raw(page),
                    },
                )
            )
        return Document(
            id=document_id(self.name, context),
            pages=pages,
            source=context.source if context else None,
            metadata={
                "provider": self.name,
                "model": result.get("modelId"),
                "api_version": result.get("apiVersion"),
                "content_format": result.get("contentFormat"),
                "content_markdown": content,
                "languages": safe_raw(result.get("languages", [])),
            },
        )


def _azure_result(payload: Any) -> Mapping[str, Any]:
    if not isinstance(payload, Mapping):
        raise ProviderInputError("Azure saved response must be a JSON object")
    status = str(payload.get("status", "")).casefold()
    if status in {"failed", "canceled", "cancelled"}:
        raise ProviderInputError(f"Azure saved response status is {status}")
    analyze_result = payload.get("analyzeResult")
    if isinstance(analyze_result, Mapping):
        return analyze_result
    for key in ("result", "data", "response"):
        nested = payload.get(key)
        if isinstance(nested, Mapping):
            if isinstance(nested.get("analyzeResult"), Mapping):
                return nested["analyzeResult"]
            if "pages" in nested:
                return nested
    if "pages" in payload:
        return payload
    raise ProviderInputError("Azure saved response contains no analyzeResult")


def _mapping_list(value: Any) -> list[Mapping[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _integer(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _spans(record: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    value = record.get("spans")
    if isinstance(value, Mapping):
        return [value]
    return _mapping_list(value)


def _span_offset(record: Mapping[str, Any]) -> int:
    offsets = [_integer(span.get("offset")) for span in _spans(record)]
    present = [offset for offset in offsets if offset is not None]
    return min(present, default=10**12)


def _span_intersects(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    left_start = _integer(left.get("offset")) or 0
    right_start = _integer(right.get("offset")) or 0
    left_end = left_start + (_integer(left.get("length")) or 0)
    right_end = right_start + (_integer(right.get("length")) or 0)
    return max(left_start, right_start) < min(left_end, right_end)


def _page_number_for_record(
    record: Mapping[str, Any], pages: Mapping[int, Mapping[str, Any]]
) -> int | None:
    for region in _mapping_list(record.get("boundingRegions")):
        number = _integer(region.get("pageNumber"))
        if number in pages:
            return number
    record_spans = _spans(record)
    for number, page in pages.items():
        if any(
            _span_intersects(record_span, page_span)
            for record_span in record_spans
            for page_span in _spans(page)
        ):
            return number
    return next(iter(pages), None)


def _azure_page_dimensions(page: Mapping[str, Any]) -> tuple[float, float, float]:
    scale = 72.0 if str(page.get("unit", "")).casefold() == "inch" else 1.0
    width = max(1.0, (as_float(page.get("width")) or 1.0) * scale)
    height = max(1.0, (as_float(page.get("height")) or 1.0) * scale)
    return width, height, scale


def _azure_geometry(
    record: Mapping[str, Any], page: Mapping[str, Any]
) -> tuple[BBox, list[Point], bool]:
    width, height, scale = _azure_page_dimensions(page)
    polygon_value: Any = record.get("polygon")
    if polygon_value is None:
        for region in _mapping_list(record.get("boundingRegions")):
            if _integer(region.get("pageNumber")) in {None, _integer(page.get("pageNumber"))}:
                polygon_value = region.get("polygon")
                if polygon_value is not None:
                    break
    points = coerce_polygon(polygon_value)
    if points:
        scaled = [Point(x=point.x * scale, y=point.y * scale) for point in points]
        bbox = BBox(
            x0=min(point.x for point in scaled),
            y0=min(point.y for point in scaled),
            x1=max(point.x for point in scaled),
            y1=max(point.y for point in scaled),
        )
        return bbox, scaled, False
    return BBox(x0=0, y0=0, x1=width, y1=height), [], True


def _azure_confidence(record: Mapping[str, Any], page: Mapping[str, Any]) -> float | None:
    direct = confidence(record.get("confidence"))
    if direct is not None:
        return direct
    record_spans = _spans(record)
    values: list[float] = []
    for word in _mapping_list(page.get("words")):
        word_span = word.get("span")
        if not isinstance(word_span, Mapping):
            continue
        if record_spans and not any(_span_intersects(span, word_span) for span in record_spans):
            continue
        value = confidence(word.get("confidence"))
        if value is not None:
            values.append(value)
    return sum(values) / len(values) if values else None


def _matching_style(
    record: Mapping[str, Any], styles: Sequence[Mapping[str, Any]]
) -> Mapping[str, Any] | None:
    record_spans = _spans(record)
    for style in styles:
        if any(
            _span_intersects(record_span, style_span)
            for record_span in record_spans
            for style_span in _spans(style)
        ):
            return style
    return None


def _azure_style(style: Mapping[str, Any] | None) -> ElementStyle:
    if not style:
        return ElementStyle()
    weight = str(style.get("fontWeight", "")).casefold()
    font_style = str(style.get("fontStyle", "")).casefold()
    return ElementStyle(
        font_family=style.get("similarFontFamily")
        if isinstance(style.get("similarFontFamily"), str)
        else None,
        font_weight=700 if weight == "bold" else 400 if weight == "normal" else None,
        italic=True if font_style == "italic" else False if font_style == "normal" else None,
        color=style.get("color") if isinstance(style.get("color"), str) else None,
        background_color=(
            style.get("backgroundColor") if isinstance(style.get("backgroundColor"), str) else None
        ),
    )


def _paragraph_type(role: str) -> ElementType:
    return {
        "pageheader": ElementType.HEADER,
        "pagefooter": ElementType.FOOTER,
        "pagenumber": ElementType.PAGE_NUMBER,
        "title": ElementType.TITLE,
        "sectionheading": ElementType.HEADING,
        "footnote": ElementType.FOOTNOTE,
        "formulablock": ElementType.FORMULA,
    }.get(role.casefold(), ElementType.PARAGRAPH)


def _table_rows(record: Mapping[str, Any]) -> list[list[str]]:
    cells = _mapping_list(record.get("cells"))
    declared_rows = _integer(record.get("rowCount"))
    declared_columns = _integer(record.get("columnCount"))
    row_count = declared_rows or (
        max(
            (_integer(cell.get("rowIndex")) or 0) + (_integer(cell.get("rowSpan")) or 1)
            for cell in cells
        )
        if cells
        else 0
    )
    column_count = declared_columns or (
        max(
            (_integer(cell.get("columnIndex")) or 0) + (_integer(cell.get("columnSpan")) or 1)
            for cell in cells
        )
        if cells
        else 0
    )
    rows = [["" for _ in range(column_count)] for _ in range(row_count)]
    for cell in cells:
        row = _integer(cell.get("rowIndex")) or 0
        column = _integer(cell.get("columnIndex")) or 0
        if 0 <= row < row_count and 0 <= column < column_count:
            rows[row][column] = str(cell.get("content") or "")
    return rows


def _content_for_spans(content: str, value: Any) -> str:
    spans = _mapping_list(value) if not isinstance(value, Mapping) else [value]
    pieces: list[str] = []
    for span in spans:
        offset = _integer(span.get("offset")) or 0
        length = _integer(span.get("length")) or 0
        if length > 0:
            pieces.append(content[offset : offset + length])
    return "".join(pieces)


def _azure_element(
    *,
    provider: str,
    element_id: str,
    source_id: str,
    kind: ElementType,
    bbox: BBox,
    polygon: list[Point],
    text: str | None,
    score: float | None,
    order: int,
    metadata: dict[str, Any],
    style: ElementStyle,
    model: str,
) -> Element:
    return Element(
        id=element_id,
        type=kind,
        bbox=bbox,
        polygon=polygon,
        text=text,
        reading_order=max(0, order),
        confidence=score,
        style=style,
        provenance=Provenance(
            engine=provider,
            source_id=source_id,
            text_confidence=score if text is not None else None,
            layout_confidence=score,
            metadata={"model": model},
        ),
        text_candidates=(
            [
                TextCandidate(
                    engine=provider,
                    value=text,
                    confidence=score,
                    source_element_id=source_id,
                )
            ]
            if text is not None
            else []
        ),
        metadata=metadata,
    )


AzureDocumentAIProvider = AzureDocumentIntelligenceProvider

__all__ = ["AzureDocumentAIProvider", "AzureDocumentIntelligenceProvider"]
