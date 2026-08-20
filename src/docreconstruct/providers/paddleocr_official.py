"""Bounded adapter for PaddleOCR's official asynchronous hosted API."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import socket
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

from docreconstruct.ir import Document

from ._hosted import (
    HTTPResponse,
    HTTPTransport,
    ProviderHTTPError,
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
    validate_https_url,
    validate_service_endpoint,
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

_DEFAULT_BASE_URL = "https://paddleocr.aistudio-app.com"
_JOBS_PATH = "/api/v2/ocr/jobs"
_MODEL = "PaddleOCR-VL-1.6"
_FINISHED_STATES = {"done", "failed"}
AddressResolver = Callable[[str, int], Sequence[str]]


class PaddleOCROfficialProvider(SavedJSONProvider):
    """Normalize saved results or call PaddleOCR's official hosted API.

    The official service is different from PaddleX's synchronous
    ``/layout-parsing`` deployment. It accepts an asynchronous job, exposes a
    status resource, and returns a JSONL result URL after completion.
    """

    name = "paddleocr_official"
    _capabilities = ProviderCapabilities(
        provider=name,
        supported_inputs=["pdf", "png", "jpeg", "tiff", "json", "jsonl"],
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
        privacy=ProviderPrivacy.THIRD_PARTY,
        license=ProviderLicense(
            name="PaddleOCR official hosted service",
            open_source=False,
            commercial_use=None,
            restrictions=["Hosted-service quota and terms are controlled by Paddle AI Studio."],
        ),
        model_name=_MODEL,
        cost=ProviderCost.UNKNOWN,
        credentials=ProviderCredentialRequirement.REQUIRED,
        credential_env_vars=["PADDLEOCR_ACCESS_TOKEN"],
        notes=[
            "Requires explicit allow_remote consent for every live request.",
            "Uses PaddleOCR's official asynchronous /api/v2/ocr/jobs API.",
            "The official API may apply account quotas or service-specific terms.",
        ],
    )

    def __init__(
        self,
        *,
        transport: HTTPTransport | None = None,
        sleeper: Callable[[float], None] | None = None,
        clock: Callable[[], float] | None = None,
        resolver: AddressResolver | None = None,
    ) -> None:
        self._transport = transport or stdlib_http_transport
        self._sleep = sleeper or time.sleep
        self._clock = clock or time.monotonic
        self._resolver = resolver or _resolve_addresses
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
        if isinstance(source, (str, Path)) and Path(source).suffix.casefold() in {
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
        token = require_credential(
            context,
            provider=self.name,
            option_name="access_token",
            environment_names=("PADDLEOCR_ACCESS_TOKEN",),
        )
        base_url = option_or_environment(context, "base_url", ("PADDLEOCR_BASE_URL",))
        base_url = validate_service_endpoint(
            (base_url or _DEFAULT_BASE_URL).rstrip("/"),
            label="PaddleOCR official API base URL",
            allowed_hosts=("paddleocr.aistudio-app.com",),
            allow_custom_endpoint=options.get("allow_custom_endpoint") is True,
        )
        parsed_base_url = urlparse(base_url)
        if parsed_base_url.query or parsed_base_url.fragment:
            raise ProviderInputError("PaddleOCR official API base URL must not contain query data")
        timeout = numeric_option(
            context,
            "timeout_seconds",
            120.0,
            minimum=1.0,
            maximum=600.0,
        )
        poll_timeout = numeric_option(
            context,
            "poll_timeout_seconds",
            timeout,
            minimum=1.0,
            maximum=600.0,
        )
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
        model = str(options.get("model", _MODEL)).strip()
        if model not in {"PaddleOCR-VL", "PaddleOCR-VL-1.5", "PaddleOCR-VL-1.6"}:
            raise ProviderInputError("unsupported PaddleOCR official document-parsing model")
        optional_payload = _optional_payload(options)
        jobs_url = f"{base_url}{_JOBS_PATH}"
        auth_headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "docreconstruct/0.1",
        }
        if hosted_source.url is not None:
            submit_body = json.dumps(
                {
                    "fileUrl": hosted_source.url,
                    "model": model,
                    "optionalPayload": optional_payload,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            submit_headers = {**auth_headers, "Content-Type": "application/json"}
        else:
            assert hosted_source.data is not None
            boundary, submit_body = _multipart_body(
                data=hosted_source.data,
                media_type=hosted_source.media_type,
                model=model,
                optional_payload=optional_payload,
            )
            submit_headers = {
                **auth_headers,
                "Content-Type": f"multipart/form-data; boundary={boundary}",
            }
        submitted = self._transport(
            method="POST",
            url=jobs_url,
            headers=submit_headers,
            body=submit_body,
            timeout=timeout,
        )
        submitted_data = _response_data(submitted, provider=self.name, statuses=(200, 201, 202))
        job_id = submitted_data.get("jobId")
        if not isinstance(job_id, str) or not job_id or len(job_id) > 200:
            raise ProviderHTTPError("PaddleOCR official API returned an invalid job ID")

        status_data = self._wait_for_result(
            jobs_url=jobs_url,
            job_id=job_id,
            headers=auth_headers,
            request_timeout=timeout,
            poll_timeout=poll_timeout,
        )
        result_url = status_data.get("resultUrl")
        if not isinstance(result_url, Mapping):
            raise ProviderHTTPError("completed PaddleOCR job has no resultUrl object")
        json_url = result_url.get("jsonUrl")
        if not isinstance(json_url, str) or not json_url:
            raise ProviderHTTPError("completed PaddleOCR job has no JSONL result URL")
        json_url = validate_https_url(json_url, label="PaddleOCR JSONL result URL")
        _validate_public_result_url(json_url, resolver=self._resolver)
        result_response = self._transport(
            method="GET",
            url=json_url,
            # Never replay the Paddle access token to a pre-signed storage URL.
            headers={"Accept": "application/x-ndjson, application/json"},
            body=None,
            timeout=timeout,
        )
        if result_response.status != 200:
            raise ProviderHTTPError(
                f"PaddleOCR JSONL result download failed with HTTP {result_response.status}"
            )
        payload = _decode_jsonl(result_response.body)
        effective_context = context or ProviderContext()
        if effective_context.source is None:
            effective_context = effective_context.model_copy(update={"source": hosted_source.label})
        document = self.normalize(payload, context=effective_context)
        return ProviderResult(
            provider=self.name,
            document=document,
            metadata={
                "hosted": True,
                "endpoint": redacted_url_label(base_url),
                "job_id": job_id,
                "model": model,
            },
        )

    def _wait_for_result(
        self,
        *,
        jobs_url: str,
        job_id: str,
        headers: Mapping[str, str],
        request_timeout: float,
        poll_timeout: float,
    ) -> Mapping[str, Any]:
        deadline = self._clock() + poll_timeout
        interval = 0.5
        polls = 0
        while self._clock() < deadline and polls < 1000:
            polls += 1
            response = self._transport(
                method="GET",
                url=f"{jobs_url}/{quote(job_id, safe='')}",
                headers=headers,
                body=None,
                timeout=request_timeout,
            )
            data = _response_data(response, provider=self.name)
            state = data.get("state")
            if state not in {"pending", "running", *_FINISHED_STATES}:
                raise ProviderHTTPError("PaddleOCR job returned an unknown state")
            if state == "done":
                return data
            if state == "failed":
                detail = str(safe_raw(data.get("errorMsg") or "unknown error"))[:400]
                raise ProviderHTTPError(f"PaddleOCR job failed: {detail}")
            remaining = deadline - self._clock()
            if remaining <= 0:
                break
            self._sleep(min(interval, remaining))
            interval = min(interval * 1.5, 5.0)
        raise ProviderHTTPError("PaddleOCR job did not finish before the polling timeout")

    def normalize(
        self,
        payload: Any,
        *,
        context: ProviderContext | None = None,
    ) -> Document:
        pages = _official_pages(payload)
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


def _optional_payload(options: Mapping[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "useLayoutDetection": True,
        "returnMarkdownImages": False,
        "visualize": False,
    }
    for local_name, remote_name in (
        ("use_doc_orientation_classify", "useDocOrientationClassify"),
        ("use_doc_unwarping", "useDocUnwarping"),
        ("use_chart_recognition", "useChartRecognition"),
        ("use_layout_detection", "useLayoutDetection"),
        ("prettify_markdown", "prettifyMarkdown"),
        ("return_markdown_images", "returnMarkdownImages"),
        ("visualize", "visualize"),
    ):
        if local_name in options:
            payload[remote_name] = bool(options[local_name])
    return payload


def _multipart_body(
    *,
    data: bytes,
    media_type: str,
    model: str,
    optional_payload: Mapping[str, Any],
) -> tuple[str, bytes]:
    boundary = f"docreconstruct-{hashlib.sha256(data).hexdigest()[:24]}"
    chunks: list[bytes] = []

    def field(name: str, value: str) -> None:
        chunks.extend(
            [
                f"--{boundary}\r\n".encode("ascii"),
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("ascii"),
                value.encode("utf-8"),
                b"\r\n",
            ]
        )

    field("model", model)
    field(
        "optionalPayload", json.dumps(optional_payload, ensure_ascii=False, separators=(",", ":"))
    )
    extension = {
        "application/pdf": ".pdf",
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/tiff": ".tiff",
    }.get(media_type, ".bin")
    chunks.extend(
        [
            f"--{boundary}\r\n".encode("ascii"),
            b'Content-Disposition: form-data; name="file"; filename="document',
            extension.encode("ascii"),
            b'"\r\n',
            f"Content-Type: {media_type}\r\n\r\n".encode("ascii"),
            data,
            b"\r\n",
            f"--{boundary}--\r\n".encode("ascii"),
        ]
    )
    return boundary, b"".join(chunks)


def _response_data(
    response: HTTPResponse,
    *,
    provider: str,
    statuses: tuple[int, ...] = (200,),
) -> Mapping[str, Any]:
    payload = decode_json_response(response, provider=provider, allowed_statuses=statuses)
    if not isinstance(payload, Mapping):
        raise ProviderHTTPError(f"{provider} response must be a JSON object")
    code = payload.get("code", 0)
    if code not in (0, None, "0"):
        detail = str(safe_raw(payload.get("msg") or payload.get("message") or code))[:400]
        raise ProviderHTTPError(f"{provider} rejected the request: {detail}")
    data = payload.get("data")
    if not isinstance(data, Mapping):
        raise ProviderHTTPError(f"{provider} response has no data object")
    return data


def _decode_jsonl(value: bytes) -> list[Any]:
    try:
        text = value.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ProviderHTTPError("PaddleOCR JSONL result is not UTF-8") from exc
    records: list[Any] = []
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise ProviderHTTPError(
                f"PaddleOCR JSONL result is malformed at line {line_number}"
            ) from exc
    if not records:
        raise ProviderHTTPError("PaddleOCR JSONL result is empty")
    return records


def _resolve_addresses(hostname: str, port: int) -> Sequence[str]:
    try:
        records = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ProviderInputError("PaddleOCR JSONL result host could not be resolved") from exc
    return tuple(dict.fromkeys(str(record[4][0]) for record in records))


def _validate_public_result_url(value: str, *, resolver: AddressResolver) -> None:
    """Reject result URLs that resolve to loopback, link-local, or private networks."""

    parsed = urlparse(value)
    hostname = (parsed.hostname or "").casefold().rstrip(".")
    if hostname == "localhost" or hostname.endswith((".localhost", ".local", ".internal")):
        raise ProviderInputError("PaddleOCR JSONL result URL must use a public host")
    try:
        literal = ipaddress.ip_address(hostname.split("%", 1)[0])
    except ValueError:
        addresses = resolver(hostname, parsed.port or 443)
    else:
        addresses = (str(literal),)
    if not addresses:
        raise ProviderInputError("PaddleOCR JSONL result host resolved to no addresses")
    for address in addresses:
        try:
            resolved = ipaddress.ip_address(address.split("%", 1)[0])
        except ValueError as exc:
            raise ProviderInputError(
                "PaddleOCR JSONL result host returned an invalid network address"
            ) from exc
        if not resolved.is_global:
            raise ProviderInputError("PaddleOCR JSONL result URL must use a public host")


def _official_pages(payload: Any) -> list[Any]:
    records = (
        list(payload)
        if isinstance(payload, Sequence) and not isinstance(payload, (str, bytes, bytearray))
        else [payload]
    )
    pages: list[Any] = []
    for record in records:
        if not isinstance(record, Mapping):
            continue
        result = record.get("result")
        container = result if isinstance(result, Mapping) else record
        values = container.get("layoutParsingResults")
        if isinstance(values, Sequence) and not isinstance(values, (str, bytes, bytearray)):
            pages.extend(values)
            continue
        if "prunedResult" in container or "pruned_result" in container:
            pages.append(container)
    if not pages and payload not in ({}, []):
        raise ProviderInputError("unrecognized PaddleOCR official result shape")
    return pages


PaddleOCRAPIProvider = PaddleOCROfficialProvider


__all__ = ["PaddleOCRAPIProvider", "PaddleOCROfficialProvider"]
