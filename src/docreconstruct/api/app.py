"""FastAPI application for the optional docreconstruct service."""

from __future__ import annotations

import importlib
import importlib.metadata
import importlib.util
import json
import logging
import math
import os
import shutil
import tempfile
from collections.abc import Mapping
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Annotated, Any, TypeVar, cast

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.background import BackgroundTasks
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, ValidationError

from docreconstruct.exceptions import (
    DocReconstructError,
    ProviderUnavailableError,
    RendererUnavailableError,
    UnsupportedInputError,
)

from .models import (
    AnalysisResponse,
    AnalyzeOptions,
    CompareOptions,
    ComparisonResponse,
    ErrorResponse,
    FormatInfo,
    FormatsResponse,
    HealthResponse,
    HybridOptions,
    HybridQuality,
    ProviderInfo,
    ProvidersResponse,
    ReconstructionResponse,
    ReconstructOptions,
    RouteOptions,
    RoutingResponse,
)

logger = logging.getLogger(__name__)
OptionsModel = TypeVar("OptionsModel", bound=BaseModel)

_OUTPUT_DETAILS = {
    "json": (".json", "application/json"),
    "html": (".html", "text/html; charset=utf-8"),
    "docx": (
        ".docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ),
    "markdown": (".md", "text/markdown; charset=utf-8"),
}


def _package_version() -> str:
    try:
        return importlib.metadata.version("docreconstruct")
    except importlib.metadata.PackageNotFoundError:
        return "0.1.0"


def _upload_limit() -> int:
    raw_value = os.getenv("DOCRECONSTRUCT_MAX_UPLOAD_MB", "50")
    try:
        megabytes = max(1, int(raw_value))
    except ValueError:
        megabytes = 50
    return megabytes * 1024 * 1024


def _parse_options(payload: str | None, model: type[OptionsModel]) -> OptionsModel:
    if payload is None or not payload.strip():
        return model()
    try:
        decoded = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=422,
            detail=f"options must be valid JSON: {exc.msg}",
        ) from exc
    try:
        return model.model_validate(decoded)
    except ValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail=exc.errors(include_url=False, include_context=False),
        ) from exc


async def _stage_upload(upload: UploadFile, directory: Path, fallback_name: str) -> Path:
    filename = Path(upload.filename or fallback_name).name
    if filename in {"", ".", ".."}:
        filename = fallback_name
    target = directory / filename
    size = 0
    limit = _upload_limit()

    try:
        with target.open("wb") as stream:
            while chunk := await upload.read(1024 * 1024):
                size += len(chunk)
                if size > limit:
                    raise HTTPException(
                        status_code=413,
                        detail=(
                            f"upload exceeds the {limit // (1024 * 1024)} MiB configured limit"
                        ),
                    )
                stream.write(chunk)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    finally:
        await upload.close()

    if size == 0:
        target.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="uploaded file is empty")
    return target


def _jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return _jsonable(value.to_dict())
    if is_dataclass(value):
        return _jsonable(asdict(cast(Any, value)))
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if hasattr(value, "__dict__"):
        return {
            key: _jsonable(item) for key, item in vars(value).items() if not key.startswith("_")
        }
    return str(value)


def _document_payload(document: Any) -> dict[str, Any]:
    payload = _jsonable(document)
    if isinstance(payload, dict):
        return payload
    return {"value": payload}


def _result_warnings(result: Any) -> list[str]:
    warnings = [str(item) for item in (getattr(result, "warnings", []) or [])]
    metadata = getattr(result, "metadata", None)
    if isinstance(metadata, dict):
        warnings.extend(str(item) for item in metadata.get("warnings", []) or [])
        pipeline_metadata = metadata.get("pipeline")
        if isinstance(pipeline_metadata, dict):
            warnings.extend(str(item) for item in pipeline_metadata.get("warnings", []) or [])
    return list(dict.fromkeys(warnings))


def _raise_pipeline_error(exc: Exception) -> None:
    if isinstance(exc, HTTPException):
        raise exc
    if isinstance(exc, (UnsupportedInputError, ValueError, KeyError, FileNotFoundError)):
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if isinstance(exc, (ProviderUnavailableError, RendererUnavailableError)):
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if isinstance(exc, (ImportError, ModuleNotFoundError)):
        raise HTTPException(
            status_code=503,
            detail=f"an optional reconstruction component is unavailable: {exc}",
        ) from exc
    if isinstance(exc, RuntimeError):
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if isinstance(exc, DocReconstructError):
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    logger.exception("Unhandled document processing error", exc_info=exc)
    raise HTTPException(status_code=500, detail="document processing failed") from exc


def _registry_names(module_name: str) -> list[str]:
    try:
        registry_module = importlib.import_module(module_name)
        factory = getattr(registry_module, "get_registry", None)
        registry = factory() if callable(factory) else registry_module.registry
        return sorted(str(name) for name in registry.names())
    except (AttributeError, ImportError, RuntimeError, TypeError):
        return []


def _provider_infos() -> list[ProviderInfo]:
    registered = _registry_names("docreconstruct.providers")
    if registered:
        try:
            provider_module = importlib.import_module("docreconstruct.providers")
            factory = getattr(provider_module, "get_registry", None)
            registry = factory() if callable(factory) else provider_module.registry
            rows: list[ProviderInfo] = []
            for name in registered:
                provider = registry.get(name)
                capabilities = provider.capabilities.model_dump(mode="json")
                capability_names = [
                    field
                    for field in (
                        "saved_json",
                        "live_inference",
                        "text",
                        "geometry",
                        "reading_order",
                        "styles",
                        "tables",
                        "images",
                    )
                    if capabilities.get(field) is True
                ]
                dependency_available = (
                    name != "native_pdf" or importlib.util.find_spec("pymupdf") is not None
                )
                reason = None
                if not dependency_available:
                    reason = "install docreconstruct[pdf]"
                elif not capabilities.get("live_inference", False) and name not in {
                    "json",
                    "native_pdf",
                }:
                    reason = "saved-output adapter; live inference is not bundled"
                rows.append(
                    ProviderInfo(
                        name=name,
                        available=dependency_available,
                        capabilities=capability_names,
                        reason=reason,
                    )
                )
            return rows
        except (AttributeError, ImportError, RuntimeError, TypeError, ValueError):
            pass

    optional_modules = {
        "native_pdf": "pymupdf",
        "paddleocr": "paddleocr",
        "mineru": "mineru",
        "olmocr": "olmocr",
    }
    providers: list[ProviderInfo] = []
    for name, module_name in optional_modules.items():
        available = importlib.util.find_spec(module_name) is not None
        providers.append(
            ProviderInfo(
                name=name,
                available=available,
                reason=None if available else f"optional package `{module_name}` is not installed",
            )
        )
    return providers


def _format_infos() -> list[FormatInfo]:
    pdf_available = importlib.util.find_spec("pymupdf") is not None
    docx_available = importlib.util.find_spec("docx") is not None
    return [
        FormatInfo(
            name="pdf",
            direction="input",
            available=pdf_available,
            media_type="application/pdf",
            reason=None if pdf_available else "install docreconstruct[pdf]",
        ),
        FormatInfo(name="png", direction="input", available=True, media_type="image/png"),
        FormatInfo(name="jpeg", direction="input", available=True, media_type="image/jpeg"),
        FormatInfo(name="tiff", direction="input", available=True, media_type="image/tiff"),
        FormatInfo(name="json", direction="output", available=True, media_type="application/json"),
        FormatInfo(name="html", direction="output", available=True, media_type="text/html"),
        FormatInfo(name="markdown", direction="output", available=True, media_type="text/markdown"),
        FormatInfo(
            name="docx",
            direction="output",
            available=docx_available,
            media_type=("application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
            reason=None if docx_available else "install docreconstruct[docx]",
        ),
    ]


def _artifact_name(source: Path, options: ReconstructOptions) -> str:
    suffix, _ = _OUTPUT_DETAILS[options.output_format.value]
    if options.output_filename:
        supplied = Path(options.output_filename)
        return supplied.name if supplied.suffix.lower() == suffix else f"{supplied.stem}{suffix}"
    return f"{source.stem}{suffix}"


def _cors_origins() -> list[str]:
    """Return explicit browser origins; wildcard CORS is never enabled implicitly."""

    configured = os.getenv("DOCRECONSTRUCT_CORS_ORIGINS", "")
    return list(
        dict.fromkeys(
            origin.strip().rstrip("/") for origin in configured.split(",") if origin.strip()
        )
    )


def _operator_feature_enabled(name: str) -> bool:
    """Require a deliberate operator opt-in for high-risk server capabilities."""

    return os.getenv(name, "").strip().casefold() in {"1", "true", "yes", "on"}


def _hybrid_artifact_name(content: Path, options: HybridOptions) -> str:
    if options.output_filename:
        return options.output_filename
    return f"{content.stem}_editable.docx"


def _hybrid_timing_headers(value: Any) -> dict[str, str]:
    """Expose only fixed, non-sensitive phase names through standard HTTP timing."""

    if not isinstance(value, Mapping):
        return {}
    phases = (
        ("authority", "authority.hash"),
        ("ocr", "ocr.online"),
        ("scan", "prepare.scan"),
        ("evidence", "prepare.evidence_match"),
        ("docx", "reconstruct.docx_render"),
        ("qa-native", "qa.native"),
        ("qa-render", "qa.render"),
        ("qa-visual", "qa.visual"),
        ("total", "job.total"),
    )
    measurements: list[tuple[str, float]] = []
    for label, key in phases:
        raw = value.get(key)
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            continue
        seconds = float(raw)
        if seconds >= 0 and math.isfinite(seconds):
            measurements.append((label, seconds))
    if not measurements:
        return {}
    headers = {
        "Server-Timing": ", ".join(
            f"{label};dur={seconds * 1000.0:.3f}" for label, seconds in measurements
        )
    }
    total = next((seconds for label, seconds in measurements if label == "total"), None)
    if total is not None:
        headers["X-DocReconstruct-Duration"] = f"{total:.6f}"
    return headers


def create_app() -> FastAPI:
    application = FastAPI(
        title="docreconstruct API",
        summary="Geometry-aware document reconstruction service",
        description=(
            "Normalize OCR or native extraction evidence into a canonical document model, "
            "then render an editable artifact. OCR engines are optional adapters and are not "
            "bundled with the API."
        ),
        version=_package_version(),
        license_info={"name": "Apache-2.0"},
    )
    cors_origins = _cors_origins()
    if cors_origins:
        application.add_middleware(
            CORSMiddleware,
            allow_origins=cors_origins,
            allow_credentials=False,
            allow_methods=["GET", "POST", "OPTIONS"],
            allow_headers=["Accept", "Content-Type"],
            expose_headers=[
                "Content-Disposition",
                "Server-Timing",
                "X-DocReconstruct-Duration",
                "X-DocReconstruct-OCR",
                "X-DocReconstruct-QA-Score",
                "X-DocReconstruct-Quality",
                "X-DocReconstruct-Visual-Score",
            ],
        )

    @application.get("/", include_in_schema=False)
    async def index() -> dict[str, str]:
        return {
            "name": "docreconstruct",
            "version": _package_version(),
            "docs": "/docs",
        }

    @application.get("/health", response_model=HealthResponse, tags=["service"])
    @application.get(
        "/v1/health", response_model=HealthResponse, tags=["service"], include_in_schema=False
    )
    async def health() -> HealthResponse:
        return HealthResponse(status="ok", version=_package_version())

    @application.get("/v1/providers", response_model=ProvidersResponse, tags=["discovery"])
    async def providers() -> ProvidersResponse:
        return ProvidersResponse(providers=_provider_infos())

    @application.get("/v1/formats", response_model=FormatsResponse, tags=["discovery"])
    async def formats() -> FormatsResponse:
        return FormatsResponse(formats=_format_infos())

    @application.post(
        "/v1/analyze",
        response_model=AnalysisResponse,
        responses={
            422: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
        tags=["documents"],
    )
    async def analyze_document(
        file: Annotated[UploadFile, File(description="PDF or raster document")],
        options: Annotated[
            str | None,
            Form(description="JSON-encoded AnalyzeOptions; omit to use defaults"),
        ] = None,
    ) -> AnalysisResponse:
        parsed = _parse_options(options, AnalyzeOptions)
        temp_dir = Path(tempfile.mkdtemp(prefix="docreconstruct-analyze-"))
        try:
            source = await _stage_upload(file, temp_dir, "document.bin")
            pipeline = importlib.import_module("docreconstruct.pipeline")
            result = await run_in_threadpool(
                pipeline.analyze,
                source,
                engines=parsed.engines or None,
                fusion=parsed.fusion,
                provider_options=parsed.provider_options or None,
            )
            return AnalysisResponse(
                document=_document_payload(result),
                engines=parsed.engines,
                fusion=parsed.fusion,
                warnings=_result_warnings(result),
            )
        except Exception as exc:
            _raise_pipeline_error(exc)
            raise  # pragma: no cover - _raise_pipeline_error always raises
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    @application.post(
        "/v1/route",
        response_model=RoutingResponse,
        responses={422: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
        tags=["documents"],
    )
    async def route_document(
        file: Annotated[UploadFile, File(description="PDF, raster document, or saved IR")],
        options: Annotated[
            str | None,
            Form(description="JSON-encoded RouteOptions; omit to use defaults"),
        ] = None,
    ) -> RoutingResponse:
        parsed = _parse_options(options, RouteOptions)
        temp_dir = Path(tempfile.mkdtemp(prefix="docreconstruct-route-"))
        try:
            source = await _stage_upload(file, temp_dir, "document.bin")
            pipeline = importlib.import_module("docreconstruct.pipeline")
            document = await run_in_threadpool(
                pipeline.analyze,
                source,
                engines=parsed.engines or None,
                fusion=parsed.fusion,
                provider_options=parsed.provider_options or None,
            )
            routing = importlib.import_module("docreconstruct.routing")
            policy = routing.RoutingPolicy(confidence_threshold=parsed.confidence_threshold)
            plan = await run_in_threadpool(
                routing.build_routing_plan,
                document,
                policy=policy,
                force_element_ids=parsed.force_element_ids,
            )
            return RoutingResponse(plan=_document_payload(plan), warnings=list(plan.warnings))
        except Exception as exc:
            _raise_pipeline_error(exc)
            raise  # pragma: no cover
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    @application.post(
        "/v1/reconstruct",
        response_model=ReconstructionResponse,
        responses={
            200: {
                "description": "A rendered download, or JSON when no artifact was produced",
                "content": {
                    "application/json": {},
                    "text/html": {},
                    "text/markdown": {},
                    ("application/vnd.openxmlformats-officedocument.wordprocessingml.document"): {},
                },
            },
            422: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
        tags=["documents"],
    )
    async def reconstruct_document(
        background_tasks: BackgroundTasks,
        file: Annotated[UploadFile, File(description="PDF or raster document")],
        options: Annotated[
            str | None,
            Form(description="JSON-encoded ReconstructOptions; omit to use defaults"),
        ] = None,
    ) -> Any:
        parsed = _parse_options(options, ReconstructOptions)
        temp_dir = Path(tempfile.mkdtemp(prefix="docreconstruct-output-"))
        try:
            source = await _stage_upload(file, temp_dir, "document.bin")
            artifact = temp_dir / _artifact_name(source, parsed)
            pipeline = importlib.import_module("docreconstruct.pipeline")
            result = await run_in_threadpool(
                pipeline.reconstruct,
                source,
                output=artifact,
                output_format=parsed.output_format.value,
                engines=parsed.engines or None,
                fusion=parsed.fusion,
                profile=parsed.profile.value,
                refine=parsed.refine,
                maximum_refinement_passes=parsed.maximum_refinement_passes,
                provider_options=parsed.provider_options or None,
            )
            if artifact.is_file():
                background_tasks.add_task(shutil.rmtree, temp_dir, ignore_errors=True)
                _, media_type = _OUTPUT_DETAILS[parsed.output_format.value]
                return FileResponse(
                    artifact,
                    filename=artifact.name,
                    media_type=media_type,
                    background=background_tasks,
                )
            response = ReconstructionResponse(
                document=_document_payload(result),
                warnings=_result_warnings(result),
            )
            shutil.rmtree(temp_dir, ignore_errors=True)
            return response
        except Exception as exc:
            shutil.rmtree(temp_dir, ignore_errors=True)
            _raise_pipeline_error(exc)
            raise  # pragma: no cover - _raise_pipeline_error always raises

    @application.post(
        "/v1/hybrid",
        responses={
            200: {
                "description": "Editable DOCX reconstructed from Markdown and source layout",
                "content": {
                    ("application/vnd.openxmlformats-officedocument.wordprocessingml.document"): {}
                },
            },
            400: {"model": ErrorResponse},
            422: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
        tags=["documents"],
    )
    async def hybrid_document(
        background_tasks: BackgroundTasks,
        content: Annotated[UploadFile, File(description="Markdown content authority")],
        layout: Annotated[UploadFile, File(description="Original PDF or raster layout authority")],
        evidence: Annotated[
            UploadFile | None,
            File(description="Optional OCR JSON geometry and structure evidence"),
        ] = None,
        options: Annotated[
            str | None,
            Form(description="JSON-encoded HybridOptions; omit to use fast native QA"),
        ] = None,
    ) -> FileResponse:
        parsed = _parse_options(options, HybridOptions)
        if parsed.evidence_provider is not None and evidence is None:
            raise HTTPException(
                status_code=422,
                detail="evidence_provider requires an evidence upload",
            )
        if parsed.remote_assets and not _operator_feature_enabled(
            "DOCRECONSTRUCT_ALLOW_REMOTE_ASSETS"
        ):
            raise HTTPException(
                status_code=503,
                detail=(
                    "remote Markdown assets are disabled by the server operator; "
                    "DOCRECONSTRUCT_ALLOW_REMOTE_ASSETS must be enabled explicitly"
                ),
            )

        renderer_path: str | None = None
        render_backend = "native"
        if parsed.quality is HybridQuality.VERIFIED:
            renderer_path = os.getenv("DOCRECONSTRUCT_LIBREOFFICE_PATH")
            if not renderer_path:
                raise HTTPException(
                    status_code=503,
                    detail=(
                        "verified quality is unavailable because the server operator has not "
                        "configured DOCRECONSTRUCT_LIBREOFFICE_PATH"
                    ),
                )
            render_backend = "libreoffice"

        if parsed.use_paddleocr_vl and not os.getenv("PADDLEOCR_VL_SERVER_URL"):
            raise HTTPException(
                status_code=503,
                detail=(
                    "PaddleOCR-VL is unavailable because the server operator has not configured "
                    "PADDLEOCR_VL_SERVER_URL"
                ),
            )

        temp_dir = Path(tempfile.mkdtemp(prefix="docreconstruct-hybrid-api-"))
        try:
            content_path = await _stage_upload(content, temp_dir, "content.md")
            layout_path = await _stage_upload(layout, temp_dir, "layout.bin")
            evidence_path = (
                await _stage_upload(evidence, temp_dir, "evidence.json")
                if evidence is not None
                else None
            )
            output_path = temp_dir / _hybrid_artifact_name(content_path, parsed)
            qa_report = temp_dir / f"{output_path.stem}.qa.json"

            hybrid_job = importlib.import_module("docreconstruct.reconstruction.hybrid_job")
            online_ocr = None
            if parsed.use_paddleocr_vl:
                extraction = importlib.import_module("docreconstruct.extraction")
                online_ocr = hybrid_job.OnlineOCRRequest(
                    mode=extraction.ExtractionMode.CLOUD,
                    providers=("paddleocr_vl_server",),
                    allow_cloud=True,
                    maximum_providers=1,
                    artifacts_directory=temp_dir / "ocr",
                    cache=False,
                    # The endpoint comes from operator-controlled environment
                    # configuration, never from the upload client. Mark that
                    # reviewed non-loopback endpoint as trusted explicitly.
                    provider_options={"paddleocr_vl_server": {"allow_custom_endpoint": True}},
                )
            result = await run_in_threadpool(
                hybrid_job.run_hybrid_job,
                content_path,
                layout_path,
                evidence=evidence_path,
                evidence_provider_hints=parsed.evidence_provider,
                strict_evidence=parsed.strict_evidence,
                output=output_path,
                allow_remote_assets=parsed.remote_assets,
                online_ocr=online_ocr,
                render_backend=render_backend,
                renderer_path=renderer_path,
                minimum_visual_score=parsed.minimum_visual_score,
                qa_report=qa_report,
            )
            if not result.validation.passed:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        "hybrid reconstruction failed project QA "
                        f"({result.validation.score:.2%}); no artifact was returned"
                    ),
                )
            if not output_path.is_file():
                raise RuntimeError("hybrid reconstruction did not produce a DOCX artifact")

            background_tasks.add_task(shutil.rmtree, temp_dir, ignore_errors=True)
            ocr_provenance = (
                "paddleocr-vl-server"
                if parsed.use_paddleocr_vl
                else "provided-evidence"
                if evidence is not None
                else "scan-layout-only"
            )
            headers = {
                "X-DocReconstruct-OCR": ocr_provenance,
                "X-DocReconstruct-QA-Score": f"{result.validation.score:.6f}",
                "X-DocReconstruct-Quality": parsed.quality.value,
            }
            rendered_visual = getattr(result.validation, "metrics", {}).get("rendered_visual")
            if isinstance(rendered_visual, Mapping):
                raw_visual_score = rendered_visual.get("score")
                if (
                    isinstance(raw_visual_score, (int, float))
                    and not isinstance(raw_visual_score, bool)
                    and math.isfinite(float(raw_visual_score))
                    and 0.0 <= float(raw_visual_score) <= 1.0
                ):
                    headers["X-DocReconstruct-Visual-Score"] = f"{float(raw_visual_score):.6f}"
            headers.update(_hybrid_timing_headers(getattr(result, "phase_seconds", None)))
            return FileResponse(
                output_path,
                filename=output_path.name,
                media_type=(
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                ),
                headers=headers,
                background=background_tasks,
            )
        except Exception as exc:
            shutil.rmtree(temp_dir, ignore_errors=True)
            _raise_pipeline_error(exc)
            raise  # pragma: no cover - _raise_pipeline_error always raises

    @application.post(
        "/v1/compare",
        response_model=ComparisonResponse,
        responses={
            422: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
        tags=["evaluation"],
    )
    async def compare_documents(
        reference: Annotated[UploadFile, File(description="Reference document")],
        candidate: Annotated[UploadFile, File(description="Reconstructed document")],
        options: Annotated[
            str | None,
            Form(description="JSON-encoded CompareOptions; omit to use defaults"),
        ] = None,
    ) -> ComparisonResponse:
        parsed = _parse_options(options, CompareOptions)
        temp_dir = Path(tempfile.mkdtemp(prefix="docreconstruct-compare-"))
        try:
            reference_path = await _stage_upload(reference, temp_dir, "reference.bin")
            candidate_path = await _stage_upload(candidate, temp_dir, "candidate.bin")
            evaluation = importlib.import_module("docreconstruct.evaluation")
            result = await run_in_threadpool(
                evaluation.evaluate,
                reference_path,
                candidate_path,
                profile=parsed.profile.value,
                output_format=(
                    parsed.output_format.value if parsed.output_format is not None else None
                ),
            )
            payload = _document_payload(result)
            fidelity = getattr(result, "fidelity", None)
            overall = getattr(fidelity, "overall", None)
            if overall is None and isinstance(payload.get("fidelity"), dict):
                overall = payload["fidelity"].get("overall")
            return ComparisonResponse(
                overall_score=float(overall) if overall is not None else None,
                report=payload,
            )
        except Exception as exc:
            _raise_pipeline_error(exc)
            raise  # pragma: no cover - _raise_pipeline_error always raises
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    return application


app = create_app()
