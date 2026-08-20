"""Public models for the audited OmniDocBench projection contract."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from docreconstruct.ir import Document


class OmniDocBenchProjectionReason(StrEnum):
    """Stable outcomes of validating annotation and raster coordinates."""

    DIMENSIONS_MATCH = "dimensions_match"
    REPORTED_DIMENSIONS_TRANSPOSED = "reported_dimensions_transposed"
    REPORTED_DIMENSIONS_INCOMPATIBLE = "reported_dimensions_incompatible"
    ANNOTATION_OUT_OF_BOUNDS = "annotation_out_of_bounds"


class OmniDocBenchConversionReason(StrEnum):
    """Stable converter outcomes used by reports, warnings, and failures."""

    DIMENSIONS_MATCH = "dimensions_match"
    REPORTED_DIMENSIONS_TRANSPOSED = "reported_dimensions_transposed"
    IGNORED_ANNOTATION_AUDIT_ONLY = "ignored_annotation_audit_only"
    MISSING_READING_ORDER = "missing_reading_order"
    UNSUPPORTED_CATEGORY_PROJECTED_AS_UNKNOWN = "unsupported_category_projected_as_unknown"
    RELATION_REFERENCES_UNKNOWN_ANNOTATION = "relation_references_unknown_annotation"
    INVALID_ANNOTATION_ID = "invalid_annotation_id"
    DUPLICATE_ANNOTATION_ID = "duplicate_annotation_id"
    INVALID_READING_ORDER = "invalid_reading_order"
    DUPLICATE_READING_ORDER = "duplicate_reading_order"
    INVALID_IGNORE_FLAG = "invalid_ignore_flag"
    INVALID_EXTRA_RELATION = "invalid_extra_relation"
    DUPLICATE_OUTPUT_NAME = "duplicate_output_name"
    TEXT_HASH_MISMATCH = "text_hash_mismatch"
    RELATION_HASH_MISMATCH = "relation_hash_mismatch"


class OmniDocBenchProjectionDiagnostic(BaseModel):
    """Auditable projection decision made before canonical conversion."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    stage: str = "projection_validation"
    image_name: str = Field(min_length=1)
    reason_code: OmniDocBenchProjectionReason
    reported_width: float = Field(gt=0)
    reported_height: float = Field(gt=0)
    raster_width: int = Field(gt=0)
    raster_height: int = Field(gt=0)
    canonical_width: int = Field(gt=0)
    canonical_height: int = Field(gt=0)
    annotation_count: int = Field(ge=0)
    retained_annotation_count: int = Field(ge=0)
    ignored_annotation_count: int = Field(ge=0)
    min_annotation_x: float = 0.0
    min_annotation_y: float = 0.0
    max_annotation_x: float
    max_annotation_y: float
    corrected: bool = False


class OmniDocBenchConversionWarning(BaseModel):
    """One deterministic, machine-readable non-fatal converter warning."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    reason_code: OmniDocBenchConversionReason
    message: str = Field(min_length=1)
    annotation_indices: list[int] = Field(default_factory=list)
    annotation_ids: list[Any] = Field(default_factory=list)
    details: dict[str, Any] = Field(default_factory=dict)


class OmniDocBenchPageConversionReport(BaseModel):
    """The complete projection and preservation contract for one source page."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = "0.2"
    converter_contract: str = "omnidocbench-to-canonical-ir/0.2"
    record_index: int = Field(ge=0)
    image_name: str = Field(min_length=1)
    output_name: str = Field(min_length=1)
    annotation_count: int = Field(ge=0)
    projected_element_count: int = Field(ge=0)
    ignored_count: int = Field(ge=0)
    audited_annotation_count: int = Field(ge=0)
    relation_count: int = Field(ge=0)
    text_hash_match: bool
    relation_hash_match: bool
    page_geometry_valid: bool
    annotation_ids_unique: bool
    reading_orders_unique: bool
    reading_order_complete: bool
    source_text_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    output_text_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    annotation_input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    raster_input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    canonical_output_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    reason_codes: list[str] = Field(default_factory=list)
    warnings: list[OmniDocBenchConversionWarning] = Field(default_factory=list)
    projection: OmniDocBenchProjectionDiagnostic


class OmniDocBenchDatasetConversionReport(BaseModel):
    """Deterministic report for an entire OmniDocBench conversion run."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = "0.2"
    converter_contract: str = "omnidocbench-to-canonical-ir/0.2"
    dataset_revision: str = Field(min_length=1)
    annotation_file_name: str = Field(min_length=1)
    annotation_file_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    page_count: int = Field(ge=0)
    annotation_count: int = Field(ge=0)
    projected_element_count: int = Field(ge=0)
    ignored_count: int = Field(ge=0)
    warning_count: int = Field(ge=0)
    pages: list[OmniDocBenchPageConversionReport] = Field(default_factory=list)


class OmniDocBenchOracleContractError(ValueError):
    """Raised when a source annotation violates the public conversion contract."""

    def __init__(
        self,
        reason_code: OmniDocBenchConversionReason,
        message: str,
        *,
        annotation_index: int | None = None,
    ) -> None:
        self.reason_code = reason_code
        self.annotation_index = annotation_index
        prefix = reason_code.value
        if annotation_index is not None:
            prefix += f" at layout_dets[{annotation_index}]"
        super().__init__(f"{prefix}: {message}")


class OmniDocBenchOracleConversionError(ValueError):
    """Raised when the oracle coordinate transform cannot be proved safely."""

    def __init__(self, diagnostic: OmniDocBenchProjectionDiagnostic) -> None:
        self.diagnostic = diagnostic
        super().__init__(
            f"{diagnostic.reason_code}: {diagnostic.image_name} has reported "
            f"{diagnostic.reported_width:g}x{diagnostic.reported_height:g}, raster "
            f"{diagnostic.raster_width}x{diagnostic.raster_height}, and annotation "
            f"extent {diagnostic.max_annotation_x:g}x{diagnostic.max_annotation_y:g}"
        )


class OmniDocBenchOracleConversion(BaseModel):
    """One converted document plus the projection proof used to create it."""

    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)

    document: Document
    diagnostic: OmniDocBenchProjectionDiagnostic
    report: OmniDocBenchPageConversionReport


__all__ = [
    "OmniDocBenchConversionReason",
    "OmniDocBenchConversionWarning",
    "OmniDocBenchDatasetConversionReport",
    "OmniDocBenchOracleContractError",
    "OmniDocBenchOracleConversion",
    "OmniDocBenchOracleConversionError",
    "OmniDocBenchPageConversionReport",
    "OmniDocBenchProjectionDiagnostic",
    "OmniDocBenchProjectionReason",
]
