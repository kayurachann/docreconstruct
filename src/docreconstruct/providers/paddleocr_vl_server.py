"""Opt-in client for an official or user-managed PaddleOCR-VL API server."""

from __future__ import annotations

import base64
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse

from docreconstruct.ir import Document

from ._hosted import (
    HTTPTransport,
    context_options,
    decode_json_response,
    load_hosted_source,
    numeric_option,
    option_or_environment,
    redacted_url_label,
    require_remote_opt_in,
    response_request_id,
    safe_raw,
    stdlib_http_transport,
)
from ._utils import looks_like_inline_json
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
from .paddleocr import PaddleOCRProvider

_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


class PaddleOCRVLServerProvider(SavedJSONProvider):
    """Normalize PaddleOCR-VL JSON or call its full ``/layout-parsing`` API.

    This adapter deliberately targets the complete PaddleOCR-VL pipeline API,
    not a bare OpenAI-compatible VLM endpoint. The latter omits layout,
    preprocessing, page restructuring, and other evidence required by the
    reconstruction pipeline.
    """

    name = "paddleocr_vl_server"
    _capabilities = ProviderCapabilities(
        provider=name,
        supported_inputs=["pdf", "png", "jpeg", "tiff", "json"],
        saved_json=True,
        live_inference=True,
        text=True,
        geometry=True,
        reading_order=True,
        tables=True,
        images=True,
        multilingual=True,
        handwriting=True,
        formulas=True,
        charts=True,
        layout=True,
        distorted_photos=True,
        dewarping=True,
        execution_modes=[ProviderExecutionMode.SAVED, ProviderExecutionMode.API],
        markdown=True,
        bounding_boxes=True,
        confidence_scores=True,
        privacy=ProviderPrivacy.USER_MANAGED,
        license=ProviderLicense(
            name="Apache License 2.0 (PaddleOCR code)",
            spdx="Apache-2.0",
            open_source=True,
            commercial_use=True,
            restrictions=["Deployed model and hosting terms remain operator-controlled."],
        ),
        model_name="PaddleOCR-VL full pipeline server",
        cost=ProviderCost.INFRASTRUCTURE,
        credentials=ProviderCredentialRequirement.OPTIONAL,
        credential_env_vars=["PADDLEOCR_VL_SERVER_TOKEN"],
        notes=[
            "Requires explicit allow_remote consent for every live request.",
            "Use the official high-performance Triton + vLLM deployment for concurrency.",
            "The endpoint must expose the complete /layout-parsing pipeline API.",
        ],
    )

    def __init__(self, *, transport: HTTPTransport | None = None) -> None:
        self._transport = transport or stdlib_http_transport
        self._normalizer = PaddleOCRProvider()

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
        endpoint = _endpoint(context)
        maximum_megabytes = numeric_option(
            context,
            "max_upload_mb",
            100.0,
            minimum=0.1,
            maximum=1024.0,
        )
        hosted_source = load_hosted_source(
            source,
            context=context,
            maximum_megabytes=maximum_megabytes,
        )
        if hosted_source.url is not None:
            file_value = hosted_source.url
        else:
            assert hosted_source.data is not None
            file_value = base64.b64encode(hosted_source.data).decode("ascii")
        file_type = 0 if hosted_source.media_type == "application/pdf" else 1
        request_payload: dict[str, Any] = {
            "file": file_value,
            "fileType": file_type,
            # Visualization is useful for debugging but adds substantial payload
            # and CPU work on high-throughput servers.
            "visualize": bool(options.get("visualize", False)),
            # Geometry-first reconstruction does not need the often very large
            # base64 Markdown image bundle. Keep it opt-in for lower latency and
            # bounded hosted responses.
            "returnMarkdownImages": bool(options.get("return_markdown_images", False)),
        }
        for local_name, api_name in (
            ("use_doc_orientation_classify", "useDocOrientationClassify"),
            ("use_doc_unwarping", "useDocUnwarping"),
            ("use_chart_recognition", "useChartRecognition"),
            ("merge_tables", "mergeTables"),
            ("relevel_titles", "relevelTitles"),
            ("concatenate_pages", "concatenatePages"),
        ):
            if local_name in options:
                request_payload[api_name] = bool(options[local_name])

        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "docreconstruct/0.1",
        }
        token = option_or_environment(
            context,
            "server_token",
            ("PADDLEOCR_VL_SERVER_TOKEN",),
        )
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        timeout = numeric_option(context, "timeout_seconds", 120.0, minimum=0.1, maximum=600.0)
        response = self._transport(
            method="POST",
            url=endpoint,
            headers=headers,
            body=json.dumps(request_payload, ensure_ascii=False).encode("utf-8"),
            timeout=timeout,
        )
        payload = decode_json_response(response, provider=self.name)
        if not isinstance(payload, Mapping):
            raise ProviderInputError("PaddleOCR-VL server response must be a JSON object")
        error_code = payload.get("errorCode", 0)
        if error_code not in (None, 0, "0"):
            detail = safe_raw(payload.get("errorMsg") or payload.get("message") or error_code)
            raise ProviderInputError(f"PaddleOCR-VL server rejected the request: {detail}")
        effective_context = context or ProviderContext()
        if effective_context.source is None:
            effective_context = effective_context.model_copy(update={"source": hosted_source.label})
        document = self.normalize(payload, context=effective_context)
        return ProviderResult(
            provider=self.name,
            document=document,
            metadata={
                "hosted": True,
                "endpoint": redacted_url_label(endpoint),
                "request_id": response_request_id(response)
                or (str(payload.get("logId")) if payload.get("logId") else None),
                "model": str(options.get("model", "PaddleOCR-VL")),
            },
        )

    def normalize(
        self,
        payload: Any,
        *,
        context: ProviderContext | None = None,
    ) -> Document:
        pages = _response_pages(payload)
        document = self._normalizer.normalize(pages, context=context)
        document.metadata.update({"provider": self.name, "upstream_provider": "paddleocr"})
        for page in document.pages:
            page.metadata.update({"provider": self.name, "upstream_provider": "paddleocr"})
            for element in page.elements:
                if element.provenance is not None:
                    element.provenance.engine = self.name
                    element.provenance.metadata.setdefault("upstream_provider", "paddleocr")
                for candidate in element.text_candidates:
                    candidate.engine = self.name
                    candidate.metadata.setdefault("upstream_provider", "paddleocr")
        return document


def _response_pages(payload: Any) -> Any:
    if isinstance(payload, Mapping):
        result = payload.get("result")
        if isinstance(result, Mapping):
            pages = result.get("layoutParsingResults")
            if isinstance(pages, Sequence) and not isinstance(pages, (str, bytes)):
                return pages
        pages = payload.get("layoutParsingResults")
        if isinstance(pages, Sequence) and not isinstance(pages, (str, bytes)):
            return pages
    return payload


def _endpoint(context: ProviderContext | None) -> str:
    options = context_options(context)
    raw = option_or_environment(
        context,
        "endpoint",
        ("PADDLEOCR_VL_SERVER_URL",),
    )
    if raw is None:
        raise ProviderInputError(
            "paddleocr_vl_server requires ProviderContext option 'endpoint' or "
            "PADDLEOCR_VL_SERVER_URL"
        )
    parsed = urlparse(raw)
    hostname = (parsed.hostname or "").casefold()
    loopback = hostname in _LOOPBACK_HOSTS
    if not parsed.hostname or parsed.username or parsed.password:
        raise ProviderInputError(
            "PaddleOCR-VL endpoint must be an absolute URL without credentials"
        )
    if parsed.scheme.casefold() != "https" and not (
        parsed.scheme.casefold() == "http" and loopback
    ):
        raise ProviderInputError(
            "PaddleOCR-VL endpoint must use HTTPS; plain HTTP is allowed only for loopback"
        )
    if not loopback and options.get("allow_custom_endpoint") is not True:
        raise ProviderInputError(
            "A non-loopback PaddleOCR-VL server requires allow_custom_endpoint=true "
            "after the operator reviews that endpoint"
        )
    path = parsed.path.rstrip("/")
    if not path.endswith("/layout-parsing"):
        path = f"{path}/layout-parsing" if path else "/layout-parsing"
    return urlunparse((parsed.scheme, parsed.netloc, path, "", "", ""))


PaddleOCRVLAPIProvider = PaddleOCRVLServerProvider
PaddleOcrVlServerProvider = PaddleOCRVLServerProvider


__all__ = [
    "PaddleOCRVLAPIProvider",
    "PaddleOCRVLServerProvider",
    "PaddleOcrVlServerProvider",
]
