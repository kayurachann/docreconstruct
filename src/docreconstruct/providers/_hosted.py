"""Shared, dependency-free primitives for explicitly enabled hosted providers.

This module contains transport and privacy mechanics only.  Request/response
schemas stay in the provider modules so vendor-specific behavior cannot leak
into the core pipeline.
"""

from __future__ import annotations

import hashlib
import json
import mimetypes
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib import error as urllib_error
from urllib import request as urllib_request
from urllib.parse import urlparse, urlunparse

from .base import ProviderContext, ProviderError, ProviderInputError


class HostedProviderError(ProviderError):
    """Base error for a hosted-provider request or response."""


class RemoteInferenceDisabledError(HostedProviderError, PermissionError):
    """Raised unless the caller explicitly permits remote document processing."""


class ProviderAuthenticationError(HostedProviderError):
    """Raised when an explicitly enabled hosted provider has no credential."""


class ProviderHTTPError(HostedProviderError):
    """Raised for a failed or malformed hosted API response."""


# Keep hosted responses bounded even when a server omits or lies about
# Content-Length. This is intentionally a process-wide safety policy rather
# than a provider option: an untrusted response must not be able to opt out.
MAX_HOSTED_RESPONSE_BYTES = 64 * 1024 * 1024
_HOSTED_RESPONSE_READ_CHUNK_BYTES = 64 * 1024


@dataclass(frozen=True, slots=True)
class HTTPResponse:
    """Small transport-neutral HTTP response used by provider tests and adapters."""

    status: int
    headers: Mapping[str, str]
    body: bytes = b""


class HTTPTransport(Protocol):
    """Mockable synchronous HTTP boundary; no vendor SDK is required."""

    def __call__(
        self,
        *,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: float,
    ) -> HTTPResponse: ...


class _NoRedirectHandler(urllib_request.HTTPRedirectHandler):
    """Refuse redirects so credentials are never replayed to another origin."""

    def redirect_request(
        self,
        req: urllib_request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        return None


def _response_headers(raw_headers: Any) -> dict[str, str]:
    if raw_headers is None:
        return {}
    try:
        return {str(key): str(value) for key, value in raw_headers.items()}
    except (AttributeError, TypeError, ValueError):
        return {}


def _bounded_response_body(
    stream: Any,
    headers: Mapping[str, str],
) -> bytes:
    declared_length: int | None = None
    for key, value in headers.items():
        if key.casefold() != "content-length":
            continue
        try:
            candidate = int(value.strip())
        except (TypeError, ValueError):
            break
        if candidate >= 0:
            declared_length = candidate
        break
    if declared_length is not None and declared_length > MAX_HOSTED_RESPONSE_BYTES:
        raise ProviderHTTPError(
            f"hosted OCR response exceeds the {MAX_HOSTED_RESPONSE_BYTES}-byte safety limit"
        )

    chunks: list[bytes] = []
    received = 0
    while True:
        remaining = MAX_HOSTED_RESPONSE_BYTES - received
        chunk = stream.read(min(_HOSTED_RESPONSE_READ_CHUNK_BYTES, remaining + 1))
        if not chunk:
            break
        received += len(chunk)
        if received > MAX_HOSTED_RESPONSE_BYTES:
            raise ProviderHTTPError(
                f"hosted OCR response exceeds the {MAX_HOSTED_RESPONSE_BYTES}-byte safety limit"
            )
        chunks.append(bytes(chunk))
    return b"".join(chunks)


def _http_response_from_stream(stream: Any, *, status: int) -> HTTPResponse:
    headers = _response_headers(getattr(stream, "headers", None))
    return HTTPResponse(
        status=status,
        headers=headers,
        body=_bounded_response_body(stream, headers),
    )


def stdlib_http_transport(
    *,
    method: str,
    url: str,
    headers: Mapping[str, str],
    body: bytes | None,
    timeout: float,
) -> HTTPResponse:
    """Perform exactly one bounded HTTP request with the standard library.

    Automatic redirects are deliberately disabled. Provider credentials can
    therefore only be sent to the URL which already passed that provider's
    endpoint validation; callers may inspect/retry a redirect explicitly after
    applying the same validation to its destination.
    """

    request = urllib_request.Request(
        url,
        data=body,
        headers=dict(headers),
        method=method.upper(),
    )
    opener = urllib_request.build_opener(_NoRedirectHandler())
    try:
        with opener.open(request, timeout=timeout) as response:
            return _http_response_from_stream(response, status=int(response.status))
    except urllib_error.HTTPError as exc:
        try:
            return _http_response_from_stream(exc, status=int(exc.code))
        finally:
            exc.close()
    except (OSError, urllib_error.URLError) as exc:
        raise ProviderHTTPError(f"hosted OCR request failed: {exc}") from exc


@dataclass(frozen=True, slots=True)
class HostedSource:
    """A local byte payload or a caller-supplied HTTPS document URL."""

    label: str
    media_type: str
    data: bytes | None = None
    url: str | None = None


_MEDIA_TYPES = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
    ".bmp": "image/bmp",
}


def context_options(context: ProviderContext | None) -> Mapping[str, Any]:
    return context.options if context is not None else {}


def require_remote_opt_in(context: ProviderContext | None, provider: str) -> None:
    """Require a per-call Boolean opt-in before reading credentials or source bytes."""

    if context_options(context).get("allow_remote") is not True:
        raise RemoteInferenceDisabledError(
            f"{provider} sends document content to a hosted service. Set "
            "ProviderContext(options={'allow_remote': True}) for this call after "
            "reviewing the provider's privacy and retention terms."
        )


def option_or_environment(
    context: ProviderContext | None,
    option_name: str,
    environment_names: tuple[str, ...],
) -> str | None:
    """Read one in-memory option, then environment variables, without persisting it."""

    value = context_options(context).get(option_name)
    if isinstance(value, str) and value.strip():
        return value.strip()
    import os

    for name in environment_names:
        value = os.environ.get(name)
        if value and value.strip():
            return value.strip()
    return None


def require_credential(
    context: ProviderContext | None,
    *,
    provider: str,
    option_name: str,
    environment_names: tuple[str, ...],
) -> str:
    value = option_or_environment(context, option_name, environment_names)
    if value is None:
        choices = ", ".join(environment_names)
        raise ProviderAuthenticationError(
            f"{provider} requires ProviderContext option {option_name!r} or "
            f"environment variable {choices}."
        )
    return value


def numeric_option(
    context: ProviderContext | None,
    name: str,
    default: float,
    *,
    minimum: float,
    maximum: float,
) -> float:
    raw = context_options(context).get(name, default)
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise ProviderInputError(f"hosted provider option {name!r} must be numeric") from exc
    if not minimum <= value <= maximum:
        raise ProviderInputError(
            f"hosted provider option {name!r} must be between {minimum} and {maximum}"
        )
    return value


def validate_https_url(value: str, *, label: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise ProviderInputError(f"{label} must be an absolute HTTPS URL")
    if parsed.username or parsed.password:
        raise ProviderInputError(f"{label} must not contain embedded credentials")
    return value


def validate_service_endpoint(
    value: str,
    *,
    label: str,
    allowed_hosts: tuple[str, ...],
    allow_custom_endpoint: bool = False,
) -> str:
    """Validate a vendor endpoint without silently sending secrets elsewhere.

    Wildcard entries use the form ``*.example.com`` and require at least one
    hostname label before the suffix. Self-hosted or test endpoints remain
    available only through a second, explicit opt-in.
    """

    endpoint = validate_https_url(value, label=label)
    hostname = (urlparse(endpoint).hostname or "").casefold().rstrip(".")
    allowed = False
    for pattern in allowed_hosts:
        normalized = pattern.casefold().rstrip(".")
        if normalized.startswith("*."):
            suffix = normalized[1:]
            allowed = hostname.endswith(suffix) and hostname != suffix[1:]
        else:
            allowed = hostname == normalized
        if allowed:
            break
    if not allowed and not allow_custom_endpoint:
        choices = ", ".join(allowed_hosts)
        raise ProviderInputError(
            f"{label} host is not an official provider endpoint ({choices}); "
            "set allow_custom_endpoint=true only for a trusted self-hosted service"
        )
    return endpoint


def redacted_url_label(value: str) -> str:
    """Return a provenance-safe URL label without signed query material."""

    parsed = urlparse(value)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))


def load_hosted_source(
    source: str | bytes | bytearray | Path,
    *,
    context: ProviderContext | None,
    maximum_megabytes: float = 50.0,
) -> HostedSource:
    """Load a bounded local input or retain an explicit HTTPS URL."""

    if isinstance(source, str) and source.lower().startswith(("https://", "http://")):
        return HostedSource(
            label=redacted_url_label(source),
            media_type="application/octet-stream",
            url=validate_https_url(source, label="hosted OCR source URL"),
        )

    if isinstance(source, (bytes, bytearray)):
        data = bytes(source)
        label = context.source if context and context.source else "input-bytes"
        configured_type = context_options(context).get("media_type", "application/pdf")
        media_type = str(configured_type)
    else:
        path = Path(source).expanduser().resolve()
        if not path.is_file():
            raise ProviderInputError(f"hosted OCR source does not exist: {path}")
        maximum_bytes = int(maximum_megabytes * 1024 * 1024)
        size = path.stat().st_size
        if size > maximum_bytes:
            raise ProviderInputError(
                f"hosted OCR source is {size} bytes; configured limit is {maximum_bytes} bytes"
            )
        data = path.read_bytes()
        label = str(path)
        media_type = _MEDIA_TYPES.get(
            path.suffix.lower(),
            mimetypes.guess_type(path.name)[0] or "application/octet-stream",
        )

    maximum_bytes = int(maximum_megabytes * 1024 * 1024)
    if len(data) > maximum_bytes:
        raise ProviderInputError(
            f"hosted OCR source is {len(data)} bytes; configured limit is {maximum_bytes} bytes"
        )
    return HostedSource(label=label, media_type=media_type, data=data)


def response_header(response: HTTPResponse, name: str) -> str | None:
    wanted = name.casefold()
    for key, value in response.headers.items():
        if str(key).casefold() == wanted:
            return str(value)
    return None


def decode_json_response(
    response: HTTPResponse,
    *,
    provider: str,
    allowed_statuses: tuple[int, ...] = (200,),
) -> Any:
    """Decode JSON without exposing request headers or credentials in errors."""

    try:
        payload = json.loads(response.body.decode("utf-8")) if response.body else {}
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProviderHTTPError(
            f"{provider} returned non-JSON data (HTTP {response.status})"
        ) from exc
    if response.status not in allowed_statuses:
        detail: Any = payload
        if isinstance(payload, Mapping):
            detail = payload.get("error", payload.get("message", payload))
        rendered = str(safe_raw(detail)).replace("\n", " ")[:400]
        raise ProviderHTTPError(f"{provider} HTTP {response.status}: {rendered}")
    return payload


def response_request_id(response: HTTPResponse) -> str | None:
    for name in ("x-request-id", "request-id", "apim-request-id"):
        if value := response_header(response, name):
            return value
    return None


_SENSITIVE_KEYS = {
    "access_key",
    "app_id",
    "app_key",
    "api_key",
    "apikey",
    "authorization",
    "secret",
    "subscription_key",
    "token",
}
_SENSITIVE_KEY_MARKERS = (
    "access_key",
    "access_token",
    "accesstoken",
    "api_key",
    "app_key",
    "authorization",
    "bearer_token",
    "bearertoken",
    "credential",
    "id_token",
    "idtoken",
    "password",
    "refresh_token",
    "refreshtoken",
    "secret",
    "subscription_key",
)
_URL_IN_TEXT = re.compile(r"https?://[^\s<>\"'\)\]]+", re.IGNORECASE)


def _redact_urls_in_text(value: str) -> str:
    return _URL_IN_TEXT.sub(lambda match: redacted_url_label(match.group(0)), value)


def safe_raw(value: Any) -> Any:
    """Retain raw provenance while removing credentials and large image payloads."""

    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, nested in value.items():
            normalized = str(key).casefold().replace("-", "_")
            if normalized in _SENSITIVE_KEYS or any(
                marker in normalized for marker in _SENSITIVE_KEY_MARKERS
            ):
                continue
            if normalized in {"image_base64", "base64source", "base64_source"} and isinstance(
                nested, str
            ):
                result[str(key)] = {
                    "omitted": True,
                    "characters": len(nested),
                    "sha256": hashlib.sha256(nested.encode("utf-8")).hexdigest(),
                }
            else:
                result[str(key)] = safe_raw(nested)
        return result
    if isinstance(value, (list, tuple)):
        return [safe_raw(item) for item in value]
    if isinstance(value, str):
        return _redact_urls_in_text(value)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)


__all__ = [
    "HTTPResponse",
    "HTTPTransport",
    "HostedProviderError",
    "HostedSource",
    "MAX_HOSTED_RESPONSE_BYTES",
    "ProviderAuthenticationError",
    "ProviderHTTPError",
    "RemoteInferenceDisabledError",
    "context_options",
    "decode_json_response",
    "load_hosted_source",
    "numeric_option",
    "option_or_environment",
    "redacted_url_label",
    "require_credential",
    "require_remote_opt_in",
    "response_header",
    "response_request_id",
    "safe_raw",
    "stdlib_http_transport",
    "validate_service_endpoint",
    "validate_https_url",
]
