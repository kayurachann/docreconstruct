"""Renderer-neutral bounded correction contracts and accept-or-rollback engine."""

from .actions import (
    OBJECTIVE_COMPONENT_ORDER,
    CorrectionAction,
    CorrectionActionType,
    CorrectionParameters,
    ObjectiveComponent,
    PredictedEffect,
    RowHeightPolicy,
)
from .cache import CandidateRenderCache, candidate_sha256
from .engine import (
    CorrectionResult,
    InitialCandidateEvaluationError,
    run_bounded_correction,
)
from .invariants import (
    actions_authorized_by_plan,
    hard_invariant_violations,
    objective_from_assessment,
)
from .models import (
    CandidateAssessment,
    CorrectionIterationRecord,
    CorrectionLimits,
    CorrectionObjective,
    CorrectionReport,
    HardInvariantViolation,
    IterationDecision,
    ObjectiveComparison,
    TerminalReason,
)
from .objective import compare_objectives, meets_minimum_objective_delta
from .protocols import (
    CandidateBuilder,
    CandidateEvaluator,
    CorrectionCandidateCallback,
    CorrectionCandidateContext,
    RawAction,
)

__all__ = [
    "OBJECTIVE_COMPONENT_ORDER",
    "ActionProposer",
    "CandidateAssessment",
    "CandidateBuilder",
    "CandidateEvaluator",
    "CandidateRenderCache",
    "CorrectionAction",
    "CorrectionActionType",
    "CorrectionCandidateCallback",
    "CorrectionCandidateContext",
    "CorrectionIterationRecord",
    "CorrectionLimits",
    "CorrectionObjective",
    "CorrectionParameters",
    "CorrectionReport",
    "CorrectionResult",
    "HardInvariantViolation",
    "InitialCandidateEvaluationError",
    "IterationDecision",
    "ObjectiveComparison",
    "ObjectiveComponent",
    "PredictedEffect",
    "RawAction",
    "RowHeightPolicy",
    "TerminalReason",
    "actions_authorized_by_plan",
    "candidate_sha256",
    "compare_objectives",
    "hard_invariant_violations",
    "meets_minimum_objective_delta",
    "objective_from_assessment",
    "run_bounded_correction",
]

# Short compatibility name for rule-based proposers; it remains a Protocol alias.
ActionProposer = CorrectionCandidateCallback
