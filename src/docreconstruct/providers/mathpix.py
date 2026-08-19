"""Saved-response and opt-in hosted adapter for the official Mathpix OCR API."""

from __future__ import annotations

import base64
import hashlib
import json
import re
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

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
    HostedSource,
    HTTPResponse,
    HTTPTransport,
    ProviderHTTPError,
    context_options,
    decode_json_response,
    load_hosted_source,
    numeric_option,
    redacted_url_label,
    require_credential,
    require_remote_opt_in,
    response_header,
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
    looks_like_inline_json,
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

_IMAGE_EXTENSIONS = {".bmp", ".gif", ".jpeg", ".jpg", ".png", ".tif", ".tiff", ".webp"}
_URL_PATTERN = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
_MARKDOWN_IMAGE_PATTERN = re.compile(r"!\[[^\]]*\]\((https?://[^\s)]+)", re.IGNORECASE)

_SHARED_REQUEST_OPTIONS = (
    "alphabets_allowed",
    "disable_itemize",
    "disable_lstlisting",
    "enable_tables_fallback",
    "fullwidth_punctuation",
    "idiomatic_eqn_arrays",
    "include_equation_tags",
    "include_page_info",
    "include_smiles",
    "math_display_delimiters",
    "math_inline_delimiters",
    "rm_fonts",
    "rm_spaces",
)


class MathpixProvider(SavedJSONProvider):
    """Normalize Mathpix JSON or call its image/PDF APIs after explicit consent."""

    name = "mathpix"
    _capabilities = ProviderCapabilities(
        provider=name,
        supported_inputs=["pdf", "png", "jpeg", "jpg", "webp", "tiff", "json"],
        saved_json=True,
        live_inference=True,
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
        dewarping=False,
        execution_modes=[ProviderExecutionMode.SAVED, ProviderExecutionMode.API],
        markdown=True,
        bounding_boxes=True,
        confidence_scores=True,
        privacy=ProviderPrivacy.THIRD_PARTY,
        license=ProviderLicense(
            name="Mathpix hosted OCR service",
            open_source=False,
            commercial_use=True,
        ),
        model_name="Mathpix OCR",
        cost=ProviderCost.METERED,
        credentials=ProviderCredentialRequirement.REQUIRED,
        credential_env_vars=["MATHPIX_APP_ID", "MATHPIX_APP_KEY"],
        notes=[
            "Hosted inference is disabled unless ProviderContext.options.allow_remote is true.",
            "Image requests use POST /v3/text; PDFs use POST /v3/pdf and status polling.",
            "metadata.improve_mathpix defaults to false to reduce provider-side retention.",
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
        app_id = require_credential(
            context,
            provider=self.name,
            option_name="app_id",
            environment_names=("MATHPIX_APP_ID",),
        )
        app_key = require_credential(
            context,
            provider=self.name,
            option_name="app_key",
            environment_names=("MATHPIX_APP_KEY",),
        )
        maximum_megabytes = numeric_option(
            context,
            "max_upload_mb",
            50.0,
            minimum=0.1,
            maximum=1024.0,
        )
        hosted_source = load_hosted_source(
            source,
            context=context,
            maximum_megabytes=maximum_megabytes,
        )
        is_pdf = _is_pdf_source(source, hosted_source.media_type, context)
        if not is_pdf and not _is_image_source(source, hosted_source.media_type, context):
            raise ProviderInputError("Mathpix live inference accepts PDF or image input")
        if is_pdf:
            return self._infer_pdf(
                hosted_source=hosted_source,
                app_id=app_id,
                app_key=app_key,
                context=context,
            )
        return self._infer_image(
            hosted_source=hosted_source,
            app_id=app_id,
            app_key=app_key,
            context=context,
        )

    def _infer_image(
        self,
        *,
        hosted_source: HostedSource,
        app_id: str,
        app_key: str,
        context: ProviderContext | None,
    ) -> ProviderResult:
        options = context_options(context)
        endpoint = validate_service_endpoint(
            str(options.get("text_endpoint", "https://api.mathpix.com/v3/text")),
            label="Mathpix image endpoint",
            allowed_hosts=("api.mathpix.com",),
            allow_custom_endpoint=options.get("allow_custom_endpoint") is True,
        )
        if hosted_source.url is not None:
            source_value = hosted_source.url
        else:
            assert hosted_source.data is not None
            source_value = (
                f"data:{hosted_source.media_type};base64,"
                f"{base64.b64encode(hosted_source.data).decode('ascii')}"
            )
            if len(source_value.encode("ascii")) > 2 * 1024 * 1024:
                raise ProviderInputError(
                    "Mathpix /v3/text base64 input exceeds the official 2 MB request limit"
                )
        request_payload: dict[str, Any] = {
            "src": source_value,
            "formats": ["text", "data"],
            "data_options": {
                "include_latex": True,
                "include_table_html": True,
                "include_tsv": True,
            },
            "enable_document_layout": bool(options.get("enable_document_layout", True)),
            "include_line_data": True,
            "metadata": {"improve_mathpix": bool(options.get("improve_mathpix", False))},
        }
        _copy_request_options(request_payload, options)
        timeout = numeric_option(context, "timeout_seconds", 30.0, minimum=1.0, maximum=600.0)
        response = self._transport(
            method="POST",
            url=endpoint,
            headers=_mathpix_headers(app_id, app_key, json_content=True),
            body=json.dumps(request_payload, ensure_ascii=False).encode("utf-8"),
            timeout=timeout,
        )
        payload = decode_json_response(response, provider=self.name)
        effective_context = _source_context(context, hosted_source.label)
        document = self.normalize(payload, context=effective_context)
        root = payload if isinstance(payload, Mapping) else {}
        return ProviderResult(
            provider=self.name,
            document=document,
            metadata={
                "hosted": True,
                "request_id": response_request_id(response)
                or _nonempty_string(root.get("request_id")),
                "model_version": _nonempty_string(root.get("version")),
                "improve_mathpix": bool(options.get("improve_mathpix", False)),
            },
        )

    def _infer_pdf(
        self,
        *,
        hosted_source: HostedSource,
        app_id: str,
        app_key: str,
        context: ProviderContext | None,
    ) -> ProviderResult:
        options = context_options(context)
        endpoint = validate_service_endpoint(
            str(options.get("pdf_endpoint", "https://api.mathpix.com/v3/pdf")),
            label="Mathpix PDF endpoint",
            allowed_hosts=("api.mathpix.com",),
            allow_custom_endpoint=options.get("allow_custom_endpoint") is True,
        ).rstrip("/")
        request_options: dict[str, Any] = {
            "metadata": {"improve_mathpix": bool(options.get("improve_mathpix", False))}
        }
        _copy_request_options(request_options, options)
        if "include_page_info" not in request_options:
            request_options["include_page_info"] = True
        if hosted_source.url is not None:
            request_payload = {"url": hosted_source.url, **request_options}
            body = json.dumps(request_payload, ensure_ascii=False).encode("utf-8")
            headers = _mathpix_headers(app_id, app_key, json_content=True)
        else:
            assert hosted_source.data is not None
            body, content_type = _multipart_pdf(hosted_source.data, request_options)
            headers = _mathpix_headers(app_id, app_key, content_type=content_type)
        timeout = numeric_option(context, "timeout_seconds", 120.0, minimum=1.0, maximum=600.0)
        submitted = self._transport(
            method="POST",
            url=endpoint,
            headers=headers,
            body=body,
            timeout=timeout,
        )
        submitted_payload = decode_json_response(
            submitted,
            provider=self.name,
            allowed_statuses=(200, 201, 202),
        )
        if not isinstance(submitted_payload, Mapping):
            raise ProviderInputError("Mathpix PDF submission response must be a JSON object")
        pdf_id = _nonempty_string(submitted_payload.get("pdf_id"))
        if pdf_id is None:
            raise ProviderInputError("Mathpix PDF submission response contains no pdf_id")
        escaped_pdf_id = quote(pdf_id, safe="")
        status_payload, completed = self._poll_pdf(
            f"{endpoint}/{escaped_pdf_id}",
            app_id=app_id,
            app_key=app_key,
            context=context,
            timeout=timeout,
        )
        lines_response = self._transport(
            method="GET",
            url=f"{endpoint}/{escaped_pdf_id}.lines.json",
            headers=_mathpix_headers(app_id, app_key),
            body=None,
            timeout=timeout,
        )
        lines_payload = decode_json_response(lines_response, provider=self.name)
        if not isinstance(lines_payload, Mapping):
            raise ProviderInputError("Mathpix PDF lines response must be a JSON object")
        mmd_response = self._transport(
            method="GET",
            url=f"{endpoint}/{escaped_pdf_id}.mmd",
            headers=_mathpix_headers(app_id, app_key, accept="text/plain"),
            body=None,
            timeout=timeout,
        )
        mmd = _decode_text_response(mmd_response, provider=self.name)
        combined: dict[str, Any] = dict(lines_payload)
        combined["mmd"] = mmd
        combined["pdf_id"] = pdf_id
        combined["status_response"] = status_payload
        if "version" not in combined and isinstance(status_payload.get("version"), str):
            combined["version"] = status_payload["version"]
        effective_context = _source_context(context, hosted_source.label)
        document = self.normalize(combined, context=effective_context)
        return ProviderResult(
            provider=self.name,
            document=document,
            metadata={
                "hosted": True,
                "pdf_id": pdf_id,
                "request_id": response_request_id(lines_response)
                or response_request_id(completed)
                or response_request_id(submitted),
                "model_version": _nonempty_string(combined.get("version")),
                "improve_mathpix": bool(options.get("improve_mathpix", False)),
            },
        )

    def _poll_pdf(
        self,
        status_url: str,
        *,
        app_id: str,
        app_key: str,
        context: ProviderContext | None,
        timeout: float,
    ) -> tuple[Mapping[str, Any], HTTPResponse]:
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
                url=status_url,
                headers=_mathpix_headers(app_id, app_key),
                body=None,
                timeout=timeout,
            )
            payload = decode_json_response(response, provider=self.name)
            if not isinstance(payload, Mapping):
                raise ProviderInputError("Mathpix PDF status response must be a JSON object")
            status = str(payload.get("status", "")).casefold()
            if status == "completed":
                return payload, response
            if status in {"error", "failed", "canceled", "cancelled"}:
                detail = payload.get("error") or payload.get("error_info") or "processing failed"
                raise ProviderHTTPError(f"Mathpix PDF {status}: {safe_raw(detail)}")
            if attempt + 1 < attempts:
                retry_after = response_header(response, "Retry-After")
                try:
                    delay = float(retry_after) if retry_after is not None else interval
                except ValueError:
                    delay = interval
                if delay > 0:
                    self._sleep(min(30.0, delay))
        raise ProviderHTTPError(f"Mathpix PDF did not finish after {attempts} polls")

    def normalize(
        self,
        payload: Any,
        *,
        context: ProviderContext | None = None,
    ) -> Document:
        root = _mathpix_root(payload)
        if not _has_ocr_content(root):
            raise ProviderInputError("Mathpix saved response contains no OCR content")
        page_payloads = _mathpix_pages(root)
        pages: list[Page] = []
        used_numbers: set[int] = set()
        for index, page_payload in enumerate(page_payloads):
            number = _mathpix_page_number(page_payload, index)
            while number in used_numbers:
                number += 1
            used_numbers.add(number)
            lines = _mathpix_lines(page_payload)
            width, height = _mathpix_dimensions(page_payload, lines, context)
            page_score = _mathpix_confidence(page_payload) or _mathpix_confidence(root)
            version = (
                _nonempty_string(page_payload.get("version"))
                or _nonempty_string(root.get("version"))
                or "Mathpix OCR"
            )
            elements: list[Element] = []
            for line_index, line in enumerate(lines):
                element = _mathpix_line_element(
                    provider=self.name,
                    page_number=number,
                    line_index=line_index,
                    line=line,
                    page_width=width,
                    page_height=height,
                    version=version,
                )
                if element is not None:
                    elements.append(element)

            page_markdown = _page_markdown(page_payload, lines)
            if not elements:
                fallback = _fallback_element(
                    provider=self.name,
                    page_number=number,
                    root=page_payload,
                    width=width,
                    height=height,
                    score=page_score,
                    version=version,
                )
                if fallback is not None:
                    elements.append(fallback)
            pages.append(
                Page(
                    id=f"page-{number}",
                    number=number,
                    width=width,
                    height=height,
                    rotation=as_float(page_payload.get("auto_rotate_degrees")) or 0.0,
                    elements=elements,
                    source_type=(
                        SourceType.IMAGE
                        if "pages" not in root and len(page_payloads) == 1
                        else SourceType.UNKNOWN
                    ),
                    metadata={
                        "provider": self.name,
                        "markdown": page_markdown,
                        "confidence_rate": confidence(page_payload.get("confidence_rate")),
                        "is_printed": page_payload.get("is_printed"),
                        "is_handwritten": page_payload.get("is_handwritten"),
                        "raw": _safe_mathpix_raw(page_payload),
                    },
                )
            )
        content_markdown = _document_markdown(root, pages)
        root_version = _nonempty_string(root.get("version"))
        return Document(
            id=document_id(self.name, context),
            pages=pages,
            source=context.source if context else None,
            metadata={
                "provider": self.name,
                "model": "Mathpix OCR",
                "model_version": root_version,
                "content_markdown": content_markdown,
                "pdf_id": _nonempty_string(root.get("pdf_id")),
                "request_id": _nonempty_string(root.get("request_id")),
                "raw": _safe_mathpix_raw(root),
            },
        )


def _mathpix_headers(
    app_id: str,
    app_key: str,
    *,
    json_content: bool = False,
    content_type: str | None = None,
    accept: str = "application/json",
) -> dict[str, str]:
    headers = {
        "Accept": accept,
        "User-Agent": "docreconstruct/0.1",
        "app_id": app_id,
        "app_key": app_key,
    }
    if json_content:
        headers["Content-Type"] = "application/json"
    elif content_type is not None:
        headers["Content-Type"] = content_type
    return headers


def _source_context(context: ProviderContext | None, label: str) -> ProviderContext:
    effective = context or ProviderContext()
    if effective.source is None:
        effective = effective.model_copy(update={"source": label})
    return effective


def _copy_request_options(target: dict[str, Any], options: Mapping[str, Any]) -> None:
    for name in _SHARED_REQUEST_OPTIONS:
        if name in options:
            target[name] = options[name]


def _is_pdf_source(
    source: str | bytes | bytearray | Path,
    media_type: str,
    context: ProviderContext | None,
) -> bool:
    kind = str(context_options(context).get("input_kind", "")).casefold()
    if kind:
        return kind == "pdf"
    if isinstance(source, (bytes, bytearray)):
        return bytes(source).lstrip().startswith(b"%PDF")
    if media_type == "application/pdf":
        return True
    if isinstance(source, str) and source.lower().startswith(("https://", "http://")):
        return Path(urlparse(source).path).suffix.casefold() == ".pdf"
    return Path(source).suffix.casefold() == ".pdf"


def _is_image_source(
    source: str | bytes | bytearray | Path,
    media_type: str,
    context: ProviderContext | None,
) -> bool:
    kind = str(context_options(context).get("input_kind", "")).casefold()
    if kind:
        return kind == "image"
    if isinstance(source, (bytes, bytearray)):
        data = bytes(source)
        return media_type.startswith("image/") or _looks_like_image_bytes(data)
    if media_type.startswith("image/"):
        return True
    if isinstance(source, str) and source.lower().startswith(("https://", "http://")):
        suffix = Path(urlparse(source).path).suffix.casefold()
    else:
        suffix = Path(source).suffix.casefold()
    return suffix in _IMAGE_EXTENSIONS


def _looks_like_image_bytes(data: bytes) -> bool:
    return (
        data.startswith(b"\x89PNG\r\n\x1a\n")
        or data.startswith(b"\xff\xd8\xff")
        or data.startswith((b"GIF87a", b"GIF89a", b"BM", b"II*\x00", b"MM\x00*"))
        or (len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP")
    )


def _multipart_pdf(data: bytes, options: Mapping[str, Any]) -> tuple[bytes, str]:
    boundary = f"docreconstruct-mathpix-{hashlib.sha256(data).hexdigest()[:24]}"
    delimiter = f"--{boundary}\r\n".encode("ascii")
    ending = f"--{boundary}--\r\n".encode("ascii")
    body = b"".join(
        (
            delimiter,
            b'Content-Disposition: form-data; name="file"; filename="document.pdf"\r\n',
            b"Content-Type: application/pdf\r\n\r\n",
            data,
            b"\r\n",
            delimiter,
            b'Content-Disposition: form-data; name="options_json"\r\n',
            b"Content-Type: application/json; charset=utf-8\r\n\r\n",
            json.dumps(options, ensure_ascii=False).encode("utf-8"),
            b"\r\n",
            ending,
        )
    )
    return body, f"multipart/form-data; boundary={boundary}"


def _decode_text_response(response: HTTPResponse, *, provider: str) -> str:
    if response.status != 200:
        decode_json_response(response, provider=provider)
        raise AssertionError("unreachable")
    try:
        return response.body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ProviderHTTPError(f"{provider} returned non-UTF-8 MMD data") from exc


def _mathpix_root(payload: Any) -> Mapping[str, Any]:
    if not isinstance(payload, Mapping):
        raise ProviderInputError("Mathpix saved response must be a JSON object")
    current: Mapping[str, Any] = payload
    for key in ("response", "result"):
        nested = current.get(key)
        if isinstance(nested, Mapping) and any(
            marker in nested for marker in ("pages", "line_data", "text", "mmd")
        ):
            current = nested
            break
    if current.get("error") or current.get("error_info"):
        detail = current.get("error") or current.get("error_info")
        raise ProviderInputError(f"Mathpix saved response contains an error: {safe_raw(detail)}")
    if str(current.get("status", "")).casefold() in {"error", "failed"}:
        raise ProviderInputError(f"Mathpix saved response status is {current.get('status')}")
    return current


def _mathpix_pages(root: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    raw_pages = root.get("pages")
    if isinstance(raw_pages, Sequence) and not isinstance(raw_pages, (str, bytes)):
        pages = [page for page in raw_pages if isinstance(page, Mapping)]
        if pages:
            return pages
    return [root]


def _has_ocr_content(root: Mapping[str, Any]) -> bool:
    pages = root.get("pages")
    if isinstance(pages, Sequence) and not isinstance(pages, (str, bytes)) and pages:
        return True
    if _mathpix_lines(root):
        return True
    if any(_nonempty_string(root.get(key)) for key in ("mmd", "markdown", "text")):
        return True
    if _nonempty_string(root.get("latex_styled")):
        return True
    return (
        _mathpix_data_value(root, "latex") is not None
        or _mathpix_data_value(root, "tsv") is not None
    )


def _mathpix_lines(page: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    for key in ("lines", "line_data"):
        value = page.get(key)
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            return [line for line in value if isinstance(line, Mapping)]
    return []


def _mathpix_page_number(page: Mapping[str, Any], fallback_index: int) -> int:
    for key in ("page", "page_number", "page_idx"):
        value = as_float(page.get(key))
        if value is not None:
            return max(1, int(value))
    value = as_float(page.get("page_index"))
    return max(1, int(value) + 1) if value is not None else fallback_index + 1


def _mathpix_dimensions(
    page: Mapping[str, Any],
    lines: Sequence[Mapping[str, Any]],
    context: ProviderContext | None,
) -> tuple[float, float]:
    width = as_float(page.get("page_width")) or as_float(page.get("image_width"))
    height = as_float(page.get("page_height")) or as_float(page.get("image_height"))
    boxes = [box for line in lines if (box := _mathpix_bbox(line)) is not None]
    if width is None and boxes:
        width = max(box.x1 for box in boxes)
    if height is None and boxes:
        height = max(box.y1 for box in boxes)
    if context is not None:
        width = width or context.page_width
        height = height or context.page_height
    return max(1.0, width or 1000.0), max(1.0, height or 1400.0)


def _mathpix_confidence(record: Mapping[str, Any]) -> float | None:
    direct = confidence(record.get("confidence"))
    return direct if direct is not None else confidence(record.get("confidence_rate"))


def _mathpix_line_element(
    *,
    provider: str,
    page_number: int,
    line_index: int,
    line: Mapping[str, Any],
    page_width: float,
    page_height: float,
    version: str,
) -> Element | None:
    kind = _mathpix_element_type(line)
    text = _clean_text(line.get("text_display")) or _clean_text(line.get("text"))
    image_ref = _mathpix_image_ref(line, text)
    if text is None and image_ref is None and kind in {ElementType.TEXT, ElementType.UNKNOWN}:
        return None
    detected_bbox = _mathpix_bbox(line)
    bbox = detected_bbox or BBox(x0=0, y0=0, x1=page_width, y1=page_height)
    score = _mathpix_confidence(line)
    source_id = _nonempty_string(line.get("id")) or f"page-{page_number}-line-{line_index + 1}"
    metadata: dict[str, Any] = {
        "content_format": "mathpix_markdown",
        "line_type": line.get("type"),
        "line_subtype": line.get("subtype"),
        "conversion_output": line.get("conversion_output", line.get("included")),
        "confidence_rate": confidence(line.get("confidence_rate")),
        "is_printed": line.get("is_printed"),
        "is_handwritten": line.get("is_handwritten"),
        "raw": _safe_mathpix_raw(line),
    }
    latex = (
        _mathpix_data_value(line, "latex")
        or _nonempty_string(line.get("latex"))
        or _nonempty_string(line.get("latex_styled"))
    )
    if latex is not None:
        metadata["latex"] = latex
    tsv = _mathpix_data_value(line, "tsv")
    if tsv is not None:
        metadata["tsv"] = tsv
        metadata["rows"] = [row.split("\t") for row in tsv.splitlines()]
    html = _nonempty_string(line.get("html"))
    if html is not None:
        metadata["html"] = _clean_text(html)
    if image_ref is not None:
        metadata["image_ref"] = image_ref
        metadata["image"] = {"src": image_ref}
    if detected_bbox is None:
        metadata["coordinate_system"] = "full_page_fallback"
    return _element(
        provider=provider,
        element_id=f"page-{page_number}-line-{line_index + 1}",
        source_id=source_id,
        kind=kind,
        bbox=bbox,
        polygon=coerce_polygon(line.get("cnt")),
        text=text,
        score=score,
        order=line_index,
        version=version,
        metadata=metadata,
    )


def _mathpix_element_type(record: Mapping[str, Any]) -> ElementType:
    raw_type = str(record.get("type") or "").casefold().replace("-", "_")
    subtype = str(record.get("subtype") or "").casefold().replace("-", "_")
    combined = f"{raw_type}_{subtype}"
    if raw_type in {"math", "equation", "equation_number"}:
        return ElementType.FORMULA
    if raw_type in {"table", "table_cell"}:
        return ElementType.TABLE
    if raw_type in {
        "chart",
        "chart_info",
        "legend_label",
        "model_label",
        "x_axis_label",
        "x_axis_tick_label",
        "y_axis_label",
        "y_axis_tick_label",
    }:
        return ElementType.CHART
    if raw_type == "diagram":
        return ElementType.FIGURE
    if raw_type == "figure_label":
        return ElementType.CAPTION
    if raw_type == "title":
        return ElementType.TITLE
    if raw_type == "section_header":
        return ElementType.HEADING
    if raw_type == "footnote":
        return ElementType.FOOTNOTE
    if raw_type == "page_info":
        return ElementType.HEADER
    if raw_type in {"multiple_choice_option", "table_of_contents_item"}:
        return ElementType.LIST_ITEM
    if raw_type == "form_field" and "checkbox" in combined:
        return ElementType.CHECKBOX
    return ElementType.TEXT


def _mathpix_bbox(record: Mapping[str, Any]) -> BBox | None:
    return coerce_bbox(record.get("cnt")) or coerce_bbox(record)


def _mathpix_data_value(record: Mapping[str, Any], wanted: str) -> str | None:
    value = record.get("data")
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return None
    for item in value:
        if not isinstance(item, Mapping):
            continue
        if str(item.get("type", "")).casefold() == wanted:
            result = item.get("value")
            if isinstance(result, str):
                return _clean_text(result)
    return None


def _mathpix_image_ref(record: Mapping[str, Any], text: str | None) -> str | None:
    for key in ("image_url", "image-uri", "image_uri", "url", "src"):
        value = record.get(key)
        if isinstance(value, str) and value.lower().startswith(("https://", "http://")):
            return redacted_url_label(value)
    if text is not None and (match := _MARKDOWN_IMAGE_PATTERN.search(text)):
        return redacted_url_label(match.group(1))
    return None


def _page_markdown(page: Mapping[str, Any], lines: Sequence[Mapping[str, Any]]) -> str | None:
    for key in ("mmd", "markdown", "text"):
        value = _clean_text(page.get(key))
        if value:
            return value
    pieces: list[str] = []
    for line in lines:
        if line.get("conversion_output") is False:
            continue
        value = _clean_text(line.get("text_display")) or _clean_text(line.get("text"))
        if value:
            pieces.append(value)
    return "\n".join(pieces) or None


def _document_markdown(root: Mapping[str, Any], pages: Sequence[Page]) -> str | None:
    for key in ("mmd", "markdown"):
        value = _clean_text(root.get(key))
        if value:
            return value
    if len(pages) == 1:
        value = _clean_text(root.get("text"))
        if value:
            return value
    pieces = [
        markdown
        for page in pages
        if isinstance((markdown := page.metadata.get("markdown")), str) and markdown
    ]
    return "\n\n".join(pieces) or None


def _fallback_element(
    *,
    provider: str,
    page_number: int,
    root: Mapping[str, Any],
    width: float,
    height: float,
    score: float | None,
    version: str,
) -> Element | None:
    text = _page_markdown(root, [])
    latex = _nonempty_string(root.get("latex_styled")) or _mathpix_data_value(root, "latex")
    tsv = _mathpix_data_value(root, "tsv")
    if text is None and latex is None and tsv is None:
        return None
    if latex is not None:
        kind = ElementType.FORMULA
    elif tsv:
        kind = ElementType.TABLE
    else:
        kind = ElementType.TEXT
    metadata: dict[str, Any] = {
        "content_format": "mathpix_markdown",
        "coordinate_system": "full_page_fallback",
        "raw": _safe_mathpix_raw(root),
    }
    if latex is not None:
        metadata["latex"] = latex
    if tsv is not None:
        metadata["tsv"] = tsv
        metadata["rows"] = [row.split("\t") for row in tsv.splitlines()]
    return _element(
        provider=provider,
        element_id=f"page-{page_number}-content",
        source_id=_nonempty_string(root.get("request_id")) or f"page-{page_number}",
        kind=kind,
        bbox=BBox(x0=0, y0=0, x1=width, y1=height),
        polygon=[],
        text=text or latex or tsv,
        score=score,
        order=0,
        version=version,
        metadata=metadata,
    )


def _element(
    *,
    provider: str,
    element_id: str,
    source_id: str,
    kind: ElementType,
    bbox: BBox,
    polygon: list[Any],
    text: str | None,
    score: float | None,
    order: int,
    version: str,
    metadata: dict[str, Any],
) -> Element:
    return Element(
        id=element_id,
        type=kind,
        bbox=bbox,
        polygon=polygon,
        text=text,
        reading_order=max(0, order),
        confidence=score,
        provenance=Provenance(
            engine=provider,
            source_id=source_id,
            text_confidence=score if text is not None else None,
            layout_confidence=score,
            metadata={"model": "Mathpix OCR", "version": version},
        ),
        text_candidates=(
            [
                TextCandidate(
                    engine=provider,
                    value=text,
                    confidence=score,
                    source_element_id=source_id,
                    metadata={"content_format": "mathpix_markdown"},
                )
            ]
            if text is not None
            else []
        ),
        metadata=metadata,
    )


def _nonempty_string(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _clean_text(value: Any) -> str | None:
    return _redact_embedded_urls(value) if isinstance(value, str) else None


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


def _redact_payload_strings(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _redact_payload_strings(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact_payload_strings(item) for item in value]
    return _redact_embedded_urls(value) if isinstance(value, str) else value


def _safe_mathpix_raw(value: Any) -> Any:
    return safe_raw(_redact_payload_strings(value))


MathpixOCRProvider = MathpixProvider
MathpixOcrProvider = MathpixProvider

__all__ = ["MathpixOCRProvider", "MathpixOcrProvider", "MathpixProvider"]
