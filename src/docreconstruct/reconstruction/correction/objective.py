"""Exact lexicographic comparison for bounded correction objectives."""

from __future__ import annotations

from .actions import ObjectiveComponent
from .models import CorrectionObjective, ObjectiveComparison


def _ordered_values(
    objective: CorrectionObjective,
) -> tuple[tuple[ObjectiveComponent, float], ...]:
    """Return higher-is-better values in the user-specified priority order."""

    return (
        (
            ObjectiveComponent.SEMANTIC_AUTHORITY,
            1.0 if objective.semantic_authority_preserved else 0.0,
        ),
        (ObjectiveComponent.MISSING_CONTENT, -float(objective.missing_content_count)),
        (
            ObjectiveComponent.PAGE_GEOMETRY,
            -float(objective.page_geometry_violation_count),
        ),
        (
            ObjectiveComponent.CLIPPING_OVERFLOW,
            -float(objective.clipping_overflow_count),
        ),
        (ObjectiveComponent.STRUCTURE, objective.structure_score),
        (ObjectiveComponent.LAYOUT, objective.layout_score),
        (ObjectiveComponent.VISUAL, objective.visual_score),
        (ObjectiveComponent.EDITABILITY, objective.editability_score),
        (ObjectiveComponent.CORRECTION_COUNT, -float(objective.correction_count)),
    )


def compare_objectives(
    candidate: CorrectionObjective,
    current: CorrectionObjective,
) -> ObjectiveComparison:
    """Compare objectives without weights or compensation across priority levels."""

    candidate_values = _ordered_values(candidate)
    current_values = _ordered_values(current)
    for (component, candidate_value), (other_component, current_value) in zip(
        candidate_values, current_values, strict=True
    ):
        if component is not other_component:  # pragma: no cover - construction invariant
            raise RuntimeError("objective component order is inconsistent")
        delta = candidate_value - current_value
        if delta != 0.0:
            return ObjectiveComparison(component=component, delta=delta, improved=delta > 0.0)
    return ObjectiveComparison(component=None, delta=0.0, improved=False)


def meets_minimum_objective_delta(
    comparison: ObjectiveComparison,
    minimum_delta: float,
) -> bool:
    """Require a material improvement in the first lexicographically decisive field."""

    return comparison.improved and comparison.delta >= minimum_delta


__all__ = ["compare_objectives", "meets_minimum_objective_delta"]
