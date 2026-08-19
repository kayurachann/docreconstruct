"""Saved-response and opt-in REST adapter for Google Cloud Document AI v1."""

from __future__ import annotations

import base64
import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

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
    HTTPTransport,
    context_options,
    decode_json_response,
    load_hosted_source,
    numeric_option,
    option_or_environment,
    redacted_url_label,
    require_credential,
    require_remote_opt_in,
    safe_raw,
    stdlib_http_transport,
)
from ._utils import as_float, confidence, document_id, looks_like_inline_json
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

_RESOURCE_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._~-]{0,127}$")
_LOCATION = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
_URL_PATTERN = re.compile(r"https?://[^\s<>'\"]+", re.IGNORECASE)
_MAPPING_SEQUENCE_EXCLUSIONS = (str, bytes, bytearray)


class GoogleDocumentAIProvider(SavedJSONProvider):
    """Normalize Document AI JSON or call its official synchronous v1 REST API."""

    name = "google_document_ai"
    _capabilities = ProviderCapabilities(
        provider=name,
        supported_inputs=["pdf", "png", "jpeg", "tiff", "gif", "bmp", "webp", "gcs", "json"],
        saved_json=True,
        live_inference=True,
        text=True,
        geometry=True,
        reading_order=True,
        styles=True,
        tables=True,
        multilingual=True,
        handwriting=True,
        layout=True,
        execution_modes=[ProviderExecutionMode.SAVED, ProviderExecutionMode.API],
        markdown=True,
        confidence_scores=True,
        privacy=ProviderPrivacy.THIRD_PARTY,
        license=ProviderLicense(
            name="Google Cloud Document AI hosted service",
            open_source=False,
            commercial_use=True,
        ),
        cost=ProviderCost.METERED,
        credentials=ProviderCredentialRequirement.REQUIRED,
        credential_env_vars=["GOOGLE_DOCUMENT_AI_ACCESS_TOKEN"],
        notes=[
            "Hosted inference is disabled unless ProviderContext.options.allow_remote is true.",
            "Uses the official Google Cloud Document AI v1 processors.process REST method.",
            "The caller supplies a short-lived OAuth bearer token; credential files are not read.",
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
        access_token = require_credential(
            context,
            provider=self.name,
            option_name="access_token",
            environment_names=("GOOGLE_DOCUMENT_AI_ACCESS_TOKEN",),
        )
        if access_token.casefold().startswith("bearer "):
            access_token = access_token[7:].strip()
        if not access_token:
            raise ProviderInputError("Google Document AI access_token must not be blank")

        project_id = _required_setting(
            context,
            option_name="project_id",
            environment_names=("GOOGLE_CLOUD_PROJECT", "GOOGLE_DOCUMENT_AI_PROJECT_ID"),
        )
        location = _required_setting(
            context,
            option_name="location",
            environment_names=("GOOGLE_DOCUMENT_AI_LOCATION",),
        ).casefold()
        processor_id = _required_setting(
            context,
            option_name="processor_id",
            environment_names=("GOOGLE_DOCUMENT_AI_PROCESSOR_ID",),
        )
        processor_version = option_or_environment(
            context,
            "processor_version",
            ("GOOGLE_DOCUMENT_AI_PROCESSOR_VERSION",),
        )
        _validate_segment(project_id, label="project_id")
        if not _LOCATION.fullmatch(location):
            raise ProviderInputError("Google Document AI location is not a valid resource location")
        _validate_segment(processor_id, label="processor_id")
        if processor_version is not None:
            _validate_segment(processor_version, label="processor_version")

        options = context_options(context)
        maximum_megabytes = numeric_option(
            context,
            "max_upload_mb",
            40.0,
            minimum=0.1,
            maximum=512.0,
        )
        source_label: str
        if isinstance(source, str) and source.casefold().startswith("gs://"):
            gcs_uri = _validate_gcs_uri(source)
            media_type = str(options.get("media_type", "application/pdf")).strip()
            if not media_type:
                raise ProviderInputError("Google Document AI media_type must not be blank")
            request_payload: dict[str, Any] = {
                "gcsDocument": {"gcsUri": gcs_uri, "mimeType": media_type}
            }
            source_label = gcs_uri
        else:
            hosted_source = load_hosted_source(
                source,
                context=context,
                maximum_megabytes=maximum_megabytes,
            )
            if hosted_source.url is not None:
                raise ProviderInputError(
                    "Google Document AI accepts inline bytes or a gs:// Cloud Storage URI; "
                    "an HTTPS source URL cannot be used as gcsDocument"
                )
            assert hosted_source.data is not None
            request_payload = {
                "rawDocument": {
                    "content": base64.b64encode(hosted_source.data).decode("ascii"),
                    "mimeType": hosted_source.media_type,
                }
            }
            source_label = hosted_source.label

        _copy_process_request_options(request_payload, options)
        resource_name = f"projects/{project_id}/locations/{location}/processors/{processor_id}"
        if processor_version is not None:
            resource_name += f"/processorVersions/{processor_version}"
        hostname = (
            "documentai.googleapis.com"
            if location == "global"
            else f"{location}-documentai.googleapis.com"
        )
        process_url = f"https://{hostname}/v1/{resource_name}:process"
        timeout = numeric_option(context, "timeout_seconds", 120.0, minimum=1.0, maximum=600.0)
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": "docreconstruct/0.1",
        }
        quota_project = option_or_environment(
            context,
            "quota_project_id",
            ("GOOGLE_CLOUD_QUOTA_PROJECT",),
        )
        if quota_project is not None:
            _validate_segment(quota_project, label="quota_project_id")
            headers["x-goog-user-project"] = quota_project
        try:
            body = json.dumps(request_payload, separators=(",", ":")).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise ProviderInputError(
                "Google Document AI request options must be JSON values"
            ) from exc
        response = self._transport(
            method="POST",
            url=process_url,
            headers=headers,
            body=body,
            timeout=timeout,
        )
        payload = decode_json_response(response, provider=self.name)

        effective_context = context or ProviderContext()
        safe_source = _safe_source_label(source_label)
        if effective_context.source != safe_source:
            effective_context = effective_context.model_copy(update={"source": safe_source})
        document = self.normalize(payload, context=effective_context)
        return ProviderResult(
            provider=self.name,
            document=document,
            metadata={
                "hosted": True,
                "api_version": "v1",
                "project_id": project_id,
                "location": location,
                "processor_id": processor_id,
                "processor_version": processor_version,
                "request_id": _google_request_id(response.headers),
            },
        )

    def normalize(
        self,
        payload: Any,
        *,
        context: ProviderContext | None = None,
    ) -> Document:
        root, response_metadata = _google_document(payload)
        raw_text = root.get("text")
        document_text = raw_text if isinstance(raw_text, str) else ""
        raw_pages = root.get("pages", [])
        if not isinstance(raw_pages, Sequence) or isinstance(
            raw_pages, _MAPPING_SEQUENCE_EXCLUSIONS
        ):
            raise ProviderInputError("Google Document AI document.pages must be an array")

        pages: list[Page] = []
        for page_index, raw_page in enumerate(raw_pages):
            if not isinstance(raw_page, Mapping):
                continue
            page_number = _positive_integer(raw_page.get("pageNumber")) or page_index + 1
            width, height, unit = _page_dimensions(raw_page)
            entries: list[tuple[int, int, int, Element]] = []
            markdown_entries: list[tuple[int, int, str]] = []
            record_counter = 0
            primary_text_entries: dict[str, list[tuple[int, int, str]]] = {
                "paragraph": [],
                "block": [],
                "token": [],
            }

            for tier, (collection_name, kind) in enumerate(
                (
                    ("paragraphs", ElementType.PARAGRAPH),
                    ("blocks", ElementType.TEXT),
                    ("tokens", ElementType.TEXT),
                )
            ):
                singular = collection_name[:-1]
                for item_index, record in enumerate(_mapping_list(raw_page.get(collection_name))):
                    record_counter += 1
                    element = _layout_element(
                        provider=self.name,
                        page_number=page_number,
                        page_index=page_index,
                        record=record,
                        record_kind=singular,
                        item_index=item_index,
                        kind=kind,
                        document_text=document_text,
                        page_width=width,
                        page_height=height,
                    )
                    order = _layout_start(record.get("layout"))
                    entries.append((order, tier, record_counter, element))
                    if element.text:
                        primary_text_entries[singular].append((order, record_counter, element.text))

            for table_index, record in enumerate(_mapping_list(raw_page.get("tables"))):
                record_counter += 1
                element = _table_element(
                    provider=self.name,
                    page_number=page_number,
                    page_index=page_index,
                    record=record,
                    table_index=table_index,
                    document_text=document_text,
                    page_width=width,
                    page_height=height,
                )
                order = _layout_start(record.get("layout"))
                entries.append((order, 3, record_counter, element))
                markdown = element.metadata.get("markdown")
                if isinstance(markdown, str) and markdown:
                    markdown_entries.append((order, record_counter, markdown))

            for field_index, record in enumerate(_mapping_list(raw_page.get("formFields"))):
                record_counter += 1
                element = _form_field_element(
                    provider=self.name,
                    page_number=page_number,
                    page_index=page_index,
                    record=record,
                    field_index=field_index,
                    document_text=document_text,
                    page_width=width,
                    page_height=height,
                )
                order = min(
                    _layout_start(record.get("fieldName")),
                    _layout_start(record.get("fieldValue")),
                )
                entries.append((order, 4, record_counter, element))
                if element.text:
                    markdown_entries.append((order, record_counter, element.text))

            ordered = sorted(
                entries,
                key=lambda item: (item[0], item[1], item[2], item[3].bbox.y0, item[3].bbox.x0),
            )
            elements = [
                element.model_copy(update={"reading_order": reading_order})
                for reading_order, (_, _, _, element) in enumerate(ordered)
            ]
            primary = (
                primary_text_entries["paragraph"]
                or primary_text_entries["block"]
                or primary_text_entries["token"]
            )
            page_markdown = _markdown_from_entries([*primary, *markdown_entries])
            if not page_markdown:
                page_markdown = _layout_text(raw_page.get("layout"), document_text) or ""

            page_languages = _detected_languages(raw_page.get("detectedLanguages"))
            handwriting = any(element.metadata.get("handwriting") is True for element in elements)
            pages.append(
                Page(
                    id=f"page-{page_number}",
                    number=page_number,
                    width=width,
                    height=height,
                    rotation=_orientation_rotation(_page_orientation(raw_page)),
                    elements=elements,
                    source_type=SourceType.IMAGE,
                    metadata={
                        "provider": self.name,
                        "markdown": page_markdown,
                        "dimension_unit": unit,
                        "detected_languages": page_languages,
                        "handwriting": handwriting,
                        "image_quality_scores": _safe_google_raw(
                            raw_page.get("imageQualityScores")
                        ),
                        "raw": _safe_page_raw(raw_page),
                    },
                )
            )

        content_markdown = "\n\n---\n\n".join(
            markdown
            for page in pages
            if (markdown := page.metadata.get("markdown")) and isinstance(markdown, str)
        )
        source = _safe_source_label(context.source) if context and context.source else None
        metadata: dict[str, Any] = {
            "provider": self.name,
            "mime_type": root.get("mimeType"),
            "docid": root.get("docid"),
            "content_text": _redact_embedded_urls(document_text),
            "content_markdown": content_markdown,
        }
        if response_metadata:
            metadata["process_response"] = response_metadata
        return Document(
            id=document_id(self.name, context),
            pages=pages,
            source=source,
            metadata=metadata,
        )


def _required_setting(
    context: ProviderContext | None,
    *,
    option_name: str,
    environment_names: tuple[str, ...],
) -> str:
    value = option_or_environment(context, option_name, environment_names)
    if value is None:
        names = ", ".join(environment_names)
        raise ProviderInputError(
            f"google_document_ai requires ProviderContext option {option_name!r} "
            f"or environment variable {names}"
        )
    return value


def _validate_segment(value: str, *, label: str) -> None:
    if not _RESOURCE_SEGMENT.fullmatch(value):
        raise ProviderInputError(f"Google Document AI {label} is not a valid resource segment")


def _validate_gcs_uri(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme.casefold() != "gs" or not parsed.netloc or not parsed.path.strip("/"):
        raise ProviderInputError(
            "Google Document AI Cloud Storage source must be gs://bucket/object"
        )
    if parsed.query or parsed.fragment or parsed.username or parsed.password:
        raise ProviderInputError(
            "Google Document AI Cloud Storage URI must not contain credentials"
        )
    return value


def _copy_process_request_options(
    request_payload: dict[str, Any],
    options: Mapping[str, Any],
) -> None:
    process_options = options.get("process_options")
    if process_options is not None:
        if not isinstance(process_options, Mapping):
            raise ProviderInputError("Google Document AI process_options must be a mapping")
        request_payload["processOptions"] = dict(process_options)
    field_mask = options.get("field_mask")
    if field_mask is not None:
        if not isinstance(field_mask, str) or not field_mask.strip():
            raise ProviderInputError("Google Document AI field_mask must be a non-blank string")
        request_payload["fieldMask"] = field_mask.strip()
    if "imageless_mode" in options:
        imageless_mode = options["imageless_mode"]
        if not isinstance(imageless_mode, bool):
            raise ProviderInputError("Google Document AI imageless_mode must be Boolean")
        request_payload["imagelessMode"] = imageless_mode
    labels = options.get("labels")
    if labels is not None:
        if not isinstance(labels, Mapping):
            raise ProviderInputError("Google Document AI labels must be a mapping")
        request_payload["labels"] = {str(key): str(value) for key, value in labels.items()}


def _google_request_id(headers: Mapping[str, str]) -> str | None:
    wanted = {"x-goog-request-id", "x-request-id", "request-id"}
    for key, value in headers.items():
        if str(key).casefold() in wanted:
            return str(value)
    return None


def _google_document(payload: Any) -> tuple[Mapping[str, Any], dict[str, Any]]:
    if not isinstance(payload, Mapping):
        raise ProviderInputError("Google Document AI saved response must be a JSON object")
    error = payload.get("error")
    if isinstance(error, Mapping) and error:
        message = error.get("message")
        raise ProviderInputError(
            f"Google Document AI saved response contains an error: {str(message or 'unknown')}"
        )
    document = payload.get("document")
    if isinstance(document, Mapping):
        metadata = {"human_review_status": _safe_google_raw(payload.get("humanReviewStatus"))}
        return document, metadata
    for key in ("result", "response", "data"):
        nested = payload.get(key)
        if isinstance(nested, Mapping):
            nested_document = nested.get("document")
            if isinstance(nested_document, Mapping):
                return nested_document, {
                    "human_review_status": _safe_google_raw(nested.get("humanReviewStatus"))
                }
    if "pages" in payload or "text" in payload:
        return payload, {}
    raise ProviderInputError(
        "Google Document AI response must be a Document or ProcessResponse.document"
    )


def _mapping_list(value: Any) -> list[Mapping[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, _MAPPING_SEQUENCE_EXCLUSIONS):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _positive_integer(value: Any) -> int | None:
    number = as_float(value)
    if number is None or number < 1:
        return None
    return int(number)


def _page_dimensions(page: Mapping[str, Any]) -> tuple[float, float, str | None]:
    image = page.get("image")
    width = height = None
    if isinstance(image, Mapping):
        width = as_float(image.get("width"))
        height = as_float(image.get("height"))
    dimension = page.get("dimension")
    unit: str | None = None
    if isinstance(dimension, Mapping):
        width = width or as_float(dimension.get("width"))
        height = height or as_float(dimension.get("height"))
        raw_unit = dimension.get("unit")
        unit = str(raw_unit) if raw_unit is not None else None
    if not width or width <= 0 or not height or height <= 0:
        max_x, max_y = _maximum_page_vertices(page)
        width = width if width and width > 0 else max_x
        height = height if height and height > 0 else max_y
    return float(width or 1.0), float(height or 1.0), unit


def _maximum_page_vertices(page: Mapping[str, Any]) -> tuple[float, float]:
    max_x = max_y = 0.0
    for collection_name in ("blocks", "paragraphs", "lines", "tokens", "tables"):
        for record in _mapping_list(page.get(collection_name)):
            layout = record.get("layout")
            for point in _raw_vertices(layout, normalized=False):
                max_x = max(max_x, point[0])
                max_y = max(max_y, point[1])
    return max_x, max_y


def _raw_vertices(value: Any, *, normalized: bool) -> list[tuple[float, float]]:
    if not isinstance(value, Mapping):
        return []
    bounding_poly = value.get("boundingPoly")
    if not isinstance(bounding_poly, Mapping):
        return []
    key = "normalizedVertices" if normalized else "vertices"
    points: list[tuple[float, float]] = []
    for vertex in _mapping_list(bounding_poly.get(key)):
        x = as_float(vertex.get("x", 0))
        y = as_float(vertex.get("y", 0))
        if x is not None and y is not None:
            points.append((x, y))
    return points


def _layout_geometry(
    layout: Any,
    page_width: float,
    page_height: float,
) -> tuple[BBox, list[Point], str]:
    points = _raw_vertices(layout, normalized=False)
    coordinate_system = "source_pixels"
    if not points:
        points = [
            (x * page_width, y * page_height) for x, y in _raw_vertices(layout, normalized=True)
        ]
        coordinate_system = "normalized_scaled"
    if not points:
        return (
            BBox(x0=0, y0=0, x1=page_width, y1=page_height),
            [],
            "full_page_fallback",
        )
    polygon = [Point(x=x, y=y) for x, y in points]
    return (
        BBox(
            x0=min(point[0] for point in points),
            y0=min(point[1] for point in points),
            x1=max(point[0] for point in points),
            y1=max(point[1] for point in points),
        ),
        polygon,
        coordinate_system,
    )


def _layout_text(layout: Any, document_text: str) -> str | None:
    if not isinstance(layout, Mapping):
        return None
    anchor = layout.get("textAnchor")
    if not isinstance(anchor, Mapping):
        return None
    direct = anchor.get("content")
    if isinstance(direct, str):
        return _redact_embedded_urls(direct)
    pieces: list[str] = []
    for segment in _mapping_list(anchor.get("textSegments")):
        start = _nonnegative_integer(segment.get("startIndex"), default=0)
        end = _nonnegative_integer(segment.get("endIndex"), default=start)
        if end > start and start < len(document_text):
            pieces.append(document_text[start : min(end, len(document_text))])
    combined = "".join(pieces)
    return _redact_embedded_urls(combined) if combined else None


def _layout_start(layout: Any) -> int:
    if not isinstance(layout, Mapping):
        return 2**63 - 1
    anchor = layout.get("textAnchor")
    if not isinstance(anchor, Mapping):
        return 2**63 - 1
    segments = _mapping_list(anchor.get("textSegments"))
    if not segments:
        return 2**63 - 1
    return min(_nonnegative_integer(segment.get("startIndex"), default=0) for segment in segments)


def _nonnegative_integer(value: Any, *, default: int) -> int:
    number = as_float(value)
    if number is None:
        return default
    return max(0, int(number))


def _layout_element(
    *,
    provider: str,
    page_number: int,
    page_index: int,
    record: Mapping[str, Any],
    record_kind: str,
    item_index: int,
    kind: ElementType,
    document_text: str,
    page_width: float,
    page_height: float,
) -> Element:
    layout = record.get("layout")
    bbox, polygon, coordinate_system = _layout_geometry(layout, page_width, page_height)
    text = _layout_text(layout, document_text)
    score = _layout_confidence(layout)
    languages = _detected_languages(record.get("detectedLanguages"))
    style = ElementStyle()
    metadata: dict[str, Any] = {
        "record_kind": record_kind,
        "detected_languages": languages,
        "coordinate_system": coordinate_system,
        "raw": _safe_google_raw(record),
    }
    if record_kind == "token":
        style_info = record.get("styleInfo")
        style = _token_style(style_info)
        metadata["detected_break"] = _safe_google_raw(record.get("detectedBreak"))
        metadata["style_info"] = _safe_google_raw(style_info)
        if isinstance(style_info, Mapping) and style_info.get("handwritten") is True:
            metadata["handwriting"] = True
    source_id = f"/document/pages/{page_index}/{record_kind}s/{item_index}"
    return _element(
        provider=provider,
        element_id=f"page-{page_number}-{record_kind}-{item_index + 1}",
        source_id=source_id,
        kind=kind,
        bbox=bbox,
        polygon=polygon,
        text=text,
        score=score,
        style=style,
        metadata=metadata,
    )


def _table_element(
    *,
    provider: str,
    page_number: int,
    page_index: int,
    record: Mapping[str, Any],
    table_index: int,
    document_text: str,
    page_width: float,
    page_height: float,
) -> Element:
    layout = record.get("layout")
    bbox, polygon, coordinate_system = _layout_geometry(layout, page_width, page_height)
    header_rows = _table_rows(record.get("headerRows"), document_text)
    body_rows = _table_rows(record.get("bodyRows"), document_text)
    rows = [*header_rows, *body_rows]
    markdown = _table_markdown(rows, header_rows=len(header_rows))
    return _element(
        provider=provider,
        element_id=f"page-{page_number}-table-{table_index + 1}",
        source_id=f"/document/pages/{page_index}/tables/{table_index}",
        kind=ElementType.TABLE,
        bbox=bbox,
        polygon=polygon,
        text=_layout_text(layout, document_text),
        score=_layout_confidence(layout),
        style=ElementStyle(),
        metadata={
            "record_kind": "table",
            "rows": rows,
            "header_row_count": len(header_rows),
            "markdown": markdown,
            "detected_languages": _detected_languages(record.get("detectedLanguages")),
            "coordinate_system": coordinate_system,
            "raw": _safe_google_raw(record),
        },
    )


def _table_rows(value: Any, document_text: str) -> list[list[str]]:
    rows: list[list[str]] = []
    for row in _mapping_list(value):
        cells: list[str] = []
        for cell in _mapping_list(row.get("cells")):
            cells.append(_layout_text(cell.get("layout"), document_text) or "")
        rows.append(cells)
    return rows


def _table_markdown(rows: Sequence[Sequence[str]], *, header_rows: int) -> str:
    if not rows:
        return ""
    width = max((len(row) for row in rows), default=0)
    if width == 0:
        return ""

    def render(row: Sequence[str]) -> str:
        values = [str(value).replace("|", "\\|").replace("\n", " ").strip() for value in row]
        values.extend("" for _ in range(width - len(values)))
        return "| " + " | ".join(values) + " |"

    lines = [render(rows[0])]
    lines.append("| " + " | ".join("---" for _ in range(width)) + " |")
    lines.extend(render(row) for row in rows[1:])
    if header_rows > 1:
        return "\n".join(lines[:2] + [render(row) for row in rows[1:]])
    return "\n".join(lines)


def _form_field_element(
    *,
    provider: str,
    page_number: int,
    page_index: int,
    record: Mapping[str, Any],
    field_index: int,
    document_text: str,
    page_width: float,
    page_height: float,
) -> Element:
    name_layout = record.get("fieldName")
    value_layout = record.get("fieldValue")
    name = record.get("correctedKeyText")
    if not isinstance(name, str) or not name:
        name = _layout_text(name_layout, document_text) or ""
    value = record.get("correctedValueText")
    if not isinstance(value, str) or not value:
        value = _layout_text(value_layout, document_text) or ""
    value_type = str(record.get("valueType") or "")
    selected: bool | None = None
    kind = ElementType.TEXT
    if value_type in {"filled_checkbox", "unfilled_checkbox"}:
        selected = value_type == "filled_checkbox"
        kind = ElementType.CHECKBOX
        value = "☒" if selected else "☐"
    name_bbox, name_polygon, name_coordinate_system = _layout_geometry(
        name_layout, page_width, page_height
    )
    value_bbox, value_polygon, value_coordinate_system = _layout_geometry(
        value_layout, page_width, page_height
    )
    bbox = _union_bbox(name_bbox, value_bbox)
    polygon = [*name_polygon, *value_polygon]
    name_score = _layout_confidence(name_layout)
    value_score = _layout_confidence(value_layout)
    available_scores = [score for score in (name_score, value_score) if score is not None]
    score = min(available_scores) if available_scores else None
    text = f"**{name.strip()}:** {value.strip()}" if name else value
    metadata: dict[str, Any] = {
        "record_kind": "form_field",
        "field_name": name,
        "field_value": value,
        "value_type": value_type,
        "name_confidence": name_score,
        "value_confidence": value_score,
        "name_detected_languages": _detected_languages(record.get("nameDetectedLanguages")),
        "value_detected_languages": _detected_languages(record.get("valueDetectedLanguages")),
        "coordinate_system": {
            "field_name": name_coordinate_system,
            "field_value": value_coordinate_system,
        },
        "raw": _safe_google_raw(record),
    }
    if selected is not None:
        metadata["selected"] = selected
    return _element(
        provider=provider,
        element_id=f"page-{page_number}-form-field-{field_index + 1}",
        source_id=f"/document/pages/{page_index}/formFields/{field_index}",
        kind=kind,
        bbox=bbox,
        polygon=polygon,
        text=text or None,
        score=score,
        style=ElementStyle(),
        metadata=metadata,
    )


def _union_bbox(left: BBox, right: BBox) -> BBox:
    return BBox(
        x0=min(left.x0, right.x0),
        y0=min(left.y0, right.y0),
        x1=max(left.x1, right.x1),
        y1=max(left.y1, right.y1),
    )


def _layout_confidence(layout: Any) -> float | None:
    return confidence(layout.get("confidence")) if isinstance(layout, Mapping) else None


def _detected_languages(value: Any) -> list[dict[str, Any]]:
    languages: list[dict[str, Any]] = []
    for language in _mapping_list(value):
        code = language.get("languageCode")
        if not isinstance(code, str) or not code:
            continue
        languages.append(
            {
                "language_code": code,
                "confidence": confidence(language.get("confidence")),
            }
        )
    return languages


def _token_style(value: Any) -> ElementStyle:
    if not isinstance(value, Mapping):
        return ElementStyle()
    font_size = as_float(value.get("fontSize")) or as_float(value.get("pixelFontSize"))
    if font_size is not None and font_size <= 0:
        font_size = None
    font_weight_value = as_float(value.get("fontWeight"))
    font_weight = int(font_weight_value) if font_weight_value is not None else None
    if font_weight is None and value.get("bold") is True:
        font_weight = 700
    if font_weight is not None:
        font_weight = max(1, min(1000, font_weight))
    font_type = value.get("fontType")
    return ElementStyle(
        font_family=str(font_type) if isinstance(font_type, str) and font_type else None,
        font_size=font_size,
        font_weight=font_weight,
        italic=value.get("italic") if isinstance(value.get("italic"), bool) else None,
        underline=(value.get("underlined") if isinstance(value.get("underlined"), bool) else None),
        color=_color(value.get("textColor")),
        background_color=_color(value.get("backgroundColor")),
    )


def _color(value: Any) -> str | None:
    if not isinstance(value, Mapping):
        return None
    components: list[int] = []
    for key in ("red", "green", "blue"):
        component = as_float(value.get(key, 0))
        if component is None:
            return None
        components.append(round(max(0.0, min(1.0, component)) * 255))
    alpha = as_float(value.get("alpha", 1))
    if alpha is None:
        alpha = 1.0
    if alpha < 1:
        components.append(round(max(0.0, min(1.0, alpha)) * 255))
    return "#" + "".join(f"{component:02X}" for component in components)


def _element(
    *,
    provider: str,
    element_id: str,
    source_id: str,
    kind: ElementType,
    bbox: BBox,
    polygon: list[Point],
    text: str | None,
    score: float | None,
    style: ElementStyle,
    metadata: dict[str, Any],
) -> Element:
    return Element(
        id=element_id,
        type=kind,
        bbox=bbox,
        polygon=polygon,
        text=text,
        confidence=score,
        style=style,
        provenance=Provenance(
            engine=provider,
            source_id=source_id,
            text_confidence=score,
            layout_confidence=score,
            metadata={"record_kind": metadata.get("record_kind")},
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


def _markdown_from_entries(entries: Sequence[tuple[int, int, str]]) -> str:
    ordered = sorted(entries, key=lambda entry: (entry[0], entry[1]))
    values: list[str] = []
    for _, _, raw_value in ordered:
        value = raw_value.strip()
        if value and (not values or values[-1] != value):
            values.append(value)
    return "\n\n".join(values)


def _page_orientation(page: Mapping[str, Any]) -> str | None:
    layout = page.get("layout")
    orientation = layout.get("orientation") if isinstance(layout, Mapping) else None
    return str(orientation) if orientation is not None else None


def _orientation_rotation(value: str | None) -> float:
    return {
        "PAGE_UP": 0.0,
        "PAGE_RIGHT": 90.0,
        "PAGE_DOWN": 180.0,
        "PAGE_LEFT": -90.0,
    }.get(str(value or "").upper(), 0.0)


def _safe_source_label(value: str) -> str:
    if value.casefold().startswith(("https://", "http://")):
        return redacted_url_label(value)
    return value


def _safe_page_raw(page: Mapping[str, Any]) -> dict[str, Any]:
    redacted = _safe_google_raw(page)
    if not isinstance(redacted, Mapping):
        return {}
    return {
        str(key): value
        for key, value in redacted.items()
        if key not in {"blocks", "paragraphs", "lines", "tokens", "tables", "formFields"}
    }


def _safe_google_raw(value: Any) -> Any:
    """Apply hosted redaction and omit Document AI image/matrix byte payloads."""

    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        mime_type = value.get("mimeType")
        for key, nested in value.items():
            normalized = str(key).casefold().replace("-", "_")
            if any(
                marker in normalized
                for marker in (
                    "api_key",
                    "authorization",
                    "bearer",
                    "credential",
                    "password",
                    "private_key",
                    "secret",
                    "token",
                )
            ):
                continue
            binary_content = (
                normalized == "content" and isinstance(mime_type, str) and isinstance(nested, str)
            )
            matrix_data = (
                normalized == "data"
                and isinstance(nested, str)
                and {"rows", "cols", "type"}.issubset(value)
            )
            if binary_content or matrix_data:
                result[str(key)] = {
                    "omitted": True,
                    "characters": len(nested),
                    "sha256": hashlib.sha256(nested.encode("utf-8")).hexdigest(),
                }
            else:
                result[str(key)] = _safe_google_raw(nested)
        return safe_raw(result)
    if isinstance(value, Sequence) and not isinstance(value, _MAPPING_SEQUENCE_EXCLUSIONS):
        return [_safe_google_raw(item) for item in value]
    if isinstance(value, str):
        return safe_raw(_redact_embedded_urls(value))
    return safe_raw(value)


def _redact_embedded_urls(value: str) -> str:
    def replace(match: re.Match[str]) -> str:
        candidate = match.group(0)
        trailing = ""
        while (
            candidate
            and candidate[-1] in ").,;:!?]}"
            and candidate.count("(") < candidate.count(")")
        ):
            trailing = candidate[-1] + trailing
            candidate = candidate[:-1]
        return redacted_url_label(candidate) + trailing

    return _URL_PATTERN.sub(replace, value)


GoogleDocumentAiProvider = GoogleDocumentAIProvider
GoogleCloudDocumentAIProvider = GoogleDocumentAIProvider


__all__ = [
    "GoogleCloudDocumentAIProvider",
    "GoogleDocumentAIProvider",
    "GoogleDocumentAiProvider",
]
