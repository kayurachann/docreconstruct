"""Convert canonical IR into renderer-neutral, traceable instructions."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from docreconstruct.analysis import DocumentArchetype, classify_document
from docreconstruct.profiles import ReconstructionProfile, settings_for


class TargetFormat(StrEnum):
    AUTO = "auto"
    DOCX = "docx"
    HTML = "html"
    JSON = "json"
    MARKDOWN = "md"
    XLSX = "xlsx"
    PPTX = "pptx"
    PDF = "pdf"

    @classmethod
    def parse(cls, value: str | TargetFormat) -> TargetFormat:
        if isinstance(value, cls):
            return value
        normalized = value.lower().lstrip(".")
        if normalized == "markdown":
            normalized = "md"
        elif normalized == "htm":
            normalized = "html"
        return cls(normalized)


class PlannedElement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str
    page_number: int = Field(ge=1)
    order: int = Field(ge=1)
    strategy: str
    constraints: dict[str, Any] = Field(default_factory=dict)


class ReconstructionPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: TargetFormat
    profile: ReconstructionProfile
    archetype: DocumentArchetype
    layout_strategy: str
    elements: list[PlannedElement]
    warnings: list[str] = Field(default_factory=list)


def choose_output_format(document: object, requested: str | TargetFormat = "auto") -> TargetFormat:
    target = TargetFormat.parse(requested)
    if target is not TargetFormat.AUTO:
        return target
    archetype = classify_document(document)
    if archetype in {DocumentArchetype.SPREADSHEET, DocumentArchetype.FINANCIAL_STATEMENT}:
        return TargetFormat.XLSX
    if archetype in {DocumentArchetype.PRESENTATION, DocumentArchetype.BROCHURE}:
        return TargetFormat.PPTX
    return TargetFormat.DOCX


def _bbox_constraints(element: object, page: object) -> dict[str, Any]:
    bbox = getattr(element, "bbox", None)
    if bbox is None:
        return {}
    coords = (
        list(bbox)
        if isinstance(bbox, (list, tuple))
        else [getattr(bbox, key) for key in ("x0", "y0", "x1", "y1")]
    )
    width = max(float(getattr(page, "width", 1.0)), 1.0)
    height = max(float(getattr(page, "height", 1.0)), 1.0)
    return {
        "normalized_bbox": [
            float(coords[0]) / width,
            float(coords[1]) / height,
            float(coords[2]) / width,
            float(coords[3]) / height,
        ],
        "preserve_text": True,
    }


def build_plan(
    document: object,
    *,
    target: str | TargetFormat = TargetFormat.AUTO,
    profile: str | ReconstructionProfile = ReconstructionProfile.BALANCED,
) -> ReconstructionPlan:
    parsed_profile, settings = settings_for(profile)
    chosen_target = choose_output_format(document, target)
    archetype = classify_document(document)
    instructions: list[PlannedElement] = []
    for page_index, page in enumerate(getattr(document, "pages", []), start=1):
        page_number = int(getattr(page, "number", page_index))
        elements = sorted(
            getattr(page, "elements", []),
            key=lambda element: (
                getattr(element, "reading_order", None) is None,
                getattr(element, "reading_order", 0) or 0,
            ),
        )
        for fallback_order, element in enumerate(elements, start=1):
            instructions.append(
                PlannedElement(
                    source_id=str(element.id),
                    page_number=page_number,
                    order=int(getattr(element, "reading_order", None) or fallback_order),
                    strategy=settings.layout_strategy,
                    constraints=_bbox_constraints(element, page),
                )
            )
    warnings: list[str] = []
    if chosen_target in {TargetFormat.XLSX, TargetFormat.PPTX, TargetFormat.PDF}:
        warnings.append(
            f"The {chosen_target.value} renderer may be supplied by an optional plugin "
            "in this release."
        )
    return ReconstructionPlan(
        target=chosen_target,
        profile=parsed_profile,
        archetype=archetype,
        layout_strategy=settings.layout_strategy,
        elements=instructions,
        warnings=warnings,
    )
