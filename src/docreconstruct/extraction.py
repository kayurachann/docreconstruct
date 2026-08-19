"""Capability-routed OCR extraction to canonical IR and Markdown."""

from __future__ import annotations

import hashlib
import json
import math
import re
import tempfile
from collections.abc import Mapping, Sequence
from concurrent.futures import Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import Field

from docreconstruct.ir import CanonicalModel, Document
from docreconstruct.providers import (
    CapabilityRequest,
    ProviderContext,
    ProviderExecutionMode,
    ProviderRegistry,
    recommend_providers,
)
from docreconstruct.providers import registry as global_registry
from docreconstruct.providers._hosted import safe_raw
from docreconstruct.renderers.json import JSONRenderer


class ExtractionMode(StrEnum):
    LOCAL = "local"
    CLOUD = "cloud"
    HYBRID = "hybrid"


class ProviderAttempt(CanonicalModel):
    provider: str
    status: str
    pages: int = 0
    warnings: list[str] = Field(default_factory=list)
    error: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    evidence_output: str | None = None
    evidence_sha256: str | None = None


class ExtractionRunManifest(CanonicalModel):
    schema_version: str = "0.1"
    source: str
    source_sha256: str
    mode: ExtractionMode
    cloud_authorized: bool
    requested_providers: list[str]
    selected_providers: list[str]
    successful_providers: list[str]
    attempts: list[ProviderAttempt]
    ensemble: bool
    document_id: str
    output: str
    maximum_concurrency: int = 1
    provider_timeout_seconds: float = 120.0
    warnings: list[str] = Field(default_factory=list)
    cache_key: str | None = None
    cache_hit: bool = False
    evidence_outputs: list[str] = Field(default_factory=list)
    evidence_sha256: dict[str, str] = Field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ExtractionResult:
    document: Document
    output: Path
    manifest: ExtractionRunManifest
    documents: tuple[Document, ...] = ()
    evidence_outputs: tuple[Path, ...] = ()


@dataclass(frozen=True, slots=True)
class _ProviderOutcome:
    attempt: ProviderAttempt
    document: Document | None = None


class _CacheArtifact(CanonicalModel):
    provider: str
    file: str
    sha256: str


class _ExtractionCacheRecord(CanonicalModel):
    schema_version: str = "0.1"
    cache_key: str
    source_sha256: str
    document_schema_version: str
    selected_providers: list[str]
    successful_providers: list[str]
    attempts: list[ProviderAttempt]
    artifacts: list[_CacheArtifact]
    ensemble: bool
    warnings: list[str] = Field(default_factory=list)


_CACHE_SCHEMA_VERSION = "0.1"
_SAFE_NAME = re.compile(r"[^a-zA-Z0-9_.-]+")
_SENSITIVE_TEXT = re.compile(
    r"(?i)\b(api[_-]?key|access[_-]?token|authorization|bearer[_-]?token|"
    r"client[_-]?secret|password|refresh[_-]?token|subscription[_-]?key)"
    r"(\s*[:=]\s*)([^\s,;]+)"
)
_SENSITIVE_EXACT = {
    "api_key",
    "authorization",
    "bearer",
    "credential",
    "password",
    "secret",
    "subscription_key",
    "token",
}
_SENSITIVE_MARKERS = (
    "access_key",
    "access_token",
    "api_key",
    "api_secret",
    "authorization",
    "auth_token",
    "bearer",
    "bearer_token",
    "client_secret",
    "credential",
    "id_token",
    "password",
    "private_key",
    "refresh_token",
    "secret",
    "subscription_key",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical_document_bytes(document: Document) -> bytes:
    return JSONRenderer().render(document).encode("utf-8")


def _atomic_write(path: Path, payload: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            stream.write(payload)
            temporary = Path(stream.name)
        temporary.replace(path)
    except Exception:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise
    return path


def _safe_provider_name(value: str) -> str:
    normalized = _SAFE_NAME.sub("_", value.strip()).strip("._")
    return normalized or "provider"


def _is_sensitive_key(value: object) -> bool:
    normalized = str(value).casefold().replace("-", "_")
    return normalized in _SENSITIVE_EXACT or any(
        marker in normalized for marker in _SENSITIVE_MARKERS
    )


def _sanitize_cache_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _sanitize_cache_value(nested)
            for key, nested in value.items()
            if not _is_sensitive_key(key)
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_sanitize_cache_value(item) for item in value]
    return safe_raw(value)


def _known_secret_values(value: Any) -> set[str]:
    secrets: set[str] = set()

    def collect(item: Any, *, sensitive: bool = False) -> None:
        if isinstance(item, Mapping):
            for key, nested in item.items():
                collect(nested, sensitive=sensitive or _is_sensitive_key(key))
            return
        if isinstance(item, Sequence) and not isinstance(item, (str, bytes, bytearray)):
            for nested in item:
                collect(nested, sensitive=sensitive)
            return
        if sensitive and item is not None:
            if isinstance(item, bytes):
                candidate = item.decode("utf-8", errors="ignore")
            else:
                candidate = str(item)
            if candidate:
                secrets.add(candidate)

    collect(value)
    return secrets


def _redact_error(error: Exception, *, secrets: set[str]) -> str:
    rendered = f"{type(error).__name__}: {error}"
    for secret in sorted(secrets, key=len, reverse=True):
        rendered = rendered.replace(secret, "[redacted]")
    rendered = _SENSITIVE_TEXT.sub("[credential redacted]", rendered)
    sanitized = safe_raw(rendered)
    return sanitized if isinstance(sanitized, str) else type(error).__name__


def _sanitized_metadata(
    name: str,
    metadata: Mapping[str, Any],
    *,
    registry: ProviderRegistry,
) -> dict[str, Any]:
    sanitized = safe_raw(metadata)
    result = dict(sanitized) if isinstance(sanitized, Mapping) else {}
    capabilities = registry.get_capabilities(name)
    if capabilities is not None:
        if capabilities.model_name and not any(key in result for key in ("model", "model_name")):
            result["model_name"] = capabilities.model_name
        if capabilities.model_version and "model_version" not in result:
            result["model_version"] = capabilities.model_version
    return result


def _provider_options_with_timeout(
    name: str,
    provider_options: Mapping[str, Any] | None,
    *,
    allow_cloud: bool,
    provider_timeout_seconds: float,
) -> dict[str, Any]:
    """Return isolated adapter options with a bounded transport timeout.

    The executor deadline below isolates the orchestration layer. Hosted HTTP
    calls also need their own timeout so a discarded worker does not retain a
    connection indefinitely. Existing shorter provider-specific limits remain
    authoritative; invalid explicit values are left for the adapter to reject.
    """

    options = _options_for_provider(name, provider_options, allow_cloud=allow_cloud)
    configured = options.get("timeout_seconds")
    transport_limit = max(1.0, provider_timeout_seconds)
    if configured is None:
        options["timeout_seconds"] = transport_limit
        return options
    if isinstance(configured, bool):
        return options
    try:
        configured_seconds = float(configured)
    except (TypeError, ValueError):
        return options
    if math.isfinite(configured_seconds) and configured_seconds > 0:
        options["timeout_seconds"] = min(configured_seconds, transport_limit)
    return options


def _attempt_provider(
    name: str,
    *,
    path: Path,
    normalized_mode: ExtractionMode,
    languages: Sequence[str],
    provider_options: Mapping[str, Any] | None,
    allow_cloud: bool,
    provider_timeout_seconds: float,
    registry: ProviderRegistry,
    secret_values: set[str],
) -> _ProviderOutcome:
    """Execute one provider without allowing its exception to escape the worker."""

    try:
        provider = registry.get(name)
        context = ProviderContext(
            source=str(path),
            options=_provider_options_with_timeout(
                name,
                provider_options,
                allow_cloud=allow_cloud,
                provider_timeout_seconds=provider_timeout_seconds,
            ),
            metadata={
                "extraction_mode": normalized_mode.value,
                "languages": list(languages),
            },
        )
        result = provider.parse(path, context=context)
        return _ProviderOutcome(
            attempt=ProviderAttempt(
                provider=name,
                status="succeeded",
                pages=len(result.document.pages),
                warnings=result.warnings,
                metadata=_sanitized_metadata(
                    name,
                    result.metadata,
                    registry=registry,
                ),
            ),
            document=result.document,
        )
    except Exception as exc:  # every provider has an independent auditable failure
        return _ProviderOutcome(
            attempt=ProviderAttempt(
                provider=name,
                status="failed",
                error=_redact_error(exc, secrets=secret_values),
            )
        )


def _timed_out_outcome(name: str, *, timeout_seconds: float) -> _ProviderOutcome:
    return _ProviderOutcome(
        attempt=ProviderAttempt(
            provider=name,
            status="timed_out",
            error=(f"TimeoutError: provider exceeded {timeout_seconds:g} second limit"),
        )
    )


def _attempt_provider_ensemble(
    names: Sequence[str],
    *,
    path: Path,
    normalized_mode: ExtractionMode,
    languages: Sequence[str],
    provider_options: Mapping[str, Any] | None,
    allow_cloud: bool,
    provider_timeout_seconds: float,
    maximum_concurrency: int,
    registry: ProviderRegistry,
    secret_values: set[str],
) -> list[_ProviderOutcome]:
    """Execute a bounded provider batch and return outcomes in request order.

    A shared deadline bounds each ensemble member from batch submission. The
    worker pool is deliberately not used as a context manager: waiting for an
    uncooperative timed-out worker during ``__exit__`` would defeat failure
    isolation. Hosted adapters receive a matching transport timeout above.
    """

    if not names:
        return []
    workers = min(maximum_concurrency, len(names))
    executor = ThreadPoolExecutor(
        max_workers=workers,
        thread_name_prefix="docreconstruct-ocr",
    )
    futures: list[Future[_ProviderOutcome]] = []
    try:
        futures = [
            executor.submit(
                _attempt_provider,
                name,
                path=path,
                normalized_mode=normalized_mode,
                languages=languages,
                provider_options=provider_options,
                allow_cloud=allow_cloud,
                provider_timeout_seconds=provider_timeout_seconds,
                registry=registry,
                secret_values=secret_values,
            )
            for name in names
        ]
        completed, _ = wait(futures, timeout=provider_timeout_seconds)
        outcomes: list[_ProviderOutcome] = []
        for name, future in zip(names, futures, strict=True):
            if future not in completed:
                future.cancel()
                outcomes.append(
                    _timed_out_outcome(
                        name,
                        timeout_seconds=provider_timeout_seconds,
                    )
                )
                continue
            try:
                outcomes.append(future.result())
            except Exception as exc:  # defensive: worker normally captures provider errors
                outcomes.append(
                    _ProviderOutcome(
                        attempt=ProviderAttempt(
                            provider=name,
                            status="failed",
                            error=_redact_error(exc, secrets=secret_values),
                        )
                    )
                )
        return outcomes
    finally:
        for future in futures:
            if not future.done():
                future.cancel()
        executor.shutdown(wait=False, cancel_futures=True)


def _attempt_provider_fallback(
    names: Sequence[str],
    *,
    path: Path,
    normalized_mode: ExtractionMode,
    languages: Sequence[str],
    provider_options: Mapping[str, Any] | None,
    allow_cloud: bool,
    provider_timeout_seconds: float,
    maximum_concurrency: int,
    registry: ProviderRegistry,
    secret_values: set[str],
) -> list[_ProviderOutcome]:
    """Try providers in order while isolating each call behind a deadline."""

    if not names:
        return []
    executor = ThreadPoolExecutor(
        max_workers=min(maximum_concurrency, len(names)),
        thread_name_prefix="docreconstruct-ocr",
    )
    futures: list[Future[_ProviderOutcome]] = []
    outcomes: list[_ProviderOutcome] = []
    try:
        for name in names:
            future = executor.submit(
                _attempt_provider,
                name,
                path=path,
                normalized_mode=normalized_mode,
                languages=languages,
                provider_options=provider_options,
                allow_cloud=allow_cloud,
                provider_timeout_seconds=provider_timeout_seconds,
                registry=registry,
                secret_values=secret_values,
            )
            futures.append(future)
            completed, _ = wait((future,), timeout=provider_timeout_seconds)
            if future not in completed:
                future.cancel()
                outcomes.append(
                    _timed_out_outcome(
                        name,
                        timeout_seconds=provider_timeout_seconds,
                    )
                )
                continue
            try:
                outcome = future.result()
            except Exception as exc:  # defensive: worker normally captures provider errors
                outcome = _ProviderOutcome(
                    attempt=ProviderAttempt(
                        provider=name,
                        status="failed",
                        error=_redact_error(exc, secrets=secret_values),
                    )
                )
            outcomes.append(outcome)
            if outcome.document is not None:
                break
        return outcomes
    finally:
        for future in futures:
            if not future.done():
                future.cancel()
        executor.shutdown(wait=False, cancel_futures=True)


def _stable_cache_key(
    *,
    source_sha256: str,
    source_format: str,
    mode: ExtractionMode,
    candidates: Sequence[str],
    languages: Sequence[str],
    handwriting: bool,
    formulas: bool,
    tables: bool,
    charts: bool,
    distorted_photo: bool,
    dewarping: bool,
    require_geometry: bool,
    ensemble: bool,
    maximum_providers: int,
    maximum_concurrency: int,
    provider_timeout_seconds: float,
    allow_cloud: bool,
    provider_options: Mapping[str, Any] | None,
    registry: ProviderRegistry,
) -> str:
    models: list[dict[str, str | None]] = []
    effective_options: dict[str, Any] = {}
    for name in candidates:
        capabilities = registry.get_capabilities(name)
        models.append(
            {
                "provider": name,
                "model_name": capabilities.model_name if capabilities is not None else None,
                "model_version": (capabilities.model_version if capabilities is not None else None),
            }
        )
        effective_options[name] = _sanitize_cache_value(
            _options_for_provider(name, provider_options, allow_cloud=allow_cloud)
        )
    payload = {
        "cache_schema_version": _CACHE_SCHEMA_VERSION,
        "document_schema_version": Document.CURRENT_SCHEMA_VERSION,
        "source": {"sha256": source_sha256, "format": source_format},
        "mode": mode.value,
        "providers": list(candidates),
        "models": models,
        "request": {
            "languages": list(languages),
            "handwriting": handwriting,
            "formulas": formulas,
            "tables": tables,
            "charts": charts,
            "distorted_photo": distorted_photo,
            "dewarping": dewarping,
            "require_geometry": require_geometry,
            "ensemble": ensemble,
            "maximum_providers": maximum_providers,
            "maximum_concurrency": maximum_concurrency,
            "provider_timeout_seconds": provider_timeout_seconds,
        },
        "provider_options": effective_options,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return _sha256_bytes(encoded)


def _cache_entry(cache_directory: Path, cache_key: str) -> Path:
    return cache_directory / cache_key


def _load_cached_documents(
    cache_directory: Path,
    *,
    cache_key: str,
    source_sha256: str,
) -> tuple[list[Document], _ExtractionCacheRecord]:
    entry = _cache_entry(cache_directory, cache_key)
    manifest_path = entry / "manifest.json"
    record = _ExtractionCacheRecord.model_validate_json(manifest_path.read_bytes())
    if record.schema_version != _CACHE_SCHEMA_VERSION:
        raise ValueError(f"unsupported cache schema {record.schema_version!r}")
    if record.cache_key != cache_key:
        raise ValueError("cache key does not match its directory")
    if record.source_sha256 != source_sha256:
        raise ValueError("cached source hash does not match the input")
    if record.document_schema_version != Document.CURRENT_SCHEMA_VERSION:
        raise ValueError("cached canonical document schema is incompatible")
    if len(record.artifacts) != len(record.successful_providers):
        raise ValueError("cache artifact/provider count mismatch")

    documents: list[Document] = []
    resolved_entry = entry.resolve()
    for expected_provider, artifact in zip(
        record.successful_providers, record.artifacts, strict=True
    ):
        if artifact.provider != expected_provider:
            raise ValueError("cache artifact provider order mismatch")
        relative = Path(artifact.file)
        if relative.is_absolute() or relative.name != artifact.file:
            raise ValueError("cache artifact path must be a plain filename")
        artifact_path = (entry / relative).resolve()
        if artifact_path.parent != resolved_entry:
            raise ValueError("cache artifact escapes its cache entry")
        payload = artifact_path.read_bytes()
        if _sha256_bytes(payload) != artifact.sha256:
            raise ValueError(f"cache artifact hash mismatch for {artifact.file}")
        documents.append(Document.model_validate_json(payload))
    if not documents:
        raise ValueError("cache record contains no canonical documents")
    return documents, record


def _write_cached_documents(
    cache_directory: Path,
    *,
    cache_key: str,
    source_sha256: str,
    selected_providers: Sequence[str],
    successful_providers: Sequence[str],
    attempts: Sequence[ProviderAttempt],
    documents: Sequence[Document],
    ensemble: bool,
    warnings: Sequence[str],
) -> None:
    entry = _cache_entry(cache_directory, cache_key)
    artifacts: list[_CacheArtifact] = []
    for index, (provider, document) in enumerate(
        zip(successful_providers, documents, strict=True), start=1
    ):
        file_name = f"document-{index:02d}-{_safe_provider_name(provider)}.json"
        payload = _canonical_document_bytes(document)
        _atomic_write(entry / file_name, payload)
        artifacts.append(
            _CacheArtifact(
                provider=provider,
                file=file_name,
                sha256=_sha256_bytes(payload),
            )
        )
    cache_attempts = [
        attempt.model_copy(update={"evidence_output": None, "evidence_sha256": None})
        for attempt in attempts
    ]
    record = _ExtractionCacheRecord(
        cache_key=cache_key,
        source_sha256=source_sha256,
        document_schema_version=Document.CURRENT_SCHEMA_VERSION,
        selected_providers=list(selected_providers),
        successful_providers=list(successful_providers),
        attempts=cache_attempts,
        artifacts=artifacts,
        ensemble=ensemble,
        warnings=list(warnings),
    )
    _atomic_write(entry / "manifest.json", record.model_dump_json(indent=2).encode("utf-8"))


def _persist_evidence(
    evidence_directory: Path,
    *,
    source: Path,
    successful_providers: Sequence[str],
    documents: Sequence[Document],
) -> tuple[list[Path], dict[str, str]]:
    outputs: list[Path] = []
    hashes: dict[str, str] = {}
    for index, (provider, document) in enumerate(
        zip(successful_providers, documents, strict=True), start=1
    ):
        payload = _canonical_document_bytes(document)
        digest = _sha256_bytes(payload)
        destination = evidence_directory / (
            f"{source.stem}.{index:02d}.{_safe_provider_name(provider)}."
            f"{digest[:12]}.canonical.json"
        )
        written = _atomic_write(destination, payload).resolve()
        outputs.append(written)
        hashes[str(written)] = digest
    return outputs, hashes


def _attach_evidence_to_attempts(
    attempts: Sequence[ProviderAttempt],
    *,
    successful_providers: Sequence[str],
    outputs: Sequence[Path],
    hashes: Mapping[str, str],
) -> list[ProviderAttempt]:
    by_provider = dict(zip(successful_providers, outputs, strict=True))
    updated: list[ProviderAttempt] = []
    for attempt in attempts:
        if attempt.status != "succeeded":
            updated.append(attempt)
            continue
        output = by_provider.get(attempt.provider)
        if output is None:
            updated.append(attempt)
            continue
        updated.append(
            attempt.model_copy(
                update={
                    "evidence_output": str(output),
                    "evidence_sha256": hashes.get(str(output)),
                }
            )
        )
    return updated


def _input_format(path: Path) -> str:
    suffix = path.suffix.casefold().lstrip(".")
    return {"jpg": "jpeg", "tif": "tiff"}.get(suffix, suffix)


def _mode_execution(mode: ExtractionMode) -> list[ProviderExecutionMode]:
    if mode is ExtractionMode.CLOUD:
        return [ProviderExecutionMode.API]
    if mode is ExtractionMode.LOCAL:
        return [ProviderExecutionMode.LOCAL]
    return [ProviderExecutionMode.LOCAL, ProviderExecutionMode.API]


def _options_for_provider(
    name: str,
    options: Mapping[str, Any] | None,
    *,
    allow_cloud: bool,
) -> dict[str, Any]:
    if options and isinstance(options.get(name), Mapping):
        nested = options.get(name)
        assert isinstance(nested, Mapping)
        selected = dict(nested)
    else:
        selected = dict(options or {})
    selected["allow_remote"] = allow_cloud
    return selected


def _provider_names(
    source: Path,
    *,
    mode: ExtractionMode,
    providers: Sequence[str] | None,
    languages: Sequence[str],
    handwriting: bool,
    formulas: bool,
    tables: bool,
    charts: bool,
    distorted_photo: bool,
    dewarping: bool,
    require_geometry: bool,
    registry: ProviderRegistry,
) -> list[str]:
    if providers:
        return list(
            dict.fromkeys(
                name.strip().casefold().replace("-", "_")
                for name in providers
                if name.strip() and name.strip().casefold() != "auto"
            )
        )
    request = CapabilityRequest(
        input_format=_input_format(source),
        languages=list(languages),
        multilingual=len(set(languages)) > 1,
        text=True,
        handwriting=handwriting,
        formulas=formulas,
        tables=tables,
        charts=charts,
        layout=True,
        reading_order=True,
        distorted_photos=distorted_photo,
        dewarping=dewarping,
        markdown=True,
        bounding_boxes=require_geometry,
        execution_modes=_mode_execution(mode),
        allow_credentials=True,
    )
    return [
        recommendation.provider
        for recommendation in recommend_providers(request, registry=registry)
    ]


def _validate_provider_mode(
    name: str,
    *,
    mode: ExtractionMode,
    require_geometry: bool,
    registry: ProviderRegistry,
) -> None:
    capabilities = registry.get_capabilities(name)
    if capabilities is None:
        raise KeyError(f"unknown provider {name!r}")
    allowed = set(_mode_execution(mode))
    if not allowed.intersection(capabilities.execution_modes):
        modes = ", ".join(item.value for item in capabilities.execution_modes) or "none"
        raise ValueError(f"provider {name!r} modes ({modes}) do not satisfy {mode.value!r}")
    if not capabilities.live_inference:
        raise ValueError(
            f"provider {name!r} only imports saved evidence; choose a live provider or plugin"
        )
    if require_geometry and not capabilities.bounding_boxes:
        raise ValueError(
            f"provider {name!r} does not declare bounding-box geometry required by this run"
        )


def extract_to_markdown(
    source: str | Path,
    *,
    output: str | Path | None = None,
    mode: ExtractionMode | str = ExtractionMode.CLOUD,
    providers: Sequence[str] | None = None,
    allow_cloud: bool = False,
    ensemble: bool = False,
    maximum_providers: int = 2,
    maximum_concurrency: int = 4,
    provider_timeout_seconds: float = 120.0,
    languages: Sequence[str] = (),
    handwriting: bool = False,
    formulas: bool = True,
    tables: bool = True,
    charts: bool = False,
    distorted_photo: bool = False,
    dewarping: bool = False,
    require_geometry: bool = False,
    provider_options: Mapping[str, Any] | None = None,
    evidence_directory: str | Path | None = None,
    cache_directory: str | Path | None = None,
    reuse_cache: bool = True,
    registry: ProviderRegistry | None = None,
) -> ExtractionResult:
    """Run compatible providers with fallback and write authoritative Markdown.

    Cloud upload is impossible unless ``allow_cloud`` is true. Credentials are
    read by the hosted adapter and are intentionally excluded from this API's
    manifest and cache identity. ``evidence_directory`` persists every
    successful provider's canonical document. ``cache_directory`` enables an
    artifact-hash-verified canonical result cache; it performs no live provider
    call on a valid hit. ``require_geometry`` makes bounding boxes a hard
    routing capability. Ensemble calls use at most ``maximum_concurrency``
    workers, and every attempted provider is isolated by
    ``provider_timeout_seconds`` in addition to its adapter transport timeout.
    """

    path = Path(source).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    normalized_mode = mode if isinstance(mode, ExtractionMode) else ExtractionMode(mode)
    if normalized_mode in {ExtractionMode.CLOUD, ExtractionMode.HYBRID} and not allow_cloud:
        raise PermissionError(
            f"{normalized_mode.value} extraction can send document bytes to a hosted OCR; "
            "set allow_cloud=True explicitly"
        )
    if maximum_providers < 1 or maximum_providers > 8:
        raise ValueError("maximum_providers must be between 1 and 8")
    if maximum_concurrency < 1 or maximum_concurrency > 8:
        raise ValueError("maximum_concurrency must be between 1 and 8")
    if (
        isinstance(provider_timeout_seconds, bool)
        or not math.isfinite(provider_timeout_seconds)
        or provider_timeout_seconds <= 0
        or provider_timeout_seconds > 600
    ):
        raise ValueError("provider_timeout_seconds must be greater than 0 and at most 600")
    destination = (
        Path(output).expanduser() if output is not None else path.with_name(f"{path.stem}.ocr.md")
    ).resolve()
    if destination.suffix.casefold() not in {".md", ".markdown"}:
        raise ValueError("OCR extraction output must use .md or .markdown")
    evidence_root = (
        Path(evidence_directory).expanduser().resolve() if evidence_directory is not None else None
    )
    if evidence_root is not None and evidence_root.exists() and not evidence_root.is_dir():
        raise NotADirectoryError(evidence_root)
    cache_root = (
        Path(cache_directory).expanduser().resolve() if cache_directory is not None else None
    )
    if cache_root is not None and cache_root.exists() and not cache_root.is_dir():
        raise NotADirectoryError(cache_root)

    active_registry = registry or global_registry
    requested = list(providers or ["auto"])
    candidates = _provider_names(
        path,
        mode=normalized_mode,
        providers=providers,
        languages=languages,
        handwriting=handwriting,
        formulas=formulas,
        tables=tables,
        charts=charts,
        distorted_photo=distorted_photo,
        dewarping=dewarping,
        require_geometry=require_geometry,
        registry=active_registry,
    )
    if not candidates:
        raise RuntimeError(
            "no registered live provider satisfies the request; install a provider plugin, "
            "relax requested capabilities, or use saved OCR evidence"
        )
    for name in candidates:
        _validate_provider_mode(
            name,
            mode=normalized_mode,
            require_geometry=require_geometry,
            registry=active_registry,
        )

    source_sha256 = _sha256(path)
    cache_key = (
        _stable_cache_key(
            source_sha256=source_sha256,
            source_format=_input_format(path),
            mode=normalized_mode,
            candidates=candidates,
            languages=languages,
            handwriting=handwriting,
            formulas=formulas,
            tables=tables,
            charts=charts,
            distorted_photo=distorted_photo,
            dewarping=dewarping,
            require_geometry=require_geometry,
            ensemble=ensemble,
            maximum_providers=maximum_providers,
            maximum_concurrency=maximum_concurrency,
            provider_timeout_seconds=provider_timeout_seconds,
            allow_cloud=allow_cloud,
            provider_options=provider_options,
            registry=active_registry,
        )
        if cache_root is not None
        else None
    )
    secret_values = _known_secret_values(provider_options)
    cache_warnings: list[str] = []
    attempts: list[ProviderAttempt] = []
    documents: list[Document] = []
    successful: list[str] = []
    selected: list[str] = []
    cache_hit = False
    if cache_root is not None and cache_key is not None and reuse_cache:
        cache_manifest = _cache_entry(cache_root, cache_key) / "manifest.json"
        if cache_manifest.is_file():
            try:
                documents, cache_record = _load_cached_documents(
                    cache_root,
                    cache_key=cache_key,
                    source_sha256=source_sha256,
                )
            except Exception as exc:
                cache_warnings.append(
                    "OCR cache ignored after validation failure: "
                    f"{_redact_error(exc, secrets=secret_values)}"
                )
                documents = []
            else:
                selected = list(cache_record.selected_providers)
                successful = list(cache_record.successful_providers)
                attempts = [attempt.model_copy(deep=True) for attempt in cache_record.attempts]
                cache_hit = True

    if not cache_hit:
        if ensemble:
            selected = list(candidates[:maximum_providers])
            outcomes = _attempt_provider_ensemble(
                selected,
                path=path,
                normalized_mode=normalized_mode,
                languages=languages,
                provider_options=provider_options,
                allow_cloud=allow_cloud,
                provider_timeout_seconds=provider_timeout_seconds,
                maximum_concurrency=maximum_concurrency,
                registry=active_registry,
                secret_values=secret_values,
            )
            for name, outcome in zip(selected, outcomes, strict=True):
                attempts.append(outcome.attempt)
                if outcome.document is not None:
                    successful.append(name)
                    documents.append(outcome.document)
        else:
            outcomes = _attempt_provider_fallback(
                candidates[:maximum_providers],
                path=path,
                normalized_mode=normalized_mode,
                languages=languages,
                provider_options=provider_options,
                allow_cloud=allow_cloud,
                provider_timeout_seconds=provider_timeout_seconds,
                maximum_concurrency=maximum_concurrency,
                registry=active_registry,
                secret_values=secret_values,
            )
            for outcome in outcomes:
                name = outcome.attempt.provider
                selected.append(name)
                attempts.append(outcome.attempt)
                if outcome.document is not None:
                    successful.append(name)
                    documents.append(outcome.document)

    if not documents:
        details = "; ".join(
            f"{attempt.provider}: {attempt.error}" for attempt in attempts if attempt.error
        )
        raise RuntimeError(f"all OCR providers failed: {details}")

    provider_warnings = [warning for attempt in attempts for warning in attempt.warnings]
    if not cache_hit and cache_root is not None and cache_key is not None:
        try:
            _write_cached_documents(
                cache_root,
                cache_key=cache_key,
                source_sha256=source_sha256,
                selected_providers=selected,
                successful_providers=successful,
                attempts=attempts,
                documents=documents,
                ensemble=len(documents) > 1,
                warnings=provider_warnings,
            )
        except Exception as exc:
            cache_warnings.append(
                "OCR cache write failed; extraction result remains valid: "
                f"{_redact_error(exc, secrets=secret_values)}"
            )

    evidence_outputs: list[Path] = []
    evidence_sha256: dict[str, str] = {}
    if evidence_root is not None:
        evidence_outputs, evidence_sha256 = _persist_evidence(
            evidence_root,
            source=path,
            successful_providers=successful,
            documents=documents,
        )
        attempts = _attach_evidence_to_attempts(
            attempts,
            successful_providers=successful,
            outputs=evidence_outputs,
            hashes=evidence_sha256,
        )

    if len(documents) > 1:
        from docreconstruct.normalization import fuse_documents

        document = fuse_documents(documents)
    else:
        document = documents[0].model_copy(deep=True)
    warnings = provider_warnings + cache_warnings
    preliminary = ExtractionRunManifest(
        source=str(path),
        source_sha256=source_sha256,
        mode=normalized_mode,
        cloud_authorized=allow_cloud,
        requested_providers=requested,
        selected_providers=selected,
        successful_providers=successful,
        attempts=attempts,
        ensemble=len(documents) > 1,
        maximum_concurrency=maximum_concurrency,
        provider_timeout_seconds=provider_timeout_seconds,
        document_id=document.id,
        output=str(destination),
        warnings=warnings,
        cache_key=cache_key,
        cache_hit=cache_hit,
        evidence_outputs=[str(item) for item in evidence_outputs],
        evidence_sha256=evidence_sha256,
    )
    metadata = dict(document.metadata)
    metadata["extraction_run"] = preliminary.model_dump(mode="json")
    document.metadata = metadata
    from docreconstruct.pipeline import export

    written = export(document, destination, output_format="markdown")
    manifest = preliminary.model_copy(update={"output": str(written)})
    return ExtractionResult(
        document=document,
        output=written,
        manifest=manifest,
        documents=tuple(documents),
        evidence_outputs=tuple(evidence_outputs),
    )


__all__ = [
    "ExtractionMode",
    "ExtractionResult",
    "ExtractionRunManifest",
    "ProviderAttempt",
    "extract_to_markdown",
]
