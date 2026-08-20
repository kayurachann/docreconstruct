"""Small callback contracts that keep correction independent of any DOCX writer."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol, TypeAlias

from docreconstruct.reconstruction.constraint_plan import ConstraintPlan

from .actions import CorrectionAction
from .models import CandidateAssessment, CorrectionObjective

RawAction: TypeAlias = CorrectionAction | Mapping[str, object]


@dataclass(frozen=True, slots=True)
class CorrectionCandidateContext:
    """Read-only facts exposed to a bounded deterministic action proposer."""

    iteration: int
    constraint_plan: ConstraintPlan
    current_candidate_sha256: str
    current_assessment: CandidateAssessment
    current_objective: CorrectionObjective
    visited_state_sha256: tuple[str, ...]
    remaining_action_budget: int


class CorrectionCandidateCallback(Protocol):
    """Propose one action group; outputs are schema-validated before use."""

    def __call__(self, context: CorrectionCandidateContext) -> Sequence[RawAction] | None: ...


class CandidateBuilder(Protocol):
    """Apply already-authorized settings and return a new artifact's exact bytes."""

    def __call__(
        self,
        current_artifact: bytes,
        actions: tuple[CorrectionAction, ...],
        constraint_plan: ConstraintPlan,
    ) -> bytes: ...


class CandidateEvaluator(Protocol):
    """Render and measure a candidate without changing its artifact bytes."""

    def __call__(
        self,
        candidate_artifact: bytes,
        candidate_sha256: str,
        constraint_plan: ConstraintPlan,
    ) -> CandidateAssessment: ...


__all__ = [
    "CandidateBuilder",
    "CandidateEvaluator",
    "CorrectionCandidateCallback",
    "CorrectionCandidateContext",
    "RawAction",
]
