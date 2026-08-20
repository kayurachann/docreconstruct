"""Schema-validated actions available to the bounded correction engine."""

from __future__ import annotations

import math
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from docreconstruct.reconstruction.constraint_plan.canonical import stable_digest


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ObjectiveComponent(StrEnum):
    """Lexicographic objective components, in their exact priority order."""

    SEMANTIC_AUTHORITY = "semantic_authority"
    MISSING_CONTENT = "missing_content"
    PAGE_GEOMETRY = "page_geometry"
    CLIPPING_OVERFLOW = "clipping_overflow"
    STRUCTURE = "structure"
    LAYOUT = "layout"
    VISUAL = "visual"
    EDITABILITY = "editability"
    CORRECTION_COUNT = "correction_count"


OBJECTIVE_COMPONENT_ORDER = tuple(ObjectiveComponent)


class CorrectionActionType(StrEnum):
    """Closed action vocabulary; arbitrary XML or source edits are intentionally absent."""

    SET_PAGE_SIZE = "set_page_size"
    ADJUST_MARGIN = "adjust_margin"
    SET_COLUMN_WIDTHS = "set_column_widths"
    ADJUST_GUTTER = "adjust_gutter"
    ADJUST_PARAGRAPH_SPACING = "adjust_paragraph_spacing"
    ADJUST_LINE_SPACING = "adjust_line_spacing"
    ADJUST_FONT_SIZE = "adjust_font_size"
    SET_TABLE_GRID_WIDTHS = "set_table_grid_widths"
    SET_ROW_HEIGHT_POLICY = "set_row_height_policy"
    CHANGE_IMAGE_CROP = "change_image_crop"
    CHANGE_ANCHOR = "change_anchor"
    INSERT_EXPLICIT_PAGE_BREAK = "insert_explicit_page_break"
    CHANGE_KEEP_WITH_NEXT = "change_keep_with_next"


class RowHeightPolicy(StrEnum):
    AUTO = "auto"
    AT_LEAST = "at_least"
    EXACT = "exact"


class CorrectionParameters(_FrozenModel):
    """Union of bounded renderer-neutral settings used by the closed action schema."""

    page_width: float | None = None
    page_height: float | None = None
    margin_top: float | None = None
    margin_right: float | None = None
    margin_bottom: float | None = None
    margin_left: float | None = None
    column_widths: tuple[float, ...] | None = None
    gutter: float | None = None
    paragraph_space_before: float | None = None
    paragraph_space_after: float | None = None
    line_spacing: float | None = None
    font_size: float | None = None
    table_grid_widths: tuple[float, ...] | None = None
    row_height_policy: RowHeightPolicy | None = None
    row_height: float | None = None
    crop_top: float | None = None
    crop_right: float | None = None
    crop_bottom: float | None = None
    crop_left: float | None = None
    anchor_x: float | None = None
    anchor_y: float | None = None
    explicit_page_break: bool | None = None
    keep_with_next: bool | None = None

    @field_validator(
        "page_width",
        "page_height",
        "margin_top",
        "margin_right",
        "margin_bottom",
        "margin_left",
        "gutter",
        "paragraph_space_before",
        "paragraph_space_after",
        "line_spacing",
        "font_size",
        "row_height",
        "crop_top",
        "crop_right",
        "crop_bottom",
        "crop_left",
        "anchor_x",
        "anchor_y",
    )
    @classmethod
    def scalar_must_be_finite(cls, value: float | None) -> float | None:
        if value is not None and not math.isfinite(value):
            raise ValueError("correction parameters must be finite")
        return value

    @field_validator("column_widths", "table_grid_widths")
    @classmethod
    def widths_must_be_bounded(cls, value: tuple[float, ...] | None) -> tuple[float, ...] | None:
        if value is None:
            return None
        if not 1 <= len(value) <= 64:
            raise ValueError("width vectors must contain between 1 and 64 entries")
        if any(not math.isfinite(item) or item <= 0 or item > 2_000 for item in value):
            raise ValueError("width entries must be finite and lie in (0, 2000]")
        return value

    @model_validator(mode="after")
    def values_must_lie_in_closed_safety_envelope(self) -> Self:
        bounded = {
            "page_width": (1.0, 4_000.0),
            "page_height": (1.0, 4_000.0),
            "margin_top": (0.0, 288.0),
            "margin_right": (0.0, 288.0),
            "margin_bottom": (0.0, 288.0),
            "margin_left": (0.0, 288.0),
            "gutter": (0.0, 288.0),
            "paragraph_space_before": (0.0, 144.0),
            "paragraph_space_after": (0.0, 144.0),
            "line_spacing": (0.5, 3.0),
            "font_size": (4.0, 144.0),
            "row_height": (0.0, 288.0),
            "crop_top": (0.0, 0.95),
            "crop_right": (0.0, 0.95),
            "crop_bottom": (0.0, 0.95),
            "crop_left": (0.0, 0.95),
            "anchor_x": (-2_000.0, 2_000.0),
            "anchor_y": (-2_000.0, 2_000.0),
        }
        for name, (lower, upper) in bounded.items():
            value = getattr(self, name)
            if value is not None and not lower <= value <= upper:
                raise ValueError(f"{name} must lie in [{lower}, {upper}]")
        if (
            self.crop_top is not None
            and self.crop_bottom is not None
            and self.crop_top + self.crop_bottom >= 1.0
        ):
            raise ValueError("vertical crop fractions must leave visible image content")
        if (
            self.crop_left is not None
            and self.crop_right is not None
            and self.crop_left + self.crop_right >= 1.0
        ):
            raise ValueError("horizontal crop fractions must leave visible image content")
        return self

    @property
    def populated_fields(self) -> frozenset[str]:
        return frozenset(
            name for name in type(self).model_fields if getattr(self, name) is not None
        )


class PredictedEffect(_FrozenModel):
    """Auditable rationale supplied by a deterministic rule-based proposer."""

    target: ObjectiveComponent
    expected_delta: float = Field(gt=0.0, le=100.0)
    diagnostic_ids: tuple[str, ...] = ()

    @field_validator("expected_delta")
    @classmethod
    def delta_must_be_finite(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("predicted objective delta must be finite")
        return value

    @field_validator("diagnostic_ids")
    @classmethod
    def diagnostic_ids_must_be_canonical(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item.startswith("rd-") or len(item) != 19 for item in value):
            raise ValueError("predicted effects may reference only render-diff diagnostic IDs")
        if value != tuple(sorted(set(value))):
            raise ValueError("diagnostic IDs must be unique and lexically ordered")
        return value


_ACTION_FIELDS: dict[CorrectionActionType, frozenset[str]] = {
    CorrectionActionType.SET_PAGE_SIZE: frozenset({"page_width", "page_height"}),
    CorrectionActionType.ADJUST_MARGIN: frozenset(
        {"margin_top", "margin_right", "margin_bottom", "margin_left"}
    ),
    CorrectionActionType.SET_COLUMN_WIDTHS: frozenset({"column_widths"}),
    CorrectionActionType.ADJUST_GUTTER: frozenset({"gutter"}),
    CorrectionActionType.ADJUST_PARAGRAPH_SPACING: frozenset(
        {"paragraph_space_before", "paragraph_space_after"}
    ),
    CorrectionActionType.ADJUST_LINE_SPACING: frozenset({"line_spacing"}),
    CorrectionActionType.ADJUST_FONT_SIZE: frozenset({"font_size"}),
    CorrectionActionType.SET_TABLE_GRID_WIDTHS: frozenset({"table_grid_widths"}),
    CorrectionActionType.SET_ROW_HEIGHT_POLICY: frozenset({"row_height_policy", "row_height"}),
    CorrectionActionType.CHANGE_IMAGE_CROP: frozenset(
        {"crop_top", "crop_right", "crop_bottom", "crop_left"}
    ),
    CorrectionActionType.CHANGE_ANCHOR: frozenset({"anchor_x", "anchor_y"}),
    CorrectionActionType.INSERT_EXPLICIT_PAGE_BREAK: frozenset({"explicit_page_break"}),
    CorrectionActionType.CHANGE_KEEP_WITH_NEXT: frozenset({"keep_with_next"}),
}

_MAX_DELTAS: dict[CorrectionActionType, float] = {
    CorrectionActionType.SET_PAGE_SIZE: 72.0,
    CorrectionActionType.ADJUST_MARGIN: 18.0,
    CorrectionActionType.SET_COLUMN_WIDTHS: 36.0,
    CorrectionActionType.ADJUST_GUTTER: 18.0,
    CorrectionActionType.ADJUST_PARAGRAPH_SPACING: 12.0,
    CorrectionActionType.ADJUST_LINE_SPACING: 0.25,
    CorrectionActionType.ADJUST_FONT_SIZE: 2.0,
    CorrectionActionType.SET_TABLE_GRID_WIDTHS: 36.0,
    CorrectionActionType.SET_ROW_HEIGHT_POLICY: 12.0,
    CorrectionActionType.CHANGE_IMAGE_CROP: 0.10,
    CorrectionActionType.CHANGE_ANCHOR: 36.0,
}


def _numeric_deltas(before: CorrectionParameters, after: CorrectionParameters) -> list[float]:
    deltas: list[float] = []
    for name in before.populated_fields:
        left = getattr(before, name)
        right = getattr(after, name)
        if isinstance(left, tuple) and isinstance(right, tuple):
            if len(left) != len(right):
                raise ValueError("bounded width adjustments must retain vector length")
            deltas.extend(abs(float(a) - float(b)) for a, b in zip(left, right, strict=True))
        elif (
            isinstance(left, (float, int))
            and not isinstance(left, bool)
            and isinstance(right, (float, int))
            and not isinstance(right, bool)
        ):
            deltas.append(abs(float(left) - float(right)))
    return deltas


class CorrectionAction(_FrozenModel):
    """One bounded, attributable correction; free-form source/XML patches are invalid."""

    action_type: CorrectionActionType
    object_ids: tuple[str, ...] = Field(min_length=1, max_length=256)
    before: CorrectionParameters
    after: CorrectionParameters
    reason: str = Field(min_length=1, max_length=500)
    predicted_effect: PredictedEffect

    @field_validator("object_ids")
    @classmethod
    def object_ids_must_be_canonical(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item.strip() for item in value):
            raise ValueError("correction object IDs must not be blank")
        if value != tuple(sorted(set(value), key=str.casefold)):
            raise ValueError("correction object IDs must be unique and canonically ordered")
        return value

    @field_validator("reason")
    @classmethod
    def reason_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("correction reason must not be blank")
        return value

    @model_validator(mode="after")
    def action_must_match_closed_schema_and_delta(self) -> Self:
        expected = _ACTION_FIELDS[self.action_type]
        if self.before.populated_fields != expected or self.after.populated_fields != expected:
            raise ValueError(
                f"{self.action_type.value} requires exactly: {', '.join(sorted(expected))}"
            )
        if self.before == self.after:
            raise ValueError("a correction action must change at least one bounded setting")
        if self.action_type is CorrectionActionType.INSERT_EXPLICIT_PAGE_BREAK and (
            self.before.explicit_page_break is not False
            or self.after.explicit_page_break is not True
        ):
            raise ValueError("explicit page-break insertion must change false to true")
        maximum = _MAX_DELTAS.get(self.action_type)
        if maximum is not None and any(
            delta > maximum + 1e-12 for delta in _numeric_deltas(self.before, self.after)
        ):
            raise ValueError(
                f"{self.action_type.value} exceeds its per-action delta limit of {maximum}"
            )
        if self.predicted_effect.target in {
            ObjectiveComponent.SEMANTIC_AUTHORITY,
            ObjectiveComponent.MISSING_CONTENT,
            ObjectiveComponent.EDITABILITY,
            ObjectiveComponent.CORRECTION_COUNT,
        }:
            raise ValueError(
                "layout corrections may not claim authority, content, or editability edits"
            )
        return self

    @property
    def fingerprint(self) -> str:
        return stable_digest(self.model_dump(mode="json"))


__all__ = [
    "CorrectionAction",
    "CorrectionActionType",
    "CorrectionParameters",
    "OBJECTIVE_COMPONENT_ORDER",
    "ObjectiveComponent",
    "PredictedEffect",
    "RowHeightPolicy",
]
