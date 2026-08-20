"""Hard-invariant checks and constraint-plan authorization for correction candidates."""

from __future__ import annotations

import math
from collections.abc import Sequence

from docreconstruct.evaluation.render_diff import RenderDiffKind
from docreconstruct.reconstruction.constraint_plan import (
    ConstraintPlan,
    ObjectFlowMode,
    SoftConstraintKind,
)

from .actions import CorrectionAction, CorrectionActionType
from .models import (
    CandidateAssessment,
    CorrectionObjective,
    HardInvariantViolation,
)

_PAGE_SIZE_TOLERANCE_POINTS = 1e-6


def _semantic_authority_preserved(
    plan: ConstraintPlan,
    assessment: CandidateAssessment,
) -> bool:
    hard = plan.hard_constraints
    return (
        assessment.content_authority_sha256 == hard.content_authority_sha256
        and assessment.layout_authority_sha256 == hard.layout_authority_sha256
        and assessment.object_content_sha256 == hard.object_content_sha256
        and assessment.object_provenance_sha256 == hard.object_provenance_sha256
        and assessment.object_ids == hard.required_object_ids
    )


def _missing_object_ids(
    plan: ConstraintPlan,
    assessment: CandidateAssessment,
) -> tuple[str, ...]:
    required = set(plan.hard_constraints.required_object_ids)
    missing = required - set(assessment.object_ids)
    missing.update(assessment.missing_object_ids)
    missing.update(assessment.source_deleted_object_ids)
    return tuple(sorted(missing, key=str.casefold))


def _page_geometry_counts(
    plan: ConstraintPlan,
    assessment: CandidateAssessment,
) -> tuple[int, int]:
    expected = plan.hard_constraints.page_sizes
    actual = assessment.page_sizes
    count_violation = int(len(expected) != len(actual))
    size_violations = abs(len(expected) - len(actual))
    for left, right in zip(expected, actual, strict=False):
        if not (
            math.isclose(
                left.width,
                right.width,
                rel_tol=0.0,
                abs_tol=_PAGE_SIZE_TOLERANCE_POINTS,
            )
            and math.isclose(
                left.height,
                right.height,
                rel_tol=0.0,
                abs_tol=_PAGE_SIZE_TOLERANCE_POINTS,
            )
        ):
            size_violations += 1
    return count_violation, size_violations


def objective_from_assessment(
    plan: ConstraintPlan,
    assessment: CandidateAssessment,
    *,
    correction_count: int,
) -> CorrectionObjective:
    """Derive non-spoofable priority fields from measured facts and the hard plan."""

    count_violation, size_violations = _page_geometry_counts(plan, assessment)
    clipping_count = sum(
        diagnostic.kind is RenderDiffKind.CLIPPING_OVERFLOW
        for diagnostic in assessment.render_diff.diagnostics
    )
    return CorrectionObjective(
        semantic_authority_preserved=_semantic_authority_preserved(plan, assessment),
        missing_content_count=len(_missing_object_ids(plan, assessment)),
        page_geometry_violation_count=count_violation + size_violations,
        clipping_overflow_count=clipping_count,
        structure_score=assessment.structure_score,
        layout_score=assessment.layout_score,
        visual_score=assessment.visual_score,
        editability_score=assessment.editability_score,
        correction_count=correction_count,
    )


def hard_invariant_violations(
    plan: ConstraintPlan,
    assessment: CandidateAssessment,
    *,
    current_objective: CorrectionObjective | None,
) -> tuple[HardInvariantViolation, ...]:
    """Return every reason a candidate is ineligible, regardless of visual gain."""

    violations: set[HardInvariantViolation] = set()
    hard = plan.hard_constraints
    if assessment.constraint_plan_fingerprint != plan.fingerprint:
        violations.add(HardInvariantViolation.PLAN_FINGERPRINT)
    if (
        assessment.content_authority_sha256 != hard.content_authority_sha256
        or assessment.layout_authority_sha256 != hard.layout_authority_sha256
    ):
        violations.add(HardInvariantViolation.AUTHORITY_HASH)
    if assessment.object_content_sha256 != hard.object_content_sha256:
        violations.add(HardInvariantViolation.OBJECT_CONTENT)
    if assessment.object_provenance_sha256 != hard.object_provenance_sha256:
        violations.add(HardInvariantViolation.OBJECT_PROVENANCE)
    if assessment.object_ids != hard.required_object_ids:
        violations.add(HardInvariantViolation.OBJECT_IDENTITY)
    if _missing_object_ids(plan, assessment):
        violations.add(HardInvariantViolation.MISSING_CONTENT)
    if assessment.source_deleted_object_ids:
        violations.add(HardInvariantViolation.SOURCE_DELETION)
    count_violation, size_violations = _page_geometry_counts(plan, assessment)
    if count_violation:
        violations.add(HardInvariantViolation.PAGE_COUNT)
    if size_violations:
        violations.add(HardInvariantViolation.PAGE_SIZE)
    if (
        assessment.render_diff.reference_page_count != hard.page_count
        or assessment.render_diff.pages_compared != hard.page_count
    ):
        violations.add(HardInvariantViolation.RENDER_REFERENCE)
    if assessment.full_page_raster_pages:
        violations.add(HardInvariantViolation.FULL_PAGE_RASTER)
    required_editable = {
        item.object_id for page in plan.pages for item in page.objects if item.editable_required
    }
    native_editable = set(assessment.native_editable_object_ids)
    if not required_editable.issubset(native_editable):
        violations.add(HardInvariantViolation.REQUIRED_EDITABILITY)
    if required_editable.intersection(assessment.raster_substituted_object_ids):
        violations.add(HardInvariantViolation.RASTER_SUBSTITUTION)
    if (
        current_objective is not None
        and assessment.editability_score < current_objective.editability_score
    ):
        violations.add(HardInvariantViolation.EDITABILITY_REGRESSION)
    return tuple(sorted(violations, key=lambda item: item.value))


_OBJECT_SOFT_RULE: dict[CorrectionActionType, SoftConstraintKind] = {
    CorrectionActionType.ADJUST_PARAGRAPH_SPACING: SoftConstraintKind.PARAGRAPH_SPACING,
    CorrectionActionType.ADJUST_LINE_SPACING: SoftConstraintKind.LINE_SPACING,
    CorrectionActionType.ADJUST_FONT_SIZE: SoftConstraintKind.FONT_SIZE,
    CorrectionActionType.SET_TABLE_GRID_WIDTHS: SoftConstraintKind.TABLE_WIDTH,
    CorrectionActionType.SET_ROW_HEIGHT_POLICY: SoftConstraintKind.TABLE_WIDTH,
    CorrectionActionType.CHANGE_IMAGE_CROP: SoftConstraintKind.IMAGE_CROP,
    CorrectionActionType.CHANGE_ANCHOR: SoftConstraintKind.ANCHOR_OFFSET,
    CorrectionActionType.INSERT_EXPLICIT_PAGE_BREAK: SoftConstraintKind.PAGE_BREAK_BEHAVIOR,
    CorrectionActionType.CHANGE_KEEP_WITH_NEXT: SoftConstraintKind.KEEP_WITH_NEXT,
}


def actions_authorized_by_plan(
    plan: ConstraintPlan,
    assessment: CandidateAssessment,
    actions: Sequence[CorrectionAction],
) -> bool:
    """Authorize one bounded constraint group; page-size authority is never mutable."""

    if not actions or len({action.action_type for action in actions}) != 1:
        return False
    if len({action.fingerprint for action in actions}) != len(actions):
        return False
    by_id = {item.object_id: item for page in plan.pages for item in page.objects}
    page_by_object = {item.object_id: page for page in plan.pages for item in page.objects}
    known_diagnostics = {item.diagnostic_id for item in assessment.render_diff.diagnostics}
    for action in actions:
        if not set(action.object_ids).issubset(by_id):
            return False
        referenced = set(action.predicted_effect.diagnostic_ids)
        if not referenced.issubset(known_diagnostics):
            return False
        if action.action_type is CorrectionActionType.SET_PAGE_SIZE:
            return False
        pages = {page_by_object[object_id].page_number for object_id in action.object_ids}
        if len(pages) != 1:
            return False
        page = plan.pages[next(iter(pages)) - 1]
        if action.action_type is CorrectionActionType.ADJUST_MARGIN:
            if SoftConstraintKind.MARGIN not in page.soft_constraints:
                return False
            after = action.after
            assert after.margin_left is not None and after.margin_right is not None
            assert after.margin_top is not None and after.margin_bottom is not None
            if (
                after.margin_left + after.margin_right >= page.page_size.width
                or after.margin_top + after.margin_bottom >= page.page_size.height
            ):
                return False
            continue
        if action.action_type is CorrectionActionType.SET_COLUMN_WIDTHS:
            widths = action.after.column_widths
            if widths is None or len(widths) != len(page.columns):
                return False
            if any(
                not column.min_width <= width <= column.max_width
                for column, width in zip(page.columns, widths, strict=True)
            ):
                return False
            available = page.page_size.width - page.margins.left - page.margins.right
            if sum(widths) > available + 1e-9:
                return False
            continue
        if action.action_type is CorrectionActionType.ADJUST_GUTTER:
            if SoftConstraintKind.COLUMN_GUTTER not in page.soft_constraints:
                return False
            if action.after.gutter is None or action.after.gutter >= page.page_size.width:
                return False
            continue
        soft_rule = _OBJECT_SOFT_RULE.get(action.action_type)
        if soft_rule is None or any(
            soft_rule not in by_id[object_id].soft_constraints for object_id in action.object_ids
        ):
            return False
        objects = [by_id[object_id] for object_id in action.object_ids]
        if action.action_type in {
            CorrectionActionType.SET_TABLE_GRID_WIDTHS,
            CorrectionActionType.SET_ROW_HEIGHT_POLICY,
        }:
            if len(objects) != 1 or objects[0].flow_mode is not ObjectFlowMode.NATIVE_TABLE:
                return False
            widths = action.after.table_grid_widths
            if (
                widths is not None
                and not objects[0].min_width <= sum(widths) <= objects[0].max_width
            ):
                return False
        if action.action_type in {
            CorrectionActionType.CHANGE_IMAGE_CROP,
            CorrectionActionType.CHANGE_ANCHOR,
        } and (len(objects) != 1 or objects[0].flow_mode is not ObjectFlowMode.INLINE_ASSET):
            return False
    return True


__all__ = [
    "actions_authorized_by_plan",
    "hard_invariant_violations",
    "objective_from_assessment",
]
