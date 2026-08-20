"""Strict, renderer-neutral constraints for bounded document correction."""

from __future__ import annotations

import math
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from docreconstruct.ir import BBox

from .canonical import stable_digest

_SHA256_PATTERN = r"^[0-9a-f]{64}$"


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class HardConstraintKind(StrEnum):
    """Safety invariants that an automatic correction may never weaken."""

    AUTHORITY_HASH = "authority_hash"
    OBJECT_ID = "object_id"
    OBJECT_PROVENANCE = "object_provenance"
    PAGE_COUNT = "page_count"
    PAGE_SIZE = "page_size"
    NO_SOURCE_DELETION = "no_source_deletion"
    NO_FULL_PAGE_RASTER = "no_full_page_raster"
    PRESERVE_NATIVE_EDITABILITY = "preserve_native_editability"
    NO_RASTER_SUBSTITUTION = "no_raster_substitution"


class SoftConstraintKind(StrEnum):
    """Bounded layout preferences that a later correction engine may tune."""

    MARGIN = "margin"
    FONT_SIZE = "font_size"
    LINE_SPACING = "line_spacing"
    PARAGRAPH_SPACING = "paragraph_spacing"
    TABLE_WIDTH = "table_width"
    COLUMN_GUTTER = "column_gutter"
    IMAGE_CROP = "image_crop"
    ANCHOR_OFFSET = "anchor_offset"
    KEEP_WITH_NEXT = "keep_with_next"
    PAGE_BREAK_BEHAVIOR = "page_break_behavior"


class ObjectFlowMode(StrEnum):
    BLOCK = "block_flow"
    INLINE_ASSET = "inline_asset"
    NATIVE_TABLE = "native_table"
    NATIVE_MATH = "native_math"


def _validate_canonical_rules(values: tuple[StrEnum, ...], *, label: str) -> None:
    serialized = tuple(value.value for value in values)
    if len(serialized) != len(set(serialized)):
        raise ValueError(f"{label} must not contain duplicates")
    if serialized != tuple(sorted(serialized)):
        raise ValueError(f"{label} must use canonical lexical order")


class Size(_FrozenModel):
    """Physical size in PDF points."""

    width: float = Field(gt=0)
    height: float = Field(gt=0)

    @field_validator("width", "height")
    @classmethod
    def dimensions_must_be_finite(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("physical dimensions must be finite")
        return value


class Insets(_FrozenModel):
    """Non-negative physical page insets in PDF points."""

    top: float = Field(ge=0)
    right: float = Field(ge=0)
    bottom: float = Field(ge=0)
    left: float = Field(ge=0)

    @field_validator("top", "right", "bottom", "left")
    @classmethod
    def insets_must_be_finite(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("page insets must be finite")
        return value


class ObjectProvenance(_FrozenModel):
    """Auditable source identity retained for one planned native object."""

    block_index: int = Field(ge=0)
    geometry_source: str = Field(min_length=1)
    evidence_providers: tuple[str, ...] = ()
    evidence_element_ids: tuple[str, ...] = ()

    @field_validator("geometry_source")
    @classmethod
    def geometry_source_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("geometry source must not be blank")
        return value

    @field_validator("evidence_providers", "evidence_element_ids")
    @classmethod
    def provenance_values_must_be_canonical(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item.strip() for item in value):
            raise ValueError("provenance identifiers must not be blank")
        if value != tuple(sorted(set(value))):
            raise ValueError("provenance identifiers must be unique and lexically ordered")
        return value

    @property
    def fingerprint(self) -> str:
        return stable_digest(self.model_dump(mode="json"))


class ConstraintPlanProvenance(_FrozenModel):
    """Versioned identity of the immutable input-to-constraint mapping."""

    adapter: Literal["hybrid_layout_plan"] = "hybrid_layout_plan"
    adapter_version: Literal["1.0"] = "1.0"
    content_authority_sha256: str = Field(pattern=_SHA256_PATTERN)
    layout_authority_sha256: str = Field(pattern=_SHA256_PATTERN)
    evidence_authority_sha256: tuple[str, ...] = ()
    hybrid_plan_sha256: str = Field(pattern=_SHA256_PATTERN)
    prepared_render_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    mapping_sha256: str = Field(pattern=_SHA256_PATTERN)

    @field_validator("evidence_authority_sha256")
    @classmethod
    def evidence_hashes_must_be_canonical(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(
            len(item) != 64 or any(ch not in "0123456789abcdef" for ch in item) for item in value
        ):
            raise ValueError("evidence authority hashes must be lowercase SHA-256 digests")
        if value != tuple(sorted(set(value))):
            raise ValueError("evidence authority hashes must be unique and lexically ordered")
        return value


class HardConstraintSet(_FrozenModel):
    """Exact authority and anti-cheating contract for the whole document."""

    content_authority_sha256: str = Field(pattern=_SHA256_PATTERN)
    layout_authority_sha256: str = Field(pattern=_SHA256_PATTERN)
    required_object_ids: tuple[str, ...] = Field(min_length=1)
    object_content_sha256: str = Field(pattern=_SHA256_PATTERN)
    object_provenance_sha256: str = Field(pattern=_SHA256_PATTERN)
    page_count: int = Field(ge=1)
    page_sizes: tuple[Size, ...] = Field(min_length=1)
    source_deletion_allowed: Literal[False] = False
    full_page_raster_allowed: Literal[False] = False
    editability_downgrade_allowed: Literal[False] = False
    rules: tuple[HardConstraintKind, ...]

    @field_validator("required_object_ids")
    @classmethod
    def object_ids_must_be_unique(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item.strip() for item in value):
            raise ValueError("required object IDs must not be blank")
        if len(value) != len(set(value)):
            raise ValueError("required object IDs must be unique")
        return value

    @field_validator("rules")
    @classmethod
    def rules_must_be_complete(
        cls, value: tuple[HardConstraintKind, ...]
    ) -> tuple[HardConstraintKind, ...]:
        _validate_canonical_rules(value, label="hard constraint rules")
        missing = set(HardConstraintKind) - set(value)
        if missing:
            names = ", ".join(sorted(item.value for item in missing))
            raise ValueError(f"hard constraint set is missing required rule(s): {names}")
        return value

    @model_validator(mode="after")
    def page_sizes_must_match_page_count(self) -> HardConstraintSet:
        if len(self.page_sizes) != self.page_count:
            raise ValueError("hard page sizes must match the declared page count")
        return self


class ObjectConstraint(_FrozenModel):
    """Bounded correction envelope for one Markdown-authoritative object."""

    object_id: str = Field(min_length=1)
    page_number: int = Field(ge=1)
    content_kind: str = Field(min_length=1)
    authority_content_sha256: str = Field(pattern=_SHA256_PATTERN)
    preferred_bbox: BBox | None = None
    min_width: float = Field(gt=0)
    max_width: float = Field(gt=0)
    preferred_height: float | None = Field(default=None, gt=0)
    flow_mode: ObjectFlowMode
    keep_with_next: bool
    editable_required: bool
    column_id: str
    provenance: ObjectProvenance
    hard_constraints: tuple[HardConstraintKind, ...]
    soft_constraints: tuple[SoftConstraintKind, ...]

    @field_validator("object_id", "content_kind", "column_id")
    @classmethod
    def identifiers_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("constraint identifiers must not be blank")
        return value

    @field_validator("min_width", "max_width", "preferred_height")
    @classmethod
    def object_dimensions_must_be_finite(cls, value: float | None) -> float | None:
        if value is not None and not math.isfinite(value):
            raise ValueError("object dimensions must be finite")
        return value

    @field_validator("hard_constraints")
    @classmethod
    def hard_rules_must_be_canonical(
        cls, value: tuple[HardConstraintKind, ...]
    ) -> tuple[HardConstraintKind, ...]:
        _validate_canonical_rules(value, label="object hard constraints")
        return value

    @field_validator("soft_constraints")
    @classmethod
    def soft_rules_must_be_canonical(
        cls, value: tuple[SoftConstraintKind, ...]
    ) -> tuple[SoftConstraintKind, ...]:
        _validate_canonical_rules(value, label="object soft constraints")
        return value

    @model_validator(mode="after")
    def validate_safety_and_bounds(self) -> ObjectConstraint:
        if self.min_width > self.max_width:
            raise ValueError("object minimum width must not exceed maximum width")
        if self.preferred_bbox is not None:
            if self.preferred_bbox.width <= 0 or self.preferred_bbox.height <= 0:
                raise ValueError("preferred object box must have positive area")
            if not self.min_width <= self.preferred_bbox.width <= self.max_width:
                raise ValueError("preferred object width must lie inside its correction bounds")
            if self.preferred_height is not None and not math.isclose(
                self.preferred_height,
                self.preferred_bbox.height,
                rel_tol=0.0,
                abs_tol=1e-9,
            ):
                raise ValueError("preferred height must equal the preferred object box height")
        required = {
            HardConstraintKind.AUTHORITY_HASH,
            HardConstraintKind.OBJECT_ID,
            HardConstraintKind.OBJECT_PROVENANCE,
            HardConstraintKind.NO_SOURCE_DELETION,
        }
        if self.editable_required:
            required.update(
                {
                    HardConstraintKind.PRESERVE_NATIVE_EDITABILITY,
                    HardConstraintKind.NO_RASTER_SUBSTITUTION,
                }
            )
        missing = required - set(self.hard_constraints)
        if missing:
            names = ", ".join(sorted(item.value for item in missing))
            raise ValueError(f"object constraint is missing hard rule(s): {names}")
        return self


class ColumnConstraint(_FrozenModel):
    """Physical column envelope and its deterministically assigned objects."""

    column_id: str = Field(min_length=1)
    preferred_bbox: BBox
    min_width: float = Field(gt=0)
    max_width: float = Field(gt=0)
    preferred_gutter_after: float | None = Field(default=None, ge=0)
    object_ids: tuple[str, ...]
    provenance: Literal["scan_metadata", "content_bbox_fallback"]
    soft_constraints: tuple[SoftConstraintKind, ...]

    @field_validator("column_id")
    @classmethod
    def column_id_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("column ID must not be blank")
        return value

    @field_validator("min_width", "max_width", "preferred_gutter_after")
    @classmethod
    def column_dimensions_must_be_finite(cls, value: float | None) -> float | None:
        if value is not None and not math.isfinite(value):
            raise ValueError("column dimensions must be finite")
        return value

    @field_validator("object_ids")
    @classmethod
    def column_object_ids_must_be_unique(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("column object IDs must be unique")
        return value

    @field_validator("soft_constraints")
    @classmethod
    def soft_rules_must_be_canonical(
        cls, value: tuple[SoftConstraintKind, ...]
    ) -> tuple[SoftConstraintKind, ...]:
        _validate_canonical_rules(value, label="column soft constraints")
        if SoftConstraintKind.COLUMN_GUTTER not in value:
            raise ValueError("column constraints must expose the gutter as a soft constraint")
        return value

    @model_validator(mode="after")
    def validate_width_bounds(self) -> ColumnConstraint:
        if self.preferred_bbox.width <= 0 or self.preferred_bbox.height <= 0:
            raise ValueError("preferred column box must have positive area")
        if not self.min_width <= self.preferred_bbox.width <= self.max_width:
            raise ValueError("preferred column width must lie inside its correction bounds")
        return self


class PageConstraintPlan(_FrozenModel):
    """One immutable physical page and all correction-safe object envelopes."""

    page_number: int = Field(ge=1)
    page_size: Size
    margins: Insets
    columns: tuple[ColumnConstraint, ...] = Field(min_length=1)
    objects: tuple[ObjectConstraint, ...]
    hard_constraints: tuple[HardConstraintKind, ...]
    soft_constraints: tuple[SoftConstraintKind, ...]

    @field_validator("hard_constraints")
    @classmethod
    def hard_rules_must_be_canonical(
        cls, value: tuple[HardConstraintKind, ...]
    ) -> tuple[HardConstraintKind, ...]:
        _validate_canonical_rules(value, label="page hard constraints")
        required = {HardConstraintKind.PAGE_SIZE, HardConstraintKind.NO_FULL_PAGE_RASTER}
        if not required.issubset(value):
            raise ValueError("page constraints must preserve size and forbid full-page raster")
        return value

    @field_validator("soft_constraints")
    @classmethod
    def soft_rules_must_be_canonical(
        cls, value: tuple[SoftConstraintKind, ...]
    ) -> tuple[SoftConstraintKind, ...]:
        _validate_canonical_rules(value, label="page soft constraints")
        return value

    @model_validator(mode="after")
    def validate_page_geometry_and_membership(self) -> PageConstraintPlan:
        if self.margins.left + self.margins.right >= self.page_size.width:
            raise ValueError("horizontal margins must leave positive page content width")
        if self.margins.top + self.margins.bottom >= self.page_size.height:
            raise ValueError("vertical margins must leave positive page content height")
        column_ids = [column.column_id for column in self.columns]
        if len(column_ids) != len(set(column_ids)):
            raise ValueError("column IDs must be unique within a page")
        object_ids = [item.object_id for item in self.objects]
        if len(object_ids) != len(set(object_ids)):
            raise ValueError("object IDs must be unique within a page")
        if any(item.page_number != self.page_number for item in self.objects):
            raise ValueError("object page number must match its containing page")
        assigned = [object_id for column in self.columns for object_id in column.object_ids]
        if len(assigned) != len(set(assigned)) or set(assigned) != set(object_ids):
            raise ValueError("every page object must belong to exactly one column")
        by_object = {item.object_id: item for item in self.objects}
        if any(
            by_object[object_id].column_id != column.column_id
            for column in self.columns
            for object_id in column.object_ids
        ):
            raise ValueError("object column references must match column membership")
        for box in [
            *(column.preferred_bbox for column in self.columns),
            *(item.preferred_bbox for item in self.objects if item.preferred_bbox is not None),
        ]:
            if (
                box.x0 < 0
                or box.y0 < 0
                or box.x1 > self.page_size.width
                or box.y1 > self.page_size.height
            ):
                raise ValueError("preferred constraint box must remain inside its physical page")
        return self


class ConstraintPlan(_FrozenModel):
    """Document-wide hard contract plus page-local bounded preferences."""

    schema_version: Literal["1.0"] = "1.0"
    provenance: ConstraintPlanProvenance
    hard_constraints: HardConstraintSet
    pages: tuple[PageConstraintPlan, ...] = Field(min_length=1)
    warnings: tuple[str, ...] = ()

    @field_validator("warnings")
    @classmethod
    def warnings_must_be_canonical(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if value != tuple(sorted(set(value))):
            raise ValueError("constraint warnings must be unique and lexically ordered")
        return value

    @model_validator(mode="after")
    def validate_hard_contract(self) -> ConstraintPlan:
        numbers = tuple(page.page_number for page in self.pages)
        expected_numbers = tuple(range(1, len(self.pages) + 1))
        if numbers != expected_numbers:
            raise ValueError("constraint pages must be consecutive and ordered from one")
        if len(self.pages) != self.hard_constraints.page_count:
            raise ValueError("constraint pages must match the hard page count")
        if tuple(page.page_size for page in self.pages) != self.hard_constraints.page_sizes:
            raise ValueError("constraint page sizes must match the hard physical sizes")
        objects = sorted(
            (item for page in self.pages for item in page.objects),
            key=lambda item: (item.provenance.block_index, item.object_id),
        )
        ids = tuple(item.object_id for item in objects)
        if ids != self.hard_constraints.required_object_ids:
            raise ValueError("constraint objects must exactly preserve all source object IDs")
        provenance_payload = [
            {"object_id": item.object_id, "provenance": item.provenance.model_dump(mode="json")}
            for item in objects
        ]
        content_payload = [
            {
                "object_id": item.object_id,
                "authority_content_sha256": item.authority_content_sha256,
            }
            for item in objects
        ]
        if stable_digest(content_payload) != self.hard_constraints.object_content_sha256:
            raise ValueError("constraint object content does not match the hard authority digest")
        if stable_digest(provenance_payload) != self.hard_constraints.object_provenance_sha256:
            raise ValueError("constraint object provenance does not match the hard digest")
        if (
            self.provenance.content_authority_sha256
            != self.hard_constraints.content_authority_sha256
            or self.provenance.layout_authority_sha256
            != self.hard_constraints.layout_authority_sha256
        ):
            raise ValueError("constraint provenance must retain the hard authority hashes")
        mapping_payload = [page.model_dump(mode="json") for page in self.pages]
        if stable_digest(mapping_payload) != self.provenance.mapping_sha256:
            raise ValueError("constraint page mapping does not match its provenance digest")
        return self

    @property
    def fingerprint(self) -> str:
        """Fingerprint every hard rule, provenance record, and bounded preference."""

        return stable_digest(self.model_dump(mode="json"))


__all__ = [
    "ColumnConstraint",
    "ConstraintPlan",
    "ConstraintPlanProvenance",
    "HardConstraintKind",
    "HardConstraintSet",
    "Insets",
    "ObjectConstraint",
    "ObjectFlowMode",
    "ObjectProvenance",
    "PageConstraintPlan",
    "Size",
    "SoftConstraintKind",
]
