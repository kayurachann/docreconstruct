"""Portable models for cost-aware document and region routing."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from docreconstruct.ir import BBox, ElementType


class RoutingAction(StrEnum):
    EXTRACT = "extract"
    RETRY = "retry"
    ADJUDICATE = "adjudicate"
    PRESERVE = "preserve"


class RoutingReason(StrEnum):
    NATIVE_FIRST = "native-first"
    INITIAL_EXTRACTION = "initial-extraction"
    COMPLEX_LAYOUT = "complex-layout"
    CONTENT_SPECIALIST = "content-specialist"
    HANDWRITING = "handwriting"
    LOW_CONFIDENCE = "low-confidence"
    PROVIDER_DISAGREEMENT = "provider-disagreement"
    PRESERVE_VISUAL = "preserve-visual"
    FORCED_REPAIR = "forced-repair"


class RoutingPolicy(BaseModel):
    """Auditable provider preferences; no provider package is imported here."""

    model_config = ConfigDict(extra="forbid")

    confidence_threshold: float = Field(default=0.75, ge=0.0, le=1.0)
    native_provider: str = "native_pdf"
    ordinary_text_provider: str = "paddleocr"
    complex_layout_provider: str = "mineru"
    handwriting_provider: str = "olmocr"
    table_provider: str = "paddleocr"
    formula_provider: str = "paddleocr"
    enable_consensus_on_disagreement: bool = True
    fallback_providers: dict[str, list[str]] = Field(
        default_factory=lambda: {
            "ordinary": ["olmocr", "mineru"],
            "layout": ["paddleocr", "olmocr"],
            "handwriting": ["paddleocr", "mineru"],
            "table": ["mineru", "olmocr"],
            "formula": ["mineru", "olmocr"],
        }
    )
    relative_costs: dict[str, float] = Field(
        default_factory=lambda: {
            "native_pdf": 0.1,
            "paddleocr": 1.0,
            "mineru": 2.0,
            "olmocr": 3.0,
            "preserve_source": 0.0,
        }
    )


class RoutingTask(BaseModel):
    """One page/region action with traceable escalation choices."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    page_number: int = Field(ge=1)
    element_id: str | None = None
    bbox: BBox
    content_type: ElementType
    action: RoutingAction
    primary_provider: str
    fallback_providers: list[str] = Field(default_factory=list)
    reasons: list[RoutingReason] = Field(min_length=1)
    require_consensus: bool = False
    preserve_source_raster: bool = False
    live_executable: bool = False
    estimated_relative_cost: float = Field(ge=0.0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class RoutingPlan(BaseModel):
    """Serializable routing plan produced without executing heavyweight models."""

    model_config = ConfigDict(extra="forbid")

    document_id: str
    policy: RoutingPolicy
    tasks: list[RoutingTask]
    estimated_relative_cost: float = Field(ge=0.0)
    warnings: list[str] = Field(default_factory=list)
