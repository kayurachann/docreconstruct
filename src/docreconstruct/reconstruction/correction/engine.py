"""Deterministic accept-or-rollback loop for bounded document correction."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from pydantic import ValidationError

from docreconstruct.reconstruction.constraint_plan import ConstraintPlan
from docreconstruct.reconstruction.constraint_plan.canonical import stable_digest

from .actions import CorrectionAction, CorrectionActionType, CorrectionParameters
from .cache import CandidateRenderCache, candidate_sha256
from .invariants import (
    _PAGE_SCOPED_ACTIONS,
    actions_authorized_by_plan,
    hard_invariant_violations,
    objective_from_assessment,
)
from .models import (
    CorrectionIterationRecord,
    CorrectionLimits,
    CorrectionObjective,
    CorrectionReport,
    HardInvariantViolation,
    IterationDecision,
    TerminalReason,
)
from .objective import compare_objectives, meets_minimum_objective_delta
from .protocols import (
    CandidateBuilder,
    CandidateEvaluator,
    CorrectionCandidateCallback,
    CorrectionCandidateContext,
)


class InitialCandidateEvaluationError(RuntimeError):
    """Raised when the initial artifact cannot be rendered/measured at all."""


@dataclass(frozen=True, slots=True)
class CorrectionResult:
    """Best retained artifact plus its standalone deterministic audit report."""

    artifact: bytes = field(repr=False)
    report: CorrectionReport


def _state_sha256(plan: ConstraintPlan, candidate_digest: str) -> str:
    return stable_digest(
        {
            "constraint_plan_fingerprint": plan.fingerprint,
            "candidate_sha256": candidate_digest,
        }
    )


def _make_report(
    *,
    plan: ConstraintPlan,
    initial_sha256: str,
    final_sha256: str,
    initial_objective: CorrectionObjective,
    best_objective: CorrectionObjective,
    initial_violations: tuple[HardInvariantViolation, ...],
    iterations: list[CorrectionIterationRecord],
    best_iteration: int,
    visited_states: list[str],
    terminal_reason: TerminalReason,
) -> CorrectionReport:
    return CorrectionReport(
        constraint_plan_fingerprint=plan.fingerprint,
        initial_candidate_sha256=initial_sha256,
        final_candidate_sha256=final_sha256,
        best_candidate_sha256=final_sha256,
        initial_objective=initial_objective,
        best_objective=best_objective,
        initial_invariant_violations=initial_violations,
        iterations=tuple(iterations),
        best_iteration=best_iteration,
        total_attempted_actions=sum(len(item.actions) for item in iterations),
        total_accepted_actions=sum(len(item.actions) for item in iterations if item.accepted),
        visited_state_sha256=tuple(visited_states),
        authority_hash_unchanged=(
            initial_objective.semantic_authority_preserved
            and best_objective.semantic_authority_preserved
        ),
        terminal_reason=terminal_reason,
    )


def _validated_actions(raw_actions: object) -> tuple[CorrectionAction, ...]:
    if raw_actions is None:
        return ()
    if isinstance(raw_actions, (str, bytes, bytearray)) or not isinstance(raw_actions, Iterable):
        raise TypeError("a correction proposal must be a sequence of typed actions")
    try:
        return tuple(CorrectionAction.model_validate(item) for item in raw_actions)
    except (TypeError, ValidationError) as exc:
        raise ValueError("correction proposal did not satisfy the closed action schema") from exc


_APPLIED_STATE_TOLERANCE = 1e-6

# Every ADJUST_MARGIN action restates all four margins, so the plan's own
# page margins are an authoritative starting point for the very first touch.
_MARGIN_SEED_FIELDS = ("margin_top", "margin_right", "margin_bottom", "margin_left")


def _state_key(action: CorrectionAction, page_number: int) -> tuple[object, ...]:
    """Identify the setting an action mutates, so successive touches line up."""

    if action.action_type in _PAGE_SCOPED_ACTIONS:
        return (action.action_type, page_number)
    return (action.action_type, tuple(action.object_ids))


def _states_agree(claimed: CorrectionParameters, recorded: CorrectionParameters) -> bool:
    """Compare only the fields the recorded state actually pins down."""

    for name in type(recorded).model_fields:
        expected = getattr(recorded, name)
        if expected is None:
            continue
        actual = getattr(claimed, name)
        if actual is None:
            return False
        if isinstance(expected, (int, float)) and not isinstance(expected, bool):
            if abs(float(actual) - float(expected)) > _APPLIED_STATE_TOLERANCE:
                return False
        elif isinstance(expected, tuple):
            if not isinstance(actual, tuple) or len(actual) != len(expected):
                return False
            if any(
                abs(float(left) - float(right)) > _APPLIED_STATE_TOLERANCE
                for left, right in zip(actual, expected, strict=True)
            ):
                return False
        elif actual != expected:
            return False
    return True


def _seed_applied_state(plan: ConstraintPlan) -> dict[tuple[object, ...], CorrectionParameters]:
    seeded: dict[tuple[object, ...], CorrectionParameters] = {}
    for page in plan.pages:
        seeded[(CorrectionActionType.ADJUST_MARGIN, page.page_number)] = CorrectionParameters(
            margin_top=page.margins.top,
            margin_right=page.margins.right,
            margin_bottom=page.margins.bottom,
            margin_left=page.margins.left,
        )
    return seeded


def _before_matches_applied_state(
    plan: ConstraintPlan,
    actions: Iterable[CorrectionAction],
    applied: dict[tuple[object, ...], CorrectionParameters],
) -> bool:
    """Reject an action whose ``before`` contradicts what was actually applied.

    ``CorrectionAction`` enforces a per-action delta limit between ``before``
    and ``after``, but both come from the proposer.  Without pinning ``before``
    to the state the engine recorded, a proposer can restate it on every
    iteration and walk a setting arbitrarily far in single "bounded" steps.
    """

    page_by_object = {
        item.object_id: page.page_number for page in plan.pages for item in page.objects
    }
    for action in actions:
        pages = {page_by_object[object_id] for object_id in action.object_ids}
        if len(pages) != 1:
            return False
        recorded = applied.get(_state_key(action, next(iter(pages))))
        if recorded is not None and not _states_agree(action.before, recorded):
            return False
    return True


def _record_applied_state(
    plan: ConstraintPlan,
    actions: Iterable[CorrectionAction],
    applied: dict[tuple[object, ...], CorrectionParameters],
) -> None:
    page_by_object = {
        item.object_id: page.page_number for page in plan.pages for item in page.objects
    }
    for action in actions:
        pages = {page_by_object[object_id] for object_id in action.object_ids}
        applied[_state_key(action, next(iter(pages)))] = action.after


def run_bounded_correction(
    initial_artifact: bytes,
    constraint_plan: ConstraintPlan,
    *,
    propose: CorrectionCandidateCallback,
    build_candidate: CandidateBuilder,
    evaluate_candidate: CandidateEvaluator,
    limits: CorrectionLimits | None = None,
    render_cache: CandidateRenderCache | None = None,
) -> CorrectionResult:
    """Run finite rule-driven search while retaining only hard-safe improvements."""

    if not isinstance(initial_artifact, bytes) or not initial_artifact:
        raise ValueError("initial candidate artifact must be non-empty exact bytes")
    limits = limits or CorrectionLimits()
    cache = render_cache or CandidateRenderCache()
    try:
        initial_sha256, initial_assessment = cache.evaluate(
            initial_artifact,
            evaluate_candidate,
            constraint_plan,
        )
    except Exception as exc:
        raise InitialCandidateEvaluationError(
            "the initial candidate could not be rendered and measured"
        ) from exc
    initial_objective = objective_from_assessment(
        constraint_plan,
        initial_assessment,
        correction_count=0,
    )
    initial_violations = hard_invariant_violations(
        constraint_plan,
        initial_assessment,
        current_objective=None,
    )
    initial_state = _state_sha256(constraint_plan, initial_sha256)
    visited_states = [initial_state]
    if initial_violations:
        report = _make_report(
            plan=constraint_plan,
            initial_sha256=initial_sha256,
            final_sha256=initial_sha256,
            initial_objective=initial_objective,
            best_objective=initial_objective,
            initial_violations=initial_violations,
            iterations=[],
            best_iteration=0,
            visited_states=visited_states,
            terminal_reason=TerminalReason.INITIAL_INVARIANT_VIOLATION,
        )
        return CorrectionResult(artifact=initial_artifact, report=report)

    current_artifact = initial_artifact
    current_sha256 = initial_sha256
    current_assessment = initial_assessment
    current_objective = initial_objective
    iterations: list[CorrectionIterationRecord] = []
    best_iteration = 0
    attempted_actions = 0
    accepted_actions = 0
    applied_state = _seed_applied_state(constraint_plan)
    terminal_reason = TerminalReason.MAX_ITERATIONS

    for iteration_number in range(1, limits.max_iterations + 1):
        context = CorrectionCandidateContext(
            iteration=iteration_number,
            constraint_plan=constraint_plan,
            current_candidate_sha256=current_sha256,
            current_assessment=current_assessment,
            current_objective=current_objective,
            visited_state_sha256=tuple(visited_states),
            remaining_action_budget=limits.max_actions - attempted_actions,
        )
        try:
            raw_actions = propose(context)
            actions = _validated_actions(raw_actions)
        except Exception:
            iterations.append(
                CorrectionIterationRecord(
                    iteration=iteration_number,
                    actions=(),
                    before=current_objective,
                    decision=IterationDecision.INVALID_ACTION,
                    accepted=False,
                    rolled_back=True,
                )
            )
            terminal_reason = TerminalReason.INVALID_PROPOSAL
            break
        if not actions:
            terminal_reason = TerminalReason.PROPOSER_EXHAUSTED
            break
        if (
            len(actions) > limits.max_actions_per_iteration
            or attempted_actions + len(actions) > limits.max_actions
        ):
            terminal_reason = TerminalReason.MAX_ACTIONS
            break
        attempted_actions += len(actions)
        if not actions_authorized_by_plan(
            constraint_plan,
            current_assessment,
            actions,
        ) or not _before_matches_applied_state(constraint_plan, actions, applied_state):
            iterations.append(
                CorrectionIterationRecord(
                    iteration=iteration_number,
                    actions=actions,
                    before=current_objective,
                    decision=IterationDecision.ACTION_NOT_AUTHORIZED,
                    accepted=False,
                    rolled_back=True,
                )
            )
            if attempted_actions >= limits.max_actions:
                terminal_reason = TerminalReason.MAX_ACTIONS
                break
            continue
        try:
            candidate_artifact = build_candidate(
                current_artifact,
                actions,
                constraint_plan,
            )
            if not isinstance(candidate_artifact, bytes) or not candidate_artifact:
                raise ValueError("candidate builder returned no exact artifact bytes")
        except Exception:
            iterations.append(
                CorrectionIterationRecord(
                    iteration=iteration_number,
                    actions=actions,
                    before=current_objective,
                    decision=IterationDecision.BUILD_FAILED,
                    accepted=False,
                    rolled_back=True,
                )
            )
            if attempted_actions >= limits.max_actions:
                terminal_reason = TerminalReason.MAX_ACTIONS
                break
            continue
        candidate_digest = candidate_sha256(candidate_artifact)
        candidate_state = _state_sha256(constraint_plan, candidate_digest)
        if candidate_state in visited_states:
            iterations.append(
                CorrectionIterationRecord(
                    iteration=iteration_number,
                    actions=actions,
                    before=current_objective,
                    candidate_sha256=candidate_digest,
                    state_sha256=candidate_state,
                    decision=IterationDecision.REPEATED_STATE,
                    accepted=False,
                    rolled_back=True,
                )
            )
            terminal_reason = TerminalReason.REPEATED_STATE
            break
        visited_states.append(candidate_state)
        try:
            measured_digest, candidate_assessment = cache.evaluate(
                candidate_artifact,
                evaluate_candidate,
                constraint_plan,
            )
            if measured_digest != candidate_digest:  # pragma: no cover - hash invariant
                raise RuntimeError("candidate cache returned an inconsistent digest")
        except Exception:
            iterations.append(
                CorrectionIterationRecord(
                    iteration=iteration_number,
                    actions=actions,
                    before=current_objective,
                    candidate_sha256=candidate_digest,
                    state_sha256=candidate_state,
                    decision=IterationDecision.EVALUATION_FAILED,
                    accepted=False,
                    rolled_back=True,
                )
            )
            if attempted_actions >= limits.max_actions:
                terminal_reason = TerminalReason.MAX_ACTIONS
                break
            continue
        candidate_objective = objective_from_assessment(
            constraint_plan,
            candidate_assessment,
            correction_count=accepted_actions + len(actions),
        )
        violations = hard_invariant_violations(
            constraint_plan,
            candidate_assessment,
            current_objective=current_objective,
        )
        comparison = compare_objectives(candidate_objective, current_objective)
        if violations:
            decision = IterationDecision.HARD_INVARIANT
            accepted = False
        elif not comparison.improved:
            decision = IterationDecision.NOT_IMPROVED
            accepted = False
        elif not meets_minimum_objective_delta(
            comparison,
            limits.minimum_objective_delta,
        ):
            decision = IterationDecision.BELOW_MINIMUM_DELTA
            accepted = False
        else:
            decision = IterationDecision.ACCEPTED
            accepted = True
        record = CorrectionIterationRecord(
            iteration=iteration_number,
            actions=actions,
            before=current_objective,
            after=candidate_objective,
            candidate_sha256=candidate_digest,
            state_sha256=candidate_state,
            decision=decision,
            accepted=accepted,
            rolled_back=not accepted,
            decisive_component=comparison.component,
            objective_delta=comparison.delta,
            invariant_violations=violations,
        )
        iterations.append(record)
        if accepted:
            current_artifact = candidate_artifact
            current_sha256 = candidate_digest
            current_assessment = candidate_assessment
            current_objective = candidate_objective
            accepted_actions += len(actions)
            _record_applied_state(constraint_plan, actions, applied_state)
            best_iteration = iteration_number
        if attempted_actions >= limits.max_actions:
            terminal_reason = TerminalReason.MAX_ACTIONS
            break
    report = _make_report(
        plan=constraint_plan,
        initial_sha256=initial_sha256,
        final_sha256=current_sha256,
        initial_objective=initial_objective,
        best_objective=current_objective,
        initial_violations=initial_violations,
        iterations=iterations,
        best_iteration=best_iteration,
        visited_states=visited_states,
        terminal_reason=terminal_reason,
    )
    return CorrectionResult(artifact=current_artifact, report=report)


__all__ = [
    "CorrectionResult",
    "InitialCandidateEvaluationError",
    "run_bounded_correction",
]
