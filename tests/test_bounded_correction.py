from __future__ import annotations

from collections.abc import Sequence

import pytest
from pydantic import ValidationError

from docreconstruct.evaluation.render_diff import (
    RenderDiffComponentScores,
    RenderDiffDiagnostic,
    RenderDiffKind,
    RenderDiffPageSummary,
    RenderDiffReport,
    RenderNormalizedBox,
    RenderPixelBox,
)
from docreconstruct.ir import BBox
from docreconstruct.reconstruction.constraint_plan import (
    ColumnConstraint,
    ConstraintPlan,
    ConstraintPlanProvenance,
    HardConstraintKind,
    HardConstraintSet,
    Insets,
    ObjectConstraint,
    ObjectFlowMode,
    ObjectProvenance,
    PageConstraintPlan,
    Size,
    SoftConstraintKind,
)
from docreconstruct.reconstruction.constraint_plan.canonical import stable_digest
from docreconstruct.reconstruction.correction import (
    OBJECTIVE_COMPONENT_ORDER,
    CandidateAssessment,
    CandidateRenderCache,
    CorrectionAction,
    CorrectionActionType,
    CorrectionLimits,
    CorrectionObjective,
    CorrectionParameters,
    HardInvariantViolation,
    IterationDecision,
    ObjectiveComponent,
    PredictedEffect,
    TerminalReason,
    candidate_sha256,
    compare_objectives,
    run_bounded_correction,
)
from docreconstruct.reconstruction.correction.invariants import actions_authorized_by_plan

_CONTENT_SHA = "a" * 64
_LAYOUT_SHA = "b" * 64


def _rules(values: Sequence) -> tuple:
    return tuple(sorted(values, key=lambda item: item.value))


def _plan() -> ConstraintPlan:
    provenance = ObjectProvenance(block_index=0, geometry_source="oracle")
    item = ObjectConstraint(
        object_id="paragraph-1",
        page_number=1,
        content_kind="paragraph",
        authority_content_sha256="c" * 64,
        preferred_bbox=BBox(x0=50, y0=80, x1=550, y1=180),
        min_width=375,
        max_width=625,
        preferred_height=100,
        flow_mode=ObjectFlowMode.BLOCK,
        keep_with_next=False,
        editable_required=True,
        column_id="page-1-column-1",
        provenance=provenance,
        hard_constraints=_rules(
            {
                HardConstraintKind.AUTHORITY_HASH,
                HardConstraintKind.OBJECT_ID,
                HardConstraintKind.OBJECT_PROVENANCE,
                HardConstraintKind.NO_SOURCE_DELETION,
                HardConstraintKind.PRESERVE_NATIVE_EDITABILITY,
                HardConstraintKind.NO_RASTER_SUBSTITUTION,
            }
        ),
        soft_constraints=_rules(
            {
                SoftConstraintKind.FONT_SIZE,
                SoftConstraintKind.KEEP_WITH_NEXT,
                SoftConstraintKind.LINE_SPACING,
                SoftConstraintKind.PAGE_BREAK_BEHAVIOR,
                SoftConstraintKind.PARAGRAPH_SPACING,
            }
        ),
    )
    page = PageConstraintPlan(
        page_number=1,
        page_size=Size(width=600, height=800),
        margins=Insets(top=50, right=50, bottom=50, left=50),
        columns=(
            ColumnConstraint(
                column_id="page-1-column-1",
                preferred_bbox=BBox(x0=50, y0=50, x1=550, y1=750),
                min_width=375,
                max_width=625,
                object_ids=("paragraph-1",),
                provenance="content_bbox_fallback",
                soft_constraints=(SoftConstraintKind.COLUMN_GUTTER,),
            ),
        ),
        objects=(item,),
        hard_constraints=_rules(
            {HardConstraintKind.NO_FULL_PAGE_RASTER, HardConstraintKind.PAGE_SIZE}
        ),
        soft_constraints=_rules(
            {
                SoftConstraintKind.COLUMN_GUTTER,
                SoftConstraintKind.MARGIN,
                SoftConstraintKind.PAGE_BREAK_BEHAVIOR,
            }
        ),
    )
    content_payload = [
        {"object_id": item.object_id, "authority_content_sha256": item.authority_content_sha256}
    ]
    provenance_payload = [
        {"object_id": item.object_id, "provenance": provenance.model_dump(mode="json")}
    ]
    hard = HardConstraintSet(
        content_authority_sha256=_CONTENT_SHA,
        layout_authority_sha256=_LAYOUT_SHA,
        required_object_ids=("paragraph-1",),
        object_content_sha256=stable_digest(content_payload),
        object_provenance_sha256=stable_digest(provenance_payload),
        page_count=1,
        page_sizes=(page.page_size,),
        rules=_rules(HardConstraintKind),
    )
    source = ConstraintPlanProvenance(
        content_authority_sha256=_CONTENT_SHA,
        layout_authority_sha256=_LAYOUT_SHA,
        hybrid_plan_sha256="d" * 64,
        mapping_sha256=stable_digest([page.model_dump(mode="json")]),
    )
    return ConstraintPlan(provenance=source, hard_constraints=hard, pages=(page,))


def _render_diff(*, candidate_pages: int = 1, clipping: int = 0) -> RenderDiffReport:
    summaries = tuple(
        RenderDiffPageSummary(
            page_number=index + 1,
            reference_width=600,
            reference_height=800,
            candidate_width=600,
            candidate_height=800,
            reference_foreground_pixels=100,
            candidate_foreground_pixels=100,
            missing_difference_pixels=0,
            extra_difference_pixels=0,
            reference_components=1,
            candidate_components=1,
        )
        for index in range(min(1, candidate_pages))
    )
    diagnostics = tuple(
        RenderDiffDiagnostic(
            diagnostic_id=f"rd-{index:016x}",
            kind=RenderDiffKind.CLIPPING_OVERFLOW,
            page_number=1,
            bbox=RenderPixelBox(x0=0, y0=700, x1=100, y1=800),
            normalized_bbox=RenderNormalizedBox(x0=0, y0=0.875, x1=1 / 6, y1=1),
            severity=0.75,
            scores=RenderDiffComponentScores(
                shape_similarity=0,
                area_similarity=0,
                position_similarity=0,
                foreground_overlap=0,
                reference_difference_fraction=0,
                candidate_difference_fraction=1,
                evidence_strength=1,
            ),
            object_ids=("paragraph-1",),
            evidence=("candidate_object_bbox_outside_page",),
        )
        for index in range(clipping)
    )
    return RenderDiffReport(
        reference_page_count=1,
        candidate_page_count=candidate_pages,
        pages_compared=min(1, candidate_pages),
        page_summaries=summaries,
        diagnostics=diagnostics,
        diagnostic_counts={"clipping_overflow": clipping} if clipping else {},
        max_severity=0.75 if clipping else 0,
    )


def _assessment(plan: ConstraintPlan, **changes: object) -> CandidateAssessment:
    page_sizes = changes.pop("page_sizes", plan.hard_constraints.page_sizes)
    assert isinstance(page_sizes, tuple)
    payload: dict[str, object] = {
        "constraint_plan_fingerprint": plan.fingerprint,
        "content_authority_sha256": _CONTENT_SHA,
        "layout_authority_sha256": _LAYOUT_SHA,
        "object_content_sha256": plan.hard_constraints.object_content_sha256,
        "object_provenance_sha256": plan.hard_constraints.object_provenance_sha256,
        "object_ids": plan.hard_constraints.required_object_ids,
        "page_sizes": page_sizes,
        "missing_object_ids": (),
        "source_deleted_object_ids": (),
        "native_editable_object_ids": ("paragraph-1",),
        "raster_substituted_object_ids": (),
        "full_page_raster_pages": (),
        "structure_score": 0.8,
        "layout_score": 0.7,
        "visual_score": 0.5,
        "editability_score": 0.9,
        "render_diff": _render_diff(candidate_pages=len(page_sizes)),
    }
    payload.update(changes)
    return CandidateAssessment.model_validate(payload)


def _action(*, reason: str = "repair visual geometry", after: float = 11.5) -> CorrectionAction:
    return CorrectionAction(
        action_type=CorrectionActionType.ADJUST_FONT_SIZE,
        object_ids=("paragraph-1",),
        before=CorrectionParameters(font_size=12),
        after=CorrectionParameters(font_size=after),
        reason=reason,
        predicted_effect=PredictedEffect(target=ObjectiveComponent.VISUAL, expected_delta=0.1),
    )


class _QueueProposer:
    def __init__(self, batches: list[Sequence[CorrectionAction] | None]) -> None:
        self.batches = batches

    def __call__(self, context) -> Sequence[CorrectionAction] | None:
        index = context.iteration - 1
        return self.batches[index] if index < len(self.batches) else None


class _Evaluator:
    def __init__(self, values: dict[bytes, CandidateAssessment], *, fail: bytes | None = None):
        self.values = values
        self.fail = fail
        self.calls: list[str] = []

    def __call__(self, artifact: bytes, digest: str, plan: ConstraintPlan) -> CandidateAssessment:
        assert digest == candidate_sha256(artifact)
        assert plan.fingerprint
        self.calls.append(digest)
        if artifact == self.fail:
            raise RuntimeError("synthetic render failure")
        return self.values[artifact]


def _objective(**changes: object) -> CorrectionObjective:
    payload: dict[str, object] = {
        "semantic_authority_preserved": True,
        "missing_content_count": 0,
        "page_geometry_violation_count": 0,
        "clipping_overflow_count": 0,
        "structure_score": 0.8,
        "layout_score": 0.8,
        "visual_score": 0.8,
        "editability_score": 0.8,
        "correction_count": 0,
    }
    payload.update(changes)
    return CorrectionObjective.model_validate(payload)


def test_objective_uses_the_exact_declared_lexicographic_order() -> None:
    assert OBJECTIVE_COMPONENT_ORDER == (
        ObjectiveComponent.SEMANTIC_AUTHORITY,
        ObjectiveComponent.MISSING_CONTENT,
        ObjectiveComponent.PAGE_GEOMETRY,
        ObjectiveComponent.CLIPPING_OVERFLOW,
        ObjectiveComponent.STRUCTURE,
        ObjectiveComponent.LAYOUT,
        ObjectiveComponent.VISUAL,
        ObjectiveComponent.EDITABILITY,
        ObjectiveComponent.CORRECTION_COUNT,
    )
    current = _objective(clipping_overflow_count=1, structure_score=0.9, visual_score=0.1)
    better_clipping = _objective(clipping_overflow_count=0, structure_score=0.1, visual_score=0)
    comparison = compare_objectives(better_clipping, current)
    assert comparison.improved and comparison.component is ObjectiveComponent.CLIPPING_OVERFLOW
    worse_layout = _objective(layout_score=0.7, visual_score=1.0)
    comparison = compare_objectives(worse_layout, _objective(layout_score=0.8, visual_score=0))
    assert not comparison.improved and comparison.component is ObjectiveComponent.LAYOUT
    assert compare_objectives(_objective(correction_count=1), _objective()).component is (
        ObjectiveComponent.CORRECTION_COUNT
    )


def test_actions_are_closed_schema_bounded_and_attributable() -> None:
    assert _action().fingerprint == _action().fingerprint
    with pytest.raises(ValidationError, match="delta limit"):
        _action(after=9)
    with pytest.raises(ValidationError, match="extra"):
        CorrectionParameters.model_validate({"font_size": 12, "raw_xml": "<w:p/>"})
    raw = _action().model_dump(mode="json")
    raw["object_ids"] = ["z", "a"]
    with pytest.raises(ValidationError, match="canonically ordered"):
        CorrectionAction.model_validate(raw)


def test_safe_visual_improvement_is_accepted_with_full_provenance() -> None:
    plan = _plan()
    initial = _assessment(plan)
    better = _assessment(plan, visual_score=0.75)
    evaluator = _Evaluator({b"base": initial, b"better": better})
    result = run_bounded_correction(
        b"base",
        plan,
        propose=_QueueProposer([[_action()], None]),
        build_candidate=lambda artifact, actions, constraint: b"better",
        evaluate_candidate=evaluator,
        limits=CorrectionLimits(max_iterations=2, minimum_objective_delta=0.01),
    )

    assert result.artifact == b"better"
    assert result.report.best_iteration == 1
    assert result.report.authority_hash_unchanged
    assert result.report.terminal_reason is TerminalReason.PROPOSER_EXHAUSTED
    record = result.report.iterations[0]
    assert record.accepted and not record.rolled_back
    assert record.decisive_component is ObjectiveComponent.VISUAL
    assert record.actions[0].object_ids == ("paragraph-1",)
    assert record.actions[0].reason == "repair visual geometry"


@pytest.mark.parametrize(
    ("case", "expected"),
    [
        ("authority", HardInvariantViolation.AUTHORITY_HASH),
        ("missing", HardInvariantViolation.MISSING_CONTENT),
        ("page_count", HardInvariantViolation.PAGE_COUNT),
        ("page_size", HardInvariantViolation.PAGE_SIZE),
        ("editability", HardInvariantViolation.EDITABILITY_REGRESSION),
        ("native_loss", HardInvariantViolation.REQUIRED_EDITABILITY),
        ("raster_substitution", HardInvariantViolation.RASTER_SUBSTITUTION),
        ("full_page_raster", HardInvariantViolation.FULL_PAGE_RASTER),
    ],
)
def test_visual_gain_cannot_override_hard_invariants(case: str, expected) -> None:
    plan = _plan()
    changes: dict[str, object] = {"visual_score": 1.0}
    if case == "authority":
        changes["content_authority_sha256"] = "f" * 64
    elif case == "missing":
        changes["missing_object_ids"] = ("paragraph-1",)
    elif case == "page_count":
        changes["page_sizes"] = (Size(width=600, height=800), Size(width=600, height=800))
    elif case == "page_size":
        changes["page_sizes"] = (Size(width=601, height=800),)
    elif case == "editability":
        changes["editability_score"] = 0.8
    elif case == "native_loss":
        changes["native_editable_object_ids"] = ()
    elif case == "raster_substitution":
        changes["raster_substituted_object_ids"] = ("paragraph-1",)
    elif case == "full_page_raster":
        changes["full_page_raster_pages"] = (1,)
    candidate = _assessment(plan, **changes)
    evaluator = _Evaluator({b"base": _assessment(plan), b"candidate": candidate})
    result = run_bounded_correction(
        b"base",
        plan,
        propose=_QueueProposer([[_action()], None]),
        build_candidate=lambda artifact, actions, constraint: b"candidate",
        evaluate_candidate=evaluator,
        limits=CorrectionLimits(max_iterations=2),
    )

    assert result.artifact == b"base"
    record = result.report.iterations[0]
    assert record.decision is IterationDecision.HARD_INVARIANT
    assert record.rolled_back and not record.accepted
    assert expected in record.invariant_violations


def test_failed_and_non_improving_candidates_always_roll_back() -> None:
    plan = _plan()
    worse = _assessment(plan, visual_score=0.4)
    evaluator = _Evaluator({b"base": _assessment(plan), b"worse": worse})
    calls = 0

    def build(artifact: bytes, actions, constraint) -> bytes:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("synthetic builder failure")
        return b"worse"

    result = run_bounded_correction(
        b"base",
        plan,
        propose=_QueueProposer([[_action(reason="first")], [_action(reason="second")], None]),
        build_candidate=build,
        evaluate_candidate=evaluator,
        limits=CorrectionLimits(max_iterations=3),
    )

    assert result.artifact == b"base"
    assert [item.decision for item in result.report.iterations] == [
        IterationDecision.BUILD_FAILED,
        IterationDecision.NOT_IMPROVED,
    ]
    assert all(item.rolled_back for item in result.report.iterations)


def test_candidate_evaluation_failure_rolls_back_without_changing_best() -> None:
    plan = _plan()
    evaluator = _Evaluator({b"base": _assessment(plan)}, fail=b"unrenderable")
    result = run_bounded_correction(
        b"base",
        plan,
        propose=_QueueProposer([[_action()], None]),
        build_candidate=lambda artifact, actions, constraint: b"unrenderable",
        evaluate_candidate=evaluator,
        limits=CorrectionLimits(max_iterations=2),
    )
    assert result.artifact == b"base"
    assert result.report.iterations[0].decision is IterationDecision.EVALUATION_FAILED
    assert result.report.iterations[0].rolled_back


def test_below_minimum_delta_rolls_back() -> None:
    plan = _plan()
    evaluator = _Evaluator(
        {b"base": _assessment(plan), b"tiny": _assessment(plan, visual_score=0.5005)}
    )
    result = run_bounded_correction(
        b"base",
        plan,
        propose=_QueueProposer([[_action()], None]),
        build_candidate=lambda artifact, actions, constraint: b"tiny",
        evaluate_candidate=evaluator,
        limits=CorrectionLimits(max_iterations=2, minimum_objective_delta=0.001),
    )
    assert result.artifact == b"base"
    assert result.report.iterations[0].decision is IterationDecision.BELOW_MINIMUM_DELTA


def test_visited_state_stops_loops_and_repeated_runs_are_identical() -> None:
    plan = _plan()

    def run_once():
        evaluator = _Evaluator({b"base": _assessment(plan)})
        result = run_bounded_correction(
            b"base",
            plan,
            propose=_QueueProposer([[_action()]]),
            build_candidate=lambda artifact, actions, constraint: b"base",
            evaluate_candidate=evaluator,
            limits=CorrectionLimits(max_iterations=10),
        )
        return result, evaluator

    first, first_evaluator = run_once()
    second, second_evaluator = run_once()
    assert first.report.to_dict() == second.report.to_dict()
    assert first.artifact == second.artifact == b"base"
    assert first.report.terminal_reason is TerminalReason.REPEATED_STATE
    assert len(first.report.iterations) == 1
    assert len(first_evaluator.calls) == len(second_evaluator.calls) == 1


def test_search_stops_at_iteration_and_action_budgets() -> None:
    plan = _plan()
    values = {b"base": _assessment(plan)}
    for index in (1, 2):
        values[f"candidate-{index}".encode()] = _assessment(plan, visual_score=0.4)
    evaluator = _Evaluator(values)
    build_count = 0

    def build(artifact: bytes, actions, constraint) -> bytes:
        nonlocal build_count
        build_count += 1
        return f"candidate-{build_count}".encode()

    result = run_bounded_correction(
        b"base",
        plan,
        propose=_QueueProposer([[_action()], [_action()]]),
        build_candidate=build,
        evaluate_candidate=evaluator,
        limits=CorrectionLimits(max_iterations=2, max_actions=2, max_actions_per_iteration=1),
    )
    assert len(result.report.iterations) == 2
    assert result.report.total_attempted_actions == 2
    assert result.report.terminal_reason is TerminalReason.MAX_ACTIONS

    iteration_limited = run_bounded_correction(
        b"base",
        plan,
        propose=_QueueProposer([[_action()]]),
        build_candidate=lambda artifact, actions, constraint: b"candidate-1",
        evaluate_candidate=evaluator,
        limits=CorrectionLimits(max_iterations=1, max_actions=5),
    )
    assert len(iteration_limited.report.iterations) == 1
    assert iteration_limited.report.terminal_reason is TerminalReason.MAX_ITERATIONS


def test_invalid_callback_payload_is_rejected_before_candidate_build() -> None:
    plan = _plan()
    built = False

    def propose(context):
        raw = _action().model_dump(mode="json")
        raw["after"]["font_size"] = 2
        return [raw]

    def build(artifact: bytes, actions, constraint) -> bytes:
        nonlocal built
        built = True
        return b"unsafe"

    result = run_bounded_correction(
        b"base",
        plan,
        propose=propose,
        build_candidate=build,
        evaluate_candidate=_Evaluator({b"base": _assessment(plan)}),
    )
    assert not built
    assert result.report.terminal_reason is TerminalReason.INVALID_PROPOSAL
    assert result.report.iterations[0].decision is IterationDecision.INVALID_ACTION
    assert result.report.iterations[0].rolled_back


def test_render_cache_is_keyed_by_exact_candidate_hash_across_runs() -> None:
    plan = _plan()
    evaluator = _Evaluator(
        {b"base": _assessment(plan), b"better": _assessment(plan, visual_score=0.8)}
    )
    cache = CandidateRenderCache()

    def run_once():
        return run_bounded_correction(
            b"base",
            plan,
            propose=_QueueProposer([[_action()], None]),
            build_candidate=lambda artifact, actions, constraint: b"better",
            evaluate_candidate=evaluator,
            limits=CorrectionLimits(max_iterations=2),
            render_cache=cache,
        )

    first = run_once()
    second = run_once()
    assert first.report.to_dict() == second.report.to_dict()
    assert set(cache.keys) == {candidate_sha256(b"base"), candidate_sha256(b"better")}
    assert cache.misses == 2
    assert cache.hits == 2
    assert len(evaluator.calls) == 2


def test_render_cache_never_reuses_plan_relative_assessment_across_plans() -> None:
    first_plan = _plan()
    second_plan = first_plan.model_copy(update={"warnings": ("second-plan",)})
    cache = CandidateRenderCache()
    calls: list[str] = []

    def evaluate(artifact: bytes, digest: str, plan: ConstraintPlan) -> CandidateAssessment:
        calls.append(plan.fingerprint)
        return _assessment(plan)

    first_digest, first = cache.evaluate(b"same-docx", evaluate, first_plan)
    second_digest, second = cache.evaluate(b"same-docx", evaluate, second_plan)

    assert first_digest == second_digest
    assert first.constraint_plan_fingerprint == first_plan.fingerprint
    assert second.constraint_plan_fingerprint == second_plan.fingerprint
    assert calls == [first_plan.fingerprint, second_plan.fingerprint]
    assert cache.keys == (first_digest,)
    assert cache.misses == 2
    assert cache.hits == 0


def test_page_size_action_is_never_authorized_and_builder_is_not_called() -> None:
    plan = _plan()
    action = CorrectionAction(
        action_type=CorrectionActionType.SET_PAGE_SIZE,
        object_ids=("paragraph-1",),
        before=CorrectionParameters(page_width=600, page_height=800),
        after=CorrectionParameters(page_width=601, page_height=800),
        reason="attempt to mutate a hard page size",
        predicted_effect=PredictedEffect(
            target=ObjectiveComponent.PAGE_GEOMETRY,
            expected_delta=1,
        ),
    )
    built = False

    def build(artifact: bytes, actions, constraint) -> bytes:
        nonlocal built
        built = True
        return b"forbidden"

    result = run_bounded_correction(
        b"base",
        plan,
        propose=_QueueProposer([[action], None]),
        build_candidate=build,
        evaluate_candidate=_Evaluator({b"base": _assessment(plan)}),
        limits=CorrectionLimits(max_iterations=2),
    )
    assert not built
    assert result.report.iterations[0].decision is IterationDecision.ACTION_NOT_AUTHORIZED
    assert result.artifact == b"base"


def test_invalid_initial_candidate_is_returned_unchanged_without_proposals() -> None:
    plan = _plan()
    called = False

    def propose(context):
        nonlocal called
        called = True
        return [_action()]

    invalid = _assessment(plan, content_authority_sha256="f" * 64)
    result = run_bounded_correction(
        b"invalid",
        plan,
        propose=propose,
        build_candidate=lambda artifact, actions, constraint: b"other",
        evaluate_candidate=_Evaluator({b"invalid": invalid}),
    )
    assert not called
    assert result.artifact == b"invalid"
    assert result.report.terminal_reason is TerminalReason.INITIAL_INVARIANT_VIOLATION
    assert HardInvariantViolation.AUTHORITY_HASH in result.report.initial_invariant_violations


def _font_action(*, before: float, after: float) -> CorrectionAction:
    return CorrectionAction(
        action_type=CorrectionActionType.ADJUST_FONT_SIZE,
        object_ids=("paragraph-1",),
        before=CorrectionParameters(font_size=before),
        after=CorrectionParameters(font_size=after),
        reason="repair visual geometry",
        predicted_effect=PredictedEffect(target=ObjectiveComponent.VISUAL, expected_delta=0.1),
    )


def _gutter_action(*, before: float, after: float) -> CorrectionAction:
    return CorrectionAction(
        action_type=CorrectionActionType.ADJUST_GUTTER,
        object_ids=("paragraph-1",),
        before=CorrectionParameters(gutter=before),
        after=CorrectionParameters(gutter=after),
        reason="widen the column gutter",
        predicted_effect=PredictedEffect(target=ObjectiveComponent.VISUAL, expected_delta=0.1),
    )


def _two_column_plan() -> ConstraintPlan:
    plan = _plan()
    page = plan.pages[0]

    def column(column_id: str, x0: float, x1: float, owns: bool) -> ColumnConstraint:
        return ColumnConstraint(
            column_id=column_id,
            preferred_bbox=BBox(x0=x0, y0=50, x1=x1, y1=750),
            min_width=200,
            max_width=300,
            object_ids=("paragraph-1",) if owns else (),
            provenance="content_bbox_fallback",
            soft_constraints=(SoftConstraintKind.COLUMN_GUTTER,),
        )

    updated = page.model_copy(
        update={
            "columns": (
                column("page-1-column-1", 50, 290, True),
                column("page-1-column-2", 310, 550, False),
            )
        }
    )
    return plan.model_copy(update={"pages": (updated,)})


def test_conflicting_actions_on_one_object_are_not_authorized_together() -> None:
    """Distinct fingerprints do not make two actions compatible.

    Two different font sizes for the same paragraph both passed the fingerprint
    check and were handed to the builder in an order the batch never specified.
    """

    plan = _plan()
    assessment = _assessment(plan)

    assert not actions_authorized_by_plan(
        plan, assessment, [_font_action(before=12, after=11), _font_action(before=12, after=13)]
    )
    # One action of the same shape is still fine.
    assert actions_authorized_by_plan(plan, assessment, [_font_action(before=12, after=11)])


def test_gutter_must_fit_between_the_margins_beside_its_columns() -> None:
    """The old check compared the gutter to the whole page width.

    ``CorrectionParameters`` already caps the raw value well below that, so the
    guard authorized every gutter the schema allowed — including ones that
    cannot coexist with the columns' own minimum widths.
    """

    single = _plan()
    assessment = _assessment(single)
    # A one-column page has no gutter to adjust at all.
    assert not actions_authorized_by_plan(
        single, assessment, [_gutter_action(before=270, after=288)]
    )

    two = _two_column_plan()
    # 600pt page less 50pt margins leaves 500pt; two 200pt minimums leave 100pt.
    assert actions_authorized_by_plan(two, assessment, [_gutter_action(before=92, after=100)])
    assert not actions_authorized_by_plan(two, assessment, [_gutter_action(before=95, after=101)])
    assert not actions_authorized_by_plan(two, assessment, [_gutter_action(before=270, after=288)])


def test_restated_before_cannot_walk_a_setting_past_its_delta_limit() -> None:
    """``before`` comes from the proposer, so the delta limit bounds nothing.

    Each action stayed within the 2.0pt per-action limit relative to its own
    claimed starting point, letting three iterations move the font from 12pt to
    18pt while every individual step looked compliant.
    """

    plan = _plan()
    initial = _assessment(plan)
    better = _assessment(plan, visual_score=0.75)
    best = _assessment(plan, visual_score=0.85)
    evaluator = _Evaluator({b"base": initial, b"second": better, b"third": best})
    artifacts = iter([b"second", b"third"])

    result = run_bounded_correction(
        b"base",
        plan,
        propose=_QueueProposer(
            [
                [_font_action(before=12, after=14)],
                # The engine applied 14pt; this claims it starts from 16pt.
                [_font_action(before=16, after=18)],
                None,
            ]
        ),
        build_candidate=lambda artifact, actions, constraint: next(artifacts),
        evaluate_candidate=evaluator,
        limits=CorrectionLimits(max_iterations=3, minimum_objective_delta=0.01),
    )

    first, second = result.report.iterations[0], result.report.iterations[1]
    assert first.accepted
    assert not second.accepted
    assert second.decision is IterationDecision.ACTION_NOT_AUTHORIZED
    # The escalating action never reached the builder, so 14pt is final.
    assert result.artifact == b"second"


def test_before_matching_the_applied_state_is_still_accepted() -> None:
    plan = _plan()
    initial = _assessment(plan)
    better = _assessment(plan, visual_score=0.75)
    best = _assessment(plan, visual_score=0.85)
    evaluator = _Evaluator({b"base": initial, b"second": better, b"third": best})
    artifacts = iter([b"second", b"third"])

    result = run_bounded_correction(
        b"base",
        plan,
        propose=_QueueProposer(
            [
                [_font_action(before=12, after=14)],
                [_font_action(before=14, after=15)],
                None,
            ]
        ),
        build_candidate=lambda artifact, actions, constraint: next(artifacts),
        evaluate_candidate=evaluator,
        limits=CorrectionLimits(max_iterations=3, minimum_objective_delta=0.01),
    )

    assert [record.accepted for record in result.report.iterations[:2]] == [True, True]
    assert result.artifact == b"third"
