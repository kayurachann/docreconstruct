"""Guard-railed render/compare/correct orchestration.

The module deliberately accepts a caller-supplied visual critic.  It does not
pretend that a first-party vision model is bundled, and it never permits a
layout correction to rewrite source text.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from docreconstruct.ir import BBox, Document, TextAlignment


class LayoutCorrection(BaseModel):
    """A restricted, auditable adjustment to one reconstructed element."""

    model_config = ConfigDict(extra="forbid")

    element_id: str = Field(min_length=1)
    page_number: int = Field(ge=1)
    reason: str = Field(min_length=1)
    dx: float = 0.0
    dy: float = 0.0
    width_delta: float = 0.0
    height_delta: float = 0.0
    font_size_delta: float = 0.0
    alignment: TextAlignment | None = None
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def must_change_layout(self) -> LayoutCorrection:
        if (
            not any((self.dx, self.dy, self.width_delta, self.height_delta, self.font_size_delta))
            and self.alignment is None
        ):
            raise ValueError("a layout correction must include at least one adjustment")
        return self


class CriticResult(BaseModel):
    """Score and adjustments produced after rendering a candidate document."""

    model_config = ConfigDict(extra="forbid")

    score: float = Field(ge=0.0, le=1.0)
    corrections: list[LayoutCorrection] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class RefinementPass(BaseModel):
    model_config = ConfigDict(extra="forbid")

    number: int = Field(ge=1)
    score_before: float = Field(ge=0.0, le=1.0)
    score_after: float = Field(ge=0.0, le=1.0)
    accepted: bool
    corrections: list[LayoutCorrection]


class RefinementResult(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    document: Document
    initial_score: float = Field(ge=0.0, le=1.0)
    final_score: float = Field(ge=0.0, le=1.0)
    passes: list[RefinementPass]


VisualCritic = Callable[[Document], CriticResult]


def apply_layout_corrections(
    document: Document, corrections: Sequence[LayoutCorrection]
) -> Document:
    """Apply geometry/style-only corrections to a deep copy of the IR."""

    revised = document.model_copy(deep=True)
    by_page = {page.number: page for page in revised.pages}
    for correction in corrections:
        page = by_page.get(correction.page_number)
        if page is None:
            raise KeyError(f"unknown page {correction.page_number}")
        element = next((item for item in page.elements if item.id == correction.element_id), None)
        if element is None:
            raise KeyError(
                f"unknown element {correction.element_id!r} on page {correction.page_number}"
            )
        old = element.bbox
        width = old.width + correction.width_delta
        height = old.height + correction.height_delta
        if width <= 0.0 or height <= 0.0:
            raise ValueError(
                f"correction for element {correction.element_id!r} on page "
                f"{correction.page_number} collapses its box to {width:g}x{height:g}"
            )
        width = min(width, page.width)
        height = min(height, page.height)
        # Clamp the translated box as a unit. Clamping the corner and the far
        # edge independently turns a move that reaches a page edge into a
        # silent resize, and a large enough move into a zero-area box.
        x0 = min(max(0.0, old.x0 + correction.dx), page.width - width)
        y0 = min(max(0.0, old.y0 + correction.dy), page.height - height)
        element.bbox = BBox(x0=x0, y0=y0, x1=x0 + width, y1=y0 + height)
        if correction.font_size_delta:
            current = element.style.font_size or 12.0
            element.style.font_size = max(1.0, current + correction.font_size_delta)
        if correction.alignment is not None:
            element.style.alignment = correction.alignment
        history = list(element.metadata.get("refinement_history", []))
        history.append(correction.model_dump(mode="json"))
        element.metadata["refinement_history"] = history
    return revised


def refine_document(
    document: Document,
    critic: VisualCritic,
    *,
    maximum_passes: int = 3,
    minimum_improvement: float = 0.001,
) -> RefinementResult:
    """Run a bounded correction loop, rolling back non-improving passes."""

    if maximum_passes < 0:
        raise ValueError("maximum_passes cannot be negative")
    baseline = critic(document)
    current = document
    current_score = baseline.score
    initial_score = baseline.score
    corrections = baseline.corrections
    passes: list[RefinementPass] = []
    for number in range(1, maximum_passes + 1):
        if not corrections:
            break
        candidate = apply_layout_corrections(current, corrections)
        evaluated = critic(candidate)
        accepted = evaluated.score >= current_score + minimum_improvement
        passes.append(
            RefinementPass(
                number=number,
                score_before=current_score,
                score_after=evaluated.score,
                accepted=accepted,
                corrections=list(corrections),
            )
        )
        if not accepted:
            break
        current = candidate
        current_score = evaluated.score
        corrections = evaluated.corrections
    return RefinementResult(
        document=current,
        initial_score=initial_score,
        final_score=current_score,
        passes=passes,
    )
