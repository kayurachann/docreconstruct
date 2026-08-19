"""Pydantic models used by the optional HTTP API.

This module intentionally depends only on the core package requirements.  It
can be imported by SDK clients that do not have FastAPI installed.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ReconstructionMode(StrEnum):
    """How the pipeline balances appearance and native editability."""

    VISUAL = "visual"
    FIDELITY = "fidelity"
    REPLICA = "replica"
    BALANCED = "balanced"
    SEMANTIC = "semantic"
    PIXEL_PERFECT = "pixel-perfect"
    EDITABLE = "editable"
    DATA = "data"
    ARCHIVAL = "archival"
    PRESENTATION = "presentation"


class OutputFormat(StrEnum):
    """Formats exposed by the v0.1 renderer surface."""

    JSON = "json"
    HTML = "html"
    DOCX = "docx"
    MARKDOWN = "markdown"


class AnalyzeOptions(BaseModel):
    """Options encoded in the ``options`` field of an analyze upload."""

    model_config = ConfigDict(extra="forbid")

    engines: list[str] = Field(default_factory=list)
    fusion: bool = False
    provider_options: dict[str, dict[str, Any]] = Field(default_factory=dict)

    @field_validator("engines")
    @classmethod
    def normalize_engines(cls, engines: list[str]) -> list[str]:
        normalized = [name.strip() for name in engines if name.strip()]
        if len(set(normalized)) != len(normalized):
            raise ValueError("engine names must be unique")
        return normalized

    @field_validator("provider_options")
    @classmethod
    def reject_server_file_options(
        cls, provider_options: dict[str, dict[str, Any]]
    ) -> dict[str, dict[str, Any]]:
        """Keep upload clients from selecting arbitrary server-side files."""

        blocked_names = {
            "file",
            "filename",
            "input_file",
            "input_path",
            "model_path",
            "output_file",
            "output_path",
            "path",
            "provider_sources",
            "source_file",
            "source_path",
            "template_file",
            "template_path",
        }

        def inspect(value: Any, location: str) -> None:
            if isinstance(value, dict):
                for key, nested in value.items():
                    normalized = str(key).strip().lower().replace("-", "_")
                    nested_location = f"{location}.{key}" if location else str(key)
                    if (
                        normalized in blocked_names
                        or normalized.endswith("_directory")
                        or normalized.endswith("_dir")
                        or normalized.endswith("_file")
                        or normalized.endswith("_path")
                    ):
                        raise ValueError(
                            f"local file option `{nested_location}` is not allowed "
                            "by the upload API"
                        )
                    inspect(nested, nested_location)
            elif isinstance(value, list):
                for index, nested in enumerate(value):
                    inspect(nested, f"{location}[{index}]")

        inspect(provider_options, "provider_options")
        return provider_options


class ReconstructOptions(AnalyzeOptions):
    """Options encoded in the ``options`` field of a reconstruct upload."""

    output_format: OutputFormat = OutputFormat.JSON
    profile: ReconstructionMode = ReconstructionMode.BALANCED
    refine: bool = False
    maximum_refinement_passes: int = Field(default=0, ge=0, le=20)
    output_filename: str | None = Field(default=None, max_length=240)

    # Renderer options are intentionally absent. Filesystem templates and other
    # trusted local renderer inputs belong in an operator-controlled SDK call,
    # not in an unauthenticated multipart request.

    @field_validator("output_filename")
    @classmethod
    def filename_only(cls, filename: str | None) -> str | None:
        if filename is None:
            return None
        filename = filename.strip()
        if not filename:
            return None
        if filename in {".", ".."} or "/" in filename or "\\" in filename:
            raise ValueError("output_filename must be a plain filename")
        return filename


class RouteOptions(AnalyzeOptions):
    """Options for cost-aware page/region routing."""

    confidence_threshold: float = Field(default=0.75, ge=0.0, le=1.0)
    force_element_ids: list[str] = Field(default_factory=list)

    @field_validator("force_element_ids")
    @classmethod
    def normalize_element_ids(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values if value.strip()]
        if len(normalized) != len(set(normalized)):
            raise ValueError("force_element_ids must be unique")
        return normalized


class CompareOptions(BaseModel):
    """Options encoded in the ``options`` field of a comparison upload."""

    model_config = ConfigDict(extra="forbid")

    profile: ReconstructionMode = ReconstructionMode.BALANCED
    output_format: OutputFormat | None = None


class HealthResponse(BaseModel):
    status: str
    version: str
    api_version: str = "v1"


class ProviderInfo(BaseModel):
    name: str
    available: bool
    capabilities: list[str] = Field(default_factory=list)
    reason: str | None = None


class ProvidersResponse(BaseModel):
    providers: list[ProviderInfo]


class FormatInfo(BaseModel):
    name: str
    direction: str
    available: bool
    media_type: str
    reason: str | None = None


class FormatsResponse(BaseModel):
    formats: list[FormatInfo]


class AnalysisResponse(BaseModel):
    document: dict[str, Any]
    engines: list[str] = Field(default_factory=list)
    fusion: bool = False
    warnings: list[str] = Field(default_factory=list)


class RoutingResponse(BaseModel):
    plan: dict[str, Any]
    warnings: list[str] = Field(default_factory=list)


class ArtifactInfo(BaseModel):
    filename: str
    media_type: str
    size: int | None = None


class ReconstructionResponse(BaseModel):
    document: dict[str, Any]
    artifact: ArtifactInfo | None = None
    warnings: list[str] = Field(default_factory=list)


class ComparisonResponse(BaseModel):
    overall_score: float | None = Field(default=None, ge=0, le=1)
    report: dict[str, Any]


class ErrorResponse(BaseModel):
    detail: str | list[dict[str, Any]]
